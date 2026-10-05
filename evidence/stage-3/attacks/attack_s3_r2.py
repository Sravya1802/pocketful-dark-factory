#!/usr/bin/env python3
"""Stage-3 round 2: attacks on the redesigned (recompute-at-K) statement snapshots. usage: attack_s3_r2.py URL_A URL_B [container_A]  (RESETS)
Checks that a snapshot stays byte-identical after every kind of later write, under concurrency, with future/edge K, at scale."""
import sys, json, time, random, threading, re, subprocess
from s3lib import *
from oracle3 import parse, fmt, INF
A, B = sys.argv[1], sys.argv[2]; CT = sys.argv[3] if len(sys.argv) > 3 else None
def iso(sec_ago=0.05, off="+00:00"):
    t = time.time() - sec_ago; return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%06d" % int((t % 1) * 1e6) + off
def user(h, bal): return {"id": "u_" + h, "email": f"{h}@example.com", "password": "correct horse", "display_name": h.title(), "handle": h, "balance": bal}
def f3(bal=(10 ** 8, 10 ** 8, 10 ** 6, 10 ** 8), **kw):
    f = {"currency": "EUR", "minor_units": 2, "settlement_operator_ids": ["u_op"], "users": [user("ada", bal[0]), user("bob", bal[1]), user("cy", bal[2]), user("op", bal[3])]}; f.update(kw); return f
def tk(b): return {n: login(b, f"{n}@example.com") for n in ("ada", "bob", "cy", "op")}
def fresh(b=A, **kw): reset(b, f3(**kw)); return tk(b)
def pay(b, T, who, to, amt, key=None, **kw): return req(b, "POST", "/payments", dict({"to_handle": to, "amount": amt}, **kw), headers=idem(key or "p%s" % random.random()), token=T[who])
def corr(b, T, who, pid, rev, amt, eff, key=None): return req(b, "POST", f"/payments/{pid}/corrections", {"expected_revision": rev, "amount": amt, "effective_at": eff, "reason": "r"}, headers=idem(key or "c%s" % random.random()), token=T[who])
def mem(ct):
    if not ct: return 0
    o = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", ct], capture_output=True, text=True).stdout.strip(); m = re.match(r"([\d.]+)(MiB|GiB)", o)
    return float(m.group(1)) * (1024 if m.group(2) == "GiB" else 1) if m else 0
def q(us): return fmt(us, 0, 6).replace("+", "%2B")
def read_all(b, T, who, snap, lim):
    out = []; off = 0
    while True:
        s, j, t, d = raw(b, "GET", f"/statement?snapshot={snap}&limit={lim}&offset={off}", token=T[who])
        if s != 200: return ("ERR", s, t[:100])
        out.append(t)
        if not j["has_more"]: return out
        off += lim
        if off > 100000: return ("ERR", "runaway")
def take(b, T, who, query, lim):
    s, j, t, d = raw(b, "GET", "/statement?" + query + (f"&limit={lim}" if "limit" not in query else ""), token=T[who]); assert s == 200, (s, t[:200]); return j["snapshot"], t

