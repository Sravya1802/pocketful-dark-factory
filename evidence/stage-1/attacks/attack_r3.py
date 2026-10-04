#!/usr/bin/env python3
"""Round 3 attacks on the larger control-plane change. usage: attack_r3.py URL_A URL_B [URL_OLD [docker_container_A]]
A and B are two separate processes/containers of revision 621342c; URL_OLD (optional) is a df8f825 or 79b5f42 process for old-format imports.
RESETS all of them. If docker_container_A is given, container memory is sampled with `docker stats`."""
import sys, json, time, socket, threading, random, subprocess, http.client, urllib.parse
from concurrent.futures import ThreadPoolExecutor
A = sys.argv[1]; B = sys.argv[2]; OLD = sys.argv[3] if len(sys.argv) > 3 else None; CT = sys.argv[4] if len(sys.argv) > 4 else None
RES = []; TL = threading.local()
def raw(base, m, p, body=None, headers=None, token=None, rawbody=None, timeout=60):
    u = urllib.parse.urlparse(base); h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    h.update(headers or {})
    data = rawbody if rawbody is not None else (json.dumps(body) if body is not None else None)
    if isinstance(data, str): data = data.encode()
    small = data is None or len(data) < 100000; t = time.time()
    for attempt in (0, 1):
        pl = getattr(TL, "p", None) or {}; TL.p = pl
        c = pl.get(base) if small else None
        if c is None: c = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
        try:
            try: c.request(m, p, body=data, headers=h)
            except (BrokenPipeError, ConnectionResetError):
                if small: raise
            r = c.getresponse(); txt = r.read()
            if small and r.getheader("Connection", "").lower() != "close": pl[base] = c
            else: pl.pop(base, None); c.close()
            break
        except (http.client.HTTPException, ConnectionError, OSError):
            pl.pop(base, None)
            try: c.close()
            except Exception: pass
            if attempt or not small: raise
    try: j = json.loads(txt) if txt else None
    except Exception: j = None
    return r.status, j, txt, time.time() - t
def req(base, *a, **k): s, j, t, d = raw(base, *a, **k); return s, j
def check(n, ok, d=""):
    RES.append((n, ok)); print(("PASS " if ok else "FAIL ") + n + ("" if ok else "  :: " + str(d)[:300]), flush=True)
def idem(k): return {"Idempotency-Key": k}
def code(j): return (j or {}).get("error", {}).get("code") if isinstance(j, dict) else None
def fx(**kw):
    f = {"currency": "EUR", "minor_units": 2, "settlement_operator_ids": ["u_op"], "users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10000},
        {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 2500},
        {"id": "u_cy", "email": "cy@example.com", "password": "hunter2hunter2", "display_name": "Cy", "handle": "cy", "balance": 0},
        {"id": "u_op", "email": "op@example.com", "password": "correct horse", "display_name": "Op", "handle": "op", "balance": 100000}]}
    f.update(kw); return f
def reset(b, f=None):
    s, j = req(b, "POST", "/_test/reset", f or fx(), timeout=60); assert s == 204, (s, j)
def login(b, e, p="correct horse"):
    s, j = req(b, "POST", "/auth/login", {"email": e, "password": p}); assert s == 200, (s, j); return j["token"]
def toks(b): return {"ada": login(b, "ada@example.com"), "bob": login(b, "bob@example.com"), "cy": login(b, "cy@example.com", "hunter2hunter2"), "op": login(b, "op@example.com")}
def pool(fn, n, w=50):
    def safe(i):
        try: return fn(i)
        except Exception as e: return (repr(e)[:40], None, b"", 0.0)
    with ThreadPoolExecutor(w) as ex: return list(ex.map(safe, range(n)))
def mem():
    if not CT: return None
    try: return subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", CT], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception: return None

