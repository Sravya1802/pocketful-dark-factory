#!/usr/bin/env python3
"""Consistency between coverage.md and the checks (API + UI). Prints counts. Exit 1 on dangling/unreferenced ids."""
import glob, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PFX = r"(?:ME|AZ|AX|AC|NEG|UPG|UI)"
tests = {"api": set(), "ui": set()}
for f in glob.glob(os.path.join(HERE, "checks", "*test_*.py")):
    kind = "ui" if os.path.basename(f).startswith("uitest_") else "api"
    tests[kind] |= set(re.findall(r'"\[(' + PFX + r'-\d+)\]', open(f).read()))
allt = tests["api"] | tests["ui"]
md = open(os.path.join(HERE, "coverage.md")).read()
rows = [l for l in md.splitlines() if re.match(r"\| [A-Z]-\d+ \|", l)]
used, shipped = set(), {}
for l in rows:
    cells = [c.strip() for c in l.strip().strip("|").split("|")]
    c = cells[3]
    for m in re.finditer(r"(" + PFX + r")-(\d+)\s*…\s*(?:" + PFX + r"-)?(\d+)", c):
        used |= {"%s-%02d" % (m.group(1), n) for n in range(int(m.group(2)), int(m.group(3)) + 1)}
    used |= set(re.findall(r"\b(" + PFX + r"-\d+)\b", c))
    sh = cells[4][:1]
    shipped[sh] = shipped.get(sh, 0) + 1
dangling = sorted(used - allt)
unref = sorted(allt - used)
print("matrix rows: %d" % len(rows))
print("shipped checks: Y=%d  P(partly)=%d  N(not at all)=%d  M(manual only)=%d  not-testable=%d" % (shipped.get("Y", 0), shipped.get("P", 0), shipped.get("N", 0), shipped.get("M", 0), shipped.get("—", 0)))
print("rows NOT covered by the shipped checks (N): %d; partly (P): %d" % (shipped.get("N", 0), shipped.get("P", 0)))
print("API checks: %d   UI checks: %d" % (len(tests["api"]), len(tests["ui"])))
if dangling: print("DANGLING ids in matrix (no such check):", dangling)
if unref: print("checks not referenced from any matrix row:", unref)
sys.exit(1 if dangling or unref else 0)
