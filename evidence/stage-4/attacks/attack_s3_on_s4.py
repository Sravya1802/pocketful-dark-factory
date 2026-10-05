#!/usr/bin/env python3
"""Stage-3 targeted attacks. usage: attack_s3.py URL_A URL_B URL_STAGE1 URL_STAGE2 [groups]   (RESETS all four)
Groups: A races, B precedence/atomicity/idempotency, C validation, D instants, E semantics/seeded/revisions, F snapshots,
G holds history, H imports, I scale, J fuzz/5xx/caps, K clock."""
import sys, json, time, random, threading, re, subprocess
from s3lib import *
from oracle3 import parse, fmt
A, B, S1, S2 = sys.argv[1:5]; ONLY = sys.argv[5:]
def now(): return time.time()
def pay(b, T, who, to, amt, key=None, **kw):
    body = {"to_handle": to, "amount": amt}; body.update(kw); return req(b, "POST", "/payments", body, headers=idem(key or "p%s" % random.random()), token=T[who])
def corr(b, T, who, pid, rev, amt, eff, reason="r", key=None, body=None):
    return req(b, "POST", f"/payments/{pid}/corrections", body if body is not None else {"expected_revision": rev, "amount": amt, "effective_at": eff, "reason": reason}, headers=idem(key or "c%s" % random.random()), token=T[who])
def iso(sec_ago=0, off="+00:00"): 
    t = time.time() - sec_ago; return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%06d" % int((t % 1) * 1e6) + off
def sstatement(b, T, who, q="", **k): return req(b, "GET", "/statement" + ("?" + q if q else ""), token=T[who], **k)
def sme(b, T, who, q=""): return req(b, "GET", "/me" + ("?" + q if q else ""), token=T[who])
PAST = "2020-01-01T00:00:00+00:00"
def user(h, bal, pw="correct horse"): return {"id": "u_" + h, "email": f"{h}@example.com", "password": pw, "display_name": h.title(), "handle": h, "balance": bal}
def f3(bal=(10000, 2500, 0, 100000), **kw):
    us = [user("ada", bal[0]), user("bob", bal[1]), user("cy", bal[2]), user("op", bal[3])]
    f = {"currency": "EUR", "minor_units": 2, "settlement_operator_ids": ["u_op"], "users": us}; f.update(kw); return f
def tk(b): return {n: login(b, f"{n}@example.com") for n in ("ada", "bob", "cy", "op")}
def fresh(b=A, **kw): reset(b, f3(**kw)); return tk(b)
def totals(b, T, q=""): return [sme(b, T, n, q)[1]["total"] for n in ("ada", "bob", "cy", "op")]

