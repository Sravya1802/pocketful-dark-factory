#!/usr/bin/env bash
# Build + run the stage-2 image (and optionally the frozen stage-1 image for the upgrade-by-import checks) and run both suites.
#   evidence/stage-2/docker_run_checks.sh /abs/stage-2 [/abs/stage-1]
# env: PY (python with playwright; default dark-factory venv), PORT2 (18082) PORT1 (18081)
# Tip: export DOCKER_BUILDKIT=0 DOCKER_CONFIG=$(mktemp -d) first if BuildKit hangs on the credential helper.
set -u
S2="${1:?usage: $0 /abs/stage-2 [/abs/stage-1]}"; S1="${2:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-/Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/.venv/bin/python}"
PORT2="${PORT2:-18082}"; PORT1="${PORT1:-18081}"; TAG="pocketful-s2-check-$$"
rc=0; fail() { echo "FAIL $*"; rc=1; }; ok() { echo "ok   $*"; }
trap 'docker rm -f $TAG-2 $TAG-1 $TAG-n >/dev/null 2>&1; docker rmi -f $TAG-2 $TAG-1 >/dev/null 2>&1' EXIT
[ -f "$S2/Dockerfile" ] && ok "Dockerfile present" || fail "Dockerfile missing"
[ -s "$S2/RUN.md" ] && ok "RUN.md present" || fail "RUN.md missing"
docker build -t $TAG-2 "$S2" >/tmp/$TAG-2.log 2>&1 && ok "stage-2 image builds" || { fail "stage-2 build (see /tmp/$TAG-2.log)"; exit 1; }
wait_health() { local t0=$(date +%s); while :; do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:$1/health)" = 200 ] && { echo $(( $(date +%s) - t0 )); return 0; }; [ $(( $(date +%s) - t0 )) -ge 60 ] && return 1; sleep 0.25; done; }
docker run -d --name $TAG-2 --cpus 2 --memory 2g -e PORT=$PORT2 -p $PORT2:$PORT2 $TAG-2 >/dev/null
s=$(wait_health $PORT2) && ok "healthy ${s}s after start (limit 60 s)" || { fail "not healthy within 60 s"; docker logs $TAG-2 | tail; exit 1; }
docker run -d --name $TAG-n --network none --cpus 2 --memory 2g -e PORT=8080 $TAG-2 >/dev/null; sleep 3
[ "$(docker inspect -f '{{.State.Running}}' $TAG-n)" = true ] && ok "container stays up with --network none" || fail "--network none: container exited"
if [ -n "$S1" ]; then
  docker build -t $TAG-1 "$S1" >/tmp/$TAG-1.log 2>&1 && docker run -d --name $TAG-1 -e PORT=$PORT1 -p $PORT1:$PORT1 $TAG-1 >/dev/null && wait_health $PORT1 >/dev/null && export STAGE1_URL=http://127.0.0.1:$PORT1 && ok "stage-1 service up for upgrade checks" || fail "stage-1 service did not start"
fi
python3 "$HERE/run_checks.py" http://127.0.0.1:$PORT2 --include-stage1 -q --json /tmp/$TAG.api.json || rc=1
"$PY" "$HERE/run_ui_checks.py" http://127.0.0.1:$PORT2 -q --json /tmp/$TAG.ui.json || rc=1
echo "results: /tmp/$TAG.api.json /tmp/$TAG.ui.json"
exit $rc
