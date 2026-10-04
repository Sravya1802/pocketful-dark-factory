#!/usr/bin/env bash
# Deliverable + deployment checks (DOC-01..05) and the full HTTP suite against the container.
#   evidence/stage-1/docker_run_checks.sh /abs/path/to/product-dir     (the dir holding Dockerfile and RUN.md)
# Needs docker. Builds the image, runs it with 2 CPU / 2 GiB, measures time-to-healthy, runs run_checks.py,
# then repeats export -> import across two fresh containers. Exit 0 only if everything passes.
set -u
PRODUCT="${1:?usage: $0 /abs/path/to/product-dir}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TAG="pocketful-s1-check-$$"
PORT_A="${PORT_A:-18081}"; PORT_B="${PORT_B:-18082}"
rc=0
fail() { echo "FAIL $*"; rc=1; }
ok() { echo "ok   $*"; }
cleanup() { docker rm -f "$TAG-a" "$TAG-b" "$TAG-d" >/dev/null 2>&1; docker rmi -f "$TAG" >/dev/null 2>&1; }
trap cleanup EXIT

[ -f "$PRODUCT/Dockerfile" ] && ok "DOC-01 Dockerfile present" || fail "DOC-01 Dockerfile missing"
[ -s "$PRODUCT/RUN.md" ] && ok "DOC-01 RUN.md present" || fail "DOC-01 RUN.md missing or empty"
grep -Eq 'docker +(build|run)|docker-compose|docker compose' "$PRODUCT/RUN.md" 2>/dev/null && ok "DOC-01 RUN.md contains a build/start command" || fail "DOC-01 RUN.md has no docker build/run command"

docker build -t "$TAG" "$PRODUCT" >/tmp/$TAG.build.log 2>&1 && ok "DOC-02 image builds" || { fail "DOC-02 docker build failed (see /tmp/$TAG.build.log)"; exit 1; }

wait_health() { # port -> prints seconds to first 200, or fails after 60
  local port=$1 t0=$(date +%s) 
  while :; do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$port/health")" = 200 ]; then echo $(( $(date +%s) - t0 )); return 0; fi
    [ $(( $(date +%s) - t0 )) -ge 60 ] && return 1
    sleep 0.25
  done
}

docker run -d --name "$TAG-a" --cpus 2 --memory 2g -e PORT=$PORT_A -p $PORT_A:$PORT_A "$TAG" >/dev/null
if secs=$(wait_health $PORT_A); then ok "DOC-02 healthy ${secs}s after start (limit 60 s) on -e PORT=$PORT_A"; else fail "DOC-02 not healthy within 60 s"; docker logs "$TAG-a" | tail -20; exit 1; fi
curl -s "http://127.0.0.1:$PORT_A/health" | grep -Eq '"status" *: *"ok"' && ok "DOC-02 health body" || fail "DOC-02 health body"

# DOC-03: no outbound network at run time. Best effort: a container with --network none must still come up and report healthy from inside.
docker run -d --name "$TAG-d" --network none --cpus 2 --memory 2g -e PORT=8080 "$TAG" >/dev/null
sleep 3
st=$(docker inspect -f '{{.State.Running}}' "$TAG-d" 2>/dev/null)
if [ "$st" = true ]; then
  inside=$(docker exec "$TAG-d" sh -c 'for c in "curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8080/health" "wget -q -O /dev/null http://127.0.0.1:8080/health && echo 200" "python3 -c \"import urllib.request;print(urllib.request.urlopen(\\\"http://127.0.0.1:8080/health\\\").status)\""; do out=$(sh -c "$c" 2>/dev/null) && [ -n "$out" ] && echo "$out" && exit 0; done; echo none' 2>/dev/null)
  case "$inside" in 200) ok "DOC-03 serves /health with --network none (default port 8080, no PORT given: DOC-04)";; none) echo "skip DOC-03/04: no HTTP client inside image to probe; verify manually";; *) fail "DOC-03/04 --network none container not healthy: $inside";; esac
else
  fail "DOC-03 container does not stay up with --network none"; docker logs "$TAG-d" | tail -20
fi

# the whole HTTP suite
python3 "$HERE/run_checks.py" "http://127.0.0.1:$PORT_A" -q --json "${JSON_OUT:-/tmp/$TAG.results.json}" || rc=1

# DOC-05: export from container A, import into a brand-new container B on another port
docker run -d --name "$TAG-b" --cpus 2 --memory 2g -e PORT=$PORT_B -p $PORT_B:$PORT_B "$TAG" >/dev/null
wait_health $PORT_B >/dev/null || { fail "DOC-05 second container not healthy"; exit 1; }
python3 - "$PORT_A" "$PORT_B" <<'PY' && ok "DOC-05 export from one container imports into a fresh container (accounts, tokens, balances, replays)" || fail "DOC-05 cross-container export/import"
import json, sys, urllib.request, uuid
a, b = sys.argv[1:3]
def call(port, method, path, body=None, tok=None, key=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    if key: h["Idempotency-Key"] = key
    r = urllib.request.Request("http://127.0.0.1:%s%s" % (port, path), method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as x: t = x.read().decode(); return x.status, json.loads(t) if t else None
    except urllib.error.HTTPError as e: t = e.read().decode(); return e.code, json.loads(t) if t else None
fx = {"currency": "EUR", "minor_units": 2, "users": [
  {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 10000},
  {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 2500}],
  "payments": [], "requests": [], "settlement_operator_ids": ["u_ada"]}
assert call(a, "POST", "/_test/reset", fx)[0] == 204
tok = call(a, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[1]["token"]
key = uuid.uuid4().hex
s, p = call(a, "POST", "/payments", {"to_handle": "bob", "amount": 1234, "note": "x"}, tok, key); assert s == 201, (s, p)
s2, st = call(a, "POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 5}]}, tok, key + "s"); assert s2 == 201, (s2, st)
s, e = call(a, "GET", "/_test/export"); assert s == 200
assert call(b, "POST", "/_test/import", e)[0] == 204
assert call(b, "GET", "/me", tok=tok)[1]["balance"] == 10000 - 1234 - 5            # token + balance survive
s, r = call(b, "POST", "/payments", {"to_handle": "bob", "amount": 1234, "note": "x"}, tok, key); assert (s, r) == (200, p), (s, r)   # retry replays
s, r = call(b, "POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 5}]}, tok, key + "s"); assert (s, r) == (200, st)
assert call(b, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[0] == 200
assert call(b, "GET", "/me", tok=tok)[1]["balance"] == 10000 - 1234 - 5
PY
exit $rc
