#!/usr/bin/env python3
"""Throw-away stage-3 reference service written by the analyst from the requirements alone (NOT product code).
It wraps the analyst's stage-2 reference (refimpl2.py) and adds created_at, revisions/corrections, temporal /me, statements,
snapshots and historical holds. Its only purpose is to prove the stage-3 acceptance checks are consistent and catch defects.
Usage: PORT=8099 python3 refimpl3.py
"""
import os
import re
import secrets
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refimpl2 as R  # noqa: E402

S, LOCK, Err = R.S, R.LOCK, R.Err
UTC = timezone.utc
INF = datetime(9999, 1, 1, tzinfo=UTC)
RFC = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
FLAGS = set(os.environ.get("REF3_BUGS", "").split(","))  # fault injection for the mutation runner


def utcnow():
    return datetime.now(UTC)


def iso(d):
    return d.astimezone(UTC).isoformat(timespec="microseconds")


def P(s):
    return datetime.fromisoformat(s)


def parse_instant(s, strict_422=True):
    if not isinstance(s, str) or not RFC.match(s):
        raise Err(422, "validation_failed", "instant")
    m = RFC.match(s)
    frac = (m.group(2) or "")[:7]
    try:
        d = datetime.fromisoformat(m.group(1) + frac + m.group(3).replace("Z", "+00:00"))
        return d.astimezone(UTC)
    except (ValueError, OverflowError):
        raise Err(422, "validation_failed", "instant")


# ------------------------------------------------------------------ state helpers
def meta():
    return S.setdefault("auth_meta", {})


def ensure():
    with LOCK:
        if "opening" in S:
            return
        S["snaps"] = {}
        users = S["users"]
        net = {u: 0 for u in users}
        for p in S["payments"]:
            p.setdefault("revs", [{"revision": 1, "amount": p["amount"], "effective_at": iso(P(p["created_at"])), "recorded_at": iso(P(p["created_at"])), "reason": ""}])
            net[p["from_user_id"]] -= p["amount"]
            net[p["to_user_id"]] += p["amount"]
        S["opening"] = {u: users[u]["balance"] - net[u] for u in users}
        for aid, a in S["auths"].items():
            m = meta().setdefault(aid, {"created": iso(P(a["created_at"])), "captures": [], "close_kind": None, "closed_at": None})
            for p in S["payments"]:
                if p.get("authorization_id") == aid:
                    m["captures"].append([iso(P(p["created_at"])), p["amount"]])
            if a["status"] == "expired":
                m["close_kind"], m["closed_at"] = "expiry", iso(datetime.fromtimestamp(a["expires_ts"], UTC))
            elif a["status"] in ("voided", "captured"):
                m["close_kind"], m["closed_at"] = "seed", m["created"]


def ensure_auth_meta():
    for aid, a in S["auths"].items():
        if aid not in meta():
            meta()[aid] = {"created": iso(P(a["created_at"])), "captures": [], "close_kind": None, "closed_at": None}


# ------------------------------------------------------------------ patched stage-2 primitives
_orig_make_payment, _orig_pay_obj, _orig_reset, _orig_sweep, _orig_route = R.make_payment, R.pay_obj, R.do_reset, R.sweep, R.route


def make_payment(*a, **kw):
    p = _orig_make_payment(*a, **kw)
    c = iso(P(p["created_at"]))
    p["revs"] = [{"revision": 1, "amount": p["amount"], "effective_at": c, "recorded_at": c, "reason": ""}]
    return p


def pay_obj(p):
    d = _orig_pay_obj(p)
    d.pop("revs", None)
    return d


def sweep():
    before = [aid for aid, a in S.get("auths", {}).items() if a["status"] == "open"]
    _orig_sweep()
    for aid in before:
        a = S["auths"][aid]
        if a["status"] == "expired":
            m = meta().setdefault(aid, {"created": iso(P(a["created_at"])), "captures": [], "close_kind": None, "closed_at": None})
            if m["close_kind"] is None:
                m["close_kind"], m["closed_at"] = "expiry", iso(datetime.fromtimestamp(a["expires_ts"], UTC))


