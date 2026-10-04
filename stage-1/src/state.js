'use strict';
// In-memory service state. Every mutation happens synchronously on the single
// JavaScript thread, so each operation is one indivisible step: no other request
// can observe or interleave with a half-applied change.

const crypto = require('crypto');
const { JNum, intValue, digestCanonical, DIGEST_PREFIX } = require('./json');
const { isValidHash } = require('./passwords');

const HANDLE_RE = /^[a-z0-9_]{1,20}$/;
const RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
const STATUSES = ['pending', 'paid', 'declined', 'cancelled'];
const MAX_ID = 64;

// A problem with a fixture or an imported state. `type` errors are wrong JSON types (400).
class StateError extends Error {
  constructor(message, type = false) {
    super(message);
    this.type = type;
  }
}

function pad(n, w = 2) {
  return String(n).padStart(w, '0');
}

let lastMs = 0;
function now() {
  let ms = Date.now();
  if (ms < lastMs) ms = lastMs;
  lastMs = ms;
  const d = new Date(ms);
  const iso = d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) + 'T'
    + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds()) + '.'
    + pad(d.getUTCMilliseconds(), 3) + '+00:00';
  return { ms, iso };
}

function parseStamp(s) {
  if (typeof s !== 'string' || !RFC3339_RE.test(s)) return null;
  const ms = Date.parse(s);
  return Number.isFinite(ms) ? { ms, iso: s } : null;
}

function codePoints(s) {
  let n = 0;
  for (const _ of s) n++; // eslint-disable-line no-unused-vars
  return n;
}

class State {
  constructor(currency, minorUnits) {
    this.currency = currency;
    this.minorUnits = minorUnits;
    this.users = new Map();     // id -> user
    this.byHandle = new Map();  // handle -> user
    this.byEmail = new Map();   // lower-cased email -> user
    this.tokens = new Map();    // token -> user id
    this.payments = new Map();  // id -> payment (insertion order)
    this.requests = new Map();  // id -> request (insertion order)
    this.splits = new Map();
    this.settlements = new Map();
    this.idem = new Map();      // user id, method+path, key -> { fp (body digest), status, body }
    this.operators = new Set();
    this.seq = 0;
  }

  nextSeq() {
    return ++this.seq;
  }

  newId(prefix, taken) {
    for (;;) {
      const id = prefix + crypto.randomBytes(8).toString('hex');
      if (!taken.has(id)) return id;
    }
  }

  newToken(userId) {
    const token = crypto.randomBytes(32).toString('base64url');
    this.tokens.set(token, userId);
    return token;
  }

  addUser(u) {
    this.users.set(u.id, u);
    this.byHandle.set(u.handle, u);
    this.byEmail.set(u.email.toLowerCase(), u);
  }

  idemKey(userId, scope, key) {
    return userId + '\u0000' + scope + '\u0000' + key;
  }

  // ---- views -------------------------------------------------------------

  paymentView(p) {
    return {
      payment_id: p.id,
      from_user_id: p.fromId,
      from_handle: this.users.get(p.fromId).handle,
      to_user_id: p.toId,
      to_handle: this.users.get(p.toId).handle,
      amount: p.amount,
      currency: this.currency,
      note: p.note,
      visibility: p.visibility,
      request_id: p.requestId,
      settlement_id: p.settlementId,
      created_at: p.createdAt,
    };
  }

  requestView(r) {
    return {
      request_id: r.id,
      requester_id: r.requesterId,
      requester_handle: this.users.get(r.requesterId).handle,
      payer_id: r.payerId,
      payer_handle: this.users.get(r.payerId).handle,
      amount: r.amount,
      currency: this.currency,
      note: r.note,
      status: r.status,
      payment_id: r.paymentId,
      created_at: r.createdAt,
    };
  }

  // ---- export --------------------------------------------------------------