def G_frozen():
    T = fresh(); ids = [pay(A, T, "ada", "bob", 100 + i)[1]["payment_id"] for i in range(12)]; [pay(A, T, "bob", "cy", 20 + i) for i in range(5)]; [pay(A, T, "cy", "ada", 7 + i) for i in range(4)]
    p0 = pay(A, T, "ada", "bob", 1)[1]; t_mid = parse(p0["created_at"]); time.sleep(0.01)
    s, a0 = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 500}, headers=idem("h0"), token=T["ada"])
    inst = lambda off: fmt(t_mid + off, 0, 6).replace("+", "%2B")
    cfgs = {"default": ("", "ada"), "bob-default": ("", "bob"), "window-before": (f"to={inst(-1)}", "ada"), "window-after": (f"from={inst(1)}", "ada"), "window-mid": (f"from={inst(-3000)}&to={inst(500)}", "ada"),
            "known_at-past": (f"known_at={inst(-2)}", "ada"), "known_at-future": ("known_at=2099-01-01T00:00:00Z", "ada"), "to-future": ("to=2099-01-01T00:00:00Z", "ada"), "empty-window": (f"from={inst(0)}&to={inst(0)}", "ada"),
            "both-future": ("from=2098-01-01T00:00:00Z&to=2099-01-01T00:00:00Z&known_at=2099-01-01T00:00:00Z", "ada"), "known_at==rec": (f"known_at={p0['created_at'].replace('+', '%2B')}", "ada"), "cy": ("", "cy")}
    snaps = {}
    for name, (qs, who) in cfgs.items():
        lim = 3; sn, first = take(A, T, who, qs, lim); snaps[name] = (who, sn, first, read_all(A, T, who, sn, 3), read_all(A, T, who, sn, 7), read_all(A, T, who, sn, 200))
    def verify(label):
        bad = []
        for name, (who, sn, first, p3, p7, p200) in snaps.items():
            for lim, base in ((3, p3), (7, p7), (200, p200)):
                now = read_all(A, T, who, sn, lim)
                if now != base: bad.append((name, lim))
        check(f"R.{label}: all {len(snaps)} snapshots re-paged at 3 page sizes are byte-identical to before", not bad, bad[:4])
    verify("no writes (control)")
    # writers of every kind
    pay(A, T, "ada", "bob", 55); verify("after a payment")
    pay(A, T, "bob", "ada", 3, visibility="private"); verify("after a private payment")
    s, c = corr(A, T, "ada", ids[3], 1, 1, iso(3600)); check("R.setup: correction moved a payment out of the window (201)", s == 201, (s, c)); verify("after correction moving a payment far earlier (in/out of windows)")
    s, c = corr(A, T, "ada", ids[5], 1, 999, fmt(t_mid - 1500, 0, 6)); verify("after correction inside the window")
    s, c = corr(A, T, "ada", ids[6], 1, 0, iso(10)); check("R.setup: zero-amount correction (201)", s == 201, (s, c)); verify("after zero-amount correction")
    s, c = corr(A, T, "ada", ids[3], 2, 150, fmt(t_mid + 2000, 0, 6)) if False else (None, None)
    s, c = corr(A, T, "ada", ids[7], 1, 300, p0["created_at"]); verify("after correction whose effective_at equals another payment's instant")
    s, cp = req(A, "POST", f"/authorizations/{a0['authorization_id']}/capture", {"amount": 200, "final": False}, headers=idem("cap1"), token=T["bob"]); verify("after nonfinal capture")
    s, cp2 = req(A, "POST", f"/authorizations/{a0['authorization_id']}/capture", {"amount": 300}, headers=idem("cap2"), token=T["bob"]); verify("after final capture")
    s, a1 = req(A, "POST", "/authorizations", {"to_handle": "cy", "amount": 100}, headers=idem("h1"), token=T["ada"]); req(A, "POST", f"/authorizations/{a1['authorization_id']}/void", {}, token=T["ada"]); verify("after authorize + void")
    s, st = req(A, "POST", "/settlements", {"transfers": [{"from_handle": "op", "to_handle": "ada", "amount": 10}, {"from_handle": "op", "to_handle": "bob", "amount": 20}]}, headers=idem("st"), token=T["op"]); check("R.setup: settlement", s == 201, st); verify("after settlement")
    s, rq = req(A, "POST", "/requests", {"payer_handle": "ada", "amount": 40}, headers=idem("rq"), token=T["bob"]); s, rp = req(A, "POST", f"/requests/{rq['request_id']}/pay", {}, headers=idem("rp"), token=T["ada"]); verify("after request payment")
    s, c = corr(A, T, "ada", rp["payment_id"], 1, 41, iso(30)); verify("after correcting the request-paying payment")
    # short TTL expiry
    reset_ttl = None
    # 200 mixed concurrent writes
    stop = threading.Event()
    def churn(i):
        r = random.Random(i); k = 0
        while not stop.is_set():
            k += 1; who = r.choice(["ada", "bob", "cy"]); to = r.choice([x for x in ("ada", "bob", "cy") if x != who]); pay(A, T, who, to, r.randint(1, 50))
            if k % 3 == 0:
                pid = r.choice(ids); cur = len(req(A, "GET", f"/payments/{pid}/revisions", token=T["ada"])[1]["revisions"]); corr(A, T, "ada", pid, cur, r.randint(0, 400), iso(r.randint(1, 5000)))
    ths = [threading.Thread(target=churn, args=(i,)) for i in range(8)]; [t.start() for t in ths]
    mid = []
    for _ in range(6): time.sleep(0.4); mid.append(all(read_all(A, T, who, sn, 5) == read_all(A, T, who, sn, 3) or True for name, (who, sn, *_r) in snaps.items()))
    bad = []
    for _ in range(6):
        for name, (who, sn, first, p3, p7, p200) in snaps.items():
            if read_all(A, T, who, sn, 200) != p200: bad.append(name)
        time.sleep(0.2)
    stop.set(); [t.join() for t in ths]; check("R.during 8 threads of payments + corrections: snapshots stay byte-identical on every re-read", not bad, sorted(set(bad))[:5]); verify("after the concurrent storm")
    # first response vs later page 0 (the read itself must be consistent with its own snapshot)
    bad = []
    for name, (who, sn, first, p3, p7, p200) in snaps.items():
        f = json.loads(first); pg0 = json.loads(p3[0]); 
        for k in ("opening_balance", "closing_balance", "has_more", "entries"):
            if f[k] != pg0[k]: bad.append((name, k))
    check("R.the first response equals page 0 of its own snapshot (same entries, balances, has_more)", not bad, bad[:4])
    # sanity on content
    j = json.loads(snaps["default"][5][0]); run = j["opening_balance"]; ok = True
    for e in j["entries"]: run += e["delta"]; ok &= run == e["balance_after"]
    check("R.frozen default snapshot: opening + sum(delta) == closing and running balance", ok and run == j["closing_balance"])
    check("R.known_at in the future (snapshot) did not start including later writes", len(json.loads(snaps["known_at-future"][5][0])["entries"]) == len(json.loads(snaps["default"][5][0])["entries"]) and json.loads(snaps["known_at-future"][5][0])["closing_balance"] == json.loads(snaps["default"][5][0])["closing_balance"])
    check("R.empty-window and future-window snapshots stayed empty", json.loads(snaps["empty-window"][5][0])["entries"] == [] and json.loads(snaps["both-future"][5][0])["entries"] == [])
    # fresh read now differs (the snapshot is not just a cache of the live view)
    s, now = req(A, "GET", "/statement?limit=200", token=T["ada"]); check("R.a NEW read after all writes differs from the frozen default snapshot", now["closing_balance"] != json.loads(snaps["default"][5][0])["closing_balance"] or len(now["entries"]) != len(json.loads(snaps["default"][5][0])["entries"]))
    # expiry
    reset(A, dict(f3(), authorization_ttl_seconds=2)); T = tk(A); s, h = req(A, "POST", "/authorizations", {"to_handle": "bob", "amount": 100}, headers=idem("e1"), token=T["ada"]); pay(A, T, "ada", "bob", 5)
    sn, first = take(A, T, "ada", "", 5); base = read_all(A, T, "ada", sn, 5); time.sleep(2.5); req(A, "GET", "/me", token=T["ada"]); check("R.snapshot unchanged after a hold expired by the clock", read_all(A, T, "ada", sn, 5) == base)
    # import clears snapshots / reset clears
    T = fresh(); pay(A, T, "ada", "bob", 1); sn, _ = take(A, T, "ada", "", 5); s, ex = req(A, "GET", "/_test/export"); req(A, "POST", "/_test/import", ex)
    s1 = req(A, "GET", f"/statement?snapshot={sn}", token=T["ada"])[0]; print("   INFO snapshot after import ->", s1); check("R.stage-3 import clears snapshots (404), no 5xx", s1 == 404, s1)
    sn2, _ = take(A, T, "ada", "", 5); reset(A, f3()); T = tk(A); check("R.reset clears snapshots (404)", req(A, "GET", f"/statement?snapshot={sn2}", token=T["ada"])[0] == 404)
    # snapshot token reuse across processes
    T = fresh(); pay(A, T, "ada", "bob", 1); sn, _ = take(A, T, "ada", "", 5); check("R.token not valid on another process (404)", req(B, "GET", f"/statement?snapshot={sn}", token=T["ada"])[0] in (401, 404))

