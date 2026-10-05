# Pocketful stage 4 — evidence (analyst)

Derived from the stage-4 requirements text only. No product code was read.

| File | What |
|---|---|
| `coverage.md` | coverage matrix: one row per requirement sentence → check ids, shipped-harness coverage (from test names), risk notes |
| `ambiguities.md` | 25 reading choices, strict or lenient |
| `run_checks.py` + `checks/test_p..s*.py` | **API** checks (Python 3.9+, stdlib only): refunds, batches, imports/snapshots, model fuzz |
| `checks/oracle.py`, `checks/oracle4.py` | independent oracle (temporal ledger + world model: refunds, settlements, captures, batches) used by the fuzz |
| `docker_run_checks.sh` | builds stage-4 (+ optional frozen stage-1/2/3 images, a second stage-4 instance) and runs everything |
| `check_matrix.py` | verifies matrix ↔ check ids, prints the counts |
| `selftest/` | analyst-written reference service (`refimpl4.py` → `refimpl3.py` → `refimpl2.py`) + `mutate_s4.py` mutation runner. **Not product code.** |

## Run

```sh
python3 evidence/stage-4/run_checks.py http://127.0.0.1:8080                       # stage-4 API checks (59; 4 need the optional URLs below)
python3 evidence/stage-4/run_checks.py http://127.0.0.1:8080 --include-previous    # + accepted stage-1 (192), stage-2 (87), stage-3 (110) API checks
STAGE1_URL=http://127.0.0.1:8081 STAGE2_URL=http://127.0.0.1:8082 STAGE3_URL=http://127.0.0.1:8083 ALT_URL=http://127.0.0.1:8084 \
  python3 evidence/stage-4/run_checks.py http://127.0.0.1:8080 --include-previous -q
```
Flags: `-q` failures only · `--json out.json` · `--ids RF-16,BC-27` · `-k Refunds` · `--failfast`. Exit 0 = all passed. ≈ 15 s for stage 4 alone, ≈ 100 s with `--include-previous`.
Every run RESETS state: use a throw-away instance, one runner at a time.
Optional environment: `STAGE1_URL` / `STAGE2_URL` / `STAGE3_URL` = running instances of the frozen stage-1 / stage-2 / stage-3 images (enable IM4-10 / IM4-20 / IM4-30
and the earlier stages' import checks); `ALT_URL` = a **second** instance of the stage-4 image (enables the cross-process snapshot check IM4-02).

The stage-2 **browser** suite must keep passing (no new UI):
```sh
/Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/.venv/bin/python evidence/stage-2/run_ui_checks.py http://127.0.0.1:8080
```

## Containers
```sh
export DOCKER_BUILDKIT=0 DOCKER_CONFIG=$(mktemp -d)
evidence/stage-4/docker_run_checks.sh /abs/result/stage-4 /abs/result/stage-1 /abs/result/stage-2 /abs/result/stage-3
```

## Self-test
```sh
cd evidence/stage-4/selftest && PORT=8099 python3 refimpl4.py &
python3 evidence/stage-4/run_checks.py http://127.0.0.1:8099 --include-previous
python3 evidence/stage-4/selftest/mutate_s4.py        # 29 injected defects, all must be KILLED (~10 min); optional args: mutant names
```
