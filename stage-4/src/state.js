'use strict';
// In-memory service state. Every mutation happens synchronously on the single
// JavaScript thread, so each operation is one indivisible step: no other request
// can observe or interleave with a half-applied change.

const crypto = require('crypto');
const { JNum, intValue, digestCanonical, DIGEST_PREFIX } = require('./json');
const { isValidHash } = require('./passwords');
const { now, nowUs, parseInstant, formatUs, advanceTo } = require('./time');

const HANDLE_RE = /^[a-z0-9_]{1,20}$/;
const STATUSES = ['pending', 'paid', 'declined', 'cancelled'];
const AUTH_STATUSES = ['open', 'captured', 'voided', 'expired'];
const DEFAULT_TTL = 600n;
// Lifetimes are bounded so every expiry stays a four-digit-year RFC 3339 timestamp.
const MAX_TTL = 100n * 366n * 24n * 3600n;
const MAX_ID = 64;

// A problem with a fixture or an imported state. `type` errors are wrong JSON types (400).
class StateError extends Error {
  constructor(message, type = false) {
    super(message);
    this.type = type;
  }
}

// A stored instant: its text as given and its microsecond value.
function parseStamp(s) {
  const t = parseInstant(s);
  return t ? { us: t.us, iso: s } : null;
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
    this.authorizations = new Map(); // id -> authorization (insertion order)
    this.openAuths = new Set();      // authorizations whose status is 'open'
    this.ttlSeconds = DEFAULT_TTL;
    this.snapshots = new Map();      // statement token -> frozen statement (until reset)
    this.correctionBatches = new Set();
    this.seq = 0;
  }

  // ---- holds -----------------------------------------------------------------
  // A user's `held` is the sum of the remainders of their open authorizations; it is
  // kept up to date by every step that opens, captures, voids or expires a hold.

  available(u) {
    return u.balance - u.held;
  }

  remaining(a) {
    return a.status === 'open' ? a.amount - a.captured : 0n;
  }

  openHold(a) {
    this.authorizations.set(a.id, a);
    this.users.get(a.fromId).holdIds.push(a.id);
    if (a.status === 'open') {
      this.openAuths.add(a.id);
      this.users.get(a.fromId).held += this.remaining(a);
    }
  }

  // Closes an open hold with `status` at `at` ({ us, iso }), releasing whatever it
  // still holds. The close is recorded for historical views: a void or final capture
  // releases at its own time, expiry at the deadline.
  closeHold(a, status, at) {
    this.users.get(a.fromId).held -= this.remaining(a);
    a.status = status;
    this.openAuths.delete(a.id);
    if (status === 'expired') {
      a.closeKind = 'expired';
      a.closedUs = a.expiresUs;
      a.closedAt = a.expiresAt;
    } else {
      a.closeKind = status === 'voided' ? 'void' : 'final';
      a.closedUs = at.us;
      a.closedAt = at.iso;
    }
  }

  // Every open authorization whose expiry is at or before `us` becomes expired and
  // releases its remainder. Called at the start of every request, so all reads and
  // writes see expiry even when nothing happened at the deadline.
  expireDue(us) {
    for (const id of this.openAuths) {
      const a = this.authorizations.get(id);
      if (a.expiresUs <= us) this.closeHold(a, 'expired');
    }
  }

  // ---- ledger -------------------------------------------------------------
  // Every payment keeps its revisions; users keep an opening balance and the ids of
  // their payments and holds. `lv` (ledger version) changes whenever anything that
  // a user's history depends on changes, so derived views can be cached safely.

  addPayment(p) {
    if (p.refundOf === undefined) p.refundOf = null;
    p.refunded = 0n; // sum of the refunds of this payment
    if (!p.revs) {
      p.revs = [{ rev: 1, amount: p.amount, effUs: p.createdUs, effAt: p.createdAt, recUs: p.createdUs,
        recAt: p.createdAt, reason: '' }];
    }
    this.payments.set(p.id, p);
    for (const uid of p.fromId === p.toId ? [p.fromId] : [p.fromId, p.toId]) {
      const u = this.users.get(uid);
      u.paymentIds.push(p.id);
      this.touch(u, p.revs[p.revs.length - 1].recUs);
    }
  }

  touch(u, recUs) {
    u.lv++;
    u.cache = null;
    if (recUs > u.maxRecUs) u.maxRecUs = recUs;
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
    u.held = 0n;
    if (u.opening === undefined) u.opening = 0n;
    u.paymentIds = [];
    u.holdIds = [];
    u.lv = 0;
    u.cache = null;
    u.maxRecUs = -(2n ** 63n);
    this.users.set(u.id, u);
    this.byHandle.set(u.handle, u);
    this.byEmail.set(u.email.toLowerCase(), u);
  }

  idemKey(userId, scope, key) {
    return userId + '\u0000' + scope + '\u0000' + key;
  }

  // ---- views -------------------------------------------------------------

  // `amount` overrides the original amount (statements show the selected revision).
  paymentView(p, amount = p.amount) {
    return {
      payment_id: p.id,
      from_user_id: p.fromId,
      from_handle: this.users.get(p.fromId).handle,
      to_user_id: p.toId,
      to_handle: this.users.get(p.toId).handle,
      amount,
      currency: this.currency,
      note: p.note,
      visibility: p.visibility,
      request_id: p.requestId,
      authorization_id: p.authorizationId,
      settlement_id: p.settlementId,
      refund_of: p.refundOf,
      created_at: p.createdAt,
    };
  }

  authorizationView(a) {
    return {
      authorization_id: a.id,
      from_user_id: a.fromId,
      from_handle: this.users.get(a.fromId).handle,
      to_user_id: a.toId,
      to_handle: this.users.get(a.toId).handle,
      amount: a.amount,
      captured_amount: a.captured,
      remaining_amount: this.remaining(a),
      currency: this.currency,
      note: a.note,
      visibility: a.visibility,
      status: a.status,
      expires_at: a.expiresAt,
      closed_at: a.status === 'open' ? null : a.closedAt,
      payment_id: a.paymentIds.length ? a.paymentIds[a.paymentIds.length - 1] : null,
      payment_ids: a.paymentIds.slice(),
      created_at: a.createdAt,
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
        handle: u.handle, balance: u.balance, opening_balance: u.opening });
    }
    const payments = [];
    for (const p of this.payments.values()) {
      payments.push({ id: p.id, from_user_id: p.fromId, to_user_id: p.toId, amount: p.amount, note: p.note,
        visibility: p.visibility, request_id: p.requestId, authorization_id: p.authorizationId,
        settlement_id: p.settlementId, refund_of: p.refundOf, created_at: p.createdAt, seq: p.seq,
        revisions: p.revs.map((r) => ({ revision: r.rev, amount: r.amount, effective_at: r.effAt,
          recorded_at: r.recAt, reason: r.reason, correction_batch_id: r.batchId || null })) });
    }
    const requests = [];
    for (const r of this.requests.values()) {
      requests.push({ id: r.id, requester_id: r.requesterId, payer_id: r.payerId, amount: r.amount, note: r.note,
        status: r.status, payment_id: r.paymentId, split_id: r.splitId, created_at: r.createdAt, seq: r.seq });
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
    const authorizations = [];
    for (const a of this.authorizations.values()) {
      authorizations.push({ id: a.id, from_user_id: a.fromId, to_user_id: a.toId, amount: a.amount,
        captured_amount: a.captured, note: a.note, visibility: a.visibility, status: a.status,
        expires_at: a.expiresAt, payment_ids: a.paymentIds.slice(), created_at: a.createdAt, seq: a.seq,
        closed_at: a.closedAt, close_kind: a.closeKind, released_at: a.closedUs === null ? null : formatUs(a.closedUs) });
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
      authorization_ttl_seconds: this.ttlSeconds,
      seq: this.seq,
      users,
      tokens,
      settlement_operator_ids: [...this.operators],
      payments,
      requests,
      splits,
      settlements,
      authorizations,
      idempotency,
      // Statement snapshots: each is only an instant, a window and echoed text.
      snapshots: [...this.snapshots].map(([token, s]) => ({ token, user_id: s.userId, known_at_us: formatUs(s.K),
        from_us: s.fromUs === null ? null : formatUs(s.fromUs), to_us: formatUs(s.toUs), known_at: s.knownAt })),
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

// `authorization_ttl_seconds`: optional, default 600; when supplied it must be a positive
// integer number of seconds. Any other value is a validation failure (422).
function ttlOf(o, what) {
  if (!has(o, 'authorization_ttl_seconds')) return DEFAULT_TTL;
  const n = intValue(o.authorization_ttl_seconds);
  if (n === undefined || n < 1n || n > MAX_TTL) {
    throw new StateError(what + ': authorization_ttl_seconds must be a positive integer');
  }
  return n;
}

// One authorization from a fixture (`exported` false) or an export (`exported` true).
function authorizationFrom(st, raw, exported, fallbackStamp) {
  const a = obj(raw, 'authorization');
  const id = str(a, 'id', 'authorization', { id: true });
  if (st.authorizations.has(id)) throw new StateError('duplicate authorization id ' + id);
  const fromId = str(a, 'from_user_id', 'authorization');
  const toId = str(a, 'to_user_id', 'authorization');
  if (!st.users.has(fromId) || !st.users.has(toId)) throw new StateError('authorization ' + id + ': unknown user');
  if (fromId === toId) throw new StateError('authorization ' + id + ': payer and receiver are the same');
  const amount = int(a, 'amount', 'authorization', { min: 1n, max: SAFE });
  const captured = int(a, 'captured_amount', 'authorization', { min: 0n, max: amount, ...(exported ? {} : { dflt: 0n }) });
  const note = str(a, 'note', 'authorization', exported ? {} : { dflt: '' });
  const visibility = str(a, 'visibility', 'authorization', exported ? {} : { dflt: 'public' });
  if (visibility !== 'public' && visibility !== 'private') throw new StateError('authorization ' + id + ': bad visibility');
  const status = str(a, 'status', 'authorization', exported ? {} : { dflt: 'open' });
  if (!AUTH_STATUSES.includes(status)) throw new StateError('authorization ' + id + ': bad status');
  if (status === 'open' && captured >= amount) throw new StateError('authorization ' + id + ': nothing left to hold');
  const expires = parseStamp(str(a, 'expires_at', 'authorization'));
  if (!expires) throw new StateError('authorization ' + id + ': bad expires_at');
  let created;
  if (exported) {
    created = parseStamp(str(a, 'created_at', 'authorization'));
    if (!created) throw new StateError('authorization ' + id + ': bad created_at');
  } else {
    created = stampOrNow(a, 'authorization', fallbackStamp);
  }
  const paymentIds = arr(a, 'payment_ids', 'authorization', exported ? {} : { dflt: [] }).map((x) => {
    if (typeof x !== 'string') throw new StateError('authorization ' + id + ': bad payment_ids', true);
    return x;
  });
  const auth = { id, fromId, toId, amount, captured, note, visibility, status,
    expiresAt: expires.iso, expiresUs: expires.us, paymentIds, createdAt: created.iso, createdUs: created.us,
    seq: exported ? Number(int(a, 'seq', 'authorization', { min: 0n, max: SAFE })) : st.nextSeq(),
    closeKind: null, closedUs: null, closedAt: null };
  // How a closed hold closed, for historical views. Stage-3 exports record it. A seeded
  // closed hold has no recorded lifecycle and holds nothing in any view; holds closed
  // in older exports are reconstructed as far as their data allows.
  if (status !== 'open') {
    const kind = exported && has(a, 'close_kind') ? str(a, 'close_kind', 'authorization', { nullable: true }) : null;
    const closed = exported && has(a, 'closed_at') && a.closed_at !== null
      ? parseStamp(str(a, 'closed_at', 'authorization')) : null;
    if (kind !== null && !['void', 'final', 'expired'].includes(kind)) throw new StateError('authorization ' + id + ': bad close_kind');
    const expiredByClock = status === 'expired' && auth.expiresUs <= nowUs();
    const shown = expiredByClock ? { us: auth.expiresUs, iso: auth.expiresAt } : { us: auth.createdUs, iso: auth.createdAt };
    const released = exported && has(a, 'released_at') && a.released_at !== null
      ? parseStamp(str(a, 'released_at', 'authorization')) : null;
    if (exported && kind !== null && closed) {
      Object.assign(auth, { closeKind: kind, closedUs: (released || closed).us, closedAt: closed.iso });
    } else if (exported && expiredByClock) {
      Object.assign(auth, { closeKind: 'expired', closedUs: auth.expiresUs, closedAt: auth.expiresAt });
    } else if (exported && status === 'captured' && paymentIds.length) {
      // Closed by its last capture.
      const last = st.payments.get(paymentIds[paymentIds.length - 1]);
      Object.assign(auth, { closeKind: 'final', closedUs: last.createdUs, closedAt: last.createdAt });
    } else {
      // No lifecycle to go on: released when created, so it never holds anything.
      Object.assign(auth, { closeKind: 'void', closedUs: auth.createdUs, closedAt: shown.iso });
    }
  }
  return auth;
}

// Applies expiry as of now and checks that no wallet holds more than it has.
function settleHolds(st) {
  st.expireDue(nowUs());
  for (const u of st.users.values()) {
    if (u.held > u.balance) throw new StateError('open holds of ' + u.id + ' exceed its balance');
  }
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
    if (t.us > stamp.us) throw new StateError('payment ' + id + ': created_at is in the future');
    st.addPayment({ id, fromId, toId, amount, note, visibility,
      requestId: str(p, 'request_id', 'payment', { dflt: null, nullable: true }),
      authorizationId: str(p, 'authorization_id', 'payment', { dflt: null, nullable: true }),
      settlementId: null, createdAt: t.iso, createdUs: t.us, seq: st.nextSeq() });
  }
  // A seeded balance is the balance after every seeded payment: the opening balance is
  // what the wallet held before any of them.
  for (const u of st.users.values()) u.opening = u.balance - netEffect(st, u);

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
      splitId: null, createdAt: t.iso, createdUs: t.us, seq: st.nextSeq() });
  }

  for (const v of arr(fx, 'settlement_operator_ids', 'fixture', { dflt: [] })) {
    if (typeof v !== 'string') throw new StateError('settlement_operator_ids must hold strings', true);
    st.operators.add(v);
  }

  st.ttlSeconds = ttlOf(fx, 'fixture');
  for (const raw of arr(fx, 'authorizations', 'fixture', { dflt: [] })) {
    st.openHold(authorizationFrom(st, raw, false, stamp));
  }
  settleHolds(st);
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
    const opening = has(u, 'opening_balance') ? int(u, 'opening_balance', 'user', { min: -SAFE, max: SAFE }) : null;
    st.addUser({ id, email, passwordHash, displayName, handle, balance, opening: opening === null ? undefined : opening,
      openingKnown: opening !== null });
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
    const amount = int(p, 'amount', 'payment', { min: 0n, max: SAFE });
    st.addPayment({ id, fromId, toId, amount,
      note: str(p, 'note', 'payment'), visibility,
      requestId: str(p, 'request_id', 'payment', { nullable: true }),
      // Stage-1 exports have no authorizations: absent means null.
      authorizationId: str(p, 'authorization_id', 'payment', { nullable: true, dflt: null }),
      settlementId: str(p, 'settlement_id', 'payment', { nullable: true }),
      // Exports before stage 4 have no refunds: absent means null.
      refundOf: str(p, 'refund_of', 'payment', { nullable: true, dflt: null }),
      createdAt: t.iso, createdUs: t.us, seq: seqOf(p, 'payment'),
      // Stage-1/2 exports have no revisions: the payment is its own revision 1.
      revs: has(p, 'revisions') ? revisionsFrom(p, id, amount, t, st.correctionBatches) : undefined });
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
      createdAt: t.iso, createdUs: t.us, seq: seqOf(r, 'request') });
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

  // Opening balances: stage-3 exports carry them and must agree with the balances and
  // revisions; for stage-1/2 exports they follow from the balances and the payments.
  for (const u of st.users.values()) {
    if (u.openingKnown) {
      if (u.opening + netEffect(st, u) !== u.balance) throw new StateError('user ' + u.id + ': ledger does not add up');
    } else {
      u.opening = u.balance - netEffect(st, u);
    }
    delete u.openingKnown;
  }

  // Stage-1 exports carry neither a lifetime nor authorizations.
  st.ttlSeconds = ttlOf(s, 'state');
  for (const raw of arr(s, 'authorizations', 'state', { dflt: [] })) {
    const a = authorizationFrom(st, raw, true);
    for (const pid of a.paymentIds) {
      if (!st.payments.has(pid)) throw new StateError('authorization: unknown payment');
    }
    st.openHold(a);
  }
  settleHolds(st);

  // Refunds: each names a payment that is not itself a refund, in the opposite
  // direction, and the refunds of a payment never exceed its current amount.
  for (const p of st.payments.values()) {
    if (p.refundOf === null) continue;
    const t = st.payments.get(p.refundOf);
    if (!t || t.refundOf !== null || t.fromId !== p.toId || t.toId !== p.fromId) {
      throw new StateError('payment ' + p.id + ': bad refund_of');
    }
    t.refunded += p.revs[p.revs.length - 1].amount;
  }
  for (const p of st.payments.values()) {
    if (p.refunded > p.revs[p.revs.length - 1].amount) throw new StateError('payment ' + p.id + ': refunds exceed it');
  }

  // Snapshots (stage-4 exports): the same instant and window page the same entries.
  let latestUs = 0n;
  for (const raw of arr(s, 'snapshots', 'state', { dflt: [] })) {
    const sn = obj(raw, 'snapshot');
    const token = str(sn, 'token', 'snapshot');
    const userId = str(sn, 'user_id', 'snapshot');
    if (!token || st.snapshots.has(token) || !st.users.has(userId)) throw new StateError('bad snapshot');
    const K = parseStamp(str(sn, 'known_at_us', 'snapshot'));
    const to = parseStamp(str(sn, 'to_us', 'snapshot'));
    const fromText = str(sn, 'from_us', 'snapshot', { nullable: true });
    const from = fromText === null ? null : parseStamp(fromText);
    if (!K || !to || (fromText !== null && !from)) throw new StateError('bad snapshot instant');
    st.snapshots.set(token, { userId, K: K.us, fromUs: from ? from.us : null, toUs: to.us,
      knownAt: str(sn, 'known_at', 'snapshot', { nullable: true }) });
    if (K.us > latestUs) latestUs = K.us;
  }
  // Everything recorded from now on must come after every imported instant, or it
  // could appear in an imported snapshot or reorder imported revisions.
  for (const p of st.payments.values()) {
    const r = p.revs[p.revs.length - 1];
    if (r.recUs > latestUs) latestUs = r.recUs;
  }
  for (const a of st.authorizations.values()) {
    if (a.createdUs > latestUs) latestUs = a.createdUs;
    if (a.closeKind !== null && a.closeKind !== 'expired' && a.closedUs > latestUs) latestUs = a.closedUs;
  }
  advanceTo(latestUs);

  // Cross references must resolve.
  for (const p of st.payments.values()) {
    if (p.authorizationId !== null && !st.authorizations.has(p.authorizationId)) {
      throw new StateError('payment: unknown authorization');
    }
    if (p.requestId !== null && !st.requests.has(p.requestId)) throw new StateError('payment: unknown request');
    if (p.settlementId !== null && !st.settlements.has(p.settlementId)) throw new StateError('payment: unknown settlement');
  }
  for (const r of st.requests.values()) {
    if (r.paymentId !== null && !st.payments.has(r.paymentId)) throw new StateError('request: unknown payment');
    if (r.splitId !== null && !st.splits.has(r.splitId)) throw new StateError('request: unknown split');
  }
  return st;
}