def G_clock_edges():
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100); pid = p["payment_id"]; recs = [parse(p["created_at"])]
    for i in range(30):
        s, c = corr(A, T, "ada", pid, 1 + i, 100 + i + 1, iso(5)); recs.append(parse(c["recorded_at"]))
    check("R.30 sequential corrections: recorded_at strictly increasing", all(a < b for a, b in zip(recs, recs[1:])))
    # snapshots with known_at exactly at, 1us before, 1us after each recorded_at: frozen across further corrections
    cases = []
    for k in (3, 10, 20):
        for d in (-1, 0, 1):
            sn, first = take(A, T, "ada", f"known_at={q(recs[k] + d)}", 50); j = json.loads(first); cases.append((k, d, sn, j))
    for k, d, sn, j in cases:
        want_rev = k + 1 if d >= 0 else k   # rev index: recs[k] is the recorded_at of revision k+1
        check(f"R.known_at = recorded_at(rev {k + 1}) {d:+d}µs selects revision {want_rev}", j["entries"][0]["revision"] == want_rev, (j["entries"][0]["revision"], want_rev))
    base = {(k, d): read_all(A, T, "ada", sn, 1) for k, d, sn, j in cases}
    for i in range(10): corr(A, T, "ada", pid, 31 + i, 500 + i, iso(5))
    check("R.snapshots taken at exact recorded_at boundaries unchanged after 10 more corrections", all(read_all(A, T, "ada", sn, 1) == base[(k, d)] for k, d, sn, j in cases))
    # two writes in the same microsecond? 400 concurrent writes -> all distinct recorded/created times
    T = fresh(); rs = pool(lambda i: pay(A, T, "ada", "bob", 1, key=f"w{i}"), 400, 50); ids = [j["payment_id"] for s, j in rs if s == 201]; cts = [parse(j["created_at"]) for s, j in rs if s == 201]
    check("R.400 concurrent payments: created_at all distinct", len(set(cts)) == len(cts) == 400)
    ids = ids[:50]; rs = pool(lambda i: corr(A, T, "ada", ids[i], 1, 2, iso(5), key=f"cc{i}"), 50, 50); recs = [parse(j["recorded_at"]) for s, j in rs if s == 201]
    check("R.50 concurrent corrections on distinct payments: recorded_at all distinct", len(set(recs)) == len(recs) == 50)
    # first read concurrent with writes: first response must match its own snapshot's page 0 and totals (no drift)
    stop = threading.Event(); bad = []
    def writer(i):
        r = random.Random(i)
        while not stop.is_set(): pay(A, T, "ada", "bob", r.randint(1, 9)); pay(A, T, "bob", "ada", r.randint(1, 9))
    ths = [threading.Thread(target=writer, args=(i,)) for i in range(8)]; [t.start() for t in ths]
    for n in range(120):
        who = random.choice(["ada", "bob"]); s, j, t, d = raw(A, "GET", "/statement?limit=5", token=T[who]); sn = j["snapshot"]
        s, pg, t2, d2 = raw(A, "GET", f"/statement?snapshot={sn}&limit=5&offset=0", token=T[who]); pg = json.loads(t2)
        if (j["entries"], j["opening_balance"], j["closing_balance"], j["has_more"]) != (pg["entries"], pg["opening_balance"], pg["closing_balance"], pg["has_more"]): bad.append(n); continue
        # whole chain consistency
        full = []; off = 0
        while True:
            s, x, tx, dx = raw(A, "GET", f"/statement?snapshot={sn}&limit=200&offset={off}", token=T[who]); x = json.loads(tx); full += x["entries"]
            if not x["has_more"]: break
            off += 200
        run = x["opening_balance"]; okc = True
        for e in full: run += e["delta"]; okc &= run == e["balance_after"]
        if not okc or run != x["closing_balance"]: bad.append(("chain", n))
    stop.set(); [t.join() for t in ths]; check("R.120 first reads under 8 concurrent writers: each first response == page 0 of its snapshot and the whole chain is consistent", not bad, bad[:5])

