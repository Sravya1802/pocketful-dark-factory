'use strict';
// Request handlers. Each handler runs after the whole body has been read; from then
// on everything that reads or changes wallet state is synchronous, so a request is
// applied as a single indivisible step against the current state. Only password
// hashing is asynchronous, and it never runs between a state check and its write.

const crypto = require('crypto');
const { JNum, JsonSyntaxError, parse, parseLarge, intValue, fingerprint, stringify, stringifyPieces } = require('./json');
const { hashPassword, verifyPassword, hashSeedPassword } = require('./passwords');
const { State, StateError, stateFromFixture, stateFromExport, codePoints, STATUSES, AUTH_STATUSES } = require('./state');
const { now, nowUs, stamp, parseInstant } = require('./time');
const ledger = require('./ledger');
const { uiResponse, wantsHtml } = require('./ui');

class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const bad = (msg) => new ApiError(400, 'malformed_request', msg);
const invalid = (msg) => new ApiError(422, 'validation_failed', msg);
const notFound = (msg = 'not found') => new ApiError(404, 'not_found', msg);
const forbidden = (msg = 'not permitted') => new ApiError(403, 'forbidden', msg);

const MAX_AMOUNT = 1000000000n;
const MAX_NOTE = 200;
const MAX_KEY = 255;

let state = new State('EUR', 2);

// ---- body and field helpers -------------------------------------------------

function has(o, k) {
  return Object.prototype.hasOwnProperty.call(o, k);
}

// `body` is the request body already decoded as UTF-8 (null when it was not valid UTF-8).
function parseBody(body, parser = parse) {
  if (body === null) throw bad('request body is not valid UTF-8');
  try {
    return parser(body);
  } catch (e) {
    if (e instanceof JsonSyntaxError || e instanceof RangeError) throw bad('request body is not valid JSON');
    throw e;
  }
}

function parseObjectBody(body) {
  const v = parseBody(body);
  if (v === null || typeof v !== 'object' || Array.isArray(v) || v instanceof JNum) {
    throw bad('request body must be a JSON object');
  }
  return v;
}

function reqString(body, k) {
  if (!has(body, k)) throw invalid(k + ' is required');
  if (typeof body[k] !== 'string') throw bad(k + ' must be a string');
  return body[k];
}

function amountField(o, k = 'amount') {
  if (!has(o, k)) throw invalid(k + ' is required');
  const n = intValue(o[k]);
  if (n === undefined) throw invalid(k + ' must be an integer number of minor units');
  if (n < 1n || n > MAX_AMOUNT) throw invalid(k + ' must be between 1 and ' + MAX_AMOUNT);
  return n;
}

function noteField(o) {
  if (!has(o, 'note')) return '';
  const v = o.note;
  if (typeof v !== 'string') throw invalid('note must be a string');
  if (codePoints(v) > MAX_NOTE) throw invalid('note must be at most ' + MAX_NOTE + ' characters');
  return v;
}

function visibilityField(o) {
  if (!has(o, 'visibility')) return 'public';
  const v = o.visibility;
  if (v !== 'public' && v !== 'private') throw invalid('visibility must be "public" or "private"');
  return v;
}

function queryInt(q, k, dflt, min, max) {
  const v = q.get(k);
  if (v === null) return dflt;
  if (!/^[0-9]+$/.test(v)) throw invalid(k + ' must be written as decimal digits');
  const n = BigInt(v);
  if (n < min || (max !== undefined && n > max)) throw invalid(k + ' out of range');
  return n;
}

function page(q, items) {
  const limit = queryInt(q, 'limit', 50n, 1n, 200n);
  const offset = queryInt(q, 'offset', 0n, 0n);
  const total = BigInt(items.length);
  if (offset >= total) return { slice: [], hasMore: false };
  const end = offset + limit;
  return { slice: items.slice(Number(offset), Number(end > total ? total : end)), hasMore: end < total };
}

function newestFirst(a, b) {
  if (a.createdUs !== b.createdUs) return a.createdUs < b.createdUs ? 1 : -1;
  return b.seq - a.seq;
}

// ---- authentication -----------------------------------------------------------

function authenticate(req) {
  const h = req.headers.authorization;
  if (typeof h !== 'string') throw new ApiError(401, 'unauthenticated', 'bearer token required');
  const m = /^Bearer +([^\s]+) *$/i.exec(h);
  if (!m) throw new ApiError(401, 'unauthenticated', 'malformed authorization header');
  const uid = state.tokens.get(m[1]);
  const user = uid === undefined ? undefined : state.users.get(uid);
  if (!user) throw new ApiError(401, 'unauthenticated', 'unknown token');
  return user;
}

