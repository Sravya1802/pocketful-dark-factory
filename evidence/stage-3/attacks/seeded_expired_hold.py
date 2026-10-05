#!/usr/bin/env python3
"""Seeded `expired` hold with a FUTURE expires_at: current /me vs historical /me?as_of views. usage: seeded_expired_hold.py URL (RESETS)"""
import sys, time
from s3lib import *
from oracle3 import parse, fmt
A = sys.argv[1]
au = [{"id": "e1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 300, "status": "expired", "expires_at": "2099-01-01T00:00:00+00:00"}]
s, j = req(A, "POST", "/_test/reset", fx(authorizations=au)); assert s == 204; T = toks(A)
cur = me(A, T["ada"]); print("current /me:", {k: cur[k] for k in ("total", "available", "held")})
now = int(time.time() * 1e6); bad = False
for lab, us in (("now-1s", now - 1_000_000), ("now-10us", now - 10), ("now+1h", now + 3_600_000_000)):
    m = req(A, "GET", "/me?as_of=" + fmt(us).replace("+", "%2B"), token=T["ada"])[1]; print(f"as_of {lab}:", {k: m[k] for k in ("total", "available", "held")})
    if m["held"] != cur["held"]: bad = True
print("FAIL: historical view just before now disagrees with the current view (hold of status 'expired' still held until its future expires_at)" if bad else "PASS")
sys.exit(1 if bad else 0)
