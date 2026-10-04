#!/usr/bin/env python3
"""Round 2 re-verification + regression attacks.  usage: attack_r2_regress.py NEW_URL [OLD_URL]
NEW_URL = revision 79b5f42; OLD_URL (optional) = revision df8f825, used for cross-version export->import.  RESETS both."""
import sys, json, time, socket, threading, random, http.client, urllib.parse
from concurrent.futures import ThreadPoolExecutor
sys.argv_saved = sys.argv
NEW = sys.argv[1]; OLD = sys.argv[2] if len(sys.argv) > 2 else None
RES = []; TL = threading.local()
def U(base): return urllib.parse.urlparse(base)
def raw(base, method, path, body=None, headers=None, token=None, rawbody=None, timeout=60):
    u = U(base); h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    if headers: h.update(headers)
    data = rawbody if rawbody is not None else (json.dumps(body) if body is not None else None)
    if isinstance(data, str): data = data.encode()
    t = time.time()
    small = data is None or len(data) < 100000
    for attempt in (0, 1):
        pool_ = getattr(TL, "p", None)
        if pool_ is None: pool_ = TL.p = {}
        c = pool_.get(base) if small else None
        if c is None: c = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
        try:
            try: c.request(method, path, body=data, headers=h)
            except (BrokenPipeError, ConnectionResetError):
                if small: raise
            r = c.getresponse(); txt = r.read()
            if small and r.getheader("Connection", "").lower() != "close": pool_[base] = c
            else: pool_.pop(base, None); c.close()
            break
        except (http.client.HTTPException, ConnectionError, OSError):
            pool_.pop(base, None)
            try: c.close()
            except Exception: pass
            if attempt or not small: raise
    try: j = json.loads(txt) if txt else None
    except Exception: j = None
    return r.status, j, txt, time.time() - t
def req(base, *a, **k):
    s, j, t, d = raw(base, *a, **k); return s, j
def check(name, ok, detail=""):
    RES.append((name, ok)); print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  :: " + str(detail)[:300]), flush=True)
def idem(k): return {"Idempotency-Key": k}
def code(j): return (j or {}).get("error", {}).get("code") if isinstance(j, dict) else None
def fx(n=0, **kw):
    f = {"currency": "EUR", "minor_units": 2, "users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10000},
        {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 2500},
        {"id": "u_cy", "email": "cy@example.com", "password": "hunter2hunter2", "display_name": "Cy", "handle": "cy", "balance": 0},
        {"id": "u_op", "email": "op@example.com", "password": "correct horse", "display_name": "Op", "handle": "op", "balance": 100000}],
        "settlement_operator_ids": ["u_op"]}
    f.update(kw); return f
def reset(base, f=None):
    s, j = req(base, "POST", "/_test/reset", f or fx(), timeout=60); assert s == 204, (s, j)
def login(base, e, p="correct horse"):
    s, j = req(base, "POST", "/auth/login", {"email": e, "password": p}); assert s == 200, (s, j); return j["token"]
def toks(base): return {"ada": login(base, "ada@example.com"), "bob": login(base, "bob@example.com"), "cy": login(base, "cy@example.com", "hunter2hunter2"), "op": login(base, "op@example.com")}
def pool(fn, n, w=50):
    def safe(i):
        try: return fn(i)
        except Exception as e: return (repr(e)[:40], None, b"", 0.0)   # client-side transport error (reported as status string)
    with ThreadPoolExecutor(w) as ex: return list(ex.map(safe, range(n)))
def sockreq(base, head, body=b"", read=True, timeout=5):
    u = U(base); sk = socket.create_connection((u.hostname, u.port), timeout=timeout); sk.sendall(head + body)
    out = b""
    if read:
        try:
            while b"\r\n\r\n" not in out:
                d = sk.recv(65536)
                if not d: break
                out += d
        except Exception as e: out = repr(e).encode()
    return sk, out

