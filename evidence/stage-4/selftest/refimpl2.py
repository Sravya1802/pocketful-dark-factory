#!/usr/bin/env python3
"""Throw-away reference service written by the analyst from the requirements alone.
Its only purpose is to prove the acceptance checks are internally consistent and runnable
(a check that fails against this reading of the spec is a check bug, not a product bug).
NOT product code. Usage: PORT=8099 python3 refimpl.py
"""
import copy
import hashlib
import json
import os
import re
import secrets
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import time as _time


class SLock:
    def __init__(self):
        self._l = threading.RLock()

    def __enter__(self):
        self._l.acquire()
        sweep()

    def __exit__(self, *a):
        self._l.release()


LOCK = SLock()


def sweep():
    t = _time.time()
    for a in S.get("auths", {}).values():
        if a["status"] == "open" and a["expires_ts"] <= t:
            a["status"] = "expired"
            a["remaining_amount"] = 0

S = {}
HANDLE_RE = re.compile(r"^[a-z0-9_]{1,20}$")
DIGITS = re.compile(r"^[0-9]+$")
MAXAMT = 10**9


class Err(Exception):
    def __init__(self, status, code, msg=""):
        self.status, self.code, self.msg = status, code, msg or code


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def hpw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 20000).hex()


def vpw(pw, stored):
    return secrets.compare_digest(hpw(pw, stored.split("$")[0]), stored)


def fresh():
    return {"currency": "EUR", "minor_units": 2, "users": {}, "tokens": {}, "payments": [], "requests": {}, "idem": {}, "seq": 0,
            "operators": [], "auths": {}, "auth_ttl": 600}


def nid(prefix):
    S["seq"] += 1
    return "%s_%d" % (prefix, S["seq"] + 1000)


