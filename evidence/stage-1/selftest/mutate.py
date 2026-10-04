#!/usr/bin/env python3
"""Mutation sanity run: apply one deliberate bug at a time to refimpl.py and confirm the checks fail.
    python3 selftest/mutate.py        (from evidence/stage-1)"""
import os, subprocess, sys, time, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "refimpl.py")).read()
MUTANTS = {
 "remainder-to-last": ('1 if i < r else 0', '1 if i >= len(hs) - r else 0'),
 "sequential-not-net": ('if any(S["users"][i]["balance"] + d < 0 for i, d in net.items()):',
                        'if any(S["users"][f["id"]]["balance"] < amt for f, to, amt, _, _ in ents):'),
 "replay-returns-201": ('return 200, rec["resp"]', 'return 201, rec["resp"]'),
 "private-leaks": ('p["visibility"] == "public" or uid in', 'True or uid in'),
 "pay-ignores-status": ('if q["status"] != "pending":\n                    raise Err(409, "request_not_pending")', 'pass'),
 "key-not-per-user": ('ik = json.dumps([uid, method, path, key])', 'ik = json.dumps(["x", method, path, key])'),
 "validation-before-key": ('rec = S["idem"].get(ik)', 'rec = None if not isinstance(body.get("amount", 1), (int, float)) else S["idem"].get(ik)'),
 "insufficient-checked-at-request-create": ('            if payer["id"] == uid:\n                raise Err(422, "self_request")', '            if payer["id"] == uid:\n                raise Err(422, "self_request")\n            if payer["balance"] < amt:\n                raise Err(409, "insufficient_funds")'),
 "export-drops-tokens": ('"state": copy.deepcopy(S)}', '"state": {**copy.deepcopy(S), "tokens": {}}}'),
 "float-amounts-accepted": ('if isinstance(v, float) and not v.is_integer():\n        raise Err(422, "validation_failed", "amount")\n    v = int(v)', 'v = int(v)'),
 "plaintext-password": ('h = hpw(pw)', 'h = "$" + pw'),
 "limit-lenient": ('if v < lo or (hi and v > hi):', 'if v < lo:'),
 "failed-key-claimed": ('    resp = fn()\n    S["idem"][ik]', '    try:\n        resp = fn()\n    except Err:\n        S["idem"][ik] = {"body": copy.deepcopy(body), "resp": {}}\n        raise\n    S["idem"][ik]'),
 "settlement-partial-commit": ('            if any(S["users"][i]["balance"] + d < 0 for i, d in net.items()):\n                raise Err(409, "insufficient_funds")',
                               '            if any(S["users"][i]["balance"] + d < 0 for i, d in net.items()):\n                for f, to, amt, _, _ in ents[:1]:\n                    f["balance"] -= amt; to["balance"] += amt\n                raise Err(409, "insufficient_funds")'),
 "payments-unlocked-race": [('        with LOCK:\n            return idem(uid, method, path, key, b, run)', '        return idem(uid, method, path, key, b, run)'),
                            ('            if me_["balance"] < amt:\n                raise Err(409, "insufficient_funds")\n            me_["balance"] -= amt',
                             '            if me_["balance"] < amt:\n                raise Err(409, "insufficient_funds")\n            __import__("time").sleep(0.01)\n            me_["balance"] -= amt')],
}
bad = 0
for name, spec in MUTANTS.items():
    pairs = spec if isinstance(spec, list) else [spec]
    src = SRC
    if any(a not in src for a, b in pairs):
        print("!! mutant %s: anchor not found" % name); bad += 1; continue
    for a, b in pairs:
        src = src.replace(a, b, 1)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
    port = "8097"
    srv = subprocess.Popen([sys.executable, f.name], env={**os.environ, "PORT": port}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    out = subprocess.run([sys.executable, os.path.join(HERE, "..", "run_checks.py"), "http://127.0.0.1:" + port, "-q"], capture_output=True, text=True).stdout
    srv.kill(); os.unlink(f.name)
    last = [l for l in out.splitlines() if l.startswith("checks run")][-1]
    killed = "failed: 0  errors: 0" not in last
    print("%-40s %s   %s" % (name, "KILLED" if killed else "SURVIVED", last))
    bad += (not killed)
sys.exit(1 if bad else 0)