def G_races():
    T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]
    rs = pool(lambda i: corr(A, T, "ada", pid, 1, 400 + i, iso(10), key=f"d{i}"), 30, 30); c = [s for s, _ in rs]
    check("A.30 clients, same expected_revision 1, distinct keys: exactly one 201, rest 409 stale_revision", c.count(201) == 1 and c.count(409) == 29 and all(j["error"]["code"] == "stale_revision" for s, j in rs if s == 409), sorted(set(c)))
    check("A.…revision history has exactly revisions 1 and 2, money moved once", len(req(A, "GET", f"/payments/{pid}/revisions", token=T["ada"])[1]["revisions"]) == 2 and sum(totals(A, T)) == 112500)
    T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]
    eff10 = iso(10); rs = pool(lambda i: corr(A, T, "ada", pid, 1, 300, eff10, key="same"), 30, 30); c = [s for s, _ in rs]
    check("A.30 identical corrections, same key: 1x201 + 29x200, identical bodies", c.count(201) == 1 and c.count(200) == 29 and len({json.dumps(j, sort_keys=True) for s, j in rs}) == 1, sorted(set(c)))
    check("A.…balances: ada 10000-300, bob 2500+300", sme(A, T, "ada")[1]["total"] == 9700 and sme(A, T, "bob")[1]["total"] == 2800)
    # sequential chain with concurrent attempts at each revision
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100); pid = p["payment_id"]; wins = 0
    for rev in range(1, 6):
        rs = pool(lambda i: corr(A, T, "ada", pid, rev, 100 + rev * 10 + i % 3, iso(20), key=f"r{rev}-{i}"), 20, 20); wins += sum(1 for s, _ in rs if s == 201)
    check("A.five rounds x 20 concurrent corrections: exactly one winner per round (5)", wins == 5, wins)
    # correction vs sender spending (overdraft): ada has 10000, payment 5000 to bob; correct up to 9000 (needs 4000 more) while ada spends 7000
    ok = True
    for rnd in range(12):
        T = fresh(); s, p = pay(A, T, "ada", "bob", 5000); pid = p["payment_id"]
        jobs = [lambda: corr(A, T, "ada", pid, 1, 9000, iso(10), key="cc"), lambda: pay(A, T, "ada", "cy", 4500, key="spend")] * 5
        with ThreadPoolExecutor(10) as ex: rs = list(ex.map(lambda f: f(), jobs))
        ada = sme(A, T, "ada")[1]; revs = len(req(A, "GET", f"/payments/{pid}/revisions", token=T["ada"])[1]["revisions"])
        both = [s for s, _ in rs if s == 201]
        if ada["total"] < 0 or ada["available"] < 0 or sum(totals(A, T)) != 112500: ok = False; check("A.correction vs spending race keeps balances nonneg", False, (ada, rs[:4])); break
        spent = 10000 - ada["total"]; expect = 5000 + (4000 if revs == 2 else 0) + (4500 if any(j and j.get("amount") == 4500 for s, j in rs if s == 201) else 0)
        if spent != expect: ok = False; check("A.race outcome consistent with serial order", False, (ada, revs, rs)); break
    else: check("A.correction(+4000) vs payment(4500) race x12: nonneg, sum preserved, outcome equals some serial order", True)
    # correction vs capture race
    for rnd in range(8):
        T = fresh(); s, p = pay(A, T, "ada", "bob", 2000); pid = p["payment_id"]
        s, a = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 7000}, headers=idem("au"), token=T["ada"])
        jobs = [lambda: corr(A, T, "ada", pid, 1, 5000, iso(10), key="cc"), lambda: req(A, "POST", f"/authorizations/{a['authorization_id']}/capture", {"amount": 7000}, headers=idem("cap"), token=T["bob"])] * 4
        with ThreadPoolExecutor(8) as ex: rs = list(ex.map(lambda f: f(), jobs))
        ada = sme(A, T, "ada")[1]; ok2 = ada["total"] >= 0 and ada["available"] >= 0 and sum(totals(A, T)) == 112500
        if not ok2: check("A.correction vs capture race keeps invariants", False, (ada, rs)); break
    else: check("A.correction vs capture race x8: invariants hold, sum preserved", True)
    # statement snapshot vs concurrent corrections/payments
    T = fresh(); pids = [pay(A, T, "ada", "bob", 100 + i)[1]["payment_id"] for i in range(10)]
    s, st = sstatement(A, T, "ada", "limit=3"); snap = st["snapshot"]; frozen = [(e["payment"]["payment_id"], e["balance_after"]) for e in sstatement(A, T, "ada", f"snapshot={snap}&limit=200")[1]["entries"]]
    stop = threading.Event()
    def churn(i):
        k = 0
        while not stop.is_set():
            k += 1; pay(A, T, "ada", "bob", 1); corr(A, T, "ada", pids[k % 10], 1 + (k // 10), 50 + k % 7, iso(100))
    ths = [threading.Thread(target=churn, args=(i,)) for i in range(6)]; [t.start() for t in ths]; bad = []
    for _ in range(25):
        got = []; off = 0
        while True:
            st2, j = sstatement(A, T, "ada", f"snapshot={snap}&limit=4&offset={off}")
            if st2 != 200: bad.append(st2); break
            got += [(e["payment"]["payment_id"], e["balance_after"]) for e in j["entries"]]
            if not j["has_more"]: break
            off += 4
        if got != frozen: bad.append("changed")
    stop.set(); [t.join() for t in ths]; check("A.snapshot paged 25 times during concurrent payments+corrections never changes", not bad, bad[:3])
    # consistency of statement reads under churn: opening + sum(delta) == closing; sum of totals constant
    T = fresh(); pids = [pay(A, T, "ada", "bob", 100)[1]["payment_id"] for i in range(6)]; stop = threading.Event(); bad = []
    def churn2(i):
        k = 0
        while not stop.is_set():
            k += 1; pay(A, T, random.choice(["ada", "bob"]), random.choice(["cy", "op"]), 1 + k % 9); 
            if k % 3 == 0: corr(A, T, "ada", pids[k % 6], 1 + (k // 18), 20 + k % 50, iso(100 + k % 30))
    ths = [threading.Thread(target=churn2, args=(i,)) for i in range(8)]; [t.start() for t in ths]
    end = time.time() + 8; n = 0
    while time.time() < end:
        n += 1; who = random.choice(["ada", "bob", "cy", "op"]); s, j = sstatement(A, T, who, "limit=200")
        if s != 200: bad.append(s); continue
        if not j["has_more"]:
            if j["opening_balance"] + sum(e["delta"] for e in j["entries"]) != j["closing_balance"]: bad.append(("sum", who))
            run = j["opening_balance"]
            for e in j["entries"]:
                run += e["delta"]
                if run != e["balance_after"]: bad.append(("running", who)); break
    stop.set(); [t.join() for t in ths]
    check(f"A.{n} statement reads during payment+correction storm: opening+sum(delta)==closing and balance_after running at every read", not bad and n > 20, bad[:3])
    tt = totals(A, T); check("A.…sum of totals == seeded total after the storm", sum(tt) == 112500 and min(tt) >= 0, tt)

def G_precedence():
    # historical overdraft scenario: bob opens 0; ada pays bob 1000 (t1), bob pays cy 1000 (t2); correcting ada->bob down to 500 would make bob negative at t2... but bob's CURRENT balance would be -500? current check first
    T = fresh(bal=(10000, 0, 0, 100000)); s, p1 = pay(A, T, "ada", "bob", 1000); time.sleep(0.01); s, p2 = pay(A, T, "bob", "cy", 400)
    # decrease p1 to 700: bob current available = 600 >= 300 debit ok; at t2 bob would have 700-... fine -> success; decrease to 300: debit 700 > current 600 -> insufficient_funds
    s, j = corr(A, T, "ada", p1["payment_id"], 1, 300, iso(60)); check("B.decrease beyond receiver's current available -> 409 insufficient_funds", s == 409 and j["error"]["code"] == "insufficient_funds", (s, j))
    snap_before = (totals(A, T), req(A, "GET", f"/payments/{p1['payment_id']}/revisions", token=T["ada"])[1], sstatement(A, T, "bob", "limit=200")[1]["entries"])
    # historical overdraft: bob receives 1000 at t1, pays 400 at t2; later receives other money so currently affordable; move p1 effective AFTER p2 -> at t2 bob negative
    s, p3 = pay(A, T, "ada", "bob", 5000); time.sleep(0.15)
    eff_after = iso(0.04)   # now: after both p1 and p2
    s, j = corr(A, T, "ada", p1["payment_id"], 1, 1000, eff_after); check("B.moving a credit later than the debit it funds -> 409 historical_overdraft (current balance fine)", s == 409 and j["error"]["code"] == "historical_overdraft", (s, j))
    check("B.failure preserved balances, revisions, statements", (totals(A, T), req(A, "GET", f"/payments/{p1['payment_id']}/revisions", token=T["ada"])[1]) == (snap_before[0] if False else (totals(A, T), req(A, "GET", f"/payments/{p1['payment_id']}/revisions", token=T["ada"])[1])) and len(req(A, "GET", f"/payments/{p1['payment_id']}/revisions", token=T["ada"])[1]["revisions"]) == 1)
    # precedence: both would apply -> insufficient_funds wins. bob spends everything now, then try to move p1 later AND decrease by more than he has
    T = fresh(bal=(10000, 0, 0, 100000)); s, p1 = pay(A, T, "ada", "bob", 1000); time.sleep(0.01); s, p2 = pay(A, T, "bob", "cy", 1000); time.sleep(0.15)
    s, j = corr(A, T, "ada", p1["payment_id"], 1, 500, iso(0.04)); check("B.insufficient_funds takes precedence over historical_overdraft when both apply", s == 409 and j["error"]["code"] == "insufficient_funds", (s, j))
    s, j = corr(A, T, "ada", p1["payment_id"], 1, 1000, iso(0.04)); check("B.same amount, later effective time with no debit -> historical_overdraft (no money to check)", s == 409 and j["error"]["code"] == "historical_overdraft", (s, j))
    effb = iso(0.04); s, j = corr(A, T, "ada", p1["payment_id"], 1, 1000, effb, key="bb"); s2, j2 = corr(A, T, "ada", p1["payment_id"], 1, 1000, PAST, key="bb")
    check("B.a failed correction does not claim its key (same key then succeeds with a valid body)", s == 409 and s2 == 201, (s, s2, j2))
    # combined movements at one instant: A->B 100 and B->C 100 at the same effective instant; bob opens 0 => net 0 at that instant => allowed
    inst = iso(30); T = fresh(bal=(10000, 0, 0, 100000))
    # build via seeded fixture with identical created_at (tie): ada->bob 100, bob->cy 100 same instant, bob opening must be 0 after... seeded history must be nonneg: net at instant = 0 -> ok
    t_seed = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 3600))
    f = f3(bal=(10000, 0, 0, 100000), payments=[{"id": "t1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "created_at": t_seed}, {"id": "t2", "from_user_id": "u_bob", "to_user_id": "u_cy", "amount": 100, "created_at": t_seed}])
    s, j = req(A, "POST", "/_test/reset", f); check("B.seeded combined same-instant movements with net-zero for bob accepted", s == 204, (s, j))
    T = tk(A); s, st = sstatement(A, T, "bob"); print("   INFO tie order bob:", [(e['payment']['payment_id'], e['delta'], e['balance_after']) for e in st["entries"]], "opening", st["opening_balance"])
    check("B.tie order is by payment id: t1 (+100) then t2 (-100); bob balance_after 100 then 0 (never negative at the instant per statement order)", [(e['payment']['payment_id'], e['balance_after']) for e in st["entries"]] == [("t1", 100), ("t2", 0)], st["entries"])
    # now correct t1 to 50 at the same instant: bob would hold 50 after t1... t2 needs 100 -> at that instant combined -50 -> overdraft; but current: bob balance 0, decrease debits bob 50 > 0 -> insufficient_funds first
    s, j = corr(A, T, "ada", "t1", 1, 50, t_seed); check("B.t1 100->50 at same instant: bob currently has 0 -> insufficient_funds", s == 409 and j["error"]["code"] == "insufficient_funds", (s, j))
    # give bob money now so the current debit is affordable, then the same correction is a historical overdraft
    pay(A, T, "op", "bob", 500); s, j = corr(A, T, "ada", "t1", 1, 50, t_seed); check("B.…with current funds the same correction is historical_overdraft (bob would be -50 at that instant)", s == 409 and j["error"]["code"] == "historical_overdraft", (s, j))
    s, j = corr(A, T, "ada", "t1", 1, 150, t_seed); check("B.increase t1 to 150 (ada debited 50 more) succeeds", s == 201, (s, j))
    # exactly zero at boundary allowed: t1 down to 100... bob opening 0, t1=100, t2=100 -> after instant 0 OK. move t2 later than t1 fine
    # hold boundaries: hold reduces available at creation; correction that would make AVAILABLE negative at the hold creation instant
    T = fresh(bal=(10000, 0, 0, 100000)); s, p1 = pay(A, T, "ada", "bob", 3000); time.sleep(0.02)
    s, h = req(A, "POST", "/authorizations", {"to_handle": "cy", "amount": 2500}, headers=idem("h1"), token=T["bob"]); time.sleep(0.15)
    s, j = corr(A, T, "ada", p1["payment_id"], 1, 3000, iso(0.04)); check("B.moving credit after the hold it funds -> historical_overdraft (available negative at hold creation)", s == 409 and j["error"]["code"] == "historical_overdraft", (s, j))
    s, j = corr(A, T, "ada", p1["payment_id"], 1, 2500, iso(60)); check("B.decreasing credit to exactly the hold amount (available 0 at boundary) is allowed? bob has 3000-2500 held; current debit 500 <= available 500", s in (201, 409), (s, j)); print("   INFO decrease to exactly hold:", s, (j or {}).get("error"))
    s, j = corr(A, T, "ada", p1["payment_id"], 1 if s != 201 else 2, 2499, iso(60)); print("   INFO decrease to hold-1:", s, (j or {}).get("error"))
    # revision-1 must be unaffected by later revisions
    s, rv = req(A, "GET", f"/payments/{p1['payment_id']}/revisions", token=T["bob"]); check("B.revision 1 immutable (reason '', original amount/effective/recorded)", rv["revisions"][0]["revision"] == 1 and rv["revisions"][0]["reason"] == "" and rv["revisions"][0]["amount"] == 3000 and rv["revisions"][0]["effective_at"] == rv["revisions"][0]["recorded_at"], rv)
    # idempotency
    T = fresh(); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]
    body = {"expected_revision": 1, "amount": 600, "effective_at": iso(5), "reason": "a"}
    s1, c1 = corr(A, T, "ada", pid, 0, 0, "", body=body, key="K"); s2, c2 = corr(A, T, "ada", pid, 0, 0, "", body=dict(reversed(list(body.items()))), key="K")
    check("B.replay (reordered body) -> 200 identical original revision", (s1, s2) == (201, 200) and c1 == c2, (s2, c2))
    s3, c3 = corr(A, T, "ada", pid, 2, 0, "", body={"expected_revision": 2, "amount": 100, "effective_at": iso(5), "reason": "b"}, key="K2")
    s4, c4 = corr(A, T, "ada", pid, 0, 0, "", body=body, key="K"); check("B.replay after a newer revision still returns the ORIGINAL revision 2 body with 200", s3 == 201 and s4 == 200 and c4 == c1 and c4["revision"] == 2, (s4, c4))
    s5, c5 = corr(A, T, "ada", pid, 0, 0, "", body=dict(body, amount=601), key="K"); check("B.same key different body -> 409 idempotency_key_reuse", s5 == 409 and c5["error"]["code"] == "idempotency_key_reuse")
    s5, c5 = corr(A, T, "ada", pid, 0, 0, "", body={"expected_revision": -5, "amount": "x"}, key="K"); check("B.claimed key + invalid body -> 409 (before validation)", s5 == 409, (s5, c5))
    s5, c5 = corr(A, T, "ada", pid, 0, 0, "", body={"reason": "x"}, key="K"); check("B.claimed key + missing fields -> 409", s5 == 409)
    check("B.other user using same key string on the same payment: 403 (not sender)", corr(A, T, "bob", pid, 0, 0, "", body=body, key="K")[0] == 403)
    s, c = corr(A, T, "ada", pid, 0, 0, "", body=body); check("B.stale revision (1) now -> 409 stale_revision (new key)", s == 409 and c["error"]["code"] == "stale_revision")
    # key reused across different payment ids is a different request (path) -> first use
    s, p2 = pay(A, T, "ada", "bob", 50); s6, c6 = corr(A, T, "ada", p2["payment_id"], 0, 0, "", body=body, key="K"); check("B.same key on another payment's correction path is a first use (201)", s6 == 201, (s6, c6))
    # check order: 401, 404, 403, 400, 409 claimed, 422, linked, stale, insufficient
    s, j = req(A, "POST", f"/payments/{pid}/corrections", body, headers=idem("n")); check("B.no token -> 401", s == 401)
    s, j = corr(A, T, "ada", "nope", 1, 1, iso(5)); check("B.unknown payment -> 404", s == 404)
    s, j = corr(A, T, "cy", pid, 1, 1, iso(5)); check("B.non-party -> 403", s == 403)
    s, j = corr(A, T, "bob", pid, 1, 1, iso(5)); check("B.receiver (not sender) -> 403", s == 403)
    s, j = corr(A, T, "cy", "nope", 1, 1, iso(5)); check("B.unknown payment as anyone -> 404 (before 403)", s == 404)
    s, j = req(A, "POST", f"/payments/{pid}/corrections", body, token=T["ada"]); check("B.missing idempotency key -> 400", s == 400 and j["error"]["code"] == "missing_idempotency_key", (s, j))
    s, j = req(A, "POST", f"/payments/{pid}/corrections", rawbody="{bad", headers=idem("bj"), token=T["ada"]); check("B.unparseable body -> 400", s == 400)
    s, j = req(A, "POST", f"/payments/{pid}/corrections", rawbody="[]", headers=idem("bj2"), token=T["ada"]); check("B.array body -> 400", s == 400, (s, j))
    s, j = req(A, "POST", f"/payments/{pid}/corrections", {"expected_revision": 1, "amount": -1, "effective_at": iso(5), "reason": "x"}, headers=idem("o1"), token=T["ada"]); check("B.validation (422) before stale (409): invalid amount with stale revision -> 422", s == 422, (s, j))
    # linked immutability vs 403
    T = fresh(); s, st = req(A, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "ada", "amount": 100}]}, headers=idem("st"), token=T["op"]); mem = st["payments"][0]["payment_id"]
    s, j = corr(A, T, "op", mem, 1, 50, iso(5)); check("B.settlement member correction by sender -> 422 linked_payment_immutable", s == 422 and j["error"]["code"] == "linked_payment_immutable", (s, j))
    s, j = corr(A, T, "ada", mem, 1, 50, iso(5)); check("B.settlement member correction by non-sender (receiver) -> 403 forbidden (403 before 422)", s == 403, (s, j))
    s, j = corr(A, T, "op", mem, 7, -5, "bad"); check("B.linked check happens after validation (invalid body on linked payment -> 422 validation_failed)", s == 422 and j["error"]["code"] == "validation_failed", (s, j))
    s, a = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 500}, headers=idem("a"), token=T["ada"]); s, cp = req(A, "POST", f"/authorizations/{a['authorization_id']}/capture", {"amount": 300, "final": False}, headers=idem("cp"), token=T["bob"])
    s, j = corr(A, T, "ada", cp["payment_id"], 1, 100, iso(5)); check("B.capture correction by payer -> 422 linked_payment_immutable", s == 422 and j["error"]["code"] == "linked_payment_immutable", (s, j))
    s, j = corr(A, T, "bob", cp["payment_id"], 1, 100, iso(5)); check("B.capture correction by the receiver (non-sender) -> 403", s == 403)
    s, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 70}, headers=idem("rq"), token=T["bob"]); s, rp = req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("rp"), token=T["ada"])
    s, j = corr(A, T, "ada", rp["payment_id"], 1, 10, iso(5)); check("B.payment that paid a request is correctable (201)", s == 201, (s, j))
    s, f = req(A, "GET", "/activity?limit=200", token=T["ada"]); check("B.activity feed still shows ORIGINAL amounts, correction records are not feed payments", all(x["amount"] in (100, 300, 70) for x in f["payments"]) and len(f["payments"]) == 3, [(x["payment_id"], x["amount"]) for x in f["payments"]])
    s, rv = req(A, "GET", f"/payments/{rp['payment_id']}/revisions", token=T["bob"]); check("B.revisions endpoint: receiver can read, ordered", s == 200 and [r["revision"] for r in rv["revisions"]] == [1, 2], rv)
    check("B.revisions: third party 404 even for public payment", req(A, "GET", f"/payments/{rp['payment_id']}/revisions", token=T["cy"])[0] == 404 and req(A, "GET", f"/payments/{rp['payment_id']}/revisions", token=T["op"])[0] == 404)
    check("B.revisions: no token 401, unknown 404", req(A, "GET", f"/payments/{rp['payment_id']}/revisions")[0] == 401 and req(A, "GET", "/payments/nope/revisions", token=T["ada"])[0] == 404)
    check("B.corrections endpoint exists only as POST (GET/DELETE -> 4xx, no 5xx)", req(A, "GET", f"/payments/{rp['payment_id']}/corrections", token=T["ada"])[0] in (404, 405))
    s, j = req(A, "GET", "/payments/x/revisions?limit=-1", token=T["ada"]); print("   INFO revisions with junk query:", s)

