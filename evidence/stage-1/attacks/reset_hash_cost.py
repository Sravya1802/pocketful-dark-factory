#!/usr/bin/env python3
"""POST /_test/reset with N seeded users, each with a DISTINCT password; reports reset latency (spec: reset timeout 10 s).
usage: reset_hash_cost.py http://HOST:PORT [N ...]   (RESETS state)"""
import sys, json, time, http.client, urllib.parse
U = urllib.parse.urlparse(sys.argv[1]); sizes = [int(x) for x in sys.argv[2:]] or [100, 300, 600, 1000, 1500]
fail = False
for n in sizes:
    users = [{"id": f"u{i}", "email": f"u{i}@x.com", "password": f"pw-{i:06d}-xyz", "display_name": "U", "handle": f"h{i}", "balance": 10} for i in range(n)]
    c = http.client.HTTPConnection(U.hostname, U.port, timeout=120); t = time.time()
    c.request("POST", "/_test/reset", json.dumps({"currency": "EUR", "minor_units": 2, "users": users}), {"Content-Type": "application/json"})
    r = c.getresponse(); r.read(); dt = time.time() - t
    print(f"{n:5d} distinct-password users: HTTP {r.status} in {dt:5.2f}s", "FAIL (>10s)" if dt > 10 else "ok"); fail |= dt > 10
sys.exit(1 if fail else 0)