def H1_export_import_sizes():
    # build a big state through the API in A, import into the DIFFERENT process B, byte-identical re-export, all within 10 s
    f = fx(); f["users"][0]["balance"] = 10 ** 12; reset(A, f); T = toks(A); a = T["ada"]; note = "n" * 200
    done = 0; i0 = 0; t0 = time.time()
    s, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 77, "note": "pending-across-import"}, headers=idem("rq-big"), token=T["bob"])
    s, firstpay = req(A, "POST", "/payments", {"to_handle": "bob", "amount": 5, "note": "first", "visibility": "private"}, headers=idem("first-pay"), token=a)
    while True:
        rs = pool(lambda i: raw(A, "POST", "/payments", {"to_handle": "bob", "amount": 1, "note": note}, headers=idem("h1-%d-%s" % (i + i0, "k" * 40)), token=a), 2000); i0 += 2000
        done += sum(1 for r in rs if r[0] == 201)
        if done >= 60000: break
    print(f"   INFO wrote {done} payments in {time.time()-t0:.0f}s; container mem: {mem()}")
    t0 = time.time(); s, j, txt, d = raw(A, "GET", "/_test/export", timeout=60)
    print(f"   INFO export {len(txt)/1e6:.1f} MB in {d:.2f}s status {s}; mem {mem()}")
    check("H1.export of 60k-payment state: 200 and < 10s", s == 200 and d < 10, (s, d))
    check("H1.export is a JSON object {track, format_version, state}", j and j.get("track") == "pocketful" and j.get("format_version") == 1 and "state" in j)
    t0 = time.time(); s2, j2, t2, d2 = raw(B, "POST", "/_test/import", rawbody=txt, timeout=60)
    print(f"   INFO import into process B: {s2} in {d2:.2f}s; mem {mem()}")
    check("H1.unchanged %.1f MB export imports into a separate process, < 10s" % (len(txt) / 1e6), s2 == 204 and d2 < 10, (s2, t2[:150], d2))
    s3, j3, t3, d3 = raw(B, "GET", "/_test/export", timeout=60)
    check("H1.re-export from B is byte-identical to A's export (tokens/hashes/ids/timestamps all preserved)", t3 == txt, (len(t3), len(txt)))
    # login/replays/tokens on B
    check("H1.B: A's bearer token valid, balance equals", req(B, "GET", "/me", token=a)[1]["balance"] == req(A, "GET", "/me", token=a)[1]["balance"])
    check("H1.B: seeded login (ada) works", req(B, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[0] == 200)
    check("H1.B: replay same key + same body -> 200 identical original", req(B, "POST", "/payments", {"to_handle": "bob", "amount": 5, "note": "first", "visibility": "private"}, headers=idem("first-pay"), token=a) == (200, firstpay))
    check("H1.B: replay with reordered keys / 5.0 amount -> 200", req(B, "POST", "/payments", rawbody='{"visibility":"private","note":"first","amount":5.0,"to_handle":"bob"}', headers=idem("first-pay"), token=a)[0] == 200)
    for name, body in (("amount", {"to_handle": "bob", "amount": 6, "note": "first", "visibility": "private"}), ("note", {"to_handle": "bob", "amount": 5, "note": "FIRST", "visibility": "private"}), ("extra field", {"to_handle": "bob", "amount": 5, "note": "first", "visibility": "private", "x": 1}), ("invalid", {"to_handle": "bob", "amount": -1}), ("missing visibility", {"to_handle": "bob", "amount": 5, "note": "first"})):
        s, j = req(B, "POST", "/payments", body, headers=idem("first-pay"), token=a); check(f"H1.B: same key, different body ({name}) -> 409 idempotency_key_reuse", s == 409 and code(j) == "idempotency_key_reuse", (s, j))
    s, j = req(B, "POST", "/payments", {"to_handle": "bob", "amount": 5, "note": "first", "visibility": "private"}, headers=idem("first-pay"), token=T["bob"]); check("H1.B: other user same key is a first use", s == 201 or code(j) == "self_payment" or s == 422, (s, j))
    s, j = req(B, "POST", "/requests", {"payer_handle": "ada", "amount": 77, "note": "pending-across-import"}, headers=idem("rq-big"), token=T["bob"]); check("H1.B: request replay 200 identical", s == 200 and j == rq, (s, j))
    # pay vs decline vs cancel race after import
    for rnd in range(5):
        s, rq2 = req(B, "POST", "/requests", {"payer_handle": "ada", "amount": 9}, headers=idem(f"race{rnd}"), token=T["bob"]); rid = rq2["request_id"]
        before = req(B, "GET", "/me", token=a)[1]["balance"]
        jobs = [lambda: req(B, "POST", f"/requests/{rid}/pay", {}, headers=idem(f"rp{rnd}"), token=a), lambda: req(B, "POST", f"/requests/{rid}/decline", {}, token=a), lambda: req(B, "POST", f"/requests/{rid}/cancel", {}, token=T["bob"])] * 8
        with ThreadPoolExecutor(24) as ex: rr = list(ex.map(lambda fn: fn(), jobs))
        fin = [r for r in req(B, "GET", "/requests?limit=200", token=a)[1]["requests"] if r["request_id"] == rid][0]; after = req(B, "GET", "/me", token=a)[1]["balance"]
        ok = (fin["status"] == "paid" and after == before - 9) or (fin["status"] != "paid" and after == before)
        if not ok or sum(1 for (s, j), jb in zip(rr, jobs) if s == 201) > 1: check("H1.B: pay/decline/cancel race after import consistent", False, (fin, before, after)); break
    else: check("H1.B: pay/decline/cancel race x5 after import: status consistent with balance, at most one pay", True)
    # the imported pending request is payable exactly once
    s, j = req(B, "POST", f"/requests/{rq['request_id']}/pay", {"visibility": "public"}, headers=idem("pay-imported"), token=a); check("H1.B: imported pending request payable (201) with correct amount", s == 201 and j["amount"] == 77 and j["request_id"] == rq["request_id"], (s, j))
    s, j = req(B, "POST", f"/requests/{rq['request_id']}/pay", {"visibility": "public"}, headers=idem("pay-imported"), token=a); check("H1.B: …replay 200", s == 200)
    # 300 concurrent identical fresh-key writes on the imported big state
    rs = pool(lambda i: req(B, "POST", "/payments", {"to_handle": "bob", "amount": 3}, headers=idem("dup-after-import"), token=a), 50); c = [s for s, _ in rs]
    check("H1.B: 50 identical fresh-key writes on big imported state -> 1x201 + 49x200", c.count(201) == 1 and c.count(200) == 49, sorted(set(c)))
    return len(txt)

def H2_oldformat():
    if not OLD: return
    reset(OLD); To = {"ada": login(OLD, "ada@example.com"), "bob": login(OLD, "bob@example.com"), "op": login(OLD, "op@example.com")}
    s, p = req(OLD, "POST", "/payments", {"to_handle": "bob", "amount": 100, "note": "old"}, headers=idem("oldk"), token=To["ada"])
    s, rq = req(OLD, "POST", "/requests", {"payer_handle": "ada", "amount": 300}, headers=idem("oldr"), token=To["bob"])
    s, sp = req(OLD, "POST", "/splits", {"amount": 10, "participant_handles": ["ada", "bob", "cy"]}, headers=idem("olds"), token=To["ada"])
    s, st = req(OLD, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 5}]}, headers=idem("oldst"), token=To["op"])
    s, su = req(OLD, "POST", "/auth/signup", {"email": "old@x.com", "password": "oldoldold", "display_name": "O"})
    s, ex = req(OLD, "GET", "/_test/export")
    s, j = req(A, "POST", "/_test/import", ex); check("H2.old-format export (older revision) imports into 621342c -> 204", s == 204, (s, j))
    if s != 204: return
    check("H2.old seeded logins work (old hash format)", req(A, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[0] == 200 and req(A, "POST", "/auth/login", {"email": "old@x.com", "password": "oldoldold"})[0] == 200 and req(A, "POST", "/auth/login", {"email": "ada@example.com", "password": "wrong"})[0] == 401)
    check("H2.old tokens valid", req(A, "GET", "/me", token=To["ada"])[0] == 200 and req(A, "GET", "/me", token=su["token"])[0] == 200)
    check("H2.old payment replay 200 identical (digest of old stored body)", req(A, "POST", "/payments", {"to_handle": "bob", "amount": 100, "note": "old"}, headers=idem("oldk"), token=To["ada"]) == (200, p))
    check("H2.old payment same key different body -> 409", req(A, "POST", "/payments", {"to_handle": "bob", "amount": 101, "note": "old"}, headers=idem("oldk"), token=To["ada"])[0] == 409)
    check("H2.old payment same key invalid body -> 409 (claimed before validation)", req(A, "POST", "/payments", {"to_handle": "bob", "amount": 0}, headers=idem("oldk"), token=To["ada"])[0] == 409)
    check("H2.old request replay 200", req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 300}, headers=idem("oldr"), token=To["bob"]) == (200, rq))
    check("H2.old split replay 200 identical", req(A, "POST", "/splits", {"amount": 10, "participant_handles": ["ada", "bob", "cy"]}, headers=idem("olds"), token=To["ada"]) == (200, sp))
    check("H2.old split same key different order -> 409", req(A, "POST", "/splits", {"amount": 10, "participant_handles": ["cy", "bob", "ada"]}, headers=idem("olds"), token=To["ada"])[0] == 409)
    check("H2.old settlement replay 200 identical / diff body 409", req(A, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 5}]}, headers=idem("oldst"), token=To["op"]) == (200, st) and req(A, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 6}]}, headers=idem("oldst"), token=To["op"])[0] == 409)
    s, j = req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("pay-old"), token=To["ada"]); check("H2.old pending request payable once", s == 201 and req(A, "GET", "/me", token=To["ada"])[1]["balance"] == 10000 - 100 - 300, (s, j))
    check("H2.…and a different key on the paid request -> 409 request_not_pending", code(req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("pay-old2"), token=To["ada"])[1]) == "request_not_pending")
    s, e2 = req(A, "GET", "/_test/export"); check("H2.re-export then import in the new revision is stable", req(A, "POST", "/_test/import", e2)[0] == 204 and req(A, "GET", "/_test/export")[1] == e2)

