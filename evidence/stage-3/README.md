# Pocketful stage 3 — evidence (analyst)

Derived from the stage-3 requirements text only. No product code was read.

| File | What |
|---|---|
| `coverage.md` | coverage matrix: one row per requirement sentence → check ids, shipped-harness coverage (from test names), risk notes |
| `ambiguities.md` | every reading choice (35), strict or lenient |
| `run_checks.py` + `checks/test_*.py` | **API** checks (Python 3.9+, stdlib only): timestamps, `/me` as_of/known_at, statements, snapshots, corrections, revisions, linked payments, historical holds, model fuzz, imports, concurrency |
| `checks/oracle.py` | independent model of the temporal ledger (used by the fuzz/model checks; written from the requirements) |
| `docker_run_checks.sh` | builds stage-3 (and optionally the frozen stage-1 / stage-2 images), runs everything against containers |
| `check_matrix.py` | verifies matrix ↔ check ids, prints the counts |
| `selftest/` | analyst-written reference service (`refimpl3.py`, wraps `refimpl2.py`) + `mutate_s3.py` mutation runner. **Not product code.** |

## Run

```sh
python3 evidence/stage-3/run_checks.py http://127.0.0.1:8080                      # stage-3 API checks (110; 3 import checks need STAGE1_URL / STAGE2_URL, else skipped)
python3 evidence/stage-3/run_checks.py http://127.0.0.1:8080 --include-previous   # + accepted stage-1 (192) and stage-2 (87) API checks
STAGE1_URL=http://127.0.0.1:8081 STAGE2_URL=http://127.0.0.1:8082 \
  python3 evidence/stage-3/run_checks.py http://127.0.0.1:8080 --include-previous -q   # + imports from real stage-1 / stage-2 services
```
Flags: `-q` failures only · `--json out.json` · `--ids CR-10,SN-08` · `-k Corrections` · `--failfast`. Exit 0 = all passed. ≈ 25 s for stage-3 alone, ≈ 85 s with `--include-previous`.
Reset-based: use a throw-away instance, one runner at a time. `STAGE1_URL`/`STAGE2_URL` must be running instances of the frozen stage-1 / stage-2 images
(`/Users/lakshmisravyarachakonda/darkfactory/band-work/result/stage-1`, `.../stage-2`).

The stage-2 **browser** suite must keep passing against stage 3 (no new UI is specified):
```sh
/Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/.venv/bin/python evidence/stage-2/run_ui_checks.py http://127.0.0.1:8080
```

## Containers
```sh
export DOCKER_BUILDKIT=0 DOCKER_CONFIG=$(mktemp -d)
evidence/stage-3/docker_run_checks.sh /abs/result/stage-3 /abs/result/stage-1 /abs/result/stage-2
```

## Self-test
```sh
cd evidence/stage-3/selftest && PORT=8099 python3 refimpl3.py &
python3 evidence/stage-3/run_checks.py http://127.0.0.1:8099 --include-previous
python3 evidence/stage-3/selftest/mutate_s3.py        # 30 injected defects, all must be KILLED (~12 min)
```