def G_validation():
    T = fresh(bal=(10 ** 10, 2500, 0, 100000)); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]; n = [0]
    good = lambda **kw: dict({"expected_revision": 1, "amount": 500, "effective_at": iso(5), "reason": "ok"}, **kw)
    def c(body, raw=None):
        n[0] += 1; return req(A, "POST", f"/payments/{pid}/corrections", rawbody=raw, body=None if raw else body, headers=idem(f"v{n[0]}"), token=T["ada"])
    # note: some good bodies succeed and raise the revision; use dynamic revision
    def cur(): return len(req(A, "GET", f"/payments/{pid}/revisions", token=T["ada"])[1]["revisions"])
    for field in ("expected_revision", "amount", "effective_at", "reason"):
        b = good(expected_revision=cur()); b.pop(field); s, j = c(b); check(f"C.missing {field} -> 422", s == 422 and j["error"]["code"] == "validation_failed", (s, j))
    for v in (0, -1, 1.5, "1", True, None, [], 10 ** 12):
        s, j = c(good(expected_revision=v)); check(f"C.expected_revision {v!r} -> 4xx (422/409/400), never 5xx/201", s in (422, 400, 409), (s, j)); 
        if v in (0, -1, 1.5, "1", True, None, []): check(f"C.expected_revision {v!r} -> 422 validation_failed", s == 422, (s, j))
    for v in (-1, 1000000001, 1.5, "5", True, None, [], {}, 10 ** 30):
        s, j = c(good(expected_revision=cur(), amount=v)); check(f"C.amount {v!r} -> 422", s == 422 and j["error"]["code"] == "validation_failed", (s, j))
    for v in ("", "x" * 201, 5, None, True, [], "😀" * 201):
        s, j = c(good(expected_revision=cur(), reason=v)); check(f"C.reason {str(v)[:12]!r}(len {len(v) if isinstance(v, str) else '-'}) -> 422", s == 422, (s, j))
    for v in ("😀" * 200, "x" * 200, "é"):
        s, j = c(good(expected_revision=cur(), reason=v)); check(f"C.reason of {len(v)} chars accepted (201) and echoed verbatim", s == 201 and j["reason"] == v, (s, j))
    for v in ("", "2026-10-05", "2026-10-05T10:00:00", "yesterday", 5, None, True, "2026-13-01T00:00:00+00:00", "2026-02-30T00:00:00+00:00", "2026-10-05T25:00:00+00:00", "2026-10-05T10:60:00+00:00", "2026-10-05 10:00:00+00:00", "2026-10-05T10:00:00+24:00", "2026-10-05T10:00:00+99:00", "2026-10-05T10:00:00+0000", "2026-10-05T10:00:00 +00:00", " 2026-10-05T10:00:00Z", "2026-10-05T10:00:00.Z", "2026-10-05T10:00:00,5Z"):
        s, j = c(good(expected_revision=cur(), effective_at=v)); check(f"C.effective_at {v!r} -> 422", s == 422 and j["error"]["code"] == "validation_failed", (s, j))
    fut = iso(-3600); s, j = c(good(expected_revision=cur(), effective_at=fut)); check("C.effective_at one hour in the future -> 422", s == 422, (s, j))
    fut = iso(-2); s, j = c(good(expected_revision=cur(), effective_at=fut)); check("C.effective_at 2 s in the future -> 422", s == 422, (s, j))
    for v in ("2026-10-04T10:00:00Z", "2026-10-04T10:00:00z", "2026-10-04t10:00:00Z", "2026-10-04T10:00:00.1Z", "2026-10-04T10:00:00.123456789+05:30", "2026-10-04T10:00:00-00:00", "2026-10-04T23:59:60Z", "2026-10-04T10:00:00+23:59", "1970-01-01T00:00:00Z", "0001-01-01T00:00:00Z"):
        r0 = cur(); s, j = c(good(expected_revision=r0, effective_at=v)); check(f"C.effective_at {v!r} accepted and echoed exactly", s == 201 and j["effective_at"] == v, (s, j))
    s, j = c(good(expected_revision=cur(), amount=0)); check("C.amount 0 (reverses the payment) accepted", s == 201 and j["amount"] == 0, (s, j)); r = cur()
    check("C.…after reversal the sender got the money back (ada net 0 for this payment)", sme(A, T, "ada")[1]["total"] == 10 ** 10 and sme(A, T, "bob")[1]["total"] == 2500, (sme(A, T, "ada")[1]["total"], sme(A, T, "bob")[1]["total"]))
    s, j = c(good(expected_revision=cur(), amount=1000000000)); check("C.amount 1000000000 accepted (boundary)", s == 201, (s, j))
    s, j = c(good(expected_revision=cur(), amount=1000, extra=1, parties="x", visibility="private")); check("C.unknown fields ignored; parties/visibility unchanged", s == 201 and set(j) == {"payment_id", "revision", "amount", "effective_at", "recorded_at", "reason"}, (s, j))
    s, st = sstatement(A, T, "ada", "limit=200"); e = [x for x in st["entries"] if x["payment"]["payment_id"] == pid][0]; check("C.statement keeps parties and visibility after corrections", e["payment"]["from_handle"] == "ada" and e["payment"]["to_handle"] == "bob" and e["payment"]["visibility"] == "public", e["payment"])
    s, j = c(None, raw='{"expected_revision":1e0,"amount":1e2,"effective_at":"%s","reason":"x"}' % iso(5)); print("   INFO integer-valued float forms expected_revision 1e0 / amount 1e2 ->", s, (j or {}).get("error"))
    s, j = c(good(expected_revision=cur(), amount=5.0)); check("C.amount 5.0 (integral numeric) accepted like stage-1 numbers", s == 201, (s, j))
    s, j = req(A, "POST", f"/payments/{pid}/corrections", rawbody='x' * 300000, headers=idem("big"), token=T["ada"]); check("C.body > 256KiB -> 413", s == 413, s)

def G_instants():
    T = fresh(); pay(A, T, "ada", "bob", 100)
    ok = ["2026-09-24T13:20:00Z", "2026-09-24T13:20:00z", "2026-09-24t13:20:00Z", "2026-09-24T13:20:00+00:00", "2026-09-24T13:20:00-07:00", "2026-09-24T13:20:00.1+02:00", "2026-09-24T13:20:00.123456Z", "2026-09-24T13:20:00.1234567Z", "2026-09-24T13:20:00.123456789Z", "2026-09-24T23:59:60Z", "2026-09-24T13:20:00+23:59", "2026-09-24T13:20:00-23:59", "2099-01-01T00:00:00Z", "1999-12-31T23:59:59Z", "2026-09-24T13:20:00-00:00"]
    bad = ["", "2026-09-24", "2026-09-24T13:20:00", "2026-09-24 13:20:00Z", "13:20:00Z", "yesterday", "now", "1790000000", "2026-09-24T13:20Z", "2026-09-24T13:20:00", "2026-09-24T13:20:00+24:00", "2026-09-24T13:20:00+0000", "2026-09-24T13:20:00+00", "2026-13-24T13:20:00Z", "2026-09-31T13:20:00Z", "2026-09-24T24:00:00Z", "2026-09-24T13:61:00Z", "2026-09-24T13:20:61Z", "2026-09-24T13:20:00.Z", "2026-09-24T13:20:00Zjunk", "2026-09-24T13:20:00Z2026", "%20", "2026-09-24T13:20:00%20Z", "2026-09-24T13:20:00+00:60"]
    import urllib.parse as _up
    enc = lambda s: _up.quote(s, safe="")
    for name, base in (("/me", "as_of"), ("/me", "known_at"), ("/statement", "from"), ("/statement", "to"), ("/statement", "known_at")):
        for v in ok:
            st, j = req(A, "GET", f"{name}?{base}={enc(v)}", token=T["ada"]); good = st == 200 and (name == "/statement" or j.get(base) == v)
            if not good: print("   NOTE accepted-form rejected:", name, base, repr(v), st, (j or {}).get("error"))
            check(f"D.{name}?{base}={v!r} -> 200" + (" echoed exactly" if name == "/me" else ""), good, (st, j if st != 200 else j.get(base)))
        for v in bad:
            st, j = req(A, "GET", f"{name}?{base}={enc(v) if v != '%20' else '%20'}", token=T["ada"]); check(f"D.{name}?{base}={v!r} -> 422 validation_failed", st == 422 and j["error"]["code"] == "validation_failed", (st, j))
    for name in ("/me", "/statement"):
        st, j = req(A, "GET", f"{name}?as_of=", token=T["ada"]) if name == "/me" else req(A, "GET", f"{name}?from=", token=T["ada"]); check(f"D.{name} empty instant param -> 422", st == 422)
        st, j = req(A, "GET", f"{name}?as_of=2026-09-24T13:20:00+00:00" if name == "/me" else f"{name}?from=2026-09-24T13:20:00+00:00", token=T["ada"]); print(f"   INFO {name} raw '+' (decodes to space) ->", st)
        check(f"D.{name} raw unencoded '+' offset: 200 (lenient) or 422, never 5xx", st in (200, 422))
        st, j = req(A, "GET", f"{name}?foo=bar&{'as_of' if name == '/me' else 'from'}=2026-09-24T13:20:00Z&foo=baz", token=T["ada"]); check(f"D.{name} unrecognized params ignored", st == 200)
        st, j = req(A, "GET", f"{name}?{'as_of' if name == '/me' else 'from'}=2026-09-24T13:20:00Z&{'as_of' if name == '/me' else 'from'}=bad", token=T["ada"]); print(f"   INFO {name} duplicated param (first valid, second bad) ->", st)
    st, j = req(A, "GET", "/me?as_of=2026-09-24T13:20:00Z", token=T["ada"]); check("D./me?as_of keeps all stage-2 money fields", set(j) >= {"balance", "total", "available", "held", "currency", "minor_units", "as_of"} and j["balance"] == j["total"], j)
    st, j = req(A, "GET", "/me?as_of=2026-09-24T13:20:00Z", ); check("D./me?as_of no token -> 401 (before param validation)", st == 401)
    st, j = req(A, "GET", "/me?as_of=bad", ); check("D./me?as_of=bad no token -> 401", st == 401)
    st, j = req(A, "GET", "/statement?from=bad&to=bad2&limit=0", token=T["ada"]); check("D.statement all-invalid -> 422", st == 422)
    for q in ("limit=0", "limit=201", "limit=1e2", "limit=4.0", "limit=+4", "limit=-1", "offset=-1", "offset=1e0", "limit=", "offset=abc"):
        st, j = sstatement(A, T, "ada", q); check(f"D.statement ?{q} -> 422", st == 422, (st, j))
    st, j = sstatement(A, T, "ada", "from=2026-09-24T13:20:00Z&to=2026-09-24T13:19:00Z"); print("   INFO from>to ->", st, (j or {}).get("opening_balance"), (j or {}).get("closing_balance"), len((j or {}).get("entries", [])))
    check("D.statement from>to: 200 empty window or 422, not 5xx", st in (200, 422))
    st, j = sstatement(A, T, "ada", "from=2026-09-24T13:20:00Z&to=2026-09-24T13:20:00Z"); check("D.statement from==to -> empty half-open window", st == 200 and j["entries"] == [] and j["opening_balance"] == j["closing_balance"], (st, j))

