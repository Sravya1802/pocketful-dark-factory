'use strict';
// Pocketful stage 1: HTTP entry point. No dependencies beyond the Node.js runtime.

const http = require('http');
const { handle, errorResponse } = require('./src/app');

const PORT = Number.parseInt(process.env.PORT || '8080', 10) || 8080;
const MAX_BODY = 64 * 1024 * 1024;
const JSON_TYPE = 'application/json; charset=utf-8';

function send(res, out) {
  if (res.headersSent || res.destroyed) return;
  if (out.text === undefined) {
    res.writeHead(out.status);
    res.end();
    return;
  }
  const buf = Buffer.from(out.text, 'utf8');
  res.writeHead(out.status, { 'Content-Type': JSON_TYPE, 'Content-Length': buf.length });
  res.end(buf);
}

const server = http.createServer({ requestTimeout: 60000, keepAliveTimeout: 30000 }, (req, res) => {
  const chunks = [];
  let size = 0;
  let tooLarge = false;
  req.on('data', (c) => {
    if (tooLarge) return;
    size += c.length;
    if (size > MAX_BODY) {
      tooLarge = true;
      chunks.length = 0;
      return;
    }
    chunks.push(c);
  });
  req.on('error', () => {});
  req.on('end', () => {
    if (tooLarge) {
      send(res, errorResponse(413, 'payload_too_large', 'request body is too large'));
      return;
    }
    let url;
    try {
      url = new URL(req.url, 'http://localhost');
    } catch (e) {
      send(res, errorResponse(404, 'not_found', 'bad request target'));
      return;
    }
    const ctx = { req, method: req.method, path: url.pathname, query: url.searchParams, raw: Buffer.concat(chunks) };
    handle(ctx).then((out) => send(res, out), (e) => {
      console.error('unhandled', e);
      send(res, errorResponse(500, 'internal_error', 'internal error'));
    });
  });
});

server.on('clientError', (err, socket) => {
  if (!socket.writable) return;
  const body = JSON.stringify({ error: { code: 'malformed_request', message: 'malformed HTTP request' } });
  socket.end('HTTP/1.1 400 Bad Request\r\nContent-Type: ' + JSON_TYPE + '\r\nContent-Length: '
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
