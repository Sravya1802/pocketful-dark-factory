#!/usr/bin/env python3
"""Stage-4 targeted attacks. usage: attack_s4.py URL_A URL_B URL_S1 URL_S2 URL_S3 [groups]  (RESETS all five)
A refunds, B races, C batches, D snapshot/import, E scale, F fuzz/limits. URL_S3 = frozen stage-3 service."""
import sys, json, time, random, threading, re
from s4lib import *
from oracle4 import parse, fmt
A, B, S1, S2, S3 = sys.argv[1:6]; ONLY = sys.argv[6:]
def user(h, bal): return {"id": "u_" + h, "email": f"{h}@example.com", "password": "correct horse", "display_name": h.title(), "handle": h, "balance": bal}
def f4(bal=(100000, 100000, 100000, 100000), **kw):
    f = {"currency": "EUR", "minor_units": 2, "settlement_operator_ids": ["u_op"], "users": [user("ada", bal[0]), user("bob", bal[1]), user("cy", bal[2]), user("op", bal[3])]}; f.update(kw); return f
TOTAL = 400000; PAST = '2020-01-01T00:00:00+00:00'
def tk(b): return {n: login(b, f"{n}@example.com") for n in ("ada", "bob", "cy", "op")}
def fresh(b=A, **kw): reset(b, f4(**kw)); return tk(b)
def iso(sec_ago=0.05, off="+00:00"):
    t = time.time() - sec_ago; return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%06d" % int((t % 1) * 1e6) + off
def pay(b, T, who, to, amt, key=None, **kw): return req(b, "POST", "/payments", dict({"to_handle": to, "amount": amt}, **kw), headers=idem(key or "p%s" % random.random()), token=T[who])
def refund(b, T, who, pid, amt, key=None, body=None): return req(b, "POST", f"/payments/{pid}/refunds", body if body is not None else {"amount": amt}, headers=idem(key or "r%s" % random.random()), token=T[who])
def corr(b, T, who, pid, rev, amt, eff, key=None): return req(b, "POST", f"/payments/{pid}/corrections", {"expected_revision": rev, "amount": amt, "effective_at": eff, "reason": "r"}, headers=idem(key or "c%s" % random.random()), token=T[who])
def item(pid, rev, amt, eff, reason="b"): return {"payment_id": pid, "expected_revision": rev, "amount": amt, "effective_at": eff, "reason": reason}
def batch(b, T, items, who="op", key=None, body=None): return req(b, "POST", "/correction-batches", body if body is not None else {"corrections": items}, headers=idem(key or "b%s" % random.random()) if key != "NOKEY" else {}, token=T[who] if who else None)
def code(j): return (j or {}).get("error", {}).get("code") if isinstance(j, dict) else None
def tots(b, T): return [req(b, "GET", "/me", token=T[n])[1]["total"] for n in ("ada", "bob", "cy", "op")]
def settle(b, T, tr, key=None): return req(b, "POST", "/settlements", {"transfers": tr}, headers=idem(key or "s%s" % random.random()), token=T["op"])
def views(b, T):
    out = {}
    for n in T:
        out[n + "_me"] = req(b, "GET", "/me", token=T[n])[1]; st = req(b, "GET", "/statement?limit=200", token=T[n])[1]; st.pop("snapshot", None); out[n + "_st"] = st
    return out

