#!/usr/bin/env python3
"""Stage-2 API attacks. usage: attack_s2_api.py URL_A URL_B [URL_STAGE1] [groups...]   (RESETS all)
A,B = two separate stage-2 processes; URL_STAGE1 = a stage-1 (621342c) process for export compat. Groups: A..N (letters)."""
import sys, json, time, random, threading, re
from s2lib import *
A, B = sys.argv[1], sys.argv[2]; S1 = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3].startswith("http") else None
ONLY = [x for x in sys.argv[3:] if not x.startswith("http")]
def auth(b, T, amt=1000, who="ada", to="bob", key=None, **kw):
    body = {"to_handle": to, "amount": amt}; body.update(kw)
    return req(b, "POST", "/authorizations", body, headers=idem(key or "au-%s" % random.random()), token=T[who])
def cap(b, T, aid, body=None, who="bob", key=None):
    return req(b, "POST", f"/authorizations/{aid}/capture", {} if body is None else body, headers=idem(key or "cp-%s" % random.random()), token=T[who])
def void(b, T, aid, who="ada"): return req(b, "POST", f"/authorizations/{aid}/void", {}, token=T[who])
def fresh(**kw): reset(A, fx(**kw)); return toks(A)
def total(T): return sum(me(A, T[k])["total"] for k in T)

def G_races():
    T = fresh()
    paths = {"payments": ("/payments", {"to_handle": "bob", "amount": 100}, "ada"), "requests": ("/requests", {"payer_handle": "ada", "amount": 100}, "bob"),
             "splits": ("/splits", {"amount": 300, "participant_handles": ["ada", "bob", "cy"]}, "ada"),
             "settlements": ("/settlements", {"transfers": [{"from_handle": "op", "to_handle": "bob", "amount": 100}]}, "op"),
             "authorizations": ("/authorizations", {"to_handle": "bob", "amount": 100}, "ada")}
    for name, (p, b, who) in paths.items():
        T = fresh(); rs = pool(lambda i: req(A, "POST", p, b, headers=idem("k"), token=T[who]), 50); c = [s for s, _ in rs]
        check(f"A.race fresh-key x50 {name}: 1x201 + 49x200 identical", c.count(201) == 1 and c.count(200) == 49 and len({json.dumps(j, sort_keys=True) for _, j in rs}) == 1, (c.count(201), c.count(200), set(c)))
    T = fresh(); s, a = auth(A, T, 1000)
    rs = pool(lambda i: cap(A, T, a["authorization_id"], {"amount": 400}, key="same"), 50); c = [s for s, _ in rs]
    check("A.race capture same key x50: 1x201 + 49x200 identical, money moved once", c.count(201) == 1 and c.count(200) == 49 and len({json.dumps(j, sort_keys=True) for _, j in rs}) == 1 and me(A, T["bob"])["total"] == 2900 and me(A, T["ada"])["held"] == 0, (c.count(201), c.count(200), me(A, T["bob"])))
    # pay (request) path
    T = fresh(); s, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 700}, headers=idem("m"), token=T["bob"])
    rs = pool(lambda i: req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("pk"), token=T["ada"]), 50); c = [s for s, _ in rs]
    check("A.race pay same key x50", c.count(201) == 1 and c.count(200) == 49, sorted(set(c)))

def G_capture_races():
    T = fresh(); s, a = auth(A, T, 1000); aid = a["authorization_id"]
    rs = pool(lambda i: cap(A, T, aid, {}), 50); c = [s for s, _ in rs]
    check("B.double final capture, 50 distinct keys: exactly one 201, rest 409 authorization_not_open", c.count(201) == 1 and c.count(409) == 49 and all(code(j) == "authorization_not_open" for s, j in rs if s == 409), sorted(set(c)))
    check("B.…money moved once (bob +1000, ada -1000, held 0)", me(A, T["bob"])["total"] == 3500 and me(A, T["ada"])["total"] == 9000 and me(A, T["ada"])["held"] == 0)
    # extended captures sum
    T = fresh(); s, a = auth(A, T, 1000); aid = a["authorization_id"]
    rs = pool(lambda i: cap(A, T, aid, {"amount": 100, "final": False}), 50); c = [s for s, _ in rs]
    got = [j for s, j in rs if s == 201]; ada = me(A, T["ada"]); bob = me(A, T["bob"])
    check("B.50 concurrent 100-captures (final:false) on 1000 hold: exactly 10 succeed", len(got) == 10, c.count(201))
    check("B.…others are 409 not_open or 422 capture_exceeds (never 5xx)", all((s == 201) or (s == 409 and code(j) == "authorization_not_open") or (s == 422 and code(j) == "capture_exceeds_authorization") for s, j in rs), sorted(set(c)))
    check("B.…cumulative captured == 1000, closed, held 0, bob +1000", bob["total"] == 3500 and ada["held"] == 0 and ada["total"] == 9000, (ada, bob))
    s, l = req(A, "GET", "/authorizations", token=T["ada"]); x = l["authorizations"][0]
    check("B.…final state captured, captured_amount 1000, remaining 0, payment_ids has 10 distinct", x["status"] == "captured" and x["captured_amount"] == 1000 and x["remaining_amount"] == 0 and len(set(x["payment_ids"])) == 10 and x["payment_id"] == x["payment_ids"][-1], x)
    # capture vs void race
    for rnd in range(15):
        T = fresh(); s, a = auth(A, T, 1000); aid = a["authorization_id"]
        jobs = [lambda: cap(A, T, aid, {"amount": 600}), lambda: void(A, T, aid)] * 10
        with ThreadPoolExecutor(20) as ex: rs = list(ex.map(lambda f: f(), jobs))
        ada = me(A, T["ada"]); bob = me(A, T["bob"]); caps = sum(1 for s, j in rs if s == 201)
        _, l = req(A, "GET", "/authorizations", token=T["ada"]); x = l["authorizations"][0]
        ok = caps <= 1 and ada["held"] == 0 and ada["total"] + bob["total"] + me(A, T["cy"])["total"] + me(A, T["op"])["total"] == SEED_TOTAL and ((x["status"] == "captured" and caps == 1 and bob["total"] == 3100) or (x["status"] == "voided" and caps == 0 and bob["total"] == 2500))
        if not ok: check("B.capture vs void race consistent", False, (rs[:4], ada, bob, x)); break
    else: check("B.capture(600)/void race x15: either captured or voided, never both, held 0, sums preserved", True)
    # extended capture then void: partially captured void releases remainder only
    T = fresh(); s, a = auth(A, T, 1000); aid = a["authorization_id"]; cap(A, T, aid, {"amount": 300, "final": False})
    s, v = void(A, T, aid); ada = me(A, T["ada"])
    check("B.void of partially captured hold: voided, captured 300 kept, remaining 0, only remainder released", s == 200 and v["status"] == "voided" and v["captured_amount"] == 300 and v["remaining_amount"] == 0 and len(v["payment_ids"]) == 1 and ada["total"] == 9700 and ada["held"] == 0, (s, v, ada))
    check("B.void twice 200 same state; capture after void 409 not_open", void(A, T, aid)[0] == 200 and code(cap(A, T, aid, {"amount": 1})[1]) == "authorization_not_open")
    # capture vs payments storm on same payer (overdraft)
    T = fresh(); s, a = auth(A, T, 8000); aid = a["authorization_id"]
    def op(i):
        if i % 2 == 0: return ("cap", req(A, "POST", f"/authorizations/{aid}/capture", {"amount": 100, "final": False}, headers=idem(f"c{i}"), token=T["bob"]))
        return ("pay", req(A, "POST", "/payments", {"to_handle": "cy", "amount": 500}, headers=idem(f"p{i}"), token=T["ada"]))
    rs = pool(op, 100); ada = me(A, T["ada"])
    pays = sum(1 for k, (s, j) in rs if k == "pay" and s == 201); caps = sum(1 for k, (s, j) in rs if k == "cap" and s == 201)
    check("B.payments (500) cannot use held funds: only available//500 = 4 succeed; held never negative", pays <= 4 and inv_ok(ada), (pays, caps, ada))
    check("B.…ada total == 10000 - 500*pays - 100*caps", ada["total"] == 10000 - 500 * pays - 100 * caps, (ada, pays, caps))

