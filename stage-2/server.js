'use strict';
// Pocketful stage 1: HTTP entry point. No dependencies beyond the Node.js runtime.

const http = require('http');
const { handle, errorResponse } = require('./src/app');

const PORT = Number.parseInt(process.env.PORT || '8080', 10) || 8080;
const JSON_TYPE = 'application/json; charset=utf-8';

// Body limits. Parsing runs on the event loop, so API bodies are capped to keep both
// memory and loop time bounded with 50 requests in flight. The largest legitimate API
// body (32 settlement transfers with 200-character escaped notes) is under 100 KiB.
const API_BODY_MAX = 256 * 1024;
// Test-control calls (reset, import, export) are processed through one lane, one at a
// time, so at most one large body is parsed and one replacement state built at once.
// Their bodies are read concurrently, except that only one body larger than
// LARGE_BODY may be in transfer or waiting at a time: others pause (TCP back-pressure)
// until it is done. So a stalled upload never blocks small control calls, and memory
// holds at most one large body. The control cap is set from measurements in a 2 CPU /
// 2 GiB container (see RUN.md).
const CONTROL_BODY_MAX = 448 * 1024 * 1024;
const LARGE_BODY = 16 * 1024 * 1024;
const BUFFERED_BUDGET = CONTROL_BODY_MAX + 64 * 1024 * 1024;
// A declared length above every cap is refused at once rather than drained.
const UNREASONABLE_LENGTH = 1024 * 1024 * 1024;
// Header limit high enough that oversized header values (e.g. a long Idempotency-Key)
// reach the API's own validation instead of the HTTP parser.
const MAX_HEADER_SIZE = 1024 * 1024;

const CONTROL_PATHS = new Set(['/_test/reset', '/_test/import', '/_test/export']);

let buffered = 0;
let controlLane = Promise.resolve();

// One slot for a large body; waiters are served in arrival order.
let largeBusy = false;
const largeWaiters = [];
function acquireLarge() {
  if (!largeBusy) {
    largeBusy = true;
    return Promise.resolve();
  }
  return new Promise((resolve) => largeWaiters.push(resolve));
}
function releaseLarge() {
  const next = largeWaiters.shift();
  if (next) next();
  else largeBusy = false;
}

function send(res, out, extraHeaders) {
  if (res.headersSent || res.destroyed) return;
  if (out.pieces !== undefined) {
    sendPieces(res, out);
    return;
  }
  if (out.text === undefined) {
    res.writeHead(out.status, extraHeaders);
    res.end();
    return;
  }
  const buf = Buffer.from(out.text, 'utf8');
  res.writeHead(out.status, { 'Content-Type': JSON_TYPE, 'Content-Length': buf.length, ...extraHeaders });
  res.end(buf);
}

// Streams a large body piece by piece, honouring back-pressure.
function sendPieces(res, out) {
  let length = 0;
  for (const p of out.pieces) length += Buffer.byteLength(p, 'utf8');
  res.writeHead(out.status, { 'Content-Type': JSON_TYPE, 'Content-Length': length });
  const pieces = out.pieces;
  let i = 0;
  const pump = () => {
    while (i < pieces.length) {
      const p = pieces[i];
      pieces[i++] = null;
      if (!res.write(p, 'utf8')) {
        res.once('drain', pump);
        return;
      }
    }
    res.end();
  };
  res.on('close', () => { i = pieces.length; });
  pump();
}

function tooLarge(max) {
  return errorResponse(413, 'payload_too_large', 'request body exceeds ' + max + ' bytes');
}

function pathOf(rawUrl) {
  const q = rawUrl.indexOf('?');
  return q === -1 ? rawUrl : rawUrl.slice(0, q);
}

function runInLane(fn) {
  const run = controlLane.then(fn);
  controlLane = run.catch(() => {});
  return run;
}

// Reads the body and answers one request. The body is decoded as it arrives, so no
// second full-size copy of a large upload is ever made.
function serve(req, res, max, control) {
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let text = '';
  let validUtf8 = true;
  let size = 0;
  let held = 0;
  let rejected = false;
  let large = 'no'; // 'no' | 'waiting' | 'held'
  const release = () => {
    buffered -= held;
    held = 0;
    if (large === 'held') releaseLarge();
    large = 'no';
  };
  req.on('data', (c) => {
    if (rejected) return; // drain and discard the rest so the client can read the answer
    size += c.length;
    if (large === 'no' && size > LARGE_BODY && size <= max) {
      large = 'waiting';
      req.pause();
      acquireLarge().then(() => {
        if (large !== 'waiting') { // the request went away while waiting
          releaseLarge();
          return;
        }
        large = 'held';
        req.resume();
      });
    }
    if (size > max || buffered + c.length > BUFFERED_BUDGET) {
      rejected = true;
      text = '';
      release();
      req.resume(); // keep draining even if it was waiting for the large-body slot
      return;
    }
    if (validUtf8) {
      try {
        text += decoder.decode(c, { stream: true });
      } catch (e) {
        validUtf8 = false;
        text = '';
      }
    }
    held += c.length;
    buffered += c.length;
  });
  let ended = false;
  // An upload that dies part-way gives its budget back at once; a complete body keeps
  // it until the request has been handled.
  req.on('error', release);
  req.on('close', () => {
    if (!ended) release();
  });
  req.on('end', () => {
    ended = true;
    if (rejected) {
      send(res, tooLarge(max));
      return;
    }
    let url;
    try {
      url = new URL(req.url, 'http://localhost');
    } catch (e) {
      release();
      send(res, errorResponse(404, 'not_found', 'bad request target'));
      return;
    }
    if (validUtf8) {
      try {
        text += decoder.decode();
      } catch (e) {
        validUtf8 = false;
      }
    }
    const ctx = { req, method: req.method, path: url.pathname, query: url.searchParams,
      body: validUtf8 ? text : null };
    text = '';
    (control ? runInLane(() => handle(ctx)) : handle(ctx)).then((out) => send(res, out), (e) => {
      console.error('unhandled', e);
      send(res, errorResponse(500, 'internal_error', 'internal error'));
    }).finally(release);
  });
}

const server = http.createServer({
  requestTimeout: 60000,
  keepAliveTimeout: 30000,
  maxHeaderSize: MAX_HEADER_SIZE,
}, (req, res) => {
  const control = CONTROL_PATHS.has(pathOf(req.url));
  const max = control ? CONTROL_BODY_MAX : API_BODY_MAX;
  const declared = Number(req.headers['content-length']);
  if (Number.isFinite(declared) && declared > UNREASONABLE_LENGTH) {
    send(res, tooLarge(max), { Connection: 'close' });
    req.resume();
    return;
  }
  serve(req, res, max, control);
});

server.on('clientError', (err, socket) => {
  if (!socket.writable) return;
  const overflow = err && err.code === 'HPE_HEADER_OVERFLOW';
  const status = overflow ? '431 Request Header Fields Too Large' : '400 Bad Request';
  const body = JSON.stringify({ error: overflow
    ? { code: 'validation_failed', message: 'request headers are too large' }
    : { code: 'malformed_request', message: 'malformed HTTP request' } });
  socket.end('HTTP/1.1 ' + status + '\r\nContent-Type: ' + JSON_TYPE + '\r\nContent-Length: '
    + Buffer.byteLength(body) + '\r\nConnection: close\r\n\r\n' + body);
});

server.listen({ port: PORT, host: '0.0.0.0', backlog: 1024 }, () => {
  console.log('pocketful listening on 0.0.0.0:' + PORT);
});

for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, () => {
    server.close();
    process.exit(0);
  });
}
