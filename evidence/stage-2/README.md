# Pocketful stage 2 — evidence (analyst)

Derived from the stage-2 requirements text only. No product code was read.

| File | What |
|---|---|
| `coverage.md` | coverage matrix: one row per requirement sentence → check ids, shipped-harness coverage (from test names), risk notes |
| `ambiguities.md` | every reading choice, strict or lenient |
| `run_checks.py` + `checks/test_*.py` | **API** checks (Python 3 stdlib only) for authorizations, expiry, holds vs available, seven write paths, races, negotiation, import compatibility |
| `run_ui_checks.py` + `checks/uitest_*.py`, `checks/ui_lib.py` | **browser** checks (Playwright + Chromium) |
| `manual-checklist.md` | what a script cannot judge (feel, hierarchy, wording, screen reader) |
| `check_matrix.py` | verifies matrix ↔ check ids both ways, prints the counts |
| `docker_run_checks.sh` | builds stage-2 (and optionally stage-1) images, runs both suites against containers |
| `selftest/` | analyst-written reference service + reference UI + three mutation runners — proof the checks pass on a correct reading and fail on injected defects. **Not product code.** |

## Run — API checks (any Python 3.9+)

```sh
python3 evidence/stage-2/run_checks.py http://127.0.0.1:8080                      # stage-2 API checks (87)
python3 evidence/stage-2/run_checks.py http://127.0.0.1:8080 --include-stage1     # + the 192 accepted stage-1 checks (one skipped: API-01, replaced by ME-01)
STAGE1_URL=http://127.0.0.1:8081 python3 evidence/stage-2/run_checks.py http://127.0.0.1:8080 -k UPG   # stage-1 export -> stage-2 import
```
Flags: `-q` failures only · `--json out.json` · `--ids AZ-20,AC-02` · `-k substring` · `--failfast`. Exit 0 = all passed. ≈ 50 s (short-TTL expiry checks sleep).
`STAGE1_URL` must be a running **stage-1** service (the frozen `stage-1/` image); without it UPG-10..12 are skipped (the rest of UPG still runs).

## Run — UI checks (needs Playwright + a launchable Chromium)

The harness venv has both (verified here: Playwright 1.63, Chromium in `~/Library/Caches/ms-playwright`):

```sh
/Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/.venv/bin/python \
    evidence/stage-2/run_ui_checks.py http://127.0.0.1:8080
```
Flags: `-q` · `--json out.json` · `--ids UI-40,UI-52` · `-k Uncertain` · `--headed` · `--failfast`. 99 browser tests (some run at 375, 768 and 1280 px). ≈ 75 s when everything passes
(failures wait up to 6 s each). Without Playwright: `pip install playwright && playwright install chromium`, or do `manual-checklist.md` by hand.

Both runners RESET the service (`POST /_test/reset`): use a throw-away instance, one runner at a time.

## Run — containers

```sh
export DOCKER_BUILDKIT=0 DOCKER_CONFIG=$(mktemp -d)       # avoids the credential-helper hang on this host
PY=/Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/.venv/bin/python \
  evidence/stage-2/docker_run_checks.sh /abs/path/result/stage-2 [/abs/path/result/stage-1]
```

## Self-test (analyst's reference reading)
```sh
cd evidence/stage-2/selftest && PORT=8099 python3 refimpl2.py &          # reference service + reference UI
python3 evidence/stage-2/run_checks.py http://127.0.0.1:8099 --include-stage1
$PY evidence/stage-2/run_ui_checks.py http://127.0.0.1:8099
python3 evidence/stage-2/selftest/mutate_api.py        # 15 API defects, all must be KILLED
$PY    evidence/stage-2/selftest/mutate_ui.py          # 14 UI defects, all must be KILLED
```