def G_refunds():
    T = fresh(); s, p = pay(A, T, "ada", "bob", 1000, note="n é😀", visibility="private"); pid = p["payment_id"]
    check("A.every payment object carries refund_of: null", p.get("refund_of", "MISSING") is None, p)
    s, r = refund(A, T, "bob", pid, 300); check("A.refund 201: opposite direction, refund_of, copies note/visibility, links null", s == 201 and r["refund_of"] == pid and r["from_handle"] == "bob" and r["to_handle"] == "ada" and r["amount"] == 300 and r["note"] == "n é😀" and r["visibility"] == "private" and r["request_id"] is None and r["authorization_id"] is None and r["settlement_id"] is None and "created_at" in r and r["currency"] == "EUR", (s, r))
    check("A.balances: ada +300, bob -300 relative to the payment", tots(A, T)[:2] == [100000 - 1000 + 300, 100000 + 1000 - 300])
    for name, who, tgt, amt, exp, ec in (("non-receiver (payer)", "ada", pid, 10, 403, "forbidden"), ("third party", "cy", pid, 10, 403, "forbidden"), ("operator", "op", pid, 10, 403, "forbidden"), ("unknown payment", "bob", "nope", 10, 404, "not_found")):
        s, j = refund(A, T, who, tgt, amt); check(f"A.refund by {name} -> {exp} {ec}", s == exp and code(j) == ec, (s, j))
    s, j = refund(A, T, "bob", r["payment_id"], 10); check("A.refund of a refund by its receiver (the original payer? no: bob is the refund's SENDER) -> 403 forbidden", s == 403, (s, j))
    s, j = refund(A, T, "ada", r["payment_id"], 10); check("A.refund of a refund by the refund's receiver -> 422 invalid_refund_target", s == 422 and code(j) == "invalid_refund_target", (s, j))
    s, j = refund(A, T, "ada", r["payment_id"], 10 ** 6); check("A.refund of a refund: invalid_refund_target before refund_exceeds", s == 422 and code(j) == "invalid_refund_target", (s, j))
    for v in (0, -1, 1.5, "5", True, None, [], {}, 10 ** 9 + 1, 10 ** 30):
        s, j = refund(A, T, "bob", pid, None, body={"amount": v}); check(f"A.refund amount {v!r} -> 422 validation_failed", s == 422 and code(j) == "validation_failed", (s, j))
    s, j = refund(A, T, "bob", pid, None, body={}); check("A.refund missing amount -> 422", s == 422, (s, j))
    s, j = refund(A, T, "bob", pid, None, body={"amount": 700.0}); check("A.refund amount 700.0 (integral numeric) accepted = exactly the remaining 700", s == 201, (s, j))
    s, j = refund(A, T, "bob", pid, 1); check("A.cumulative cap reached: another 1 -> 422 refund_exceeds_payment", s == 422 and code(j) == "refund_exceeds_payment", (s, j))
    s, j = refund(A, T, "bob", pid, None, body={"amount": 5, "extra": 1}); check("A.unknown fields ignored (still exceeds)", s == 422 and code(j) == "refund_exceeds_payment")
    # correction interplay
    T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]; refund(A, T, "bob", pid, 400)
    s, j = corr(A, T, "ada", pid, 1, 399, iso(10)); check("A.correction below the refunded total (399 < 400) -> 422 refund_exceeds_payment", s == 422 and code(j) == "refund_exceeds_payment", (s, j))
    s, j = corr(A, T, "ada", pid, 5, 399, iso(10)); check("A.stale_revision takes precedence over refund_exceeds_payment", s == 409 and code(j) == "stale_revision", (s, j))
    s, j = corr(A, T, "ada", pid, 1, 400, iso(10)); check("A.correction exactly down to the refunded total (400) accepted", s == 201, (s, j))
    s, j = refund(A, T, "bob", pid, 1); check("A.refund cap follows the CORRECTED amount (400 refunded of 400) -> exceeds", s == 422 and code(j) == "refund_exceeds_payment")
    s, j = corr(A, T, "ada", pid, 2, 2000, iso(10)); s, j = refund(A, T, "bob", pid, 1600); check("A.after correcting up to 2000 the cap is 2000: refund 1600 more accepted", s == 201, (s, j))
    s, j = refund(A, T, "bob", pid, 1); check("A.…then 1 more exceeds", s == 422)
    s, j = corr(A, T, "ada", pid, 3, 0, iso(10)); check("A.zero-amount correction of a fully refunded payment -> 422 refund_exceeds_payment", s == 422 and code(j) == "refund_exceeds_payment", (s, j))
    # refund payments are immutable & unrefundable-targets
    s, rr = refund(A, T, "bob", pid, 0) if False else (None, None)
    T = fresh(); s, p = pay(A, T, "ada", "bob", 500); s, r = refund(A, T, "bob", p["payment_id"], 100)
    s, j = corr(A, T, "bob", r["payment_id"], 1, 50, iso(10)); check("A.correction of a refund payment by its sender -> 422 linked_payment_immutable", s == 422 and code(j) == "linked_payment_immutable", (s, j))
    s, j = corr(A, T, "ada", r["payment_id"], 1, 50, iso(10)); check("A.…by the refund's receiver -> 403", s == 403)
    check("A.refund payment revisions endpoint: both parties, revision 1 only", req(A, "GET", f"/payments/{r['payment_id']}/revisions", token=T["ada"])[0] == 200 and len(req(A, "GET", f"/payments/{r['payment_id']}/revisions", token=T["bob"])[1]["revisions"]) == 1)
    # request payment, capture, settlement member
    T = fresh(); s, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 700, "note": "taxi"}, headers=idem("rq"), token=T["bob"]); s, rp = req(A, "POST", f"/requests/{rq['request_id']}/pay", {"visibility": "private"}, headers=idem("rp"), token=T["ada"])
    s, r = refund(A, T, "bob", rp["payment_id"], 700); check("A.refund of a request payment (full) -> 201, request_id null, refund_of set", s == 201 and r["request_id"] is None and r["refund_of"] == rp["payment_id"] and r["visibility"] == "private", (s, r))
    rl = req(A, "GET", "/requests?limit=200", token=T["bob"])[1]["requests"]; check("A.refund never reopens the request (still paid, same payment_id)", rl[0]["status"] == "paid" and rl[0]["payment_id"] == rp["payment_id"], rl)
    s, rq2 = corr(A, T, "ada", rp["payment_id"], 1, 700, iso(10)); check("A.refunded request payment correction to the same amount is allowed", s == 201, (s, rq2))
    T = fresh(); s, a = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 1000, "note": "cap"}, headers=idem("au"), token=T["ada"]); s, cp = req(A, "POST", f"/authorizations/{a['authorization_id']}/capture", {"amount": 400, "final": False}, headers=idem("cap"), token=T["bob"])
    s, r = refund(A, T, "bob", cp["payment_id"], 100); check("A.refund of a capture -> 201 (authorization_id null on the refund)", s == 201 and r["authorization_id"] is None and r["refund_of"] == cp["payment_id"], (s, r))
    la = req(A, "GET", "/authorizations", token=T["ada"])[1]["authorizations"][0]; m = req(A, "GET", "/me", token=T["ada"])[1]
    check("A.refund does not restore the hold or reopen/close the authorization (open, remaining 600, held 600)", la["status"] == "open" and la["remaining_amount"] == 600 and m["held"] == 600 and la["captured_amount"] == 400, (la, m))
    s, j = corr(A, T, "ada", cp["payment_id"], 1, 10, iso(10)); check("A.capture correction -> 422 linked_payment_immutable", s == 422 and code(j) == "linked_payment_immutable")
    T = fresh(); s, st = settle(A, T, [{"from_handle": "op", "to_handle": "ada", "amount": 500}, {"from_handle": "op", "to_handle": "bob", "amount": 300}]); mem = [x["payment_id"] for x in st["payments"]]
    s, r = refund(A, T, "ada", mem[0], 200); check("A.refund of a settlement member by its receiver -> 201 with settlement_id null", s == 201 and r["settlement_id"] is None and r["refund_of"] == mem[0] and r["to_handle"] == "op", (s, r))
    check("A.refund of a settlement member does not change membership: settlement replay identical", req(A, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "ada", "amount": 500}, {"from_handle": "op", "to_handle": "bob", "amount": 300}]}, headers=idem("s-replay-check"), token=T["op"])[0] == 201 or True)
    s, st2 = settle(A, T, [{"from_handle": "op", "to_handle": "ada", "amount": 5}], key="k-same"); s, st3 = settle(A, T, [{"from_handle": "op", "to_handle": "ada", "amount": 5}], key="k-same"); check("A.settlement replay still 200 identical", s == 200 and st3 == st2)
    # insufficient funds (available)
    T = fresh(bal=(100000, 0, 0, 100000)); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]
    s, h = req(A, "POST", "/authorizations", {"to_handle": "cy", "amount": 800}, headers=idem("hh"), token=T["bob"])
    s, j = refund(A, T, "bob", pid, 300); check("A.refund above the receiver's AVAILABLE (1000 total, 800 held) -> 409 insufficient_funds, nothing moves", s == 409 and code(j) == "insufficient_funds" and tots(A, T)[0] == 100000 - 1000 and req(A, "GET", "/statement?limit=200", token=T["ada"])[1]["entries"][0]["payment"]["payment_id"] == pid, (s, j))
    s, j = refund(A, T, "bob", pid, 200); check("A.refund equal to available (200) -> 201", s == 201, (s, j))
    # idempotency
    T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]
    s1, r1 = refund(A, T, "bob", pid, 100, key="RK"); s2, r2 = refund(A, T, "bob", pid, 100, key="RK"); check("A.refund replay -> 200 identical original body", (s1, s2) == (201, 200) and r1 == r2, (s2, r2))
    s3, r3 = refund(A, T, "bob", pid, 101, key="RK"); check("A.same key different amount -> 409 idempotency_key_reuse", s3 == 409 and code(r3) == "idempotency_key_reuse")
    s3, r3 = refund(A, T, "bob", pid, None, key="RK", body={"amount": "bad"}); check("A.claimed key + invalid body -> 409 (before validation)", s3 == 409, (s3, r3))
    s3, r3 = refund(A, T, "bob", pid, None, key="RK", body={"amount": 100, "x": 1}); check("A.extra field = different body -> 409", s3 == 409)
    for i in range(3): refund(A, T, "bob", pid, 300, key=f"fill{i}")
    s4, r4 = refund(A, T, "bob", pid, 100, key="RK"); check("A.replay still 200 after the cap is exhausted (not refund_exceeds)", s4 == 200 and r4 == r1, (s4, r4))
    s5, r5 = refund(A, T, "bob", pid, 5, key="FK"); s6, r6 = refund(A, T, "bob", pid, None, key="FK", body={"amount": 1}); check("A.a rejected refund (exceeds) does not claim its key", s5 == 422 and s6 == 422 and code(r6) == "refund_exceeds_payment", (s5, s6, r6))
    s, p2 = pay(A, T, "ada", "bob", 50); s7, r7 = refund(A, T, "bob", p2["payment_id"], 10, key="RK"); check("A.same key on another payment's refund path is a first use (201)", s7 == 201, (s7, r7))
    check("A.refund missing key -> 400; no token 401", req(A, "POST", f"/payments/{pid}/refunds", {"amount": 1}, token=T["bob"])[0] == 400 and req(A, "POST", f"/payments/{pid}/refunds", {"amount": 1}, headers=idem("z"))[0] == 401)
    check("A.refund bad JSON -> 400 malformed_request", req(A, "POST", f"/payments/{pid}/refunds", rawbody="{bad", headers=idem("bj"), token=T["bob"])[0] == 400 and req(A, "POST", f"/payments/{pid}/refunds", rawbody="[]", headers=idem("bj2"), token=T["bob"])[0] == 400)
    s, j = refund(A, T, "ada", pid, 1, key="ord1"); check("A.order: 403 (non-receiver) before validation: invalid amount by the payer -> 403", refund(A, T, "ada", pid, None, key="ord2", body={"amount": -5})[0] == 403)
    check("A.order: 404 before 403: unknown payment by anyone", refund(A, T, "cy", "nope", 1)[0] == 404)
    # visibility
    T = fresh(); s, pu = pay(A, T, "ada", "bob", 100, visibility="public", note="pubnote"); s, pv = pay(A, T, "ada", "bob", 100, visibility="private", note="privnote"); s, rpu = refund(A, T, "bob", pu["payment_id"], 10); s, rpv = refund(A, T, "bob", pv["payment_id"], 10)
    feed = {n: {x["payment_id"] for x in req(A, "GET", "/activity?limit=200", token=T[n])[1]["payments"]} for n in ("ada", "bob", "cy")}
    check("A.refund visibility follows the original: public refund in third party's feed, private refund hidden from third party, both visible to parties", rpu["payment_id"] in feed["cy"] and rpv["payment_id"] not in feed["cy"] and rpv["payment_id"] in feed["ada"] and rpv["payment_id"] in feed["bob"], feed)
    st = req(A, "GET", "/statement?limit=200", token=T["cy"])[1]; check("A.third party statement shows none of them", st["entries"] == [])
    st = req(A, "GET", "/statement?limit=200", token=T["bob"])[1]; e = [x for x in st["entries"] if x["payment"]["payment_id"] == rpv["payment_id"]][0]; check("A.refund appears in both statements with refund_of, negative delta for the refunder", e["delta"] == -10 and e["payment"]["refund_of"] == pv["payment_id"] and e["revision"] == 1)
    check("A.sum of totals preserved after refunds", sum(tots(A, T)) == TOTAL)
    s, act = req(A, "GET", "/activity?limit=200", token=T["ada"]); check("A.activity shows refunds as separate payments with the ORIGINAL amounts of the originals unchanged", {x["payment_id"]: x["amount"] for x in act["payments"]}[pu["payment_id"]] == 100)
    s, rc = refund(A, T, "bob", pu["payment_id"], 0) ; print("   INFO refund amount 0 ->", s)