function idempotencyKey(req) {
  const key = req.headers['idempotency-key'];
  if (typeof key !== 'string' || key.length === 0) {
    throw new ApiError(400, 'missing_idempotency_key', 'Idempotency-Key header is required');
  }
  if (codePoints(key) > MAX_KEY) throw invalid('Idempotency-Key must be 1 to ' + MAX_KEY + ' characters');
  return key;
}

// Runs `fn(body)` at most once per (caller, method and path, key). A recorded success
// is replayed with 200 and its original body; the same key with a different body on
// the same path is refused. The same key on another path is an unrelated request.
// Failures throw before anything is recorded, so they claim nothing.
function idempotent(ctx, user, key, fn) {
  const body = parseObjectBody(ctx.body);
  const scope = ctx.method + ' ' + ctx.path;
  const fp = fingerprint(body);
  const k = state.idemKey(user.id, scope, key);
  const rec = state.idem.get(k);
  if (rec) {
    if (rec.fp !== fp) {
      throw new ApiError(409, 'idempotency_key_reuse', 'Idempotency-Key was already used for a different request');
    }
    return { status: 200, text: rec.body };
  }
  const result = fn(body);
  const text = stringify(result);
  state.idem.set(k, { userId: user.id, scope, key, fp, status: 201, body: text });
  return { status: 201, text };
}

// ---- money movement -------------------------------------------------------------

function userByHandle(handle) {
  return state.byHandle.get(handle);
}

function recordPayment(from, to, amount, note, visibility, requestId, settlementId, stamp, authorizationId = null) {
  const p = {
    id: state.newId('p_', state.payments),
    fromId: from.id, toId: to.id, amount, note, visibility,
    requestId, authorizationId, settlementId, createdAt: stamp.iso, createdUs: stamp.us, seq: state.nextSeq(),
  };
  from.balance -= amount;
  to.balance += amount;
  state.addPayment(p);
  return p;
}

// ---- handlers ----------------------------------------------------------------------

// Hands over a large request text without keeping a reference, so it can be collected
// as soon as it has been parsed.
function takeBody(ctx) {
  const text = ctx.body;
  ctx.body = null;
  return text;
}

async function reset(ctx) {
  const fx = parseBody(takeBody(ctx), parseLarge);
  let built;
  try {
    built = stateFromFixture(fx);
  } catch (e) {
    if (e instanceof StateError) throw e.type ? bad(e.message) : invalid(e.message);
    throw e;
  }
  const { state: next, passwords } = built;
  const users = [...passwords.keys()].map((uid) => next.users.get(uid));
  const hashes = await Promise.all(users.map((u) => hashSeedPassword(u.id, u.email, passwords.get(u.id))));
  users.forEach((u, i) => { u.passwordHash = hashes[i]; });
  state = next;
  return { status: 204 };
}

// The snapshot is taken and serialized synchronously, so it is atomic; it is sent as
// pieces so its size is not limited by the engine's maximum string length.
function exportState() {
  return { status: 200,
    pieces: stringifyPieces({ track: 'pocketful', format_version: 1, state: state.exportState() }) };
}