def G_overdraft_holds():
    T = fresh(); s, a = auth(A, T, 8000)
    check("C.available after hold: 2000, held 8000, balance==total==10000", me(A, T["ada"]) == {**me(A, T["ada"]), "total": 10000, "balance": 10000, "available": 2000, "held": 8000} and me(A, T["ada"])["available"] == 2000)
    s, j = req(A, "POST", "/payments", {"to_handle": "bob", "amount": 2001}, headers=idem("p1"), token=T["ada"]); check("C.payment above available -> 409 insufficient_funds (total would cover)", s == 409 and code(j) == "insufficient_funds", (s, j))
    s, j = req(A, "POST", "/payments", {"to_handle": "bob", "amount": 2000}, headers=idem("p2"), token=T["ada"]); check("C.payment == available -> 201", s == 201, (s, j))
    s, j = auth(A, T, 1); check("C.authorize with 0 available -> 409 insufficient_funds", s == 409 and code(j) == "insufficient_funds", (s, j))
    s, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 50}, headers=idem("r"), token=T["bob"])
    s, j = req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("rp"), token=T["ada"]); check("C.paying a request while funds held -> 409 insufficient_funds, request stays pending", s == 409 and code(j) == "insufficient_funds" and req(A, "GET", "/requests?status=pending", token=T["ada"])[1]["requests"][0]["status"] == "pending", (s, j))
    s, j = req(A, "POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 1}]}, headers=idem("st"), token=T["op"]); check("C.settlement net debit of a wallet with no available -> 409 and nothing moves", s == 409 and total(T) == SEED_TOTAL and me(A, T["cy"])["total"] == 0, (s, j))
    s, j = req(A, "POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 5}, {"from_handle": "cy", "to_handle": "ada", "amount": 5}]}, headers=idem("st2"), token=T["op"]); check("C.settlement with zero NET debit for held wallet is affordable", s == 201, (s, j))
    s, j = void(A, T, a["authorization_id"]); check("C.void releases: available back to 8000", s == 200 and me(A, T["ada"])["available"] == 8000, me(A, T["ada"]))
    s, j = req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("rp2"), token=T["ada"]); check("C.request now payable after void (new key)", s == 201, (s, j))
    # capture may spend reserved money even though available is 0
    T = fresh(); s, a = auth(A, T, 10000)
    s, j = cap(A, T, a["authorization_id"], {"amount": 10000}); check("C.capture spends reserved money while payer available==0", s == 201 and me(A, T["ada"])["total"] == 0 and me(A, T["bob"])["total"] == 12500, (s, j))
    # two holds, partial coverage
    T = fresh(); s1, a1 = auth(A, T, 6000); s2, a2 = auth(A, T, 5000); check("C.second hold exceeding available -> 409", s1 == 201 and s2 == 409 and code(a2) == "insufficient_funds", (s1, s2))
    s, a3 = auth(A, T, 4000); check("C.hold == remaining available -> 201", s == 201 and me(A, T["ada"])["available"] == 0 and me(A, T["ada"])["held"] == 10000)
    # authorize storm
    T = fresh(); rs = pool(lambda i: auth(A, T, 900, key=f"s{i}"), 50); n = sum(1 for s, j in rs if s == 201); ada = me(A, T["ada"])
    check("C.50 concurrent 900-holds on 10000: exactly 11 succeed, available 100, never negative", n == 11 and ada["available"] == 100 and ada["held"] == 9900 and all(s in (201, 409) for s, _ in rs), (n, ada))
    # watcher invariant during storm of mixed ops
    T = fresh(); stop = threading.Event(); bad = []
    def watcher():
        while not stop.is_set():
            for k in ("ada", "bob", "cy", "op"):
                m = me(A, T[k])
                if not inv_ok(m): bad.append((k, m))
    ths = [threading.Thread(target=watcher) for _ in range(3)]; [t.start() for t in ths]
    def mixed(i):
        r = random.Random(i); k = r.random(); who = r.choice(["ada", "bob", "cy", "op"]); to = r.choice([x for x in ["ada", "bob", "cy", "op"] if x != who])
        if k < .3: return auth(A, T, r.randint(1, 4000), who=who, to=to, key=f"m{i}")
        if k < .55: return req(A, "POST", "/payments", {"to_handle": to, "amount": r.randint(1, 3000)}, headers=idem(f"mp{i}"), token=T[who])
        lst = req(A, "GET", "/authorizations?limit=200&status=open", token=T[who])[1]["authorizations"]
        if not lst: return (200, None)
        x = r.choice(lst); aid = x["authorization_id"]
        if k < .8: return cap(A, T, aid, {"amount": r.randint(1, max(1, x["remaining_amount"])), "final": r.random() < .3}, who=r.choice(["ada", "bob", "cy", "op"]), key=f"mc{i}")
        return void(A, T, aid, who=r.choice(["ada", "bob", "cy", "op"]))
    rs = pool(mixed, 2000); time.sleep(.3); stop.set(); [t.join() for t in ths]
    check("C.2000 mixed ops @50 with 3 watchers: invariants (balance==total, available==total-held>=0, held<=total) at every read", not bad and all(isinstance(s, int) and s < 500 for s, _ in rs), (bad[:2], sorted({str(s) for s, _ in rs})))
    check("C.…sum of totals == seeded total after storm", total(T) == SEED_TOTAL, total(T))
    ms = [me(A, T[k]) for k in T]; _, hl = req(A, "GET", "/authorizations?limit=200", token=T["ada"])
    ok = True
    for k in T:
        lst = req(A, "GET", "/authorizations?limit=200&direction=outgoing&status=open", token=T[k])[1]["authorizations"]
        if sum(x["remaining_amount"] for x in lst) != me(A, T[k])["held"]: ok = False
    check("C.…held == sum of remaining_amount of open outgoing holds, for every user", ok)
    for k in T:
        for x in req(A, "GET", "/authorizations?limit=200", token=T[k])[1]["authorizations"]:
            if x["captured_amount"] + x["remaining_amount"] > x["amount"] or x["captured_amount"] > x["amount"] or x["captured_amount"] < 0 or (x["status"] == "open" and x["remaining_amount"] != x["amount"] - x["captured_amount"]) or (x["status"] == "captured" and x["remaining_amount"] != 0):
                ok = False; print(x); break
    check("C.…every hold: captured + remaining <= amount, closed holds remaining 0", ok)

def G_idem():
    T = fresh(); b = {"to_handle": "bob", "amount": 500, "note": "n", "visibility": "private"}
    s, a = req(A, "POST", "/authorizations", b, headers=idem("K"), token=T["ada"]); aid = a["authorization_id"]
    s2, a2 = req(A, "POST", "/authorizations", {"visibility": "private", "note": "n", "amount": 5e2, "to_handle": "bob"}, headers=idem("K"), token=T["ada"]); check("D.authorization replay (reordered, 5e2) -> 200 identical", (s, s2) == (201, 200) and a == a2, (s2, a2))
    s3, j3 = req(A, "POST", "/authorizations", dict(b, amount=501), headers=idem("K"), token=T["ada"]); check("D.authorization same key different body -> 409", s3 == 409 and code(j3) == "idempotency_key_reuse")
    s3, j3 = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": -1}, headers=idem("K"), token=T["ada"]); check("D.claimed key + invalid body -> 409 (before validation)", s3 == 409, (s3, j3))
    s3, j3 = req(A, "POST", "/authorizations", dict(b, **{"extra": 1}), headers=idem("K"), token=T["ada"]); check("D.extra field changes body -> 409", s3 == 409)
    # replay after the hold changed
    cap(A, T, aid, {"amount": 200}, key="c0"); s4, a4 = req(A, "POST", "/authorizations", b, headers=idem("K"), token=T["ada"]); check("D.authorization replay after capture returns ORIGINAL body (status open, captured 0)", s4 == 200 and a4 == a and a4["status"] == "open" and a4["captured_amount"] == 0, a4)
    # capture key semantics
    T = fresh(); s, a = auth(A, T, 2000); aid = a["authorization_id"]
    s1, p1 = cap(A, T, aid, {"amount": 2000}, key="CK"); s2, p2 = cap(A, T, aid, {}, key="CK")
    check("D.capture {amount:2000} then {} same key -> 409 idempotency_key_reuse (different JSON values)", (s1, s2) == (201, 409) and code(p2) == "idempotency_key_reuse", (s1, s2, p2))
    s3, p3 = cap(A, T, aid, {"amount": 2000}, key="CK"); check("D.exact replay -> 200 identical payment after authorization closed", s3 == 200 and p3 == p1, (s3, p3))
    s3, p3 = cap(A, T, aid, {"amount": 2000, "final": True}, key="CK"); check("D.{amount,final:true} differs from {amount} -> 409", s3 == 409)
    s3, p3 = cap(A, T, aid, {"amount": -5}, key="CK"); check("D.claimed capture key + invalid body -> 409", s3 == 409, (s3, p3))
    s3, p3 = cap(A, T, aid, {"amount": 2000}, key="CK2"); check("D.new key on closed hold -> 409 authorization_not_open", s3 == 409 and code(p3) == "authorization_not_open")
    s3, p3 = cap(A, T, aid, {"amount": 2000}, who="ada", key="CK"); check("D.payer using receiver's key: 403 (own scope, forbidden)", s3 == 403, (s3, p3))
    # same key different path
    T = fresh(); s, a = auth(A, T, 1000, key="X"); s2, p = req(A, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("X"), token=T["ada"]); check("D.same key on /payments after /authorizations is a first use (201)", s2 == 201, (s2, p))
    s3, c3 = cap(A, T, a["authorization_id"], {"amount": 5}, who="bob", key="X"); check("D.receiver key X on capture independent of payer's X", s3 == 201, (s3, c3))
    # 4xx not claiming
    T = fresh(); s, j = auth(A, T, 10 ** 6, key="F"); s2, j2 = auth(A, T, 5, key="F"); check("D.insufficient_funds hold does not claim key", (s, s2) == (409, 201), (s, s2))
    s, j = cap(A, T, j2["authorization_id"], {"amount": 99}, key="F2"); s2, j3 = cap(A, T, j2["authorization_id"], {"amount": 5}, key="F2"); check("D.422 capture_exceeds does not claim key", (s, s2) == (422, 201) and code(j) == "capture_exceeds_authorization", (s, s2, j))
    s, a = auth(A, T, 100); s1, j1 = cap(A, T, a["authorization_id"], {"amount": 5}, who="cy", key="F3"); s2, j2 = cap(A, T, a["authorization_id"], {"amount": 5}, who="bob", key="F3"); check("D.403 capture does not claim key", (s1, s2) == (403, 201))
    # missing key / length
    s, j = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 1}, token=T["ada"]); check("D.authorization missing key -> 400 missing_idempotency_key", s == 400 and code(j) == "missing_idempotency_key")
    s, j = req(A, "POST", f"/authorizations/{a['authorization_id']}/capture", {}, token=T["bob"]); check("D.capture missing key -> 400", s == 400 and code(j) == "missing_idempotency_key", (s, j))
    s, j = cap(A, T, a["authorization_id"], {}, key="k" * 256); check("D.capture key 256 chars -> 422", s == 422, (s, j))
    # capture default body forms
    T = fresh(); s, a = auth(A, T, 1000); aid = a["authorization_id"]
    s, p = req(A, "POST", f"/authorizations/{aid}/capture", rawbody="", headers=idem("e"), token=T["bob"]); print("   INFO capture with empty body ->", s, code(p))
    check("D.capture with empty body no 5xx", s < 500)
    s, p = req(A, "POST", f"/authorizations/{aid}/capture", rawbody="null", headers=idem("e2"), token=T["bob"]); check("D.capture body null -> 400 malformed", s == 400, (s, p))

