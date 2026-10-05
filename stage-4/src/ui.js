'use strict';
// Browser screens. Every screen is the same small HTML shell; the client script in
// public/app.js renders it and reads data with the user's bearer token, so the pages
// themselves load without authentication. All assets are served from the image.

const fs = require('fs');
const path = require('path');

const PUBLIC = path.join(__dirname, '..', 'public');

const ASSETS = new Map([
  ['/assets/app.css', { file: 'app.css', type: 'text/css; charset=utf-8' }],
  ['/assets/app.js', { file: 'app.js', type: 'text/javascript; charset=utf-8' }],
  ['/assets/icon.svg', { file: 'icon.svg', type: 'image/svg+xml' }],
]);
for (const a of ASSETS.values()) a.text = fs.readFileSync(path.join(PUBLIC, a.file), 'utf8');

// Route -> [screen name, page title]. `/requests` and `/authorizations` are shared with
// the API and are served as HTML only to clients that ask for it.
const SCREENS = {
  '/': ['wallet', 'Wallet'],
  '/requests': ['requests', 'Requests'],
  '/split': ['split', 'Split a bill'],
  '/authorizations': ['authorizations', 'Holds'],
  '/signup': ['signup', 'Create your account'],
  '/login': ['login', 'Log in'],
};
const SHARED = new Set(['/requests', '/authorizations']);

const SECURITY_HEADERS = {
  'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    + "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Cache-Control': 'no-cache',
};

function wantsHtml(req) {
  const accept = req.headers.accept;
  return typeof accept === 'string' && /text\/html/i.test(accept);
}

function shell(screen, title) {
  return '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    + '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    + '<meta name="color-scheme" content="light">\n'
    + '<title>' + title + ' · Pocketful</title>\n'
    + '<link rel="icon" href="/assets/icon.svg" type="image/svg+xml">\n'
    + '<link rel="stylesheet" href="/assets/app.css">\n'
    + '<script src="/assets/app.js" defer></script>\n'
    + '</head>\n<body data-screen="' + screen + '">\n'
    + '<a class="skip-link" href="#main">Skip to content</a>\n'
    + '<header class="topbar" id="topbar"></header>\n'
    + '<main id="main" class="page" tabindex="-1">\n'
    + '<p class="boot" role="status" aria-busy="true">Loading Pocketful…</p>\n'
    + '<noscript><p class="boot">Pocketful needs JavaScript to run in your browser.</p></noscript>\n'
    + '</main>\n</body>\n</html>\n';
}

function html(status, text) {
  return { status, text, type: 'text/html; charset=utf-8', headers: SECURITY_HEADERS };
}

// Returns a response for a browser screen or asset, or null when the request is for the API.
function uiResponse(ctx, notFound = false) {
  if (ctx.method !== 'GET') return null;
  if (notFound) return html(404, shell('notfound', 'Page not found'));
  const asset = ASSETS.get(ctx.path);
  if (asset) return { status: 200, text: asset.text, type: asset.type, headers: SECURITY_HEADERS };
  const screen = SCREENS[ctx.path];
  if (!screen) return null;
  if (SHARED.has(ctx.path) && !wantsHtml(ctx.req)) return null;
  return html(200, shell(screen[0], screen[1]));
}

module.exports = { uiResponse, wantsHtml };
