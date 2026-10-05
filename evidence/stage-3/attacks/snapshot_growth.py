#!/usr/bin/env python3
"""Stage-3: memory growth per statement snapshot under different interleaved writes. usage: snapshot_growth.py URL N CYCLES container  (RESETS)
modes: none = read only; payment = one payment between reads; failed403 = a rejected correction between reads; correction = a successful correction;
Spec: "Statement/snapshot memory must stay bounded (snapshots live until reset)"; 2 GiB limit."""
import sys, time, subprocess, re, json
from s3lib import *
URL, N, CYC, CT = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
def mem():
    o = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", CT], capture_output=True, text=True).stdout.strip(); m = re.match(r"([\d.]+)(MiB|GiB)", o)
    return float(m.group(1)) * (1024 if m.group(2) == "GiB" else 1) if m else -1
users = [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10 ** 12}, {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 10 ** 12}]
base = int(time.time()) - 10 * 86400
pays = [{"id": f"s{i:06d}", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1 + i % 50, "note": "n" * 100, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + i * 10))} for i in range(N)]
body = json.dumps({"currency": "EUR", "minor_units": 2, "users": users, "payments": pays})
res = {}
for mode in ("none", "payment", "failed403", "correction"):
    subprocess.run(["docker", "restart", CT], capture_output=True); time.sleep(2)
    s, j, t, d = raw(URL, "POST", "/_test/reset", rawbody=body, timeout=60); assert s == 204; ta = login(URL, "ada@example.com"); tb = login(URL, "bob@example.com"); time.sleep(1); m0 = mem()
    for i in range(CYC):
        if mode == "payment": req(URL, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem(f"p{i}"), token=ta)
        elif mode == "failed403": req(URL, "POST", "/payments/s000001/corrections", {"expected_revision": 1, "amount": 1, "effective_at": "2026-09-30T00:00:00+00:00", "reason": "x"}, headers=idem(f"f{i}"), token=tb)
        elif mode == "correction": req(URL, "POST", f"/payments/s{i % N:06d}/corrections", {"expected_revision": 1, "amount": 3, "effective_at": "2026-09-30T00:00:00+00:00", "reason": "c"}, headers=idem(f"c{i}"), token=ta)
        s, st = req(URL, "GET", "/statement?limit=10", token=ta)
        if s != 200: print(mode, "statement failed at cycle", i, s); break
    time.sleep(1); m1 = mem(); res[mode] = (m0, m1, (m1 - m0) / max(CYC, 1)); print(f"{mode:10s}: memory {m0:7.0f} -> {m1:7.0f} MiB over {CYC} snapshots of a {N}-payment history = {(m1 - m0) / CYC:6.2f} MiB per snapshot")
bad = [m for m, v in res.items() if v[2] > 0.5]
print("FAIL: unbounded growth per snapshot in modes " + ", ".join(bad) if bad else "PASS"); sys.exit(1 if bad else 0)
