#!/usr/bin/env python3
"""API mutation sanity for stage 2: inject one defect at a time into refimpl2.py; the API checks must fail.
    python3 selftest/mutate_api.py   (from evidence/stage-2; takes ~15 min)"""
import os, subprocess, sys, tempfile, time, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "refimpl2.py")).read()
M = {
 "payments-ignore-holds": ('            if avail(uid) < amt:\n                raise Err(409, "insufficient_funds")\n            me_["balance"] -= amt\n            rec["balance"] += amt\n            return pay_obj(make_payment(me_, rec, amt, note, vis))',
                           '            if me_["balance"] < amt:\n                raise Err(409, "insufficient_funds")\n            me_["balance"] -= amt\n            rec["balance"] += amt\n            return pay_obj(make_payment(me_, rec, amt, note, vis))'),
 "settlement-ignores-holds": ('if any(avail(i) + d < 0 for i, d in net.items()):', 'if any(S["users"][i]["balance"] + d < 0 for i, d in net.items()):'),
 "capture-default-nonfinal": ('fin = b.get("final", True)', 'fin = b.get("final", False)'),
 "no-clock-expiry": ('        if a["status"] == "open" and a["expires_ts"] <= t:\n            a["status"] = "expired"\n            a["remaining_amount"] = 0', '        pass'),
 "exceeds-compares-total": ('if amt > a["remaining_amount"]:', 'if amt > a["amount"]:'),
 "receiver-may-void": ('            if a["from_user_id"] != uid:\n                raise Err(403, "forbidden")\n            if a["status"] == "voided"', '            if uid not in (a["from_user_id"], a["to_user_id"]):\n                raise Err(403, "forbidden")\n            if a["status"] == "voided"'),
 "capture-skips-open-check": ('                if a["status"] != "open":\n                    raise Err(409, "authorization_not_open")\n                if amt is None:', '                if amt is None:'),
 "payment-lacks-authorization-id": ('    d.setdefault("authorization_id", None)\n    return d', '    d.pop("authorization_id", None)\n    return d'),
 "expired-seed-counts-as-held": ('live = st == "open" and ex > tnow', 'live = st == "open"'),
 "oversubscription-allowed": ('                    if used[f["id"]] > f["balance"]:\n                        raise ValueError', '                    pass'),
 "available-not-derived": ('"total": u["balance"], "available": avail(uid), "held": held(uid)', '"total": u["balance"], "available": u["balance"], "held": held(uid)'),
 "void-not-releasing": ('a["status"], a["remaining_amount"] = "voided", 0', 'a["status"] = "voided"'),
 "auth-in-feed": ('vis = [p for p in S["payments"] if p["visibility"] == "public" or uid in (p["from_user_id"], p["to_user_id"])]',
                  'vis = [p for p in S["payments"] if p["visibility"] == "public" or uid in (p["from_user_id"], p["to_user_id"])] + [dict(a, payment_id=a["authorization_id"], from_handle=a["from_handle"]) for a in S["auths"].values() if uid in (a["from_user_id"], a["to_user_id"])]'),
 "html-for-all-accept": ('"text/html" in hdrs.get("accept", "")))\n        if page:', 'True))\n        if page:'),
 "capture-race-unlocked": [('            with LOCK:\n                return idem(uid, method, path, key, b, run)\n        with LOCK:\n            a = S["auths"].get(aid)', '            return idem(uid, method, path, key, b, run)\n        with LOCK:\n            a = S["auths"].get(aid)'),
                           ('                f, t = S["users"][a["from_user_id"]], S["users"][uid]\n                f["balance"] -= amt', '                __import__("time").sleep(0.01)\n                f, t = S["users"][a["from_user_id"]], S["users"][uid]\n                f["balance"] -= amt')],
}
bad = 0
for name, spec in M.items():
    pairs = spec if isinstance(spec, list) else [spec]
    src = SRC
    if any(a not in src for a, b in pairs):
        print("!! %s: anchor not found" % name); bad += 1; continue
    for a, b in pairs: src = src.replace(a, b, 1)
    tmp = tempfile.mkdtemp()
    open(os.path.join(tmp, "refimpl2.py"), "w").write(src)
    shutil.copy(os.path.join(HERE, "refui.html"), tmp)
    srv = subprocess.Popen([sys.executable, os.path.join(tmp, "refimpl2.py")], env={**os.environ, "PORT": "8095"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    out = subprocess.run([sys.executable, os.path.join(HERE, "..", "run_checks.py"), "http://127.0.0.1:8095", "-q"], capture_output=True, text=True).stdout
    srv.kill(); shutil.rmtree(tmp)
    last = [l for l in out.splitlines() if l.startswith("checks run")][-1]
    killed = "failed: 0  errors: 0" not in last
    print("%-34s %s  %s" % (name, "KILLED" if killed else "SURVIVED", last)); sys.stdout.flush()
    bad += (not killed)
sys.exit(1 if bad else 0)