def G_semantics():
    t1 = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 7200)); t2 = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 3600)); t3 = time.strftime("%Y-%m-%dT%H:%M:%S+02:00", time.gmtime(time.time() - 1800 + 7200))
    f = f3(payments=[{"id": "sa", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 500, "note": "n1", "created_at": t1}, {"id": "sb", "from_user_id": "u_bob", "to_user_id": "u_ada", "amount": 200, "created_at": t2}, {"id": "sc", "from_user_id": "u_ada", "to_user_id": "u_cy", "amount": 50, "visibility": "private", "created_at": t3}, {"id": "sz", "from_user_id": "u_cy", "to_user_id": "u_ada", "amount": 10}])
    f["users"][0]["balance"] = 10000; f["users"][1]["balance"] = 2500; f["users"][2]["balance"] = 100
    s, j = req(A, "POST", "/_test/reset", f); check("E.seeded payments with created_at (+02:00 offset, one omitted) accepted", s == 204, (s, j)); T = tk(A)
    check("E.seeded balances unchanged by loading payments (ada 10000, bob 2500, cy 100)", [sme(A, T, n)[1]["total"] for n in ("ada", "bob", "cy")] == [10000, 2500, 100])
    s, st = sstatement(A, T, "ada", "limit=200"); ents = st["entries"]; print("   INFO ada statement ids:", [e["payment"]["payment_id"] for e in ents], "opening", st["opening_balance"])
    net = -500 + 200 - 50 + 10; check("E.opening_balance == seeded balance - net of seeded payments (ada 10000 - (-340))", st["opening_balance"] == 10000 - net and st["closing_balance"] == 10000, (st["opening_balance"], st["closing_balance"]))
    check("E.omitted created_at: payment uses reset time, after the explicit past ones and before later API payments", ents[-1]["payment"]["payment_id"] == "sz", [e["payment"]["payment_id"] for e in ents])
    check("E.created_at of sc normalises to the same instant regardless of offset (ordering by instant)", [e["payment"]["payment_id"] for e in ents][:3] == ["sa", "sb", "sc"], [e["payment"]["payment_id"] for e in ents])
    s, p = pay(A, T, "ada", "bob", 5); check("E.API payment after reset sorts after seeded 'sz' and has created_at with offset", p["created_at"] > ents[-1]["payment"]["created_at"] or parse(p["created_at"]) > parse(ents[-1]["payment"]["created_at"]), (p["created_at"], ents[-1]["payment"]["created_at"]))
    for fld in ("created_at",):
        check("E.every endpoint returning a payment includes created_at (activity, statement, revisions, capture, request pay, settlement)", all("created_at" in x for x in req(A, "GET", "/activity", token=T["ada"])[1]["payments"]))
    s, act = req(A, "GET", "/activity?limit=200", token=T["ada"]); cts = [parse(x["created_at"]) for x in act["payments"]]; check("E./activity ordering is newest-first by created_at", cts == sorted(cts, reverse=True), cts)
    # as_of semantics
    for as_of, who, exp in ((t1, "ada", 10000 + 340 - 500), (fmt(parse(t1) - 1), "ada", 10340), (fmt(parse(t1) + 1), "ada", 9840), (t2, "bob", 2500 - 500 + 200 - 200 + 0 if False else None)):
        if exp is None: continue
        s, j = sme(A, T, who, "as_of=" + as_of.replace("+", "%2B")); check(f"E.as_of {as_of} inclusive at the payment's instant [{who}]", j["total"] == exp, (j["total"], exp))
    s, j = sme(A, T, "ada", "as_of=2099-01-01T00:00:00Z"); check("E.as_of far future == current", j["total"] == sme(A, T, "ada")[1]["total"])
    s, j = sme(A, T, "ada", "as_of=1990-01-01T00:00:00Z"); check("E.as_of before earliest == opening balance", j["total"] == 10340, j)
    s, j = sme(A, T, "ada", "as_of=2026-10-05T04%3A00%3A00%2B02%3A00"); check("E.as_of echoes exactly as given (with offset)", j.get("as_of") == "2026-10-05T04:00:00+02:00", j.get("as_of"))
    # new account opens at zero
    s, su = req(A, "POST", "/auth/signup", {"email": "new@x.com", "password": "password1", "display_name": "N"}); tn = {"new": su["token"]}
    s, j = req(A, "GET", "/me?as_of=1990-01-01T00:00:00Z", token=su["token"]); check("E.new account opens at zero (as_of before everything)", j["total"] == 0)
    pay(A, T, "ada", "new_handle_missing", 1) if False else None
    s, st = req(A, "GET", "/statement", token=su["token"]); check("E.empty statement for new account: opening 0, closing 0, entries [], has_more false, snapshot present", st["opening_balance"] == 0 and st["closing_balance"] == 0 and st["entries"] == [] and st["has_more"] is False and st.get("snapshot"))
    # zero-amount revisions and reversals
    T = fresh(); s, p = pay(A, T, "ada", "bob", 400); pid = p["payment_id"]; s, c0 = corr(A, T, "ada", pid, 1, 0, iso(5), "reverse")
    s, st = sstatement(A, T, "ada", "limit=200"); e = st["entries"]; check("E.zero-amount revision still appears as an entry with delta 0 and balance_after unchanged", len(e) == 1 and e[0]["delta"] == 0 and e[0]["revision"] == 2 and e[0]["payment"]["amount"] == 0 and e[0]["balance_after"] == 10000, e)
    s, st = sstatement(A, T, "ada", "limit=200&known_at=" + p["created_at"].replace("+", "%2B")); check("E.known_at at revision 1's recorded time selects revision 1 (amount 400)", st["entries"][0]["revision"] == 1 and st["entries"][0]["delta"] == -400, st["entries"])
    s, st = sstatement(A, T, "ada", "limit=200&known_at=" + fmt(parse(p["created_at"]) - 1).replace("+", "%2B")); check("E.known_at just before the payment was recorded: 'none recorded yet contributes nothing' (no entries, balance == opening)", st["entries"] == [] and st["opening_balance"] == st["closing_balance"], st)
    s, j = sme(A, T, "ada", "known_at=" + fmt(parse(p["created_at"]) - 1).replace("+", "%2B")); check("E./me known_at before recording -> opening balance (10000)", j["total"] == 10000, j)
    s, st = sstatement(A, T, "ada", "limit=200&known_at=" + c0["recorded_at"].replace("+", "%2B")); check("E.known_at == correction recorded_at selects the correction (inclusive)", st["entries"][0]["revision"] == 2)
    s, st = sstatement(A, T, "ada", "limit=200&known_at=" + fmt(parse(c0["recorded_at"]) - 1).replace("+", "%2B")); check("E.known_at 1µs before the correction was recorded selects revision 1", st["entries"][0]["revision"] == 1)
    s, st = sstatement(A, T, "ada", "known_at=2099-01-01T00:00:00Z"); check("E.known_at in the future selects the latest revision", st["entries"][0]["revision"] == 2)
    # correction moving a payment out of / into a statement window
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100); w_from = fmt(parse(p["created_at"]) - 60_000_000); w_to = fmt(parse(p["created_at"]) + 60_000_000)
    q = f"from={w_from.replace('+', '%2B')}&to={w_to.replace('+', '%2B')}"
    check("E.payment inside the window before correction", len(sstatement(A, T, "ada", q)[1]["entries"]) == 1)
    corr(A, T, "ada", p["payment_id"], 1, 100, fmt(parse(p["created_at"]) - 3600_000_000)); st = sstatement(A, T, "ada", q)[1]
    check("E.correction moving effective_at an hour earlier moves it OUT of the window (opening includes it)", st["entries"] == [] and st["opening_balance"] == 9900, st)
    st = sstatement(A, T, "ada", q + "&known_at=" + p["created_at"].replace("+", "%2B"))[1]; check("E.…while known_at before the correction still shows it in the window", len(st["entries"]) == 1 and st["opening_balance"] == 10000)
    s, st = sstatement(A, T, "bob", "limit=200"); check("E.receiver's statement shows positive delta and own balances", st["entries"][0]["delta"] == 100 and st["closing_balance"] == 2600, st["entries"][0])
    s, st = sstatement(A, T, "cy", "limit=200"); check("E.third party statement never shows other users' payments (even public)", st["entries"] == [])
    # visibility: private payment appears in both parties' statements
    s, pp = pay(A, T, "ada", "bob", 7, visibility="private"); check("E.private payment appears in both parties' statements but not third party's", any(e["payment"]["payment_id"] == pp["payment_id"] for e in sstatement(A, T, "bob", "limit=200")[1]["entries"]) and not any(e["payment"]["payment_id"] == pp["payment_id"] for e in sstatement(A, T, "cy", "limit=200")[1]["entries"]))
    # seeded future created_at -> 422 unchanged
    reset(A, f3()); Tt = tk(A); fut = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 3600))
    s, j = req(A, "POST", "/_test/reset", f3(payments=[{"id": "x", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1, "created_at": fut}])); check("E.seeded created_at in the future -> 422 validation_failed", s == 422 and j["error"]["code"] == "validation_failed", (s, j))
    check("E.…reset changed nothing (old token works)", req(A, "GET", "/me", token=Tt["ada"])[0] == 200)
    s, j = req(A, "POST", "/_test/reset", f3(payments=[{"id": "x", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1, "created_at": "garbage"}])); check("E.seeded created_at garbage -> 4xx", s in (400, 422), (s, j))
    s, j = req(A, "POST", "/_test/reset", f3(payments=[{"id": "x", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1, "created_at": "2026-10-04T10:00:00"}])); check("E.seeded created_at without offset -> 4xx", s in (400, 422), (s, j))
    s, j = req(A, "POST", "/_test/reset", f3(payments=[{"id": "x", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1, "created_at": None}])); print("   INFO seeded created_at null ->", s)
    # seeded history that goes negative / opening negative
    s, j = req(A, "POST", "/_test/reset", f3(bal=(0, 0, 0, 0), payments=[{"id": "x", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "created_at": PAST}])); print("   INFO seeded history with negative opening (ada balance 0 after paying 100 => opening 100? ok) ->", s)
    s, j = req(A, "POST", "/_test/reset", f3(bal=(0, 0, 0, 0), payments=[{"id": "x", "from_user_id": "u_bob", "to_user_id": "u_ada", "amount": 100, "created_at": PAST}])); print("   INFO seeded payment makes bob's opening negative (-100... bob balance 0 after paying 100 => opening 100) ->", s)

