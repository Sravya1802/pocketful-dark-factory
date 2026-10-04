#!/usr/bin/env python3
"""Resource attack: N concurrent large bodies (default 50 x 20MB number-heavy JSON) while probing /health and a normal payment.
usage: bigbody_storm.py http://HOST:PORT [N] [MB]   (RESETS state). Spec: <=50 in flight, 5 s per-request timeout, 2 GiB RAM."""
import sys, json, time, threading, http.client, urllib.parse
from concurrent.futures import ThreadPoolExecutor
U = urllib.parse.urlparse(sys.argv[1]); N = int(sys.argv[2]) if len(sys.argv) > 2 else 50; MB = int(sys.argv[3]) if len(sys.argv) > 3 else 20
def call(m, p, body=None, h=None, timeout=60):
    c = http.client.HTTPConnection(U.hostname, U.port, timeout=timeout); hh = {"Content-Type": "application/json"}; hh.update(h or {})
    t = time.time()
    try:
        c.request(m, p, body=body, headers=hh); r = c.getresponse(); d = r.read(); return r.status, d, time.time() - t
    except Exception as e: return repr(e), b"", time.time() - t
fx = {"currency": "EUR", "minor_units": 2, "users": [{"id": "u_a", "email": "a@x.com", "password": "correct horse", "display_name": "A", "handle": "a", "balance": 10000}, {"id": "u_b", "email": "b@x.com", "password": "correct horse", "display_name": "B", "handle": "b", "balance": 0}]}
assert call("POST", "/_test/reset", json.dumps(fx))[0] == 204
tok = json.loads(call("POST", "/auth/login", json.dumps({"email": "a@x.com", "password": "correct horse"}))[1])["token"]
count = MB * 1024 * 1024 // 6
body = ('{"to_handle":"b","amount":1,"pad":[' + ",".join(["1.5e3"] * count) + "]}").encode()
print(f"body {len(body)/1e6:.1f} MB x {N} concurrent")
lat = []; stop = threading.Event()
def probe():
    while not stop.is_set(): lat.append(call("GET", "/health", timeout=60)[2]); time.sleep(0.05)
th = threading.Thread(target=probe); th.start()
with ThreadPoolExecutor(N) as ex:
    rs = list(ex.map(lambda i: call("POST", "/payments", body, {"Authorization": "Bearer " + tok, "Idempotency-Key": f"big{i}"}), range(N)))
stop.set(); th.join()
print("statuses:", sorted({str(r[0]) for r in rs}), "max request latency %.1fs" % max(r[2] for r in rs), "max /health latency %.1fs" % max(lat))
alive = call("GET", "/health")[0]; print("alive after:", alive)
bad = [r for r in rs if r[0] != 201 and r[0] not in (413, 422, 400)]
fail = max(r[2] for r in rs) > 5 or max(lat) > 5 or alive != 200 or bad
print("FAIL (exceeds 5s per-request budget / 5xx / crash)" if fail else "PASS"); sys.exit(1 if fail else 0)