function importState(ctx) {
  const env = parseBody(takeBody(ctx), parseLarge);
  if (env === null || typeof env !== 'object' || Array.isArray(env) || env instanceof JNum) {
    throw invalid('import body must be an export object');
  }
  if (env.track !== 'pocketful') throw invalid('track must be "pocketful"');
  if (intValue(env.format_version) !== 1n) throw invalid('format_version must be 1');
  if (!has(env, 'state')) throw invalid('state is required');
  let next;
  try {
    next = stateFromExport(env.state);
  } catch (e) {
    throw invalid('invalid state: ' + e.message);
  }
  state = next;
  return { status: 204 };
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+$/;

function deriveHandle(email) {
  const local = email.slice(0, email.indexOf('@')).toLowerCase();
  let out = '';
  for (const ch of local) out += /^[a-z0-9_]$/.test(ch) ? ch : '_';
  return [...out].slice(0, 20).join('');
}

function credentialFields(body) {
  for (const k of ['email', 'password']) {
    if (has(body, k) && typeof body[k] !== 'string') throw bad(k + ' must be a string');
  }
  return { email: reqString(body, 'email'), password: reqString(body, 'password') };
}

function signupConflict(email, handle) {
  if (state.byEmail.has(email.toLowerCase())) return new ApiError(409, 'email_taken', 'email already registered');
  if (state.byHandle.has(handle)) return new ApiError(409, 'handle_taken', 'handle ' + handle + ' is already taken');
  return null;
}

async function signup(ctx) {
  const body = parseObjectBody(ctx.body);
  if (has(body, 'display_name') && typeof body.display_name !== 'string') throw bad('display_name must be a string');
  const { email, password } = credentialFields(body);
  const displayName = reqString(body, 'display_name');
  if (!EMAIL_RE.test(email)) throw invalid('email must look like local@domain');
  if (codePoints(password) < 8) throw invalid('password must be at least 8 characters');
  const handle = deriveHandle(email);
  const early = signupConflict(email, handle);
  if (early) throw early;
  const passwordHash = await hashPassword(password);
  // Hashing is done; check and insert in one synchronous step.
  const conflict = signupConflict(email, handle);
  if (conflict) throw conflict;
  const user = { id: state.newId('u_', state.users), email, passwordHash, displayName, handle, balance: 0n };
  state.addUser(user);
  const token = state.newToken(user.id);
  return { status: 201, body: { user_id: user.id, display_name: user.displayName, token } };
}

async function login(ctx) {
  const body = parseObjectBody(ctx.body);
  const { email, password } = credentialFields(body);
  const user = state.byEmail.get(email.toLowerCase());
  const fail = new ApiError(401, 'unauthenticated', 'wrong email or password');
  if (!user) throw fail;
  if (!(await verifyPassword(password, user.passwordHash))) throw fail;
  // The state may have been replaced while hashing; only issue a token to a live account.
  if (state.users.get(user.id) !== user) throw fail;
  const token = state.newToken(user.id);
  return { status: 200, body: { user_id: user.id, display_name: user.displayName, token } };
}

// An optional instant query parameter: null when absent; present but not an RFC 3339
// instant with an offset (including empty) is 422.
function instantParam(q, name) {
  if (!q.has(name)) return null;
  const text = q.get(name);
  const t = parseInstant(text);
  if (!t) throw invalid(name + ' must be an RFC 3339 instant with an offset');
  return { text, us: t.us, ceilUs: t.ceilUs };
}

function me(ctx) {
  const u = authenticate(ctx.req);
  const asOf = instantParam(ctx.query, 'as_of');
  const knownAt = instantParam(ctx.query, 'known_at');
  if (!asOf && !knownAt) {
    return { status: 200, body: { user_id: u.id, display_name: u.displayName, handle: u.handle,
      balance: u.balance, total: u.balance, available: state.available(u), held: u.held,
      currency: state.currency, minor_units: state.minorUnits } };
  }
  // The view: revisions known at `known_at` (default: when this read began), applied
  // by effective time up to and including `as_of` (default: when this read began).
  const T = asOf ? asOf.us : ctx.now;
  const K = knownAt ? knownAt.us : ctx.now;
  const total = ledger.totalAt(state, u, T, K);
  const held = ledger.heldAt(state, u, T, K);
  const body = { user_id: u.id, display_name: u.displayName, handle: u.handle,
    balance: total, total, available: total - held, held, currency: state.currency, minor_units: state.minorUnits };
  if (asOf) body.as_of = asOf.text;
  if (knownAt) body.known_at = knownAt.text;
  return { status: 200, body };
}

// ---- statements ---------------------------------------------------------------------

function statement(ctx) {
  const u = authenticate(ctx.req);
  const q = ctx.query;
  if (q.has('snapshot')) {
    if (q.has('from') || q.has('to') || q.has('known_at')) {
      throw invalid('a snapshot already fixes from, to and known_at; send only limit and offset');
    }
    const token = q.get('snapshot');
    const snap = state.snapshots.get(token);
    if (!snap || snap.userId !== u.id) throw notFound('no such statement snapshot');
    return { status: 200, body: statementPage(snap, token, q) };
  }
  const from = instantParam(q, 'from');
  const to = instantParam(q, 'to');
  const knownAt = instantParam(q, 'known_at');
  const K = knownAt ? knownAt.us : ctx.now;
  const toUs = to ? to.ceilUs : ctx.now; // half-open: effective strictly before `to`
  const fromUs = from ? from.ceilUs : null; // effective at or after `from`
  pageBounds(q); // validate limit and offset before freezing anything
  // The view is immutable, so the snapshot only records where the window lies in it.
  const view = ledger.userLedger(state, u, K);
  const start = fromUs === null ? 0 : ledger.lowerBound(view.effs, fromUs);
  // A window that ends before it starts is empty, with both balances taken at `from`.
  const end = Math.max(start, ledger.lowerBound(view.effs, toUs));
  const snap = { userId: u.id, view, start, end, knownAt: knownAt ? knownAt.text : null };
  const token = state.newId('st3_', state.snapshots) + crypto.randomBytes(8).toString('hex');
  state.snapshots.set(token, snap);
  return { status: 200, body: statementPage(snap, token, q) };
}

function pageBounds(q) {
  const limit = queryInt(q, 'limit', 50n, 1n, 200n);
  const offset = queryInt(q, 'offset', 0n, 0n);
  return { limit, offset };
}

function statementPage(snap, token, q) {
  const { limit, offset } = pageBounds(q);
  const { view, start, end } = snap;
  const count = BigInt(end - start);
  const first = offset >= count ? end : start + Number(offset);
  const last = offset + limit >= count ? end : start + Number(offset + limit);
  const entries = [];
  for (let i = first; i < last; i++) {
    const { p, r, d } = view.items[i];
    entries.push({ payment: state.paymentView(p, r.amount), delta: d, balance_after: view.opening + view.prefix[i + 1],
      revision: r.rev, effective_at: r.effAt, recorded_at: r.recAt });
  }
  const body = { opening_balance: view.opening + view.prefix[start], entries,
    closing_balance: view.opening + view.prefix[end], has_more: offset + limit < count, snapshot: token };
  if (snap.knownAt !== null) body.known_at = snap.knownAt;
  return body;
}

// ---- corrections -------------------------------------------------------------------

function findOwnPayment(user, id) {
  const p = state.payments.get(id);
  if (!p) throw notFound('no such payment');
  if (p.fromId !== user.id) throw forbidden('only the sender may correct this payment');
  return p;
}

function correctPayment(ctx, id) {
  const user = authenticate(ctx.req);
  const p = findOwnPayment(user, id);
  const key = idempotencyKey(ctx.req);
  return idempotent(ctx, user, key, (body) => {
    for (const k of ['expected_revision', 'amount', 'effective_at', 'reason']) {
      if (!has(body, k)) throw invalid(k + ' is required');
    }
    const expected = intValue(body.expected_revision);
    if (expected === undefined || expected < 1n) throw invalid('expected_revision must be a positive integer');
    const amount = intValue(body.amount);
    if (amount === undefined || amount < 0n || amount > MAX_AMOUNT) {
      throw invalid('amount must be an integer from 0 to ' + MAX_AMOUNT);
    }
    const eff = parseInstant(body.effective_at);
    if (!eff) throw invalid('effective_at must be an RFC 3339 instant with an offset');
    if (eff.us > nowUs()) throw invalid('effective_at must not be later than now');
    if (typeof body.reason !== 'string' || codePoints(body.reason) < 1 || codePoints(body.reason) > 200) {
      throw invalid('reason must be 1 to 200 characters');
    }
    if (p.settlementId !== null || p.authorizationId !== null) {
      throw new ApiError(422, 'linked_payment_immutable', p.settlementId !== null
        ? 'settlement members cannot be corrected one by one' : 'captures cannot be corrected');
    }
    const latest = p.revs[p.revs.length - 1];
    if (expected !== BigInt(latest.rev)) {
      throw new ApiError(409, 'stale_revision', 'the latest revision is ' + latest.rev);
    }
    // The difference moves between the same two wallets: an increase debits the
    // sender, a decrease debits the receiver. Today's funds first, then history.
    const sender = state.users.get(p.fromId);
    const receiver = state.users.get(p.toId);
    const diff = amount - latest.amount;
    const debited = diff > 0n ? sender : receiver;
    const debit = diff > 0n ? diff : -diff;
    if (state.available(debited) < debit) {
      throw new ApiError(409, 'insufficient_funds', 'available balance is below the correction difference');
    }
    const override = { p, amount, effUs: eff.us };
    if (!ledger.historyHolds(state, sender, override) || !ledger.historyHolds(state, receiver, override)) {
      throw new ApiError(409, 'historical_overdraft', 'the correction would make a balance negative in the past');
    }
    let recUs = nowUs();
    if (recUs <= latest.recUs) recUs = latest.recUs + 1n;
    const recorded = stamp(recUs);
    const r = { rev: latest.rev + 1, amount, effUs: eff.us, effAt: body.effective_at, recUs, recAt: recorded.iso,
      reason: body.reason };
    p.revs.push(r);
    sender.balance -= diff;
    receiver.balance += diff;
    state.touch(sender, recUs);
    state.touch(receiver, recUs);
    return { payment_id: p.id, revision: r.rev, amount: r.amount, effective_at: r.effAt, recorded_at: r.recAt,
      reason: r.reason };
  });
}

function paymentRevisions(ctx, id) {
  const user = authenticate(ctx.req);
  const p = state.payments.get(id);
  // Only the two parties may read the history; to anyone else the payment is invisible.
  if (!p || (p.fromId !== user.id && p.toId !== user.id)) throw notFound('no such payment');
  return { status: 200, body: { revisions: p.revs.map((r) => ({ payment_id: p.id, revision: r.rev, amount: r.amount,
    effective_at: r.effAt, recorded_at: r.recAt, reason: r.reason })) } };
}

function createPayment(ctx) {
  const user = authenticate(ctx.req);
  const key = idempotencyKey(ctx.req);
  return idempotent(ctx, user, key, (body) => {
    const toHandle = reqString(body, 'to_handle');
    const amount = amountField(body);
    const note = noteField(body);
    const visibility = visibilityField(body);
    if (toHandle === user.handle) throw new ApiError(422, 'self_payment', 'cannot pay yourself');
    const to = userByHandle(toHandle);
    if (!to) throw notFound('no user has handle ' + toHandle);
    if (state.available(user) < amount) throw new ApiError(409, 'insufficient_funds', 'available balance is below amount');
    return state.paymentView(recordPayment(user, to, amount, note, visibility, null, null, now()));
  });
}

function createRequest(ctx) {
  const user = authenticate(ctx.req);
  const key = idempotencyKey(ctx.req);
  return idempotent(ctx, user, key, (body) => {
    const payerHandle = reqString(body, 'payer_handle');
    const amount = amountField(body);
    const note = noteField(body);
    if (payerHandle === user.handle) throw new ApiError(422, 'self_request', 'cannot request money from yourself');
    const payer = userByHandle(payerHandle);
    if (!payer) throw notFound('no user has handle ' + payerHandle);
    const t = now();
    const r = { id: state.newId('rq_', state.requests), requesterId: user.id, payerId: payer.id, amount, note,
      status: 'pending', paymentId: null, splitId: null, createdAt: t.iso, createdUs: t.us, seq: state.nextSeq() };
    state.requests.set(r.id, r);
    return state.requestView(r);
  });
}

function findRequest(id) {
  const r = state.requests.get(id);
  if (!r) throw notFound('no such request');
  return r;
}

function payRequest(ctx, id) {
  const user = authenticate(ctx.req);
  const key = idempotencyKey(ctx.req);
  return idempotent(ctx, user, key, (body) => {
    const visibility = visibilityField(body);
    const r = findRequest(id);
    if (r.payerId !== user.id) throw forbidden('only the payer may pay this request');
    if (r.status !== 'pending') throw new ApiError(409, 'request_not_pending', 'request is ' + r.status);
    if (state.available(user) < r.amount) {
      throw new ApiError(409, 'insufficient_funds', 'available balance is below amount');
    }
    const requester = state.users.get(r.requesterId);
    const p = recordPayment(user, requester, r.amount, r.note, visibility, r.id, null, now());
    r.status = 'paid';
    r.paymentId = p.id;
    return state.paymentView(p);
  });
}

function closeRequest(ctx, id, target) {
  const user = authenticate(ctx.req);
  const r = findRequest(id);
  const actorId = target === 'declined' ? r.payerId : r.requesterId;
  if (actorId !== user.id) {
    throw forbidden(target === 'declined' ? 'only the payer may decline' : 'only the requester may cancel');
  }
  if (r.status !== 'pending' && r.status !== target) {
    throw new ApiError(409, 'request_not_pending', 'request is ' + r.status);
  }
  r.status = target;
  return { status: 200, body: state.requestView(r) };
}

function listRequests(ctx) {
  const user = authenticate(ctx.req);
  const q = ctx.query;
  const direction = q.get('direction');
  if (direction !== null && direction !== 'incoming' && direction !== 'outgoing') {
    throw invalid('direction must be incoming or outgoing');
  }
  const status = q.get('status');
  if (status !== null && !STATUSES.includes(status)) throw invalid('unknown status');
  const items = [];
  for (const r of state.requests.values()) {
    const incoming = r.payerId === user.id;
    const outgoing = r.requesterId === user.id;
    if (direction === 'incoming' ? !incoming : direction === 'outgoing' ? !outgoing : !(incoming || outgoing)) continue;
    if (status !== null && r.status !== status) continue;
    items.push(r);
  }
  items.sort(newestFirst);
  const { slice, hasMore } = page(q, items);
  return { status: 200, body: { requests: slice.map((r) => state.requestView(r)), has_more: hasMore } };
}

function createSplit(ctx) {
  const user = authenticate(ctx.req);
  const key = idempotencyKey(ctx.req);
  return idempotent(ctx, user, key, (body) => {
    if (has(body, 'participant_handles') && !Array.isArray(body.participant_handles)) {
      throw bad('participant_handles must be an array');
    }
    const amount = amountField(body);
    if (!has(body, 'participant_handles')) throw invalid('participant_handles is required');
    const handles = body.participant_handles;
    for (const h of handles) if (typeof h !== 'string') throw bad('participant_handles must hold strings');
    if (handles.length === 0) throw invalid('participant_handles must not be empty');
    if (new Set(handles).size !== handles.length) throw invalid('participant_handles contains a duplicate');
    const note = noteField(body);
    const people = handles.map((h) => {
      const u = userByHandle(h);
      if (!u) throw notFound('no user has handle ' + h);
      return u;
    });
    const n = BigInt(people.length);
    const base = amount / n;
    const extra = amount % n;
    const shares = people.map((u, i) => ({ userId: u.id, amount: BigInt(i) < extra ? base + 1n : base }));
    const t = now();
    const split = { id: state.newId('sp_', state.splits), creatorId: user.id, amount, note, shares,
      requestIds: [], createdAt: t.iso };
    for (const s of shares) {
      if (s.userId === user.id) continue;
      const r = { id: state.newId('rq_', state.requests), requesterId: user.id, payerId: s.userId, amount: s.amount,
        note, status: 'pending', paymentId: null, splitId: split.id, createdAt: t.iso, createdUs: t.us,
        seq: state.nextSeq() };
      state.requests.set(r.id, r);
      split.requestIds.push(r.id);
    }
    state.splits.set(split.id, split);
    return {
      split_id: split.id,
      amount,
      currency: state.currency,
      note,
      shares: shares.map((s) => ({ handle: state.users.get(s.userId).handle, amount: s.amount })),
      requests: split.requestIds.map((id) => state.requestView(state.requests.get(id))),
      created_at: split.createdAt,
    };
  });
}

function activity(ctx) {
  const user = authenticate(ctx.req);
  const items = [];
  for (const p of state.payments.values()) {
    if (p.visibility === 'public' || p.fromId === user.id || p.toId === user.id) items.push(p);
  }
  items.sort(newestFirst);
  const { slice, hasMore } = page(ctx.query, items);
  return { status: 200, body: { payments: slice.map((p) => state.paymentView(p)), has_more: hasMore } };
}

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v) && !(v instanceof JNum);