def G_clock():
    T = fresh(authorization_ttl_seconds=2); s, a = auth(A, T, 3000); aid = a["authorization_id"]
    t0 = time.time(); ex = a["expires_at"]
    check("E.expires_at == created_at + ttl(2s)", True)
    s, a2 = auth(A, T, 1000); check("E.second hold fine", s == 201)
    m = me(A, T["ada"]); check("E.before expiry held 4000 available 6000", m["held"] == 4000 and m["available"] == 6000, m)
    time.sleep(2.6)   # NO requests during the deadline
    m = me(A, T["ada"]); check("E.after deadline with no request in between: /me available restored 10000, held 0", m["available"] == 10000 and m["held"] == 0 and m["total"] == 10000, m)
    _, l = req(A, "GET", "/authorizations", token=T["ada"]); check("E.list shows status expired for both", all(x["status"] == "expired" and x["remaining_amount"] == 0 for x in l["authorizations"]) and len(l["authorizations"]) == 2, l)
    _, lo = req(A, "GET", "/authorizations?status=open", token=T["ada"]); _, le = req(A, "GET", "/authorizations?status=expired", token=T["bob"]); check("E.status=open excludes expired; status=expired includes (receiver too)", lo["authorizations"] == [] and len(le["authorizations"]) == 2)
    s, j = cap(A, T, aid, {"amount": 1}); check("E.capture of clock-expired -> 409 authorization_expired (or not_open)", s == 409 and code(j) in ("authorization_expired", "authorization_not_open"), (s, j)); print("   INFO capture expired code:", code(j))
    s, j = void(A, T, aid); check("E.void of expired -> 409 authorization_not_open", s == 409 and code(j) == "authorization_not_open", (s, j))
    s, j = req(A, "POST", "/payments", {"to_handle": "bob", "amount": 10000}, headers=idem("full"), token=T["ada"]); check("E.full-balance payment works after expiry released the hold", s == 201, (s, j))
    # partial capture then expiry
    T = fresh(authorization_ttl_seconds=2); s, a = auth(A, T, 3000); aid = a["authorization_id"]; cap(A, T, aid, {"amount": 1000, "final": False})
    time.sleep(2.6); m = me(A, T["ada"]); _, l = req(A, "GET", "/authorizations", token=T["bob"]); x = l["authorizations"][0]
    check("E.partially captured then expired: captured 1000 kept, only remainder released, total 9000, held 0", m["total"] == 9000 and m["held"] == 0 and x["status"] == "expired" and x["captured_amount"] == 1000 and x["remaining_amount"] == 0 and len(x["payment_ids"]) == 1, (m, x))
    # replay of creation after expiry
    T = fresh(authorization_ttl_seconds=2); s, a = auth(A, T, 100, key="rk"); time.sleep(2.6); s2, a2 = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 100}, headers=idem("rk"), token=T["ada"])
    check("E.creation replay after expiry returns original body (status open)", s2 == 200 and a2 == a, (s2, a2))
    # capture exactly at TTL boundary races
    T = fresh(authorization_ttl_seconds=1); out = []
    for i in range(8):
        s, a = auth(A, T, 10, key=f"b{i}"); time.sleep(0.93 + 0.02 * i); s2, j2 = cap(A, T, a["authorization_id"], {}, key=f"bc{i}"); m = me(A, T["ada"]); out.append((s2, code(j2)))
        if not inv_ok(m): check("E.boundary capture invariants", False, m); break
    else: check("E.captures racing the 1s TTL boundary: only 201/authorization_expired/not_open, invariants hold", all((s == 201) or (s == 409 and c in ("authorization_expired", "authorization_not_open")) for s, c in out), out)
    mm = [me(A, T[k]) for k in T]; time.sleep(1.2); check("E.sum of totals preserved after boundary captures", total(T) == SEED_TOTAL)
    # ttl default and validation
    for ttl, ok in ((None, True), (0, False), (-1, False), (1.5, False), ("600", False), (True, False), (1e2, True), (3 * 10 ** 10, False), ([], False), ({}, False), (1, True)):
        f = fx(); 
        if ttl is not None: f["authorization_ttl_seconds"] = ttl
        s, j = req(A, "POST", "/_test/reset", f); check(f"E.ttl {ttl!r} -> {'204' if ok else '422/400'}", (s == 204) == ok and (ok or s in (400, 422)), (s, j))
    reset(A, fx()); T = toks(A); s, a = auth(A, T, 100); ea = time.mktime(time.strptime(a["expires_at"][:19], "%Y-%m-%dT%H:%M:%S")); ca = time.mktime(time.strptime(a["created_at"][:19], "%Y-%m-%dT%H:%M:%S"))
    check("E.default ttl 600s (expires_at - created_at)", abs((ea - ca) - 600) <= 1, a)
    check("E.timestamps RFC3339 with explicit offset", re.fullmatch(r".*(Z|[+-]\d\d:\d\d)$", a["expires_at"]) is not None and re.search(r"[+-]\d\d:\d\d$", a["created_at"]) is not None, a)

