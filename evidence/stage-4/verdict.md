# Pocketful stage 4 - gate verdict

**STATUS: PROVISIONAL ACCEPT of revision `7cd430af817ba0c7d4422470d693f108e2e1ad80` - NOT FINAL.**
The gate's own checks all pass and found no blocker. The required adversary report for this revision had not reached the gate when this was written
(no `evidence/stage-4/attack-report.md` and no adversary commit after `f89e4ea` in `git log`). Per the gate's rules a verdict is final only once the
adversary's report for the same revision has been considered and its failures reproduced; this file is therefore a provisional verdict, to be superseded
(a `verdict-final.md`) when that report arrives. If it reports a reproducible failure, the verdict becomes REJECT.

Tree clean; `git diff 7cd430a HEAD -- stage-4` is empty (later commits touch evidence only). Adversary report considered: **none available**.

## What I ran myself
| Check | Result |
|---|---|
| Shipped harness `--stage 4 --mode isolated`, out dir `/Users/lakshmisravyarachakonda/darkfactory/band-work/checks/s4-gate-1791181642` | stage 4 pass, "claimed stage: 4" |
| Analyst `docker_run_checks.sh` (stage-4 image, healthy 1 s, `--network none`, second stage-4 instance, frozen stage-1/2/3 services) | API 445/446; stage-2 UI 99/99; all container checks ok. The one failure is IM4-30 (see below) |
| IM4-30 independently judged | the frozen stage-3 service keeps snapshots in memory only (`stage-3/src/state.js:56`); I minted a stage-3 snapshot (`st3_2a2a0cc0...`), exported, and the token and the word "snapshot" are absent from the export (state keys: currency, minor_units, authorization_ttl_seconds, seq, users, tokens, settlement_operator_ids, payments, requests, splits, settlements, authorizations, idempotency). The row cannot pass in any implementation; the check's earlier steps (corrections, revisions, settlement membership, replays, historical views) pass. Not grounds for rejection. |
| Own independent oracle fuzz for refunds, batches, settlements, single corrections (6 seeds, about 6,000 comparisons): refund cap/403/422/409 outcomes, batch precedence incl. `incomplete_settlement`, instant mismatch, `insufficient_funds` on combined net effect, `historical_overdraft` at every boundary; `/me?as_of&known_at`, `/statement` windows, paging via snapshots, tie order, `refund_of`, sums across historical views | 0 genuine mismatches; every outcome code exercised |
| Own probes (30 checks): 50 concurrent refunds vs the cap (33 succeed, 17 refund_exceeds_payment, balance exact); 50 same-key refunds (one 201); refund of refund / by sender / unknown / string amount / no token / no key; request-payment refund leaves the request paid; capture refund leaves the hold released; captures and refunds uncorrectable; correction below refunded amount; batch 401, non-operator 403 before key/body, 400 key, empty 422; partial settlement 422 `incomplete_settlement`; 1 us difference 422; mixed offset spellings accepted with one `recorded_at` and `correction_batch_id`; replay 200 identical; key reuse 409; reversal restores balances; individually unaffordable items netting to affordable accepted; unaffordable batch 409; 30 racing corrections (batch + single) on one expected revision give exactly one winner | all pass |
| Snapshots across import (A -> B, separate processes): pages byte-identical for a default snapshot and a future-`known_at` snapshot; other user's token 404; 5 payments and a correction on the destination do not leak into the imported snapshots; tampered balance and tampered snapshot imports 422 with state unchanged | all pass |
| Scale: 40,000 payments: reset 0.2 s, a 32-item batch 0.01 s, 50 concurrent corrections max 0.22 s, 50 concurrent old-snapshot pages max 0.01 s | pass |
| Spec-vs-code review of `app.js` (refunds, batches, `applyRevisions`, precedence) and the snapshot export/import in `state.js` | matches the spec and the builder's disclosed choices; no code shaped around the tests found |

## Findings
None from the gate's own work.

## Open
The adversary's stage-4 report. Not considered here; this verdict is not final until it is.