function createSettlement(ctx) {
  const user = authenticate(ctx.req);
  if (!state.operators.has(user.id)) throw forbidden('only settlement operators may submit settlements');
  const key = idempotencyKey(ctx.req);
  return idempotent(ctx, user, key, (body) => {
    const transfers = body.transfers;
    if (!Array.isArray(transfers)) throw invalid('transfers must be an array');
    if (transfers.length < 1 || transfers.length > 32) throw invalid('transfers must hold 1 to 32 entries');
    const plan = transfers.map((t, i) => {
      const at = 'transfers[' + i + ']';
      if (!isPlainObject(t)) throw invalid(at + ' must be an object');
      for (const k of ['from_handle', 'to_handle']) {
        if (typeof t[k] !== 'string') throw invalid(at + '.' + k + ' must be a string');
      }
      const amount = amountField(t);
      const note = noteField(t);
      const visibility = visibilityField(t);
      const from = userByHandle(t.from_handle);
      if (!from) throw notFound(at + ': no user has handle ' + t.from_handle);
      const to = userByHandle(t.to_handle);
      if (!to) throw notFound(at + ': no user has handle ' + t.to_handle);
      if (from === to) throw new ApiError(422, 'self_payment', at + ' moves money to its own sender');
      return { from, to, amount, note, visibility };
    });
    const net = new Map();
    for (const t of plan) {
      // Held funds cannot fund a net debit: start from what each wallet has available.
      net.set(t.from, (net.get(t.from) ?? state.available(t.from)) - t.amount);
      net.set(t.to, (net.get(t.to) ?? state.available(t.to)) + t.amount);
    }
    for (const bal of net.values()) {
      if (bal < 0n) throw new ApiError(409, 'insufficient_funds', 'the settlement is not collectively affordable');
    }
    const stamp = now();
    const id = state.newId('st_', state.settlements);
    const payments = plan.map((t) => recordPayment(t.from, t.to, t.amount, t.note, t.visibility, null, id, stamp));
    state.settlements.set(id, { id, operatorId: user.id, paymentIds: payments.map((p) => p.id), committedAt: stamp.iso });
    return { settlement_id: id, committed_at: stamp.iso, payments: payments.map((p) => state.paymentView(p)) };
  });
}