def G_seeded():
    past = "2020-01-01T00:00:00+00:00"; fut = "2099-01-01T00:00:00+00:00"
    def au(i, st, ex, amt=1000, frm="u_ada", to="u_bob"): return {"id": i, "from_user_id": frm, "to_user_id": to, "amount": amt, "note": "s", "visibility": "public", "status": st, "expires_at": ex}
    f = fx(authorizations=[au("a1", "open", fut, 2000), au("a2", "open", past, 5000), au("a3", "captured", fut, 3000), au("a4", "voided", fut, 3000), au("a5", "expired", fut, 3000)])
    reset(A, f); T = toks(A); m = me(A, T["ada"])
    check("F.seeded: only unexpired open holds: held 2000, available 8000 (past-open expired, captured/voided/expired hold nothing)", m["held"] == 2000 and m["available"] == 8000 and m["total"] == 10000, m)
    _, l = req(A, "GET", "/authorizations?limit=200", token=T["ada"]); st = {x["authorization_id"]: x["status"] for x in l["authorizations"]}
    check("F.seeded statuses: a2 (open, past expires_at) reads expired; others as seeded", st.get("a1") == "open" and st.get("a2") == "expired" and st.get("a3") == "captured" and st.get("a4") == "voided" and st.get("a5") == "expired", st)
    check("F.seeded ids preserved, receiver sees them", {x["authorization_id"] for x in req(A, "GET", "/authorizations?limit=200", token=T["bob"])[1]["authorizations"]} >= {"a1", "a2"})
    s, j = cap(A, T, "a1", {"amount": 500, "final": False}); check("F.capture seeded hold a1", s == 201 and j["authorization_id"] == "a1", (s, j))
    s, j = cap(A, T, "a2", {}); check("F.capture seeded past-open -> 409 expired/not_open", s == 409, (s, j))
    s, j = cap(A, T, "a3", {}); check("F.capture seeded captured -> 409 authorization_not_open", s == 409 and code(j) == "authorization_not_open", (s, j))
    s, j = cap(A, T, "a5", {}); print("   INFO capture seeded expired(with future expires_at) ->", code(j)); check("F.capture seeded expired -> 409", s == 409)
    s, j = void(A, T, "a4"); check("F.void seeded voided -> 200", s == 200 and j["status"] == "voided")
    s, j = void(A, T, "a3"); check("F.void seeded captured -> 409", s == 409 and code(j) == "authorization_not_open")
    # oversubscribed -> 422 changes nothing
    reset(A, fx()); T0 = toks(A); tk = T0["ada"]
    over = fx(authorizations=[au("o1", "open", fut, 6000), au("o2", "open", fut, 4001)])
    s, j = req(A, "POST", "/_test/reset", over); check("F.oversubscribed seeded holds (10001 > 10000) -> 422 validation_failed", s == 422 and code(j) == "validation_failed", (s, j))
    check("F.…reset changed nothing (old token still works)", req(A, "GET", "/me", token=tk)[0] == 200)
    ok = fx(authorizations=[au("o1", "open", fut, 6000), au("o2", "open", fut, 4000)]); s, j = req(A, "POST", "/_test/reset", ok); check("F.exactly 10000 of holds == balance accepted", s == 204, (s, j)); T = toks(A); check("F.…available 0", me(A, T["ada"])["available"] == 0)
    s, j = req(A, "POST", "/_test/reset", fx(authorizations=[au("o1", "open", fut, 6000), au("o2", "open", past, 4001), au("o3", "voided", fut, 99999)])); check("F.expired/voided holds don't count toward oversubscription -> 204", s == 204, (s, j))
    for name, a in (("bad status", au("x", "frozen", fut)), ("unknown payer", au("x", "open", fut, frm="zz")), ("unknown receiver", au("x", "open", fut, to="zz")), ("same party", au("x", "open", fut, frm="u_ada", to="u_ada")), ("bad expires_at", au("x", "open", "tomorrow")), ("amount 0", au("x", "open", fut, 0)), ("amount float", au("x", "open", fut, 1.5)), ("dup id", None)):
        auths = [a] if a else [au("d", "open", fut, 1), au("d", "open", fut, 1)]
        s, j = req(A, "POST", "/_test/reset", fx(authorizations=auths)); check(f"F.invalid seeded authorization ({name}) -> 422/400, no 5xx", s in (400, 422), (s, j))
    s, j = req(A, "POST", "/_test/reset", fx()); check("F.fixture without authorizations key -> empty list", s == 204 and req(A, "GET", "/authorizations", token=toks(A)["ada"])[1]["authorizations"] == [])
    s, j = req(A, "POST", "/_test/reset", fx(authorizations=None)); print("   INFO authorizations:null ->", s); check("F.authorizations:null no 5xx", s < 500)
    s, j = req(A, "POST", "/_test/reset", fx(authorizations={})); check("F.authorizations:{} no 5xx", s < 500)
    # seeded hold that expires a little later (an hour+ future) behaves as open now; with no clock tricks
    f = fx(authorizations=[au("n1", "open", "2026-10-04T23:59:59+00:00", 1000)]); s, j = req(A, "POST", "/_test/reset", f); print("   INFO near-future seeded ->", s)
    # explicit offsets
    f = fx(authorizations=[au("z1", "open", "2099-01-01T00:00:00Z", 500), au("z2", "open", "2099-01-01T05:30:00+05:30", 500), au("z3", "open", "2019-01-01T05:30:00+05:30", 500)]); s, j = req(A, "POST", "/_test/reset", f); T = toks(A) if s == 204 else None
    if T: check("F.Z and +05:30 offsets parsed (z3 past -> expired): held 1000", me(A, T["ada"])["held"] == 1000, me(A, T["ada"]))
    else: check("F.offset forms accepted", False, (s, j))