def G1_body_caps():
    reset(NEW); T = toks(NEW); a = T["ada"]
    def padded(n_total):
        base = '{"to_handle":"bob","amount":1,"pad":"%s"}'
        fixed = len(base % "")
        return (base % ("x" * (n_total - fixed))).encode()
    for n, exp in ((262144, 201), (262145, 413)):
        b = padded(n); assert len(b) == n
        s, j = req(NEW, "POST", "/payments", rawbody=b, headers=idem(f"cap{n}"), token=a)
        check(f"G1.API body of exactly {n} bytes -> {exp}", s == exp and (exp == 201 or code(j) == "payload_too_large"), (s, j))
    # declared length over cap, no body sent: answered immediately
    t0 = time.time(); sk, out = sockreq(NEW, b"POST /payments HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + a.encode() + b"\r\nContent-Length: 99999999999\r\nConnection: close\r\n\r\n"); sk.close()
    check("G1.huge declared Content-Length answered 413 at once (no waiting for body)", out.startswith(b"HTTP/1.1 413") and time.time() - t0 < 2, out[:60])
    # error body is JSON error shape
    s, j, t, d = raw(NEW, "POST", "/payments", rawbody=padded(300000), headers=idem("shape"), token=a)
    check("G1.413 body carries the standard error shape", s == 413 and isinstance(j, dict) and "message" in j.get("error", {}), t[:100])
    # chunked body over cap
    body = padded(400000); chunks = b"".join(hex(len(body[i:i+65536]))[2:].encode() + b"\r\n" + body[i:i+65536] + b"\r\n" for i in range(0, len(body), 65536)) + b"0\r\n\r\n"
    sk, out = sockreq(NEW, b"POST /payments HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + a.encode() + b"\r\nIdempotency-Key: ch1\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n", chunks); sk.close()
    check("G1.chunked body over cap -> 413", out.startswith(b"HTTP/1.1 413"), out[:80])
    # keep-alive connection after 413 is usable (drain)
    u = U(NEW); c = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    c.request("POST", "/payments", body=padded(1_000_000), headers={"Authorization": "Bearer " + a, "Idempotency-Key": "ka1", "Content-Type": "application/json"}); r = c.getresponse(); r.read()
    st1 = r.status
    try:
        c.request("GET", "/me", headers={"Authorization": "Bearer " + a}); r2 = c.getresponse(); r2.read(); st2 = r2.status
    except Exception as e: st2 = repr(e)
    check("G1.413 on keep-alive: connection drained or cleanly closed (next request works on same or new conn)", st1 == 413 and st2 in (200,) or (st1 == 413 and isinstance(st2, str)), (st1, st2))
    # legit 32 transfers each 200-char note, worst-case escaped emoji, explicit visibility
    reset(NEW); T = toks(NEW)
    note = "😀" * 200
    tr = [{"from_handle": "op", "to_handle": "ada", "amount": 1, "note": note, "visibility": "private"} for _ in range(32)]
    body = json.dumps({"transfers": tr})  # ensure_ascii => 😀 escapes (12 bytes per emoji)
    print("   INFO max-size settlement body bytes:", len(body))
    s, j = req(NEW, "POST", "/settlements", rawbody=body, headers=idem("max"), token=T["op"])
    check("G1.32 transfers x 200-emoji notes (escaped, %d B) -> 201, notes verbatim" % len(body), s == 201 and all(p["note"] == note for p in j["payments"]), (s, str(j)[:200]))
    s, j2 = req(NEW, "POST", "/settlements", rawbody=body, headers=idem("max"), token=T["op"]); check("G1.…replay 200 identical", s == 200 and j2 == j)
    # split with many participants within cap
    hs = [f"h{i}" for i in range(5000)]
    users = fx()["users"] + [{"id": f"x{i}", "email": f"x{i}@e.com", "password": "correct horse", "display_name": "X", "handle": h, "balance": 0} for i, h in enumerate(hs)]
    reset(NEW, fx(users=users)); T = toks(NEW)
    s, j = req(NEW, "POST", "/splits", {"amount": 10 ** 9, "participant_handles": ["ada"] + hs, "note": "n" * 200}, headers=idem("bigsplit"), token=T["ada"])
    check("G1.split with 5001 participants (~40KB body) 201, shares sum", s == 201 and sum(x["amount"] for x in j["shares"]) == 10 ** 9 and len(j["requests"]) == 5000, (s, str(j)[:150]))
    s, j2 = req(NEW, "POST", "/splits", {"amount": 10 ** 9, "participant_handles": ["ada"] + hs, "note": "n" * 200}, headers=idem("bigsplit"), token=T["ada"]); check("G1.…big split replay 200 identical", s == 200 and j2 == j)
    # 50 concurrent bodies near the cap (250KiB): all handled, health stays fast
    reset(NEW); T = toks(NEW); b = padded(250000); lat = []; stop = threading.Event()
    def probe():
        while not stop.is_set(): lat.append(raw(NEW, "GET", "/health")[3]); time.sleep(0.02)
    th = threading.Thread(target=probe); th.start()
    rs = pool(lambda i: raw(NEW, "POST", "/payments", rawbody=b, headers=idem(f"near{i}"), token=T["ada"]), 50)
    stop.set(); th.join()
    check("G1.50 concurrent 250KB bodies: all 201, each < 5s, /health < 1s", all(r[0] == 201 for r in rs) and max(r[3] for r in rs) < 5 and max(lat) < 1, (sorted({r[0] for r in rs}), max(r[3] for r in rs), max(lat)))
    # F1 repro: 50 x 42MB
    big = ('{"to_handle":"bob","amount":1,"pad":[' + ",".join(["1.5e3"] * 7_000_000) + "]}").encode()
    t0 = time.time(); rs = pool(lambda i: raw(NEW, "POST", "/payments", rawbody=big, headers=idem(f"huge{i}"), token=T["ada"]), 50)
    sts = sorted({r[0] for r in rs}, key=str); print("   INFO 50 x %.0fMB statuses %s in %.1fs" % (len(big) / 1e6, sts, time.time() - t0))
    check("G1.F1 repro (50 x 42MB): 413 (or client-side transport reset while uploading), fast, service alive", all(x == 413 or isinstance(x, str) for x in sts) and 413 in sts and time.time() - t0 < 10 and req(NEW, "GET", "/health")[0] == 200, sts)
    # F2: number-heavy at cap and concurrent
    nb = ('{"to_handle":"bob","amount":1,"pad":[' + ",".join(["1.5e3"] * 40000) + "]}").encode(); print("   INFO number-heavy body bytes:", len(nb))
    lat = []; stop = threading.Event(); th = threading.Thread(target=probe); th.start()
    rs = pool(lambda i: raw(NEW, "POST", "/payments", rawbody=nb, headers=idem(f"numh{i}"), token=T["ada"]), 50)
    stop.set(); th.join()
    check("G1.F2 repro: 50 concurrent near-cap number-heavy bodies, max latency < 2s, /health < 1s", max(r[3] for r in rs) < 2 and max(lat) < 1, (max(r[3] for r in rs), max(lat)))

def G2_budget_dos():
    # shared 256MiB budget: connections that declare big bodies and stall must not starve normal traffic
    reset(NEW); T = toks(NEW); a = T["ada"]
    held = []
    for i in range(12):
        sk, _ = sockreq(NEW, b"POST /_test/reset HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 33000000\r\n\r\n{", read=False); held.append(sk)
    time.sleep(0.5)
    s, j = req(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("dos1"), token=a)
    check("G2.12 stalled 32MiB reset uploads do not block/refuse a normal payment", s == 201, (s, j))
    s, j = req(NEW, "GET", "/health"); check("G2.health ok while reset uploads stalled", s == 200)
    held2 = []
    for i in range(60):
        sk, _ = sockreq(NEW, b"POST /payments HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + a.encode() + b"\r\nContent-Length: 260000\r\n\r\n{", read=False); held2.append(sk)
    time.sleep(0.5)
    s, j = req(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("dos2"), token=a)
    check("G2.60 stalled 260KB API uploads do not refuse a normal payment", s == 201, (s, j))
    for sk in held + held2: sk.close()
    # stalled reset uploads then a legit reset still works (budget released after close)
    time.sleep(0.5); reset(NEW); check("G2.reset still works after stalled uploads closed", True)
    # concurrent large resets use budget; API still served
    users = [{"id": f"u{i}", "email": f"u{i}@x.com", "password": "same password", "display_name": "U", "handle": f"h{i}", "balance": 1} for i in range(10)]
    pays = [{"id": f"p{i}", "from_user_id": f"u{i % 10}", "to_user_id": f"u{(i + 1) % 10}", "amount": 1, "note": "n" * 100, "visibility": "public"} for i in range(60000)]
    body = json.dumps(fx(users=users, payments=pays)); print("   INFO large reset body MB:", round(len(body) / 1e6, 1))
    t0 = time.time(); s, j, t, d = raw(NEW, "POST", "/_test/reset", rawbody=body, timeout=30)
    check("G2.reset with %.1fMB fixture (60000 payments) -> 204 < 10s" % (len(body) / 1e6), s == 204 and d < 10, (s, d, t[:100]))
    rs = pool(lambda i: raw(NEW, "POST", "/_test/reset", rawbody=body, timeout=40), 6, 6)
    print("   INFO 6 concurrent large resets:", sorted({(r[0]) for r in rs}), [round(r[3], 1) for r in rs])
    check("G2.6 concurrent large resets: no 5xx/crash, all within 10s each", all(r[0] in (204, 413, 503) for r in rs) and max(r[3] for r in rs) < 10 and req(NEW, "GET", "/health")[0] == 200, [(r[0], round(r[3], 1)) for r in rs])
    # fixture beyond reset cap
    huge = '{"currency":"EUR","minor_units":2,"users":[],"pad":"' + "x" * (33 * 1024 * 1024) + '"}'
    s, j, t, d = raw(NEW, "POST", "/_test/reset", rawbody=huge); print("   INFO 33MiB reset body ->", s, t[:80])
    check("G2.33MiB reset body: no 5xx (r3: cap raised to 448MiB, so 204/422 expected)", s in (204, 400, 413, 422), (s, t[:100]))
    s, j, t, d = raw(NEW, "POST", "/_test/import", rawbody=huge); check("G2.33MiB import body (no track) -> 422 not 413 (r3)", s == 422, (s, t[:100]))

def G2b_budget_exhaustion():
    # connections that really upload ~30MB of a declared 32MiB reset body and then stall: can they exhaust the shared budget
    reset(NEW); T = toks(NEW); a = T["ada"]; held = []
    chunk = b"x" * (30 * 1024 * 1024)
    def up(i):
        u = U(NEW); sk = socket.create_connection((u.hostname, u.port), timeout=120); held.append(sk)
        try: sk.sendall(b"POST /_test/reset HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 33554432\r\n\r\n{" + chunk)
        except Exception: pass
    for i in range(12): threading.Thread(target=up, args=(i,), daemon=True).start()
    time.sleep(1)
    s, j = req(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("exh1"), token=a)
    check("G2b.12 stalled uploads holding ~360MB: normal payment still served (201)", s == 201, (s, j))
    s, j = req(NEW, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}); check("G2b.login still served", s == 200, (s, j))
    s, j = req(NEW, "POST", "/_test/reset", fx()); print("   INFO reset while uploads stalled ->", s, j and j.get("error")); check("G2b.reset itself (small) still served", s == 204, (s, j))
    for sk in held: sk.close()
    time.sleep(1); s, j = req(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("exh2"), token=login(NEW, "ada@example.com")); check("G2b.service recovers after uploads closed", s == 201, (s, j))

def G3_export_size():
    # state growth through the API: does an unchanged export remain importable under the 32MiB cap?
    f = fx(); f["users"][0]["balance"] = 10 ** 12
    reset(NEW, f); T = toks(NEW); a = T["ada"]
    note = "n" * 200
    def w(i): return raw(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 1, "note": note}, headers=idem("g3-%d-%s" % (i + G3_n[0], "k" * 40)), token=a)
    G3_n = [0]; t0 = time.time(); done = 0; size = 0
    while time.time() - t0 < 240:
        rs = pool(w, 2000); G3_n[0] += 2000; done += sum(1 for r in rs if r[0] == 201)
        s, ex, txt, d = raw(NEW, "GET", "/_test/export", timeout=60)
        size = len(txt)
        if size > 34 * 1024 * 1024 or s != 200: break  # keep going past the 32MiB import cap (33554432 bytes)
    print(f"   INFO after {done} successful payments (200-char notes, 40-char keys): export {size/1e6:.1f} MB, last export {d:.1f}s, status {s}, elapsed {time.time()-t0:.0f}s")
    s2, j2, t2, d2 = raw(NEW, "POST", "/_test/import", rawbody=txt, timeout=60)
    print(f"   INFO import of that export: {s2} in {d2:.1f}s")
    print("   INFO export bytes:", size, "import cap bytes: 33554432")
    check("G3.unchanged export of a state built through the API (%d payments, %.1f MB) is accepted by import" % (done, size / 1e6), s2 == 204, (s2, t2[:150]))
    check("G3.export/import each within 10s at that size", d < 10 and d2 < 10, (d, d2))
    print("   INFO bytes per payment (export):", size // max(done, 1), "-> payments before 32MiB cap:", int(32 * 1024 * 1024 / max(size / max(done, 1), 1)))

def G4_passwords_and_xversion():
    reset(NEW); T = toks(NEW)
    s, ex = req(NEW, "GET", "/_test/export"); hs = [u["password_hash"] for u in ex["state"]["users"]]
    check("G4.per-user salt: no two seeded users share a stored hash even with same password", len(set(hs)) == len(hs), hs)
    print("   INFO hash format:", hs[0][:40])
    s, j = req(NEW, "POST", "/auth/signup", {"email": "zed@x.com", "password": "zedzedzed", "display_name": "Z"}); ztok = j["token"]
    s, ex = req(NEW, "GET", "/_test/export"); print("   INFO signup hash format:", [u["password_hash"][:30] for u in ex["state"]["users"] if u["email"] == "zed@x.com"])
    check("G4.import: login for seeded and signup users", req(NEW, "POST", "/_test/import", ex)[0] == 204 and req(NEW, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[0] == 200 and req(NEW, "POST", "/auth/login", {"email": "zed@x.com", "password": "zedzedzed"})[0] == 200 and req(NEW, "GET", "/me", token=ztok)[0] == 200)
    check("G4.wrong passwords still 401 after import", req(NEW, "POST", "/auth/login", {"email": "zed@x.com", "password": "zedzedzee"})[0] == 401 and req(NEW, "POST", "/auth/login", {"email": "ada@example.com", "password": "Correct horse"})[0] == 401)
    # seeded user whose password equals another's: login of each independent
    reset(NEW, fx(users=[{"id": "a", "email": "a@x.com", "password": "same-password", "display_name": "A", "handle": "a", "balance": 1}, {"id": "b", "email": "b@x.com", "password": "same-password", "display_name": "B", "handle": "b", "balance": 1}]))
    check("G4.two seeded users same password both log in; cross-password fails", req(NEW, "POST", "/auth/login", {"email": "a@x.com", "password": "same-password"})[0] == 200 and req(NEW, "POST", "/auth/login", {"email": "b@x.com", "password": "same-password"})[0] == 200 and req(NEW, "POST", "/auth/login", {"email": "b@x.com", "password": "other-password"})[0] == 401)
    # 50 concurrent logins + signups timing in same-process
    reset(NEW); rs = pool(lambda i: raw(NEW, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}), 100)
    check("G4.100 logins @50 all 200, max < 5s", all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5, max(r[3] for r in rs))
    rs = pool(lambda i: raw(NEW, "POST", "/auth/signup", {"email": f"s{i}@x.com", "password": "pw-%08d" % i, "display_name": "S"}), 200)
    check("G4.200 signups @50 all 201, max < 5s", all(r[0] == 201 for r in rs) and max(r[3] for r in rs) < 5, (sorted({r[0] for r in rs}), max(r[3] for r in rs)))
    lat = []; stop = threading.Event()
    def pr():
        while not stop.is_set(): lat.append(raw(NEW, "POST", "/auth/login", {"email": "ada@example.com", "password": "nope"})[3]); time.sleep(0.01)
    # money paths stay fast while hashing storm runs
    T = toks(NEW); th = threading.Thread(target=pr); th.start()
    rs2 = pool(lambda i: raw(NEW, "POST", "/auth/signup", {"email": f"st{i}@x.com", "password": "pw-%08d" % i, "display_name": "S"}), 150)
    t0 = time.time(); pr_ = pool(lambda i: raw(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem(f"hp{i}"), token=T["ada"]), 100)
    stop.set(); th.join()
    check("G4.payments during signup storm all 201 and < 1.5s each", all(r[0] == 201 for r in pr_) and max(r[3] for r in pr_) < 1.5, (sorted({r[0] for r in pr_}), max(r[3] for r in pr_)))
    if OLD:
        # data written by the earlier revision loaded into the current one
        reset(OLD); To = toks(OLD)
        req(OLD, "POST", "/payments", {"to_handle": "bob", "amount": 100, "note": "old"}, headers=idem("oldk"), token=To["ada"])
        s, rq = req(OLD, "POST", "/requests", {"payer_handle": "ada", "amount": 300}, headers=idem("oldr"), token=To["bob"])
        s, sp = req(OLD, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 5}]}, headers=idem("olds"), token=To["op"])
        s, nu = req(OLD, "POST", "/auth/signup", {"email": "old@x.com", "password": "oldoldold", "display_name": "O"})
        s, exo = req(OLD, "GET", "/_test/export")
        s, j = req(NEW, "POST", "/_test/import", exo)
        check("G4.XVER import of export from df8f825 into 79b5f42 -> 204", s == 204, (s, j))
        if s == 204:
            check("G4.XVER seeded login (old N=16384 hash) works", req(NEW, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[0] == 200)
            check("G4.XVER signup login + old token work", req(NEW, "POST", "/auth/login", {"email": "old@x.com", "password": "oldoldold"})[0] == 200 and req(NEW, "GET", "/me", token=nu["token"])[0] == 200 and req(NEW, "GET", "/me", token=To["ada"])[0] == 200)
            check("G4.XVER retry of old payment replays 200", req(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 100, "note": "old"}, headers=idem("oldk"), token=To["ada"])[0] == 200)
            check("G4.XVER old pending request payable once, balances right", req(NEW, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("xp"), token=To["ada"])[0] == 201 and req(NEW, "GET", "/me", token=To["ada"])[1]["balance"] == 10000 - 100 - 300)
            check("G4.XVER old settlement replay 200 identical", req(NEW, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "cy", "amount": 5}]}, headers=idem("olds"), token=To["op"]) == (200, sp))
            s, exn = req(NEW, "GET", "/_test/export"); check("G4.XVER re-export then re-import in new revision is lossless", req(NEW, "POST", "/_test/import", exn)[0] == 204 and req(NEW, "GET", "/_test/export")[1] == exn)