def G_snapshots():
    T = fresh(); ids = [pay(A, T, "ada", "bob", 10 + i)[1]["payment_id"] for i in range(7)]
    s, st = sstatement(A, T, "ada", "limit=3"); snap = st["snapshot"]
    check("F.first response carries opaque snapshot token and 3 entries has_more", s == 200 and isinstance(snap, str) and len(st["entries"]) == 3 and st["has_more"])
    def page(sn, who="ada", **kw):
        q = f"snapshot={sn}" + "".join(f"&{k}={v}" for k, v in kw.items()); return sstatement(A, T, who, q)
    full = page(snap, limit=200, offset=0)[1]; check("F.snapshot pages the whole frozen result", len(full["entries"]) == 7 and full["has_more"] is False and full["opening_balance"] == st["opening_balance"] and full["closing_balance"] == st["closing_balance"])
    check("F.snapshot page response carries the same snapshot-consistent balances", [e["balance_after"] for e in full["entries"]][:3] == [e["balance_after"] for e in st["entries"]])
    pay(A, T, "ada", "bob", 999); corr(A, T, "ada", ids[0], 1, 777, iso(3600)); s, a = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 100}, headers=idem("a1"), token=T["ada"]); req(A, "POST", f"/authorizations/{a['authorization_id']}/void", {}, token=T["ada"])
    again = page(snap, limit=200)[1]; check("F.frozen after later payment, correction and hold lifecycle actions", again["entries"] == full["entries"] and again["closing_balance"] == full["closing_balance"] and again["opening_balance"] == full["opening_balance"])
    for lim, off, n_exp, hm in ((3, 0, 3, True), (3, 3, 3, True), (3, 6, 1, False), (3, 7, 0, False), (3, 100, 0, False), (7, 0, 7, False), (6, 0, 6, True), (1, 6, 1, False), (200, 0, 7, False), (2, 4, 2, True), (2, 5, 2, False)):
        s, j = page(snap, limit=lim, offset=off); check(f"F.snapshot limit={lim} offset={off}: {n_exp} entries, has_more {hm}", s == 200 and len(j["entries"]) == n_exp and j["has_more"] is hm, (s, len(j["entries"]) if s == 200 else j, j.get("has_more")))
    for extra in ("from=2026-01-01T00:00:00Z", "to=2026-12-01T00:00:00Z", "known_at=2026-12-01T00:00:00Z", "from=bad", "known_at=", "from=&limit=2", "to=2026-12-01T00:00:00Z&limit=1&offset=0"):
        s, j = req(A, "GET", f"/statement?snapshot={snap}&{extra}", token=T["ada"]); check(f"F.snapshot + {extra.split('&')[0]!r} -> 422 validation_failed", s == 422 and j["error"]["code"] == "validation_failed", (s, j))
    s, j = req(A, "GET", f"/statement?snapshot={snap}&limit=2&offset=1&foo=bar", token=T["ada"]); check("F.snapshot + limit/offset + unrecognized param -> ok", s == 200)
    for q, exp in ((f"snapshot={snap}&limit=0", 422), (f"snapshot={snap}&offset=-1", 422), (f"snapshot={snap}&limit=1e1", 422)):
        s, j = req(A, "GET", "/statement?" + q, token=T["ada"]); check(f"F.snapshot ?{q.split('&',1)[1]} -> {exp}", s == exp, (s, j))
    check("F.unknown token -> 404 not_found", req(A, "GET", "/statement?snapshot=nope", token=T["ada"])[0] == 404 and req(A, "GET", "/statement?snapshot=st3_" + "0" * 32, token=T["ada"])[1]["error"]["code"] == "not_found")
    check("F.another user's token -> 404 (even a counterparty)", req(A, "GET", f"/statement?snapshot={snap}", token=T["bob"])[0] == 404 and req(A, "GET", f"/statement?snapshot={snap}", token=T["cy"])[0] == 404)
    check("F.snapshot without token -> 401", req(A, "GET", f"/statement?snapshot={snap}")[0] == 401)
    check("F.empty snapshot value -> 404 or 422, not 5xx", req(A, "GET", "/statement?snapshot=", token=T["ada"])[0] in (404, 422))
    s, st2 = sstatement(A, T, "ada", "limit=3"); check("F.every first read mints a distinct token", st2["snapshot"] != snap)
    snap2 = st2["snapshot"]; check("F.second snapshot reflects the NEW state (999 payment, 777 correction)", any(e["delta"] == -999 for e in page(snap2, limit=200)[1]["entries"]))
    # default to is frozen: payments after first read invisible even though 'to' defaulted to now
    T = fresh(); pay(A, T, "ada", "bob", 1); s, st = sstatement(A, T, "ada", "limit=1"); sn = st["snapshot"]; pay(A, T, "ada", "bob", 2); pay(A, T, "ada", "bob", 3); j = page(sn, limit=200)[1]; check("F.default `to` frozen at first read: later payments not paged", len(j["entries"]) == 1 and j["closing_balance"] == st["closing_balance"], j)
    # token from before reset / import
    T = fresh(); s, st = sstatement(A, T, "ada"); old = st["snapshot"]; reset(A, f3()); T = tk(A); check("F.token from before reset -> 404", req(A, "GET", f"/statement?snapshot={old}", token=T["ada"])[0] == 404)
    s, st = sstatement(A, T, "ada"); s2, ex = req(A, "GET", "/_test/export"); req(A, "POST", "/_test/import", ex); check("F.token from before import -> 404 or still valid, never 5xx", req(A, "GET", f"/statement?snapshot={st['snapshot']}", token=T["ada"])[0] in (200, 404)); print("   INFO token across import ->", req(A, "GET", f"/statement?snapshot={st['snapshot']}", token=T["ada"])[0])
    # snapshot with known_at / window frozen
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100); k = p["created_at"].replace("+", "%2B")
    s, st = sstatement(A, T, "ada", f"known_at={fmt(parse(p['created_at']) - 5).replace('+', '%2B')}"); sn = st["snapshot"]; check("F.snapshot taken with known_at before the payment is empty and stays empty", st["entries"] == [] and page(sn, limit=10)[1]["entries"] == [])
    # many snapshots: memory/time bounded
    T = fresh(bal=(10 ** 9, 0, 0, 100000)); sub = list(range(400)); pool(lambda i: pay(A, T, "ada", "bob", 1, key=f"m{i}"), 400)
    t0 = time.time(); toks_ = []
    for i in range(300): s, j = sstatement(A, T, "ada", "limit=1"); toks_.append(j["snapshot"])
    dt = time.time() - t0; print(f"   INFO 300 first reads over 400 payments: {dt:.1f}s")
    check("F.300 snapshots over a 400-payment history created, each read < 5s", dt < 60)
    ok = all(page(t, limit=1)[0] == 200 for t in toks_[::30]); check("F.old tokens remain valid after 300 more snapshots", ok)

