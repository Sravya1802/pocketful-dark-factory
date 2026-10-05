#!/usr/bin/env bash
# Pre-submission check. Run against the PUSHED repo, from a fresh clone, as judges will.
#   factory/scripts/final-check.sh <git-url-or-path> [track] [evidence-out-dir]
# Exits non-zero if any blocking check fails. Writes no-network logs to evidence-out-dir.
set -uo pipefail

SRC="${1:?usage: final-check.sh <git-url-or-path> [track] [evidence-out-dir]}"
TRACK="${2:-pocketful}"
KICKOFF="${KICKOFF:-$HOME/darkfactory/dark-factory-wearedevs}"
PY="$KICKOFF/.venv/bin/python"
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/final-check.XXXXXX")"
CLONE="$WORK/repo"
EVID="${3:-$WORK/evidence}"
mkdir -p "$EVID"
FAIL=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=1; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

step "1. fresh clone"
if git clone -q "$SRC" "$CLONE"; then pass "cloned into $CLONE"; else fail "clone failed"; exit 1; fi

step "2. harness check (gates 1, 2, mandate half of 4, credentials)"
if (cd "$KICKOFF" && "$PY" -m harness check "$CLONE" --track "$TRACK"); then pass "harness check"; else fail "harness check"; fi

step "3. mandates: both tracks' vocabulary, harness/model lines, track-revealing words"
(cd "$KICKOFF" && "$PY" - "$CLONE" <<'PY')
import pathlib, re, sys
from harness import vocabulary
root = pathlib.Path(sys.argv[1]) / "mandates"
bad = 0
for track in ("pocketful", "tablekeeper", "toy"):
    vocab = set(vocabulary.for_track(track))
    for f in sorted(root.glob("*.md")):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            for kind, term in vocabulary.terms_in(line):
                if term in vocab:
                    bad += 1; print(f"  FAIL {f.name}:{n} {track} {kind} `{term}`")
tells = re.compile(r"\b(wallet|payments?|money|balances?|refunds?|settlements?|reservations?|restaurants?|bookings?|diners?|minor units|currency|venmo|opentable|pocketful|tablekeeper)\b", re.I)
for f in sorted(root.glob("*.md")):
    for n, line in enumerate(f.read_text().splitlines(), 1):
        for m in tells.finditer(line):
            print(f"  WARN {f.name}:{n} track-revealing word '{m.group(0)}'")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && pass "no vocabulary hits in any track list" || fail "mandate vocabulary"

step "4. extra credential scan"
if grep -rInE --exclude-dir=.git --exclude=final-check.sh 'sk-ant-[A-Za-z0-9_-]{10,}|FEATHERLESS_API_KEY=[^ ]+|ANTHROPIC_API_KEY=[^ ]+|rc_[A-Za-z0-9]{20,}' "$CLONE" >/dev/null; then
  fail "possible credential (run: grep -rInE 'sk-ant-|API_KEY=' on the clone)"; else pass "no extra credential shapes"; fi

step "5. repository hygiene"
[ -f "$CLONE/.gitmodules" ] && fail ".gitmodules present" || pass "no submodules"
LINKS="$(find "$CLONE" -path "$CLONE/.git" -prune -o -type l -print)"
[ -n "$LINKS" ] && fail "symlinks: $LINKS" || pass "no symlinks"
for d in "$CLONE"/stage-*; do
  [ -d "$d" ] || continue; n="$(basename "$d")"
  [ -e "$d/.git" ] && fail "$n has its own .git"
  [ -f "$d/Dockerfile" ] && [ -f "$d/RUN.md" ] && pass "$n has Dockerfile and RUN.md" || fail "$n missing Dockerfile or RUN.md"
  [ -z "$(ls -A "$d" 2>/dev/null)" ] && fail "$n is empty in the clone"
done
for f in README.md FACTORY.md room.json; do
  [ -s "$CLONE/$f" ] && pass "$f present" || fail "$f missing or empty"
done
grep -qiE 'TODO|TBD|placeholder' "$CLONE/README.md" "$CLONE/FACTORY.md" 2>/dev/null && warn "README/FACTORY still contain TODO/TBD/placeholder"

step "6. remote assets in product code (must ship inside the image)"
HITS="$(grep -rInE --include='*.html' --include='*.js' --include='*.css' --include='*.ts' --include='*.tsx' --include='*.jsx' \
  '(src|href)=["'"'"']https?://|@import +url\(["'"'"']?https?://|url\(["'"'"']?https?://|fonts\.googleapis|cdn\.|unpkg\.com|jsdelivr' "$CLONE"/stage-* 2>/dev/null | grep -v node_modules || true)"
[ -n "$HITS" ] && { fail "remote asset references:"; echo "$HITS" | head -20; } || pass "no remote asset references"

step "7. per stage: docker build, run with --network none, /health within 60s"
docker info >/dev/null 2>&1 || { fail "docker daemon not running"; }
for d in "$CLONE"/stage-*; do
  [ -d "$d" ] || continue; n="$(basename "$d")"; tag="final-check-$n-$STAMP"; log="$EVID/no-network-$n.log"
  { echo "# $n  $(date -u +%FT%TZ)  docker $(docker --version)"; echo "# commit $(git -C "$CLONE" rev-parse HEAD)"; } > "$log"
  if ! docker build -q -t "$tag" "$d" >>"$log" 2>&1; then fail "$n docker build (see $log)"; continue; fi
  cid="$(docker run -d --network none -e PORT=8080 --memory 2g --cpus 2 "$tag")"
  ok=0
  for i in $(seq 1 60); do
    if docker exec "$cid" sh -c 'command -v wget >/dev/null && wget -qO- http://127.0.0.1:8080/health || python3 -c "import urllib.request;print(urllib.request.urlopen(\"http://127.0.0.1:8080/health\").read().decode())" || curl -fs http://127.0.0.1:8080/health' >>"$log" 2>&1; then ok=1; echo "# healthy after ${i}s with --network none" >>"$log"; break; fi
    sleep 1
  done
  docker logs "$cid" >>"$log" 2>&1; docker rm -f "$cid" >/dev/null
  [ $ok -eq 1 ] && pass "$n healthy with no network ($log)" || fail "$n not healthy within 60s with no network ($log)"
done

step "8. harness run --all --mode isolated (the graded mode)"
OUT="$HOME/darkfactory/band-work/checks/final-$STAMP"
if (cd "$KICKOFF" && "$PY" -m harness run --track "$TRACK" --repo "$CLONE" --all --mode isolated --out "$OUT" 2>&1 | tee "$EVID/harness-all.log" | tail -15); then pass "harness run finished (read the claimed-stage lines above)"; else fail "harness run"; fi

step "9. manual items (no script can do these)"
echo "  - follow each stage's RUN.md by hand in a clean environment and use the UI"
echo "  - read room.json: reciprocal @handle exchange present, no rewritten history"
echo "  - video contains the BAND room recording; evidence map timestamps filled"
echo "  - re-read every mandate: would a team building something else use it as is?"

step "result"
[ $FAIL -eq 0 ] && echo "ALL BLOCKING CHECKS PASSED" || echo "BLOCKING FAILURES ABOVE"
echo "clone: $CLONE   evidence: $EVID"
exit $FAIL