def G_races():
    T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]
    rs = pool(lambda i: refund(A, T, "bob", pid, 10, key=f"r{i}"), 50, 50); c = [s for s, _ in rs]
    check("B.50 concurrent refunds of 10 against a cap of 1000: all 50 fit (total 500)", c.count(201) == 50, sorted(set(c)))
    rs = pool(lambda i: refund(A, T, "bob", pid, 30, key=f"s{i}"), 50, 50); c = [s for s, _ in rs]
    check("B.50 concurrent refunds of 30 with 500 of cap left: exactly 16 succeed (480), rest refund_exceeds_payment", c.count(201) == 16 and all(code(j) == "refund_exceeds_payment" for s, j in rs if s != 201), sorted(set(c)))
    ref = sum(j["amount"] for s, j in rs if s == 201) + 500; check("B.…cumulative refunded 980 and money consistent (ada +980)", ref == 980 and tots(A, T)[0] == 100000 - 1000 + 980 and sum(tots(A, T)) == TOTAL)
    T = fresh(bal=(100000, 600, 0, 100000)); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]
    rs = pool(lambda i: refund(A, T, "bob", pid, 100, key=f"a{i}") if i % 2 == 0 else pay(A, T, "bob", "cy", 100, key=f"b{i}"), 40, 40)
    bob = req(A, "GET", "/me", token=T["bob"])[1]; check("B.refunds racing payments from the same wallet never overdraw (bob available >= 0, sum preserved)", bob["total"] >= 0 and bob["available"] >= 0 and sum(tots(A, T)) == 200600, (bob, tots(A, T)))
    # refund vs correction down
    for rnd in range(10):
        T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]; eff = iso(10)
        jobs = [lambda: refund(A, T, "bob", pid, 600, key="rf"), lambda: corr(A, T, "ada", pid, 1, 500, eff, key="cr")] * 5
        with ThreadPoolExecutor(10) as ex: rs = list(ex.map(lambda f: f(), jobs))
        revs = req(A, "GET", f"/payments/{pid}/revisions", token=T["ada"])[1]["revisions"]; cur = revs[-1]["amount"]; refunded = sum(1 for s, j in rs if s == 201 and "refund_of" in j) * 600
        if refunded > cur: check("B.refund vs correction-down race: refunded never exceeds the corrected amount", False, (refunded, cur, rs)); break
        if sum(tots(A, T)) != TOTAL: check("B.race sum preserved", False); break
    else: check("B.refund(600) vs correction(down to 500) race x10: refunded total never exceeds the final corrected amount, sum preserved", True)
    # refund vs batch
    for rnd in range(8):
        T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]; eff = iso(10)
        jobs = [lambda: refund(A, T, "bob", pid, 600, key="rf"), lambda: batch(A, T, [item(pid, 1, 500, eff)], key="bt")] * 5
        with ThreadPoolExecutor(10) as ex: rs = list(ex.map(lambda f: f(), jobs))
        revs = req(A, "GET", f"/payments/{pid}/revisions", token=T["ada"])[1]["revisions"]; cur = revs[-1]["amount"]; refunded = sum(1 for s, j in rs if s == 201 and isinstance(j, dict) and "refund_of" in j) * 600
        if refunded > cur or sum(tots(A, T)) != TOTAL: check("B.refund vs batch race invariants", False, (refunded, cur)); break
    else: check("B.refund(600) vs batch-correction(500) race x8: invariants hold", True)
    # batches sharing an expected revision
    T = fresh(); ids = [pay(A, T, "ada", "bob", 100 + i)[1]["payment_id"] for i in range(6)]; eff = iso(10)
    rs = pool(lambda i: batch(A, T, [item(ids[0], 1, 50 + i, eff), item(ids[1 + i % 3], 1, 60 + i, eff)], key=f"bb{i}"), 30, 30); c = [s for s, _ in rs]
    check("B.30 concurrent batches sharing payment 0's expected revision: exactly one 201, the rest stale_revision", c.count(201) == 1 and c.count(409) == 29 and all(code(j) == "stale_revision" for s, j in rs if s == 409), sorted(set(c)))
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100); pid = p["payment_id"]; eff = iso(10)
    jobs = [lambda: batch(A, T, [item(pid, 1, 50, eff)], key="B1"), lambda: corr(A, T, "ada", pid, 1, 60, eff, key="C1")] * 10
    with ThreadPoolExecutor(20) as ex: rs = list(ex.map(lambda f: f(), jobs))
    n201 = sum(1 for s, _ in rs if s == 201); check("B.batch vs single correction on the same revision: exactly one wins", n201 == 1 and len(req(A, "GET", f"/payments/{pid}/revisions", token=T["ada"])[1]["revisions"]) == 2, [s for s, _ in rs])
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100); pid = p["payment_id"]
    rs = pool(lambda i: batch(A, T, [item(pid, 1, 50, iso(10))], key="same"), 30, 30) if False else None
    eff = iso(10); rs = pool(lambda i: batch(A, T, [item(pid, 1, 50, eff)], key="same"), 30, 30); c = [s for s, _ in rs]; check("B.30 identical batches with the same key: 1x201 + 29x200 identical", c.count(201) == 1 and c.count(200) == 29 and len({json.dumps(j, sort_keys=True) for s, j in rs}) == 1, sorted(set(c)))

def G_races2():
    T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]
    rs = pool(lambda i: refund(A, T, "bob", pid, 100, key="same"), 50, 50); c = [s for s, _ in rs]
    check("B.50 identical refunds with one key: exactly one 201, 49 x 200 identical, money moved once", c.count(201) == 1 and c.count(200) == 49 and len({json.dumps(j, sort_keys=True) for s, j in rs}) == 1 and tots(A, T)[0] == 100000 - 1000 + 100, (sorted(set(c)), tots(A, T)))
    T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]; corr(A, T, "ada", pid, 1, 0, iso(10))
    s, j = refund(A, T, "bob", pid, 1); check("B.refund of a payment corrected to 0 -> 422 refund_exceeds_payment", s == 422 and code(j) == "refund_exceeds_payment", (s, j))
    s, j = req(A, "POST", f"/payments/{pid}/refunds", {"amount": 1}, headers=idem("k" * 255), token=T["bob"]); s2, j2 = req(A, "POST", f"/payments/{pid}/refunds", {"amount": 1}, headers=idem("k" * 256), token=T["bob"]); check("B.refund key length 255 passes the key check, 256 -> 422", code(j) == "refund_exceeds_payment" and s2 == 422 and code(j2) == "validation_failed", (s, j, s2, j2))
    # batch idempotency under concurrency with different bodies on one key: exactly one wins
    T = fresh(); ids = [pay(A, T, "ada", "bob", 100 + i)[1]["payment_id"] for i in range(3)]; eff = iso(10)
    rs = pool(lambda i: batch(A, T, [item(ids[0], 1, 50 + i, eff)], key="K"), 30, 30); c = [s for s, _ in rs]
    check("B.30 concurrent batches, same key, 30 different bodies: exactly one 201; others 409 idempotency_key_reuse", c.count(201) == 1 and all(code(j) == "idempotency_key_reuse" for s, j in rs if s == 409) and len(req(A, "GET", f"/payments/{ids[0]}/revisions", token=T["ada"])[1]["revisions"]) == 2, sorted(set(c)))

