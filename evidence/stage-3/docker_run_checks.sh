#!/usr/bin/env bash
# Build + run the stage-3 image (and optionally the frozen stage-1 / stage-2 images for import checks) and run the suites.
#   evidence/stage-3/docker_run_checks.sh /abs/stage-3 [/abs/stage-1 [/abs/stage-2]]
# Tip: export DOCKER_BUILDKIT=0 DOCKER_CONFIG=$(mktemp -d) first if BuildKit hangs on the credential helper.
set -u
S3="${1:?usage: $0 /abs/stage-3 [/abs/stage-1 [/abs/stage-2]]}"; S1="${2:-}"; S2="${3:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-/Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/.venv/bin/python}"
P3="${P3:-18083}"; P1="${P1:-18081}"; P2="${P2:-18082}"; TAG="pocketful-s3-check-$$"
rc=0; fail() { echo "FAIL $*"; rc=1; }; ok() { echo "ok   $*"; }
trap 'docker rm -f $TAG-3 $TAG-1 $TAG-2 $TAG-n >/dev/null 2>&1; docker rmi -f $TAG-3 $TAG-1 $TAG-2 >/dev/null 2>&1' EXIT
[ -f "$S3/Dockerfile" ] && ok "Dockerfile present" || fail "Dockerfile missing"
[ -s "$S3/RUN.md" ] && ok "RUN.md present" || fail "RUN.md missing"
docker build -t $TAG-3 "$S3" >/tmp/$TAG-3.log 2>&1 && ok "stage-3 image builds" || { fail "stage-3 build (see /tmp/$TAG-3.log)"; exit 1; }
wait_health() { local t0=$(date +%s); while :; do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:$1/health)" = 200 ] && { echo $(( $(date +%s) - t0 )); return 0; }; [ $(( $(date +%s) - t0 )) -ge 60 ] && return 1; sleep 0.25; done; }
docker run -d --name $TAG-3 --cpus 2 --memory 2g -e PORT=$P3 -p $P3:$P3 $TAG-3 >/dev/null
s=$(wait_health $P3) && ok "healthy ${s}s after start (limit 60 s)" || { fail "not healthy within 60 s"; docker logs $TAG-3 | tail; exit 1; }
docker run -d --name $TAG-n --network none --cpus 2 --memory 2g -e PORT=8080 $TAG-3 >/dev/null; sleep 3
[ "$(docker inspect -f '{{.State.Running}}' $TAG-n)" = true ] && ok "container stays up with --network none" || fail "--network none: container exited"
if [ -n "$S1" ]; then docker build -t $TAG-1 "$S1" >/tmp/$TAG-1.log 2>&1 && docker run -d --name $TAG-1 -e PORT=$P1 -p $P1:$P1 $TAG-1 >/dev/null && wait_health $P1 >/dev/null && export STAGE1_URL=http://127.0.0.1:$P1 && ok "stage-1 service up" || fail "stage-1 service"; fi
if [ -n "$S2" ]; then docker build -t $TAG-2 "$S2" >/tmp/$TAG-2.log 2>&1 && docker run -d --name $TAG-2 -e PORT=$P2 -p $P2:$P2 $TAG-2 >/dev/null && wait_health $P2 >/dev/null && export STAGE2_URL=http://127.0.0.1:$P2 && ok "stage-2 service up" || fail "stage-2 service"; fi
python3 "$HERE/run_checks.py" http://127.0.0.1:$P3 --include-previous -q --json /tmp/$TAG.api.json || rc=1
unset STAGE1_URL STAGE2_URL
"$PY" "$HERE/../stage-2/run_ui_checks.py" http://127.0.0.1:$P3 -q --json /tmp/$TAG.ui.json || rc=1
echo "results: /tmp/$TAG.api.json /tmp/$TAG.ui.json"
exit $rc