def G_validation():
    T = fresh(); a = T["ada"]; n = [0]
    def az(body, **k): n[0] += 1; return req(A, "POST", "/authorizations", rawbody=body if isinstance(body, str) else json.dumps(body), headers=idem(f"v{n[0]}"), token=k.get("tok", a))
    for g in ("1", "1e3", "1000.0", "10e-1", "1E+2"):
        s, j = az('{"to_handle":"bob","amount":%s}' % g); check(f"G.authorize amount {g} accepted", s == 201, (s, j))
    for g in ("0", "-0", "-1", "1.5", "1000000001", "true", '"5"', "null", "[]", "1e999", "NaN", "01", "+1"):
        s, j = az('{"to_handle":"bob","amount":%s}' % g); check(f"G.authorize amount {g} rejected 4xx", s in (400, 422) and code(j) in ("validation_failed", "malformed_request"), (s, j))
    s, j = az({"to_handle": "ada", "amount": 1}); check("G.authorize to self -> 422 self_payment", s == 422 and code(j) == "self_payment", (s, j))
    s, j = az({"to_handle": "zzz", "amount": 1}); check("G.authorize unknown handle -> 404", s == 404)
    s, j = az({"to_handle": "bob", "amount": 1, "note": "x" * 201}); check("G.authorize note 201 -> 422", s == 422)
    s, j = az({"to_handle": "bob", "amount": 1, "note": None}); check("G.authorize note null -> 422", s == 422)
    s, j = az({"to_handle": "bob", "amount": 1, "visibility": "x"}); check("G.authorize visibility x -> 422", s == 422)
    s, j = az({"to_handle": 5, "amount": 1}); check("G.authorize to_handle number -> 400/422", s in (400, 422))
    s, j = az({"amount": 1}); check("G.authorize missing to_handle -> 422", s == 422)
    s, j = az("{bad"); check("G.authorize bad JSON -> 400", s == 400)
    s, j = az("[]"); check("G.authorize array body -> 400", s == 400)
    note = "é😀<b>\u202e"; s, j = az({"to_handle": "bob", "amount": 1, "note": note}); check("G.authorize note verbatim unicode", s == 201 and j["note"] == note)
    s, j = az({"to_handle": "bob", "amount": 1}, tok="nope"); check("G.authorize unauthenticated 401", s == 401)
    s, j = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 1}, headers=idem("u")); check("G.authorize no token 401", s == 401 and code(j) == "unauthenticated")
    check("G.default visibility public, status open, payment_id null, payment_ids [], captured 0", (lambda x: x["visibility"] == "public" and x["status"] == "open" and x["payment_id"] is None and x["payment_ids"] == [] and x["captured_amount"] == 0 and x["remaining_amount"] == x["amount"] and x["currency"] == "EUR")(az({"to_handle": "bob", "amount": 7})[1]))
    # capture validation
    T = fresh(); s, h = auth(A, T, 1000); aid = h["authorization_id"]; k = [0]
    def cp(body, who="bob", aid_=None):
        k[0] += 1; return req(A, "POST", f"/authorizations/{aid_ or aid}/capture", rawbody=body if isinstance(body, str) else json.dumps(body), headers=idem(f"cv{k[0]}"), token=T[who])
    for body, exp in (({"amount": 0}, 422), ({"amount": -1}, 422), ({"amount": 1.5}, 422), ({"amount": "5"}, 422), ({"amount": True}, 422), ({"amount": None}, 422), ({"amount": 1001}, 422), ({"amount": 10 ** 10}, 422), ('{"amount":1e0}', 201)):
        s, j = cp(body); check(f"G.capture {body!r} -> {exp}" + (" capture_exceeds" if body == {"amount": 1001} else ""), s == exp and (body != {"amount": 1001} or code(j) == "capture_exceeds_authorization"), (s, j))
    for fin in ('"true"', "1", "null", "0", "[]"):
        s, j = cp('{"amount":1,"final":%s}' % fin); print(f"   INFO final={fin} ->", s, code(j)); check(f"G.capture final={fin} (non-boolean) -> 400/422, no 5xx, no money moved", s in (400, 422), (s, j))
    check("G.…rejected captures moved no money (only the 1-unit capture happened)", me(A, T["bob"])["total"] == 2501 and me(A, T["ada"])["held"] == 0, (me(A, T["bob"]), me(A, T["ada"])))
    T = fresh(); s, h = auth(A, T, 1000); aid = h["authorization_id"]
    s, j = cp({}, who="ada"); check("G.payer capture -> 403", s == 403 and code(j) == "forbidden", (s, j))
    s, j = cp({}, who="cy"); check("G.third-party capture -> 403 (not 404)", s == 403, (s, j))
    s, j = cp({}, aid_="nope"); check("G.unknown authorization capture -> 404", s == 404)
    s, j = cp({"amount": 5000}, who="cy"); check("G.third party + exceeding amount -> 403 first", s == 403, (s, j))
    s, j = cp({"amount": 5000}); check("G.receiver exceeding -> 422 capture_exceeds_authorization", s == 422 and code(j) == "capture_exceeds_authorization")
    s, j = void(A, T, aid, who="bob"); check("G.receiver void -> 403", s == 403 and code(j) == "forbidden", (s, j))
    s, j = void(A, T, aid, who="cy"); check("G.third-party void -> 403", s == 403, (s, j))
    s, j = void(A, T, "nope"); check("G.void unknown -> 404", s == 404)
    s, j = req(A, "POST", f"/authorizations/{aid}/void", None, token=None); check("G.void no token 401", s == 401)
    check("G.third party list does not see it; receiver and payer do", req(A, "GET", "/authorizations", token=T["cy"])[1]["authorizations"] == [] and len(req(A, "GET", "/authorizations", token=T["bob"])[1]["authorizations"]) == 1)
    s, j = cp({"amount": 1000, "final": False}); check("G.final:false capturing entire remainder closes it", s == 201 and req(A, "GET", "/authorizations", token=T["ada"])[1]["authorizations"][0]["status"] == "captured")
    s, j = cp({"amount": 1}); check("G.capture on closed -> 409 not_open (even with 'exceeds')", s == 409 and code(j) == "authorization_not_open", (s, j))
    # capture payment shape & feed
    T = fresh(); s, h = auth(A, T, 1000, note="déposit", visibility="private"); aid = h["authorization_id"]
    s, f0 = req(A, "GET", "/activity", token=T["ada"]); check("G.open authorization absent from /activity for both parties", f0["payments"] == [] and req(A, "GET", "/activity", token=T["bob"])[1]["payments"] == [])
    s, p = cap(A, T, aid, {"amount": 400, "final": False})
    check("G.capture payment: shape, amount, note/visibility copied, authorization_id set, request_id null, settlement_id null", s == 201 and p["amount"] == 400 and p["note"] == "déposit" and p["visibility"] == "private" and p["authorization_id"] == aid and p["request_id"] is None and p["from_handle"] == "ada" and p["to_handle"] == "bob" and p["currency"] == "EUR" and p["payment_id"] and "created_at" in p, p)
    check("G.private capture payment hidden from third party, visible to both", req(A, "GET", "/activity", token=T["cy"])[1]["payments"] == [] and len(req(A, "GET", "/activity", token=T["ada"])[1]["payments"]) == 1 and len(req(A, "GET", "/activity", token=T["bob"])[1]["payments"]) == 1)
    s, q = req(A, "POST", "/payments", {"to_handle": "cy", "amount": 1}, headers=idem("pp"), token=T["ada"]); check("G.ordinary payment has authorization_id null", q.get("authorization_id", "MISSING") is None, q)
    s, l = req(A, "GET", "/authorizations", token=T["ada"]); x = l["authorizations"][0]; check("G.open partially captured: captured 400, remaining 600, payment_id latest, payment_ids [id]", x["status"] == "open" and x["captured_amount"] == 400 and x["remaining_amount"] == 600 and x["payment_id"] == p["payment_id"] and x["payment_ids"] == [p["payment_id"]], x)
    s, p2 = cap(A, T, aid, {"final": False}); check("G.omitted amount defaults to remainder (600) and closes", s == 201 and p2["amount"] == 600 and req(A, "GET", "/authorizations", token=T["ada"])[1]["authorizations"][0]["payment_ids"] == [p["payment_id"], p2["payment_id"]])
    # extended then final smaller releases remainder
    T = fresh(); s, h = auth(A, T, 1000); aid = h["authorization_id"]; cap(A, T, aid, {"amount": 100, "final": False}); s, p = cap(A, T, aid, {"amount": 100}); m = me(A, T["ada"])
    check("G.final capture of 100 after 100 non-final: captured 200, remainder 800 released immediately", s == 201 and m["held"] == 0 and m["available"] == 9800 and req(A, "GET", "/authorizations", token=T["ada"])[1]["authorizations"][0]["status"] == "captured", m)
    # listing params
    for q, exp in (("direction=x", 422), ("status=x", 422), ("limit=0", 422), ("limit=201", 422), ("offset=-1", 422), ("limit=1e2", 422), ("limit=4.0", 422), ("direction=incoming&status=captured", 200), ("foo=1", 200)):
        s, j = req(A, "GET", "/authorizations?" + q, token=T["ada"]); check(f"G.GET /authorizations?{q} -> {exp}", s == exp, (s, j))
    check("G.GET /authorizations no auth -> 401", req(A, "GET", "/authorizations")[0] == 401)
    T = fresh(); [auth(A, T, 10, key=f"l{i}") for i in range(5)]
    _, l1 = req(A, "GET", "/authorizations?limit=2", token=T["ada"]); _, l2 = req(A, "GET", "/authorizations?limit=2&offset=4", token=T["ada"]); _, l3 = req(A, "GET", "/authorizations?limit=5", token=T["ada"])
    check("G.pagination has_more true/false/false; newest first", l1["has_more"] is True and len(l1["authorizations"]) == 2 and l2["has_more"] is False and len(l2["authorizations"]) == 1 and l3["has_more"] is False and [x["created_at"] for x in l3["authorizations"]] == sorted([x["created_at"] for x in l3["authorizations"]], reverse=True))
    check("G.direction filters", len(req(A, "GET", "/authorizations?direction=outgoing", token=T["ada"])[1]["authorizations"]) == 5 and req(A, "GET", "/authorizations?direction=incoming", token=T["ada"])[1]["authorizations"] == [] and len(req(A, "GET", "/authorizations?direction=incoming", token=T["bob"])[1]["authorizations"]) == 5)