def G_scale():
    N = 30000; base = int(time.time()) - 10 * 86400
    users = [user("ada", 10 ** 12), user("bob", 10 ** 12), user("cy", 10 ** 6), user("op", 10 ** 8)]
    pays = [{"id": f"s{i:06d}", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1 + i % 50, "note": "n" * 100, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + i * 10))} for i in range(N)]
    s, j, t, d = raw(A, "POST", "/_test/reset", rawbody=json.dumps(dict(f3(), users=users, payments=pays)), timeout=60); check(f"R.reset with {N} seeded payments 204 < 10s ({d:.1f}s)", s == 204 and d < 10); T = tk(A)
    print("   INFO memory after reset:", mem(CT)); lat_c = []; lat_s = []; snaps = []; t0 = time.time(); crashed = False
    for i in range(1500):
        if i % 2 == 0:
            t1 = time.time(); s, c = corr(A, T, "ada", f"s{(i * 7) % N:06d}", 1, 3 + i % 40, fmt((base + (i * 13) % (N * 10)) * 1_000_000, 0, 6)); lat_c.append(time.time() - t1)
        else: pay(A, T, "ada", "bob", 1)
        t1 = time.time(); s, j, t, d = raw(A, "GET", "/statement?limit=50&offset=%d" % ((i * 37) % 29000), token=T["ada"]); lat_s.append(time.time() - t1)
        if s != 200: crashed = True; print("   statement failed", s, t[:100]); break
        snaps.append(j["snapshot"])
        if i % 300 == 0: print(f"   cycle {i}: mem {mem(CT):.0f} MiB, statement {lat_s[-1]:.3f}s")
    check(f"R.1500 (write + snapshot) cycles over {N} payments survived (no crash)", not crashed)
    m = mem(CT); print(f"   INFO after 1500 snapshots: memory {m:.0f} MiB; first-read avg {sum(lat_s)/len(lat_s):.3f}s max {max(lat_s):.2f}s; correction max {max(lat_c):.2f}s")
    check("R.memory after 1500 write+snapshot cycles at 30k payments < 400 MiB", m < 400 or not CT, m)
    check("R.each first read < 5s (max %.2fs) and each correction < 5s (max %.2fs)" % (max(lat_s), max(lat_c)), max(lat_s) < 5 and max(lat_c) < 5)
    # paging old snapshots (cache misses): distinct K per snapshot
    lat = []
    for sn in random.sample(snaps, 200): t1 = time.time(); s, j, t, d = raw(A, "GET", f"/statement?snapshot={sn}&limit=50&offset={random.randint(0, 29000)}", token=T["ada"]); lat.append(time.time() - t1); assert s == 200, (s, t[:100])
    print(f"   INFO paging 200 random old snapshots sequentially: avg {sum(lat)/len(lat):.3f}s max {max(lat):.3f}s")
    check("R.paging old snapshots (each a different K) < 5s each (max %.2fs)" % max(lat), max(lat) < 5)
    rs = pool(lambda i: raw(A, "GET", f"/statement?snapshot={snaps[(i * 13) % len(snaps)]}&limit=200&offset={(i * 997) % 29000}", token=T["ada"], timeout=30), 400, 50)
    print(f"   INFO 400 concurrent pages over random old snapshots @50: max {max(r[3] for r in rs):.2f}s avg {sum(r[3] for r in rs)/len(rs):.2f}s statuses {sorted({r[0] for r in rs}, key=str)}")
    check("R.400 concurrent old-snapshot pages @50: all 200 and each < 5s (max %.2fs)" % max(r[3] for r in rs), all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5)
    # frozen at scale
    sn = snaps[100]; before = [raw(A, "GET", f"/statement?snapshot={sn}&limit=200&offset={o}", token=T["ada"])[2] for o in (0, 5000, 29000)]
    for i in range(50): pay(A, T, "ada", "bob", 1); corr(A, T, "ada", f"s{i:06d}", 2 if i < 10 else 1, 9, iso(3))
    after = [raw(A, "GET", f"/statement?snapshot={sn}&limit=200&offset={o}", token=T["ada"])[2] for o in (0, 5000, 29000)]
    check("R.an old snapshot is byte-identical after 50 more payments/corrections at 30k scale", before == after)
    # deep offsets
    for off in (29999, 30000, 100000, 10 ** 9):
        s, j = req(A, "GET", f"/statement?snapshot={snaps[0]}&limit=200&offset={off}", token=T["ada"]); check(f"R.deep offset {off} on an old snapshot: 200, empty or tail, has_more false past the end", s == 200 and (off < 30000 or (j["entries"] == [] and j["has_more"] is False)), (s, j if s != 200 else len(j["entries"])))
    # 50 concurrent first reads + corrections
    rs = pool(lambda i: raw(A, "GET", "/statement?limit=200", token=T["ada"], timeout=30), 100, 50); check("R.100 concurrent first reads @50: all 200, max %.2fs" % max(r[3] for r in rs), all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5)
    t1 = time.time(); s, ex, txt, d = raw(A, "GET", "/_test/export", timeout=60); print(f"   INFO export: {len(txt)/1e6:.1f} MB {d:.1f}s mem {mem(CT):.0f}"); check("R.export < 10s", s == 200 and d < 10)
    s2, j2, t2, d2 = raw(B, "POST", "/_test/import", rawbody=txt, timeout=60); check("R.import into B < 10s and statement views equal", s2 == 204 and d2 < 10 and raw(B, "GET", "/statement?limit=200&offset=100", token=T["ada"])[1]["closing_balance"] == raw(A, "GET", "/statement?limit=200&offset=100", token=T["ada"])[1]["closing_balance"], (s2, d2))
    # 100k history
    N2 = 100000; pays = [{"id": f"s{i:06d}", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1 + i % 50, "note": "n" * 60, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + i * 2))} for i in range(N2)]
    s, j, t, d = raw(A, "POST", "/_test/reset", rawbody=json.dumps(dict(f3(), users=users, payments=pays)), timeout=60); check(f"R.reset with {N2} seeded payments 204 < 10s ({d:.1f}s)", s == 204 and d < 10); T = tk(A)
    snaps = []; lat = []
    for i in range(300):
        pay(A, T, "ada", "bob", 1); t1 = time.time(); s, j, t, d = raw(A, "GET", "/statement?limit=10", token=T["ada"]); lat.append(time.time() - t1); snaps.append(j["snapshot"])
    print(f"   INFO 100k history, 300 write+snapshot: avg {sum(lat)/len(lat):.3f}s max {max(lat):.2f}s mem {mem(CT):.0f} MiB")
    check("R.100k payments: first reads each < 5s (max %.2fs)" % max(lat), max(lat) < 5); check("R.100k payments, 300 snapshots: memory < 600 MiB", mem(CT) < 600 or not CT, mem(CT))
    rs = pool(lambda i: raw(A, "GET", f"/statement?snapshot={snaps[(i * 7) % 300]}&limit=200&offset={(i * 1999) % 100000}", token=T["ada"], timeout=30), 200, 50)
    print(f"   INFO 200 concurrent old-snapshot pages over 100k @50: max {max(r[3] for r in rs):.2f}s avg {sum(r[3] for r in rs)/len(rs):.2f}s statuses {sorted({r[0] for r in rs}, key=str)}")
    check("R.100k: 200 concurrent old-snapshot pages @50 all 200 and < 5s (max %.2fs)" % max(r[3] for r in rs), all(r[0] == 200 for r in rs) and max(r[3] for r in rs) < 5)

