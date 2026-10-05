'use strict';
// Historical views of the ledger. A view is chosen by two instants (BigInt µs):
//   K (known_at): for each payment, the latest revision recorded at or before K;
//   T (as_of):    selected revisions count when their effective time is at or before T.
// Balances are the opening balance plus the deltas of the selected revisions; holds
// follow their event timelines. Nothing here mutates state.

const INF = 2n ** 80n;

function lowerBound(arr, x) {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function upperBound(arr, x) {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

// The revision of `p` known at K (null when none had been recorded yet).
function selected(p, K) {
  const revs = p.revs;
  for (let i = revs.length - 1; i >= 0; i--) {
    if (revs[i].recUs <= K) return revs[i];
  }
  return null;
}

// A user's selected movements in statement order (effective time, then payment id),
// with prefix sums of their deltas. Immutable once built; the latest-knowledge list
// is cached per user until the user's ledger changes.
function userLedger(st, u, K) {
  const latest = K >= u.maxRecUs;
  if (latest && u.cache) return u.cache;
  const items = [];
  for (const pid of u.paymentIds) {
    const p = st.payments.get(pid);
    const r = latest ? p.revs[p.revs.length - 1] : selected(p, K);
    if (!r) continue;
    let d = 0n;
    if (p.fromId === u.id) d -= r.amount;
    if (p.toId === u.id) d += r.amount;
    items.push({ p, r, d });
  }
  items.sort((a, b) => (a.r.effUs < b.r.effUs ? -1 : a.r.effUs > b.r.effUs ? 1
    : a.p.id < b.p.id ? -1 : a.p.id > b.p.id ? 1 : 0));
  const effs = items.map((x) => x.r.effUs);
  const prefix = new Array(items.length + 1);
  prefix[0] = 0n;
  for (let i = 0; i < items.length; i++) prefix[i + 1] = prefix[i] + items[i].d;
  const view = { items, effs, prefix, opening: u.opening };
  if (latest) u.cache = view;
  return view;
}

function totalAt(st, u, T, K) {
  const v = userLedger(st, u, K);
  return v.opening + v.prefix[upperBound(v.effs, T)];
}

// Captures of a hold: { us, amount } in time order (captures are immutable payments).
function captures(st, a) {
  return a.paymentIds.map((pid) => {
    const p = st.payments.get(pid);
    return { us: p.createdUs, amount: p.amount };
  });
}

// Amount a hold reserves in the view (T, K). The hold exists once created (and its
// creation is known); captures reduce it at their time; a void or final capture
// releases it at its time once known; expiry releases it at the deadline, which is
// known as soon as the creation is.
function holdAt(st, a, T, K) {
  if (a.createdUs > K || T < a.createdUs) return 0n;
  let release = a.expiresUs;
  if ((a.closeKind === 'void' || a.closeKind === 'final') && a.closedUs <= K && a.closedUs < release) release = a.closedUs;
  if (T >= release) return 0n;
  let held = a.amount;
  for (const c of captures(st, a)) {
    if (c.us <= T && c.us <= K) held -= c.amount;
  }
  return held;
}

function heldAt(st, u, T, K) {
  let held = 0n;
  for (const id of u.holdIds) held += holdAt(st, st.authorizations.get(id), T, K);
  return held;
}

// Every change to the user's total and held amounts under the latest revisions, as
// [instant, dTotal, dHeld]. `override` replaces one payment's latest revision.
function events(st, u, override) {
  const out = [];
  for (const pid of u.paymentIds) {
    const p = st.payments.get(pid);
    let amount;
    let at;
    if (override && override.p === p) {
      amount = override.amount;
      at = override.effUs;
    } else {
      const r = p.revs[p.revs.length - 1];
      amount = r.amount;
      at = r.effUs;
    }
    let d = 0n;
    if (p.fromId === u.id) d -= amount;
    if (p.toId === u.id) d += amount;
    out.push([at, d, 0n]);
  }
  for (const id of u.holdIds) {
    const a = st.authorizations.get(id);
    let release = a.expiresUs;
    if ((a.closeKind === 'void' || a.closeKind === 'final') && a.closedUs < release) release = a.closedUs;
    out.push([a.createdUs, 0n, a.amount]);
    let left = a.amount;
    for (const c of captures(st, a)) {
      if (c.us > release) continue;
      out.push([c.us, 0n, -c.amount]);
      left -= c.amount;
    }
    if (release >= a.createdUs) out.push([release, 0n, -left]);
  }
  out.sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0));
  return out;
}

// True when, under the latest revisions (with `override` applied), the user's total
// and available are never negative: at the opening and after all movements at every
// instant where a payment takes effect or a hold changes.
function historyHolds(st, u, override) {
  let total = u.opening;
  let held = 0n;
  if (total < 0n) return false;
  const ev = events(st, u, override);
  for (let i = 0; i < ev.length;) {
    const t = ev[i][0];
    for (; i < ev.length && ev[i][0] === t; i++) {
      total += ev[i][1];
      held += ev[i][2];
    }
    if (total < 0n || total - held < 0n) return false;
  }
  return true;
}

module.exports = { INF, lowerBound, upperBound, selected, userLedger, totalAt, heldAt, historyHolds };