def G_me_and_stage1():
    T = fresh(); m = me(A, T["ada"])
    check("H./me keys: user_id, display_name, handle, balance, total, available, held, currency, minor_units; no holds -> all agree", set(m) >= {"user_id", "display_name", "handle", "balance", "total", "available", "held", "currency", "minor_units"} and m["balance"] == m["total"] == m["available"] == 10000 and m["held"] == 0, m)
    for cur, mu in (("JPY", 0), ("BHD", 3)):
        reset(A, fx(currency=cur, minor_units=mu)); T = toks(A); s, h = auth(A, T, 1000); m = me(A, T["ada"]); check(f"H.{cur} authorization/amount semantics minor_units {mu}", h["currency"] == cur and m["held"] == 1000 and m["available"] == 9000)
    # settlements exact net semantic with holds, 32 transfers
    T = fresh(); auth(A, T, 90000, who="op", to="cy"); s, j = req(A, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "bob", "amount": 10001}]}, headers=idem("s1"), token=T["op"]); check("H.settlement debit above op's available (10000) -> 409 though total 100000", s == 409 and total(T) == SEED_TOTAL, (s, j))
    s, j = req(A, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "bob", "amount": 10000}, {"from_handle": "bob", "to_handle": "cy", "amount": 12500}]}, headers=idem("s2"), token=T["op"]); check("H.settlement net-affordable chain with holds -> 201", s == 201, (s, j))
    s, j = req(A, "POST", "/settlements", {"transfers": [{"from_handle": "cy", "to_handle": "ada", "amount": 12500}]}, headers=idem("s3"), token=T["op"]); check("H.settlement member credit lands, constituents carry authorization_id null", s == 201 and all(p.get("authorization_id", "MISSING") is None for p in j["payments"]), (s, j))
    # request pay uses available; request payment authorization_id null
    s, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 100}, headers=idem("rq"), token=T["bob"]); s, p = req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("rqp"), token=T["ada"]); check("H.request payment: authorization_id null, request_id set", s == 201 and p["authorization_id"] is None and p["request_id"] == rq["request_id"], p)
    s, j = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 1}, headers=idem("x"), token=T["ada"]); print("   INFO authorizing request out of scope: n/a")

