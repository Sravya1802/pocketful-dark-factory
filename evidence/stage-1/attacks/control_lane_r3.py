#!/usr/bin/env python3
"""Round 3: control-lane starvation / deadlock probes. usage: control_lane_r3.py URL [stall_seconds=20]  (RESETS state)
Scenario 1: ONE client uploads >16MiB of a declared control body to /_test/import (or reset) then stalls. Meanwhile: small reset,
small export, API payment, health. Measures how long each waits and whether the lane recovers when the stalled client disconnects
or after the server's own timeout."""
import sys, socket, time, json, threading, urllib.parse, http.client
u = urllib.parse.urlparse(sys.argv[1]); STALL = float(sys.argv[2]) if len(sys.argv) > 2 else 20
def call(m, p, body=None, h=None, timeout=15):
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout); t = time.time()
    try:
        c.request(m, p, body=body, headers=dict({"Content-Type": "application/json"}, **(h or {}))); r = c.getresponse(); d = r.read(); return r.status, round(time.time() - t, 2), d
    except Exception as e: return type(e).__name__, round(time.time() - t, 2), b""
fx = json.dumps({"currency": "EUR", "minor_units": 2, "users": [{"id": "a", "email": "a@x.com", "password": "correct horse", "display_name": "A", "handle": "a", "balance": 10}, {"id": "b", "email": "b@x.com", "password": "correct horse", "display_name": "B", "handle": "b", "balance": 0}]})
assert call("POST", "/_test/reset", fx)[0] == 204
fails = []
def chk(n, ok, d=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: " + str(d))); ok or fails.append(n)
for path in ("/_test/import", "/_test/reset"):
    assert call("POST", "/_test/reset", fx)[0] == 204
    tok = json.loads(call("POST", "/auth/login", json.dumps({"email": "a@x.com", "password": "correct horse"}))[2])["token"]
    sk = socket.create_connection((u.hostname, u.port), timeout=30)
    sk.sendall(b"POST " + path.encode() + b" HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100000000\r\n\r\n{" + b" " * (20 * 1024 * 1024))
    time.sleep(1)
    res = {}
    def small(name, *a, **k): res[name] = call(*a, **k)
    ths = [threading.Thread(target=small, args=("reset",), kwargs=dict(m="POST", p="/_test/reset", body=fx, timeout=STALL + 15)),
           threading.Thread(target=small, args=("export",), kwargs=dict(m="GET", p="/_test/export", timeout=STALL + 15)),
           threading.Thread(target=small, args=("pay",), kwargs=dict(m="POST", p="/payments", body=json.dumps({"to_handle": "b", "amount": 1}), h={"Authorization": "Bearer " + tok, "Idempotency-Key": "cl" + path}, timeout=STALL + 15)),
           threading.Thread(target=small, args=("health",), kwargs=dict(m="GET", p="/health", timeout=STALL + 15))]
    ths[2].start(); ths[3].start(); ths[2].join(); ths[3].join()   # pay/health first: the small reset below invalidates tokens
    ths[0].start(); ths[1].start()
    time.sleep(STALL)
    print(f"-- after {STALL}s with one stalled {path} upload (>16MiB sent, never finished), still waiting:", sorted(k for k in ("reset", "export", "pay", "health") if k not in res))
    print("   finished so far:", {k: v[:2] for k, v in res.items()})
    chk(f"CL.{path}: /health and API payment unaffected by stalled control upload", res.get("health", (0,))[0] == 200 and res.get("pay", (0,))[0] in (201, 409))
    chk(f"CL.{path}: small reset/export are served within 10s despite a stalled upload (spec: control calls have a 10 s timeout)", all(k in res and res[k][1] < 10 and res[k][0] in (200, 204) for k in ("reset", "export")), {k: res.get(k, "waiting")[:2] if k in res else "still waiting" for k in ("reset", "export")})
    sk.close()
    [t.join(timeout=60) for t in ths]
    print("   after the stalled client disconnected:", {k: v[:2] for k, v in res.items()})
    chk(f"CL.{path}: lane recovers after stalled client disconnects (all calls complete OK)", all(k in res and res[k][0] in (200, 201, 204, 409) for k in ("reset", "export", "pay", "health")))
    time.sleep(1); chk(f"CL.{path}: fresh reset works afterwards", call("POST", "/_test/reset", fx)[0] == 204)
print("FAILED" if fails else "ALL PASS"); sys.exit(1 if fails else 0)
