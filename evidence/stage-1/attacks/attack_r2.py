#!/usr/bin/env python3
"""[r2 copy: expectations for body caps updated] Black-box attacks on Pocketful stage 1.  usage: attack.py http://HOST:PORT   (RESETS state)
Prints PASS/FAIL per attack; exit 1 if any FAIL."""
import sys, json, threading, http.client, urllib.parse, time, random, re
from concurrent.futures import ThreadPoolExecutor
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
U = urllib.parse.urlparse(BASE)
RES = []
PIPE = [0]
TL = threading.local()

def raw(method, path, body=None, headers=None, token=None, rawbody=None, timeout=15):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    if headers: h.update(headers)
    data = rawbody if rawbody is not None else (json.dumps(body) if body is not None else None)
    if isinstance(data, str): data = data.encode()
    t = time.time()
    for attempt in (0, 1):
        c = getattr(TL, "c", None)
        if c is None: c = TL.c = http.client.HTTPConnection(U.hostname, U.port, timeout=timeout)
        try:
            try:
                c.request(method, path, body=data, headers=h)
            except (BrokenPipeError, ConnectionResetError):
                if not data or len(data) < 100000: raise
                PIPE[0] += 1   # server answered (413) and closed while we were still uploading; read its reply
            r = c.getresponse(); txt = r.read()
            if r.getheader("Connection", "").lower() == "close": c.close(); TL.c = None
            break
        except (http.client.HTTPException, ConnectionError, OSError):
            try: c.close()
            except Exception: pass
            TL.c = None
            if attempt: raise
    try: j = json.loads(txt) if txt else None
    except Exception: j = None
    return r.status, j, txt, time.time() - t

def req(*a, **k):
    s, j, t, d = raw(*a, **k); return s, j

def check(name, ok, detail=""):
    RES.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  :: " + str(detail)), flush=True)

def fx(users=None, **kw):
    f = {"currency": "EUR", "minor_units": 2, "users": users or [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10000},
        {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 2500},
        {"id": "u_cy", "email": "cy@example.com", "password": "hunter2hunter2", "display_name": "Cy", "handle": "cy", "balance": 0},
        {"id": "u_op", "email": "op@example.com", "password": "correct horse", "display_name": "Op", "handle": "op", "balance": 1000}],
        "payments": [], "requests": [], "settlement_operator_ids": ["u_op"]}
    f.update(kw); return f

def reset(f=None):
    s, j = req("POST", "/_test/reset", f or fx()); assert s == 204, (s, j)

def login(email, pw="correct horse"):
    s, j = req("POST", "/auth/login", {"email": email, "password": pw}); assert s == 200, (s, j); return j["token"]

def tokens():
    return {"ada": login("ada@example.com"), "bob": login("bob@example.com"), "cy": login("cy@example.com", "hunter2hunter2"), "op": login("op@example.com")}

def total(T):
    return sum(req("GET", "/me", token=T[k])[1]["balance"] for k in T)

def idem(k): return {"Idempotency-Key": k}
def errcode(j): return (j or {}).get("error", {}).get("code") if isinstance(j, dict) else None

def pool(fn, n, workers=50):
    with ThreadPoolExecutor(workers) as ex: return list(ex.map(fn, range(n)))

# ---------------- A. races on all five write paths, fresh key, identical request
def A_races():
    reset(); T = tokens()
    paths = {
      "payments": ("/payments", {"to_handle": "bob", "amount": 100}, "ada"),
      "requests": ("/requests", {"payer_handle": "ada", "amount": 100}, "bob"),
      "splits": ("/splits", {"amount": 300, "participant_handles": ["ada", "bob", "cy"]}, "ada"),
      "settlements": ("/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100}]}, "op"),
    }
    for name, (p, b, who) in paths.items():
        reset(); T = tokens(); key = "k-" + name
        rs = pool(lambda i: req("POST", p, b, headers=idem(key), token=T[who]), 50)
        c = [s for s, _ in rs]; bodies = {json.dumps(j, sort_keys=True) for _, j in rs}
        check(f"A.race identical fresh key {name}: 1x201 + 49x200 same body",
              c.count(201) == 1 and c.count(200) == 49 and len(bodies) == 1, (c.count(201), c.count(200), len(bodies), set(c)))
    # pay path
    reset(); T = tokens()
    s, rq = req("POST", "/requests", {"payer_handle": "ada", "amount": 700}, headers=idem("mk"), token=T["bob"])
    rs = pool(lambda i: req("POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("pk"), token=T["ada"]), 50)
    c = [s for s, _ in rs]
    check("A.race pay same key: 1x201+49x200", c.count(201) == 1 and c.count(200) == 49, sorted(set(c)))
    ada = req("GET", "/me", token=T["ada"])[1]["balance"]; bob = req("GET", "/me", token=T["bob"])[1]["balance"]
    check("A.pay moved money once", ada == 9300 and bob == 3200, (ada, bob))
    # pay with DIFFERENT keys: one money move only
    reset(); T = tokens()
    s, rq = req("POST", "/requests", {"payer_handle": "ada", "amount": 700}, headers=idem("mk"), token=T["bob"])
    rs = pool(lambda i: req("POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem(f"pk{i}"), token=T["ada"]), 50)
    c = [s for s, _ in rs]
    check("A.race pay distinct keys: exactly one 201, rest 409 request_not_pending", c.count(201) == 1 and c.count(409) == 49 and all(errcode(j) == "request_not_pending" for s, j in rs if s == 409), sorted(set(c)))
    check("A.pay distinct keys: balances", req("GET", "/me", token=T["ada"])[1]["balance"] == 9300)
    # pay vs cancel vs decline race
    for rounds in range(15):
        reset(); T = tokens()
        s, rq = req("POST", "/requests", {"payer_handle": "ada", "amount": 700}, headers=idem("mk"), token=T["bob"])
        rid = rq["request_id"]
        jobs = [lambda: req("POST", f"/requests/{rid}/pay", {}, headers=idem("pk"), token=T["ada"]),
                lambda: req("POST", f"/requests/{rid}/cancel", {}, token=T["bob"]),
                lambda: req("POST", f"/requests/{rid}/decline", {}, token=T["ada"])] * 8
        with ThreadPoolExecutor(24) as ex: rs = list(ex.map(lambda f: f(), jobs))
        final = [r for r in req("GET", "/requests", token=T["ada"])[1]["requests"] if r["request_id"] == rid][0]
        ada = req("GET", "/me", token=T["ada"])[1]["balance"]
        okm = (final["status"] == "paid" and ada == 9300) or (final["status"] != "paid" and ada == 10000)
        paid201 = sum(1 for (s, j), jb in zip(rs, jobs) if s == 201)
        if not okm or total(T) != 13500 or paid201 > 1 or (final["status"] == "paid") != (paid201 == 1):
            check("A.pay/cancel/decline race consistency", False, (final, ada, paid201)); break
    else: check("A.pay/cancel/decline race consistency x15", True)

# ---------------- B. overdraft storms / invariants during
def B_overdraft():
    reset(); T = tokens(); stop = threading.Event(); bad = []
    def watcher():
        while not stop.is_set():
            s, j = req("GET", "/me", token=T["ada"])
            if s == 200 and j["balance"] < 0: bad.append(j)
            tt = 0
            for k in ("ada", "bob", "cy", "op"):
                pass
    th = threading.Thread(target=watcher); th.start()
    rs = pool(lambda i: req("POST", "/payments", {"to_handle": random.choice(["bob", "cy", "op"]), "amount": 333}, headers=idem(f"o{i}"), token=T["ada"]), 200)
    stop.set(); th.join()
    ok = sum(1 for s, _ in rs if s == 201); ins = sum(1 for s, j in rs if s == 409 and errcode(j) == "insufficient_funds")
    ada = req("GET", "/me", token=T["ada"])[1]["balance"]
    check("B.overdraft storm: 30 succeed (10000//333), rest insufficient_funds, never negative", ok == 30 and ins == 170 and ada == 10000 - 30 * 333 and not bad, (ok, ins, ada, bad[:1]))
    check("B.sum invariant after storm", total(T) == 13500)
    # circular concurrent payments
    reset(); T = tokens()
    def circ(i):
        a, b = [("ada", "bob"), ("bob", "ada")][i % 2]
        return req("POST", "/payments", {"to_handle": b, "amount": 1 + i % 7}, headers=idem(f"c{i}"), token=T[a])
    rs = pool(circ, 300)
    check("B.circular storm: no 5xx, sum invariant", all(s < 500 for s, _ in rs) and total(T) == 13500, [s for s, _ in rs if s >= 500][:3])
    # Settlement atomicity: net-affordable cycle, and unaffordable
    reset(); T = tokens()
    # cy has 0: ada->cy 500 and cy->bob 500 : net ok
    s, j = req("POST", "/settlements", {"transfers": [{"from_handle": "cy", "to_handle": "bob", "amount": 500}, {"from_handle": "ada", "to_handle": "cy", "amount": 500}]}, headers=idem("s1"), token=T["op"])
    check("B.settlement order-independent affordability (cy pays before being paid)", s == 201, (s, j))
    s, j = req("POST", "/settlements", {"transfers": [{"from_handle": "cy", "to_handle": "bob", "amount": 500}, {"from_handle": "ada", "to_handle": "cy", "amount": 400}]}, headers=idem("s2"), token=T["op"])
    check("B.settlement unaffordable -> 409 and nothing moves", s == 409 and errcode(j) == "insufficient_funds" and total(T) == 13500 and req("GET", "/me", token=T["cy"])[1]["balance"] == 0, (s, j))
    # concurrent settlements competing for the same funds
    reset(); T = tokens()
    body = lambda i: {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 400}, {"from_handle": "op", "to_handle": "bob", "amount": 400}]}
    rs = pool(lambda i: req("POST", "/settlements", body(i), headers=idem(f"cs{i}"), token=T["op"]), 20)
    op = req("GET", "/me", token=T["op"])[1]["balance"]
    check("B.concurrent settlements: only affordable ones commit (1000 // 800 = 1)", sum(1 for s, _ in rs if s == 201) == 1 and op == 200, (op, [s for s, _ in rs]))

# ---------------- C. idempotency edge cases
def C_idem():
    reset(); T = tokens(); a = T["ada"]
    P = {"to_handle": "bob", "amount": 100}
    s1, j1 = req("POST", "/payments", P, headers=idem("K"), token=a)
    s2, j2 = req("POST", "/payments", {"amount": 100, "to_handle": "bob"}, headers=idem("K"), token=a)
    check("C.replay with reordered keys => 200 same body", (s1, s2) == (201, 200) and j1 == j2, (s1, s2))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 100.0}, headers=idem("K"), token=a)
    check("C.replay with amount 100.0 (same JSON value) => 200 (not 409)", s == 200, (s, j))
    s, j = req("POST", "/payments", rawbody='{"to_handle":"bob","amount":1e2}', headers=idem("K"), token=a)
    check("C.replay with amount 1e2 => 200", s == 200, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 100, "extra": 1}, headers=idem("K"), token=a)
    check("C.same key + extra unknown field is a different body => 409", s == 409, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": -5}, headers=idem("K"), token=a)
    check("C.claimed key + invalid body => 409 idempotency_key_reuse (before validation)", s == 409 and errcode(j) == "idempotency_key_reuse", (s, j))
    s, j = req("POST", "/payments", {"to_handle": "nobody", "amount": 100}, headers=idem("K"), token=a)
    check("C.claimed key + unknown handle => 409", s == 409, (s, j))
    # other user same key
    s, j = req("POST", "/payments", {"to_handle": "ada", "amount": 100}, headers=idem("K"), token=T["bob"])
    check("C.other user same key -> 201 payment to ada", s in (201, 200), (s, j))
    # path scoping: same key on /requests with same-ish body
    s, j = req("POST", "/requests", {"payer_handle": "bob", "amount": 100}, headers=idem("K"), token=a)
    check("C.same key different path succeeds 201", s == 201, (s, j))
    s, j = req("POST", "/requests", {"payer_handle": "bob", "amount": 100}, headers=idem("K"), token=a)
    check("C.…and its replay is 200", s == 200, (s, j))
    # key across pay of different requests = different path
    rid = [r for r in req("GET", "/requests", token=T["bob"])[1]["requests"]][0]["request_id"]
    s, j = req("POST", f"/requests/{rid}/pay", {}, headers=idem("K"), token=T["bob"])
    check("C.same key on /requests/{id}/pay (third path) => 201", s == 201, (s, j))
    # 4xx not claiming
    for body, code in ((  {"to_handle": "bob", "amount": 0}, 422), ({"to_handle": "zzz", "amount": 5}, 404), ({"to_handle": "ada", "amount": 5}, 422)):
        s, j = req("POST", "/payments", body, headers=idem("F"), token=a)
        check(f"C.first failing attempt returns {code}", s == code, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 5}, headers=idem("F"), token=a)
    check("C.key reusable after 4xx failures", s == 201, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 10 ** 10}, headers=idem("F2"), token=a)
    s2, j2 = req("POST", "/payments", {"to_handle": "bob", "amount": 5}, headers=idem("F2"), token=a)
    check("C.key reusable after 422 over-max", (s, s2) == (422, 201), (s, s2))
    # insufficient funds 409 does not claim
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 999999}, headers=idem("F3"), token=a)
    s2, j2 = req("POST", "/payments", {"to_handle": "bob", "amount": 5}, headers=idem("F3"), token=a)
    check("C.key reusable after insufficient_funds 409", (s, s2) == (409, 201), (s, s2, j2))
    # key lengths
    for L, exp in ((0, 400), (1, 201), (255, 201), (256, 422)):
        s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers={"Idempotency-Key": "x" * L}, token=a)
        check(f"C.key length {L} => {exp}", s == exp, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1}, token=a)
    check("C.missing key => 400 missing_idempotency_key", s == 400 and errcode(j) == "missing_idempotency_key", (s, j))
    # missing key + bad body: spec orders? key missing 400
    s, j = req("POST", "/payments", rawbody="{not json", headers=idem("bj"), token=a)
    check("C.unparseable body => 400 malformed_request", s == 400 and errcode(j) == "malformed_request", (s, j))
    s, j = req("POST", "/payments", rawbody="[1,2]", headers=idem("bj2"), token=a)
    check("C.array body => 400 malformed_request", s == 400, (s, j))
    # unauth before key
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("u"))
    check("C.no token => 401", s == 401 and errcode(j) == "unauthenticated", (s, j))
    # decline-after-paid replay returns original even after change
    reset(); T = tokens()
    s, rq = req("POST", "/requests", {"payer_handle": "ada", "amount": 50}, headers=idem("r1"), token=T["bob"])
    req("POST", f"/requests/{rq['request_id']}/cancel", {}, token=T["bob"])
    s, j = req("POST", "/requests", {"payer_handle": "ada", "amount": 50}, headers=idem("r1"), token=T["bob"])
    check("C.replay of request creation after cancel returns original pending body", s == 200 and j == rq and j["status"] == "pending", (s, j))
    s, rq2 = req("POST", "/requests", {"payer_handle": "ada", "amount": 50}, headers=idem("r2"), token=T["bob"])
    s, p1 = req("POST", f"/requests/{rq2['request_id']}/pay", {"visibility": "public"}, headers=idem("p"), token=T["ada"])
    s, p2 = req("POST", f"/requests/{rq2['request_id']}/pay", {}, headers=idem("p"), token=T["ada"])
    check("C.pay {} vs {visibility:public} same key => 409", s == 409 and errcode(p2) == "idempotency_key_reuse", (s, p2))
    s, p3 = req("POST", f"/requests/{rq2['request_id']}/pay", {"visibility": "public"}, headers=idem("p"), token=T["ada"])
    check("C.pay replay after paid => 200 original", s == 200 and p3 == p1, (s, p3))
    s, p4 = req("POST", f"/requests/{rq2['request_id']}/pay", {}, headers=idem("p-new"), token=T["ada"])
    check("C.pay already-paid new key => 409 request_not_pending", s == 409 and errcode(p4) == "request_not_pending", (s, p4))
    # empty body on pay
    s, rq3 = req("POST", "/requests", {"payer_handle": "ada", "amount": 50}, headers=idem("r3"), token=T["bob"])
    s, j = req("POST", f"/requests/{rq3['request_id']}/pay", rawbody="", headers=idem("pe"), token=T["ada"])
    check("C.pay with empty body (no JSON) -> 201 or 400 (documented)", s in (201, 400), (s, j))
    s, j = req("POST", f"/requests/{rq3['request_id']}/pay", {"visibility": "secret"}, headers=idem("pv"), token=T["ada"])
    check("C.pay bad visibility => 422", s == 422, (s, j))
    s, j = req("POST", f"/requests/{rq3['request_id']}/pay", {"visibility": None}, headers=idem("pv2"), token=T["ada"])
    check("C.pay visibility null => 422", s == 422, (s, j))
    # response lost then retry: simulate by doing request then replay (covered); also whitespace/key-order
    s, j = req("POST", "/payments", rawbody='  { "amount" : 3 ,\n"to_handle":"bob" , "note":"x"}', headers=idem("ws"), token=T["ada"])
    s2, j2 = req("POST", "/payments", rawbody='{"note":"x","to_handle":"bob","amount":3}', headers=idem("ws"), token=T["ada"])
    check("C.whitespace/key order replay", (s, s2) == (201, 200) and j == j2, (s, s2))
    # duplicate JSON keys in body
    s, j = req("POST", "/payments", rawbody='{"to_handle":"bob","amount":1,"amount":2}', headers=idem("dup"), token=T["ada"])
    check("C.duplicate JSON key doesn't 5xx", s < 500, (s, j))