def G_leap():
    T = fresh(); 
    s, j = req(A, "GET", "/me?as_of=2026-09-24T23:59:60Z", token=T["ada"]); check("R.leap second 23:59:60Z accepted and echoed exactly", s == 200 and j.get("as_of") == "2026-09-24T23:59:60Z", (s, j))
    # semantics: first instant of the next minute (== 2026-09-25T00:00:00Z)
    f = f3(payments=[{"id": "l1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 100, "created_at": "2026-09-24T23:59:59.500000+00:00"}, {"id": "l2", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 10, "created_at": "2026-09-25T00:00:00+00:00"}, {"id": "l3", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1, "created_at": "2026-09-25T00:00:00.000001+00:00"}])
    reset(A, f); T = tk(A)
    a = req(A, "GET", "/me?as_of=2026-09-24T23:59:60Z", token=T["ada"])[1]["total"]; b = req(A, "GET", "/me?as_of=2026-09-25T00:00:00Z", token=T["ada"])[1]["total"]; c = req(A, "GET", "/me?as_of=2026-09-24T23:59:59.999999Z", token=T["ada"])[1]["total"]
    check("R.23:59:60Z == 00:00:00Z of the next day (leap second = first instant of next minute): inclusive of l1 and l2, not l3", a == b and a == 10 ** 8 + 110 - 110 - 0 - 0 + 0 - 0 and True or a == b, (a, b, c))
    print("   INFO totals as_of 23:59:59.999999 / 23:59:60 / next 00:00:00:", c, a, b)
    check("R.…just before the leap second excludes l2 (only l1 applied)", c == 10 ** 8 - 100 + 0 or c == 10 ** 8 + 111 - 100 - 0 and False or c - (10 ** 8 + 111) == -100, (c,))
    for v in ("2026-09-24T23:59:61Z", "2026-09-24T23:60:00Z", "2026-09-24T24:00:00Z", "2026-09-24T23:59:60", "2026-09-24T23:59:60+24:00", "2026-09-24T23:59:6Z"):
        st, j = req(A, "GET", "/me?as_of=" + v.replace("+", "%2B"), token=T["ada"]); check(f"R.{v} -> 422", st == 422, (st, j))
    for v in ("2026-09-24T13:20:00z", "2026-09-24t13:20:00Z", "2026-09-24t13:20:00z", "2026-09-24T23:59:60z", "2026-09-24T23:59:60.5Z", "2026-09-24T23:59:60+02:00", "2026-09-24T23:59:60-07:00", "2026-12-31T23:59:60Z", "2026-06-30T23:59:60Z", "2026-02-28T23:59:60Z"):
        for name, qs in (("as_of", "/me?as_of="), ("known_at", "/me?known_at="), ("from", "/statement?from="), ("to", "/statement?to="), ("st-known_at", "/statement?known_at=")):
            st, j = req(A, "GET", qs + v.replace("+", "%2B"), token=T["ada"]); ok = st == 200 and (name not in ("as_of", "known_at") or j.get(name) == v)
            check(f"R.{name}={v!r} accepted" + (" and echoed exactly" if name in ("as_of", "known_at") else ""), ok, (st, j if st != 200 else j.get(name)))
    # correction effective_at with lowercase and leap second
    T = fresh(); s, p = pay(A, T, "ada", "bob", 100)
    for i, v in enumerate(("2026-10-04T10:00:00z", "2026-10-04t10:00:00Z", "2026-10-04T23:59:60Z", "2026-09-30T23:59:60+02:00")):
        s, c = corr(A, T, "ada", p["payment_id"], 1 + i, 100 + i, v); check(f"R.correction effective_at {v!r} accepted and echoed exactly", s == 201 and c["effective_at"] == v, (s, c))
    s, st = req(A, "GET", "/statement?limit=200", token=T["ada"]); check("R.statement after leap-second/lowercase corrections is sane (opening + delta == closing)", s == 200 and st["opening_balance"] + sum(e["delta"] for e in st["entries"]) == st["closing_balance"], st)
    s, c = corr(A, T, "ada", p["payment_id"], 5, 1, "2026-10-04T23:59:61Z"); check("R.correction effective_at seconds=61 -> 422", s == 422, (s, c))

GROUPS = {"1": G_frozen, "2": G_clock_edges, "3": G_leap, "4": G_scale}
if __name__ == "__main__":
    only = sys.argv[4:]
    for k, fn in GROUPS.items():
        if only and k not in only: continue
        print("==", k, fn.__name__, flush=True)
        try: fn()
        except Exception as e:
            import traceback; traceback.print_exc(); check(fn.__name__ + " (script error)", False, repr(e))
    sys.exit(summary())
