#!/usr/bin/env python3
"""Stage-2 UI acceptance checks in a real browser (Playwright + Chromium).

    /path/to/dark-factory-wearedevs/.venv/bin/python evidence/stage-2/run_ui_checks.py http://127.0.0.1:8080 [-k substr] [--ids UI-30,UI-40] [--headed] [-q] [--json out.json]

Needs Python 3 with `playwright` installed and a Chromium it can launch (the harness venv has both:
`dark-factory-wearedevs/.venv/bin/python`). Resets the service's state: use a throw-away instance.
The service must serve the UI at the given origin (/, /requests, /split, /signup, /login, /authorizations).
"""
import argparse, json, os, re, sys, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "checks"))


class Result(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.rows, self._t0 = [], 0

    def startTest(self, test):
        self._t0 = time.time()
        super().startTest(test)

    def _add(self, test, outcome, detail=""):
        m = re.match(r"\[([A-Z]+-\d+)\]", test.shortDescription() or "")
        self.rows.append({"id": m.group(1) if m else None, "test": test.id(), "outcome": outcome, "seconds": round(time.time() - self._t0, 2), "detail": detail[-1500:]})

    def addSuccess(self, t): super().addSuccess(t); self._add(t, "pass")
    def addFailure(self, t, e): super().addFailure(t, e); self._add(t, "fail", self._exc_info_to_string(e, t))
    def addError(self, t, e): super().addError(t, e); self._add(t, "error", self._exc_info_to_string(e, t))
    def addSkip(self, t, r): super().addSkip(t, r); self._add(t, "skip", r)


def flat(s):
    for t in s:
        if isinstance(t, unittest.TestSuite): yield from flat(t)
        else: yield t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", nargs="?", default=os.environ.get("BASE_URL", "http://127.0.0.1:8080"))
    ap.add_argument("-k"); ap.add_argument("--ids"); ap.add_argument("--json"); ap.add_argument("--headed", action="store_true")
    ap.add_argument("-q", action="store_true"); ap.add_argument("--failfast", action="store_true")
    a = ap.parse_args()
    os.environ["BASE_URL"] = a.base_url
    if a.headed: os.environ["HEADED"] = "1"
    tests = list(flat(unittest.defaultTestLoader.discover(os.path.join(HERE, "checks"), pattern="uitest_*.py")))
    if a.k: tests = [t for t in tests if a.k in t.id() or a.k in (t.shortDescription() or "")]
    if a.ids:
        want = set(a.ids.split(","))
        tests = [t for t in tests if any(("[%s]" % w) in (t.shortDescription() or "") for w in want)]
    res = unittest.TextTestRunner(resultclass=Result, verbosity=0 if a.q else 2, failfast=a.failfast).run(unittest.TestSuite(tests))
    if a.json:
        json.dump({"base_url": a.base_url, "total": res.testsRun, "failures": len(res.failures), "errors": len(res.errors), "skipped": len(res.skipped), "results": res.rows}, open(a.json, "w"), indent=1)
    print("\nUI checks run: %d  passed: %d  failed: %d  errors: %d  skipped: %d" % (res.testsRun, len([r for r in res.rows if r["outcome"] == "pass"]), len(res.failures), len(res.errors), len(res.skipped)))
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