  exportState() {
    const users = [];
    for (const u of this.users.values()) {
      users.push({ id: u.id, email: u.email, password_hash: u.passwordHash, display_name: u.displayName,
        handle: u.handle, balance: u.balance });
    }
    const payments = [];
    for (const p of this.payments.values()) {
      payments.push({ id: p.id, from_user_id: p.fromId, to_user_id: p.toId, amount: p.amount, note: p.note,
        visibility: p.visibility, request_id: p.requestId, settlement_id: p.settlementId,
        created_at: p.createdAt, created_ms: p.createdMs, seq: p.seq });
    }
    const requests = [];
    for (const r of this.requests.values()) {
      requests.push({ id: r.id, requester_id: r.requesterId, payer_id: r.payerId, amount: r.amount, note: r.note,
        status: r.status, payment_id: r.paymentId, split_id: r.splitId, created_at: r.createdAt,
        created_ms: r.createdMs, seq: r.seq });
    }
    const splits = [];
    for (const s of this.splits.values()) {
      splits.push({ id: s.id, creator_id: s.creatorId, amount: s.amount, note: s.note,
        shares: s.shares.map((x) => ({ user_id: x.userId, amount: x.amount })), request_ids: s.requestIds.slice(),
        created_at: s.createdAt });
    }
    const settlements = [];
    for (const s of this.settlements.values()) {
      settlements.push({ id: s.id, operator_id: s.operatorId, payment_ids: s.paymentIds.slice(),
        committed_at: s.committedAt });
    }
    const tokens = [];
    for (const [t, uid] of this.tokens) tokens.push({ token: t, user_id: uid });
    const idempotency = [];
    for (const rec of this.idem.values()) {
      idempotency.push({ user_id: rec.userId, scope: rec.scope, key: rec.key, fingerprint: rec.fp,
        status: rec.status, body: rec.body });
    }
    return {
      currency: this.currency,
      minor_units: this.minorUnits,
      seq: this.seq,
      users,
      tokens,
      settlement_operator_ids: [...this.operators],
      payments,
      requests,
      splits,
      settlements,
      idempotency,
    };
  }
}

// ---- shared field readers for fixtures and imported state -----------------

function has(o, k) {
  return Object.prototype.hasOwnProperty.call(o, k);
}
function isObj(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v) && !(v instanceof JNum);
}
function field(o, k, what) {
  if (!has(o, k)) throw new StateError(what + ': missing ' + k);
  return o[k];
}
function str(o, k, what, opts = {}) {
  if (!has(o, k) || o[k] === undefined) {
    if ('dflt' in opts) return opts.dflt;
    throw new StateError(what + ': missing ' + k);
  }
  const v = o[k];
  if (v === null && opts.nullable) return null;
  if (typeof v !== 'string') throw new StateError(what + ': ' + k + ' must be a string', true);
  if (opts.id && (v.length < 1 || v.length > MAX_ID)) throw new StateError(what + ': bad ' + k);
  return v;
}
function int(o, k, what, opts = {}) {
  if (!has(o, k)) {
    if ('dflt' in opts) return opts.dflt;
    throw new StateError(what + ': missing ' + k);
  }
  const v = o[k];
  if (!(v instanceof JNum) && typeof v !== 'number') throw new StateError(what + ': ' + k + ' must be a number', true);
  const n = intValue(v);
  if (n === undefined) throw new StateError(what + ': ' + k + ' must be an integer');
  if (opts.min !== undefined && n < opts.min) throw new StateError(what + ': ' + k + ' out of range');
  if (opts.max !== undefined && n > opts.max) throw new StateError(what + ': ' + k + ' out of range');
  return n;
}
function arr(o, k, what, opts = {}) {
  if (!has(o, k)) {
    if ('dflt' in opts) return opts.dflt;
    throw new StateError(what + ': missing ' + k);
  }
  if (!Array.isArray(o[k])) throw new StateError(what + ': ' + k + ' must be an array', true);
  return o[k];
}
// Visits every element of the array o[k], dropping each parsed element (and finally the
// array) once used, so a large imported tree can be collected while the state is built.
function each(o, k, what, fn) {
  const a = arr(o, k, what);
  for (let i = 0; i < a.length; i++) {
    fn(a[i]);
    a[i] = null;
  }
  o[k] = null;
}