// Net effect on `u` of the latest revision of every payment.
function netEffect(st, u) {
  let net = 0n;
  for (const pid of u.paymentIds) {
    const p = st.payments.get(pid);
    const amt = p.revs[p.revs.length - 1].amount;
    if (p.fromId === u.id) net -= amt;
    if (p.toId === u.id) net += amt;
  }
  return net;
}

// Revision list from a stage-3 export: revision numbers 1..n in order, revision 1 is
// the original payment, recorded times strictly increase.
function revisionsFrom(p, id, amount, created, batchIds) {
  const out = [];
  for (const raw of arr(p, 'revisions', 'payment')) {
    const r = obj(raw, 'revision');
    const rev = Number(int(r, 'revision', 'revision', { min: 1n, max: 1000000000n }));
    const eff = parseStamp(str(r, 'effective_at', 'revision'));
    const rec = parseStamp(str(r, 'recorded_at', 'revision'));
    if (!eff || !rec) throw new StateError('payment ' + id + ': bad revision time');
    const entry = { rev, amount: int(r, 'amount', 'revision', { min: 0n, max: SAFE }), effUs: eff.us, effAt: eff.iso,
      recUs: rec.us, recAt: rec.iso, reason: str(r, 'reason', 'revision') };
    const batchId = str(r, 'correction_batch_id', 'revision', { nullable: true, dflt: null });
    if (batchId !== null) {
      entry.batchId = batchId;
      batchIds.add(batchId);
    }
    if (entry.rev !== out.length + 1) throw new StateError('payment ' + id + ': revisions out of order');
    if (out.length && entry.recUs <= out[out.length - 1].recUs) throw new StateError('payment ' + id + ': recorded times must increase');
    out.push(entry);
  }
  if (!out.length || out[0].amount !== amount || out[0].effUs !== created.us) {
    throw new StateError('payment ' + id + ': revision 1 must be the original payment');
  }
  return out;
}

module.exports = { State, StateError, stateFromFixture, stateFromExport, now, codePoints, HANDLE_RE, STATUSES,
  AUTH_STATUSES, parseStamp };
