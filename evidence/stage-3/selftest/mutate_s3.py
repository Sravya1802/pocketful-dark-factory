#!/usr/bin/env python3
"""Stage-3 mutation sanity: inject one defect at a time into refimpl3.py (text edits or REF3_BUGS flags); the checks must fail.
    python3 selftest/mutate_s3.py   (from evidence/stage-3; ~12 min)"""
import os, shutil, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "refimpl3.py")).read()
FLAGS = ["future-seed-ok", "opening-ignores-seed", "ignore-available", "no-overdraft-check", "known_at-ignored"]
M = {
 "ties-by-id-descending": ('out.sort(key=lambda m: (m[0], m[1]))', 'out.sort(key=lambda m: (m[0], [-ord(c) for c in m[1]]))'),
 "as_of-exclusive": ('        if eff <= T:\n            b +=', '        if eff < T:\n            b +='),
 "window-end-inclusive": ('            if eff >= to:\n                continue', '            if eff > to:\n                continue'),
 "window-start-exclusive": ('if frm is not None and eff < frm:', 'if frm is not None and eff <= frm:'),
 "snapshot-not-frozen": ('    if "snapshot" in qs:\n        if any', '    if False and "snapshot" in qs:\n        if any'),
 "snapshot-readable-by-others": ('if not sn or sn["uid"] != uid:', 'if not sn:'),
 "snapshot-accepts-window": ('        if any(k_ in qs for k_ in ("from", "to", "known_at")):\n            raise Err(422, "validation_failed", "snapshot with window")\n', ''),
 "replay-returns-newest-revision": ('        return R.idem(uid, "POST", "/payments/%s/corrections" % pid, key, b, run)',
                                    '        st_, resp_ = R.idem(uid, "POST", "/payments/%s/corrections" % pid, key, b, run)\n        if st_ == 200:\n            l_ = p["revs"][-1]\n            resp_ = {k_: l_[k_] for k_ in ("revision", "amount", "effective_at", "recorded_at", "reason")} | {"payment_id": pid}\n        return st_, resp_'),
 "stale-revision-unchecked": ('        if er != latest["revision"]:\n            raise Err(409, "stale_revision")', '        pass'),
 "insufficient-funds-unchecked": ('if diff != 0 and R.avail(debtor) < abs(diff):', 'if False:'),
 "correction-always-debits-sender": ('        sender["balance"] -= diff\n        receiver["balance"] += diff', '        sender["balance"] -= abs(diff)\n        receiver["balance"] += abs(diff)'),
 "linked-payments-correctable": ('        if p.get("settlement_id") or p.get("authorization_id"):\n            raise Err(422, "linked_payment_immutable")\n', ''),
 "revisions-visible-to-third-parties": ('if not p or uid not in (p["from_user_id"], p["to_user_id"]):', 'if not p:'),
 "zero-amount-rejected": ('amount = int_field(b["amount"], 0, 10**9)', 'amount = int_field(b["amount"], 1, 10**9)'),
 "future-effective-allowed": ('        if eff > utcnow():\n            raise Err(422, "validation_failed", "effective in the future")\n', ''),
 "capture-does-not-reduce-hold": ('                mm["captures"].append([iso(P(cp["created_at"])), cp["amount"]])', '                pass'),
 "deadline-unknown-until-known-at": ('    rel = [expires]\n', '    rel = [INF] if P(m["created"]) != K else [expires]\n'),
 "void-without-closed_at": ('mm["close_kind"], mm["closed_at"] = "void", iso(utcnow())', 'mm["close_kind"], mm["closed_at"] = "void", None'),
 "statement-ignores-known_at": ('tp.get("to", start), tp.get("known_at", INF)', 'tp.get("to", start), INF'),
 "recorded-at-whole-seconds": ('        rec = utcnow()\n        last = P(latest["recorded_at"])\n        if rec <= last:\n            rec = last + timedelta(microseconds=1)',
                               '        rec = utcnow().replace(microsecond=0)\n        last = P(latest["recorded_at"])'),
 "statement-shows-original-amount": ('            po["amount"] = r["amount"]\n', ''),
 "feed-in-insertion-order": ('            vis.sort(key=lambda x: (x[0], x[1]), reverse=True)', '            vis.reverse()'),
 "seeded-created_at-ignored": ('c = iso(parse_instant(spec["created_at"])) if spec.get("created_at") else p["created_at"]', 'c = p["created_at"]'),
 "as_of-echo-normalised": ('            obj[name] = given', '            obj[name] = iso(tp[name][1])'),
 "corrections-unlocked-race": [('    with LOCK:\n        return R.idem(uid, "POST", "/payments/%s/corrections" % pid, key, b, run)', '    return R.idem(uid, "POST", "/payments/%s/corrections" % pid, key, b, run)'),
                               ('        diff = amount - latest["amount"]\n', '        __import__("time").sleep(0.01)\n        diff = amount - latest["amount"]\n')],
}
for f in FLAGS:
    M["flag:" + f] = f
bad = 0
for name, spec in M.items():
    if len(sys.argv) > 1 and name not in sys.argv[1:]:
        continue
    env = {**os.environ, "PORT": "8094"}
    src = SRC
    if isinstance(spec, str):
        env["REF3_BUGS"] = spec
    else:
        pairs = spec if isinstance(spec, list) else [spec]
        if any(a not in src for a, b in pairs):
            print("!! %s: anchor not found" % name); bad += 1; continue
        for a, b in pairs:
            src = src.replace(a, b, 1)
    tmp = tempfile.mkdtemp()
    for fn in ("refimpl2.py", "refui.html"):
        shutil.copy(os.path.join(HERE, fn), tmp)
    open(os.path.join(tmp, "refimpl3.py"), "w").write(src)
    srv = subprocess.Popen([sys.executable, os.path.join(tmp, "refimpl3.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    out = subprocess.run([sys.executable, os.path.join(HERE, "..", "run_checks.py"), "http://127.0.0.1:8094", "-q"], capture_output=True, text=True).stdout
    srv.kill(); shutil.rmtree(tmp)
    last = [l for l in out.splitlines() if l.startswith("checks run")][-1]
    killed = "failed: 0  errors: 0" not in last
    print("%-38s %s  %s" % (name, "KILLED" if killed else "SURVIVED", last)); sys.stdout.flush()
    bad += (not killed)
sys.exit(1 if bad else 0)