# ---------------- D. number parsing, validation fuzz
def D_numbers():
    reset(); T = tokens(); a = T["ada"]; n = [0]
    def pay(rawamt, note=None):
        n[0] += 1
        body = '{"to_handle":"bob","amount":%s}' % rawamt
        return req("POST", "/payments", rawbody=body, headers=idem(f"n{n[0]}"), token=a)
    good = ["1", "1000.0", "1e3", "1E3", "1.0e3", "10e-1", "0.1e1", "1000000000", "1e9", "100e-2"]
    for g in good:
        s, j = pay(g); check(f"D.amount {g} accepted (201, or 409 only when above balance)", s == 201 or (s == 409 and g in ("1000000000", "1e9")), (s, j))
    bad = ["0", "-0", "-1", "1.5", "0.5", "1000000001", "1e10", "1e400", "123456789012345678901234567890", "true", "false", '"100"', "null", "[]", "{}", "1e-1", "1.0000000000000000000001", "NaN", "Infinity", "-Infinity", "01", "+1", ".5", "1.", "0x10", "1e", '""', "9007199254740993", "1e3.5"]
    for b in bad:
        s, j = pay(b)
        exp = (422, 400)
        check(f"D.amount {b} rejected 4xx (no 5xx, no move)", s in exp and errcode(j) in ("validation_failed", "malformed_request"), (s, j))
    s, j = pay("-0.0"); check("D.amount -0.0 rejected", s == 422, (s, j))
    s, j = pay("1000000000.0000"); check("D.amount 1000000000.0000 ok? (integral)", s in (201, 409), (s, j))
    check("D.wrong-type 'amount string' is 422 (not 400)", pay('"100"')[0] == 422)
    check("D.wrong-type to_handle number is 400 malformed", req("POST", "/payments", rawbody='{"to_handle":5,"amount":1}', headers=idem("t1"), token=a)[0] in (400, 422))
    print("   INFO to_handle=5 ->", req("POST", "/payments", rawbody='{"to_handle":5,"amount":1}', headers=idem("t2"), token=a))
    print("   INFO missing to_handle ->", req("POST", "/payments", {"amount": 1}, headers=idem("t3"), token=a))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1, "note": None}, headers=idem("t4"), token=a)
    check("D.note null => 422", s == 422, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1, "note": 5}, headers=idem("t5"), token=a)
    check("D.note number => 422", s == 422, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1, "note": "x" * 200}, headers=idem("t6"), token=a)
    check("D.note 200 chars ok", s == 201)
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1, "note": "x" * 201}, headers=idem("t7"), token=a)
    check("D.note 201 chars => 422", s == 422)
    emoji = "😀" * 200
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1, "note": emoji}, headers=idem("t8"), token=a)
    check("D.note 200 emoji (200 code points, 400 UTF-16 units) accepted", s == 201 and (j or {}).get("note") == emoji, (s, str(j)[:100]))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1, "note": "😀" * 201}, headers=idem("t9"), token=a)
    check("D.note 201 emoji => 422", s == 422, (s,))
    weird = "a\u0000b\u202e \t\n  é\u0301 \U0001F600 <script>&amp;\"\\ "
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1, "note": weird}, headers=idem("t10"), token=a)
    check("D.note verbatim unicode/control chars", s == 201 and j["note"] == weird, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1, "note": "  spaced  "}, headers=idem("t11"), token=a)
    check("D.note not trimmed", j and j["note"] == "  spaced  ")
    s, j = req("POST", "/payments", rawbody='{"to_handle":"bob","amount":1,"note":"\\ud800"}', headers=idem("t12"), token=a)
    print("   INFO lone surrogate note ->", s, str(j)[:120])
    check("D.lone surrogate no 5xx", s < 500)
    for h in ("BOB", "Bob", " bob", "bob ", "b0b", "", "bo\u0062\u0000"):
        s, j = req("POST", "/payments", {"to_handle": h, "amount": 1}, headers=idem("hh%d" % abs(hash(h))), token=a)
        check(f"D.to_handle {h!r} => 404/422 only (handles are lowercase exact)", s in (404, 422), (s, j))
    for v in ("PUBLIC", "Private", "", " public", 1, True):
        s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1, "visibility": v}, headers=idem("v" + str(v)), token=a)
        check(f"D.visibility {v!r} => 422", s == 422, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "ada", "amount": 99999999999999}, headers=idem("sp"), token=a)
    check("D.self payment with huge amount: validation (422)", s == 422, (s, j))
    # order of errors: self payment w/ bad amount?
    print("   INFO self+amount0 ->", req("POST", "/payments", {"to_handle": "ada", "amount": 0}, headers=idem("so"), token=a))
    # body kinds
    for name, body in (("empty", ""), ("null", "null"), ("string", '"x"'), ("number", "5"), ("trunc", '{"to_handle":'), ("trailing", '{"to_handle":"bob","amount":1} x')):
        s, j = req("POST", "/payments", rawbody=body, headers=idem("b" + name), token=a)
        check(f"D.body {name} => 400 malformed_request", s == 400 and errcode(j) == "malformed_request", (s, j))
    s, j = req("POST", "/payments", rawbody='{"a":' * 5000 + "1" + "}" * 5000, headers=idem("deep"), token=a)
    check("D.deeply nested 5000 => 4xx not 5xx/hang", s < 500, (s,))
    s, j = req("POST", "/payments", rawbody='{"to_handle":"bob","amount":1,"pad":"' + "x" * 5_000_000 + '"}', headers=idem("big"), token=a)
    check("D.5MB body over the 256KiB API cap -> 413 JSON error (r2: cap added)", s == 413 and errcode(j) == "payload_too_large", (s, str(j)[:80]))
    s, j = req("POST", "/payments", rawbody='{"to_handle":"bob","amount":1e999999999}', headers=idem("hugeexp"), token=a)
    check("D.amount 1e999999999 rejected fast, no 5xx", s in (400, 422), (s,))
    s, j = req("POST", "/payments", rawbody='{"to_handle":"bob","amount":' + "9" * 100000 + '}', headers=idem("hugedigits"), token=a)
    check("D.amount of 100000 digits rejected, no 5xx", s in (400, 422), (s,))
    s, j = req("POST", "/payments", rawbody='{"to_handle":"bob","amount":1e-999999999}', headers=idem("tinyexp"), token=a)
    check("D.amount 1e-999999999 rejected, no 5xx/hang", s in (400, 422), (s,))
    s, j = req("POST", "/payments", rawbody='{"to_handle":"bob","amount":0e999999999}', headers=idem("zeroexp"), token=a)
    check("D.amount 0e999999999 rejected (value 0), no hang", s in (400, 422), (s,))

