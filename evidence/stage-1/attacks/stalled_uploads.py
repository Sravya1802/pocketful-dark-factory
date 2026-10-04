#!/usr/bin/env python3
"""N connections each upload ~MB of a declared 32MiB /_test/reset body then stall. Then probe normal traffic and check the
service survived. usage: stalled_uploads.py URL [N=50] [MB=31]   (RESETS state)  Spec: 2 GiB limit, up to 50 in flight."""
import sys, socket, time, json, urllib.parse, http.client
u = urllib.parse.urlparse(sys.argv[1]); N = int(sys.argv[2]) if len(sys.argv) > 2 else 50; MB = int(sys.argv[3]) if len(sys.argv) > 3 else 31
def call(m, p, body=None, h=None):
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    try: c.request(m, p, body=body, headers=dict({"Content-Type": "application/json"}, **(h or {}))); r = c.getresponse(); return r.status, r.read()
    except Exception as e: return repr(e), b""
fx = {"currency": "EUR", "minor_units": 2, "users": [{"id": "a", "email": "a@x.com", "password": "correct horse", "display_name": "A", "handle": "a", "balance": 10}, {"id": "b", "email": "b@x.com", "password": "correct horse", "display_name": "B", "handle": "b", "balance": 0}]}
assert call("POST", "/_test/reset", json.dumps(fx))[0] == 204
tok = json.loads(call("POST", "/auth/login", json.dumps({"email": "a@x.com", "password": "correct horse"}))[1])["token"]
held = []; chunk = b"x" * (MB * 1024 * 1024); st = []
for i in range(N):
    try:
        sk = socket.create_connection((u.hostname, u.port), timeout=30); sk.sendall(b"POST /_test/reset HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 33554432\r\n\r\n{" + chunk); held.append(sk)
    except Exception as e: st.append(repr(e))
time.sleep(2)
r = [call("GET", "/health"), call("POST", "/payments", json.dumps({"to_handle": "b", "amount": 1}), {"Authorization": "Bearer " + tok, "Idempotency-Key": "s1"}), call("POST", "/auth/login", json.dumps({"email": "a@x.com", "password": "correct horse"}))]
print(f"{len(held)} stalled uploads of ~{MB}MB; health/payment/login while stalled:", [x[0] for x in r], st[:2])
for sk in held: sk.close()
time.sleep(2); after = call("GET", "/health")[0]; print("health after release:", after)
ok = r[0][0] == 200 and r[1][0] in (201, 413, 503) and after == 200
print("PASS (alive; any refusal is a clean 4xx/503)" if ok else "FAIL"); sys.exit(0 if ok else 1)