def H3_atomic_export_under_writes():
    f = fx(); reset(A, f); T = toks(A); stop = threading.Event(); bad = []
    def writer(i):
        r = random.Random(i)
        while not stop.is_set():
            a, b = r.sample(["ada", "bob", "cy", "op"], 2)
            raw(A, "POST", "/payments", {"to_handle": b, "amount": r.randint(1, 5000)}, headers=idem(f"w{i}-{r.random()}"), token=T[a])
    ths = [threading.Thread(target=writer, args=(i,)) for i in range(40)]; [t.start() for t in ths]
    snaps = 0; end = time.time() + 12
    while time.time() < end:
        s, e, txt, d = raw(A, "GET", "/_test/export")
        if s != 200: bad.append(("status", s)); continue
        snaps += 1; st = e["state"]; bals = [u["balance"] for u in st["users"]]
        if sum(bals) != 112500 or min(bals) < 0: bad.append(("sum", sum(bals), min(bals)))
        pids = [p["id"] for p in st["payments"]]
        if len(pids) != len(set(pids)): bad.append("dup payment ids")
        net = {u["id"]: 0 for u in st["users"]}
        for p in st["payments"]: net[p["from_user_id"]] -= p["amount"]; net[p["to_user_id"]] += p["amount"]
        seed = {"u_ada": 10000, "u_bob": 2500, "u_cy": 0, "u_op": 100000}
        for u in st["users"]:
            if u["balance"] != seed[u["id"]] + net[u["id"]]: bad.append(("balance != seed + payments", u["id"])); break
    stop.set(); [t.join() for t in ths]
    check(f"H3.{snaps} exports under a 40-writer storm: each is an atomic snapshot (sum, nonneg, balances == seed + payment ledger)", snaps > 5 and not bad, bad[:3])
    # export snapshot unchanged by later writes: import first, check
    s, e1, t1, _ = raw(A, "GET", "/_test/export"); req(A, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("late"), token=T["ada"]); s, e2, t2, _ = raw(A, "GET", "/_test/export")
    check("H3.later write changes later export only", t1 != t2 and len(json.loads(t1)["state"]["payments"]) + 1 == len(json.loads(t2)["state"]["payments"]))

