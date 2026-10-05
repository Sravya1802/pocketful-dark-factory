#!/usr/bin/env python3
"""Stage-4 mutation sanity: inject one defect at a time into refimpl4.py (text edits or REF4_BUGS/REF3_BUGS flags); the stage-4 checks must fail.
    python3 selftest/mutate_s4.py [mutant names...]   (from evidence/stage-4; ~10 min for all)"""
import os, shutil, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "refimpl4.py")).read()
EV = os.path.dirname(HERE)
M = {
 "refund-by-sender-allowed": ('        if p["to_user_id"] != uid:\n            raise Err(403, "forbidden")\n    if key is None or key == "":\n        raise Err(400, "missing_idempotency_key")\n    b = R3.json_body(raw)\n\n    def run():\n        if "amount" not in b:',
                              '        if uid not in (p["to_user_id"], p["from_user_id"]):\n            raise Err(403, "forbidden")\n    if key is None or key == "":\n        raise Err(400, "missing_idempotency_key")\n    b = R3.json_body(raw)\n\n    def run():\n        if "amount" not in b:'),
 "refund-of-refund-allowed": ('        if p.get("refund_of"):\n            raise Err(422, "invalid_refund_target")\n', ''),
 "refund-cap-uses-original-amount": ('p["revs"][-1]["amount"] and "no-refund-cap" not in FLAGS', 'p["amount"] and "no-refund-cap" not in FLAGS'),
 "refund-ignores-held-funds": ('        if R.avail(uid) < amt:\n            raise Err(409, "insufficient_funds")\n        receiver["balance"] -= amt', '        if S["users"][uid]["balance"] < amt:\n            raise Err(409, "insufficient_funds")\n        receiver["balance"] -= amt'),
 "refund-drops-note": ('amt, p["note"], p["visibility"])', 'amt, "", p["visibility"])'),
 "refund-always-public": ('amt, p["note"], p["visibility"])', 'amt, p["note"], "public")'),
 "refund-of-not-set": ('        q["refund_of"] = pid\n', ''),
 "refund-wrong-direction": ('q = R.make_payment(receiver, sender, amt,', 'q = R.make_payment(sender, receiver, amt,'),
 "refund-reopens-request": ('        q["refund_of"] = pid\n', '        q["refund_of"] = pid\n        for rq in S["requests"].values():\n            if rq.get("payment_id") == pid:\n                rq["status"], rq["payment_id"] = "pending", None\n'),
 "refund-becomes-settlement-member": ('        q["refund_of"] = pid\n', '        q["refund_of"] = pid\n        q["settlement_id"] = p.get("settlement_id")\n'),
 "refunded-counts-only-the-largest": ('return sum(x["amount"] for x in S["payments"] if x.get("refund_of") == pid)', 'return max([x["amount"] for x in S["payments"] if x.get("refund_of") == pid] or [0])'),
 "refund-payments-correctable": ('    if p.get("authorization_id") or p.get("refund_of"):\n        return True', '    if p.get("authorization_id"):\n        return True'),
 "captures-correctable": ('    if p.get("authorization_id") or p.get("refund_of"):\n        return True', '    if p.get("refund_of"):\n        return True'),
 "batch-open-to-non-operators": ('        if uid not in S["operators"]:\n            raise Err(403, "forbidden")\n    key = hdrs', '        if False:\n            raise Err(403, "forbidden")\n    key = hdrs'),
 "batch-duplicates-allowed": (' or len(set(map(str, ids))) != len(ids):', ':'),
 "batch-limit-33": ('not 1 <= len(lst) <= 32', 'not 1 <= len(lst) <= 33'),
 "batch-recorded-at-not-later-than-members": [('    rec = utcnow()\n    for it in items:\n        last = ', '    rec = utcnow() - timedelta(seconds=30)\n    for it in items:\n        last = '),
                                              ('        if rec <= last:\n            rec = last + timedelta(microseconds=1)\n    out = []', '        pass\n    out = []')],
 "batch-ignores-credits-in-affordability": ('net[p["to_user_id"]] = net.get(p["to_user_id"], 0) + diff', 'net[p["to_user_id"]] = net.get(p["to_user_id"], 0) + 0'),
 "batch-skips-historical-check": ('    if R3.violates(overrides):', '    if False:'),
 "batch-revisions-lack-batch-id": ('            new["correction_batch_id"] = bid\n', '            new["x_batch"] = bid\n'),
 "batch-revisions-sorted-not-in-input-order": ('return bid, iso(rec), out', 'return bid, iso(rec), sorted(out, key=lambda x: x["payment_id"])'),
 "batch-parses-all-items-before-item-checks": [('            check_item(it, True)          # item errors are reported in input order\n', '            pass\n'),
                                               ('    if batch:\n        groups = {}', '    for it in (items if batch else []):\n        check_item(it, True)\n    if batch:\n        groups = {}')],
 "batch-future-effective-allowed": ('    if eff > utcnow():\n        raise Err(422, "validation_failed", "effective in the future")\n    return {"pid"', '    return {"pid"'),
 "batch-unlocked-race": [('    with LOCK:\n        return R.idem(uid, "POST", "/correction-batches", key, b, run)', '    return R.idem(uid, "POST", "/correction-batches", key, b, run)'),
                         ('        bid, rec, revs = apply_items(items, True)', '        __import__("time").sleep(0.01)\n        bid, rec, revs = apply_items(items, True)')],
 "refund-unlocked-race": [('    with LOCK:\n        return R.idem(uid, "POST", "/payments/%s/refunds" % pid, key, b, run)', '    return R.idem(uid, "POST", "/payments/%s/refunds" % pid, key, b, run)'),
                          ('        if p.get("refund_of"):\n            raise Err(422, "invalid_refund_target")\n', '        if p.get("refund_of"):\n            raise Err(422, "invalid_refund_target")\n        __import__("time").sleep(0.01)\n')],
 "snapshots-dropped-by-import": "drop-snapshots",
 "flag:no-refund-floor": "no-refund-floor",
 "flag:no-completeness": "no-completeness",
 "flag:no-instant-check": "no-instant-check",
 "flag:no-refund-cap": "no-refund-cap",
}
bad = 0
for name, spec in M.items():
    if len(sys.argv) > 1 and name not in sys.argv[1:]:
        continue
    env = {**os.environ, "PORT": "8093"}
    src = SRC
    if isinstance(spec, str):
        env["REF4_BUGS"] = spec
    else:
        pairs = spec if isinstance(spec, list) else [spec]
        if any(a not in src for a, b in pairs):
            print("!! %s: anchor not found" % name); bad += 1; continue
        for a, b in pairs:
            src = src.replace(a, b, 1)
    tmp = tempfile.mkdtemp()
    for fn in ("refimpl2.py", "refimpl3.py", "refui.html"):
        shutil.copy(os.path.join(HERE, fn), tmp)
    open(os.path.join(tmp, "refimpl4.py"), "w").write(src)
    srv = subprocess.Popen([sys.executable, os.path.join(tmp, "refimpl4.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    out = subprocess.run([sys.executable, os.path.join(EV, "run_checks.py"), "http://127.0.0.1:8093", "-q"], capture_output=True, text=True).stdout
    srv.kill(); shutil.rmtree(tmp)
    last = [l for l in out.splitlines() if l.startswith("checks run")][-1]
    killed = "failed: 0  errors: 0" not in last
    print("%-44s %s  %s" % (name, "KILLED" if killed else "SURVIVED", last)); sys.stdout.flush()
    bad += (not killed)
sys.exit(1 if bad else 0)