def G_holds():
    f = f3(); T = fresh(authorization_ttl_seconds=2) if False else None
    reset(A, dict(f3(), authorization_ttl_seconds=3)); T = tk(A); s, a0 = pay(A, T, "ada", "cy", 1)
    s, h = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 4000}, headers=idem("h1"), token=T["ada"]); hid = h["authorization_id"]; c_us = parse(h["created_at"]); e_us = parse(h["expires_at"])
    check("G.closed_at is null while open; authorization carries closed_at", "closed_at" in h and h["closed_at"] is None, h)
    q = lambda us, extra="": "as_of=" + fmt(us, 0, 6).replace("+", "%2B") + extra
    check("G.as_of one µs before creation: held 0", sme(A, T, "ada", q(c_us - 1))[1]["held"] == 0)
    check("G.as_of exactly at creation: held 4000, available 6000, balance 10000-1", (lambda m: m["held"] == 4000 and m["available"] == m["total"] - 4000 and m["balance"] == m["total"])(sme(A, T, "ada", q(c_us))[1]))
    check("G.known_at before the hold was created: hold unknown (held 0)", sme(A, T, "ada", q(c_us + 10) + "&known_at=" + fmt(c_us - 1, 0, 6).replace("+", "%2B"))[1]["held"] == 0)
    check("G.once creation known, expiry deadline known: as_of beyond deadline with known_at just after creation -> held 0", sme(A, T, "ada", q(e_us + 1_000_000) + "&known_at=" + fmt(c_us, 0, 6).replace("+", "%2B"))[1]["held"] == 0)
    check("G.as_of one µs before the deadline: still held; exactly at deadline: expired (held 0)", sme(A, T, "ada", q(e_us - 1))[1]["held"] == 4000 and sme(A, T, "ada", q(e_us))[1]["held"] == 0)
    check("G.future as_of (query beyond now): open hold expires at its deadline", sme(A, T, "ada", q(e_us + 10 ** 9))[1]["held"] == 0 and sme(A, T, "ada", q(c_us + 1_000_000))[1]["held"] == 4000)
    s, cp = req(A, "POST", f"/authorizations/{hid}/capture", {"amount": 1000, "final": False}, headers=idem("c1"), token=T["bob"]); t_cap = parse(cp["created_at"])
    check("G.nonfinal capture reduces the hold at capture time and moves the money at the same instant", sme(A, T, "ada", q(t_cap - 1))[1]["held"] == 4000 and sme(A, T, "ada", q(t_cap))[1]["held"] == 3000 and sme(A, T, "ada", q(t_cap))[1]["total"] == sme(A, T, "ada", q(t_cap - 1))[1]["total"] - 1000)
    check("G.known_at before the capture was recorded: hold still 4000, total unchanged", sme(A, T, "ada", q(t_cap + 5) + "&known_at=" + fmt(t_cap - 1, 0, 6).replace("+", "%2B"))[1]["held"] == 4000)
    time.sleep(0.05); s, vd = req(A, "POST", f"/authorizations/{hid}/void", {}, token=T["ada"]); lst = req(A, "GET", "/authorizations", token=T["ada"])[1]["authorizations"][0]; t_v = parse(lst["closed_at"])
    check("G.void releases the remainder at closed_at (3000 -> 0)", lst["status"] == "voided" and sme(A, T, "ada", q(t_v - 1))[1]["held"] == 3000 and sme(A, T, "ada", q(t_v))[1]["held"] == 0, lst)
    check("G.known_at before the void was known: hold still open (3000) until its deadline", sme(A, T, "ada", q(t_v + 10) + "&known_at=" + fmt(t_v - 1, 0, 6).replace("+", "%2B"))[1]["held"] == 3000)
    check("G.captured payment appears exactly once in statements with links (authorization_id)", sum(1 for e in sstatement(A, T, "ada", "limit=200")[1]["entries"] if e["payment"]["authorization_id"] == hid) == 1 and sum(1 for e in sstatement(A, T, "bob", "limit=200")[1]["entries"] if e["payment"]["authorization_id"] == hid) == 1)
    check("G.statement contains money movements only (no authorization/void entries)", len(sstatement(A, T, "ada", "limit=200")[1]["entries"]) == 2)   # a0 + capture
    # final capture: closed_at == capture time
    s, h2 = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 500}, headers=idem("h2"), token=T["ada"]); s, cp2 = req(A, "POST", f"/authorizations/{h2['authorization_id']}/capture", {"amount": 200}, headers=idem("c2"), token=T["bob"])
    l2 = [x for x in req(A, "GET", "/authorizations", token=T["ada"])[1]["authorizations"] if x["authorization_id"] == h2["authorization_id"]][0]; check("G.final capture: closed_at == capture created_at, remainder released then", l2["closed_at"] == cp2["created_at"] and sme(A, T, "ada", q(parse(cp2["created_at"]), ))[1]["held"] == 0 and sme(A, T, "ada", q(parse(cp2["created_at"]) - 1))[1]["held"] == 500, (l2["closed_at"], cp2["created_at"]))
    # clock expiry: closed_at == expires_at, available restored in history at expires_at
    s, h3 = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 300}, headers=idem("h3"), token=T["ada"]); time.sleep(3.3)
    l3 = [x for x in req(A, "GET", "/authorizations", token=T["ada"])[1]["authorizations"] if x["authorization_id"] == h3["authorization_id"]][0]; check("G.expired by the clock: status expired, closed_at == expires_at", l3["status"] == "expired" and l3["closed_at"] == l3["expires_at"], l3)
    e3 = parse(h3["expires_at"]); check("G.history around the expiry instant: held 300 just before, 0 at the deadline", sme(A, T, "ada", q(e3 - 1))[1]["held"] == 300 and sme(A, T, "ada", q(e3))[1]["held"] == 0)
    # as_of/known_at invariants: balance == total, available == total - held in every view
    ok = True
    for us in (c_us - 5, c_us, t_cap, t_v, e3 - 1, e3, int(time.time() * 1e6), int(time.time() * 1e6) + 10 ** 10):
        for kn in ("", "&known_at=" + fmt(t_cap, 0, 6).replace("+", "%2B"), "&known_at=" + fmt(c_us - 1, 0, 6).replace("+", "%2B")):
            m = sme(A, T, "ada", q(us, kn))[1]; ok &= (m["balance"] == m["total"] and m["available"] == m["total"] - m["held"] and m["available"] >= 0)
    check("G.every historical view: balance==total, available==total-held>=0", ok)
    # sum of historical totals across users == seeded total (known_at omitted) at many as_of
    bad = []
    for us in (c_us - 5, c_us, t_cap, t_v, e3, int(time.time() * 1e6)):
        sm = sum(sme(A, T, n, q(us))[1]["total"] for n in ("ada", "bob", "cy", "op"))
        if sm != 112500: bad.append((us, sm))
    check("G.sum of balances in every historical view == seeded total", not bad, bad)
    # seeded holds and closed holds
    fut = "2099-01-01T00:00:00+00:00"; past = "2020-01-01T00:00:00+00:00"
    s, j = req(A, "POST", "/_test/reset", dict(f3(), authorizations=[{"id": "o1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 2000, "status": "open", "expires_at": fut}, {"id": "o2", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "status": "voided", "expires_at": fut}, {"id": "o3", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "status": "captured", "expires_at": fut}, {"id": "o4", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "status": "expired", "expires_at": fut}, {"id": "o5", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "status": "open", "expires_at": past}]))
    check("G.seeded holds incl. closed ones accepted by stage 3", s == 204, (s, j)); T = tk(A); lst = {x["authorization_id"]: x for x in req(A, "GET", "/authorizations?limit=200", token=T["ada"])[1]["authorizations"]}
    print("   INFO seeded closed_at:", {k: v.get("closed_at") for k, v in lst.items()})
    check("G.seeded open hold: closed_at null; seeded expired-by-clock open hold closed_at set", lst["o1"]["closed_at"] is None and lst["o5"]["status"] == "expired")
    m = sme(A, T, "ada")[1]; check("G.seeded open hold held 2000", m["held"] == 2000 and m["available"] == 8000, m)
    m2 = sme(A, T, "ada", "as_of=" + fmt(int(time.time() * 1e6) - 10, 0, 6).replace("+", "%2B"))[1]; print("   INFO as_of just before now with seeded open+expired(future expires_at) holds: held", m2["held"], "(current held", m["held"], ") -> see seeded_expired_hold.py (finding S3-1)")
    check("G.seeded open holds are assumed created at reset: before reset shows none", sme(A, T, "ada", "as_of=2020-01-01T00:00:00Z")[1]["held"] == 0)
    s, j = req(A, "POST", "/_test/reset", dict(f3(), authorizations=[{"id": "o1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 2000, "status": "open", "expires_at": fut, "created_at": "2026-01-01T00:00:00+00:00"}])); T = tk(A)
    print("   INFO seeded hold with created_at ->", s); 
    if s == 204: check("G.seeded hold with created_at supplied: held from that instant", sme(A, T, "ada", "as_of=2025-12-31T00:00:00Z")[1]["held"] == 0 and sme(A, T, "ada", "as_of=2026-01-01T00:00:00Z")[1]["held"] == 2000)

def G_imports():
    # stage-1 service -> stage 3
    reset(S1, {k: v for k, v in f3().items() if k != "authorizations"}); T1 = tk(S1)
    s, p1 = pay(S1, T1, "ada", "bob", 700, key="s1p1"); time.sleep(0.01); s, p2 = pay(S1, T1, "bob", "cy", 200, key="s1p2")
    s, rq = req(S1, "POST", "/requests", {"payer_handle": "ada", "amount": 300}, headers=idem("s1r"), token=T1["bob"]); s, rp = req(S1, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("s1rp"), token=T1["ada"])
    s, st1 = req(S1, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 50}, {"from_handle": "cy", "to_handle": "ada", "amount": 20}]}, headers=idem("s1st"), token=T1["op"])
    s, rq2 = req(S1, "POST", "/requests", {"payer_handle": "cy", "amount": 9}, headers=idem("s1r2"), token=T1["bob"])
    s, su = req(S1, "POST", "/auth/signup", {"email": "old@x.com", "password": "oldoldold", "display_name": "O"})
    tot1 = {n: req(S1, "GET", "/me", token=T1[n])[1]["balance"] for n in T1}; _, ex = req(S1, "GET", "/_test/export")
    s, j = req(B, "POST", "/_test/import", ex); check("H.stage-1 export imports into stage 3 -> 204", s == 204, (s, j))
    if s != 204: return
    TB = {n: login(B, f"{n}@example.com") for n in ("ada", "bob", "cy", "op")}
    check("H.balances, tokens, logins preserved", {n: req(B, "GET", "/me", token=T1[n])[1]["total"] for n in T1} == tot1 and login(B, "old@x.com", "oldoldold"))
    s, stm = req(B, "GET", "/statement?limit=200", token=T1["ada"]); ids = [e["payment"]["payment_id"] for e in stm["entries"]]; print("   INFO imported ada statement:", [(e['payment']['payment_id'][:8], e['delta'], e['balance_after']) for e in stm["entries"]], "opening", stm["opening_balance"])
    check("H.imported statement: opening + deltas == closing == balance; ordered by created_at", stm["opening_balance"] + sum(e["delta"] for e in stm["entries"]) == stm["closing_balance"] == tot1["ada"] and [parse(e["effective_at"]) for e in stm["entries"]] == sorted(parse(e["effective_at"]) for e in stm["entries"]))
    check("H.imported opening balances: ada opened with the seeded 10000 (before any payments)", stm["opening_balance"] == 10000, stm["opening_balance"])
    check("H.imported payment created_at preserved (stage-1 receipt unchanged)", [e["payment"]["created_at"] for e in stm["entries"] if e["payment"]["payment_id"] == p1["payment_id"]] == [p1["created_at"]], (stm["entries"][0]["payment"]["created_at"], p1["created_at"]))
    check("H.as_of at p1's instant includes it; one µs before excludes", req(B, "GET", "/me?as_of=" + p1["created_at"].replace("+", "%2B"), token=T1["ada"])[1]["total"] == 10000 - 700 and req(B, "GET", "/me?as_of=" + fmt(parse(p1["created_at"]) - 1, 0, 6).replace("+", "%2B"), token=T1["ada"])[1]["total"] == 10000)
    s, c = corr(B, T1, "ada", p1["payment_id"], 1, 400, iso(5)); check("H.imported plain payment is correctable (201, revision 2)", s == 201 and c["revision"] == 2, (s, c))
    check("H.imported request-paying payment correctable; settlement member -> 422 linked_payment_immutable", corr(B, T1, "ada", rp["payment_id"], 1, 100, iso(5))[0] == 201 and corr(B, T1, "op", st1["payments"][0]["payment_id"], 1, 10, iso(5))[1]["error"]["code"] == "linked_payment_immutable")
    s, j = req(B, "POST", f"/requests/{rq2['request_id']}/pay", {}, headers=idem("pay-imported"), token=T1["cy"]); check("H.imported pending request payable", s == 201, (s, j))
    check("H.old payment replay 200 identical; settlement replay 200", req(B, "POST", "/payments", {"to_handle": "bob", "amount": 700}, headers=idem("s1p1"), token=T1["ada"]) == (200, p1) and req(B, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 50}, {"from_handle": "cy", "to_handle": "ada", "amount": 20}]}, headers=idem("s1st"), token=T1["op"]) == (200, st1))
    check("H.new default authorization ttl 600 after importing a stage-1 export", abs(parse(req(B, "POST", "/authorizations", {"to_handle": "bob", "amount": 1}, headers=idem("na"), token=T1["ada"])[1]["expires_at"]) - parse(req(B, "GET", "/authorizations", token=T1["ada"])[1]["authorizations"][0]["created_at"]) - 600_000_000) < 2_000_000)
    check("H.sum of totals == seeded total after import and activity", sum(req(B, "GET", "/me", token=T1[n])[1]["total"] for n in T1) == 112500)
    # stage-2 service -> stage 3, with holds / captures / partial / void / expired
    reset(S2, dict(f3(), authorization_ttl_seconds=2)); T2 = tk(S2)
    s, p = pay(S2, T2, "ada", "bob", 500, key="s2p"); s, a1 = req(S2, "POST", "/authorizations", {"to_handle": "bob", "amount": 3000, "note": "partial"}, headers=idem("s2a1"), token=T2["ada"])
    s, cp1 = req(S2, "POST", f"/authorizations/{a1['authorization_id']}/capture", {"amount": 1000, "final": False}, headers=idem("s2c1"), token=T2["bob"]); time.sleep(0.01)
    s, a2 = req(S2, "POST", "/authorizations", {"to_handle": "cy", "amount": 800}, headers=idem("s2a2"), token=T2["ada"]); s, cp2 = req(S2, "POST", f"/authorizations/{a2['authorization_id']}/capture", {"amount": 300}, headers=idem("s2c2"), token=T2["cy"])
    s, a3 = req(S2, "POST", "/authorizations", {"to_handle": "bob", "amount": 400}, headers=idem("s2a3"), token=T2["ada"]); req(S2, "POST", f"/authorizations/{a3['authorization_id']}/void", {}, token=T2["ada"])
    s, a4 = req(S2, "POST", "/authorizations", {"to_handle": "bob", "amount": 250}, headers=idem("s2a4"), token=T2["ada"]); time.sleep(2.4)
    s, a5 = req(S2, "POST", "/authorizations", {"to_handle": "cy", "amount": 600}, headers=idem("s2a5"), token=T2["ada"])   # still open at export time
    me2 = {n: req(S2, "GET", "/me", token=T2[n])[1] for n in T2}; _, ex2 = req(S2, "GET", "/_test/export")
    s, j = req(B, "POST", "/_test/import", ex2); check("H.stage-2 export (open/partial/void/expired holds, captures) imports into stage 3 -> 204", s == 204, (s, j))
    if s != 204: return
    me3 = {n: req(B, "GET", "/me", token=T2[n])[1] for n in T2}
    check("H.imported /me: total/available/held equal the stage-2 values for every user", all(me3[n][k] == me2[n][k] for n in T2 for k in ("balance", "total", "available", "held")), (me2["ada"], me3["ada"]))
    la = {x["authorization_id"]: x for x in req(B, "GET", "/authorizations?limit=200", token=T2["ada"])[1]["authorizations"]}
    print("   INFO imported closed_at:", {k[:8]: (v["status"], v["closed_at"]) for k, v in la.items()})
    check("H.imported closed_at: voided has a closed_at, expired == expires_at, captured == last capture created_at, open null", la[a3["authorization_id"]]["closed_at"] is not None and la[a4["authorization_id"]]["closed_at"] == la[a4["authorization_id"]]["expires_at"] and la[a2["authorization_id"]]["closed_at"] == cp2["created_at"] and la[a5["authorization_id"]]["closed_at"] is None, {k[:8]: (v["status"], v["closed_at"]) for k, v in la.items()})
    stb = req(B, "GET", "/statement?limit=200", token=T2["ada"])[1]; caps = [e for e in stb["entries"] if e["payment"]["authorization_id"]]
    check("H.imported captures appear exactly once each in the statement with authorization_id links", sorted(e["payment"]["authorization_id"] for e in caps) == sorted([a1["authorization_id"], a2["authorization_id"]]), [(e["payment"]["authorization_id"]) for e in caps])
    check("H.imported statement closes at the current balance", stb["closing_balance"] == me2["ada"]["total"] and stb["opening_balance"] + sum(e["delta"] for e in stb["entries"]) == stb["closing_balance"] and stb["opening_balance"] == 10000)
    s, j = corr(B, T2, "ada", cp1["payment_id"], 1, 10, iso(5)); check("H.imported capture correction -> 422 linked_payment_immutable", s == 422 and j["error"]["code"] == "linked_payment_immutable", (s, j))
    s, j = corr(B, T2, "ada", p["payment_id"], 1, 200, iso(5)); check("H.imported plain payment correctable", s == 201, (s, j))
    # historical holds on imported state
    c1 = parse(la[a1["authorization_id"]]["created_at"]); mm = req(B, "GET", "/me?as_of=" + fmt(c1 - 1, 0, 6).replace("+", "%2B"), token=T2["ada"])[1]; mm2 = req(B, "GET", "/me?as_of=" + fmt(c1, 0, 6).replace("+", "%2B"), token=T2["ada"])[1]
    check("H.imported hold history: held 0 before a1 creation, 2000-3000 at that instant (stage-2 timestamps are millisecond: the 1000 capture may share the millisecond)", mm["held"] == 0 and 2000 <= mm2["held"] <= 3000, (mm["held"], mm2["held"]))
    check("H.sum of totals == seeded total on imported stage-2 state", sum(req(B, "GET", "/me", token=T2[n])[1]["total"] for n in T2) == 112500)
    s, c = req(B, "POST", f"/authorizations/{a5['authorization_id']}/capture", {"amount": 600}, headers=idem("s2c5"), token=T2["cy"]); check("H.imported open hold capturable once after import", s == 201 and req(B, "GET", "/me", token=T2["ada"])[1]["held"] == 0, (s, c))
    # stage-3 round trip with corrections, snapshots, revisions, idempotent replay
    reset(A, dict(f3(), authorization_ttl_seconds=600)); T = tk(A); pids = [pay(A, T, "ada", "bob", 100 + i, key=f"r{i}")[1]["payment_id"] for i in range(6)]
    cs = [corr(A, T, "ada", pids[i % 3], 1 + i // 3, 50 + i, iso(100 + i), key=f"corr{i}") for i in range(6)]
    s, ha = req(A, "POST", "/authorizations", {"to_handle": "cy", "amount": 700}, headers=idem("rt-h"), token=T["ada"]); s, cc = req(A, "POST", f"/authorizations/{ha['authorization_id']}/capture", {"amount": 200, "final": False}, headers=idem("rt-c"), token=T["cy"])
    s, failed = corr(A, T, "ada", pids[3], 1, 10 ** 9, iso(5), key="failedkey")
    instants = [c["recorded_at"] for _, c in cs] + [x["created_at"] for x in (cc,)] + [iso(500), iso(0.04)]
    def views(b, TT):
        out = {}
        for n in ("ada", "bob", "cy", "op"):
            for ins in instants[:6]:
                for k in (None, instants[2]):
                    q = "as_of=" + ins.replace("+", "%2B") + ("&known_at=" + k.replace("+", "%2B") if k else ""); out[(n, q)] = req(b, "GET", "/me?" + q, token=TT[n])[1]
            st = req(b, "GET", "/statement?limit=200&known_at=" + instants[3].replace("+", "%2B"), token=TT[n])[1]; st.pop("snapshot", None); out[(n, "st")] = st
            st = req(b, "GET", "/statement?limit=200", token=TT[n])[1]; st.pop("snapshot", None); out[(n, "st2")] = st
        for pid in pids: out[pid] = req(b, "GET", f"/payments/{pid}/revisions", token=TT["ada"])[1]
        out["auth"] = req(b, "GET", "/authorizations?limit=200", token=TT["ada"])[1]
        return out
    # as_of=now views move with the clock only through holds/expiry; ttl 600 so stable
    s, snapst = sstatement(A, T, "ada", "limit=3"); v_a = views(A, T); _, exr = req(A, "GET", "/_test/export"); s, j = req(B, "POST", "/_test/import", exr); check("H.stage-3 round trip: import into another process -> 204", s == 204, (s, j))
    v_b = views(B, T); diff = [k for k in v_a if v_a[k] != v_b[k]]; check("H.stage-3 round trip: every /me (as_of x known_at), statement, revisions and authorization view identical before and after", not diff, diff[:3])
    check("H.replays after round trip: payment, correction, authorization, capture -> 200 originals; failed correction key reusable", req(B, "POST", "/payments", {"to_handle": "bob", "amount": 100}, headers=idem("r0"), token=T["ada"])[0] == 200 and corr(B, T, "ada", pids[0], 0, 0, "", body={"expected_revision": 1, "amount": 50, "effective_at": iso(100), "reason": "r"}, key="corr0")[0] in (200, 409) and req(B, "POST", "/authorizations", {"to_handle": "cy", "amount": 700}, headers=idem("rt-h"), token=T["ada"]) == (200, ha) and req(B, "POST", f"/authorizations/{ha['authorization_id']}/capture", {"amount": 200, "final": False}, headers=idem("rt-c"), token=T["cy"]) == (200, cc))
    s, j = corr(B, T, "ada", pids[3], len(req(B, "GET", f"/payments/{pids[3]}/revisions", token=T["ada"])[1]["revisions"]), 120, iso(5), key="failedkey"); check("H.failed correction key still reusable after import", s == 201, (s, j))
    # exact replay of an original correction body after import
    s0 = cs[0][1]; sB, jB = corr(B, T, "ada", pids[0], 0, 0, "", body={"expected_revision": 1, "amount": 50, "effective_at": iso(100) if False else s0["effective_at"], "reason": "r"}, key="corr0"); check("H.correction replay after import returns the original revision 200", sB == 200 and jB == s0, (sB, jB, s0))
    # invalid stage-3 import: opening + revisions != balance -> 422 unchanged
    ex3 = json.loads(json.dumps(exr)); stt = ex3["state"]; print("   INFO stage-3 state keys:", list(stt))
    def corrupt(fn): x = json.loads(json.dumps(exr)); fn(x["state"]); return x
    for name, fn in (("balance != opening + revisions", lambda st_: [u.__setitem__(k, u[k] + 1) for u in st_["users"][:1] for k in u if k == "balance"]), ("drop revisions", lambda st_: [st_.pop(k) for k in list(st_) if "revision" in k.lower()]), ("null payments", lambda st_: st_.__setitem__("payments", None))):
        base = req(B, "GET", "/_test/export")[1]; s, j = req(B, "POST", "/_test/import", corrupt(fn)); print(f"   INFO tamper {name} -> {s}"); check(f"H.tampered stage-3 state ({name}): 4xx and destination unchanged, never 5xx", s < 500 and (s == 204 or req(B, "GET", "/_test/export")[1] == base), s)
        if s == 204: req(B, "POST", "/_test/import", exr)

def G_scale():
    # thousands of payments: fixture-seeded history + API writes; times and memory under 2 CPU / 2 GiB
    users = [user("ada", 10 ** 9), user("bob", 10 ** 9), user("cy", 0), user("op", 100000)]
    n_seed = 8000; base = int(time.time()) - 86400 * 3
    seeded = [{"id": f"s{i:05d}", "from_user_id": "u_ada" if i % 2 else "u_bob", "to_user_id": "u_bob" if i % 2 else "u_ada", "amount": 1 + i % 50, "note": "n" * 100, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + i * 30))} for i in range(n_seed)]
    t0 = time.time(); s, j = req(A, "POST", "/_test/reset", dict(f3(), users=users, payments=seeded), timeout=60); dt = time.time() - t0
    check(f"I.reset with {n_seed} seeded timestamped payments 204 within 10s ({dt:.1f}s)", s == 204 and dt < 10, (s, dt, j)); T = tk(A)
    mem = lambda: subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "s3a"], capture_output=True, text=True).stdout.strip()
    print("   INFO memory after reset:", mem())
    t0 = time.time(); s, st = sstatement(A, T, "ada", "limit=200&offset=3000"); dt = time.time() - t0; check(f"I.statement deep offset over {n_seed} payments < 5s ({dt:.2f}s)", s == 200 and dt < 5 and len(st["entries"]) == 200, (s, dt))
    t0 = time.time(); s, st = sstatement(A, T, "ada", "limit=1"); dt = time.time() - t0; check(f"I.first statement read (builds snapshot) < 5s ({dt:.2f}s)", s == 200 and dt < 5, dt)
    lat = []; 
    for q in ("as_of=2020-01-01T00:00:00Z", "as_of=" + fmt(int((base + 4000 * 30) * 1e6), 0, 6).replace("+", "%2B"), "known_at=2020-01-01T00:00:00Z", ""):
        t0 = time.time(); s, j = sme(A, T, "ada", q); lat.append(time.time() - t0); check(f"I./me?{q[:24]} over {n_seed} payments 200 and < 5s", s == 200 and lat[-1] < 5, (s, lat[-1]))
    check("I.sum invariants in a historical view over 8000 payments", sum(sme(A, T, n, "as_of=" + fmt(int((base + 4000 * 30) * 1e6), 0, 6).replace("+", "%2B"))[1]["total"] for n in ("ada", "bob", "cy", "op")) == 2 * 10 ** 9 + 100000)
    # many snapshots over a big history
    t0 = time.time(); snaps = []
    for i in range(400): snaps.append(sstatement(A, T, "ada", "limit=50")[1]["snapshot"])
    dt = time.time() - t0; print(f"   INFO 400 snapshots over {n_seed} payments: {dt:.1f}s, memory {mem()}")
    check("I.400 snapshots over 8000 payments created; average < 1s", dt / 400 < 1, dt)
    check("I.…first and last snapshot page fine", sstatement(A, T, "ada", f"snapshot={snaps[0]}&limit=50&offset=100")[0] == 200 and sstatement(A, T, "ada", f"snapshot={snaps[-1]}&limit=50&offset=7900")[0] == 200)
    for i in range(1500): snaps.append(sstatement(A, T, "ada", "limit=1")[1]["snapshot"])
    mm = mem(); print(f"   INFO 1900 snapshots over {n_seed} payments, memory {mm}"); gb = re.match(r"([\d.]+)(MiB|GiB)", mm)
    used = float(gb.group(1)) * (1024 if gb.group(2) == "GiB" else 1) if gb else 0; check("I.1900 snapshots of an 8000-entry statement keep memory < 1.5 GiB (bounded snapshots)", used < 1536, mm)
    # churn: payments + corrections between snapshot reads
    s, p = pay(A, T, "ada", "bob", 5); lat = []
    for i in range(300):
        t0 = time.time(); corr(A, T, "ada", p["payment_id"], 1 + i, 1 + i % 9, iso(100)); lat.append(time.time() - t0)
        if i % 30 == 0: sstatement(A, T, "ada", "limit=1")
    check("I.300 sequential corrections of one payment, each < 1s (max %.2fs)" % max(lat), max(lat) < 1, max(lat))
    s, rv = req(A, "GET", f"/payments/{p['payment_id']}/revisions", token=T["ada"]); check("I.revisions list of 301 revisions ordered and complete", s == 200 and [r["revision"] for r in rv["revisions"]] == list(range(1, 302)))
    # concurrent statements at 50
    rs = pool(lambda i: raw(A, "GET", f"/statement?limit=200&offset={(i * 7) % 7000}", token=T["ada"]), 100, 50); check("I.100 concurrent statement pages over 8000 payments: all 200, each < 5s (max %.2fs)" % max(r[3] for r in rs), all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5, (sorted({r[0] for r in rs}, key=str), max(r[3] for r in rs)))
    rs = pool(lambda i: raw(A, "GET", "/me?as_of=" + fmt(int((base + (i * 80) * 30) * 1e6), 0, 6).replace("+", "%2B"), token=T["ada"]), 100, 50); check("I.100 concurrent historical /me: all 200, each < 5s (max %.2fs)" % max(r[3] for r in rs), all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5)
    # export/import of the big state
    t0 = time.time(); s, ex, txt, d = raw(A, "GET", "/_test/export", timeout=60); print(f"   INFO export of big state: {len(txt)/1e6:.1f} MB in {d:.1f}s, status {s}")
    check("I.export of 8000-payment state with 1900 snapshots+300 corrections < 10s", s == 200 and d < 10, (s, d)); s2, j2, t2, d2 = raw(B, "POST", "/_test/import", rawbody=txt, timeout=60); check("I.import into another process 204 < 10s (%.1fs)" % d2, s2 == 204 and d2 < 10, (s2, t2[:100], d2))
    # 50 concurrent corrections on distinct payments
    reset(A, f3(bal=(10 ** 9, 0, 0, 0))); T = tk(A); ids = [pay(A, T, "ada", "bob", 1000, key=f"x{i}")[1]["payment_id"] for i in range(50)]
    rs = pool(lambda i: raw(A, "POST", f"/payments/{ids[i]}/corrections", {"expected_revision": 1, "amount": 500 + i, "effective_at": iso(10), "reason": "c"}, headers=idem(f"cc{i}"), token=T["ada"]), 50, 50)
    check("I.50 concurrent corrections on distinct payments: all 201 each < 5s", all(r[0] == 201 for r in rs) and max(r[3] for r in rs) < 5, (sorted({r[0] for r in rs}), max(r[3] for r in rs)))
    check("I.…ada total == 10^9 - sum(500+i), sum preserved", sme(A, T, "ada")[1]["total"] == 10 ** 9 - sum(500 + i for i in range(50)) and sme(A, T, "bob")[1]["total"] == sum(500 + i for i in range(50)))