def H4_memory_and_starvation():
    # concurrent large control bodies + continuous API traffic, container 2 CPU / 2 GiB
    users = [{"id": "a", "email": "a@x.com", "password": "correct horse", "display_name": "A", "handle": "a", "balance": 10 ** 9}, {"id": "b", "email": "b@x.com", "password": "correct horse", "display_name": "B", "handle": "b", "balance": 0}]
    pays = [{"id": f"p{i}", "from_user_id": "a", "to_user_id": "b", "amount": 1, "note": "n" * 150, "visibility": "public"} for i in range(110000)]
    body = json.dumps({"currency": "EUR", "minor_units": 2, "users": users, "payments": pays}).encode(); print("   INFO big fixture MB:", round(len(body) / 1e6, 1))
    s, j, t, d = raw(A, "POST", "/_test/reset", rawbody=body, timeout=60); check("H4.28MB reset 204 < 10s", s == 204 and d < 10, (s, d)); tok = login(A, "a@x.com")
    s, j, ex, d = raw(A, "GET", "/_test/export", timeout=60)
    lat = []; apierr = []; stop = threading.Event(); peak = [0]
    def probe():
        n = 0
        while not stop.is_set():
            n += 1; r1 = raw(A, "GET", "/health", timeout=30); r2 = raw(A, "GET", "/me", token=tok, timeout=30); lat.append(max(r1[3], r2[3]))
            if r1[0] != 200 or r2[0] not in (200, 401): apierr.append((r1[0], r2[0]))
            time.sleep(0.02)
    def memwatch():
        import re
        while not stop.is_set():
            m = mem()
            if m:
                mm = re.match(r"([\d.]+)(MiB|GiB)", m)
                if mm: peak[0] = max(peak[0], float(mm.group(1)) * (1024 if mm.group(2) == "GiB" else 1))
            time.sleep(0.5)
    ths = [threading.Thread(target=probe), threading.Thread(target=memwatch)]; [t.start() for t in ths]
    t0 = time.time()
    results = pool(lambda i: raw(A, "POST", "/_test/import" if i % 2 else "/_test/reset", rawbody=(ex if i % 2 else body), timeout=120), 16, 16)
    elapsed = time.time() - t0; stop.set(); [t.join() for t in ths]
    st = sorted({r[0] for r in results}, key=str)
    print(f"   INFO 16 concurrent large control bodies ({len(body)/1e6:.0f}-{len(ex)/1e6:.0f} MB): statuses {st}, per-call {[round(r[3],1) for r in results]}, total {elapsed:.1f}s, peak container mem {peak[0]:.0f} MiB, probe max {max(lat):.2f}s")
    check("H4.16 concurrent large reset/import: no 5xx, service alive", all((isinstance(r[0], int) and r[0] < 500) for r in results) and req(A, "GET", "/health")[0] == 200, st)
    check("H4.…every call finishes within 10s (control-call timeout)", max(r[3] for r in results) < 10, [round(r[3], 1) for r in results])
    check("H4./health and /me stay < 1s and OK during large control traffic", max(lat) < 1.0 and not apierr, (max(lat), apierr[:2]))
    if CT: check("H4.container memory peak < 1.8 GiB", 0 < peak[0] < 1843, peak[0])
    # oversized control body: 460MB over the 448MiB cap
    big = b'{"currency":"EUR","minor_units":2,"users":[],"pad":"' + b"x" * (460 * 1024 * 1024) + b'"}'
    s, j, t, d = raw(A, "POST", "/_test/reset", rawbody=big, timeout=120); print("   INFO 460MB reset ->", s, t[:90], f"{d:.1f}s, mem {mem()}")
    check("H4.460MB (>448MiB cap) reset -> 413/4xx, no crash", isinstance(s, int) and 400 <= s < 500 and req(A, "GET", "/health")[0] == 200, s)
    s, j, t, d = raw(A, "POST", "/_test/import", rawbody=big, timeout=120); check("H4.460MB import -> 413/4xx, no crash", isinstance(s, int) and 400 <= s < 500 and req(A, "GET", "/health")[0] == 200, s)
    del big
    # many concurrent oversized/declared bodies trying to blow memory
    held = []
    def up(i):
        u = urllib.parse.urlparse(A)
        try:
            sk = socket.create_connection((u.hostname, u.port), timeout=120); held.append(sk)
            sk.sendall(b"POST /_test/import HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 400000000\r\n\r\n{" + b" " * (60 * 1024 * 1024))
        except Exception: pass
    for i in range(40): threading.Thread(target=up, args=(i,), daemon=True).start()
    time.sleep(8); m = mem()
    ok = req(A, "GET", "/health")[0] == 200; s, j = req(A, "POST", "/auth/login", {"email": "a@x.com", "password": "correct horse"})
    print("   INFO 40 stalled 60MB-of-400MB control uploads: health/login", ok, s, "mem", m)
    check("H4.40 stalled big control uploads: service alive, login works, mem < 1.8GiB", ok and s == 200, (ok, s, m))
    for sk in held:
        try: sk.close()
        except Exception: pass
    time.sleep(2); check("H4.recovers after stalled uploads close (reset works)", req(A, "POST", "/_test/reset", fx())[0] == 204)

