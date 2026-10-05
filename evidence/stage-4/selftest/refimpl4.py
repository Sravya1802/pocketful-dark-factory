#!/usr/bin/env python3
"""Throw-away stage-4 reference service written by the analyst from the requirements alone (NOT product code).
Wraps the analyst's stage-3 reference (refimpl3.py -> refimpl2.py) and adds refunds, correction batches and refund-aware corrections.
Its only purpose is to prove the stage-4 acceptance checks are consistent and catch defects. Usage: PORT=8099 python3 refimpl4.py
"""
import os
import re
import secrets
import sys
import urllib.parse
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refimpl3 as R3  # noqa: E402

R = R3.R
S, LOCK, Err = R.S, R.LOCK, R.Err
FLAGS = set(os.environ.get("REF4_BUGS", "").split(","))
iso, P, utcnow, parse_instant, INF = R3.iso, R3.P, R3.utcnow, R3.parse_instant, R3.INF

_r3_pay_obj = R3.pay_obj
_route3 = R3.route3


def pay_obj(p):
    d = _r3_pay_obj(p)
    d.setdefault("refund_of", None)
    return d


R.pay_obj = pay_obj
R3.pay_obj = pay_obj


def payment(pid):
    return next((x for x in S["payments"] if x["payment_id"] == pid), None)


def refunded(pid):
    return sum(x["amount"] for x in S["payments"] if x.get("refund_of") == pid)


def immutable(p, batch):
    if p.get("authorization_id") or p.get("refund_of"):
        return True
    return bool(p.get("settlement_id")) and not batch


# ------------------------------------------------------------------ corrections (single and batch)
def parse_item(b, with_id):
    if not isinstance(b, dict):
        raise Err(422, "validation_failed", "item")
    for f in (["payment_id"] if with_id else []) + ["expected_revision", "amount", "effective_at", "reason"]:
        if f not in b:
            raise Err(422, "validation_failed", f + " missing")
    if with_id and not isinstance(b["payment_id"], str):
        raise Err(400, "malformed_request", "payment_id type")
    er = b["expected_revision"]
    if isinstance(er, bool) or not isinstance(er, (int, float)):
        raise Err(400, "malformed_request", "type")
    er = R3.int_field(er, 1, None)
    amount = R3.int_field(b["amount"], 0, 10**9)
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
    return {"pid": b.get("payment_id"), "er": er, "amount": amount, "eff": eff, "reason": reason}


def check_item(it, batch):
    p = it["p"]
    if immutable(p, batch):
        raise Err(422, "linked_payment_immutable")
    if it["er"] != p["revs"][-1]["revision"]:
        raise Err(409, "stale_revision")
    if it["amount"] < refunded(p["payment_id"]) and "no-refund-floor" not in FLAGS:
        raise Err(422, "refund_exceeds_payment")


def apply_items(items, batch):
    """items: parsed dicts with 'p' (payment). Validates in spec order and applies atomically. Returns list of new revisions."""
    if batch:
        groups = {}
        for it in items:
            sid = it["p"].get("settlement_id")
            if sid:
                groups.setdefault(sid, []).append(it)
        for sid, its in groups.items():
            members = {x["payment_id"] for x in S["payments"] if x.get("settlement_id") == sid}
            if members != {i["p"]["payment_id"] for i in its} and "no-completeness" not in FLAGS:
                raise Err(422, "incomplete_settlement")
        for sid, its in groups.items():
            if len({i["eff"] for i in its}) > 1 and "no-instant-check" not in FLAGS:
                raise Err(422, "validation_failed", "settlement members need identical effective instants")
    net = {}
    for it in items:
        p = it["p"]
        diff = it["amount"] - p["revs"][-1]["amount"]
        net[p["from_user_id"]] = net.get(p["from_user_id"], 0) - diff
        net[p["to_user_id"]] = net.get(p["to_user_id"], 0) + diff
    for uid, n in net.items():
        if n < 0 and R.avail(uid) + n < 0:
            raise Err(409, "insufficient_funds")
        if "per-item-funds" in FLAGS:
            pass
    overrides = {it["p"]["payment_id"]: {"revision": it["p"]["revs"][-1]["revision"] + 1, "amount": it["amount"], "effective_at": iso(it["eff"]),
                                         "recorded_at": iso(utcnow()), "reason": it["reason"]} for it in items}
    if R3.violates(overrides):
        raise Err(409, "historical_overdraft")
    rec = utcnow()
    for it in items:
        last = P(it["p"]["revs"][-1]["recorded_at"])
        if rec <= last:
            rec = last + timedelta(microseconds=1)
    out = []
    bid = "cb_%s" % secrets.token_hex(5) if batch else None
    for it in items:
        p = it["p"]
        diff = it["amount"] - p["revs"][-1]["amount"]
        S["users"][p["from_user_id"]]["balance"] -= diff
        S["users"][p["to_user_id"]]["balance"] += diff
        new = {"revision": p["revs"][-1]["revision"] + 1, "amount": it["amount"], "effective_at": iso(it["eff"]), "recorded_at": iso(rec), "reason": it["reason"]}
        if batch:
            new["correction_batch_id"] = bid
        p["revs"].append(new)
        out.append(dict(new, payment_id=p["payment_id"]))
    return bid, iso(rec), out


