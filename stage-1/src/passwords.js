'use strict';
// scrypt password hashing. Hashing runs on the libuv thread pool so the event loop,
// which owns all wallet state, is never blocked by it.

const crypto = require('crypto');

const N = 16384;
const R = 8;
const P = 1;
const KEYLEN = 32;
const MAXMEM = 64 * 1024 * 1024;

function scrypt(password, salt, n, r, p, keylen) {
  return new Promise((resolve, reject) => {
    crypto.scrypt(password, salt, keylen, { N: n, r, p, maxmem: MAXMEM }, (err, key) => {
      if (err) reject(err);
      else resolve(key);
    });
  });
}

async function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const key = await scrypt(password, salt, N, R, P, KEYLEN);
  return ['scrypt', N, R, P, salt.toString('base64'), key.toString('base64')].join('$');
}

const HASH_RE = /^scrypt\$([0-9]+)\$([0-9]+)\$([0-9]+)\$([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+)$/;

function isValidHash(h) {
  if (typeof h !== 'string') return false;
  const m = HASH_RE.exec(h);
  if (!m) return false;
  const n = Number(m[1]);
  const r = Number(m[2]);
  const p = Number(m[3]);
  // Bounded parameters: a hash from an imported state must not be able to stall the service.
  return n >= 2 && n <= 1 << 17 && (n & (n - 1)) === 0 && r >= 1 && r <= 16 && p >= 1 && p <= 4
    && 128 * n * r * p <= MAXMEM / 2;
}

async function verifyPassword(password, stored) {
  if (!isValidHash(stored)) return false;
  const [, n, r, p, salt, key] = HASH_RE.exec(stored);
  const expected = Buffer.from(key, 'base64');
  if (expected.length === 0) return false;
  const actual = await scrypt(password, Buffer.from(salt, 'base64'), Number(n), Number(r), Number(p), expected.length);
  return crypto.timingSafeEqual(actual, expected);
}

// Seed fixtures are reset many times with the same passwords. Remember the scrypt
// result for a seed password under a keyed digest (per-process secret, memory only,
// never exported) so repeated resets stay well inside their time limit.
const seedSecret = crypto.randomBytes(32);
const seedCache = new Map();
const SEED_CACHE_MAX = 10000;

async function hashSeedPassword(password) {
  const tag = crypto.createHmac('sha256', seedSecret).update(password, 'utf8').digest('base64');
  let pending = seedCache.get(tag);
  if (!pending) {
    pending = hashPassword(password);
    if (seedCache.size >= SEED_CACHE_MAX) seedCache.clear();
    seedCache.set(tag, pending);
    pending.catch(() => seedCache.delete(tag));
  }
  return pending;
}

module.exports = { hashPassword, verifyPassword, hashSeedPassword, isValidHash };