def G_export():
    T = fresh(authorization_ttl_seconds=900); s, h1 = auth(A, T, 3000, key="ak1", note="n1", visibility="private"); s, h2 = auth(A, T, 1000, key="ak2")
    s, p = cap(A, T, h1["authorization_id"], {"amount": 1000, "final": False}, key="ck1")
    s, h3 = auth(A, T, 500, key="ak3"); void(A, T, h3["authorization_id"])
    s, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 77}, headers=idem("rqk"), token=T["bob"])
    s, ex, txt, d = raw(A, "GET", "/_test/export"); check("I.export 200 shape", s == 200 and ex["track"] == "pocketful" and ex["format_version"] == 1 and "state" in ex)
    before = {k: me(A, T[k]) for k in T}; list_before = req(A, "GET", "/authorizations?limit=200", token=T["ada"])[1]
    s, j = req(B, "POST", "/_test/import", rawbody=txt); check("I.import into separate process -> 204", s == 204, (s, j))
    check("I.B: /me identical for every user (total, available, held)", {k: me(B, T[k]) for k in T} == before)
    check("I.B: authorizations list identical (ids, expires_at, captured, payment_ids, created_at)", req(B, "GET", "/authorizations?limit=200", token=T["ada"])[1] == list_before)
    check("I.B: replay authorization key -> 200 original", req(B, "POST", "/authorizations", {"to_handle": "bob", "amount": 3000, "note": "n1", "visibility": "private"}, headers=idem("ak1"), token=T["ada"]) == (200, h1))
    check("I.B: replay capture key -> 200 original payment", req(B, "POST", f"/authorizations/{h1['authorization_id']}/capture", {"amount": 1000, "final": False}, headers=idem("ck1"), token=T["bob"]) == (200, p))
    s, j = req(B, "POST", f"/authorizations/{h1['authorization_id']}/capture", {"amount": 1000}, headers=idem("ck1"), token=T["bob"]); check("I.B: capture key reused with different body -> 409", s == 409, (s, j))
    s, p2 = req(B, "POST", f"/authorizations/{h1['authorization_id']}/capture", {"amount": 2000}, headers=idem("ck2"), token=T["bob"]); check("I.B: imported open hold capturable for remainder (2000) once; held released", s == 201 and me(B, T["ada"])["held"] == 1000 and me(B, T["ada"])["total"] == 10000 - 1000 - 2000, (s, p2, me(B, T["ada"])))
    s, j = req(B, "POST", f"/authorizations/{h1['authorization_id']}/capture", {"amount": 1}, headers=idem("ck3"), token=T["bob"]); check("I.B: closed after final -> 409 not_open", s == 409 and code(j) == "authorization_not_open")
    check("I.B: voided hold stays voided, void again 200", req(B, "POST", f"/authorizations/{h3['authorization_id']}/void", {}, token=T["ada"])[1]["status"] == "voided")
    s, j = req(B, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("pq"), token=T["ada"]); check("I.B: imported pending request payable", s == 201, (s, j))
    s, e2 = req(A, "GET", "/_test/export"); req(B, "POST", "/_test/import", e2); s, e3 = req(B, "GET", "/_test/export"); check("I.export->import->export byte-stable (A->B)", e3 == e2 and True)
    # hold expiry after import: ttl preserved; hold created with ttl 3 then exported, imported after expiry
    T = fresh(authorization_ttl_seconds=3); s, h = auth(A, T, 4000, key="exp1"); s, ex, txt, d = raw(A, "GET", "/_test/export"); time.sleep(3.5)
    s, j = req(B, "POST", "/_test/import", rawbody=txt); m = me(B, T["ada"]); _, l = req(B, "GET", "/authorizations", token=T["ada"])
    check("I.import of an export whose hold expired meanwhile: hold expired, available full (expiry from absolute expires_at)", s == 204 and m["available"] == 10000 and m["held"] == 0 and l["authorizations"][0]["status"] == "expired", (s, m, l))
    s, a2 = req(B, "POST", "/authorizations", {"to_handle": "bob", "amount": 100}, headers=idem("post-import"), token=T["ada"]); ea = a2["expires_at"]; ca = a2["created_at"]
    ttl = time.mktime(time.strptime(ea[:19], "%Y-%m-%dT%H:%M:%S")) - time.mktime(time.strptime(ca[:19], "%Y-%m-%dT%H:%M:%S")); check("I.ttl setting survives import (new holds expire after 3s, not 600)", abs(ttl - 3) <= 1, ttl)
    # invalid state with bad authorizations
    T = fresh(); s, h = auth(A, T, 1000); s, ex = req(A, "GET", "/_test/export"); base = raw(A, "GET", "/_test/export")[2]
    def tamper(fn): x = json.loads(json.dumps(ex)); fn(x["state"]); return x
    st = ex["state"]; print("   INFO state keys:", list(st))
    for name, fn in (("drop authorizations", lambda s_: [s_.pop(k) for k in list(s_) if "author" in k.lower()]), ("oversubscribe", lambda s_: [s_[k][0].__setitem__("amount", 99999999) for k in s_ if "author" in k.lower() and isinstance(s_[k], list) and s_[k]]), ("null auth", lambda s_: [s_.__setitem__(k, None) for k in list(s_) if "author" in k.lower()])):
        s, j = req(A, "POST", "/_test/import", tamper(fn)); print(f"   INFO tamper {name} -> {s}"); check(f"I.tampered import ({name}): 4xx and unchanged, or accepted consistently, never 5xx", s < 500 and (s == 204 or raw(A, "GET", "/_test/export")[2] == base), s)
        if s == 204: req(A, "POST", "/_test/import", ex)
    # concurrent exports under hold storm are atomic
    T = fresh(); stop = threading.Event(); bad = []
    def w(i):
        r = random.Random(i)
        while not stop.is_set():
            who = r.choice(["ada", "bob", "cy", "op"]); to = r.choice([x for x in ["ada", "bob", "cy", "op"] if x != who])
            raw(A, "POST", "/authorizations" if r.random() < .5 else "/payments", {"to_handle": to, "amount": r.randint(1, 2000)}, headers=idem(f"x{i}{r.random()}"), token=T[who])
    ths = [threading.Thread(target=w, args=(i,)) for i in range(30)]; [t.start() for t in ths]; snaps = 0
    t_end = time.time() + 8
    while time.time() < t_end:
        s, e, t, d = raw(A, "GET", "/_test/export")
        if s != 200: bad.append(s); continue
        snaps += 1
        users = e["state"]["users"]; tot = sum(u["balance"] for u in users)
        if tot != SEED_TOTAL or min(u["balance"] for u in users) < 0: bad.append(("sum", tot))
    stop.set(); [t.join() for t in ths]; check(f"I.{snaps} exports during hold/payment storm: sum of totals == seeded, no negative", snaps > 3 and not bad, bad[:3])
    # import then B invariants
    s, e, t, d = raw(A, "GET", "/_test/export"); req(B, "POST", "/_test/import", rawbody=t); Tb = {k: login(B, f"{k}@example.com", "hunter2hunter2" if k == "cy" else "correct horse") for k in ("ada", "bob", "cy", "op")}
    ms = [me(B, Tb[k]) for k in Tb]; check("I.after storm export→import: every user's invariants hold and sum == seeded", all(inv_ok(m) for m in ms) and sum(m["total"] for m in ms) == SEED_TOTAL, ms)

