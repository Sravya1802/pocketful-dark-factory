# Pocketful stage 1 — evidence (analyst)

Everything here was derived from the requirements text only. No product code was read.

| File | What |
|---|---|
| `coverage.md` | coverage matrix: one row per requirement sentence → check ids, shipped-harness coverage, risk notes |
| `ambiguities.md` | every reading choice made, and whether the check is strict or lenient |
| `run_checks.py` + `checks/` | black-box HTTP acceptance checks (Python 3 standard library only) |
| `docker_run_checks.sh` | deliverables + deployment: Dockerfile/RUN.md present, build, 2 CPU/2 GiB, ≤60 s to healthy, `--network none`, full suite in-container, cross-container export→import |
| `check_matrix.py` | verifies matrix ids ↔ check ids and prints the row counts |
| `selftest/` | analyst-written reference service (`refimpl.py`) and `mutate.py` — proof that the checks pass on a correct reading of the spec and fail on 15 injected defects. Not product code. |

## Run

```sh
# against a service already listening (state is RESET by the checks; use a throw-away instance)
python3 evidence/stage-1/run_checks.py http://127.0.0.1:8080
python3 evidence/stage-1/run_checks.py http://127.0.0.1:8080 -q --json /tmp/out.json     # quiet + machine-readable
python3 evidence/stage-1/run_checks.py http://127.0.0.1:8080 --ids PAY-01,IDM-20          # selected checks
python3 evidence/stage-1/run_checks.py http://127.0.0.1:8080 -k settlement                # by substring
BASE_URL=http://host:port python3 evidence/stage-1/run_checks.py                          # env alternative

# container, as the judge would run it
evidence/stage-1/docker_run_checks.sh /abs/path/to/result/stage-1
```

Wall time ≈ 15 s on a fast service (a handful of checks sleep ~1.1 s so that `created_at` ordering can be asserted).
Failure output names the check id (`[IDM-20]`) — look it up in `coverage.md`.