// ---- authorizations ----------------------------------------------------------------

function createAuthorization(ctx) {
  const user = authenticate(ctx.req);
  const key = idempotencyKey(ctx.req);
  return idempotent(ctx, user, key, (body) => {
    const toHandle = reqString(body, 'to_handle');
    const amount = amountField(body);
    const note = noteField(body);
    const visibility = visibilityField(body);
    if (toHandle === user.handle) throw new ApiError(422, 'self_payment', 'cannot authorize a payment to yourself');
    const to = userByHandle(toHandle);
    if (!to) throw notFound('no user has handle ' + toHandle);
    if (state.available(user) < amount) {
      throw new ApiError(409, 'insufficient_funds', 'available balance is below amount');
    }
    const t = now();
    const expires = stamp(t.us + state.ttlSeconds * 1000000n);
    const a = { id: state.newId('a_', state.authorizations), fromId: user.id, toId: to.id, amount, captured: 0n,
      note, visibility, status: 'open', expiresAt: expires.iso, expiresUs: expires.us, paymentIds: [],
      createdAt: t.iso, createdUs: t.us, seq: state.nextSeq(), closeKind: null, closedUs: null, closedAt: null };
    state.openHold(a);
    return state.authorizationView(a);
  });
}

function findAuthorization(id) {
  const a = state.authorizations.get(id);
  if (!a) throw notFound('no such authorization');
  return a;
}