function obj(v, what) {
  if (!isObj(v)) throw new StateError(what + ' must be an object', true);
  return v;
}

const SAFE = 2n ** 53n;

function checkCurrency(o) {
  const currency = str(o, 'currency', 'state');
  if (currency.length === 0) throw new StateError('currency must not be empty');
  const minor = int(o, 'minor_units', 'state');
  if (minor !== 0n && minor !== 2n && minor !== 3n) throw new StateError('minor_units must be 0, 2 or 3');
  return new State(currency, Number(minor));
}

function stampOrNow(o, what, fallback) {
  if (!has(o, 'created_at') || o.created_at === null) return fallback;
  const s = parseStamp(o.created_at);
  if (!s) throw new StateError(what + ': bad created_at');
  return s;
}

// ---- reset fixture -> State (passwords still plaintext; caller hashes them) ----

function stateFromFixture(fx) {
  obj(fx, 'fixture');
  const st = checkCurrency(fx);
  const stamp = now();
  const passwords = new Map(); // user id -> plaintext, handed back for hashing only
  const emails = new Set();

  for (const raw of arr(fx, 'users', 'fixture')) {
    const u = obj(raw, 'user');
    const id = str(u, 'id', 'user', { id: true });
    const email = str(u, 'email', 'user');
    const password = str(u, 'password', 'user');
    const displayName = str(u, 'display_name', 'user');
    const handle = str(u, 'handle', 'user');
    const balance = int(u, 'balance', 'user', { min: 0n, max: SAFE });
    if (!HANDLE_RE.test(handle)) throw new StateError('user: invalid handle ' + handle);
    if (st.users.has(id)) throw new StateError('duplicate user id ' + id);
    if (st.byHandle.has(handle)) throw new StateError('duplicate handle ' + handle);
    if (emails.has(email.toLowerCase())) throw new StateError('duplicate email ' + email);
    emails.add(email.toLowerCase());
    st.addUser({ id, email, passwordHash: null, displayName, handle, balance });
    passwords.set(id, password);
  }

  for (const raw of arr(fx, 'payments', 'fixture', { dflt: [] })) {
    const p = obj(raw, 'payment');
    const id = str(p, 'id', 'payment', { id: true });
    if (st.payments.has(id)) throw new StateError('duplicate payment id ' + id);
    const fromId = str(p, 'from_user_id', 'payment');
    const toId = str(p, 'to_user_id', 'payment');
    if (!st.users.has(fromId) || !st.users.has(toId)) throw new StateError('payment ' + id + ': unknown user');
    const amount = int(p, 'amount', 'payment', { min: 0n, max: SAFE });
    const note = str(p, 'note', 'payment', { dflt: '' });
    const visibility = str(p, 'visibility', 'payment', { dflt: 'public' });
    if (visibility !== 'public' && visibility !== 'private') throw new StateError('payment ' + id + ': bad visibility');
    const t = stampOrNow(p, 'payment', stamp);
    st.payments.set(id, { id, fromId, toId, amount, note, visibility,
      requestId: str(p, 'request_id', 'payment', { dflt: null, nullable: true }),
      settlementId: null, createdAt: t.iso, createdMs: t.ms, seq: st.nextSeq() });
  }

  for (const raw of arr(fx, 'requests', 'fixture', { dflt: [] })) {
    const r = obj(raw, 'request');
    const id = str(r, 'id', 'request', { id: true });
    if (st.requests.has(id)) throw new StateError('duplicate request id ' + id);
    const requesterId = str(r, 'requester_id', 'request');
    const payerId = str(r, 'payer_id', 'request');
    if (!st.users.has(requesterId) || !st.users.has(payerId)) throw new StateError('request ' + id + ': unknown user');
    const amount = int(r, 'amount', 'request', { min: 0n, max: SAFE });
    const note = str(r, 'note', 'request', { dflt: '' });
    const status = str(r, 'status', 'request', { dflt: 'pending' });
    if (!STATUSES.includes(status)) throw new StateError('request ' + id + ': bad status');
    const t = stampOrNow(r, 'request', stamp);
    st.requests.set(id, { id, requesterId, payerId, amount, note, status,
      paymentId: str(r, 'payment_id', 'request', { dflt: null, nullable: true }),
      splitId: null, createdAt: t.iso, createdMs: t.ms, seq: st.nextSeq() });
  }

  for (const v of arr(fx, 'settlement_operator_ids', 'fixture', { dflt: [] })) {
    if (typeof v !== 'string') throw new StateError('settlement_operator_ids must hold strings', true);
    st.operators.add(v);
  }
  return { state: st, passwords };
}