def G5_headers():
    reset(NEW); T = toks(NEW); a = T["ada"]
    for L, exp in ((255, 201), (256, 422), (20000, 422), (500000, 422), (900000, 422)):
        try: s, j, t, d = raw(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("k" * L), token=a)
        except Exception as e: s, j = repr(e), None
        check(f"G5.Idempotency-Key of {L} chars -> {exp} validation_failed JSON", s == exp and (exp == 201 or code(j) == "validation_failed"), (s, j))
    for L in (1_100_000, 3_000_000):
        sk, out = sockreq(NEW, b"GET /me HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + a.encode() + b"\r\nX-Pad: " + b"p" * L + b"\r\nConnection: close\r\n\r\n"); sk.close()
        print(f"   INFO {L}-byte header ->", out[:40])
        check(f"G5.{L}-byte header -> 431 (or clean 4xx), no crash", (out.startswith(b"HTTP/1.1 431") or out.startswith(b"HTTP/1.1 4") or b"Reset" in out or b"Broken" in out or out == b"") and req(NEW, "GET", "/health")[0] == 200, out[:60])
    s, j, t, d = raw(NEW, "GET", "/activity?x=" + "a" * 100000, token=a); check("G5.100KB query string handled (200/4xx), no 5xx", s < 500, s)
    # many headers
    hdrs = b"".join(b"X-H%d: v\r\n" % i for i in range(5000))
    sk, out = sockreq(NEW, b"GET /health HTTP/1.1\r\nHost: x\r\n" + hdrs + b"Connection: close\r\n\r\n"); sk.close(); print("   INFO 5000 headers ->", out[:30])
    check("G5.5000 headers no crash", req(NEW, "GET", "/health")[0] == 200)

def G6_core_smoke():
    # core invariants re-asserted under the new body handling
    reset(NEW); T = toks(NEW)
    rs = pool(lambda i: req(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 100}, headers=idem("same"), token=T["ada"]), 50)
    c = [s for s, _ in rs]; check("G6.fresh-key identical x50: 1x201 49x200", c.count(201) == 1 and c.count(200) == 49, sorted(set(c)))
    rs = pool(lambda i: req(NEW, "POST", "/payments", {"to_handle": "bob", "amount": 333}, headers=idem(f"o{i}"), token=T["cy"]), 100)
    check("G6.zero-balance wallet overdraft storm: all 409", all(s == 409 for s, _ in rs))
    s, j = req(NEW, "POST", "/payments", rawbody='{"to_handle":"bob","amount":1e3}', headers=idem("e3"), token=T["ada"]); check("G6.1e3 accepted", s == 201 and j["amount"] == 1000, (s, j))
    s, j = req(NEW, "POST", "/payments", rawbody='{"to_handle":"bob","amount":-0}', headers=idem("m0"), token=T["ada"]); check("G6.-0 rejected 422", s == 422)

if __name__ == "__main__":
    ONLY = sys.argv[3:]
    for fn in (G1_body_caps, G2_budget_dos, G2b_budget_exhaustion, G3_export_size, G4_passwords_and_xversion, G5_headers, G6_core_smoke):
        if ONLY and fn.__name__.split('_')[0] not in ONLY: continue
        print("==", fn.__name__, flush=True)
        try: fn()
        except Exception as e:
            import traceback; traceback.print_exc(); check(fn.__name__ + " (script error)", False, repr(e))
    bad = [r for r in RES if not r[1]]; print(f"\n{len(RES) - len(bad)} passed, {len(bad)} failed"); sys.exit(1 if bad else 0)