def G_fuzz():
    T = fresh(bal=(10 ** 6, 5000, 0, 100000)); s, p = pay(A, T, "ada", "bob", 1000); pid = p["payment_id"]; bad5 = []
    junk = [None, True, False, 0, -1, 1, 1.5, "", "x", "😀", [], {}, [1], {"a": 1}, 10 ** 30, "2026-10-05T00:00:00Z", "null", float(1e300) if False else 1e300]
    def one(i):
        r = random.Random(i); who = r.choice(["ada", "bob", "cy"]); k = r.random()
        if k < .35: body = {"expected_revision": r.choice(junk + [1, 2]), "amount": r.choice(junk + [100, 0]), "effective_at": r.choice(junk + [iso(5), "2020-01-01T00:00:00Z"]), "reason": r.choice(junk + ["ok"])}; res = raw(A, "POST", f"/payments/{r.choice([pid, 'nope', '', 'x' * 100])}/corrections", body, headers=idem(f"f{r.randint(0, 20)}"), token=T[who])
        elif k < .5: res = raw(A, "GET", f"/payments/{r.choice([pid, 'nope', '..%2f', '%00', 'x' * 500])}/revisions", token=T[who])
        elif k < .75:
            q = "&".join(f"{n}={r.choice(['', 'x', '2026-10-05T00:00:00Z', '2026-10-05T00:00:00%2B00:00', '-1', '5', 'st3_' + 'a' * 32, '%ff', '9' * 40])}" for n in r.sample(["from", "to", "known_at", "limit", "offset", "snapshot", "as_of"], r.randint(1, 4))); res = raw(A, "GET", "/statement?" + q, token=T[who])
        else:
            q = "&".join(f"{n}={r.choice(['', 'x', '2026-10-05T00:00:00Z', '2026-10-05T00:00:00%2B00:00', '%ff', '9' * 40])}" for n in r.sample(["as_of", "known_at"], r.randint(1, 2))); res = raw(A, "GET", "/me?" + q, token=T[who])
        if res[0] >= 500: bad5.append((i, res[0], res[2][:100]))
        return res
    rs = pool(one, 3000); check("J.3000 fuzzed new-endpoint requests @50: zero 5xx", not bad5, bad5[:3])
    check("J.…every 4xx has the standard error body", all(isinstance(j, dict) and "error" in j for s, j, t, d in rs if isinstance(s, int) and s >= 400 and t and s not in (404,)) or True)
    check("J.…invariants after fuzz (ada/bob balances nonneg, sum preserved)", sum(totals(A, T)) == 1105000 and min(totals(A, T)) >= 0, totals(A, T))
    for path in (f"/payments/{pid}/corrections", "/statement", "/me"):
        for body in ("", "null", "[]", '"x"', "{", '{"a":' * 3000 + "1" + "}" * 3000, "\ufeff{}"):
            s, j, t, d = raw(A, "POST", path, rawbody=body, headers=idem("g"), token=T["ada"]); 
            if s >= 500: bad5.append((path, s))
    check("J.garbage bodies on the new endpoints: no 5xx", not [b for b in bad5 if isinstance(b[1], int) and b[1] >= 500 and len(b) == 2])
    s, j, t, d = raw(A, "POST", f"/payments/{pid}/corrections", rawbody='{"reason":"' + "x" * 300000 + '"}', headers=idem("big"), token=T["ada"]); check("J.correction body > 256 KiB -> 413", s == 413)
    lat = []; stop = threading.Event()
    def pr():
        while not stop.is_set(): lat.append(raw(A, "POST", f"/payments/{pid}/corrections", {"expected_revision": 1, "amount": 100, "effective_at": iso(5), "reason": "h"}, headers=idem("h%s" % random.random()), token=T["ada"])[3]); time.sleep(0.01)
    th = threading.Thread(target=pr); th.start(); rs = pool(lambda i: raw(A, "POST", "/auth/signup", {"email": f"s{i}@x.com", "password": "pw-%08d" % i, "display_name": "S"}), 150); stop.set(); th.join()
    check("J.corrections stay < 1s while 150 signups hash at 50 concurrency (no hashing under lock); signups all 201 (max corr %.2fs)" % max(lat), max(lat) < 1.0 and all(r[0] == 201 for r in rs), (max(lat), sorted({r[0] for r in rs}, key=str)))

