#!/usr/bin/env python3
"""Stage-3 r2: latency of paging OLD snapshots (each a different known_at => view recomputed) over a large history at 50 concurrency.
usage: scale_pages_r2.py URL N_PAYMENTS [SNAPSHOTS=100] [CONCURRENCY=50] [container]   (RESETS)  Spec: 5 s per request, 50 in flight."""
import sys, time, json, re, subprocess, random
from s3lib import *
URL, N = sys.argv[1], int(sys.argv[2]); S = int(sys.argv[3]) if len(sys.argv) > 3 else 100; C = int(sys.argv[4]) if len(sys.argv) > 4 else 50; CT = sys.argv[5] if len(sys.argv) > 5 else None
users = [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10 ** 12}, {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 10 ** 12}]
base = int(time.time()) - 20 * 86400
pays = [{"id": f"s{i:07d}", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1 + i % 50, "note": "n" * 60, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base + i * 2))} for i in range(N)]
body = json.dumps({"currency": "EUR", "minor_units": 2, "users": users, "payments": pays}); print(f"{N} payments, fixture {len(body)/1e6:.0f} MB")
s, j, t, d = raw(URL, "POST", "/_test/reset", rawbody=body, timeout=120); print("reset", s, f"{d:.1f}s"); ta = login(URL, "ada@example.com")
snaps = []
for i in range(S):
    req(URL, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem(f"p{i}"), token=ta); t0 = time.time(); s, j = req(URL, "GET", "/statement?limit=10", token=ta, timeout=30); snaps.append(j["snapshot"])
# single page (cache miss) latency
lat1 = []
for sn in random.sample(snaps, min(30, S)): t0 = time.time(); s, j, t, d = raw(URL, "GET", f"/statement?snapshot={sn}&limit=200&offset=1000", token=ta, timeout=30); lat1.append(time.time() - t0)
print(f"sequential old-snapshot page: avg {sum(lat1)/len(lat1):.3f}s max {max(lat1):.3f}s")
rs = pool(lambda i: raw(URL, "GET", f"/statement?snapshot={snaps[(i * 7) % S]}&limit=200&offset={(i * 1999) % N}", token=ta, timeout=60), 300, C)
mx = max(r[3] for r in rs); print(f"{300} pages of old snapshots @{C} concurrent: max {mx:.2f}s avg {sum(r[3] for r in rs)/len(rs):.2f}s statuses {sorted({r[0] for r in rs}, key=str)}")
ok = all(r[0] == 200 for r in rs) and mx < 5
print("PASS" if ok else "FAIL: a snapshot page exceeded the 5 s per-request limit (or errored) under 50 concurrent pages"); sys.exit(0 if ok else 1)