function captureAuthorization(ctx, id) {
  const user = authenticate(ctx.req);
  const key = idempotencyKey(ctx.req);
  return idempotent(ctx, user, key, (body) => {
    let amount = null;
    if (has(body, 'amount')) {
      amount = intValue(body.amount);
      if (amount === undefined || amount < 1n) throw invalid('amount must be a positive integer number of minor units');
    }
    if (has(body, 'final') && typeof body.final !== 'boolean') throw bad('final must be a boolean');
    const final = has(body, 'final') ? body.final : true;
    const a = findAuthorization(id);
    if (a.toId !== user.id) throw forbidden('only the receiver may capture this authorization');
    if (a.status === 'expired' && a.expiresUs <= ctx.now) {
      throw new ApiError(409, 'authorization_expired', 'the authorization expired at ' + a.expiresAt);
    }
    if (a.status !== 'open') throw new ApiError(409, 'authorization_not_open', 'authorization is ' + a.status);
    const remaining = state.remaining(a);
    if (amount === null) amount = remaining;
    if (amount > remaining) {
      throw new ApiError(422, 'capture_exceeds_authorization', 'only ' + remaining + ' remains on this authorization');
    }
    // One step: the captured part leaves the hold and moves to the receiver; a final
    // capture (or one that takes the whole remainder) closes the hold and releases the rest.
    const payer = state.users.get(a.fromId);
    payer.held -= amount;
    a.captured += amount;
    const p = recordPayment(payer, user, amount, a.note, a.visibility, null, null, now(), a.id);
    a.paymentIds.push(p.id);
    payer.cache = null;
    if (final || a.captured === a.amount) state.closeHold(a, 'captured', { us: p.createdUs, iso: p.createdAt });
    return state.paymentView(p);
  });
}