def do_reset(fx):
    if isinstance(fx, dict):
        for p in fx.get("payments", []) or []:
            if isinstance(p, dict) and "created_at" in p:
                d = parse_instant(p["created_at"])
                if d > utcnow() and "future-seed-ok" not in FLAGS:
                    raise Err(422, "validation_failed", "created_at in the future")
    _orig_reset(fx)
    with LOCK:
        S["snaps"] = {}
        S["auth_meta"] = {}
        reset_time = iso(utcnow())
        users = S["users"]
        net = {u: 0 for u in users}
        for p, spec in zip(S["payments"], fx.get("payments", [])):
            c = iso(parse_instant(spec["created_at"])) if spec.get("created_at") else p["created_at"]
            p["created_at"] = c
            p["revs"] = [{"revision": 1, "amount": p["amount"], "effective_at": c, "recorded_at": c, "reason": ""}]
            net[p["from_user_id"]] -= p["amount"]
            net[p["to_user_id"]] += p["amount"]
        S["payments"].sort(key=lambda p: p["created_at"]) if False else None
        S["opening"] = {u: users[u]["balance"] - (net[u] if "opening-ignores-seed" not in FLAGS else 0) for u in users}
        for spec in fx.get("authorizations", []) or []:
            a = S["auths"][spec["id"]]
            created = iso(parse_instant(spec["created_at"])) if spec.get("created_at") else reset_time
            a["created_at"] = created
            live = a["status"] == "open"
            S["auth_meta"][a["authorization_id"]] = {"created": created, "captures": [], "close_kind": None if live else "seed", "closed_at": None if live else created}
            if a["status"] == "expired" and spec.get("status", "open") == "open":   # open in the fixture but already past its deadline
                S["auth_meta"][a["authorization_id"]].update(close_kind="expiry", closed_at=iso(datetime.fromtimestamp(a["expires_ts"], UTC)))


R.make_payment, R.pay_obj, R.sweep, R.do_reset = make_payment, pay_obj, sweep, do_reset


# ------------------------------------------------------------------ ledger views
def hold_remaining(a, m, T, K):
    """remaining held amount of authorization `a` in the view (T, K)"""
    if m["close_kind"] == "seed" and True:
        return 0
    created = P(m["created"])
    if created > K or T < created:
        return 0
    expires = datetime.fromtimestamp(a["expires_ts"], UTC)
    rel = [expires]
    if m["close_kind"] in ("void", "final") and P(m["closed_at"]) <= K:
        rel.append(P(m["closed_at"]))
    if T >= min(rel):
        return 0
    return a["amount"] - sum(amt for t, amt in m["captures"] if P(t) <= T and P(t) <= K)


def user_held(uid, T, K):
    ensure_auth_meta()
    return sum(hold_remaining(a, meta()[aid], T, K) for aid, a in S["auths"].items() if a["from_user_id"] == uid)


def selected(p, K):
    best = None
    for r in p["revs"]:
        if P(r["recorded_at"]) <= K:
            best = r
    return best


def movements(K, override=None):
    out = []
    for p in S["payments"]:
        r = selected(p, K) if not override or p["payment_id"] not in override else override[p["payment_id"]]
        if r:
            out.append((P(r["effective_at"]), p["payment_id"], p, r))
    out.sort(key=lambda m: (m[0], m[1]))
    return out


def user_total(uid, T, K, override=None):
    b = S["opening"].get(uid, 0)
    for eff, pid, p, r in movements(K, override):
        if eff <= T:
            b += -r["amount"] if p["from_user_id"] == uid else (r["amount"] if p["to_user_id"] == uid else 0)
    return b


def violates(override):
    now = utcnow()
    users = list(S["users"])
    if any(S["opening"].get(u, 0) < 0 for u in users):
        return True
    ts = {m[0] for m in movements(INF, override)}
    ensure_auth_meta()
    for aid, a in S["auths"].items():
        m = meta()[aid]
        ts |= {P(m["created"]), datetime.fromtimestamp(a["expires_ts"], UTC)}
        ts |= {P(t) for t, _ in m["captures"]}
        if m["closed_at"]:
            ts.add(P(m["closed_at"]))
    for t in sorted(x for x in ts if x <= now):
        for u in users:
            tot = user_total(u, t, INF, override)
            if tot < 0 or (tot - user_held(u, t, INF) < 0 and "ignore-available" not in FLAGS):
                return True
    return False


# ------------------------------------------------------------------ routes
def bearer(hdrs):
    m = re.match(r"^Bearer ([^\s]+)$", hdrs.get("authorization", ""))
    uid = S["tokens"].get(m.group(1)) if m else None
    if not uid:
        raise Err(401, "unauthenticated")
    return uid