// ---- imported export state -> State -----------------------------------------

function stateFromExport(s) {
  obj(s, 'state');
  const st = checkCurrency(s);
  st.seq = Number(int(s, 'seq', 'state', { min: 0n, max: SAFE }));

  for (const raw of arr(s, 'users', 'state')) {
    const u = obj(raw, 'user');
    const id = str(u, 'id', 'user', { id: true });
    const email = str(u, 'email', 'user');
    const passwordHash = str(u, 'password_hash', 'user');
    if (!isValidHash(passwordHash)) throw new StateError('user: bad password hash');
    const displayName = str(u, 'display_name', 'user');
    const handle = str(u, 'handle', 'user');
    const balance = int(u, 'balance', 'user', { min: 0n, max: SAFE });
    if (!HANDLE_RE.test(handle)) throw new StateError('user: invalid handle');
    if (st.users.has(id) || st.byHandle.has(handle) || st.byEmail.has(email.toLowerCase())) {
      throw new StateError('duplicate user');
    }
    st.addUser({ id, email, passwordHash, displayName, handle, balance });
  }

  for (const raw of arr(s, 'tokens', 'state')) {
    const t = obj(raw, 'token');
    const token = str(t, 'token', 'token');
    const uid = str(t, 'user_id', 'token');
    if (!token || !st.users.has(uid) || st.tokens.has(token)) throw new StateError('bad token');
    st.tokens.set(token, uid);
  }

  for (const v of arr(s, 'settlement_operator_ids', 'state')) {
    if (typeof v !== 'string') throw new StateError('settlement_operator_ids must hold strings', true);
    st.operators.add(v);
  }

  const seqOf = (o, what) => Number(int(o, 'seq', what, { min: 0n, max: SAFE }));
  const stampOf = (o, key, what) => {
    const t = parseStamp(str(o, key, what));
    if (!t) throw new StateError(what + ': bad ' + key);
    return t;
  };

  each(s, 'payments', 'state', (raw) => {
    const p = obj(raw, 'payment');
    const id = str(p, 'id', 'payment', { id: true });
    if (st.payments.has(id)) throw new StateError('duplicate payment');
    const fromId = str(p, 'from_user_id', 'payment');
    const toId = str(p, 'to_user_id', 'payment');
    if (!st.users.has(fromId) || !st.users.has(toId)) throw new StateError('payment: unknown user');
    const visibility = str(p, 'visibility', 'payment');
    if (visibility !== 'public' && visibility !== 'private') throw new StateError('payment: bad visibility');
    const t = stampOf(p, 'created_at', 'payment');
    st.payments.set(id, { id, fromId, toId,
      amount: int(p, 'amount', 'payment', { min: 0n, max: SAFE }),
      note: str(p, 'note', 'payment'), visibility,
      requestId: str(p, 'request_id', 'payment', { nullable: true }),
      settlementId: str(p, 'settlement_id', 'payment', { nullable: true }),
      createdAt: t.iso, createdMs: Number(int(p, 'created_ms', 'payment', { min: 0n, max: SAFE })),
      seq: seqOf(p, 'payment') });
  });

  each(s, 'requests', 'state', (raw) => {
    const r = obj(raw, 'request');
    const id = str(r, 'id', 'request', { id: true });
    if (st.requests.has(id)) throw new StateError('duplicate request');
    const requesterId = str(r, 'requester_id', 'request');
    const payerId = str(r, 'payer_id', 'request');
    if (!st.users.has(requesterId) || !st.users.has(payerId)) throw new StateError('request: unknown user');
    const status = str(r, 'status', 'request');
    if (!STATUSES.includes(status)) throw new StateError('request: bad status');
    const t = stampOf(r, 'created_at', 'request');
    st.requests.set(id, { id, requesterId, payerId,
      amount: int(r, 'amount', 'request', { min: 0n, max: SAFE }),
      note: str(r, 'note', 'request'), status,
      paymentId: str(r, 'payment_id', 'request', { nullable: true }),
      splitId: str(r, 'split_id', 'request', { nullable: true }),
      createdAt: t.iso, createdMs: Number(int(r, 'created_ms', 'request', { min: 0n, max: SAFE })),
      seq: seqOf(r, 'request') });
  });

  for (const raw of arr(s, 'splits', 'state')) {
    const sp = obj(raw, 'split');
    const id = str(sp, 'id', 'split', { id: true });
    const creatorId = str(sp, 'creator_id', 'split');
    if (!st.users.has(creatorId) || st.splits.has(id)) throw new StateError('bad split');
    const shares = arr(sp, 'shares', 'split').map((x) => {
      obj(x, 'share');
      const userId = str(x, 'user_id', 'share');
      if (!st.users.has(userId)) throw new StateError('share: unknown user');
      return { userId, amount: int(x, 'amount', 'share', { min: 0n, max: SAFE }) };
    });
    const requestIds = arr(sp, 'request_ids', 'split').map((x) => {
      if (typeof x !== 'string' || !st.requests.has(x)) throw new StateError('split: unknown request');
      return x;
    });
    st.splits.set(id, { id, creatorId, amount: int(sp, 'amount', 'split', { min: 0n, max: SAFE }),
      note: str(sp, 'note', 'split'), shares, requestIds, createdAt: stampOf(sp, 'created_at', 'split').iso });
  }

  for (const raw of arr(s, 'settlements', 'state')) {
    const se = obj(raw, 'settlement');
    const id = str(se, 'id', 'settlement', { id: true });
    if (st.settlements.has(id)) throw new StateError('duplicate settlement');
    const paymentIds = arr(se, 'payment_ids', 'settlement').map((x) => {
      if (typeof x !== 'string' || !st.payments.has(x)) throw new StateError('settlement: unknown payment');
      return x;
    });
    st.settlements.set(id, { id, operatorId: str(se, 'operator_id', 'settlement'), paymentIds,
      committedAt: stampOf(se, 'committed_at', 'settlement').iso });
  }

  each(s, 'idempotency', 'state', (raw) => {
    const rec = obj(raw, 'idempotency record');
    const userId = str(rec, 'user_id', 'idempotency record');
    const scope = str(rec, 'scope', 'idempotency record');
    const key = str(rec, 'key', 'idempotency record');
    // Exports from earlier revisions hold the canonical body itself rather than its digest.
    let fp = str(rec, 'fingerprint', 'idempotency record');
    if (!fp.startsWith(DIGEST_PREFIX)) fp = digestCanonical(fp);
    const body = str(rec, 'body', 'idempotency record');
    const status = Number(int(rec, 'status', 'idempotency record', { min: 200n, max: 299n }));
    if (!st.users.has(userId)) throw new StateError('idempotency record: unknown user');
    try {
      JSON.parse(body);
    } catch (e) {
      throw new StateError('idempotency record: body is not JSON');
    }
    st.idem.set(st.idemKey(userId, scope, key), { userId, scope, key, fp, status, body });
  });

  // Cross references must resolve.
  for (const p of st.payments.values()) {
    if (p.requestId !== null && !st.requests.has(p.requestId)) throw new StateError('payment: unknown request');
    if (p.settlementId !== null && !st.settlements.has(p.settlementId)) throw new StateError('payment: unknown settlement');
  }
  for (const r of st.requests.values()) {
    if (r.paymentId !== null && !st.payments.has(r.paymentId)) throw new StateError('request: unknown payment');
    if (r.splitId !== null && !st.splits.has(r.splitId)) throw new StateError('request: unknown split');
  }
  return st;
}

module.exports = { State, StateError, stateFromFixture, stateFromExport, now, codePoints, HANDLE_RE, STATUSES };
