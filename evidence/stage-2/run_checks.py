#!/usr/bin/env python3
"""Run the Pocketful STAGE-2 API acceptance checks against a running service.

    python3 evidence/stage-2/run_checks.py http://127.0.0.1:8080 [-k substring] [--ids AZ-01,AC-02] [--include-stage1] [--failfast]

--include-stage1 also runs the accepted stage-1 checks (../stage-1/checks), which must keep passing in stage 2
(one stage-1 check, API-01 = exact /me shape, is skipped because stage 2 adds fields; ME-01 replaces it).
Set STAGE1_URL=http://host:port (a running STAGE-1 service) to enable the upgrade-by-import checks UPG-10..12.

Standard library only. Exit code 0 = all passed. A one-line-per-check report is printed;
--json PATH also writes machine-readable results (id, name, outcome, detail).
State is reset by the checks themselves (POST /_test/reset): never point this at a service you care about.
"""
import argparse
import json
import os
import re
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
S1_CHECKS = os.path.join(HERE, "..", "stage-1", "checks")
SKIP_IF_STAGE1 = {"API-01"}  # exact stage-1 /me shape; stage 2 adds total/available/held
sys.path.insert(0, os.path.join(HERE, "checks"))


class Result(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.rows = []
        self._t0 = 0

    def startTest(self, test):
        self._t0 = time.time()
        super().startTest(test)

    def _add(self, test, outcome, detail=""):
        doc = (test.shortDescription() or "")
        m = re.match(r"\[([A-Z]+-\d+[a-z]?)\]", doc)
        self.rows.append({"id": m.group(1) if m else None, "test": test.id(), "outcome": outcome,
                          "seconds": round(time.time() - self._t0, 2), "detail": detail[-1500:]})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, "fail", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, "error", self._exc_info_to_string(err, test))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", nargs="?", default=os.environ.get("BASE_URL", "http://127.0.0.1:8080"))
    ap.add_argument("-k", default=None, help="only tests whose id or description contains this substring")
    ap.add_argument("--ids", default=None, help="comma-separated check ids, e.g. PAY-01,IDM-03")
    ap.add_argument("--json", default=None)
    ap.add_argument("--failfast", action="store_true")
    ap.add_argument("--include-stage1", action="store_true")
    ap.add_argument("-q", action="store_true", help="only print failures")
    a = ap.parse_args()
    os.environ["BASE_URL"] = a.base_url
    suite = unittest.defaultTestLoader.discover(os.path.join(HERE, "checks"), pattern="test_*.py")

    def flat(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                yield from flat(t)
            else:
                yield t

    tests = list(flat(suite))
    if a.include_stage1:
        sys.path.append(S1_CHECKS)  # appended: stage-2 `lib` (a superset) wins
        tests += [t for t in flat(unittest.defaultTestLoader.discover(S1_CHECKS, pattern="test_*.py"))
                  if not any(("[%s]" % i) in (t.shortDescription() or "") for i in SKIP_IF_STAGE1)]
    if a.k:
        tests = [t for t in tests if a.k in t.id() or a.k in (t.shortDescription() or "")]
    if a.ids:
        want = set(a.ids.split(","))
        tests = [t for t in tests if (re.match(r"\[([A-Z]+-\d+[a-z]?)\]", t.shortDescription() or "") or [None, None])[1] in want
                 or any(("[%s]" % w) in (t.shortDescription() or "") for w in want)]
    s = unittest.TestSuite(tests)
    runner = unittest.TextTestRunner(resultclass=Result, verbosity=0 if a.q else 2, failfast=a.failfast, descriptions=True)
    res = runner.run(s)
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"base_url": a.base_url, "total": res.testsRun, "failures": len(res.failures), "errors": len(res.errors),
                       "results": res.rows}, f, indent=1)
    print("\nchecks run: %d  passed: %d  failed: %d  errors: %d" % (res.testsRun, len([r for r in res.rows if r["outcome"] == "pass"]),
                                                                    len(res.failures), len(res.errors)))
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
