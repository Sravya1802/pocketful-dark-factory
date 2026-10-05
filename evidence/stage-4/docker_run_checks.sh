#!/usr/bin/env bash
# Build + run the stage-4 image (twice: a second instance enables the cross-process snapshot check) and optionally the frozen
# stage-1/2/3 images for the import checks, then run the suites.
#   evidence/stage-4/docker_run_checks.sh /abs/stage-4 [/abs/stage-1 [/abs/stage-2 [/abs/stage-3]]]
# Tip: export DOCKER_BUILDKIT=0 DOCKER_CONFIG=$(mktemp -d) first if BuildKit hangs on the credential helper.
set -u
S4="${1:?usage: $0 /abs/stage-4 [/abs/stage-1 [/abs/stage-2 [/abs/stage-3]]]}"; S1="${2:-}"; S2="${3:-}"; S3="${4:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-/Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/.venv/bin/python}"
P4="${P4:-18084}"; PA="${PA:-18085}"; P1="${P1:-18081}"; P2="${P2:-18082}"; P3="${P3:-18083}"; TAG="pocketful-s4-check-$$"
rc=0; fail() { echo "FAIL $*"; rc=1; }; ok() { echo "ok   $*"; }
trap 'docker rm -f $TAG-4 $TAG-a $TAG-1 $TAG-2 $TAG-3 $TAG-n >/dev/null 2>&1; docker rmi -f $TAG-4 $TAG-1 $TAG-2 $TAG-3 >/dev/null 2>&1' EXIT
[ -f "$S4/Dockerfile" ] && ok "Dockerfile present" || fail "Dockerfile missing"
[ -s "$S4/RUN.md" ] && ok "RUN.md present" || fail "RUN.md missing"
docker build -t $TAG-4 "$S4" >/tmp/$TAG-4.log 2>&1 && ok "stage-4 image builds" || { fail "stage-4 build (see /tmp/$TAG-4.log)"; exit 1; }
wait_health() { local t0=$(date +%s); while :; do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:$1/health)" = 200 ] && { echo $(( $(date +%s) - t0 )); return 0; }; [ $(( $(date +%s) - t0 )) -ge 60 ] && return 1; sleep 0.25; done; }
docker run -d --name $TAG-4 --cpus 2 --memory 2g -e PORT=$P4 -p $P4:$P4 $TAG-4 >/dev/null
s=$(wait_health $P4) && ok "healthy ${s}s after start (limit 60 s)" || { fail "not healthy within 60 s"; docker logs $TAG-4 | tail; exit 1; }
docker run -d --name $TAG-a -e PORT=$PA -p $PA:$PA $TAG-4 >/dev/null && wait_health $PA >/dev/null && export ALT_URL=http://127.0.0.1:$PA && ok "second stage-4 instance up (cross-process check)" || fail "second instance"
docker run -d --name $TAG-n --network none --cpus 2 --memory 2g -e PORT=8080 $TAG-4 >/dev/null; sleep 3
[ "$(docker inspect -f '{{.State.Running}}' $TAG-n)" = true ] && ok "container stays up with --network none" || fail "--network none: container exited"
up() { # tag dir port var
  [ -n "$2" ] || return 0
  docker build -t $TAG-$1 "$2" >/tmp/$TAG-$1.log 2>&1 && docker run -d --name $TAG-$1 -e PORT=$3 -p $3:$3 $TAG-$1 >/dev/null && wait_health $3 >/dev/null && export $4=http://127.0.0.1:$3 && ok "stage-$1 service up" || fail "stage-$1 service"
}
up 1 "$S1" $P1 STAGE1_URL; up 2 "$S2" $P2 STAGE2_URL; up 3 "$S3" $P3 STAGE3_URL
python3 "$HERE/run_checks.py" http://127.0.0.1:$P4 --include-previous -q --json /tmp/$TAG.api.json || rc=1
unset STAGE1_URL STAGE2_URL STAGE3_URL ALT_URL
"$PY" "$HERE/../stage-2/run_ui_checks.py" http://127.0.0.1:$P4 -q --json /tmp/$TAG.ui.json || rc=1
echo "results: /tmp/$TAG.api.json /tmp/$TAG.ui.json"
exit $rc