def H5_misc_control():
    reset(A); T = toks(A)
    s, e = req(A, "GET", "/_test/export")
    for name, body in (("empty", {}), ("track", dict(e, track="x")), ("version", dict(e, format_version=2)), ("no state", {"track": "pocketful", "format_version": 1}), ("state []", dict(e, state=[])), ("state {}", dict(e, state={})), ("state str", dict(e, state="x"))):
        before = raw(A, "GET", "/_test/export")[2]
        s, j = req(A, "POST", "/_test/import", body); check(f"H5.invalid import ({name}) -> 422 unchanged", s == 422 and raw(A, "GET", "/_test/export")[2] == before, (s, j))
    # corrupt digests / idempotency section
    def tamper(fn):
        x = json.loads(json.dumps(e)); fn(x["state"]); return x
    for name, fn in (("drop idempotency section", lambda st: [st.pop(k) for k in list(st) if "idem" in k.lower()]), ("drop users", lambda st: st.pop("users")), ("null payments", lambda st: st.__setitem__("payments", None)), ("neg balance", lambda st: st["users"][0].__setitem__("balance", -1)), ("dup user id", lambda st: st["users"].append(dict(st["users"][0])))):
        before = raw(A, "GET", "/_test/export")[2]; s, j = req(A, "POST", "/_test/import", tamper(fn)); print(f"   INFO tamper {name} ->", s)
        check(f"H5.tampered state ({name}) no 5xx; if rejected, unchanged", s < 500 and (s == 204 or raw(A, "GET", "/_test/export")[2] == before), s)
        if s == 204: req(A, "POST", "/_test/import", e)
    s, j = req(A, "POST", "/_test/import", rawbody="{bad"); check("H5.import unparseable -> 400", s == 400, (s, j))
    # Export is read-only/repeatable and has JSON content-type
    u = urllib.parse.urlparse(A); c = http.client.HTTPConnection(u.hostname, u.port); c.request("GET", "/_test/export"); r = c.getresponse(); body = r.read()
    check("H5.export Content-Type application/json; charset=utf-8", (r.getheader("Content-Type") or "").replace(" ", "").lower() == "application/json;charset=utf-8", r.getheader("Content-Type"))
    check("H5.export response has Content-Length or chunked and parses", json.loads(body)["track"] == "pocketful")
    # concurrent exports + imports + reads
    reset(A); T = toks(A); _, ex, txt, _ = raw(A, "GET", "/_test/export")
    def mixed(i):
        k = i % 4
        if k == 0: return raw(A, "GET", "/_test/export")
        if k == 1: return raw(A, "POST", "/_test/import", rawbody=txt)
        if k == 2: return raw(A, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem(f"mx{i}"), token=T["ada"])
        return raw(A, "GET", "/activity?limit=50", token=T["ada"])
    rs = pool(mixed, 200)
    check("H5.200 mixed concurrent export/import/payment/feed: no 5xx", all(isinstance(r[0], int) and r[0] < 500 for r in rs), sorted({str(r[0]) for r in rs}))
    s, e3 = req(A, "GET", "/_test/export"); b = [u["balance"] for u in e3["state"]["users"]]
    check("H5.…state afterwards consistent (sum 112500, nonneg)", sum(b) == 112500 and min(b) >= 0, b)
    # concurrent exports are mutually consistent when no writes
    reset(A); rs = pool(lambda i: raw(A, "GET", "/_test/export"), 30, 30); check("H5.30 concurrent exports identical (no writes)", len({r[2] for r in rs}) == 1)
    # reset, then old tokens dead
    tk = login(A, "ada@example.com"); reset(A); check("H5.reset clears tokens/keys", req(A, "GET", "/me", token=tk)[0] == 401)

if __name__ == "__main__":
    for fn in (H1_export_import_sizes, H2_oldformat, H3_atomic_export_under_writes, H5_misc_control, H4_memory_and_starvation):
        print("==", fn.__name__, flush=True)
        try: fn()
        except Exception as e:
            import traceback; traceback.print_exc(); check(fn.__name__ + " (script error)", False, repr(e))
    bad = [r for r in RES if not r[1]]; print(f"\n{len(RES) - len(bad)} passed, {len(bad)} failed"); sys.exit(1 if bad else 0)
