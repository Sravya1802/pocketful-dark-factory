#!/usr/bin/env python3
"""Stage-4: import a state whose snapshot record has known_at_us in the far future. Spec: invalid state -> 422 without change (§10 stage 1).
usage: tampered_snapshot_clock.py URL   (RESETS)  Observes whether the service accepts it and what created_at the next payment gets."""
import sys, json, time
from s4lib import *
A = sys.argv[1]; reset(A, fx()); T = toks(A); req(A, "POST", "/payments", {"to_handle": "bob", "amount": 5}, headers=idem("p"), token=T["ada"])
s, st = req(A, "GET", "/statement?limit=1", token=T["ada"]); s, ex = req(A, "GET", "/_test/export")
ex["state"]["snapshots"][0]["known_at_us"] = "2099-01-01T00:00:00.000000+00:00"
s, j = req(A, "POST", "/_test/import", ex); print("import with snapshot K in 2099 ->", s, j)
if s == 204:
    T = toks(A); s, p = req(A, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("p2"), token=T["ada"]); print("next payment created_at:", p["created_at"], "(real time:", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), ")")
    s, c = req(A, "POST", f"/payments/{p['payment_id']}/corrections", {"expected_revision": 1, "amount": 2, "effective_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reason": "x"}, headers=idem("c"), token=T["ada"]); print("correction with effective_at = real now ->", s, c if s != 201 else "201")
    reset(A, fx()); T = toks(A); s, p2 = req(A, "POST", "/payments", {"to_handle": "bob", "amount": 1}, headers=idem("p3"), token=T["ada"]); print("after a /_test/reset the next payment created_at is still:", p2["created_at"], "(the service clock stays in 2099 until the process restarts)")
    print("FAIL: an impossible snapshot instant (a read can never start in the future) was accepted and moved the service clock" if p["created_at"].startswith("2099") else "ok")
    sys.exit(1 if p["created_at"].startswith("2099") else 0)
print("PASS (rejected)")