def json_body(raw):
    import json as _j
    try:
        b = _j.loads(raw.decode("utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except Exception:
        raise Err(400, "malformed_request", "unparseable")
    if not isinstance(b, dict):
        raise Err(400, "malformed_request", "not an object")
    return b


def int_field(v, lo, hi, code=422):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Err(422, "validation_failed", "type")
    if isinstance(v, float) and not v.is_integer():
        raise Err(422, "validation_failed", "integer")
    v = int(v)
    if v < lo or (hi is not None and v > hi):
        raise Err(422, "validation_failed", "range")
    return v


def correction_route(uid, pid, hdrs, raw):
    key = hdrs.get("idempotency-key")
    with LOCK:
        p = next((x for x in S["payments"] if x["payment_id"] == pid), None)
        if not p:
            raise Err(404, "not_found")
        if p["from_user_id"] != uid:
            raise Err(403, "forbidden")
    if key is None or key == "":
        raise Err(400, "missing_idempotency_key")
    b = json_body(raw)

    def run():
        for f in ("expected_revision", "amount", "effective_at", "reason"):
            if f not in b:
                raise Err(422, "validation_failed", f + " missing")
        er = b["expected_revision"]
        if isinstance(er, bool) or not isinstance(er, (int, float)):
            raise Err(400, "malformed_request", "type")
        er = int_field(er, 1, None)
        amount = int_field(b["amount"], 0, 10**9)
        reason = b["reason"]
        if not isinstance(reason, str):
            raise Err(400, "malformed_request", "reason type")
        if not 1 <= len(reason) <= 200:
            raise Err(422, "validation_failed", "reason")
        if not isinstance(b["effective_at"], str):
            raise Err(400, "malformed_request", "effective type")
        eff = parse_instant(b["effective_at"])
        if eff > utcnow():
            raise Err(422, "validation_failed", "effective in the future")
        if p.get("settlement_id") or p.get("authorization_id"):
            raise Err(422, "linked_payment_immutable")
        latest = p["revs"][-1]
        if er != latest["revision"]:
            raise Err(409, "stale_revision")
        diff = amount - latest["amount"]
        debtor = p["from_user_id"] if diff > 0 else p["to_user_id"]
        if diff != 0 and R.avail(debtor) < abs(diff):
            raise Err(409, "insufficient_funds")
        new = {"revision": latest["revision"] + 1, "amount": amount, "effective_at": iso(eff), "recorded_at": "", "reason": reason}
        if "no-overdraft-check" not in FLAGS and violates({pid: {**new, "recorded_at": iso(utcnow())}}):
            raise Err(409, "historical_overdraft")
        sender, receiver = S["users"][p["from_user_id"]], S["users"][p["to_user_id"]]
        sender["balance"] -= diff
        receiver["balance"] += diff
        rec = utcnow()
        last = P(latest["recorded_at"])
        if rec <= last:
            rec = last + timedelta(microseconds=1)
        new["recorded_at"] = iso(rec)
        p["revs"].append(new)
        return {k_: new[k_] for k_ in ("revision", "amount", "effective_at", "recorded_at", "reason")} | {"payment_id": pid}

    with LOCK:
        return R.idem(uid, "POST", "/payments/%s/corrections" % pid, key, b, run)


def revisions_route(uid, pid):
    with LOCK:
        p = next((x for x in S["payments"] if x["payment_id"] == pid), None)
        if not p or uid not in (p["from_user_id"], p["to_user_id"]):
            raise Err(404, "not_found")
        return 200, {"revisions": [dict(r, payment_id=pid) for r in p["revs"]]}


def temporal_params(qs):
    out = {}
    for name in ("as_of", "known_at"):
        if name in qs:
            out[name] = (qs[name], parse_instant(qs[name]))
    return out


def me_route(uid, qs, hdrs, raw):
    tp = temporal_params(qs)
    if not tp:
        return _orig_route("GET", "/me", qs, hdrs, raw)
    start = utcnow()
    with LOCK:
        status, obj = _orig_route("GET", "/me", {}, hdrs, raw)
        T = tp["as_of"][1] if "as_of" in tp else start
        K = tp["known_at"][1] if "known_at" in tp else INF
        if "known_at-ignored" in FLAGS:
            K = INF
        tot = user_total(uid, T, K)
        held = user_held(uid, T, K)
        obj.update({"balance": tot, "total": tot, "held": held, "available": tot - held})
        for name, (given, _) in tp.items():
            obj[name] = given
        return 200, obj


def statement_route(uid, qs, hdrs, raw):
    limit, offset = R.paging(qs)
    if "snapshot" in qs:
        if any(k_ in qs for k_ in ("from", "to", "known_at")):
            raise Err(422, "validation_failed", "snapshot with window")
        with LOCK:
            sn = S["snaps"].get(qs["snapshot"])
            if not sn or sn["uid"] != uid:
                raise Err(404, "not_found")
            return 200, {"opening_balance": sn["opening"], "entries": sn["entries"][offset:offset + limit], "closing_balance": sn["closing"],
                         "has_more": len(sn["entries"]) > offset + limit, "snapshot": qs["snapshot"]}
    tp = {}
    for name in ("from", "to", "known_at"):
        if name in qs:
            tp[name] = parse_instant(qs[name])
    start = utcnow()
    frm, to, K = tp.get("from"), tp.get("to", start), tp.get("known_at", INF)
    with LOCK:
        base = S["opening"].get(uid, 0)
        pre, run, entries = 0, base, []
        for eff, pid, p, r in movements(K):
            if uid not in (p["from_user_id"], p["to_user_id"]):
                continue
            d = -r["amount"] if p["from_user_id"] == uid else r["amount"]
            if frm is not None and eff < frm:
                pre += d
                run += d
                continue
            if eff >= to:
                continue
            run += d
            po = pay_obj(p)
            po["amount"] = r["amount"]
            entries.append({"payment": po, "delta": d, "balance_after": run, "revision": r["revision"], "effective_at": r["effective_at"], "recorded_at": r["recorded_at"]})
        opening = base + pre
        closing = opening + sum(e["delta"] for e in entries)
        token = secrets.token_urlsafe(12)
        S["snaps"][token] = {"uid": uid, "opening": opening, "closing": closing, "entries": entries}
        return 200, {"opening_balance": opening, "entries": entries[offset:offset + limit], "closing_balance": closing, "has_more": len(entries) > offset + limit, "snapshot": token}


def decorate(obj, creating=False):
    if isinstance(obj, dict):
        if "authorization_id" in obj and "expires_at" in obj and "remaining_amount" in obj:
            m = meta().get(obj["authorization_id"])
            obj["closed_at"] = None if creating or not m else m["closed_at"]
        if isinstance(obj.get("authorizations"), list):
            for x in obj["authorizations"]:
                decorate(x)
    return obj


def route3(method, path, qs, hdrs, raw):
    if path in ("/health", "/_test/reset", "/_test/export", "/_test/import", "/auth/signup", "/auth/login") or not S:
        return _orig_route(method, path, qs, hdrs, raw)
    ensure()
    with LOCK:
        pass
    m = re.match(r"^/payments/([^/]+)/(corrections|revisions)$", path)
    if m:
        uid = bearer(hdrs)
        pid = urllib.parse.unquote(m.group(1))
        if m.group(2) == "corrections" and method == "POST":
            return correction_route(uid, pid, hdrs, raw)
        if m.group(2) == "revisions" and method == "GET":
            return revisions_route(uid, pid)
        raise Err(405, "method_not_allowed")
    if path == "/statement" and method == "GET":
        return statement_route(bearer(hdrs), qs, hdrs, raw)
    if path == "/me" and method == "GET" and ("as_of" in qs or "known_at" in qs):
        return me_route(bearer(hdrs), qs, hdrs, raw)
    if path == "/activity" and method == "GET":
        uid = bearer(hdrs)
        limit, off = R.paging(qs)
        with LOCK:
            vis = [(P(p["created_at"]), i, p) for i, p in enumerate(S["payments"]) if p["visibility"] == "public" or uid in (p["from_user_id"], p["to_user_id"])]
            vis.sort(key=lambda x: (x[0], x[1]), reverse=True)
            page = vis[off:off + limit]
            return 200, {"payments": [pay_obj(p) for _, _, p in page], "has_more": len(vis) > off + limit}
    am = re.match(r"^/authorizations/([^/]+)/(capture|void)$", path)
    before_payments = len(S["payments"])
    status, obj = _orig_route(method, path, qs, hdrs, raw)
    with LOCK:
        ensure_auth_meta()
        if am and method == "POST" and status in (200, 201):
            aid = urllib.parse.unquote(am.group(1))
            a = S["auths"].get(aid)
            mm = meta().get(aid)
            if am.group(2) == "capture" and status == 201 and len(S["payments"]) > before_payments:
                cp = S["payments"][-1]
                mm["captures"].append([iso(P(cp["created_at"])), cp["amount"]])
                if a["status"] == "captured":
                    mm["close_kind"], mm["closed_at"] = "final", iso(P(cp["created_at"]))
            if am.group(2) == "void" and a["status"] == "voided" and mm["close_kind"] is None:
                mm["close_kind"], mm["closed_at"] = "void", iso(utcnow())
        if path.startswith("/authorizations"):
            decorate(obj, creating=(method == "POST" and path == "/authorizations"))
    return status, obj


R.route = route3

if __name__ == "__main__":
    R.S.update(R.fresh())
    R.Srv(("0.0.0.0", int(os.environ.get("PORT", "8080"))), R.H).serve_forever()