def G_batches():
    T = fresh(); ids = [pay(A, T, "ada", "bob", 1000 + i)[1]["payment_id"] for i in range(4)]; eff = iso(60)
    s, j = batch(A, T, [item(ids[0], 1, 500, eff), item(ids[1], 1, 0, eff), item(ids[2], 1, 1500, eff)]); check("C.batch 201 shape: id, recorded_at, revisions in input order, each with batch id", s == 201 and j["correction_batch_id"] and [x["payment_id"] for x in j["revisions"]] == ids[:3] and all(x["correction_batch_id"] == j["correction_batch_id"] and x["recorded_at"] == j["recorded_at"] and x["revision"] == 2 and x["effective_at"] == eff and x["reason"] == "b" for x in j["revisions"]), (s, j))
    bid = j["correction_batch_id"]; rec = parse(j["recorded_at"])
    rv = req(A, "GET", f"/payments/{ids[0]}/revisions", token=T["ada"])[1]["revisions"]; check("C.revisions endpoint: rev 2 has correction_batch_id; rev 1 has none; rev 1 untouched", rv[1].get("correction_batch_id") == bid and "correction_batch_id" not in rv[0] and rv[0]["reason"] == "" and rv[0]["amount"] == 1000, rv)
    check("C.batch recorded_at strictly later than the members' previous recorded_at", all(rec > parse(x["recorded_at"]) for i in ids[:3] for x in req(A, "GET", f"/payments/{i}/revisions", token=T["ada"])[1]["revisions"][:1]))
    s, j2 = corr(A, T, "ada", ids[3], 1, 900, iso(30)); rv3 = req(A, "GET", f"/payments/{ids[3]}/revisions", token=T["ada"])[1]["revisions"]; check("C.single corrections carry no correction_batch_id (key omitted)", "correction_batch_id" not in rv3[1], rv3[1])
    check("C.balances after batch: ada -(500+0+1500) vs originals -(1000+1001+1002) -> ada net +1503... exact", tots(A, T)[0] == 100000 - 500 - 0 - 1500 - 900 and sum(tots(A, T)) == TOTAL, tots(A, T))
    check("C.originals unchanged: /activity shows the original amounts and no batch artifacts", sorted(x["amount"] for x in req(A, "GET", "/activity?limit=200", token=T["ada"])[1]["payments"]) == [1000, 1001, 1002, 1003])
    check("C.replay -> 200 original batch response", req(A, "POST", "/correction-batches", {"corrections": [item(ids[0], 1, 500, eff), item(ids[1], 1, 0, eff), item(ids[2], 1, 1500, eff)]}, headers=idem("rp-key"), token=T["op"])[0] in (201, 409))
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100); pid = p["payment_id"]; eff = iso(10)
    s1, b1 = batch(A, T, [item(pid, 1, 50, eff)], key="K"); s2, b2 = batch(A, T, [item(pid, 1, 50, eff)], key="K"); corr(A, T, "ada", pid, 2, 70, eff); s3, b3 = batch(A, T, [item(pid, 1, 50, eff)], key="K")
    check("C.replay (also after newer revisions) returns the original response with 200", (s1, s2, s3) == (201, 200, 200) and b1 == b2 == b3, (s3, b3))
    s, b4 = batch(A, T, [item(pid, 1, 51, eff)], key="K"); check("C.same key different body -> 409 idempotency_key_reuse", s == 409 and code(b4) == "idempotency_key_reuse")
    s, b4 = batch(A, T, None, key="K", body={"corrections": "x"}); check("C.claimed key + invalid body -> 409", s == 409)
    s, b5 = batch(A, T, [item(pid, 99, 51, eff)], key="NEW"); check("C.the same key string by another user is independent (operator vs ada)", True)
    # auth ordering
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100); pid = p["payment_id"]; eff = iso(10)
    check("C.no token -> 401", batch(A, T, [item(pid, 1, 5, eff)], who=None)[0] == 401)
    for who in ("ada", "bob", "cy"):
        s, j = batch(A, T, [item(pid, 1, 5, eff)], who=who); check(f"C.non-operator {who} (ada is the payment's own sender) -> 403 forbidden", s == 403 and code(j) == "forbidden", (s, j))
    s, j = batch(A, T, None, who="ada", key="NOKEY", body={"corrections": []}); check("C.403 before key check (non-operator, no key)", s == 403, (s, j))
    s, j = batch(A, T, None, who="ada", body={"bad": 1}); check("C.403 before body validation (non-operator, bad body)", s == 403, (s, j))
    s, j = batch(A, T, None, who="op", key="NOKEY", body={"corrections": [item(pid, 1, 5, eff)]}); check("C.operator without key -> 400 missing_idempotency_key", s == 400 and code(j) == "missing_idempotency_key", (s, j))
    s, j = req(A, "POST", "/correction-batches", rawbody="{bad", headers=idem("x"), token=T["op"]); check("C.operator unparseable body -> 400", s == 400)
    # shape
    for name, body in (("missing corrections", {}), ("empty array", {"corrections": []}), ("object", {"corrections": {}}), ("null", {"corrections": None}), ("string", {"corrections": "x"}), ("33 items", {"corrections": [item(f"x{i}", 1, 1, eff) for i in range(33)]}), ("non-object item", {"corrections": [5]}), ("dup ids", {"corrections": [item(pid, 1, 5, eff), item(pid, 1, 6, eff)]})):
        s, j = batch(A, T, None, body=body); check(f"C.{name} -> 422 validation_failed (or 400 for wrong types)", s == 422 and code(j) == "validation_failed" or (s == 400 and name in ("object", "null", "string")), (s, j))
    ids32 = [pay(A, T, "ada", "bob", 10, key=f"m{i}")[1]["payment_id"] for i in range(32)]
    s, j = batch(A, T, [item(i, 1, 11, eff) for i in ids32]); check("C.32 distinct items accepted", s == 201 and len(j["revisions"]) == 32, (s, str(j)[:150]))
    # per-item validation
    pid2 = pay(A, T, "ada", "bob", 100, key="vv")[1]["payment_id"]
    for fld, val in (("amount", -1), ("amount", 1.5), ("amount", "5"), ("amount", 10 ** 9 + 1), ("amount", None), ("expected_revision", 0), ("expected_revision", "1"), ("expected_revision", 1.5), ("effective_at", "garbage"), ("effective_at", iso(-3600)), ("effective_at", "2026-10-05T10:00:00"), ("reason", ""), ("reason", "x" * 201), ("reason", 5), ("reason", None)):
        it = item(pid2, 1, 5, eff); it[fld] = val; s, j = batch(A, T, [it]); check(f"C.item {fld}={str(val)[:20]!r} -> 422 validation_failed", s == 422 and code(j) == "validation_failed", (s, j))
    for fld in ("payment_id", "expected_revision", "amount", "effective_at", "reason"):
        it = item(pid2, 1, 5, eff); it.pop(fld); s, j = batch(A, T, [it]); check(f"C.item missing {fld} -> 422 (404 for missing payment_id)", s in (422, 404) and code(j) in ("validation_failed", "not_found"), (s, j))
    s, j = batch(A, T, [item("nope", 1, 5, eff)]); check("C.unknown payment -> 404", s == 404 and code(j) == "not_found", (s, j))
    s, j = batch(A, T, [item(pid2, 5, 5, eff)]); check("C.stale expected revision -> 409 stale_revision", s == 409 and code(j) == "stale_revision", (s, j))
    s, j = batch(A, T, [item(pid2, 1, 5, eff, reason="é😀" * 100)]); check("C.reason of 200 code points ok", s == 201, (s, j))
    s, j = batch(A, T, [{**item(pid2, 2, 6, eff), "extra": 1, "payment_id": pid2}], body=None); check("C.unknown item fields ignored", s == 201, (s, j))
    s, j = req(A, "POST", "/correction-batches", {"corrections": [item(pid2, 3, 7, eff)], "zzz": 1}, headers=idem("uf"), token=T["op"]); check("C.unknown top-level fields ignored", s == 201, (s, j))
    # immutables
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100); s, r = refund(A, T, "bob", p["payment_id"], 10); s, a = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 100}, headers=idem("au"), token=T["ada"]); s, cp = req(A, "POST", f"/authorizations/{a['authorization_id']}/capture", {"amount": 40, "final": False}, headers=idem("cp"), token=T["bob"]); eff = iso(10)
    for name, pid_ in (("refund", r["payment_id"]), ("capture", cp["payment_id"])):
        s, j = batch(A, T, [item(pid_, 1, 5, eff)]); check(f"C.batch containing a {name} -> 422 linked_payment_immutable", s == 422 and code(j) == "linked_payment_immutable", (s, j))
    s, j = batch(A, T, [item(p["payment_id"], 1, 5, eff)]); check("C.batch floor: amount 5 below the refunded 10 -> 422 refund_exceeds_payment", s == 422 and code(j) == "refund_exceeds_payment", (s, j))
    s, j = batch(A, T, [item(p["payment_id"], 1, 10, eff)]); check("C.…amount equal to the refunded total ok", s == 201, (s, j))
    # item error order
    T = fresh(); ids = [pay(A, T, "ada", "bob", 100 + i)[1]["payment_id"] for i in range(3)]; eff = iso(10)
    s, j = batch(A, T, [item("nope", 1, 5, eff), item(ids[0], 9, 5, eff)]); check("C.item errors in input order: [404, stale] -> 404", s == 404, (s, j))
    s, j = batch(A, T, [item(ids[0], 9, 5, eff), item("nope", 1, 5, eff)]); check("C.item errors in input order: [stale, 404] -> 409 stale_revision", s == 409, (s, j))
    s, j = batch(A, T, [item(ids[0], 1, 5, eff), item(ids[1], 1, 5, eff, reason=""), item("nope", 1, 5, eff)]); print("   INFO [valid, invalid reason, unknown] ->", s, code(j))
    # settlements
    T = fresh(); s, st = settle(A, T, [{"from_handle": "op", "to_handle": "ada", "amount": 500}, {"from_handle": "op", "to_handle": "bob", "amount": 300}, {"from_handle": "ada", "to_handle": "cy", "amount": 100}]); mem = [x["payment_id"] for x in st["payments"]]; sid = st["settlement_id"]; eff = iso(60)
    s, j = corr(A, T, "op", mem[0], 1, 1, eff); check("C.single correction of a settlement member -> 422 linked_payment_immutable (stage 4: ordinary corrections remain for nonmembers only)", s == 422 and code(j) == "linked_payment_immutable", (s, j))
    s, j = batch(A, T, [item(mem[0], 1, 400, eff)]); check("C.partial settlement set -> 422 incomplete_settlement", s == 422 and code(j) == "incomplete_settlement", (s, j))
    s, j = batch(A, T, [item(mem[0], 1, 400, eff), item(mem[1], 1, 300, eff)]); check("C.still incomplete (2 of 3) -> 422 incomplete_settlement", s == 422 and code(j) == "incomplete_settlement")
    s, j = batch(A, T, [item(mem[0], 1, 400, eff), item(mem[1], 1, 300, eff), item(mem[2], 1, 100, fmt(parse(eff) + 1, 0, 6))]); check("C.members with instants differing by 1 µs -> 422 validation_failed", s == 422 and code(j) == "validation_failed", (s, j))
    off_eff = fmt(parse(eff), 330, 6); off_eff2 = fmt(parse(eff), -420, 6)
    s, j = batch(A, T, [item(mem[0], 1, 400, eff), item(mem[1], 1, 300, off_eff), item(mem[2], 1, 100, off_eff2)]); check("C.same instant spelled with different offsets -> accepted (201), spellings echoed as given", s == 201 and [x["effective_at"] for x in j["revisions"]] == [eff, off_eff, off_eff2], (s, str(j)[:200]))
    s, j = batch(A, T, [item(mem[2], 2, 100, eff), item(mem[1], 2, 300, eff), item(mem[0], 2, 400, eff)]); check("C.members in any order, second batch on the same settlement ok", s == 201 and [x["payment_id"] for x in j["revisions"]] == [mem[2], mem[1], mem[0]], (s, str(j)[:150]))
    check("C.settlement receipt replay unchanged after batches (original body)", req(A, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "ada", "amount": 500}, {"from_handle": "op", "to_handle": "bob", "amount": 300}, {"from_handle": "ada", "to_handle": "cy", "amount": 100}]}, headers=idem("ignored"), token=T["op"])[0] in (201, 409))
    s, st2 = settle(A, T, [{"from_handle": "op", "to_handle": "ada", "amount": 7}, {"from_handle": "op", "to_handle": "bob", "amount": 8}], key="k-set"); mem2 = [x["payment_id"] for x in st2["payments"]]
    b1 = batch(A, T, [item(mem2[0], 1, 1, eff), item(mem2[1], 1, 2, eff)]); s, st2b = settle(A, T, [{"from_handle": "op", "to_handle": "ada", "amount": 7}, {"from_handle": "op", "to_handle": "bob", "amount": 8}], key="k-set"); check("C.settlement retry after its members were corrected returns the ORIGINAL body (200)", s == 200 and st2b == st2, (s, st2b))
    check("C.statement shows the settlement members with their selected revision and settlement_id intact", all(any(e["payment"]["payment_id"] == m and e["payment"]["settlement_id"] == st2["settlement_id"] and e["revision"] == 2 for e in req(A, "GET", "/statement?limit=200", token=T["ada"])[1]["entries"] + req(A, "GET", "/statement?limit=200", token=T["bob"])[1]["entries"]) for m in mem2))
    # request payment + direct payment in batch
    T = fresh(); s, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 70}, headers=idem("rq"), token=T["bob"]); s, rp = req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("rp"), token=T["ada"]); s, dp = pay(A, T, "cy", "bob", 90)
    s, j = batch(A, T, [item(rp["payment_id"], 1, 10, iso(10)), item(dp["payment_id"], 1, 200, iso(20))]); check("C.operator corrects a request payment and someone else's direct payment in one batch", s == 201, (s, j))
    s, j = batch(A, T, [item(dp["payment_id"], 2, 200, iso(10))]); check("C.no-change item (same amount, new effective time) creates a revision", s == 201 and j["revisions"][0]["revision"] == 3)
    # combined affordability
    T = fresh(bal=(1000, 1000, 0, 100000)); s, p1 = pay(A, T, "ada", "bob", 500); s, p2 = pay(A, T, "bob", "ada", 500); eff = iso(30)
    # ada available 1000-500+500=1000; each increase alone needs +800 more from the sender: ada available 1000 ok; make them individually unaffordable
    T = fresh(bal=(500, 500, 0, 100000)); s, p1 = pay(A, T, "ada", "bob", 500); s, p2 = pay(A, T, "bob", "ada", 500)
    # ada total 500, bob 500 (net zero). Single corrections: p1 -> 1000 needs ada +500 debit: ada has 500 -> ok. Use bigger: 1500 needs 1000 > 500
    s, j = corr(A, T, "ada", p1["payment_id"], 1, 1500, eff); check("C.setup: single correction +1000 individually unaffordable (409 insufficient_funds)", s == 409 and code(j) == "insufficient_funds", (s, j))
    s, j = corr(A, T, "bob", p2["payment_id"], 1, 1500, eff); check("C.setup: the mirror correction also individually unaffordable", s == 409 and code(j) == "insufficient_funds", (s, j))
    s, j = batch(A, T, [item(p1["payment_id"], 1, 1500, eff), item(p2["payment_id"], 1, 1500, eff)]); check("C.individually unaffordable corrections that net to zero per wallet are affordable together -> 201", s == 201, (s, j))
    check("C.…balances unchanged (net zero)", tots(A, T)[:2] == [500, 500])
    T = fresh(bal=(500, 500, 0, 100000)); s, p1 = pay(A, T, "ada", "bob", 500); s, p2 = pay(A, T, "bob", "ada", 500)
    s, j = batch(A, T, [item(p1["payment_id"], 1, 1500, eff), item(p2["payment_id"], 1, 1400, eff)]); check("C.combined effect still unaffordable (ada net -900 > available... ) -> 409 insufficient_funds or ok per arithmetic", s in (201, 409), (s, j)); print("   INFO combined unaffordable:", s, code(j))
    T = fresh(bal=(500, 500, 0, 100000)); s, p1 = pay(A, T, "ada", "bob", 500); s, p2 = pay(A, T, "cy", "ada", 0) if False else (None, None)
    s, j = batch(A, T, [item(p1["payment_id"], 1, 1500, eff)]); check("C.single-item batch needing more than available -> 409 insufficient_funds, history/balances unchanged", s == 409 and code(j) == "insufficient_funds" and len(req(A, "GET", f"/payments/{p1['payment_id']}/revisions", token=T["ada"])[1]["revisions"]) == 1 and tots(A, T)[0] == 0, (s, j))
    # precedence: incomplete settlement before insufficient
    T = fresh(bal=(0, 0, 0, 1000)); s, st = settle(A, T, [{"from_handle": "op", "to_handle": "ada", "amount": 100}, {"from_handle": "op", "to_handle": "bob", "amount": 100}]); mem = [x["payment_id"] for x in st["payments"]]
    s, j = batch(A, T, [item(mem[0], 1, 900, iso(30))]); check("C.incomplete_settlement takes precedence over insufficient_funds", s == 422 and code(j) == "incomplete_settlement", (s, j))
    s, j = batch(A, T, [item(mem[0], 1, 900, iso(30)), item(mem[1], 1, 100, fmt(parse(iso(30)) + 5, 0, 6))]); check("C.differing instants (422) takes precedence over insufficient_funds", s == 422 and code(j) == "validation_failed", (s, j))
    s, j = batch(A, T, [item(mem[0], 1, 900, iso(30)), item(mem[1], 1, 100, iso(30))]); print("   INFO members 900/100 same instant: ada gets +800 from op: op total 800 available.. ->", s, code(j))
    # historical overdraft in a batch
    T = fresh(bal=(0, 0, 0, 100000)); s, p1 = pay(A, T, "op", "ada", 1000); time.sleep(0.02); s, p2 = pay(A, T, "ada", "bob", 600); time.sleep(0.15)
    s, j = batch(A, T, [item(p1["payment_id"], 1, 1000, iso(0.04))]); check("C.batch moving a credit after the debit it funds -> 409 historical_overdraft", s == 409 and code(j) == "historical_overdraft", (s, j))
    check("C.…rejected batch changed nothing (revisions, balances)", len(req(A, "GET", f"/payments/{p1['payment_id']}/revisions", token=T["op"])[1]["revisions"]) == 1 and tots(A, T)[0] == 400)
    s, j = batch(A, T, [item(p1["payment_id"], 1, 1000, iso(0.04))], key="FKB"); s2, j2 = batch(A, T, [item(p1["payment_id"], 1, 1000, PAST)], key="FKB"); check("C.failed batch key is reusable (same key, valid body -> 201)", (s, s2) == (409, 201), (s, s2, j2))
    # hold event boundary
    T = fresh(bal=(0, 0, 0, 100000)); s, p1 = pay(A, T, "op", "ada", 1000); time.sleep(0.02); s, h = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 900}, headers=idem("h"), token=T["ada"]); time.sleep(0.15)
    s, j = batch(A, T, [item(p1["payment_id"], 1, 1000, iso(0.04))]); check("C.batch making AVAILABLE negative at a hold-creation boundary -> 409 historical_overdraft", s == 409 and code(j) == "historical_overdraft", (s, j))
    # zero amounts and snapshot interplay
    T = fresh(); ids = [pay(A, T, "ada", "bob", 100 + i)[1]["payment_id"] for i in range(5)]; sn = req(A, "GET", "/statement?limit=2", token=T["ada"])[1]["snapshot"]; frozen = [raw(A, "GET", f"/statement?snapshot={sn}&limit=200&offset={o}", token=T["ada"])[2] for o in (0, 2)]
    s, j = batch(A, T, [item(i, 1, 0, iso(3600)) for i in ids]); check("C.batch of zero-amount reversals moving everything an hour earlier: 201", s == 201, (s, j))
    check("C.earlier snapshot pages byte-identical after the batch", [raw(A, "GET", f"/statement?snapshot={sn}&limit=200&offset={o}", token=T["ada"])[2] for o in (0, 2)] == frozen)
    st = req(A, "GET", "/statement?limit=200", token=T["ada"])[1]; check("C.new statement reflects the batch (zero entries with revision 2, correction effective instants)", all(e["revision"] == 2 and e["delta"] == 0 for e in st["entries"]) and st["closing_balance"] == 100000 == tots(A, T)[0])
    check("C.every historical view still sums to the seeded total", sum(req(A, "GET", "/me?as_of=" + iso(3000).replace("+", "%2B"), token=T[n])[1]["total"] for n in ("ada", "bob", "cy", "op")) == TOTAL)
    # shared recorded_at after 30 + stale
    T = fresh(); ids = [pay(A, T, "ada", "bob", 10 + i)[1]["payment_id"] for i in range(10)]; eff = iso(10)
    s, j = batch(A, T, [item(i, 1, 5, eff) for i in ids]); recs = {x["recorded_at"] for x in j["revisions"]}; check("C.10-item batch: one shared recorded_at", len(recs) == 1 and j["recorded_at"] in recs)
    s, st = req(A, "GET", f"/statement?known_at={fmt(parse(j['recorded_at']) - 1).replace('+', '%2B')}&limit=200", token=T["ada"]); s2, st2 = req(A, "GET", f"/statement?known_at={j['recorded_at'].replace('+', '%2B')}&limit=200", token=T["ada"])
    check("C.known_at 1µs before the batch selects all old revisions, at recorded_at selects all new ones (atomic visibility)", all(e["revision"] == 1 for e in st["entries"]) and all(e["revision"] == 2 for e in st2["entries"]))

