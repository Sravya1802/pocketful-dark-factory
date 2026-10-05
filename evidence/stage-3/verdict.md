# Pocketful stage 3 - gate verdict

**VERDICT: REJECT revision `959960ce6bbf1afbc66caf6c59cc7ffeeaa89aaf`**

Tree clean; `git diff 959960c HEAD -- stage-3` is empty (later commits touch evidence only).
Adversary report considered: `evidence/stage-3/attack-report.md`, commit `af638e1` (author adversary; relayed to me by the coordinator because the adversary's turn ended), attacking `959960c...` (same revision).

## What I ran myself
| Check | Result |
|---|---|
| Shipped harness `--stage 3 --mode isolated`, out dir `/Users/lakshmisravyarachakonda/darkfactory/band-work/checks/s3-gate-1791168774` | stage 3 pass, "claimed stage: 3" |
| Analyst `docker_run_checks.sh` (stage-3 image, healthy 0 s, `--network none`, frozen stage-1 and stage-2 services for import checks, stage-2 UI suite) | UI 99/99; API 387/388: BT-09 failed in the container, see note |
| Own independent oracle fuzz (5 seeds, random payments, corrections, `/me` as_of x known_at, `/statement` windows, tie order, paging via snapshot, sums across views) | about 8000 comparisons, no genuine mismatches; all four correction outcomes exercised |
| Own historical-hold probes (create, nonfinal and final capture, void, expiry, known_at before creation, future as_of, capture correction, precedence) | all as specified |
| Adversary S3-1, S3-2, S3-3 reproduced | all three, see below |
| Spec-vs-code review (`ledger.js`, `time.js`, correction and statement handlers) | correction order, historical overdraft check, selection rule and half-open windows match the spec; no code shaped around tests found |

Note on BT-09 (analyst check "old known_at keeps answering after many corrections"): it failed intermittently only in the Docker VM (twice in several runs; passes on bare node 3/3 and in the container when rerun alone). The Docker Desktop VM clock runs about 1.5 ms behind the host, so server timestamps precede the client's own timestamps; on the host, server `created_at` falls inside the client's before/after window. Environmental artifact of the check, not a service defect (the adversary saw the same skew).

## Findings (must be fixed)

### K1 - Statement snapshots retain O(history) memory each; the service can be driven to a heap OOM (adversary S3-1) - reproduced
- Requirement: stage-1 §2 (2 GiB, no crash); stage-3 "Tokens last until reset", and the engineering stance that statement/snapshot memory stays bounded.
- Expected: a snapshot costs little (a frozen view is just a point in an append-only history), so memory stays bounded by history size, not history size times snapshots.
- Actual: with a 10,000-payment history, read-only repetition is flat (0.00 MiB per snapshot), but after any write between reads (a payment or a successful correction) every new snapshot adds about 1.35-1.38 MiB: 200 write+statement cycles took the container from 42 MiB to 318 MiB (payment) and 312 MiB (correction). The adversary showed 30,000 payments reaching a JS heap OOM (exit 139, state lost) after about 400 cycles. Cause: `userLedger` rebuilds and caches a fresh copy of the user's whole ledger after each write, and each snapshot pins that copy.
- Reproduce: `python3 evidence/stage-3/attacks/snapshot_growth.py http://HOST:PORT 10000 200 <container-name>` (prints MiB per snapshot per mode, exits 1); `scale_snapshots.py http://HOST:PORT 30000 600 <container>` for the crash.
- Suggested: revisions are append-only with strictly increasing `recorded_at`, so a snapshot only needs `(user, K = read time, window, default to)`: keep the per-snapshot record O(1) and recompute or share the view, or share one ledger structure between snapshots instead of copying it per write.

### K2 - Seeded `expired` hold with a future `expires_at` is live in historical views (adversary S3-2) - reproduced
- Requirement: stage 3 "Without `as_of`, use the instant the request began"; stage 2: only `open` seeded holds hold funds.
- Expected: `/me?as_of=<now-10us>` and `/me?as_of=<now+1h>` agree with plain `/me` (`held 0, available 10000`).
- Actual: plain `/me` and `as_of=now-1s` show `held 0`; `as_of=now-10us` and `now+1h` show `held 300, available 9700`.
- Reproduce: `python3 evidence/stage-3/attacks/seeded_expired_hold.py http://HOST:PORT` (reset with `{"status":"expired","amount":300,"expires_at":"2099-01-01T00:00:00+00:00"}`).

### K3 - RFC 3339 lowercase `t`/`z` and leap second rejected (adversary S3-3) - reproduced
- Requirement: `as_of`, `known_at`, `from`, `to` and correction `effective_at` are "an RFC 3339 instant with an offset"; the spec lists only naive local time, bare date and empty as invalid. RFC 3339 §5.6 allows lowercase `t`/`z` and seconds `60`.
- Expected: `2026-09-24T13:20:00z`, `2026-09-24t13:20:00Z`, `2026-09-24T23:59:60Z` accepted.
- Actual: all three give 422 on `/me?as_of` (reproduced), and per the adversary on every instant parameter and `effective_at`.
- Reproduce: `curl -H "Authorization: Bearer $T" "$URL/me?as_of=2026-09-24T13:20:00z"` -> 422 (while `...00Z` -> 200).

## Everything else passes
Shipped harness, 387 of 388 analyst checks (the one intermittent failure explained above), stage-2 UI 99/99, stage-1 and stage-2 export imports, correction precedence and atomic preservation, revisions authorization, idempotency, snapshot rules, historical holds, 5xx fuzz and the adversary's 93,758-comparison oracle run. A new revision will be re-run in full.