# ---------------- E. splits and rounding
def E_splits():
    reset(); T = tokens(); a = T["ada"]; n = [0]
    def sp(amt, hs, who="ada", **kw):
        n[0] += 1
        b = {"amount": amt, "participant_handles": hs}; b.update(kw)
        return req("POST", "/splits", b, headers=idem(f"s{n[0]}"), token=T[who])
    for amt, nn, exp in ((1000, 3, [334, 333, 333]), (1, 3, [1, 0, 0]), (10, 3, [4, 3, 3]), (999, 3, [333] * 3), (5, 5, [1] * 5)):
        hs = (["ada", "bob", "cy", "op"] + [])[:nn] if nn <= 4 else None
        if hs is None: continue
        s, j = sp(amt, hs)
        check(f"E.split {amt}/{nn}", s == 201 and [x["amount"] for x in j["shares"]] == exp, (s, j))
    s, j = sp(1000, ["cy", "bob", "ada"])
    check("E.split order gives extra unit to first handle (cy)", [x["amount"] for x in j["shares"]] == [334, 333, 333] and [x["handle"] for x in j["shares"]] == ["cy", "bob", "ada"] and [r["payer_handle"] for r in j["requests"]] == ["cy", "bob"], j)
    s, j = sp(1, ["cy", "bob", "ada"]); check("E.split 1 among 3 -> shares 1,0,0; a 0-share still yields a request (spec 9)", s == 201 and [r["amount"] for r in j["requests"]] == [1, 0], j)
    s, j = sp(7, ["ada"]); check("E.solo split valid, requests []", s == 201 and j["requests"] == [] and j["shares"][0]["amount"] == 7, (s, j))
    s, j = sp(7, ["bob"]); check("E.split without caller: 1 request (caller not included)", s == 201 and len(j["requests"]) == 1 and j["shares"][0]["amount"] == 7, (s, j))
    s, j = sp(7, ["bob", "bob"]); check("E.dup handle 422", s == 422, (s, j))
    s, j = sp(7, []); check("E.empty 422", s == 422)
    s, j = sp(7, ["bob", "nobody"]); check("E.unknown handle 404", s == 404)
    s, j = sp(7, "bob"); check("E.participants not array => 400/422", s in (400, 422), (s, j))
    s, j = sp(7, ["bob", 5]); check("E.participants non-string elem => 400/422", s in (400, 422), (s, j))
    s, j = sp(0, ["bob"]); check("E.amount 0 422", s == 422)
    s, j = sp(1000000000, ["ada", "bob", "cy"]); check("E.max amount split ok", s == 201 and sum(x["amount"] for x in j["shares"]) == 10**9, (s,))
    s, j = sp(100, ["ada", "bob"], note="x" * 201); check("E.note 201 -> 422", s == 422)
    hs = ["ada", "bob", "cy", "op"]
    s, j = sp(10**9, hs * 1); check("E.split many shares sum", sum(x["amount"] for x in j["shares"]) == 10**9)
    # BHD/JPY same
    for amt in range(1, 40):
        s, j = sp(amt, hs[:3]) if amt % 2 else sp(amt, hs)
        sh = [x["amount"] for x in j["shares"]]
        if sum(sh) != amt or max(sh) - min(sh) > 1 or sh != sorted(sh, reverse=True):
            check("E.share property", False, (amt, sh)); break
    else: check("E.share property amounts 1..39", True)
    # split requests visible to both, not third; payment paid via them
    reset(); T = tokens()
    s, j = req("POST", "/splits", {"amount": 100, "participant_handles": ["bob", "cy"]}, headers=idem("z"), token=T["ada"])
    ids = [r["request_id"] for r in j["requests"]]
    vis = lambda who: {r["request_id"] for r in req("GET", "/requests", token=T[who])[1]["requests"]}
    check("E.split requests visible to payers + requester only", vis("bob") == {ids[0]} and vis("cy") == {ids[1]} and vis("ada") == set(ids) and vis("op") == set(), "")
    check("E.split not in activity", all(len(req("GET", "/activity", token=T[w])[1]["payments"]) == 0 for w in T))
    s, jj = req("POST", f"/requests/{ids[0]}/pay", {}, headers=idem("p"), token=T["cy"])
    check("E.third-party pay => 403", s == 403 and errcode(jj) == "forbidden", (s, jj))
    s, jj = req("POST", f"/requests/{ids[0]}/pay", {}, headers=idem("p"), token=T["ada"])
    check("E.requester pays own request => 403", s == 403, (s, jj))
    s, jj = req("POST", f"/requests/{ids[0]}/cancel", {}, token=T["bob"]); check("E.payer cancel => 403", s == 403)
    s, jj = req("POST", f"/requests/{ids[0]}/decline", {}, token=T["ada"]); check("E.requester decline => 403", s == 403)
    s, jj = req("POST", f"/requests/{ids[0]}/cancel", {}, token=T["op"]); check("E.operator cancel others' request => 403 (permission not extended)", s == 403, (s, jj))
    s, jj = req("POST", f"/requests/{ids[0]}/pay", {}, headers=idem("po"), token=T["op"]); check("E.operator pay others' request => 403", s == 403, (s, jj))
    s, jj = req("POST", "/requests/nonexistent/pay", {}, headers=idem("pn"), token=T["op"]); check("E.pay unknown => 404", s == 404)
    s, jj = req("POST", "/requests/nonexistent/cancel", {}, token=T["op"]); check("E.cancel unknown => 404", s == 404)
    s, jj = req("POST", f"/requests/{ids[0]}/pay", {}, token=T["bob"]); check("E.pay missing idem key => 400", s == 400, (s, jj))
    # decline twice, cancel after decline
    s, jj = req("POST", f"/requests/{ids[0]}/decline", {}, token=T["bob"]); s2, j2 = req("POST", f"/requests/{ids[0]}/decline", {}, token=T["bob"])
    check("E.decline twice 200", (s, s2) == (200, 200) and j2["status"] == "declined")
    s, jj = req("POST", f"/requests/{ids[0]}/cancel", {}, token=T["ada"]); check("E.cancel after decline 409", s == 409 and errcode(jj) == "request_not_pending")
    s, jj = req("POST", f"/requests/{ids[0]}/pay", {}, headers=idem("pz"), token=T["bob"]); check("E.pay after decline 409 request_not_pending", s == 409 and errcode(jj) == "request_not_pending", (s, jj))
    # request exceeding balance then becomes payable
    reset(); T = tokens()
    s, rq = req("POST", "/requests", {"payer_handle": "cy", "amount": 500}, headers=idem("q"), token=T["ada"])
    check("E.request beyond payer balance allowed", s == 201 and rq["status"] == "pending", (s, rq))
    s, j = req("POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("pp"), token=T["cy"])
    check("E.pay while short 409 insufficient_funds", s == 409 and errcode(j) == "insufficient_funds")
    check("E.still pending", req("GET", "/requests?status=pending", token=T["cy"])[1]["requests"][0]["status"] == "pending")
    req("POST", "/payments", {"to_handle": "cy", "amount": 500}, headers=idem("fund"), token=T["ada"])
    s, j = req("POST", f"/requests/{rq['request_id']}/pay", {"visibility": "private"}, headers=idem("pp"), token=T["cy"])
    check("E.same key after 409 then funded => 201", s == 201 and j["visibility"] == "private" and j["request_id"] == rq["request_id"], (s, j))
    # pay 0-share request
    s, j = req("POST", "/splits", {"amount": 1, "participant_handles": ["ada", "bob", "cy"]}, headers=idem("z0"), token=T["ada"])
    rid0 = j["requests"][0]["request_id"]
    s, jj = req("POST", f"/requests/{rid0}/pay", {}, headers=idem("pz0"), token=T["bob"])
    print("   INFO paying zero-amount request ->", s, str(jj)[:160])
    check("E.zero-amount payment no 5xx; total preserved", s < 500 and total(T) == 13500)

# ---------------- F. feed visibility
def F_feed():
    reset(); T = tokens()
    mk = lambda who, to, vis, k: req("POST", "/payments", {"to_handle": to, "amount": 1, "visibility": vis, "note": k}, headers=idem(k), token=T[who])[1]
    p = {"pub": mk("ada", "bob", "public", "pub"), "priv_ab": mk("ada", "bob", "private", "priv_ab"), "priv_bc": mk("bob", "cy", "private", "priv_bc"), "pub_bc": mk("bob", "cy", "public", "pub_bc")}
    feed = lambda who, q="": {x["note"] for x in req("GET", "/activity" + q, token=T[who])[1]["payments"]}
    check("F.ada feed", feed("ada") == {"pub", "priv_ab", "pub_bc"}, feed("ada"))
    check("F.bob feed (receiver sees private)", feed("bob") == {"pub", "priv_ab", "priv_bc", "pub_bc"}, feed("bob"))
    check("F.cy feed", feed("cy") == {"pub", "pub_bc", "priv_bc"}, feed("cy"))
    check("F.op feed: only public (operator no private access)", feed("op") == {"pub", "pub_bc"}, feed("op"))
    s, j = req("GET", "/activity?limit=1&offset=0", token=T["bob"]); check("F.limit=1 has_more", len(j["payments"]) == 1 and j["has_more"] is True)
    s, j = req("GET", "/activity?limit=4&offset=0", token=T["bob"]); check("F.limit=4 exactly all => has_more false", len(j["payments"]) == 4 and j["has_more"] is False, j["has_more"])
    s, j = req("GET", "/activity?limit=200&offset=4", token=T["bob"]); check("F.offset past end empty", j["payments"] == [] and j["has_more"] is False)
    for q in ("limit=0", "limit=201", "limit=-1", "limit=1e2", "limit=4.0", "limit=+4", "limit=", "limit=abc", "offset=-1", "offset=1e0", "offset=", "limit=0x10", "limit=%2B4", "limit=5&limit=6", "limit=%D9%A1%D9%A2", "limit=099999999999999999999999"):
        s, j = req("GET", "/activity?" + q, token=T["bob"])
        want = 422
        if q == "limit=5&limit=6": want = None
        check(f"F.query {q!r} => {want}", want is None and s < 500 or s == want, (s, j if s != 200 else ""))
    s, j = req("GET", "/activity?limit=05", token=T["bob"]); print("   INFO limit=05 ->", s)
    s, j = req("GET", "/activity?foo=bar&limit=2", token=T["bob"]); check("F.unknown query ignored", s == 200 and len(j["payments"]) == 2)
    check("F.newest first", [x["note"] for x in req("GET", "/activity", token=T["bob"])[1]["payments"]][0] in ("pub_bc", "priv_bc"))
    check("F.activity no auth 401", req("GET", "/activity")[0] == 401)
    check("F.bad token 401", req("GET", "/activity", token="nope")[0] == 401)
    check("F.malformed auth header 401", req("GET", "/activity", headers={"Authorization": "Basic abc"})[0] == 401)
    check("F.bearer lowercase scheme", req("GET", "/me", headers={"Authorization": "bearer " + T["ada"]})[0] in (200, 401))
    # paying a request with private visibility: hidden from third, visible to both
    s, rq = req("POST", "/requests", {"payer_handle": "bob", "amount": 5}, headers=idem("rr"), token=T["cy"])
    req("POST", f"/requests/{rq['request_id']}/pay", {"visibility": "private"}, headers=idem("rp"), token=T["bob"])
    check("F.private request payment visible to both parties only", "x" not in feed("ada") and sum(1 for w in T if any(x["request_id"] == rq["request_id"] for x in req("GET", "/activity", token=T[w])[1]["payments"])) == 2)
    # requests never in feed + request listing filters
    s, j = req("GET", "/requests?direction=incoming", token=T["bob"]); check("F.incoming", all(r["payer_handle"] == "bob" for r in j["requests"]))
    s, j = req("GET", "/requests?direction=outgoing", token=T["bob"]); check("F.outgoing", all(r["requester_handle"] == "bob" for r in j["requests"]))
    for q in ("direction=sideways", "status=nope", "direction=", "status=PAID", "limit=0"):
        check(f"F./requests?{q} => 422", req("GET", "/requests?" + q, token=T["bob"])[0] == 422)
    check("F./requests of op empty", req("GET", "/requests", token=T["op"])[1]["requests"] == [])

# ---------------- G. auth
def G_auth():
    reset(); T = tokens()
    s, j = req("POST", "/auth/signup", {"email": "New.User+x@Example.com", "password": "12345678", "display_name": "N"})
    check("G.signup 201 + token works", s == 201 and req("GET", "/me", token=j["token"])[1]["handle"] == "new_user_x" and req("GET", "/me", token=j["token"])[1]["balance"] == 0, (s, j))
    s, j2 = req("POST", "/auth/signup", {"email": "new.user+x@example.com", "password": "12345678", "display_name": "N"})
    check("G.signup email case-variant dup => 409 email_taken", s == 409 and errcode(j2) == "email_taken", (s, j2))
    s, j2 = req("POST", "/auth/signup", {"email": "new.user_x@other.com", "password": "12345678", "display_name": "N"})
    check("G.signup handle collision => 409 handle_taken", s == 409 and errcode(j2) == "handle_taken", (s, j2))
    s, j3 = req("POST", "/auth/login", {"email": "new.user_x@other.com", "password": "12345678"})
    check("G.handle_taken created no account", s == 401, (s, j3))
    s, j2 = req("POST", "/auth/signup", {"email": "a" * 30 + "@x.com", "password": "12345678", "display_name": "L"})
    check("G.long local part truncated to 20", s == 201 and req("GET", "/me", token=j2["token"])[1]["handle"] == "a" * 20, (s, j2))
    s, j2 = req("POST", "/auth/signup", {"email": "Ünï@x.com", "password": "12345678", "display_name": "L"})
    print("   INFO unicode local ->", s, req("GET", "/me", token=j2["token"])[1] if s == 201 else j2)
    for em in ("noat", "@x.com", "a@", "a@@b", "", "a b@c.com", None, 5):
        s, j2 = req("POST", "/auth/signup", {"email": em, "password": "12345678", "display_name": "L"})
        check(f"G.signup email {em!r} => 422/400", s in (422, 400), (s, j2))
    s, j2 = req("POST", "/auth/signup", {"email": "short@x.com", "password": "1234567", "display_name": "L"}); check("G.7-char password 422", s == 422)
    s, j2 = req("POST", "/auth/signup", {"email": "p8@x.com", "password": "12345678", "display_name": "L"}); check("G.8-char ok", s == 201)
    s, j2 = req("POST", "/auth/signup", {"email": "uni@x.com", "password": "😀😀😀😀", "display_name": "L"}); print("   INFO 4 emoji (8 bytes/4 cps) password ->", s)
    s, j2 = req("POST", "/auth/signup", {"email": "nd@x.com", "password": "12345678"}); print("   INFO missing display_name ->", s, j2)
    s, j2 = req("POST", "/auth/signup", {"email": "nd2@x.com", "password": 12345678, "display_name": "x"}); check("G.password number => 400/422", s in (400, 422), (s, j2))
    s, j = req("POST", "/auth/login", {"email": "ada@example.com", "password": "wrong"}); check("G.wrong pw 401", s == 401 and errcode(j) == "unauthenticated")
    s, j = req("POST", "/auth/login", {"email": "nobody@example.com", "password": "wrong"}); check("G.unknown 401", s == 401)
    s, j = req("POST", "/auth/login", {"email": "ADA@EXAMPLE.COM", "password": "correct horse"}); print("   INFO login uppercase email ->", s)
    s, j = req("POST", "/auth/login", {}); print("   INFO login empty ->", s, j)
    s, j = req("POST", "/auth/login", {"email": "ada@example.com", "password": None}); print("   INFO login pw null ->", s, j)
    a1 = login("ada@example.com"); a2 = login("ada@example.com")
    check("G.multiple tokens valid concurrently", a1 != a2 and req("GET", "/me", token=a1)[0] == 200 and req("GET", "/me", token=a2)[0] == 200)
    # hashing under load
    def lg(i): return raw("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
    rs = pool(lg, 100)
    check("G.100 logins @50 conc: all 200, p100<5s", all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5, (set(r[0] for r in rs), max(r[3] for r in rs)))
    def sg(i): return raw("POST", "/auth/signup", {"email": f"load{i}@x.com", "password": "pw-%08d" % i, "display_name": "L"})
    rs = pool(sg, 100)
    check("G.100 signups @50 conc: all 201, max<5s", all(r[0] == 201 for r in rs) and max(r[3] for r in rs) < 5, (set(r[0] for r in rs), max(r[3] for r in rs)))
    # health + payments latency while signups storm (no hashing under lock / event loop stall)
    stop = threading.Event(); lat = []
    def hl():
        while not stop.is_set():
            lat.append(raw("GET", "/health")[3]); time.sleep(0.01)
    th = threading.Thread(target=hl); th.start()
    pool(lambda i: raw("POST", "/auth/signup", {"email": f"storm{i}@x.com", "password": "pw-%08d" % i, "display_name": "L"}), 150)
    stop.set(); th.join()
    check("G.health latency during signup storm < 1s", max(lat) < 1.0, max(lat))
    s, j = req("GET", "/me", token=T["ada"]); check("G./me shape", set(j) >= {"user_id", "display_name", "handle", "balance", "currency", "minor_units"} and j["currency"] == "EUR")
    # case: signup token for signup'd user can receive money immediately
    s, j = req("POST", "/payments", {"to_handle": "new_user_x", "amount": 5}, headers=idem("tonew"), token=T["ada"]); check("G.pay to new signup", s == 201, (s, j))

# ---------------- H. settlements
def H_settle():
    reset(); T = tokens(); op = T["op"]; n = [0]
    def st(tr, who="op", **kw):
        n[0] += 1; b = {"transfers": tr}; b.update(kw)
        return req("POST", "/settlements", b, headers=idem(f"st{n[0]}"), token=T[who] if who else None)
    t = lambda f, to, a, **k: dict(from_handle=f, to_handle=to, amount=a, **k)
    check("H.no token 401", st([t("ada", "bob", 1)], who=None)[0] == 401)
    check("H.non-operator 403", st([t("ada", "bob", 1)], who="ada")[0] == 403)
    s, j = st([t("ada", "bob", 100, note="n", visibility="private"), t("bob", "cy", 50)])
    ok = s == 201 and [p["from_handle"] for p in j["payments"]] == ["ada", "bob"] and j["payments"][0]["visibility"] == "private" and j["payments"][1]["visibility"] == "public" and all(p["settlement_id"] == j["settlement_id"] and p["request_id"] is None and p["created_at"] == j["committed_at"] for p in j["payments"])
    check("H.settlement 201 shape, defaults, shared created_at", ok, (s, j))
    sid = j["settlement_id"] if s == 201 else None
    feed = lambda w: req("GET", "/activity", token=T[w])[1]["payments"]
    check("H.settlement member private hidden from operator? (op is not party)", all(p["note"] != "n" for p in feed("op")))
    check("H.payments exposed to parties in feed w/ settlement_id", [p["settlement_id"] for p in feed("ada")] == [sid, sid])
    check("H.ordinary payment settlement_id null", (lambda r: r[1]["settlement_id"] is None)(req("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("o"), token=T["ada"])))
    check("H.balances after", (req("GET", "/me", token=T["ada"])[1]["balance"], req("GET", "/me", token=T["bob"])[1]["balance"], req("GET", "/me", token=T["cy"])[1]["balance"]) == (9899, 2551, 50))
    # errors order
    for name, tr, code in (("empty", [], 422), ("33", [t("ada", "bob", 1)] * 33, 422), ("not-array", "x", 422), ("non-object", [5], 422), ("self", [t("ada", "ada", 5)], 422), ("unknown", [t("ada", "zzz", 5)], 404), ("amount0", [t("ada", "bob", 0)], 422), ("amt-str", [t("ada", "bob", "5")], 422), ("vis", [t("ada", "bob", 5, visibility="x")], 422), ("note", [t("ada", "bob", 5, note="x" * 201)], 422), ("missing-from", [{"to_handle": "bob", "amount": 1}], 422)):
        s, j = st(tr)
        check(f"H.{name} => {code}", s in (code, 400) and (s == code or name in ("not-array", "non-object")), (s, j))
    s, j = st([t("ada", "bob", 10 ** 6), t("ada", "ada", 5)]); check("H.entry error (self) precedes insufficient funds", s == 422 and errcode(j) == "self_payment", (s, j))
    s, j = st([t("ada", "bob", 10 ** 9), t("ada", "zzz", 5)]); check("H.entry error 404 precedes insufficient", s == 404, (s, j))
    s, j = st([t("ada", "bob", 10 ** 9)]); check("H.insufficient 409", s == 409)
    s, j = st([t("ada", "bob", 1)] * 32); check("H.32 transfers ok", s == 201 and len(j["payments"]) == 32, s)
    # operator included in transfers & reuse key / invalid claims nothing
    k = "inv"; s, _ = req("POST", "/settlements", {"transfers": []}, headers=idem(k), token=op); s2, j2 = req("POST", "/settlements", {"transfers": [t("op", "bob", 1)]}, headers=idem(k), token=op)
    check("H.invalid doesn't claim key", (s, s2) == (422, 201), (s, s2, j2))
    s, j3 = req("POST", "/settlements", {"transfers": [t("op", "bob", 1)]}, headers=idem(k), token=op)
    check("H.replay 200 identical", s == 200 and j3 == j2)
    s, j3 = req("POST", "/settlements", {"transfers": [t("op", "bob", 2)]}, headers=idem(k), token=op)
    check("H.same key different body 409", s == 409)
    s, j3 = req("POST", "/settlements", {"transfers": [t("op", "bob", 2)]}, headers=idem(k), token=T["ada"])
    check("H.non-operator w/ operator's key: 403 (auth before idem)", s == 403, (s, j3))
    # operator can't see other requests
    req("POST", "/requests", {"payer_handle": "bob", "amount": 5}, headers=idem("r"), token=T["ada"])
    check("H.operator /requests excludes others'", req("GET", "/requests", token=op)[1]["requests"] == [])
    # extra fields on transfers ignored, null note
    s, j = st([dict(t("op", "bob", 1), junk=1)]); check("H.unknown field on transfer ignored", s == 201, (s, j))
    s, j = st([t("op", "bob", 1, note=None)]); check("H.note null => 422", s == 422, (s, j))
    s, j = st([t("op", "bob", 1e3 if False else 1)]); 
    # concurrency: many settlements invariant
    reset(); T = tokens()
    def cs(i): return req("POST", "/settlements", {"transfers": [t("ada", "bob", 7), t("bob", "ada", 3), t("cy", "ada", 0 + 1)]}, headers=idem(f"cc{i}"), token=T["op"])
    rs = pool(cs, 60)
    check("H.concurrent settlements w/ 0-balance cy: first fail? sum invariant & no 5xx", all(s < 500 for s, _ in rs) and total(T) == 13500, [s for s, _ in rs][:10])
    check("H.cy never negative", req("GET", "/me", token=T["cy"])[1]["balance"] >= 0)
    # self-payment checked on handle case
    s, j = st([t("ada", "ADA", 5)]); check("H.uppercase handle unknown 404", s == 404)

# ---------------- I. export / import
def I_export():
    reset(); T = tokens(); a = T["ada"]
    s, p1 = req("POST", "/payments", {"to_handle": "bob", "amount": 100, "note": "é😀", "visibility": "private"}, headers=idem("pk"), token=a)
    s, rq = req("POST", "/requests", {"payer_handle": "ada", "amount": 300}, headers=idem("rk"), token=T["bob"])
    s, sp = req("POST", "/splits", {"amount": 10, "participant_handles": ["ada", "bob", "cy"]}, headers=idem("sk"), token=a)
    s, st = req("POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 10}]}, headers=idem("stk"), token=T["op"])
    req("POST", "/payments", {"to_handle": "bob", "amount": 999999}, headers=idem("failk"), token=a)
    s, newu = req("POST", "/auth/signup", {"email": "zed@x.com", "password": "zedzedzed", "display_name": "Z"})
    s, ex = req("GET", "/_test/export")
    check("I.export shape", s == 200 and ex["track"] == "pocketful" and ex["format_version"] == 1 and "state" in ex, str(ex)[:200])
    before = {k: req("GET", "/me", token=T[k])[1] for k in T}
    acts = {k: req("GET", "/activity?limit=200", token=T[k])[1] for k in T}; rqs = {k: req("GET", "/requests", token=T[k])[1] for k in T}
    # mutate then import
    req("POST", "/payments", {"to_handle": "bob", "amount": 5}, headers=idem("later"), token=a)
    s, ex2 = req("GET", "/_test/export")
    check("I.export is snapshot (earlier snapshot unchanged after later writes)", json.dumps(ex) != json.dumps(ex2))
    s, j = req("POST", "/_test/import", ex); check("I.import 204", s == 204, (s, j))
    check("I.import: balances preserved", {k: req("GET", "/me", token=T[k])[1] for k in T} == before)
    check("I.import: activity preserved exactly (ids, timestamps)", {k: req("GET", "/activity?limit=200", token=T[k])[1] for k in T} == acts)
    check("I.import: requests preserved", {k: req("GET", "/requests", token=T[k])[1] for k in T} == rqs)
    check("I.import: removed post-export write", all(p["note"] != "" or p["amount"] != 5 for p in req("GET", "/activity", token=a)[1]["payments"]))
    check("I.import: new-user token still valid", req("GET", "/me", token=newu["token"])[0] == 200)
    check("I.import: login with signup pw", req("POST", "/auth/login", {"email": "zed@x.com", "password": "zedzedzed"})[0] == 200)
    check("I.import: replay payment 200 identical", req("POST", "/payments", {"to_handle": "bob", "amount": 100, "note": "é😀", "visibility": "private"}, headers=idem("pk"), token=a) == (200, p1))
    check("I.import: replay request 200", req("POST", "/requests", {"payer_handle": "ada", "amount": 300}, headers=idem("rk"), token=T["bob"]) == (200, rq))
    check("I.import: replay split 200", req("POST", "/splits", {"amount": 10, "participant_handles": ["ada", "bob", "cy"]}, headers=idem("sk"), token=a) == (200, sp))
    check("I.import: replay settlement 200", req("POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 10}]}, headers=idem("stk"), token=T["op"]) == (200, st))
    check("I.import: operator permission preserved", req("POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 1}]}, headers=idem("stx"), token=T["op"])[0] == 201)
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("failk"), token=a); check("I.import: failed key stays reusable", s == 201, (s, j))
    # pay the exported pending request w/ old token
    s, j = req("POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("payx"), token=a); check("I.import: pending request payable", s == 201 and j["request_id"] == rq["request_id"], (s, j))
    # ids: new payment ids don't collide with imported ones
    ids = [p["payment_id"] for p in req("GET", "/activity?limit=200", token=a)[1]["payments"]]
    check("I.import: no id collision after new writes", len(ids) == len(set(ids)))
    rids = [r["request_id"] for k in T for r in req("GET", "/requests?limit=200", token=T[k])[1]["requests"]]
    # idempotent import
    s, j = req("POST", "/_test/import", ex); s2, j2 = req("POST", "/_test/import", ex); check("I.import twice 204/204", (s, s2) == (204, 204))
    check("I.import repeated no duplication", {k: req("GET", "/activity?limit=200", token=T[k])[1] for k in T} == acts)
    # export -> import -> export equality
    s, e3 = req("GET", "/_test/export"); check("I.export after import equals original export", e3 == ex, "state differs")
    # invalid imports change nothing
    base = req("GET", "/_test/export")[1]
    for name, body in (("empty obj", {}), ("wrong track", dict(ex, track="x")), ("wrong version", dict(ex, format_version=2)), ("no state", {"track": "pocketful", "format_version": 1}), ("state null", dict(ex, state=None)), ("state string", dict(ex, state="x")), ("state empty", dict(ex, state={})), ("state array", dict(ex, state=[])), ("version str", dict(ex, format_version="1")), ("version 1.0", None)):
        if body is None: s, j = req("POST", "/_test/import", rawbody=json.dumps(ex).replace('"format_version": 1', '"format_version": 1.0'))
        else: s, j = req("POST", "/_test/import", body)
        check(f"I.invalid import ({name}) => 422/204(1.0) w/o change", (s == 422 or (name == "version 1.0" and s == 204)) and req("GET", "/_test/export")[1] == base, (s, j))
    s, j = req("POST", "/_test/import", rawbody="{bad"); check("I.import unparseable => 400", s == 400, (s, j))
    # corrupted state: mutate pieces
    def corrupt(path_fn):
        e = json.loads(json.dumps(ex)); path_fn(e["state"]); return e
    def drop_first_key(st):
        for k in list(st)[:1]: del st[k]
    s, j = req("POST", "/_test/import", corrupt(drop_first_key)); check("I.state missing a section => 422, unchanged, no 5xx", s == 422 and req("GET", "/_test/export")[1] == base, (s, str(j)[:100]))
    def neg(st):
        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "balance" or k == "balance_minor" : o[k] = -5
                    walk(v)
            elif isinstance(o, list):
                for v in o: walk(v)
        walk(st)
    s, j = req("POST", "/_test/import", corrupt(neg)); print("   INFO negative balance import ->", s, str(j)[:100])
    check("I.import state w/ negative balance either 422-unchanged or ok-no-5xx", s < 500)
    if s == 204: req("POST", "/_test/import", ex)
    # state contains no plaintext password
    txt = json.dumps(ex)
    check("I.export has no plaintext passwords", "correct horse" not in txt and "zedzedzed" not in txt and "hunter2hunter2" not in txt)
    # reset clears imported
    reset(); check("I.reset clears imported tokens", req("GET", "/me", token=newu["token"])[0] == 401 and req("POST", "/auth/login", {"email": "zed@x.com", "password": "zedzedzed"})[0] == 401)
    check("I.reset clears idem keys", req("POST", "/payments", {"to_handle": "bob", "amount": 100}, headers=idem("pk"), token=login("ada@example.com"))[0] == 201)
    # export under concurrent writes is consistent
    reset(); T = tokens(); stop = threading.Event(); incons = []
    def writer():
        i = 0
        while not stop.is_set():
            i += 1; req("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem(f"w{i}{random.random()}"), token=T["ada"])
    ths = [threading.Thread(target=writer) for _ in range(8)]
    [t.start() for t in ths]
    exs = []
    for _ in range(15):
        s, e = req("GET", "/_test/export"); exs.append(e)
    stop.set(); [t.join() for t in ths]
    # import each snapshot and check sum
    bad = 0
    for e in exs[::3]:
        assert req("POST", "/_test/import", e)[0] == 204
        T2 = tokens()
        if total(T2) != 13500: bad += 1
    check("I.snapshots under write load keep sum invariant after import", bad == 0, bad)
    reset()
    # reset errors
    f = fx(); f["users"][0]["balance"] = -1
    s, j = req("POST", "/_test/reset", f); check("I.reset negative balance 422", s == 422 and errcode(j) == "validation_failed", (s, j))
    check("I.reset failure changed nothing", req("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[0] == 200)
    s, j = req("POST", "/_test/reset", rawbody="{x"); check("I.reset unparseable => 400", s == 400)
    print("   INFO reset {} ->", req("POST", "/_test/reset", {}))
    for name, mut in (("dup handle", lambda f: f["users"][1].__setitem__("handle", "ada")), ("bad handle", lambda f: f["users"][1].__setitem__("handle", "Bad Handle")), ("dup id", lambda f: f["users"][1].__setitem__("id", "u_ada")), ("dup email", lambda f: f["users"][1].__setitem__("email", "ada@example.com")), ("bad minor_units", lambda f: f.__setitem__("minor_units", 5)), ("unknown payment user", lambda f: f.__setitem__("payments", [{"id": "p", "from_user_id": "zz", "to_user_id": "u_ada", "amount": 1, "visibility": "public"}])), ("unknown operator", lambda f: f.__setitem__("settlement_operator_ids", ["zzz"])), ("float balance", lambda f: f["users"][0].__setitem__("balance", 1.5)), ("req unknown user", lambda f: f.__setitem__("requests", [{"id": "r", "requester_id": "zz", "payer_id": "u_ada", "amount": 1, "status": "pending"}]))):
        reset(); f = fx(); mut(f); s, j = req("POST", "/_test/reset", f)
        still = req("POST", "/auth/login", {"email": "op@example.com", "password": "correct horse"})[0]
        check(f"I.reset invalid fixture ({name}) -> 4xx not 5xx, state unchanged if rejected", s in (204, 400, 422) and (s == 204 or still == 200), (s, j))
    reset(fx(currency="JPY", minor_units=0)); T = tokens(); check("I.JPY reset /me", req("GET", "/me", token=T["ada"])[1]["currency"] == "JPY" and req("GET", "/me", token=T["ada"])[1]["minor_units"] == 0)
    reset(fx(currency="BHD", minor_units=3)); T = tokens(); check("I.BHD", req("GET", "/me", token=T["ada"])[1]["minor_units"] == 3)
    # fixture seeded with big balances / payments / requests
    big = fx(); big["users"][0]["balance"] = 2 ** 53 - 1; big["users"][1]["balance"] = 5
    s, j = req("POST", "/_test/reset", big); print("   INFO reset w/ 2^53-1 balance ->", s, j)
    T = tokens(); print("   INFO ada balance:", req("GET", "/me", token=T["ada"])[1]["balance"])
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 10 ** 9}, headers=idem("big"), token=T["ada"]); check("I.big-balance payment exact", s == 201 and req("GET", "/me", token=T["ada"])[1]["balance"] == 2 ** 53 - 1 - 10 ** 9, (s,))
    # fixture with seeded payments & requests: ids preserved and visible
    f = fx(payments=[{"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 500, "note": "coffee", "visibility": "private"}], requests=[{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi", "status": "pending"}, {"id": "rq_2", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1, "status": "paid", "payment_id": "p_1"}])
    reset(f); T = tokens()
    a_ = req("GET", "/activity", token=T["ada"])[1]["payments"]; check("I.seeded private payment visible to parties, not op", len(a_) == 1 and a_[0]["payment_id"] == "p_1" and req("GET", "/activity", token=T["op"])[1]["payments"] == [], a_)
    check("I.seeded request payable, seeded balances not replayed", req("POST", "/requests/rq_1/pay", {}, headers=idem("sp"), token=T["ada"])[0] == 201 and req("GET", "/me", token=T["ada"])[1]["balance"] == 10000 - 1200)
    check("I.seeded paid request not payable", req("POST", "/requests/rq_2/pay", {}, headers=idem("sp2"), token=T["ada"])[0] == 409)

# ---------------- J. protocol / misc
def J_misc():
    reset(); T = tokens(); a = T["ada"]
    check("J.health", req("GET", "/health") == (200, {"status": "ok"}))
    s, j, t, d = raw("GET", "/nope", token=a); check("J.unknown route 404 w/ error body", s == 404 and errcode(j) == "not_found", (s, t[:80]))
    s, j, t, d = raw("DELETE", "/payments", token=a); print("   INFO DELETE /payments ->", s, t[:80]); check("J.wrong method 4xx w/ error body", 400 <= s < 500 and errcode(j), (s, t[:80]))
    s, j, t, d = raw("GET", "/payments", token=a); print("   INFO GET /payments ->", s)
    s, j, t, d = raw("GET", "/me/", token=a); print("   INFO GET /me/ ->", s)
    s, j, t, d = raw("GET", "/me?x=%zz", token=a); check("J.bad percent-escape query no 5xx", s < 500, s)
    s, j, t, d = raw("GET", "/%00", token=a); check("J.NUL path no 5xx", s < 500, s)
    s, j, t, d = raw("GET", "/requests/" + "x" * 5000 + "/pay", token=a); check("J.huge path no 5xx", s < 500, s)
    s, j, t, d = raw("POST", "/requests/%E0%A4%A/pay", {}, headers=idem("x"), token=a); check("J.bad escape in id no 5xx", s < 500, s)
    s, j, t, d = raw("GET", "/me"); check("J.401 body shape", s == 401 and errcode(j) == "unauthenticated" and isinstance(j["error"]["message"], str))
    s, j, t, d = raw("GET", "/me", headers={"Content-Type": "text/plain"}, token=a); check("J.GET w/ odd content-type ok", s == 200)
    s, j, t, d = raw("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers={"Idempotency-Key": "ct", "Content-Type": "text/plain"}, token=a); print("   INFO POST w/ text/plain ->", s)
    s, j, t, d = raw("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers={"Idempotency-Key": "cs", "Content-Type": "application/json; charset=utf-8"}, token=a)
    check("J.content-type response is application/json; charset=utf-8", True)
    c = http.client.HTTPConnection(U.hostname, U.port); c.request("GET", "/me", headers={"Authorization": "Bearer " + a}); r = c.getresponse(); ct = r.getheader("Content-Type"); r.read()
    check("J.response Content-Type 'application/json; charset=utf-8'", ct and ct.lower().replace(" ", "") == "application/json;charset=utf-8", ct)
    ts = req("GET", "/me", token=a)
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("ts"), token=a)
    check("J.created_at RFC3339 with offset", re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)", j["created_at"]) and not j["created_at"].endswith("Z"), j["created_at"])
    check("J.ids <= 64 chars", all(len(str(j[k])) <= 64 for k in ("payment_id",)))
    check("J.currency in payment", j["currency"] == "EUR")
    # idempotency key header with unicode / whitespace
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers={"Idempotency-Key": "   "}, token=a); print("   INFO whitespace key ->", s, errcode(j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers={"Idempotency-Key": "ключ-😀".encode("utf-8").decode("latin-1")}, token=a); print("   INFO non-ascii key ->", s, errcode(j))
    # duplicate Idempotency-Key header
    c = http.client.HTTPConnection(U.hostname, U.port)
    c.putrequest("POST", "/payments"); c.putheader("Authorization", "Bearer " + a); c.putheader("Idempotency-Key", "d1"); c.putheader("Idempotency-Key", "d2"); c.putheader("Content-Type", "application/json")
    body = b'{"to_handle":"bob","amount":1}'; c.putheader("Content-Length", str(len(body))); c.endheaders(body); r = c.getresponse(); r.read(); print("   INFO duplicate Idempotency-Key header ->", r.status)
    check("J.dup header no 5xx", r.status < 500)
    # HTTP/1.1 keep-alive pipelining sanity + slowloris-ish partial body then complete
    import socket
    sk = socket.create_connection((U.hostname, U.port)); sk.sendall(b"POST /auth/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 200\r\n\r\n{")
    sk.settimeout(2); 
    try: sk.recv(100)
    except Exception: pass
    sk.close(); check("J.half-sent body connection doesn't wedge server", req("GET", "/health")[0] == 200)
    # 100 idle sockets
    socks = [socket.create_connection((U.hostname, U.port)) for _ in range(300)]
    t0 = time.time(); ok = req("GET", "/health")[0] == 200; [s_.close() for s_ in socks]
    check("J.300 idle connections don't block health", ok and time.time() - t0 < 2)
    # chunked request body
    sk = socket.create_connection((U.hostname, U.port)); b = b'{"email":"ada@example.com","password":"correct horse"}'
    sk.sendall(b"POST /auth/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n" + hex(len(b))[2:].encode() + b"\r\n" + b + b"\r\n0\r\n\r\n")
    resp = b""
    sk.settimeout(3)
    try:
        while True:
            d = sk.recv(4096)
            if not d: break
            resp += d
    except Exception: pass
    check("J.chunked request body login 200", resp.startswith(b"HTTP/1.1 200"), resp[:60])
    # oversized declared content-length without sending
    sk = socket.create_connection((U.hostname, U.port)); sk.sendall(b"POST /payments HTTP/1.1\r\nHost: x\r\nContent-Length: 99999999999\r\nConnection: close\r\n\r\n"); sk.settimeout(3)
    try: r_ = sk.recv(200)
    except Exception as e: r_ = repr(e).encode()
    print("   INFO huge content-length ->", r_[:40]); sk.close()
    check("J.still healthy", req("GET", "/health")[0] == 200)

# ---------------- K. mixed fuzz at concurrency: no 5xx, invariants
def K_fuzz():
    reset(); T = tokens(); toks = list(T.values()); hs = ["ada", "bob", "cy", "op", "zzz", "ADA", ""]
    amts = [1, 0, -1, 5, 100, 10**9, 10**10, "5", 1.5, None, True, 333, 1000]
    bad5 = []; stop = threading.Event(); invbad = []
    def watcher():
        while not stop.is_set():
            try:
                if total(T) != 13500: invbad.append("sum")
            except Exception as e: invbad.append(repr(e))
    # note: total() reads sequentially so a transient mismatch is possible between reads; use export-free check only at quiesce.
    def one(i):
        r = random.Random(i); who = r.choice(list(T)); tok = T[who]; k = f"f{r.randint(0, 40)}"
        op = r.choice(["pay", "reqq", "split", "settle", "decl", "canc", "payreq", "act", "reqs"])
        if op == "pay": res = raw("POST", "/payments", {"to_handle": r.choice(hs), "amount": r.choice(amts), "visibility": r.choice(["public", "private", "x"])}, headers=idem(k), token=tok)
        elif op == "reqq": res = raw("POST", "/requests", {"payer_handle": r.choice(hs), "amount": r.choice(amts)}, headers=idem(k), token=tok)
        elif op == "split": res = raw("POST", "/splits", {"amount": r.choice(amts), "participant_handles": r.sample(hs, r.randint(0, 4))}, headers=idem(k), token=tok)
        elif op == "settle": res = raw("POST", "/settlements", {"transfers": [{"from_handle": r.choice(hs), "to_handle": r.choice(hs), "amount": r.choice(amts)} for _ in range(r.randint(0, 4))]}, headers=idem(k), token=tok)
        elif op in ("decl", "canc", "payreq"):
            lst = req("GET", "/requests?limit=200", token=tok)[1]
            if not lst or not lst["requests"]: return (200, None, b"", 0)
            rid = r.choice(lst["requests"])["request_id"]
            if op == "payreq": res = raw("POST", f"/requests/{rid}/pay", {}, headers=idem(k), token=tok)
            else: res = raw("POST", f"/requests/{rid}/" + ("decline" if op == "decl" else "cancel"), {}, token=tok)
        elif op == "act": res = raw("GET", f"/activity?limit={r.choice([1, 50, 200, 0])}&offset={r.choice([0, 3, -1])}", token=tok)
        else: res = raw("GET", "/requests?direction=" + r.choice(["incoming", "outgoing", "x", ""]), token=tok)
        if res[0] >= 500: bad5.append((op, res[0], res[2][:100]))
        return res
    rs = pool(one, 3000)
    neg = [req("GET", "/me", token=t)[1]["balance"] for t in toks]
    check("K.fuzz 3000 mixed ops @50: zero 5xx", not bad5, bad5[:3])
    check("K.fuzz: all errors carry error body", all(isinstance(j, dict) and "error" in j for s, j, t, d in rs if s >= 400 and j is not None or (s >= 400 and t)))
    check("K.fuzz: balances nonneg and sum preserved", min(neg) >= 0 and sum(neg) == 13500, neg)
    check("K.fuzz: max latency < 5s", max(r[3] for r in rs) < 5, max(r[3] for r in rs))
    # every request is in exactly one terminal/pending state and paid requests have payment_id & payments exist once
    allr = {}
    for k in T:
        for r_ in req("GET", "/requests?limit=200", token=T[k])[1]["requests"]: allr[r_["request_id"]] = r_
    pays = {}
    for k in T:
        for p in req("GET", "/activity?limit=200", token=T[k])[1]["payments"]: pays[p["payment_id"]] = p
    paid = [r_ for r_ in allr.values() if r_["status"] == "paid"]
    per_req = {}
    for p in pays.values():
        if p["request_id"]: per_req[p["request_id"]] = per_req.get(p["request_id"], 0) + 1
    check("K.each request paid at most once, paid requests have exactly 1 payment", all(v == 1 for v in per_req.values()) and all(per_req.get(r_["request_id"]) == 1 and r_["payment_id"] in pays for r_ in paid), "")

# ---------------- L. invariants sampled DURING a write storm (via export snapshot - atomic)
def L_during():
    reset(); T = tokens(); stop = threading.Event(); bad = []
    def writer(i):
        r = random.Random(i)
        while not stop.is_set():
            a, b = r.sample(["ada", "bob", "cy", "op"], 2)
            raw("POST", "/payments", {"to_handle": b, "amount": r.randint(1, 3000)}, headers=idem(f"L{i}{r.random()}"), token=T[a])
    ths = [threading.Thread(target=writer, args=(i,)) for i in range(40)]; [t.start() for t in ths]
    snaps = 0
    t_end = time.time() + 6
    while time.time() < t_end:
        s, e = req("GET", "/_test/export")
        if s != 200: bad.append(s); continue
        snaps += 1
        txt = json.dumps(e)
        # balances in state: find all "balance" ints
        bals = []
        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k.startswith("balance") and isinstance(v, int): bals.append(v)
                    walk(v)
            elif isinstance(o, list): [walk(v) for v in o]
        walk(e["state"])
        if bals and (sum(bals) != 13500 or min(bals) < 0): bad.append((sum(bals), min(bals)))
    stop.set(); [t.join() for t in ths]
    check(f"L.{snaps} atomic exports during 40-writer storm: sum==13500 & nonneg", snaps > 3 and not bad, bad[:3])
    s, j, t, d = raw("GET", "/activity?limit=200", token=T["ada"]); check("L.reads fast after storm", d < 2)


# ---------------- M. extra attacks
def M_extra():
    import socket
    # signup races
    reset()
    rs = pool(lambda i: req("POST", "/auth/signup", {"email": "race@x.com", "password": "12345678", "display_name": "R"}), 50)
    c = [s for s, _ in rs]; check("M.50 concurrent signups same email: 1x201, 49x409 email_taken", c.count(201) == 1 and c.count(409) == 49 and all(errcode(j) == "email_taken" for s, j in rs if s == 409), sorted(set(c)))
    reset()
    rs = pool(lambda i: req("POST", "/auth/signup", {"email": f"same{'.' if i % 2 else '_'}h@d{i}.com".replace(".h", "_h").replace("same_h", "samehandle"), "password": "12345678", "display_name": "R"}), 50)
    c = [s for s, _ in rs]; check("M.50 concurrent signups same derived handle: exactly 1x201, rest 409 handle_taken", c.count(201) == 1 and c.count(409) == 49 and all(errcode(j) == "handle_taken" for s, j in rs if s == 409), (sorted(set(c)), [errcode(j) for s, j in rs if s == 409][:2]))
    reset()
    s, j = req("POST", "/auth/signup", {"email": "ada@other.com", "password": "12345678", "display_name": "x"}); check("M.signup handle collides with seeded handle => 409 handle_taken", s == 409 and errcode(j) == "handle_taken", (s, j))
    s, j = req("POST", "/auth/signup", {"email": "ADA@EXAMPLE.COM", "password": "12345678", "display_name": "x"}); check("M.signup seeded email different case => 409 email_taken", s == 409 and errcode(j) == "email_taken", (s, j))
    s, j = req("POST", "/auth/signup", {"email": "abcdefghijklmnopqrstuvwxyz1@x.com", "password": "12345678", "display_name": "x"})
    s2, j2 = req("POST", "/auth/signup", {"email": "abcdefghijklmnopqrstXXXX@x.com", "password": "12345678", "display_name": "x"}); check("M.truncation collision (20 chars) => handle_taken", (s, s2) == (201, 409) and errcode(j2) == "handle_taken", (s, s2, j2))
    # no account on handle_taken: same email can sign up later with different... (email stays free)
    s3, j3 = req("POST", "/auth/signup", {"email": "abcdefghijklmnopqrstXXXX@x.com", "password": "12345678", "display_name": "x"}); check("M.handle_taken repeat still 409 handle_taken (not email_taken)", errcode(j3) == "handle_taken", (s3, j3))
    # tokens
    toks = set()
    for i in range(30):
        toks.add(login("ada@example.com"))
    check("M.tokens unique and >=16 chars", len(toks) == 30 and all(len(t) >= 16 for t in toks))
    # response lost then retry (client aborts before reading)
    reset(); T = tokens()
    for path, body, who in (("/payments", {"to_handle": "bob", "amount": 111}, "ada"), ("/requests", {"payer_handle": "ada", "amount": 111}, "bob"), ("/splits", {"amount": 9, "participant_handles": ["ada", "bob"]}, "ada"), ("/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 111}]}, "op")):
        data = json.dumps(body).encode(); key = "lost" + path
        for _ in range(5):
            sk = socket.create_connection((U.hostname, U.port))
            sk.sendall(b"POST " + path.encode() + b" HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + T[who].encode() + b"\r\nIdempotency-Key: " + key.encode() + b"\r\nContent-Type: application/json\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data)
            sk.close()
        time.sleep(0.3)
        s, j = req("POST", path, body, headers=idem(key), token=T[who])
        check(f"M.lost response then retry {path}: 200 replay (original committed once)", s == 200, (s, j))
    ada = req("GET", "/me", token=T["ada"])[1]["balance"]
    check("M.lost-response retries moved money exactly once per op (ada 10000-111-111(settle))", ada == 10000 - 111 - 111, ada)
    check("M.lost: exactly one request and one split-request", len(req("GET", "/requests?limit=200", token=T["ada"])[1]["requests"]) == 2 , len(req("GET", "/requests?limit=200", token=T["ada"])[1]["requests"]))
    # same key concurrent but different bodies: exactly one wins
    reset(); T = tokens()
    rs = pool(lambda i: req("POST", "/payments", {"to_handle": "bob", "amount": 1 + i % 5}, headers=idem("diff"), token=T["ada"]), 50)
    c = [s for s, _ in rs]; check("M.same key, 5 different bodies concurrently: exactly 1x201; others 200 (same body) or 409", c.count(201) == 1 and set(c) <= {200, 201, 409}, sorted(set(c)))
    ada = req("GET", "/me", token=T["ada"])[1]["balance"]; check("M.…and exactly one payment applied", 10000 - ada in (1, 2, 3, 4, 5) and len(req("GET", "/activity", token=T["ada"])[1]["payments"]) == 1, ada)
    # order of entry errors in settlement
    s, j = req("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}, {"from_handle": "ada", "to_handle": "zzz", "amount": 1}, {"from_handle": "ada", "to_handle": "bob", "amount": 0}]}, headers=idem("e1"), token=T["op"])
    check("M.settlement first entry error by input order (404 at idx1 before 422 at idx2)", s == 404, (s, j))
    s, j = req("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 0}, {"from_handle": "ada", "to_handle": "zzz", "amount": 1}]}, headers=idem("e2"), token=T["op"])
    check("M.settlement idx0 422 before idx1 404", s == 422, (s, j))
    s, j = req("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "ada", "amount": 1}, {"from_handle": "ada", "to_handle": "zzz", "amount": 1}]}, headers=idem("e3"), token=T["op"])
    check("M.settlement idx0 self_payment before idx1 404", s == 422 and errcode(j) == "self_payment", (s, j))
    s, j = req("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1000000001}]}, headers=idem("e4"), token=T["op"]); check("M.settlement amount>1e9 => 422", s == 422, (s, j))
    s, j = req("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}, token=T["op"]); check("M.settlement missing key => 400", s == 400, (s, j))
    s, j = req("POST", "/settlements", {"transfers": {"0": 1}}, headers=idem("e5"), token=T["op"]); check("M.settlement transfers object => 4xx", s in (400, 422), (s, j))
    s, j = req("POST", "/settlements", {}, headers=idem("e6"), token=T["op"]); check("M.settlement no transfers => 422", s == 422, (s, j))
    s, j = req("POST", "/settlements", {"transfers": [None]}, headers=idem("e7"), token=T["op"]); check("M.settlement null entry => 4xx", s in (400, 422), (s, j))
    s, j = req("POST", "/settlements", {"transfers": [{"from_handle": 5, "to_handle": "bob", "amount": 1}]}, headers=idem("e8"), token=T["op"]); check("M.settlement non-string handle => 4xx", s in (400, 422), (s, j))
    # activity order newest first for sequential writes
    reset(); T = tokens()
    for i in range(12): req("POST", "/payments", {"to_handle": "bob", "amount": 1, "note": str(i)}, headers=idem(f"o{i}"), token=T["ada"]); time.sleep(0.002)
    notes = [p["note"] for p in req("GET", "/activity?limit=200", token=T["ada"])[1]["payments"]]
    check("M.activity newest first (sequential writes, ties same-ms)", notes == [str(i) for i in range(11, -1, -1)], notes)
    pg = []; off = 0
    while True:
        j = req("GET", f"/activity?limit=5&offset={off}", token=T["ada"])[1]; pg += [p["note"] for p in j["payments"]]; off += 5
        if not j["has_more"]: break
    check("M.pagination walk covers all once", pg == notes, pg)
    # reset under concurrent writes then verify
    reset(); T = tokens(); stop = threading.Event()
    def w(i):
        while not stop.is_set():
            raw("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem(f"rw{i}-{random.random()}"), token=T["ada"])
    ths = [threading.Thread(target=w, args=(i,)) for i in range(30)]; [t.start() for t in ths]
    time.sleep(0.5); t0 = time.time(); s, j = req("POST", "/_test/reset", fx()); dt = time.time() - t0
    stop.set(); [t.join() for t in ths]
    check("M.reset during write storm 204 within 10s", s == 204 and dt < 10, (s, dt))
    # after reset + writers: tokens from before reset are dead (new fixture, new tokens)
    # reset big fixtures / hashing cost
    for n, distinct in ((300, True), (2000, False), (1000, True)):
        users = [{"id": f"u{i}", "email": f"u{i}@x.com", "password": (f"pw-{i:06d}-xx" if distinct else "same password"), "display_name": "U", "handle": f"h{i}", "balance": 10} for i in range(n)]
        t0 = time.time(); s, j = req("POST", "/_test/reset", fx(users=users, settlement_operator_ids=[]), timeout=30); dt = time.time() - t0
        print(f"   INFO reset {n} users distinct_pw={distinct}: {s} in {dt:.2f}s")
        check(f"M.reset {n} users (distinct pw={distinct}) 204 < 10s", s == 204 and dt < 10, (s, dt))
        t0 = time.time(); s, j = req("POST", "/auth/login", {"email": f"u{n-1}@x.com", "password": (f"pw-{n-1:06d}-xx" if distinct else "same password")}); dt = time.time() - t0
        check(f"M.login of seeded user after {n}-user reset ok < 5s", s == 200 and dt < 5, (s, dt))
        rs = pool(lambda i: raw("POST", "/auth/login", {"email": f"u{i}@x.com", "password": (f"pw-{i:06d}-xx" if distinct else "same password")}), 50)
        check(f"M.50 concurrent seeded logins after {n}-user reset all 200, < 5s", all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5, (set(r[0] for r in rs), max(r[3] for r in rs)))
        rs = pool(lambda i: raw("POST", "/auth/login", {"email": f"u{i}@x.com", "password": "wrong"}), 50)
        check(f"M.50 concurrent wrong-password logins: all 401, < 5s", all(r[0] == 401 for r in rs) and max(r[3] for r in rs) < 5, (set(r[0] for r in rs), max(r[3] for r in rs)))
    s, ex = req("GET", "/_test/export", timeout=30); t0 = time.time(); s2, _ = req("POST", "/_test/import", ex, timeout=30)
    check("M.export+import of 1000-user state within 10s", s == 200 and s2 == 204 and time.time() - t0 < 10)
    # large fixture of payments
    users = [{"id": f"u{i}", "email": f"u{i}@x.com", "password": "same password", "display_name": "U", "handle": f"h{i}", "balance": 100} for i in range(50)]
    pays = [{"id": f"p{i}", "from_user_id": f"u{i % 50}", "to_user_id": f"u{(i + 1) % 50}", "amount": 1, "note": "n", "visibility": "public" if i % 2 else "private"} for i in range(20000)]
    t0 = time.time(); s, j = req("POST", "/_test/reset", fx(users=users, payments=pays, settlement_operator_ids=[]), timeout=30); dt = time.time() - t0
    check("M.reset with 20000 seeded payments < 10s", s == 204 and dt < 10, (s, dt))
    tk = login("u0@x.com", "same password"); t0 = time.time(); s, j = req("GET", "/activity?limit=200&offset=19000", token=tk); dt = time.time() - t0
    check("M.deep offset page on 20000 payments < 1s", s == 200 and dt < 1, (s, dt))
    rs = pool(lambda i: raw("GET", f"/activity?limit=200&offset={i * 50}", token=tk), 100)
    check("M.100 concurrent feed reads on 20000 payments all 200 < 5s", all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5, max(r[3] for r in rs))
    # extreme balances: 2^53 boundaries
    big = fx(); big["users"][0]["balance"] = 2 ** 53 - 1 - 13500 + 10000
    req("POST", "/_test/reset", big); T = tokens()
    for i in range(5): req("POST", "/payments", {"to_handle": "bob", "amount": 10**9}, headers=idem(f"b{i}"), token=T["ada"])
    check("M.balances exact near 2^53", total(T) == 2 ** 53 - 1 - 13500 + 10000 + 2500 + 0 + 1000, total(T))
    # receiver bigger than 2^53 after payments?
    big = fx(); big["users"][0]["balance"] = 2 ** 53 - 1; big["users"][1]["balance"] = 2 ** 53 - 1
    s, j = req("POST", "/_test/reset", big); print("   INFO two 2^53-1 balances reset ->", s, j)
    if s == 204:
        T = tokens(); s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 10**9}, headers=idem("ov"), token=T["ada"]); print("   INFO pay into 2^53-1 wallet ->", s, j and j.get("error"))
        check("M.no 5xx at balance overflow edge", s < 500)
        print("   INFO bob balance:", req("GET", "/me", token=T["bob"])[1]["balance"])

# ---------------- N. hardening: big/deep/odd inputs, idempotency equality, misc
def N_harden():
    import socket, unicodedata
    reset(); T = tokens(); a = T["ada"]
    # extra number forms
    n = [0]
    def pay(rawamt):
        n[0] += 1; return req("POST", "/payments", rawbody='{"to_handle":"bob","amount":%s}' % rawamt, headers=idem(f"nn{n[0]}"), token=a)
    for g in ("1E+3", "1e+3", "1000e-0", "0.001e6", "1.000e3", "10000e-1", "1000.000000000000000000000000000000"):
        s, j = pay(g); check(f"N.amount {g} accepted", s == 201 and j["amount"] == 1000, (s, j))
    for g in ("-0e0", "0e0", "1e-3", "999.9999999999999999999", "1e+", "--1", "1_000", "١٠٠", "1,000", "0b1", "1e3e3", "\"1e3\"", "[1000]"):
        s, j = pay(g); check(f"N.amount {g} rejected 4xx", s in (400, 422), (s, j))
    # idempotency equality semantics
    k = "eq1"
    s1, j1 = req("POST", "/payments", {"to_handle": "bob", "amount": 5}, headers=idem(k), token=a)
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 5, "visibility": "public"}, headers=idem(k), token=a); check("N.explicit default visibility is a different body => 409", s == 409, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 5, "note": ""}, headers=idem(k), token=a); check("N.explicit empty note is different body => 409", s == 409, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 5, "note": None}, headers=idem(k), token=a); check("N.claimed key + note:null => 409 (before validation)", s == 409, (s, j))
    s, j = req("POST", "/payments", rawbody='{"amount":4,"amount":5,"to_handle":"bob"}', headers=idem(k), token=a); check("N.duplicate JSON key last-wins equals original => 200", s == 200, (s, j))
    s, j = req("POST", "/payments", {"to_handle": "bob", "amount": "5"}, headers=idem(k), token=a); check("N.claimed key + amount string => 409", s == 409, (s, j))
    # key case + unicode normalisation
    for kk in ("Case", "case", "caf\u00e9", unicodedata.normalize("NFD", "caf\u00e9")):
        s, j = req("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem(kk.encode("utf-8").decode("latin-1")), token=a); check(f"N.key {kk!r} distinct => 201", s == 201, (s, j))
    # arrays order in settlements body matters
    tr = [{"from_handle": "ada", "to_handle": "bob", "amount": 1}, {"from_handle": "ada", "to_handle": "cy", "amount": 2}]
    s, j = req("POST", "/settlements", {"transfers": tr}, headers=idem("so"), token=T["op"]); s2, j2 = req("POST", "/settlements", {"transfers": tr[::-1]}, headers=idem("so"), token=T["op"])
    check("N.settlement transfer order change with same key => 409", (s, s2) == (201, 409), (s, s2))
    s2, j2 = req("POST", "/settlements", rawbody='{"transfers":[{"amount":1e0,"to_handle":"bob","from_handle":"ada"},{"to_handle":"cy","from_handle":"ada","amount":2.0}]}', headers=idem("so"), token=T["op"])
    check("N.settlement replay w/ reordered obj keys & numeric forms => 200 identical", s2 == 200 and j2 == j, (s2, j2))
    # pay payment semantics
    reset(); T = tokens()
    s, rq = req("POST", "/requests", {"payer_handle": "ada", "amount": 77, "note": "taxi"}, headers=idem("q"), token=T["bob"])
    s, p = req("POST", f"/requests/{rq['request_id']}/pay", {"visibility": "private"}, headers=idem("pq"), token=T["ada"])
    print("   INFO pay-payment body:", p)
    check("N.request payment direction payer->requester, amount, request_id", p["from_handle"] == "ada" and p["to_handle"] == "bob" and p["amount"] == 77 and p["request_id"] == rq["request_id"] and p["visibility"] == "private", p)
    r2 = req("GET", "/requests?status=paid", token=T["bob"])[1]["requests"]
    check("N.paid request carries payment_id and status", len(r2) == 1 and r2[0]["payment_id"] == p["payment_id"], r2)
    check("N.balances after pay", (req("GET", "/me", token=T["ada"])[1]["balance"], req("GET", "/me", token=T["bob"])[1]["balance"]) == (9923, 2577))
    # error body shape everywhere
    for method, path, body, hd, tok in (("GET", "/me", None, {}, None), ("POST", "/payments", {"to_handle": "x", "amount": 1}, idem("zz"), T["ada"]), ("POST", "/auth/login", {}, {}, None), ("GET", "/nothing", None, {}, T["ada"]), ("POST", "/_test/import", {}, {}, None)):
        s, j, t, d = raw(method, path, body, headers=hd, token=tok)
        check(f"N.error body shape {method} {path}", s >= 400 and isinstance(j, dict) and set(j) == {"error"} and set(j["error"]) >= {"code", "message"}, (s, t[:100]))
    # headers: auth edge cases
    for name, h in (("two spaces", "Bearer  " + T["ada"]), ("trailing space", "Bearer " + T["ada"] + " "), ("no scheme", T["ada"]), ("empty bearer", "Bearer "), ("BEARER", "BEARER " + T["ada"])):
        s, j, t, d = raw("GET", "/me", headers={"Authorization": h}); print(f"   INFO auth {name!r} -> {s}"); check(f"N.auth {name} no 5xx", s < 500)
    # oversized headers get JSON error?
    for L in (300, 5000, 20000):
        try:
            s, j, t, d = raw("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("k" * L), token=T["ada"])
        except Exception as e:
            s, j, t = repr(e), None, b""
        print(f"   INFO Idempotency-Key len {L} -> {s} {t[:60]}")
    s, j, t, d = raw("POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("k" * 300), token=T["ada"]); check("N.key 300 chars => 422", s == 422, (s, j))
    s, j, t, d = raw("GET", "/activity?x=" + "a" * 20000, token=T["ada"]); print("   INFO 20k query ->", s, t[:80])
    check("N.20k query no 5xx", isinstance(s, int) and s < 500)
    for m in ("HEAD", "OPTIONS", "PUT", "PATCH"):
        s, j, t, d = raw(m, "/health"); print(f"   INFO {m} /health -> {s}")
    s, j, t, d = raw("GET", "/health/"); print("   INFO /health/ ->", s); s, j, t, d = raw("GET", "/health?x=1"); check("N.health ignores query", s == 200)
    # deep nesting crash attempts
    for depth in (50000, 200000, 1000000):
        body = "[" * depth + "]" * depth
        try: s, j, t, d = raw("POST", "/payments", rawbody=body, headers=idem(f"deep{depth}"), token=T["ada"], timeout=20)
        except Exception as e: s, t = repr(e), b""
        print(f"   INFO nesting depth {depth} -> {s} {t[:60]}")
        check(f"N.nesting depth {depth}: 4xx JSON error (no 5xx / crash)", s in (400, 413, 422), s)
        check("N.alive after deep nesting", req("GET", "/health")[0] == 200)
    for depth in (50000, 200000):
        body = '{"to_handle":"bob","amount":1,"x":' + '{"a":' * depth + "1" + "}" * depth + "}"
        try: s, j, t, d = raw("POST", "/payments", rawbody=body, headers=idem(f"deepo{depth}"), token=T["ada"], timeout=20)
        except Exception as e: s, t = repr(e), b""
        print(f"   INFO object nesting {depth} -> {s}")
        check(f"N.object nesting {depth} in unknown field no 5xx", s in (201, 400, 413, 422), s)
    check("N.alive after deep nesting objects", req("GET", "/health")[0] == 200)
    # big array of transfers
    big = [{"from_handle": "ada", "to_handle": "bob", "amount": 1}] * 300000
    t0 = time.time(); s, j = req("POST", "/settlements", {"transfers": big}, headers=idem("bigtr"), token=T["op"], timeout=30); dt = time.time() - t0
    check("N.300k transfers => 413 fast (<5s) (r2 cap)", s in (413, 422) and dt < 5, (s, dt))
    t0 = time.time(); s, j = req("POST", "/splits", {"amount": 10, "participant_handles": [f"u{i}" for i in range(100000)]}, headers=idem("bigsp"), token=T["ada"], timeout=30); dt = time.time() - t0
    check("N.split 100k unknown handles => 413/404 fast (<5s) (r2 cap)", s in (413, 404) and dt < 5, (s, dt))
    t0 = time.time(); s, j = req("POST", "/splits", {"amount": 10, "participant_handles": ["bob"] * 100000}, headers=idem("bigsp2"), token=T["ada"], timeout=30); dt = time.time() - t0
    check("N.split 100k duplicates => 413/422 fast (r2 cap)", s in (413, 422) and dt < 5, (s, dt))
    # event loop starvation by a large body: parse a 30MB valid body while probing /health
    pad = "x" * 30_000_000  # r2: expect 413
    lat = []; stop = threading.Event()
    def probe():
        while not stop.is_set():
            lat.append(raw("GET", "/health", timeout=30)[3]); time.sleep(0.02)
    th = threading.Thread(target=probe); th.start()
    t0 = time.time(); s, j, t, d = raw("POST", "/payments", rawbody='{"to_handle":"bob","amount":1,"pad":"' + pad + '"}', headers=idem("pad30"), token=T["ada"], timeout=60); dt = time.time() - t0
    stop.set(); th.join()
    print(f"   INFO 30MB body: status {s} in {dt:.2f}s; max /health latency during = {max(lat):.2f}s")
    check("N.30MB body does not stall other requests > 1s", max(lat) < 1.0, max(lat))
    check("N.30MB body handled in < 5s", dt < 5, dt)
    check("N.30MB body -> 413", s == 413, s)
    # numbers-heavy 30MB body (custom number parsing)
    body = '{"to_handle":"bob","amount":1,"pad":[' + ",".join(["1.5e3"] * 3_000_000) + "]}"
    lat = []; stop = threading.Event(); th = threading.Thread(target=probe); th.start()
    t0 = time.time(); s, j, t, d = raw("POST", "/payments", rawbody=body, headers=idem("padnum"), token=T["ada"], timeout=60); dt = time.time() - t0
    stop.set(); th.join()
    print(f"   INFO 3M-number body: status {s} in {dt:.2f}s; max /health latency = {max(lat):.2f}s")
    check("N.number-heavy 20MB body < 5s and no >1s stall", dt < 5 and max(lat) < 1.0, (dt, max(lat)))
    # slow-drip body must not block others
    sk = socket.create_connection((U.hostname, U.port)); sk.sendall(b"POST /payments HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\n{")
    check("N.slow-drip body leaves server responsive", req("GET", "/health")[0] == 200); sk.close()
    # 50 simultaneous large(5MB) writes
    pad5 = "x" * 5_000_000
    rs = pool(lambda i: raw("POST", "/payments", rawbody='{"to_handle":"bob","amount":1,"pad":"' + pad5 + '"}', headers=idem(f"p5-{i}"), token=T["ada"], timeout=60), 50)
    check("N.50 concurrent 5MB writes all 413 and each < 5s (r2 cap)", all(r[0] == 413 for r in rs) and max(r[3] for r in rs) < 5, (set(r[0] for r in rs), max(r[3] for r in rs)))
    check("N.alive", req("GET", "/health")[0] == 200)

ALL = [A_races, B_overdraft, C_idem, D_numbers, E_splits, F_feed, G_auth, H_settle, I_export, J_misc, K_fuzz, L_during, M_extra, N_harden]
if __name__ == "__main__":
    only = sys.argv[2:] 
    for fn in ALL:
        if only and fn.__name__[0] not in only: continue
        print(f"== {fn.__name__}", flush=True)
        try: fn()
        except Exception as e:
            import traceback; traceback.print_exc(); check(fn.__name__ + " (script error)", False, repr(e))
    bad = [r for r in RES if not r[1]]
    print(f"\n{len(RES) - len(bad)} passed, {len(bad)} failed")
    sys.exit(1 if bad else 0)