def G_stage1_compat():
    if not S1: print("   (no stage-1 URL given: skipped)"); return
    reset(S1, {k: v for k, v in fx().items()}); T1 = {k: login(S1, f"{k}@example.com", "hunter2hunter2" if k == "cy" else "correct horse") for k in ("ada", "bob", "cy", "op")}
    s, p = req(S1, "POST", "/payments", {"to_handle": "bob", "amount": 100, "note": "old"}, headers=idem("lostk"), token=T1["ada"])
    s, rq = req(S1, "POST", "/requests", {"payer_handle": "ada", "amount": 300}, headers=idem("oldr"), token=T1["bob"])
    s, sp = req(S1, "POST", "/splits", {"amount": 10, "participant_handles": ["ada", "bob", "cy"]}, headers=idem("olds"), token=T1["ada"])
    s, st = req(S1, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 5}]}, headers=idem("oldst"), token=T1["op"])
    s, su = req(S1, "POST", "/auth/signup", {"email": "old@x.com", "password": "oldoldold", "display_name": "O"})
    s, ex = req(S1, "GET", "/_test/export"); s1bal = {k: req(S1, "GET", "/me", token=T1[k])[1]["balance"] for k in T1}
    s, j = req(A, "POST", "/_test/import", ex); check("J.stage-1 export imports into stage-2 -> 204", s == 204, (s, j))
    if s != 204: return
    check("J.stage-1 tokens valid; /me has total==balance, held 0, available==balance", all((lambda m: m["balance"] == s1bal[k] and m["total"] == m["balance"] and m["available"] == m["balance"] and m["held"] == 0)(me(A, T1[k])) for k in T1))
    check("J.stage-1 logins (seeded + signup) work", login(A, "ada@example.com") and login(A, "old@x.com", "oldoldold"))
    check("J.old payment replay 200 identical (body has authorization_id? compare as JSON value)", req(A, "POST", "/payments", {"to_handle": "bob", "amount": 100, "note": "old"}, headers=idem("lostk"), token=T1["ada"])[0] == 200)
    s, r2 = req(A, "POST", "/payments", {"to_handle": "bob", "amount": 100, "note": "old"}, headers=idem("lostk"), token=T1["ada"]); print("   INFO old replay body:", r2 == p, "| differences:", {k: (p.get(k), r2.get(k)) for k in set(p) | set(r2) if p.get(k) != r2.get(k)})
    check("J.old replay different body -> 409", req(A, "POST", "/payments", {"to_handle": "bob", "amount": 101, "note": "old"}, headers=idem("lostk"), token=T1["ada"])[0] == 409)
    s, j = req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("pq"), token=T1["ada"]); check("J.old pending request payable once", s == 201 and (lambda m: m["total"] == s1bal["ada"] - 300)(me(A, T1["ada"])), (s, j))
    check("J.old split / settlement replays 200", req(A, "POST", "/splits", {"amount": 10, "participant_handles": ["ada", "bob", "cy"]}, headers=idem("olds"), token=T1["ada"])[0] == 200 and req(A, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 5}]}, headers=idem("oldst"), token=T1["op"])[0] == 200)
    s, h = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 100}, headers=idem("newauth"), token=T1["ada"]); check("J.new default ttl after import of stage-1 export is 600 (no ttl in old state)", s == 201 and abs((time.mktime(time.strptime(h["expires_at"][:19], "%Y-%m-%dT%H:%M:%S")) - time.mktime(time.strptime(h["created_at"][:19], "%Y-%m-%dT%H:%M:%S"))) - 600) <= 1, (s, h))
    # stage-1 export with a pre-existing hold in A? import of stage-1 export REPLACES holds
    check("J.stage-1 import replaced prior stage-2 holds (list has only the new one)", len(req(A, "GET", "/authorizations", token=T1["ada"])[1]["authorizations"]) == 1)

def G_fuzz():
    T = fresh(); hs = ["ada", "bob", "cy", "op", "zzz", "ADA", ""]; amts = [1, 0, -1, 5, 100, 10 ** 9, 10 ** 10, "5", 1.5, None, True, 333, 1000]; bad5 = []
    def one(i):
        r = random.Random(i); who = r.choice(list(T)); tok = T[who]; k = f"f{r.randint(0, 30)}"; op = r.choice(["auth", "cap", "void", "list", "pay", "me", "capbad"])
        if op == "auth": res = raw(A, "POST", "/authorizations", {"to_handle": r.choice(hs), "amount": r.choice(amts), "visibility": r.choice(["public", "private", "x"])}, headers=idem(k), token=tok)
        elif op in ("cap", "void", "capbad"):
            lst = req(A, "GET", "/authorizations?limit=200", token=tok)[1]
            if not lst or not lst["authorizations"]: return (200, None, b"", 0)
            aid = r.choice(lst["authorizations"])["authorization_id"]
            if op == "void": res = raw(A, "POST", f"/authorizations/{aid}/void", {}, token=tok)
            else:
                body = r.choice([{}, {"amount": r.choice(amts)}, {"amount": r.choice(amts), "final": r.choice([True, False, "x", None, 1])}, {"final": False}]) if op == "capbad" else {"amount": r.randint(1, 500), "final": r.random() < .5}
                res = raw(A, "POST", f"/authorizations/{aid}/capture", body, headers=idem(k), token=tok)
        elif op == "list": res = raw(A, "GET", f"/authorizations?direction={r.choice(['incoming', 'outgoing', 'x', ''])}&status={r.choice(['open', 'expired', 'x'])}&limit={r.choice([1, 50, 0, 201])}", token=tok)
        elif op == "me": res = raw(A, "GET", "/me", token=tok)
        else: res = raw(A, "POST", "/payments", {"to_handle": r.choice(hs), "amount": r.choice(amts)}, headers=idem(k), token=tok)
        if res[0] >= 500: bad5.append((op, res[0], res[2][:100]))
        return res
    rs = pool(one, 4000); ms = [me(A, T[k]) for k in T]
    check("K.4000 fuzzed ops @50: zero 5xx", not bad5, bad5[:3])
    check("K.…every error carries the standard error body", all(isinstance(j, dict) and "error" in j for s, j, t, d in rs if isinstance(s, int) and s >= 400 and t))
    check("K.…invariants and sum == seeded total after fuzz", all(inv_ok(m) for m in ms) and sum(m["total"] for m in ms) == SEED_TOTAL, ms)
    check("K.…max latency < 5s", max(r[3] for r in rs) < 5, max(r[3] for r in rs))
    big = '{"to_handle":"bob","amount":1,"pad":"' + "x" * 300000 + '"}'; s, j, t, d = raw(A, "POST", "/authorizations", rawbody=big, headers=idem("big"), token=T["ada"]); check("K.authorization body > 256KiB -> 413", s == 413, s)
    s, j, t, d = raw(A, "POST", "/authorizations/x/capture", rawbody=big, headers=idem("big2"), token=T["bob"]); check("K.capture body > 256KiB -> 413", s == 413, s)
    lat = []; stop = threading.Event()
    def pr():
        while not stop.is_set(): lat.append(raw(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 1}, headers=idem("h%s" % random.random()), token=T["ada"])[3]); time.sleep(0.01)
    th = threading.Thread(target=pr); th.start(); rs = pool(lambda i: raw(A, "POST", "/auth/signup", {"email": f"s{i}@x.com", "password": "pw-%08d" % i, "display_name": "S"}), 150); stop.set(); th.join()
    check("K.holds stay < 1s while 150 signups hash at 50 concurrency (no hashing under lock); signups all 201", max(lat) < 1.0 and all(r[0] == 201 for r in rs), (max(lat), sorted({r[0] for r in rs}, key=str)))

GROUPS = {"A": G_races, "B": G_capture_races, "C": G_overdraft_holds, "D": G_idem, "E": G_clock, "F": G_seeded, "G": G_validation, "H": G_me_and_stage1, "I": G_export, "J": G_stage1_compat, "K": G_fuzz}
if __name__ == "__main__":
    for k, fn in GROUPS.items():
        if ONLY and k not in ONLY: continue
        print("==", k, fn.__name__, flush=True)
        try: fn()
        except Exception as e:
            import traceback; traceback.print_exc(); check(fn.__name__ + " (script error)", False, repr(e))
    sys.exit(summary())
