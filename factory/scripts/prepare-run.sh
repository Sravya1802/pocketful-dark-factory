#!/usr/bin/env bash
# Create the fresh result repository for the submitted run and point every seat at it.
#   factory/scripts/prepare-run.sh            # dry run: prints what it would do
#   factory/scripts/prepare-run.sh --apply
# Refuses to touch an existing non-empty result repo. Does not create rooms or dispatch.
set -euo pipefail
ROOT="$HOME/darkfactory"
RESULT="$ROOT/band-work/result"
OWNER="$(band whoami 2>/dev/null | sed -nE 's/.* @([^ ]+) .*/\1/p')"
SEATS=(coordinator analyst builder adversary gate)
APPLY="${1:-}"
run() { echo "+ $*"; [ "$APPLY" = "--apply" ] && "$@"; return 0; }

[ -n "$OWNER" ] || { echo "band whoami failed; is Band Desktop running and signed in?"; exit 1; }
if [ -d "$RESULT" ] && [ -n "$(ls -A "$RESULT" 2>/dev/null)" ]; then
  echo "refusing: $RESULT exists and is not empty. Move it aside first."; exit 1; fi

echo "== checks before the run"
pmset -g batt | grep -q "AC Power" && echo "  charger: on AC power" || echo "  WARNING: on battery. Plug in before dispatching."
for s in "${SEATS[@]}"; do [ -f "$ROOT/factory/mandates/$s.md" ] || { echo "missing mandate $s.md"; exit 1; }; done
(cd "$ROOT/dark-factory-wearedevs" && .venv/bin/python - "$ROOT/factory/mandates" <<'PY'
import pathlib, sys
from harness import vocabulary
bad = [(f.name, n, t) for tr in ("pocketful", "tablekeeper") for f in pathlib.Path(sys.argv[1]).glob("*.md")
       for n, l in enumerate(f.read_text().splitlines(), 1) for _, t in vocabulary.terms_in(l) if t in set(vocabulary.for_track(tr))]
print("  mandate vocabulary: clean" if not bad else f"  VOCABULARY HITS: {bad}"); sys.exit(1 if bad else 0)
PY
)

echo "== result repository"
run mkdir -p "$RESULT/mandates" "$ROOT/band-work/checks"
run cp "$ROOT/factory/mandates/"*.md "$RESULT/mandates/"
run git -C "$RESULT" init -q -b main
run git -C "$RESULT" add -A
run git -C "$RESULT" -c user.name=human-setup -c user.email=setup@factory.local commit -q -m "Setup: seat mandates (human, before dispatch)"

echo "== point every seat at the result repository"
for s in "${SEATS[@]}"; do run band runtime template set --as "$OWNER/$s" --spawn-cwd "$RESULT"; done

echo
echo "Next (you): charger in, lid open, then"
echo "  caffeinate -i -m -s -t 36000 &"
echo "  create a NEW room in Band Desktop, add coordinator, start screen recording,"
echo "  paste factory/dispatch/pocketful-run.md (below its line) once, and send nothing else."
