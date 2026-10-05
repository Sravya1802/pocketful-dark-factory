# Pocketful stage 3 - gate verdict, round 2

**VERDICT: ACCEPT revision `c5b8629dc841321fe4653356822b3b7eb9a3f4ec`**

Tree clean; `git diff c5b8629 HEAD -- stage-3` is empty (later commits touch evidence only).
Adversary report considered: `evidence/stage-3/attack-report-r2.md`, commit `6f62bfe` (author adversary), attacking `c5b8629...` (same revision).
Earlier: `verdict.md` (79ff565, REJECT 959960c: K1-K3), adversary round 1 `attack-report.md` (af638e1).

## What I ran myself
| Check | Result |
|---|---|
| Shipped harness `--stage 3 --mode isolated`, out dir `/Users/lakshmisravyarachakonda/darkfactory/band-work/checks/s3-gate-r2-1791175194` | stage 3 pass, "claimed stage: 3" |
| Analyst `docker_run_checks.sh` (stage-3 image, healthy 0 s, `--network none`, frozen stage-1 and stage-2 services, stage-2 UI) | API 388/388 (incl. `--include-previous` and real stage-1/2 export imports), UI 99/99 |
| K1 snapshot memory, 2 CPU / 2 GiB container | FIXED: 200 write+snapshot cycles on a 10,000-payment history cost 0.01 MiB (payment) / 0.06 MiB (correction) per snapshot (was 1.38 / 1.35); 30,000 payments / 600 corrections: 95 MiB, all snapshots alive, oldest still pages |
| K2 seeded `expired` hold with future `expires_at` | FIXED: `as_of=now-10us` and `now+1h` show held 0, equal to plain `/me` |
| K3 lowercase `t`/`z`, `23:59:60Z` | FIXED: accepted and echoed exactly; seconds 61 and minute 60 still 422 |
| Snapshot frozenness (own test): 18 snapshots per run (future `known_at`, empty, before/after windows, deep offset) re-paged after 200 mixed writes (payments, corrections incl. zero-amount, settlements, request payments, capture/void) and 600 concurrent writes | every page byte-identical, no errors |
| Own independent oracle fuzz (3 more seeds on c5b8629, about 4,700 comparisons) and own hold-history probes | no genuine mismatches (the only flags were my script's `expected_revision` 0 cases, correctly 422); holds correct |
| Adversary R2-1 reproduced | yes, as a scaling trend (below) |
| Code review of the snapshot redesign (snapshot = user, read instant K, window, echoed text; view recomputed per page; one cached historical view per user) | sound: revisions are append-only and recorded strictly after K on a strictly increasing microsecond clock, so the same K always selects the same revisions; no code shaped around tests found |

## Adversary R2-1 (Low) - reproduced, not blocking
Paging an old snapshot recomputes the view, so latency grows with history. I reproduced the trend: at 60,000 payments, 300 old-snapshot pages at 50 concurrency all returned 200 with max 2.84 s (avg 0.43 s), sequential page 12 ms. The adversary measured max 4.3 s at 100,000 payments and 8.9 s at 150,000 (single-threaded queueing, no errors, memory flat). This needs 100,000+ payments plus 50 simultaneous readers on distinct old snapshots, far beyond what a stage-3 test can build, and it is the intended trade for bounded memory (K1). Follow-up if scale matters: cache more than one historical view per user, or build views incrementally.

## Adversary result summary
K1, K2 and K3 verified fixed by me and by the adversary. Adversary regression hunt: 106 snapshot-redesign checks (frozen after every kind of write, clock edges), oracle fuzz 93,574 comparisons with 0 failures, targeted suite 503/503, stage-2 API 227/227, stage-1 449/449, stage-2 UI 336/341 (the same 5 known low S2 findings). I found nothing contradicting these.

## Follow-ups (non-blocking)
1. R2-1 above.
2. Carried from stage 2: S2-1 silent failed refresh, S2-2 Accept q-values.
