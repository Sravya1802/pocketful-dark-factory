#!/usr/bin/env python3
"""Consistency between coverage.md and the checks. Prints counts. Exit 1 on dangling/unreferenced ids."""
import glob, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
tests = set()
for f in glob.glob(os.path.join(HERE, "checks", "test_*.py")):
    tests |= set(re.findall(r'"\[([A-Z]+-\d+[a-z]?)\]', open(f).read()))
md = open(os.path.join(HERE, "coverage.md")).read()
rows = [l for l in md.splitlines() if re.match(r"\| R-\d+ \|", l)]
used = set()
shipped = {"Y": 0, "P": 0, "N": 0, "D": 0, "-": 0}
for l in rows:
    cells = [c.strip() for c in l.strip().strip("|").split("|")]
    used |= set(re.findall(r"\b([A-Z]{2,4}-\d{2}[a-z]?)\b", cells[3]))
    sh = cells[4][:1]
    shipped["-" if sh in ("—", "-") else sh] += 1
used = {u for u in used if not u.startswith("R-")}
doc = {u for u in used if u.startswith("DOC-")}
dangling = sorted(used - tests - doc)
unref = sorted(tests - used)
print("matrix rows: %d" % len(rows))
print("shipped checks exercise: Y=%d  P(partly)=%d  N(not at all)=%d  D(deliverable, docker script)=%d  not-testable=%d" % (
    shipped["Y"], shipped["P"], shipped["N"], shipped["D"], shipped["-"]))
print("rows NOT covered by the shipped checks (N): %d; partly (P): %d" % (shipped["N"], shipped["P"]))
print("checks: %d (HTTP) + docs/deployment DOC-01..05 (docker script)" % len(tests))
if dangling: print("DANGLING ids in matrix (no such check):", dangling)
if unref: print("checks not referenced from any matrix row:", unref)
sys.exit(1 if dangling or unref else 0)