function voidAuthorization(ctx, id) {
  const user = authenticate(ctx.req);
  const a = findAuthorization(id);
  if (a.fromId !== user.id) throw forbidden('only the payer may void this authorization');
  if (a.status === 'open') state.closeHold(a, 'voided', now());
  else if (a.status !== 'voided') throw new ApiError(409, 'authorization_not_open', 'authorization is ' + a.status);
  return { status: 200, body: state.authorizationView(a) };
}

function listAuthorizations(ctx) {
  const user = authenticate(ctx.req);
  const q = ctx.query;
  const direction = q.get('direction');
  if (direction !== null && direction !== 'incoming' && direction !== 'outgoing') {
    throw invalid('direction must be incoming or outgoing');
  }
  const status = q.get('status');
  if (status !== null && !AUTH_STATUSES.includes(status)) throw invalid('unknown status');
  const items = [];
  for (const a of state.authorizations.values()) {
    const outgoing = a.fromId === user.id;
    const incoming = a.toId === user.id;
    if (direction === 'incoming' ? !incoming : direction === 'outgoing' ? !outgoing : !(incoming || outgoing)) continue;
    if (status !== null && a.status !== status) continue;
    items.push(a);
  }
  items.sort(newestFirst);
  const { slice, hasMore } = page(q, items);
  return { status: 200, body: { authorizations: slice.map((a) => state.authorizationView(a)), has_more: hasMore } };
}

