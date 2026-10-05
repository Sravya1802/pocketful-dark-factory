# Pocketful stage 4 - gate verdict (final; supersedes the provisional `verdict.md`, f89a76e)

**VERDICT: ACCEPT revision `7cd430af817ba0c7d4422470d693f108e2e1ad80`**

Tree clean; `git diff 7cd430a HEAD -- stage-4` is empty (later commits touch evidence only).
Adversary report considered: `evidence/stage-4/attack-report.md`, commit `202894b883ee4c20bc178b8b318c7e97bfa513f8` (author adversary), attacking `7cd430a...` (same revision).

## What I ran myself (unchanged from the provisional verdict)
- Shipped harness `--stage 4 --mode isolated`, out dir `/Users/lakshmisravyarachakonda/darkfactory/band-work/checks/s4-gate-1791181642`: stage 4 pass, "claimed stage: 4".
- Analyst `docker_run_checks.sh`: API 445/446, stage-2 UI 99/99, all container checks ok. The one failure is IM4-30 (below).
- Own independent oracle fuzz of refunds, batches, settlements and single corrections (6 seeds, about 6,000 comparisons): 0 genuine mismatches, all outcome codes exercised.
- 30 own probes (refund cap under 50 concurrent refunds, same-key replays, refund of refund/by sender/capture/request payment, immutability of captures and refunds, correction below refunded, batch auth ordering, settlement completeness and instant equality incl. 1 us and offset spellings, shared `recorded_at` and `correction_batch_id`, combined-effect affordability, 30 racing corrections with one winner), snapshots across import into another process (byte-identical, no leaks from later writes, tampered balance and snapshot -> 422 unchanged), scale at 40,000 payments (32-item batch 0.01 s, 50 concurrent corrections max 0.22 s): all pass.
- Spec-vs-code review of refunds, batches, `applyRevisions` and snapshot export/import: matches the spec and the disclosed choices; no code shaped around the tests.

## IM4-30
Independently confirmed impossible: the frozen stage-3 export has no snapshot data (I minted a stage-3 token and it is absent from the export text, which has no `snapshots` key; adversary reached the same result). A stage-3 token after import into stage 4 is 404, never 5xx. Stage-4 snapshots survive import (my test and the adversary's six-shape round trip). Not grounds for rejection.

## Adversary findings and treatment
- Oracle fuzz 59,610 comparisons, 0 failures; targeted checks 204/204 (+18/18 in container); stage-1 449/449, stage-2 API 227/227, stage-3 suite 503/503, stage-2 UI 336/341 (the same 5 known low findings). I found nothing contradicting these.
- **S4-1 (Low) reproduced:** an export hand-edited so a snapshot has `known_at_us` `2099-01-01T00:00:00.000000+00:00` imports with 204, after which every new payment/revision is stamped `2099-01-01T00:00:00.00000x` and `/_test/reset` does not restore the clock (only a restart). Reproduced with `tampered_snapshot_clock.py`. Not blocking: it needs a hand-edited export (the service's own exports cannot contain it; garbage values, unknown users and bad tokens in a snapshot record are correctly 422), and the stage-4 requirement is that the clock never issues an instant at or before an imported one. Follow-up: reject snapshot (and payment/revision) instants later than now on import, or do not advance the clock past real time.
- **S4-2 (Low):** old-snapshot paging latency grows with history size; same trend I reproduced and accepted at stage 3 (R2-1). Fine at 30,000-40,000 payments (my run: 0.01 s); the adversary measured 12.2 s only with 150,000 payments and 300 concurrent pages of distinct old snapshots. Follow-up: cache more than one historical view per user.

## Follow-ups (non-blocking)
1. S4-1 import validation of future instants.
2. S4-2 more than one cached historical view per user.
3. Carried from stage 2: S2-1 silent failed refresh, S2-2 Accept q-values.