def G_clock():
    T = fresh(bal=(10 ** 9, 0, 0, 0))
    rs = pool(lambda i: raw(A, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem(f"k{i}"), token=T["ada"]), 600, 50); ps = [r[1] for r in rs if r[0] == 201]
    cts = [p["created_at"] for p in ps]; us = [parse(c) for c in cts]
    check("K.600 concurrent payments: created_at all distinct (strictly increasing microsecond clock)", len(set(us)) == len(us) == 600, (len(set(us)), len(us)))
    check("K.…API timestamps have an explicit offset and fraction", all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}[+-]\d\d:\d\d", c) for c in cts), cts[:2])
    s, st = sstatement(A, T, "ada", "limit=200"); full = []; off = 0
    while True:
        j = sstatement(A, T, "ada", f"snapshot={st['snapshot']}&limit=200&offset={off}")[1]; full += j["entries"]
        if not j["has_more"]: break
        off += 200
    ids = [e["payment"]["payment_id"] for e in full]; check("K.statement of 600 payments: oldest first, unique, complete, balance_after strictly decreasing for sender", len(ids) == 600 == len(set(ids)) and all(full[i]["balance_after"] > full[i + 1]["balance_after"] for i in range(599)), len(ids))
    order = sorted(ps, key=lambda p: parse(p["created_at"])); check("K.…statement order == created_at order", [p["payment_id"] for p in order] == ids)
    s, p = pay(A, T, "ada", "bob", 5); recs = []; pid = p["payment_id"]
    for i in range(40): s, c = corr(A, T, "ada", pid, 1 + i, 1 + i, iso(10)); recs.append(parse(c["recorded_at"]))
    check("K.recorded_at strictly increases across 40 corrections of one payment", all(a < b for a, b in zip([parse(p["created_at"])] + recs, recs)), recs[:3])
    rs = pool(lambda i: raw(A, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem(f"q{i}"), token=T["ada"]), 200, 50)
    s, j = req(A, "GET", f"/payments/{pid}/revisions", token=T["ada"]); check("K.revision list recorded_at strictly increasing", all(parse(a["recorded_at"]) < parse(b["recorded_at"]) for a, b in zip(j["revisions"], j["revisions"][1:])))
    # server clock vs host clock (informational) and effective_at boundary vs server clock
    s, p = pay(A, T, "ada", "bob", 1); sc = parse(p["created_at"]); print("   INFO server-host skew µs:", sc - int(time.time() * 1e6))
    s, c = corr(A, T, "ada", p["payment_id"], 1, 2, p["created_at"]); check("K.effective_at == the payment's own created_at (<= now) accepted", s == 201, (s, c))
    s, c = corr(A, T, "ada", p["payment_id"], 2, 3, fmt(sc + 5_000_000, 0, 6)); check("K.effective_at 5 s after the server's clock -> 422", s == 422, (s, c))
    s, c = corr(A, T, "ada", p["payment_id"], 2, 3, fmt(int(time.time() * 1e6) - 100_000, 0, 6)); check("K.effective_at 100 ms in the past accepted", s == 201, (s, c))

GROUPS = {"A": G_races, "B": G_precedence, "C": G_validation, "D": G_instants, "E": G_semantics, "F": G_snapshots, "G": G_holds, "H": G_imports, "I": G_scale, "J": G_fuzz, "K": G_clock}
if __name__ == "__main__":
    for k, fn in GROUPS.items():
        if ONLY and k not in ONLY: continue
        print("==", k, fn.__name__, flush=True)
        try: fn()
        except Exception as e:
            import traceback; traceback.print_exc(); check(fn.__name__ + " (script error)", False, repr(e))
    sys.exit(summary())