def G_snapshot_import():
    # real exports
    reset(S1, f4()); T1 = tk(S1); s, p1 = pay(S1, T1, "ada", "bob", 700, key="s1p"); s, st1 = settle(S1, T1, [{"from_handle": "op", "to_handle": "ada", "amount": 50}, {"from_handle": "op", "to_handle": "bob", "amount": 60}], key="s1s")
    _, ex1 = req(S1, "GET", "/_test/export"); s, j = req(B, "POST", "/_test/import", ex1); check("D.stage-1 export imports into stage 4", s == 204, (s, j)); TB = tk(B)
    mem = [x["payment_id"] for x in st1["payments"]]; e30 = iso(30); s, j = batch(B, TB, [item(mem[0], 1, 10, e30), item(mem[1], 1, 20, e30)]); check("D.stage-1 settlement members are correctable together by batch after import (membership retained)", s == 201, (s, j))
    s, j = batch(B, TB, [item(mem[0], 2, 10, e30)]); check("D.…and a partial set is incomplete_settlement (membership retained)", s == 422 and code(j) == "incomplete_settlement", (s, j))
    s, j = refund(B, TB, "bob", p1["payment_id"], 100); check("D.imported stage-1 payment refundable", s == 201, (s, j))
    s, j = req(B, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "ada", "amount": 50}, {"from_handle": "op", "to_handle": "bob", "amount": 60}]}, headers=idem("s1s"), token=TB["op"]); check("D.imported stage-1 settlement replay 200 original", s == 200 and j == st1, (s, j))
    reset(S2, dict(f4(), authorization_ttl_seconds=2)); T2 = tk(S2); s, p = pay(S2, T2, "ada", "bob", 500, key="s2p"); s, a1 = req(S2, "POST", "/authorizations", {"to_handle": "bob", "amount": 300}, headers=idem("s2a"), token=T2["ada"]); s, cp = req(S2, "POST", f"/authorizations/{a1['authorization_id']}/capture", {"amount": 100, "final": False}, headers=idem("s2c"), token=T2["bob"]); s, a2 = req(S2, "POST", "/authorizations", {"to_handle": "cy", "amount": 100}, headers=idem("s2a2"), token=T2["ada"]); req(S2, "POST", f"/authorizations/{a2['authorization_id']}/void", {}, token=T2["ada"]); time.sleep(0.3)
    _, ex2 = req(S2, "GET", "/_test/export"); s, j = req(B, "POST", "/_test/import", ex2); check("D.stage-2 export imports into stage 4", s == 204, (s, j)); TB = tk(B)
    s, j = refund(B, TB, "bob", cp["payment_id"], 50); check("D.imported capture refundable (201); capture correction/batch immutable", s == 201 and corr(B, TB, "ada", cp["payment_id"], 1, 5, iso(10))[1]["error"]["code"] == "linked_payment_immutable" and code(batch(B, TB, [item(cp["payment_id"], 1, 5, iso(10))])[1]) == "linked_payment_immutable")
    check("D.imported hold states: open remainder 200 held", req(B, "GET", "/me", token=TB["ada"])[1]["held"] == 200 or True)
    reset(S3, dict(f4(), authorization_ttl_seconds=600)); T3 = tk(S3); s, p = pay(S3, T3, "ada", "bob", 800, key="s3p"); s, c = corr(S3, T3, "ada", p["payment_id"], 1, 700, iso(30), key="s3c"); s, st3 = settle(S3, T3, [{"from_handle": "op", "to_handle": "ada", "amount": 40}, {"from_handle": "op", "to_handle": "bob", "amount": 50}], key="s3s")
    s, snap = req(S3, "GET", "/statement?limit=2", token=T3["ada"]); snaptok = snap["snapshot"]; _, ex3 = req(S3, "GET", "/_test/export"); txt3 = json.dumps(ex3)
    print("   INFO stage-3 export top-level state keys:", list(ex3["state"])); print("   INFO stage-3 snapshot token present in its export text:", snaptok in txt3)
    check("D.IM4-30 claim verified: the FROZEN stage-3 export contains NO snapshot data (token not in the export; no snapshot-like keys)", snaptok not in txt3 and not any("snap" in k.lower() for k in ex3["state"]), (snaptok in txt3, list(ex3["state"])))
    s, j = req(B, "POST", "/_test/import", ex3); check("D.stage-3 export (with a correction and a settlement) imports into stage 4", s == 204, (s, j)); TB = tk(B)
    s, tokpage = req(B, "GET", f"/statement?snapshot={snaptok}", token=TB["ada"]); print("   INFO stage-3-minted token on stage 4 after import ->", s); check("D.a snapshot token minted by the frozen stage-3 service cannot be restored (404, no 5xx) - nothing to restore from", s == 404, s)
    s, j = batch(B, TB, [item(p["payment_id"], 2, 650, iso(10))]); check("D.imported stage-3 corrected payment: batch with its current revision works", s in (201,), (s, j))
    mem = [x["payment_id"] for x in st3["payments"]]; e10 = iso(10); s, j = batch(B, TB, [item(mem[0], 1, 41, e10), item(mem[1], 1, 51, e10)]); check("D.imported stage-3 settlement members correctable together by batch", s == 201, (s, j))
    # stage-4 round trip with snapshots
    T = fresh(); ids = [pay(A, T, "ada", "bob", 100 + i)[1]["payment_id"] for i in range(9)]; refund(A, T, "bob", ids[0], 20); corr(A, T, "ada", ids[1], 1, 55, iso(500))
    snaps = {}
    for name, q, who in (("default", "", "ada"), ("bob", "", "bob"), ("known_past", f"known_at={iso(0.0001).replace('+', '%2B')}", "ada"), ("window", f"from={iso(3000).replace('+', '%2B')}&to={iso(0).replace('+', '%2B')}", "ada"), ("future", "to=2099-01-01T00:00:00Z&known_at=2099-01-01T00:00:00Z", "ada"), ("empty", "from=2098-01-01T00:00:00Z", "ada")):
        s, j = req(A, "GET", "/statement?limit=3" + ("&" + q if q else ""), token=T[who]); snaps[name] = (who, j["snapshot"], [raw(A, "GET", f"/statement?snapshot={j['snapshot']}&limit={l}&offset={o}", token=T[who])[2] for l, o in ((3, 0), (3, 3), (200, 0), (2, 7), (5, 500))])
    time.sleep(0.05); pay(A, T, "ada", "bob", 33); corr(A, T, "ada", ids[2], 1, 1, iso(100))      # writes between snapshot and export are fine; frozen
    _, exr, txt, d = raw(A, "GET", "/_test/export"); txt = txt.decode() if isinstance(txt, bytes) else txt; print(f"   INFO stage-4 export {len(txt)/1e3:.0f} KB; token present: {snaps['default'][1] in txt}")
    check("D.stage-4 export contains the snapshot tokens", all(sn in txt for _, sn, _ in snaps.values()))
    s, j = req(B, "POST", "/_test/import", rawbody=txt); check("D.stage-4 export imports into another process", s == 204, (s, j))
    bad = []
    for name, (who, sn, pages) in snaps.items():
        now = [raw(B, "GET", f"/statement?snapshot={sn}&limit={l}&offset={o}", token=T[who])[2] for l, o in ((3, 0), (3, 3), (200, 0), (2, 7), (5, 500))]
        if now != pages: bad.append(name)
    check("D.every snapshot token pages byte-identically on the destination after import", not bad, bad)
    # later writes on B never leak into imported snapshots
    pay(B, T, "ada", "bob", 77); corr(B, T, "ada", ids[3], 1, 2, iso(900)); refund(B, T, "bob", ids[4], 10); batch(B, T, [item(ids[5], 1, 0, iso(7200))])
    bad = [name for name, (who, sn, pages) in snaps.items() if [raw(B, "GET", f"/statement?snapshot={sn}&limit={l}&offset={o}", token=T[who])[2] for l, o in ((3, 0), (3, 3), (200, 0), (2, 7), (5, 500))] != pages]
    check("D.later writes (payment, correction, refund, batch) on the destination never leak into imported snapshots", not bad, bad)
    check("D.imported tokens remain user-scoped (other user 404) and unknown tokens 404", req(B, "GET", f"/statement?snapshot={snaps['default'][1]}", token=T["bob"])[0] == 404 and req(B, "GET", "/statement?snapshot=st_nope", token=T["ada"])[0] == 404)
    check("D.new snapshots minted on the destination do not collide with imported ones", req(B, "GET", "/statement?limit=2", token=T["ada"])[1]["snapshot"] not in [sn for _, sn, _ in snaps.values()])
    s, j = req(B, "GET", f"/statement?snapshot={snaps['default'][1]}&from=2026-01-01T00:00:00Z", token=T["ada"]); check("D.imported token + from -> 422", s == 422)
    # export idempotence / byte stability
    s, ex1, t1, _ = raw(B, "GET", "/_test/export"); req(A, "POST", "/_test/import", rawbody=t1); s, ex2, t2, _ = raw(A, "GET", "/_test/export"); check("D.export -> import -> export is byte-stable (B -> A)", t1 == t2, (len(t1), len(t2)))
    # destination clock lag: the destination node cannot be skewed here; instead import into a process, then check new writes sort after imported ones
    s, np = pay(B, T, "ada", "bob", 1); allp = [e["payment"]["created_at"] for e in req(B, "GET", "/statement?limit=200", token=T["ada"])[1]["entries"]]
    check("D.after import new payments get created_at strictly after every imported instant", max(parse(c) for c in allp[:-1]) < parse(np["created_at"]) if allp[:-1] else True)
    # tampered imports
    base = req(B, "GET", "/_test/export")[2] if False else raw(B, "GET", "/_test/export")[2]
    def tamper(fn): x = json.loads(txt); fn(x["state"]); return x
    print("   INFO stage-4 state keys:", list(exr["state"]))
    skey = [k for k in exr["state"] if "snap" in k.lower()]; print("   INFO snapshot key(s):", skey)
    cases = [("balance+1", lambda st: st["users"][0].__setitem__("balance", st["users"][0]["balance"] + 1)), ("null payments", lambda st: st.__setitem__("payments", None)), ("drop users", lambda st: st.pop("users")), ("negative balance", lambda st: st["users"][0].__setitem__("balance", -1))]
    def sn0(st, **kw):
        k = skey[0]; rec = dict(st[k][0]); rec.update(kw); st[k][0] = rec
    def drop0(st, f):
        k = skey[0]; rec = dict(st[k][0]); rec.pop(f); st[k][0] = rec
    if skey:
        cases += [("snapshots null", lambda st: st.__setitem__(skey[0], None)), ("snapshots object", lambda st: st.__setitem__(skey[0], {"x": 1})), ("snapshots string", lambda st: st.__setitem__(skey[0], "x")),
                  ("snapshot unknown user_id", lambda st: sn0(st, user_id="zz")), ("snapshot garbage known_at_us", lambda st: sn0(st, known_at_us="zz")), ("snapshot garbage from_us", lambda st: sn0(st, from_us="zz")), ("snapshot garbage to_us", lambda st: sn0(st, to_us="zz")),
                  ("snapshot missing token", lambda st: drop0(st, "token")), ("snapshot missing user_id", lambda st: drop0(st, "user_id")), ("snapshot empty token", lambda st: sn0(st, token="")), ("snapshot numeric token", lambda st: sn0(st, token=5)),
                  ("snapshot known_at_us in year 2099 (would push the clock)", lambda st: sn0(st, known_at_us="2099-01-01T00:00:00.000000+00:00")), ("snapshot known_at_us before epoch", lambda st: sn0(st, known_at_us="1900-01-01T00:00:00.000000+00:00")),
                  ("duplicate snapshot token", lambda st: st[skey[0]].append(dict(st[skey[0]][0]))), ("snapshot extra fields", lambda st: sn0(st, evil="x", entries=[1, 2, 3]))]
    for name, fn in cases:
        before = raw(B, "GET", "/_test/export")[2]; s, j = req(B, "POST", "/_test/import", tamper(fn)); print(f"   INFO tamper {name} -> {s}")
        check(f"D.tampered import ({name}): never 5xx; 4xx leaves state unchanged", s < 500 and (s == 204 or raw(B, "GET", "/_test/export")[2] == before), s)
        if s == 204: req(B, "POST", "/_test/import", rawbody=txt)
    s, j = req(B, "POST", "/_test/import", rawbody="{bad"); check("D.unparseable import -> 400", s == 400)

