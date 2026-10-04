#!/usr/bin/env python3
"""Round 3: very large state through reset -> export -> import into a different process, with timings and container memory.
usage: big_state_r3.py URL_A URL_B N_PAYMENTS [container_A [container_B]]   (RESETS both)  Spec §10: control calls 10 s; §2: 2 GiB."""
import sys, json, time, subprocess, threading, http.client, urllib.parse, re
A, B, N = sys.argv[1], sys.argv[2], int(sys.argv[3]); CA = sys.argv[4] if len(sys.argv) > 4 else None
def call(base, m, p, body=None, timeout=120):
    u = urllib.parse.urlparse(base); c = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout); t = time.time()
    try:
        c.request(m, p, body=body, headers={"Content-Type": "application/json"}); r = c.getresponse(); d = r.read(); return r.status, round(time.time() - t, 2), d
    except Exception as e: return type(e).__name__, round(time.time() - t, 2), b""
peak = [0.0]; stop = threading.Event()
def watch():
    while not stop.is_set() and CA:
        o = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", CA], capture_output=True, text=True).stdout
        m = re.match(r"([\d.]+)(MiB|GiB)", o)
        if m: peak[0] = max(peak[0], float(m.group(1)) * (1024 if m.group(2) == "GiB" else 1))
if CA: threading.Thread(target=watch, daemon=True).start()
users = [{"id": f"u{i}", "email": f"u{i}@x.com", "password": "correct horse", "display_name": "U", "handle": f"h{i}", "balance": 10 ** 9} for i in range(20)]
pays = [{"id": f"p{i}", "from_user_id": f"u{i % 20}", "to_user_id": f"u{(i + 1) % 20}", "amount": 1, "note": "n" * 120, "visibility": "public" if i % 2 else "private"} for i in range(N)]
body = json.dumps({"currency": "EUR", "minor_units": 2, "users": users, "payments": pays}); print(f"fixture {len(body)/1e6:.0f} MB, {N} payments")
fails = []
def chk(n, ok, d=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: " + str(d))); ok or fails.append(n)
s, t, _ = call(A, "POST", "/_test/reset", body); print("reset:", s, t, "s peak mem", round(peak[0])); chk("reset big fixture 204 < 10s", s == 204 and t < 10, (s, t))
s, t, ex = call(A, "GET", "/_test/export"); print(f"export: {s} {t}s {len(ex)/1e6:.0f} MB peak mem {round(peak[0])}"); chk("export 200 < 10s", s == 200 and t < 10, (s, t))
s, t, _ = call(B, "POST", "/_test/import", ex); print("import into B:", s, t, "s"); chk("import of unchanged export 204 < 10s", s == 204 and t < 10, (s, t))
s, t, ex2 = call(B, "GET", "/_test/export"); chk("re-export byte-identical", ex2 == ex, (len(ex2), len(ex)))
s, t, _ = call(A, "POST", "/_test/import", ex); chk("re-import into source 204 < 10s", s == 204 and t < 10, (s, t))
tk = json.loads(call(B, "POST", "/auth/login", json.dumps({"email": "u3@x.com", "password": "correct horse"}))[2])["token"]
c = http.client.HTTPConnection(urllib.parse.urlparse(B).hostname, urllib.parse.urlparse(B).port); t0 = time.time(); c.request("GET", "/activity?limit=200&offset=%d" % (N // 2), headers={"Authorization": "Bearer " + tk}); r = c.getresponse(); r.read()
chk("feed read on big imported state < 1s", r.status == 200 and time.time() - t0 < 1, (r.status, time.time() - t0))
print("peak container A mem MiB:", round(peak[0])); stop.set(); chk("peak mem < 1.8GiB", peak[0] < 1843 or not CA, peak[0])
print("FAILED" if fails else "ALL PASS"); sys.exit(1 if fails else 0)
