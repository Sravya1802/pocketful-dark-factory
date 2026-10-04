# Pocketful stage 1 - gate verdict, round 3

**VERDICT: ACCEPT revision `621342c6d1d5651e863b24e8124601552874b4d7`**

Tree clean; `git diff 621342c HEAD -- stage-1` is empty (later commits touch evidence only).
Adversary report considered: `evidence/stage-1/attack-report-r3.md`, commit `ef71b5a72006e4a293e9685a018bc3991f998055`, attacking `621342c...` (same revision).
Earlier rounds: verdict.md (9d0c224, REJECT df8f825), verdict-r2.md (966aa3c, REJECT 79b5f42, finding H1).

## What I ran myself
| Check | Result |
|---|---|
| Shipped harness `--mode isolated --stage 1`, out dir `/Users/lakshmisravyarachakonda/darkfactory/band-work/checks/s1-gate-r3-1791144141` | stage 1 pass, "claimed stage: 1" |
| Analyst `docker_run_checks.sh` (build, 2 CPU/2 GiB, healthy 0 s, `--network none`, DOC-01..05, 192 in-container checks, cross-container export->import) | 192/192 passed, all DOC ok |
| H1 (own export refused above 32 MiB), 2 CPU/2 GiB container | FIXED. 28000 payments -> 31.0 MB export, 40000 -> 44.3 MB export: import 204 in 0.3 s / 0.6 s, re-export byte-identical, same-key replay 200, same key different body 409, login and old token valid, /health max 0.44 s during import |
| Heavy control calls | 6 concurrent 44 MB imports all 204 (max health 0.43 s, container peak ~618 MiB, no OOM); stalled 20 MB control upload does not block small reset (204 in 5 ms), health or export; 50 x 42 MB API bodies all 413, alive |
| Earlier fixes (G1 large-body crash, G2 loop stall, G3 long key 422) | still fixed |
| Adversary R3-1 reproduced | yes (below) |
| Code review of diff 79b5f42..621342c | native JSON.parse with source-text reviver keeps numbers exact; single control lane, one large body in transfer, streamed export; idempotency fingerprints are sha256 of canonical body; seeded users get scrypt N=2048 with per-user salt; nothing shaped around tests |

## Adversary findings and how I treated them
- H1/R2-1: fixed (also confirmed by adversary: 36 MB, 68 MB, 137 MB, 240 MB exports import byte-identically; 400k payments in 2.7 s).
- R3-1 (a client that declares 200 MB to a control endpoint, sends >16 MiB and goes quiet holds the single large-body slot, so another >16 MiB control upload waits until it closes): **reproduced** by me (a 20 MB import queued behind such a stall timed out at 20 s; after the stalled socket closed it returned 204 in 0.08 s). Not a requirement failure: stage 1 states no rule for stalled clients, the stalled upload does not affect /health, API calls, logins, small reset/export, and the harness does not stall uploads. Recorded as a robustness defect for later (add an idle/body timeout on control uploads).
- R3-2 (1,000,000-payment state: fresh-process import 15.3 s, self-import OOM): not reproduced by me (needs ~1M writes); accepted as measured. 700k payments import in 7.2 s per the adversary, far beyond what the 5 s API timeout and the harness can build. Outside the spec's stated ranges.
- No regressions per the adversary's 449 + 54 + 62 checks in container, and none in mine (old-format import, replays after import with digests, races after import, atomic exports under writes, 413 paths, tampered imports 422).

## Matrix and spec
The 58 matrix rows not covered by the shipped checks are covered by the analyst checks (all pass) plus the adversary suites; the 7 not-testable rows hold. No requirement in the spec is unmet that I could find.

## Residual risks (non-blocking, for the next stage)
1. R3-1 stalled control upload (above).
2. Seeded users hashed at scrypt N=2048 (signups N=16384): within §6, reset speed tradeoff.
3. States of ~1M payments exceed the 10 s control limit on import.