// ---- routing --------------------------------------------------------------------

const ROUTES = [
  { path: /^\/health$/, methods: { GET: () => ({ status: 200, body: { status: 'ok' } }) } },
  { path: /^\/_test\/reset$/, methods: { POST: reset } },
  { path: /^\/_test\/export$/, methods: { GET: exportState } },
  { path: /^\/_test\/import$/, methods: { POST: importState } },
  { path: /^\/auth\/signup$/, methods: { POST: signup } },
  { path: /^\/auth\/login$/, methods: { POST: login } },
  { path: /^\/me$/, methods: { GET: me } },
  { path: /^\/payments$/, methods: { POST: createPayment } },
  { path: /^\/payments\/([^/]+)\/corrections$/, methods: { POST: correctPayment } },
  { path: /^\/payments\/([^/]+)\/revisions$/, methods: { GET: paymentRevisions } },
  { path: /^\/statement$/, methods: { GET: statement } },
  { path: /^\/requests$/, methods: { POST: createRequest, GET: listRequests } },
  { path: /^\/requests\/([^/]+)\/pay$/, methods: { POST: payRequest } },
  { path: /^\/requests\/([^/]+)\/decline$/, methods: { POST: (ctx, id) => closeRequest(ctx, id, 'declined') } },
  { path: /^\/requests\/([^/]+)\/cancel$/, methods: { POST: (ctx, id) => closeRequest(ctx, id, 'cancelled') } },
  { path: /^\/splits$/, methods: { POST: createSplit } },
  { path: /^\/activity$/, methods: { GET: activity } },
  { path: /^\/settlements$/, methods: { POST: createSettlement } },
  { path: /^\/authorizations$/, methods: { POST: createAuthorization, GET: listAuthorizations } },
  { path: /^\/authorizations\/([^/]+)\/capture$/, methods: { POST: captureAuthorization } },
  { path: /^\/authorizations\/([^/]+)\/void$/, methods: { POST: voidAuthorization } },
];

function decodeSegment(s) {
  try {
    return decodeURIComponent(s);
  } catch (e) {
    return null;
  }
}

// Returns { status, text? } where text is the serialized JSON body.
async function handle(ctx) {
  try {
    // Expiry is applied before anything reads or writes holds.
    ctx.now = nowUs();
    state.expireDue(ctx.now);
    const screen = uiResponse(ctx);
    if (screen) return screen;
    for (const route of ROUTES) {
      const m = route.path.exec(ctx.path);
      if (!m) continue;
      const fn = route.methods[ctx.method];
      if (!fn) throw new ApiError(405, 'method_not_allowed', 'method not allowed on ' + ctx.path);
      let arg;
      if (m[1] !== undefined) {
        arg = decodeSegment(m[1]);
        if (arg === null) {
          authenticate(ctx.req);
          throw notFound('no such resource');
        }
      }
      const out = await fn(ctx, arg);
      if (out.text !== undefined || out.pieces !== undefined) return out;
      return out.body === undefined ? { status: out.status } : { status: out.status, text: stringify(out.body) };
    }
    if (ctx.method === 'GET' && wantsHtml(ctx.req)) return uiResponse(ctx, true);
    throw notFound('no route for ' + ctx.path);
  } catch (e) {
    if (e instanceof ApiError) return errorResponse(e.status, e.code, e.message);
    console.error('internal error', e);
    return errorResponse(500, 'internal_error', 'internal error');
  }
}

function errorResponse(status, code, message) {
  return { status, text: stringify({ error: { code, message } }) };
}

module.exports = { handle, errorResponse };
