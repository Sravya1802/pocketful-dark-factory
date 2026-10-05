# Pocketful stage 3 - attack report, round 2 (adversary)

Revision attacked: `c5b8629dc841321fe4653356822b3b7eb9a3f4ec` (`git archive` of `stage-3/`, product code untouched). Previous round: attack-report.md / af638e1 (revision 959960c).
Two containers (A :18310, B :18311, `--cpus 2 --memory 2g`, `DOCKER_BUILDKIT=0` + empty `DOCKER_CONFIG`), one `--network none` container (health 200), frozen stage-1 (621342c) and stage-2 (92bbbf9) services for real exports. New files in `evidence/stage-3/attacks/`: `attack_s3_r2.py` (snapshot-redesign attacks, 106 checks + scale), `scale_pages_r2.py`, outputs `run-r2-*.txt`; the round-1 scripts (`oracle3.py`, `fuzz3.py`, `attack_s3.py`, `snapshot_growth.py`, `scale_snapshots.py`, `seeded_expired_hold.py`) were re-run unchanged.

## Verdict on the round-1 findings
| Finding | Status | Evidence |
|---|---|---|
| S3-1 snapshot memory O(history) per snapshot after a write, heap OOM | **Fixed** | `snapshot_growth.py` (10 000 payments, 200 cycles): none 0.00, payment 0.00, rejected-403 0.00, correction 0.05 MiB per snapshot (was 1.33-1.37). `scale_snapshots.py` 30 000 payments / 600 (correction + snapshot) cycles: 85 MiB at the end, oldest snapshot still pages (was a crash at ~400 cycles). 1 500 (write + snapshot) cycles over 30 000 payments: 303 -> 306 MiB. 300 snapshots over a 100 000-payment history: 317 MiB. |
| S3-2 seeded `expired` hold with future `expires_at` in as_of views | **Fixed** | `seeded_expired_hold.py`: plain `/me`, `as_of=now-10µs` and `as_of=now+1h` all show held 0 / available 10000. |
| S3-3 lowercase `t`/`z` and `23:59:60Z` rejected | **Fixed** | 50 combinations accepted on `/me?as_of`, `/me?known_at`, `/statement?from|to|known_at` and correction `effective_at`, echoed exactly; leap second equals the first instant of the next minute (as_of `23:59:60Z` == `00:00:00Z` next day: includes a payment at 00:00:00, excludes one at 00:00:00.000001, `23:59:59.999999` excludes both); seconds 61, minute 60, hour 24, naive and `+24:00` variants still 422. |

## New finding

### R2-1 - Paging an old snapshot recomputes the view, so latency grows linearly with history and breaches 5 s under concurrency at large histories - Low
- Spec: §2 per-request timeout 5 s at up to 50 in flight; "Statement/snapshot memory must stay bounded" (the memory fix trades this for CPU).
- Expected: page latency independent of history size, or bounded well inside 5 s for the history sizes a harness can create.
- Actual: sequential page of an old snapshot: 39 ms average at 150 000 payments, 64 ms at 250 000. With 50 concurrent pages of distinct old snapshots (each a different `known_at`, so the one-per-user cache misses) the single-threaded server queues them: 30 000 payments max 2.1 s, **100 000 max 4.3 s, 150 000 max 8.9 s, 250 000 max 18.7 s** (all 200, no errors, no crash, memory flat).
- Repro: `python3 attacks/scale_pages_r2.py http://HOST:PORT 150000 100 50 <container>` (exit 1; output `run-r2-scale-pages.txt`); 100 000 payments is within the limit (`run-r2-scale-docker.txt`, max 4.27 s).
- Exposure: needs a ~100 000+ payment history plus 50 simultaneous snapshot readers; not realistic for stage-3 tests ("thousands of payments"), reported as the measured ceiling.

## Observations
- A rejected correction between reads no longer costs memory; a successful correction costs ~0.05 MiB per snapshot at 10 000 payments (the new revision itself).
- Snapshot tokens are cleared by `/_test/import` (404) and `/_test/reset`, as the builder describes; tokens are not valid on another process.
- Container clock vs host skew (1-2 ms) still applies to host-clock oracles; the oracle fuzz tolerated it (0 mismatches).
- Stage-1/2 observations unchanged (S2-1 silent failed refresh, S2-2 Accept q-values).

## What passed (regression hunt on the snapshot redesign)
### Frozenness (`attack_s3_r2.py` group 1, 106 checks with groups 2-3)
12 snapshots over every shape (default, other user, window before/after/middle, `known_at` in the past, `known_at` in the future, `to` in the future, empty window, all-future window, `known_at` equal to a revision's `recorded_at`, a third user) each re-paged at page sizes 3, 7 and 200 after every kind of later write; **every page byte-identical to the first reading** after: a payment, a private payment, a correction moving a payment an hour earlier (out of windows), a correction inside the window, a zero-amount correction, a correction whose `effective_at` equals another payment's instant, a nonfinal and a final capture, authorize + void, a settlement, a request payment, a correction of the request-paying payment, a hold expiring by the clock, and 8 threads of concurrent payments + corrections (6 re-reads of every snapshot during the storm and one after). The first response equals page 0 of its own snapshot (entries, balances, `has_more`) for all 12; opening + sum(delta) == closing and the running balance hold; the future-`known_at` snapshot did not start including later writes; empty windows stayed empty; a fresh read after the writes differs (the snapshot is not just a live view).
### Clock edges (group 2)
30 sequential corrections: `recorded_at` strictly increasing; `known_at` = revision N's `recorded_at` selects revision N, `-1 µs` selects N-1, `+1 µs` selects N (9 cases), and those snapshots are unchanged after 10 more corrections; 400 concurrent payments: all `created_at` distinct; 50 concurrent corrections on distinct payments: all `recorded_at` distinct; 120 first reads under 8 concurrent writers: each first response equals page 0 of its snapshot and the whole paged chain is internally consistent (no drift between the read instant and the later recomputation).
### Scale (group 4, `run-r2-scale-docker.txt`)
30 000 seeded payments: reset 0.3 s; 1 500 (write + snapshot) cycles without a crash, first reads max 0.03 s, corrections max 0.03 s; 200 random old snapshots paged sequentially max 23 ms; 400 concurrent old-snapshot pages @50 all 200 (max 2.1 s); an old snapshot byte-identical after 50 more payments/corrections; deep offsets 29 999, 30 000, 100 000, 10^9: 200 with empty tail and `has_more` false past the end; 100 concurrent first reads max < 5 s; export 14.9 MB in 0.1 s and import into another process with equal statement views. 100 000 payments: reset < 10 s, 300 write+snapshot cycles avg 17 ms, memory 317 MiB, 200 concurrent old pages all 200 (max 4.27 s).
### Full re-runs on c5b8629
- Oracle fuzz (`fuzz3.py`, my independent model): **93 574 comparisons, 0 failures** (16 seeds; 486 accepted corrections, 52 `historical_overdraft`, 67 `insufficient_funds`, all predicted).
- Targeted suite (`attack_s3.py`): **503 passed, 0 failed** (round 1: 18 failed on S3-3).
- Stage-2 API suite 227/227; stage-2 UI suite 336/341 (the same 5 known low findings, nothing new); stage-1 suite 449/449.