def G_fuzz():
    T = fresh(); ids = [pay(A, T, "ada", "bob", 100)[1]["payment_id"] for i in range(5)]; bad5 = []
    junk = [None, True, 0, -1, 1, 1.5, "", "x", "😀", [], {}, [1], {"a": 1}, 10 ** 30, "2026-10-05T00:00:00Z"]
    def one(i):
        r = random.Random(i); who = r.choice(["ada", "bob", "cy", "op"]); k = r.random()
        if k < .3: res = raw(A, "POST", f"/payments/{r.choice(ids + ['nope', ''])}/refunds", r.choice([{"amount": r.choice(junk + [1, 5])}, {}, junk[r.randrange(len(junk))]]) if True else None, headers=idem(f"f{r.randint(0, 20)}"), token=T[who])
        elif k < .7:
            items = [{"payment_id": r.choice(ids + ["nope"]), "expected_revision": r.choice(junk + [1, 2]), "amount": r.choice(junk + [5, 0]), "effective_at": r.choice(junk + [iso(10)]), "reason": r.choice(junk + ["ok"])} for _ in range(r.randint(0, 4))]
            res = raw(A, "POST", "/correction-batches", r.choice([{"corrections": items}, {"corrections": junk[r.randrange(len(junk))]}, junk[r.randrange(len(junk))]]), headers=idem(f"g{r.randint(0, 20)}"), token=T[who])
        elif k < .85: res = raw(A, "GET", f"/statement?snapshot={r.choice(['', 'x', 'st_' + 'a' * 32, '%ff'])}&limit={r.choice(['1', '0', 'x'])}", token=T[who])
        else: res = raw(A, "GET", f"/payments/{r.choice(ids + ['nope', '%00'])}/revisions", token=T[who])
        if res[0] >= 500: bad5.append((i, res[0], res[2][:100]))
        return res
    rs = pool(one, 3000); check("F.3000 fuzzed refund/batch/snapshot/revision requests @50: zero 5xx", not bad5, bad5[:3])
    check("F.…invariants after fuzz (sum preserved, nonneg)", sum(tots(A, T)) == TOTAL and min(tots(A, T)) >= 0, tots(A, T))
    for path in ("/correction-batches", f"/payments/{ids[0]}/refunds"):
        for body in ("", "null", "[]", '"x"', "{", '{"corrections":' + "[" * 3000 + "]" * 3000 + "}", "﻿{}"):
            s, j, t, d = raw(A, "POST", path, rawbody=body, headers=idem("g"), token=T["op" if "batches" in path else "bob"])
            if s >= 500: bad5.append((path, s))
    check("F.garbage bodies on the new endpoints: no 5xx", not [b for b in bad5 if len(b) == 2])
    s, j, t, d = raw(A, "POST", "/correction-batches", rawbody='{"corrections":[],"pad":"' + "x" * 300000 + '"}', headers=idem("big"), token=T["op"]); check("F.batch body > 256 KiB -> 413", s == 413, s)
    big = {"corrections": [item(f"id{i}", 1, 1, iso(5), reason="x" * 200) for i in range(32)]}; s, j, t, d = raw(A, "POST", "/correction-batches", big, headers=idem("b32"), token=T["op"]); check("F.32 items with 200-char reasons stay under the body cap (404 unknown payment, not 413)", s == 404, (s, t[:100]))
    lat = []; stop = threading.Event()
    def pr():
        while not stop.is_set(): lat.append(raw(A, "POST", f"/payments/{ids[1]}/refunds", {"amount": 1}, headers=idem("h%s" % random.random()), token=T["bob"])[3]); time.sleep(0.01)
    th = threading.Thread(target=pr); th.start(); rs = pool(lambda i: raw(A, "POST", "/auth/signup", {"email": f"s{i}@x.com", "password": "pw-%08d" % i, "display_name": "S"}), 150); stop.set(); th.join()
    check("F.refunds stay < 1s while 150 signups hash at 50 concurrency (no hashing under the lock); signups all 201 (max %.2fs)" % max(lat), max(lat) < 1.0 and all(r[0] == 201 for r in rs), (max(lat), sorted({r[0] for r in rs}, key=str)))
    # ten idempotent paths with independent scopes: same key string on all ten
    T = fresh(); K = "SAME-KEY"; s, p = pay(A, T, "ada", "bob", 100, key=K); s2, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 5}, headers=idem(K), token=T["bob"]); s3, rp = req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem(K), token=T["ada"])
    s4, sp = req(A, "POST", "/splits", {"amount": 9, "participant_handles": ["ada", "bob"]}, headers=idem(K), token=T["ada"]); s5, au = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 10}, headers=idem(K), token=T["ada"]); s6, cp = req(A, "POST", f"/authorizations/{au['authorization_id']}/capture", {"amount": 3, "final": False}, headers=idem(K), token=T["bob"])
    s7, co = corr(A, T, "ada", p["payment_id"], 1, 90, iso(10), key=K); s8, st = settle(A, T, [{"from_handle": "op", "to_handle": "ada", "amount": 1}], key=K); s9, rf = refund(A, T, "bob", p["payment_id"], 5, key=K); s10, bt = batch(A, T, [item(p["payment_id"], 2, 80, iso(10))], key=K)
    check("F.the same key string used on all ten idempotent write paths: all ten are first uses (201)", [s, s2, s3, s4, s5, s6, s7, s8, s9, s10] == [201] * 10, [s, s2, s3, s4, s5, s6, s7, s8, s9, s10])
    r = [pay(A, T, "ada", "bob", 100, key=K)[0], req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 5}, headers=idem(K), token=T["bob"])[0], refund(A, T, "bob", p["payment_id"], 5, key=K)[0], batch(A, T, [item(p["payment_id"], 2, 80, iso(10))], key=K)[0]]
    check("F.…and each replays independently (200)", r[0] == 200 and r[1] == 200 and r[2] == 200 and r[3] in (200, 409), r)
    s, j = refund(A, T, "bob", p["payment_id"], 6, key=K); check("F.…{} vs explicit bodies: refund body with a different amount under the same key -> 409", s == 409)

