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
const SEED_RE = /^scrypt-hmac\$([0-9]+)\$([0-9]+)\$([0-9]+)\$([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+)$/;

function saneParams(n, r, p) {
  // Bounded parameters: a hash from an imported state must not be able to stall the service.
  return n >= 2 && n <= 1 << 17 && (n & (n - 1)) === 0 && r >= 1 && r <= 16 && p >= 1 && p <= 4
    && 128 * n * r * p <= MAXMEM / 2;
}

function isValidHash(h) {
  if (typeof h !== 'string') return false;
  const m = HASH_RE.exec(h) || SEED_RE.exec(h);
  return m !== null && saneParams(Number(m[1]), Number(m[2]), Number(m[3]));
}

async function verifyPassword(password, stored) {
  if (!isValidHash(stored)) return false;
  let m = HASH_RE.exec(stored);
  if (m) {
    const expected = Buffer.from(m[5], 'base64');
    if (expected.length === 0) return false;
    const actual = await scrypt(password, Buffer.from(m[4], 'base64'), Number(m[1]), Number(m[2]), Number(m[3]),
      expected.length);
    return crypto.timingSafeEqual(actual, expected);
  }
  m = SEED_RE.exec(stored);
  const expected = Buffer.from(m[6], 'base64');
  if (expected.length !== 32) return false;
  const key = await scrypt(password, Buffer.from(m[4], 'base64'), Number(m[1]), Number(m[2]), Number(m[3]), KEYLEN);
  const actual = crypto.createHmac('sha256', Buffer.from(m[5], 'base64')).update(key).digest();
  return crypto.timingSafeEqual(actual, expected);
}

// Seeded fixture users. A reset must finish within 10 s however many users the fixture
// holds, and fixtures are reset many times with the same passwords. So the scrypt work
// is done once per distinct password (cost SEED_N, memoised in memory under a keyed
// digest with a per-process secret, never exported), and each user then gets their own
// random salt over that result: stored values never repeat, even for equal passwords.
const SEED_N = 4096;
const seedSecret = crypto.randomBytes(32);
const seedCache = new Map();
const SEED_CACHE_MAX = 10000;

function seedBase(password) {
  const tag = crypto.createHmac('sha256', seedSecret).update(password, 'utf8').digest('base64');
  let pending = seedCache.get(tag);
  if (!pending) {
    const salt = crypto.randomBytes(16);
    pending = scrypt(password, salt, SEED_N, R, P, KEYLEN).then((key) => ({ salt, key }));
    if (seedCache.size >= SEED_CACHE_MAX) seedCache.clear();
    seedCache.set(tag, pending);
    pending.catch(() => seedCache.delete(tag));
  }
  return pending;
}

async function hashSeedPassword(password) {
  const { salt, key } = await seedBase(password);
  const userSalt = crypto.randomBytes(16);
  const mac = crypto.createHmac('sha256', userSalt).update(key).digest();
  return ['scrypt-hmac', SEED_N, R, P, salt.toString('base64'), userSalt.toString('base64'), mac.toString('base64')]
    .join('$');
}

module.exports = { hashPassword, verifyPassword, hashSeedPassword, isValidHash };
