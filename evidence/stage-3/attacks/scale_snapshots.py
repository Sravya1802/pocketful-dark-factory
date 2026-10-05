#!/usr/bin/env python3
"""Stage-3 scale: N seeded payments, then (correction + snapshot read) cycles; reports latency and container memory.
usage: scale_snapshots.py URL N_PAYMENTS CYCLES [docker_container]   (RESETS)  Spec: 5 s per request, 2 GiB, snapshots live until reset."""
import sys, time, subprocess, re, json
from s3lib import *
URL, N, CYC = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]); CT = sys.argv[4] if len(sys.argv) > 4 else None
def mem():
    if not CT: return "n/a"
    return subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", CT], capture_output=True, text=True).stdout.strip()
def mib(s):
    m = re.match(r"([\d.]+)(MiB|GiB)", s); return float(m.group(1)) * (1024 if m.group(2) == "GiB" else 1) if m else 0
users = [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10 ** 12}, {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 10 ** 12}]
base = int(time.time()) - 10 * 86400
pays = [{"id": f"s{i:06d}", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1 + i % 50, "note": "n" * 100, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + i * 10))} for i in range(N)]
body = json.dumps({"currency": "EUR", "minor_units": 2, "users": users, "payments": pays}); print(f"fixture {len(body)/1e6:.1f} MB, {N} payments")
t0 = time.time(); s, j, t, d = raw(URL, "POST", "/_test/reset", rawbody=body, timeout=60); print("reset", s, f"{d:.1f}s mem {mem()}"); ta = login(URL, "ada@example.com")
fails = []
def chk(n, ok, d=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: " + str(d))); ok or fails.append(n)
chk("reset within 10s", s == 204 and d < 10, (s, d))
lat_c = []; lat_s = []; toks_ = []
for i in range(CYC):
    pid = f"s{(i * 7) % N:06d}"   # all seeded payments are ada -> bob, so ada may correct them (each payment corrected once)
    t = time.time(); sc, c = req(URL, "POST", f"/payments/{pid}/corrections", {"expected_revision": 1, "amount": 3 + i % 40, "effective_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + (i * 13) % (N * 10))), "reason": "c"}, headers=idem(f"c{i}"), token=ta, timeout=30); lat_c.append(time.time() - t)
    t = time.time(); ss, st = req(URL, "GET", "/statement?limit=50", token=ta, timeout=30); lat_s.append(time.time() - t)
    if ss == 200: toks_.append(st["snapshot"])
    if i % max(1, CYC // 5) == 0: print(f"  cycle {i}: correction {sc} {lat_c[-1]:.3f}s, statement {ss} {lat_s[-1]:.3f}s, snapshots {len(toks_)}, mem {mem()}")
chk(f"{CYC} corrections each < 5s (max {max(lat_c):.2f}s, avg {sum(lat_c)/len(lat_c):.3f}s)", max(lat_c) < 5)
chk(f"{CYC} first statement reads each < 5s (max {max(lat_s):.2f}s, avg {sum(lat_s)/len(lat_s):.3f}s)", max(lat_s) < 5)
m = mem(); print("final memory", m); chk("memory < 1.5 GiB with all snapshots alive", mib(m) < 1536 or not CT, m)
chk("oldest snapshot still pages (frozen before 90% of the corrections)", req(URL, "GET", f"/statement?snapshot={toks_[0]}&limit=5&offset=100", token=ta)[0] == 200)
t = time.time(); s, ex, txt, d = raw(URL, "GET", "/_test/export", timeout=60); print(f"export {len(txt)/1e6:.1f} MB {d:.1f}s mem {mem()}"); chk("export < 10s", s == 200 and d < 10, d)
print("FAILED" if fails else "ALL PASS"); sys.exit(1 if fails else 0)