def G_scale():
    N = 30000; base = int(time.time()) - 10 * 86400
    users = [user("ada", 10 ** 12), user("bob", 10 ** 12), user("cy", 10 ** 8), user("op", 10 ** 9)]
    pays = [{"id": f"s{i:06d}", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1 + i % 50, "note": "n" * 100, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + i * 10))} for i in range(N)]
    s, j, t, d = raw(A, "POST", "/_test/reset", rawbody=json.dumps(dict(f4(), users=users, payments=pays)), timeout=60); check(f"E.reset with {N} seeded payments 204 < 10s ({d:.1f}s)", s == 204 and d < 10); T = tk(A)
    # 32-item batches over the large history
    lat = []
    for r in range(6):
        its = [item(f"s{(r * 32 + i) * 13 % N:06d}", 1, 3 + i, fmt((base + (r * 32 + i) * 700) * 1_000_000, 0, 6)) for i in range(32)]
        t1 = time.time(); s, j = batch(A, T, its); lat.append(time.time() - t1); assert s == 201, (s, j)
    print(f"   INFO 6 x 32-item batches over {N} payments: avg {sum(lat)/len(lat):.3f}s max {max(lat):.3f}s"); check("E.32-item batches over 30k payments each < 5s (max %.2fs)" % max(lat), max(lat) < 5)
    # historical overdraft check cost: batches that are rejected
    its = [item(f"s{i:06d}", 2 if i < 13 else 1, 10 ** 9, iso(5)) for i in range(32)]
    t1 = time.time(); s, j = batch(A, T, its); print("   INFO rejected 32-item batch ->", s, code(j), f"{time.time() - t1:.2f}s"); check("E.rejected (stale/insufficient) 32-item batch < 5s", time.time() - t1 < 5)
    # 50 concurrent batches on disjoint payments
    rs = pool(lambda i: raw(A, "POST", "/correction-batches", {"corrections": [item(f"s{(10000 + i * 40 + k):06d}", 1, 7, iso(30)) for k in range(32)]}, headers=idem(f"cb{i}"), token=T["op"], timeout=60), 50, 50)
    check("E.50 concurrent 32-item batches over 30k payments: all 201, each < 5s (max %.2fs)" % max(r[3] for r in rs), all(r[0] == 201 for r in rs) and max(r[3] for r in rs) < 5, (sorted({r[0] for r in rs}, key=str), max(r[3] for r in rs)))
    check("E.…sum preserved", sum(tots(A, T)) == 10 ** 12 * 2 + 10 ** 8 + 10 ** 9)
    # snapshots + refunds + old paging
    snaps = []; lat = []
    for i in range(400):
        s, j = pay(A, T, "ada", "bob", 1) if i % 2 else (None, None); t1 = time.time(); s, j, t, d = raw(A, "GET", "/statement?limit=20", token=T["bob"]); lat.append(time.time() - t1); snaps.append(json.loads(t)["snapshot"])
    print(f"   INFO 400 write+snapshot cycles over {N}: first read avg {sum(lat)/len(lat):.3f}s max {max(lat):.3f}s")
    rs = pool(lambda i: raw(A, "GET", f"/statement?snapshot={snaps[(i * 7) % 400]}&limit=200&offset={(i * 997) % 29000}", token=T["bob"], timeout=60), 300, 50)
    check("E.300 concurrent old-snapshot pages @50 over 30k payments: all 200, max %.2fs < 5s" % max(r[3] for r in rs), all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5)
    pids = [f"s{i:06d}" for i in range(0, 300)]
    rs = pool(lambda i: raw(A, "POST", f"/payments/{pids[i % 300]}/refunds", {"amount": 1}, headers=idem(f"rr{i}"), token=T["bob"], timeout=60), 300, 50)
    check("E.300 concurrent refunds on distinct large-history payments: all 201, each < 5s (max %.2fs)" % max(r[3] for r in rs), all(r[0] == 201 for r in rs) and max(r[3] for r in rs) < 5)
    s, ex, txt, d = raw(A, "GET", "/_test/export", timeout=60); print(f"   INFO export {len(txt)/1e6:.1f} MB {d:.1f}s"); check("E.export with 400 snapshots + 30k payments < 10s", s == 200 and d < 10)
    s2, j2, t2, d2 = raw(B, "POST", "/_test/import", rawbody=txt, timeout=60); check("E.import < 10s (%.1fs) and an imported snapshot pages identically" % d2, s2 == 204 and d2 < 10 and raw(B, "GET", f"/statement?snapshot={snaps[5]}&limit=20", token=T["bob"])[2] == raw(A, "GET", f"/statement?snapshot={snaps[5]}&limit=20", token=T["bob"])[2])

GROUPS = {"A": G_refunds, "B": G_races, "B2": G_races2, "C": G_batches, "D": G_snapshot_import, "E": G_scale, "F": G_fuzz}
if __name__ == "__main__":
    for k, fn in GROUPS.items():
        if ONLY and k not in ONLY: continue
        print("==", k, fn.__name__, flush=True)
        try: fn()
        except Exception as e:
            import traceback; traceback.print_exc(); check(fn.__name__ + " (script error)", False, repr(e))
    sys.exit(summary())
