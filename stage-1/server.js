'use strict';
// Pocketful stage 1: HTTP entry point. No dependencies beyond the Node.js runtime.

const http = require('http');
const { handle, errorResponse } = require('./src/app');

const PORT = Number.parseInt(process.env.PORT || '8080', 10) || 8080;
const JSON_TYPE = 'application/json; charset=utf-8';

// Body limits. Parsing runs on the event loop, so bodies are capped to keep both
// memory and loop time bounded with 50 requests in flight. The largest legitimate API
// body (32 settlement transfers with 200-character escaped notes) is under 100 KiB.
// Test-control bodies (reset fixtures, imported exports) may be large but are bounded
// too, and all buffered bodies together share one budget.
const API_BODY_MAX = 256 * 1024;
const CONTROL_BODY_MAX = 32 * 1024 * 1024;
const BUFFERED_BUDGET = 256 * 1024 * 1024;
// A declared length this far above every cap is refused at once rather than drained.
const UNREASONABLE_LENGTH = 1024 * 1024 * 1024;
// Header limit high enough that oversized header values (e.g. a long Idempotency-Key)
// reach the API's own validation instead of the HTTP parser.
const MAX_HEADER_SIZE = 1024 * 1024;

const CONTROL_PATHS = new Set(['/_test/reset', '/_test/import']);

let buffered = 0;

function send(res, out, extraHeaders) {
  if (res.headersSent || res.destroyed) return;
  if (out.text === undefined) {
    res.writeHead(out.status, extraHeaders);
    res.end();
    return;
  }
  const buf = Buffer.from(out.text, 'utf8');
  res.writeHead(out.status, { 'Content-Type': JSON_TYPE, 'Content-Length': buf.length, ...extraHeaders });
  res.end(buf);
}

function tooLarge(max) {
  return errorResponse(413, 'payload_too_large', 'request body exceeds ' + max + ' bytes');
}

function pathOf(rawUrl) {
  const q = rawUrl.indexOf('?');
  return q === -1 ? rawUrl : rawUrl.slice(0, q);
}

const server = http.createServer({
  requestTimeout: 60000,
  keepAliveTimeout: 30000,
  maxHeaderSize: MAX_HEADER_SIZE,
}, (req, res) => {
  const max = CONTROL_PATHS.has(pathOf(req.url)) ? CONTROL_BODY_MAX : API_BODY_MAX;
  const declared = Number(req.headers['content-length']);
  if (Number.isFinite(declared) && declared > UNREASONABLE_LENGTH) {
    send(res, tooLarge(max), { Connection: 'close' });
    req.resume();
    return;
  }

  const chunks = [];
  let size = 0;
  let held = 0;
  let rejected = false;
  const release = () => {
    buffered -= held;
    held = 0;
  };
  req.on('data', (c) => {
    if (rejected) return; // drain and discard the rest so the client can read the answer
    size += c.length;
    if (size > max || buffered + c.length > BUFFERED_BUDGET) {
      rejected = true;
      chunks.length = 0;
      release();
      return;
    }
    chunks.push(c);
    held += c.length;
    buffered += c.length;
  });
  req.on('error', release);
  req.on('close', release);
  req.on('end', () => {
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
    const ctx = { req, method: req.method, path: url.pathname, query: url.searchParams, raw: Buffer.concat(chunks) };
    chunks.length = 0;
    handle(ctx).then((out) => send(res, out), (e) => {
      console.error('unhandled', e);
      send(res, errorResponse(500, 'internal_error', 'internal error'));
    }).finally(release);
  });
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
