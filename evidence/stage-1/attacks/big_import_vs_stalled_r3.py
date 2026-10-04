#!/usr/bin/env python3
"""Round 3: a legitimate large (>16MiB) import/reset while ANOTHER client holds the single large-control slot with a stalled upload.
usage: big_import_vs_stalled_r3.py URL [wait_seconds=40]   (RESETS state). Spec §10: control calls have a 10 s timeout."""
import sys, socket, time, json, threading, urllib.parse, http.client
u = urllib.parse.urlparse(sys.argv[1]); WAIT = float(sys.argv[2]) if len(sys.argv) > 2 else 40
def call(m, p, body=None, timeout=120):
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout); t = time.time()
    try: c.request(m, p, body=body, headers={"Content-Type": "application/json"}); r = c.getresponse(); d = r.read(); return r.status, round(time.time() - t, 2), d
    except Exception as e: return type(e).__name__, round(time.time() - t, 2), b""
users = [{"id": "a", "email": "a@x.com", "password": "correct horse", "display_name": "A", "handle": "a", "balance": 10}, {"id": "b", "email": "b@x.com", "password": "correct horse", "display_name": "B", "handle": "b", "balance": 0}]
pays = [{"id": f"p{i}", "from_user_id": "a", "to_user_id": "b", "amount": 1, "note": "n" * 150, "visibility": "public"} for i in range(110000)]
body = json.dumps({"currency": "EUR", "minor_units": 2, "users": users, "payments": pays}); print("legit big fixture MB:", round(len(body) / 1e6, 1))
s, t, _ = call("POST", "/_test/reset", body); print("reset alone:", s, t, "s")
s, t, ex = call("GET", "/_test/export"); print("export alone:", s, t, "s", round(len(ex) / 1e6, 1), "MB")
s, t, _ = call("POST", "/_test/import", ex); print("import alone:", s, t, "s")
# stalled client takes the large slot
sk = socket.create_connection((u.hostname, u.port), timeout=30)
sk.sendall(b"POST /_test/import HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 200000000\r\n\r\n{" + b" " * (20 * 1024 * 1024)); time.sleep(1)
res = {}
th = threading.Thread(target=lambda: res.update(r=call("POST", "/_test/import", ex, timeout=WAIT + 30))); th.start(); th.join(WAIT)
waiting = th.is_alive()
print(f"legit big import while another client is stalled: {'STILL WAITING after %ss' % WAIT if waiting else res['r'][:2]}")
fails = []
if waiting or res["r"][1] > 10: fails.append("legit big import delayed > 10s by a stalled big upload")
sk.close(); th.join(60); print("after stalled client disconnects:", res.get("r", ("still waiting",))[:2])
print("FAIL: " + "; ".join(fails) if fails else "PASS"); sys.exit(1 if fails else 0)