def single_correction(uid, pid, hdrs, raw):
    key = hdrs.get("idempotency-key")
    with LOCK:
        p = payment(pid)
        if not p:
            raise Err(404, "not_found")
        if p["from_user_id"] != uid:
            raise Err(403, "forbidden")
    if key is None or key == "":
        raise Err(400, "missing_idempotency_key")
    b = R3.json_body(raw)

    def run():
        it = parse_item(b, False)
        it["p"] = p
        check_item(it, False)
        _, _, revs = apply_items([it], False)
        x = revs[0]
        return {k_: x[k_] for k_ in ("revision", "amount", "effective_at", "recorded_at", "reason", "payment_id")}

    with LOCK:
        return R.idem(uid, "POST", "/payments/%s/corrections" % pid, key, b, run)


def batch_correction(uid, hdrs, raw):
    with LOCK:
        if uid not in S["operators"]:
            raise Err(403, "forbidden")
    key = hdrs.get("idempotency-key")
    if key is None or key == "":
        raise Err(400, "missing_idempotency_key")
    b = R3.json_body(raw)

    def run():
        lst = b.get("corrections")
        if not isinstance(lst, list) or not 1 <= len(lst) <= 32:
            raise Err(422, "validation_failed", "corrections")
        ids = [x.get("payment_id") for x in lst if isinstance(x, dict)]
        if len(ids) != len(lst) or len(set(map(str, ids))) != len(ids):
            raise Err(422, "validation_failed", "items / distinct payment_ids")
        items = []
        for x in lst:
            it = parse_item(x, True)
            it["p"] = payment(it["pid"])
            if not it["p"]:
                raise Err(404, "not_found")
            check_item(it, True)          # item errors are reported in input order
            items.append(it)
        bid, rec, revs = apply_items(items, True)
        return {"correction_batch_id": bid, "recorded_at": rec, "revisions": revs}

    with LOCK:
        return R.idem(uid, "POST", "/correction-batches", key, b, run)


# ------------------------------------------------------------------ refunds
def refund_route(uid, pid, hdrs, raw):
    key = hdrs.get("idempotency-key")
    with LOCK:
        p = payment(pid)
        if not p:
            raise Err(404, "not_found")
        if p["to_user_id"] != uid:
            raise Err(403, "forbidden")
    if key is None or key == "":
        raise Err(400, "missing_idempotency_key")
    b = R3.json_body(raw)

    def run():
        if "amount" not in b:
            raise Err(422, "validation_failed", "amount")
        amt = R3.int_field(b["amount"], 1, 10**9)
        if p.get("refund_of"):
            raise Err(422, "invalid_refund_target")
        if amt + refunded(pid) > p["revs"][-1]["amount"] and "no-refund-cap" not in FLAGS:
            raise Err(422, "refund_exceeds_payment")
        receiver, sender = S["users"][uid], S["users"][p["from_user_id"]]
        if R.avail(uid) < amt:
            raise Err(409, "insufficient_funds")
        receiver["balance"] -= amt
        sender["balance"] += amt
        q = R.make_payment(receiver, sender, amt, p["note"], p["visibility"])
        q["refund_of"] = pid
        return pay_obj(q)

    with LOCK:
        return R.idem(uid, "POST", "/payments/%s/refunds" % pid, key, b, run)


def route4(method, path, qs, hdrs, raw):
    m = re.match(r"^/payments/([^/]+)/(corrections|refunds)$", path)
    if (m or path == "/correction-batches") and S:
        R3.ensure()
        uid = R3.bearer(hdrs)
        if path == "/correction-batches":
            if method != "POST":
                raise Err(405, "method_not_allowed")
            return batch_correction(uid, hdrs, raw)
        pid = urllib.parse.unquote(m.group(1))
        if method != "POST":
            raise Err(405, "method_not_allowed")
        if m.group(2) == "corrections":
            return single_correction(uid, pid, hdrs, raw)
        return refund_route(uid, pid, hdrs, raw)
    if path == "/_test/import" and "drop-snapshots" in FLAGS:
        res = _route3(method, path, qs, hdrs, raw)
        S["snaps"] = {}
        return res
    return _route3(method, path, qs, hdrs, raw)


R.route = route4

if __name__ == "__main__":
    R.S.update(R.fresh())
    R.Srv(("0.0.0.0", int(os.environ.get("PORT", "8080"))), R.H).serve_forever()