def amount_of(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Err(422, "validation_failed", "amount")
    if isinstance(v, float) and not v.is_integer():
        raise Err(422, "validation_failed", "amount")
    v = int(v)
    if v < 1 or v > MAXAMT:
        raise Err(422, "validation_failed", "amount range")
    return v


def need(body, f, typ=None):
    if f not in body:
        raise Err(422, "validation_failed", f + " missing")
    v = body[f]
    if typ and not isinstance(v, typ):
        raise Err(400, "malformed_request", f + " type")
    return v


def note_of(body):
    n = body.get("note", "")
    if not isinstance(n, str) or len(n) > 200:
        raise Err(422, "validation_failed", "note")
    return n


def vis_of(body):
    v = body.get("visibility", "public")
    if v not in ("public", "private") or not isinstance(v, str):
        raise Err(422, "validation_failed", "visibility")
    return v


def by_handle(h):
    for u in S["users"].values():
        if u["handle"] == h:
            return u
    return None


def held(uid):
    return sum(a["remaining_amount"] for a in S["auths"].values() if a["from_user_id"] == uid and a["status"] == "open")


def avail(uid):
    return S["users"][uid]["balance"] - held(uid)


def pay_obj(p):
    d = dict(p)
    d.setdefault("authorization_id", None)
    return d


def auth_obj(a):
    return {k: a[k] for k in ("authorization_id", "from_user_id", "from_handle", "to_user_id", "to_handle", "amount", "captured_amount",
                              "remaining_amount", "currency", "note", "visibility", "status", "expires_at", "payment_id", "payment_ids", "created_at")}


def make_payment(frm, to, amount, note, vis, request_id=None, settlement_id=None, created_at=None, authorization_id=None):
    p = {"payment_id": nid("p"), "from_user_id": frm["id"], "from_handle": frm["handle"], "to_user_id": to["id"], "to_handle": to["handle"],
         "amount": amount, "currency": S["currency"], "note": note, "visibility": vis, "request_id": request_id,
         "created_at": created_at or now(), "settlement_id": settlement_id, "authorization_id": authorization_id}
    S["payments"].append(p)
    return p


def req_obj(q):
    return {k: q[k] for k in ("request_id", "requester_id", "requester_handle", "payer_id", "payer_handle", "amount", "currency", "note",
                              "status", "payment_id", "created_at")}


def paging(qs):
    out = []
    for name, dflt, lo, hi in (("limit", 50, 1, 200), ("offset", 0, 0, None)):
        if name in qs:
            v = qs[name]
            if not DIGITS.match(v):
                raise Err(422, "validation_failed", name)
            v = int(v)
            if v < lo or (hi and v > hi):
                raise Err(422, "validation_failed", name)
        else:
            v = dflt
        out.append(v)
    return out


def idem(uid, method, path, key, body, fn):
    """Run fn() once per (user, method, path, key); replays return the stored response."""
    if key is None or key == "":
        raise Err(400, "missing_idempotency_key")
    if len(key) > 255:
        raise Err(422, "validation_failed", "key")
    ik = json.dumps([uid, method, path, key])
    rec = S["idem"].get(ik)
    if rec:
        if rec["body"] == body:
            return 200, rec["resp"]
        raise Err(409, "idempotency_key_reuse")
    resp = fn()
    S["idem"][ik] = {"body": copy.deepcopy(body), "resp": copy.deepcopy(resp)}
    return 201, resp


def make_user(uid, email, pw_hash, display, handle, balance):
    S["users"][uid] = {"id": uid, "email": email, "pw": pw_hash, "display_name": display, "handle": handle, "balance": balance}


def do_reset(fx):
    if not isinstance(fx, dict):
        raise Err(422, "validation_failed")
    new = fresh()
    new["currency"] = fx.get("currency", "EUR")
    new["minor_units"] = fx.get("minor_units", 2)
    ttl = fx.get("authorization_ttl_seconds", 600)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
        raise Err(422, "validation_failed", "ttl")
    new["auth_ttl"] = ttl
    pre = []
    for u in fx.get("users", []):
        if not isinstance(u.get("balance"), int) or u["balance"] < 0:
            raise Err(422, "validation_failed", "balance")
        pre.append(u)
    hashes = {u["id"]: hpw(u["password"]) for u in pre}  # outside the state lock
    with LOCK:
        old = copy.deepcopy(S)
        S.clear()
        S.update(new)
        try:
            for u in pre:
                make_user(u["id"], u["email"], hashes[u["id"]], u.get("display_name", ""), u["handle"], u["balance"])
            for p in fx.get("payments", []):
                make_payment(S["users"][p["from_user_id"]], S["users"][p["to_user_id"]], p["amount"], p.get("note", ""),
                             p.get("visibility", "public"))
                S["payments"][-1]["payment_id"] = p["id"]
            for r in fx.get("requests", []):
                a, b = S["users"][r["requester_id"]], S["users"][r["payer_id"]]
                S["requests"][r["id"]] = {"request_id": r["id"], "requester_id": a["id"], "requester_handle": a["handle"], "payer_id": b["id"],
                                          "payer_handle": b["handle"], "amount": r["amount"], "currency": S["currency"], "note": r.get("note", ""),
                                          "status": r.get("status", "pending"), "payment_id": None, "created_at": now()}
                S["seq"] += 1
                S["requests"][r["id"]]["order"] = S["seq"]
            S["operators"] = list(fx.get("settlement_operator_ids", []))
            tnow = _time.time()
            used = {}
            for a in fx.get("authorizations", []):
                f, t = S["users"][a["from_user_id"]], S["users"][a["to_user_id"]]
                ex = datetime.fromisoformat(a["expires_at"]).timestamp()
                st = a.get("status", "open")
                if st not in ("open", "captured", "voided", "expired"):
                    raise ValueError
                live = st == "open" and ex > tnow
                if live:
                    used[f["id"]] = used.get(f["id"], 0) + a["amount"]
                    if used[f["id"]] > f["balance"]:
                        raise ValueError
                S["seq"] += 1
                S["auths"][a["id"]] = {"authorization_id": a["id"], "from_user_id": f["id"], "from_handle": f["handle"], "to_user_id": t["id"],
                                       "to_handle": t["handle"], "amount": a["amount"], "captured_amount": a.get("captured_amount", 0),
                                       "remaining_amount": a["amount"] if live else 0, "currency": S["currency"], "note": a.get("note", ""),
                                       "visibility": a.get("visibility", "public"), "status": st, "expires_at": a["expires_at"],
                                       "expires_ts": ex, "payment_id": None, "payment_ids": [], "created_at": now(), "order": S["seq"]}
        except Exception:
            S.clear()
            S.update(old)
            raise Err(422, "validation_failed", "fixture")


def derive_handle(email):
    return re.sub(r"[^a-z0-9_]", "_", email.split("@")[0].lower())[:20]


def route(method, path, qs, hdrs, raw):
    def body_obj():
        try:
            b = json.loads(raw.decode("utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        except Exception:
            raise Err(400, "malformed_request", "unparseable")
        if not isinstance(b, dict):
            raise Err(400, "malformed_request", "not an object")
        return b

    def auth():
        h = hdrs.get("authorization", "")
        m = re.match(r"^Bearer ([^\s]+)$", h)
        with LOCK:
            uid = S["tokens"].get(m.group(1)) if m else None
        if not uid:
            raise Err(401, "unauthenticated")
        return uid

    parts = [p for p in path.split("/") if p != ""] if path != "/" else []
    key = hdrs.get("idempotency-key")
    if path == "/health" and method == "GET":
        return 200, {"status": "ok"}
    if path == "/_test/reset" and method == "POST":
        try:
            fx = json.loads(raw.decode("utf-8"))
        except Exception:
            raise Err(400, "malformed_request")
        do_reset(fx)
        return 204, None
    if path == "/_test/export" and method == "GET":
        with LOCK:
            return 200, {"track": "pocketful", "format_version": 1, "state": copy.deepcopy(S)}
    if path == "/_test/import" and method == "POST":
        try:
            o = json.loads(raw.decode("utf-8"))
        except Exception:
            raise Err(400, "malformed_request")
        if not isinstance(o, dict):
            raise Err(422, "validation_failed")
        if o.get("track") != "pocketful" or type(o.get("format_version")) is not int or o["format_version"] != 1:
            raise Err(422, "validation_failed")
        st = o.get("state")
        want = fresh()
        if isinstance(st, dict):
            st.setdefault("auths", {})
            st.setdefault("auth_ttl", 600)
        if not isinstance(st, dict) or any(k not in st or type(st[k]) is not type(want[k]) for k in want):
            raise Err(422, "validation_failed")
        try:
            for u in st["users"].values():
                u["id"], u["handle"], u["balance"], u["pw"], u["email"]
            for p in st["payments"]:
                p["payment_id"], p["amount"]
            for q in st["requests"].values():
                q["request_id"], q["status"]
        except Exception:
            raise Err(422, "validation_failed")
        with LOCK:
            S.clear()
            S.update(copy.deepcopy(st))
        return 204, None
    if path == "/auth/signup" and method == "POST":
        b = body_obj()
        email, pw, dn = need(b, "email"), need(b, "password"), need(b, "display_name")
        for v in (email, pw, dn):
            if not isinstance(v, str):
                raise Err(400, "malformed_request")
        if len(pw) < 8 or not re.match(r"^[^@\s]+@[^@\s]+$", email):
            raise Err(422, "validation_failed")
        h = hpw(pw)
        with LOCK:
            if any(u["email"] == email for u in S["users"].values()):
                raise Err(409, "email_taken")
            handle = derive_handle(email)
            if by_handle(handle):
                raise Err(409, "handle_taken")
            uid = nid("u")
            make_user(uid, email, h, dn, handle, 0)
            t = secrets.token_hex(16)
            S["tokens"][t] = uid
        return 201, {"user_id": uid, "display_name": dn, "token": t}
    if path == "/auth/login" and method == "POST":
        b = body_obj()
        email, pw = need(b, "email"), need(b, "password")
        with LOCK:
            u = next((u for u in S["users"].values() if u["email"] == email), None)
        if not isinstance(pw, str) or not u or not vpw(pw, u["pw"]):
            raise Err(401, "unauthenticated")
        with LOCK:
            t = secrets.token_hex(16)
            S["tokens"][t] = u["id"]
        return 200, {"user_id": u["id"], "display_name": u["display_name"], "token": t}
    if path == "/me" and method == "GET":
        uid = auth()
        with LOCK:
            u = S["users"][uid]
            return 200, {"user_id": uid, "display_name": u["display_name"], "handle": u["handle"], "balance": u["balance"],
                         "total": u["balance"], "available": avail(uid), "held": held(uid), "currency": S["currency"], "minor_units": S["minor_units"]}
    if path == "/activity" and method == "GET":
        uid = auth()
        limit, off = paging(qs)
        with LOCK:
            vis = [p for p in S["payments"] if p["visibility"] == "public" or uid in (p["from_user_id"], p["to_user_id"])]
            vis = vis[::-1]
            page = vis[off:off + limit]
            return 200, {"payments": [pay_obj(p) for p in page], "has_more": len(vis) > off + limit}
    if path == "/requests" and method == "GET":
        uid = auth()
        limit, off = paging(qs)
        d, st = qs.get("direction"), qs.get("status")
        if d is not None and d not in ("incoming", "outgoing"):
            raise Err(422, "validation_failed")
        if st is not None and st not in ("pending", "paid", "declined", "cancelled"):
            raise Err(422, "validation_failed")
        with LOCK:
            rs = [q for q in S["requests"].values() if uid in (q["requester_id"], q["payer_id"])]
            if d == "incoming":
                rs = [q for q in rs if q["payer_id"] == uid]
            if d == "outgoing":
                rs = [q for q in rs if q["requester_id"] == uid]
            if st:
                rs = [q for q in rs if q["status"] == st]
            rs.sort(key=lambda q: (q["created_at"], q["order"]), reverse=True)
            return 200, {"requests": [req_obj(q) for q in rs[off:off + limit]], "has_more": len(rs) > off + limit}
    if path == "/payments" and method == "POST":
        uid = auth()
        if key is None or key == "":
            raise Err(400, "missing_idempotency_key")
        b = body_obj()

        def run():
            to = need(b, "to_handle", str)
            amt = amount_of(need(b, "amount"))
            note, vis = note_of(b), vis_of(b)
            rec = by_handle(to)
            if not rec:
                raise Err(404, "not_found")
            me_ = S["users"][uid]
            if rec["id"] == uid:
                raise Err(422, "self_payment")
            if avail(uid) < amt:
                raise Err(409, "insufficient_funds")
            me_["balance"] -= amt
            rec["balance"] += amt
            return pay_obj(make_payment(me_, rec, amt, note, vis))

        with LOCK:
            return idem(uid, method, path, key, b, run)
    if path == "/requests" and method == "POST":
        uid = auth()
        if key is None or key == "":
            raise Err(400, "missing_idempotency_key")
        b = body_obj()

        def run():
            ph = need(b, "payer_handle", str)
            amt = amount_of(need(b, "amount"))
            note = note_of(b)
            payer = by_handle(ph)
            if not payer:
                raise Err(404, "not_found")
            if payer["id"] == uid:
                raise Err(422, "self_request")
            return req_obj(new_request(S["users"][uid], payer, amt, note))

        with LOCK:
            return idem(uid, method, path, key, b, run)
    m = re.match(r"^/requests/([^/]+)/(pay|decline|cancel)$", path)
    if m and method == "POST":
        uid = auth()
        rid, act = urllib.parse.unquote(m.group(1)), m.group(2)
        if act == "pay":
            if key is None or key == "":
                raise Err(400, "missing_idempotency_key")
            b = body_obj() if raw.strip() else {}

            def run():
                q = S["requests"].get(rid)
                if not q:
                    raise Err(404, "not_found")
                if q["payer_id"] != uid:
                    raise Err(403, "forbidden")
                vis = vis_of(b)
                if q["status"] != "pending":
                    raise Err(409, "request_not_pending")
                me_ = S["users"][uid]
                if avail(uid) < q["amount"]:
                    raise Err(409, "insufficient_funds")
                to = S["users"][q["requester_id"]]
                me_["balance"] -= q["amount"]
                to["balance"] += q["amount"]
                p = make_payment(me_, to, q["amount"], q["note"], vis, request_id=rid)
                q["status"], q["payment_id"] = "paid", p["payment_id"]
                return pay_obj(p)

            with LOCK:
                return idem(uid, method, path, key, b, run)
        with LOCK:
            q = S["requests"].get(rid)
            if not q:
                raise Err(404, "not_found")
            who = "payer_id" if act == "decline" else "requester_id"
            if q[who] != uid:
                raise Err(403, "forbidden")
            target = "declined" if act == "decline" else "cancelled"
            if q["status"] == target:
                return 200, req_obj(q)
            if q["status"] != "pending":
                raise Err(409, "request_not_pending")
            q["status"] = target
            return 200, req_obj(q)
    if path == "/splits" and method == "POST":
        uid = auth()
        if key is None or key == "":
            raise Err(400, "missing_idempotency_key")
        b = body_obj()

        def run():
            amt = amount_of(need(b, "amount"))
            hs = need(b, "participant_handles", list)
            if any(not isinstance(h, str) for h in hs):
                raise Err(400, "malformed_request")
            note = note_of(b)
            if not hs or len(set(hs)) != len(hs):
                raise Err(422, "validation_failed")
            users = []
            for h in hs:
                u = by_handle(h)
                if not u:
                    raise Err(404, "not_found")
                users.append(u)
            q, r = divmod(amt, len(hs))
            shares = [{"handle": h, "amount": q + (1 if i < r else 0)} for i, h in enumerate(hs)]
            reqs = [req_obj(new_request(S["users"][uid], u, sh["amount"], note)) for u, sh in zip(users, shares) if u["id"] != uid]
            return {"split_id": nid("sp"), "amount": amt, "currency": S["currency"], "note": note, "shares": shares, "requests": reqs,
                    "created_at": now()}

        with LOCK:
            return idem(uid, method, path, key, b, run)
    if path == "/settlements" and method == "POST":
        uid = auth()
        with LOCK:
            if uid not in S["operators"]:
                raise Err(403, "forbidden")
        if key is None or key == "":
            raise Err(400, "missing_idempotency_key")
        b = body_obj()

        def run():
            tr = b.get("transfers")
            if not isinstance(tr, list) or not 1 <= len(tr) <= 32:
                raise Err(422, "validation_failed")
            ents = []
            for t in tr:
                if not isinstance(t, dict) or not isinstance(t.get("from_handle"), str) or not isinstance(t.get("to_handle"), str):
                    raise Err(422, "validation_failed")
                amt = amount_of(t["amount"]) if "amount" in t else (_ for _ in ()).throw(Err(422, "validation_failed"))
                note, vis = note_of(t), vis_of(t)
                f, to = by_handle(t["from_handle"]), by_handle(t["to_handle"])
                if not f or not to:
                    raise Err(404, "not_found")
                if f["id"] == to["id"]:
                    raise Err(422, "self_payment")
                ents.append((f, to, amt, note, vis))
            net = {}
            for f, to, amt, _, _ in ents:
                net[f["id"]] = net.get(f["id"], 0) - amt
                net[to["id"]] = net.get(to["id"], 0) + amt
            if any(avail(i) + d < 0 for i, d in net.items()):
                raise Err(409, "insufficient_funds")
            for i, d in net.items():
                S["users"][i]["balance"] += d
            sid, ts = nid("st"), now()
            return {"settlement_id": sid, "committed_at": ts,
                    "payments": [pay_obj(make_payment(f, to, amt, note, vis, settlement_id=sid, created_at=ts)) for f, to, amt, note, vis in ents]}

        with LOCK:
            return idem(uid, method, path, key, b, run)
    if path == "/authorizations" and method == "POST":
        uid = auth()
        if key is None or key == "":
            raise Err(400, "missing_idempotency_key")
        b = body_obj()

        def run():
            to = need(b, "to_handle", str)
            amt = amount_of(need(b, "amount"))
            note, vis = note_of(b), vis_of(b)
            rec = by_handle(to)
            if not rec:
                raise Err(404, "not_found")
            if rec["id"] == uid:
                raise Err(422, "self_payment")
            if avail(uid) < amt:
                raise Err(409, "insufficient_funds")
            me_ = S["users"][uid]
            ts = _time.time()
            S["seq"] += 1
            aid = nid("a")
            S["auths"][aid] = {"authorization_id": aid, "from_user_id": uid, "from_handle": me_["handle"], "to_user_id": rec["id"],
                               "to_handle": rec["handle"], "amount": amt, "captured_amount": 0, "remaining_amount": amt, "currency": S["currency"],
                               "note": note, "visibility": vis, "status": "open",
                               "expires_at": datetime.fromtimestamp(int(ts) + S["auth_ttl"], timezone.utc).isoformat(),
                               "expires_ts": int(ts) + S["auth_ttl"], "payment_id": None, "payment_ids": [],
                               "created_at": datetime.fromtimestamp(int(ts), timezone.utc).isoformat(), "order": S["seq"]}
            return auth_obj(S["auths"][aid])

        with LOCK:
            return idem(uid, method, path, key, b, run)
    m2 = re.match(r"^/authorizations/([^/]+)/(capture|void)$", path)
    if m2 and method == "POST":
        uid = auth()
        aid, act = urllib.parse.unquote(m2.group(1)), m2.group(2)
        if act == "capture":
            if key is None or key == "":
                raise Err(400, "missing_idempotency_key")
            b = body_obj() if raw.strip() else {}

            def run():
                a = S["auths"].get(aid)
                if not a:
                    raise Err(404, "not_found")
                if a["to_user_id"] != uid:
                    raise Err(403, "forbidden")
                amt = amount_of(b["amount"]) if "amount" in b else None
                fin = b.get("final", True)
                if not isinstance(fin, bool):
                    raise Err(400, "malformed_request", "final")
                if a["status"] == "expired":
                    raise Err(409, "authorization_expired")
                if a["status"] != "open":
                    raise Err(409, "authorization_not_open")
                if amt is None:
                    amt = a["remaining_amount"]
                if amt > a["remaining_amount"]:
                    raise Err(422, "capture_exceeds_authorization")
                f, t = S["users"][a["from_user_id"]], S["users"][uid]
                f["balance"] -= amt
                t["balance"] += amt
                p = make_payment(f, t, amt, a["note"], a["visibility"], authorization_id=aid)
                a["captured_amount"] += amt
                a["remaining_amount"] -= amt
                a["payment_ids"].append(p["payment_id"])
                a["payment_id"] = p["payment_id"]
                if fin or a["remaining_amount"] == 0:
                    a["status"] = "captured"
                    a["remaining_amount"] = 0
                return pay_obj(p)

            with LOCK:
                return idem(uid, method, path, key, b, run)
        with LOCK:
            a = S["auths"].get(aid)
            if not a:
                raise Err(404, "not_found")
            if a["from_user_id"] != uid:
                raise Err(403, "forbidden")
            if a["status"] == "voided":
                return 200, auth_obj(a)
            if a["status"] != "open":
                raise Err(409, "authorization_not_open")
            a["status"], a["remaining_amount"] = "voided", 0
            return 200, auth_obj(a)
    if path == "/authorizations" and method == "GET" and "text/html" not in hdrs.get("accept", ""):
        uid = auth()
        limit, off = paging(qs)
        d, st = qs.get("direction"), qs.get("status")
        if d is not None and d not in ("incoming", "outgoing"):
            raise Err(422, "validation_failed")
        if st is not None and st not in ("open", "captured", "voided", "expired"):
            raise Err(422, "validation_failed")
        with LOCK:
            rs = [a for a in S["auths"].values() if uid in (a["from_user_id"], a["to_user_id"])]
            if d == "outgoing":
                rs = [a for a in rs if a["from_user_id"] == uid]
            if d == "incoming":
                rs = [a for a in rs if a["to_user_id"] == uid]
            if st:
                rs = [a for a in rs if a["status"] == st]
            rs.sort(key=lambda a: (a["created_at"], a["order"]), reverse=True)
            return 200, {"authorizations": [auth_obj(a) for a in rs[off:off + limit]], "has_more": len(rs) > off + limit}
    known = {"/authorizations", "/health", "/_test/reset", "/_test/export", "/_test/import", "/auth/signup", "/auth/login", "/me", "/activity", "/requests",
             "/payments", "/splits", "/settlements"}
    if path in known or m:
        raise Err(405, "method_not_allowed")
    raise Err(404, "not_found")


def new_request(requester, payer, amt, note):
    rid = nid("rq")
    S["requests"][rid] = {"request_id": rid, "requester_id": requester["id"], "requester_handle": requester["handle"], "payer_id": payer["id"],
                          "payer_handle": payer["handle"], "amount": amt, "currency": S["currency"], "note": note, "status": "pending",
                          "payment_id": None, "created_at": now(), "order": S["seq"]}
    return S["requests"][rid]


UI_HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "refui.html")).read() if os.path.exists(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "refui.html")) else "<html><body>no ui</body></html>"


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        u = urllib.parse.urlsplit(self.path)
        qs = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True, errors="replace"))
        hdrs = {k.lower(): v for k, v in self.headers.items()}
        page = self.command == "GET" and (u.path in ("/", "/split", "/signup", "/login") or (
            u.path in ("/requests", "/authorizations") and "text/html" in hdrs.get("accept", "")))
        if page:
            data = UI_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        try:
            status, obj = route(self.command, u.path, qs, hdrs, raw)
        except Err as e:
            status, obj = e.status, {"error": {"code": e.code, "message": e.msg}}
        except RecursionError:
            status, obj = 400, {"error": {"code": "malformed_request", "message": "too deep"}}
        data = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(status)
        if obj is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


class Srv(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256


if __name__ == "__main__":
    S.update(fresh())
    Srv(("0.0.0.0", int(os.environ.get("PORT", "8080"))), H).serve_forever()
