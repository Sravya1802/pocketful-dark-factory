# Pocketful stage 4 - attack report (adversary)

Revision attacked: `7cd430af817ba0c7d4422470d693f108e2e1ad80` (`git archive` of `stage-4/`, product code untouched and not read).
Containers (A :18400, B :18401, `--cpus 2 --memory 2g`, `DOCKER_BUILDKIT=0` + empty `DOCKER_CONFIG`), a `--network none` container (health 200), the FROZEN stage-3 service as a container (c5b8629 image, :18402) and the frozen stage-1 / stage-2 services as host node processes for real exports.

**Environment caveat:** the Docker Desktop VM clock was unreliable during this run (measured 4.5 minutes behind the host, later seconds ahead; `resync_docker_clock.sh` resets it but it drifted again). That makes any oracle that compares server timestamps with the host clock meaningless. The oracle fuzz and the targeted functional suites therefore ran against `node server.js` of the same revision on the host (same code, host clock); the container was used for the memory/limits/scale/hashing-under-lock/UI checks.

Scripts and recorded output are in `evidence/stage-4/attacks/`: `oracle4.py` (my own model, extended for refunds, refund caps, immutability, settlement membership and batch precedence), `fuzz4.py` (random sequences vs the oracle), `attack_s4.py` (targeted, groups A refunds, B races, C batches, D snapshots/imports, E scale, F fuzz/limits), `scale_pages_s4.py`, `tampered_snapshot_clock.py`, `resync_docker_clock.sh`, regression copies of the stage-1/2/3 suites, outputs `run-s4-*.txt`.

## Verdict
The refund and batch logic is correct against my independent oracle: **59 610 comparisons, 0 failures** (14 seeds x ~100 random operations, covering every refusal code of both endpoints, mixed offsets, zero amounts, partial settlement sets, hold boundaries) and **204/204 targeted checks** plus 18/18 scale/fuzz checks in the container. Snapshots survive import in a stage-4 round trip, byte-identical, and the builder's IM4-30 claim is confirmed. Findings: one import-validation hole that can push the service clock to the far future permanently, and one scale ceiling carried over from stage 3.

## Findings (reproduced)

### S4-1 - Import accepts a snapshot record with an impossible future instant; the service clock jumps there and stays, even across `/_test/reset` - Medium/Low
- Spec: stage 1 §10 "an invalid state give[s] 422 `validation_failed` without changing the destination"; stage 3: a snapshot freezes the "default `to`" at a read (a read cannot start in the future); correction `effective_at` must be "not later than now".
- Expected: a state whose snapshot has `known_at_us` after any instant the source could have read is invalid (422), or at least must not move the clock.
- Actual: export a state with one snapshot, set `state.snapshots[0].known_at_us` to `"2099-01-01T00:00:00.000000+00:00"` and import it: **204**. The service then stamps every new payment with `created_at: 2099-01-01T00:00:00.00000x+00:00` ("clock never issues an instant at or before an imported one"), new revisions' `recorded_at` likewise, and `/_test/reset` does **not** restore it (the next payment after a reset is still in 2099); only a process restart does. A correction with `effective_at` equal to the real now is then accepted although it is 73 years before server time, so the "not later than now" rule loses its meaning. A `known_at_us` before 1970 and a snapshot with extra fields are also accepted (no visible effect). Garbage values, unknown `user_id`, missing/empty/numeric/duplicate tokens and non-list `snapshots` are all correctly 422 with no change.
- Repro: `python3 evidence/stage-4/attacks/tampered_snapshot_clock.py http://HOST:PORT` (exit 1; output `run-s4-tampered-snapshot-clock.txt`).
- Exposure: needs a hand-edited export; the service's own exports can never contain it.

### S4-2 - Old-snapshot paging latency grows with history size and exceeds 5 s at 50 concurrent pages beyond ~100k payments (stage-3 R2-1, unchanged) - Low
- Spec: §2 5 s per request at 50 in flight.
- Actual: 150 000 seeded payments, 100 snapshots each at a different `known_at`: one page costs ~40 ms sequentially, but 300 pages at 50 concurrency reach a max of **12.2 s** (avg 1.9 s; all 200, no crash, memory flat). Within the limit at 30 000 payments (32-item batches, 300 concurrent old-snapshot pages and 300 concurrent refunds all < 5 s).
- Repro: `python3 attacks/scale_pages_s4.py http://HOST:PORT 150000 100 50 <container>` (`run-s4-scale-pages.txt`).

## IM4-30 (snapshot tokens minted by the frozen stage-3 service surviving import): the builder's impossibility claim is correct
The frozen stage-3 export contains no snapshot data at all. Its state keys are `currency, minor_units, authorization_ttl_seconds, seq, users, tokens, settlement_operator_ids, payments, requests, splits, settlements, authorizations, idempotency` (no `snapshots` key), and a token minted by stage 3 (`st3_...`, taken from its `GET /statement`) does not appear anywhere in the export text. Stage 4's own export adds a `snapshots` array of `{token, user_id, known_at_us, from_us, to_us, known_at}` records, and a stage-3-minted token is 404 after import into stage 4 (never 5xx). Nothing can be restored from a stage-3 export; the requirement can only hold for stage-4 exports, which it does (below).

## Observations
- Refund amount 0 gives 422 `validation_failed` (spec: invalid amount).
- Individually unaffordable correction items that net to zero per wallet are accepted as one batch (combined effect), as specified.
- Stage-1/2/3 observations unchanged (S2-1 silent failed refresh, S2-2 Accept q-values, R2-1 above).

## What passed
### Oracle fuzz (`fuzz4.py`, `run-s4-fuzz-host.txt`: 59 610 comparisons, 0 failures)
Random sequences of payments, request payments, captures (final/nonfinal), authorizations/voids/short-TTL expiry, settlements, single corrections, **correction batches** (plain, whole settlement sets, partial sets, differing member instants, duplicates, stale revisions, zero amounts, mixed offset spellings and fraction digits) and **refunds** (full, partial, over-cap, refund-of-refund, wrong actor, amount 0 and over 10^9, after corrections, of captures/settlement members/request payments). After each step: current `/me`; periodically 12 random `/me?as_of&known_at` and 8 random `/statement` views (windows, tie order, `balance_after`, opening/closing, revisions, effective/recorded times, `payment.amount`, `has_more`, snapshot paging frozen). Refund outcomes (403 / 422 validation / `invalid_refund_target` / `refund_exceeds_payment` / 409 `insufficient_funds` / 201) equal the oracle's prediction in every case and shapes match (`refund_of`, links null, copied note/visibility). Batch outcomes (201 / `linked_payment_immutable` / `stale_revision` / `refund_exceeds_payment` / `incomplete_settlement` / `validation_failed` / `insufficient_funds` / `historical_overdraft`) equal the oracle's precedence; a 201 batch has one shared `recorded_at` strictly later than every member's previous one and correct revision numbers; rejected batches leave every view unchanged; settlements move exactly their net amounts.

### Targeted (`attack_s4.py`, 204 checks, `run-s4-targeted-host.txt`; groups E/F in `run-s4-scale-fuzz-docker.txt`: 18/18)
- **Refunds (A):**
  - Eligibility: direct, request, capture and settlement-member payments are refundable; non-receiver, third party and operator get 403, unknown 404, refund-of-refund 422 `invalid_refund_target` (before the cap check).
  - Amounts (0, -1, 1.5, string, bool, null, [], {}, 10^9+1, 10^30, missing) are 422; `700.0` is accepted; the cumulative cap follows the corrected amount.
  - A correction below the refunded total is 422 `refund_exceeds_payment` (after `stale_revision`); exactly at the floor is accepted; a zero-correction of a fully refunded payment is refused.
  - Refund payments and captures are immutable (422 for the sender, 403 for the receiver).
  - A refund never reopens the request (still paid, same payment_id) or the authorization (open, remaining and held unchanged) and creates no settlement membership (refund `settlement_id` null, settlement replay still the original).
  - A refund is limited to the receiver's available funds: the hold is respected and equal-to-available works.
  - Idempotency: replay 200 identical (also after the cap is exhausted); same key with a different body 409; a claimed key beats validation; a rejected refund does not claim its key; the same key on another payment is a first use. Missing key 400, no token 401, bad JSON 400, 403 before body validation, 404 before 403.
  - Visibility: a public refund is in the third party's feed, a private one is hidden, both parties see both, the third-party statement is empty. `refund_of` is present on every payment object and the sum of totals is preserved.
- **Races (B):**
  - 50 concurrent refunds of 10 all fit; 50 of 30 with 500 of cap left succeed exactly 16 times (cap 980).
  - Refunds racing payments from the same wallet never overdraw.
  - Refund vs correction-down x10 and refund vs batch x8: refunded never exceeds the final corrected amount, sums preserved.
  - 30 concurrent batches sharing one expected revision: exactly one 201, 29 `stale_revision`. Batch vs single correction x10: one wins.
  - 30 identical batches with one key: 1 x 201 + 29 x 200. 30 different bodies with one key: one 201, the rest `idempotency_key_reuse`.
  - 50 identical refunds with one key: one 201 + 49 x 200, money moved once.
- **Batches (C):**
  - Shape, shared `recorded_at`, `correction_batch_id` on revisions (absent on revision 1 and on single corrections), originals and `/activity` unchanged, replay 200 (also after newer revisions), key reuse 409, claimed key before validation.
  - Auth order: 401; 403 for every non-operator including a payment's own sender, before key and body checks; operator without key 400; bad JSON 400.
  - Bounds and validation: 1..32 items (0, 33, duplicates, non-array/object give 422); every item field validated; 404 for unknown payment, 409 stale; captures/refunds 422 `linked_payment_immutable`; refund floor (below refunded 422, equal accepted); item errors in input order.
  - Settlements: a partial set gives `incomplete_settlement`; member instants differing by 1 µs give `validation_failed`; the same instant with different offset spellings is accepted and echoed as sent; any member order works; a second batch on the same settlement works; a settlement retry after correction returns the original body; the statement shows revision 2 with `settlement_id` intact.
  - Request and others' direct payments are correctable; a no-change item creates a revision.
  - Combined affordability: individually unaffordable items that net to zero per wallet are accepted together.
  - Precedence: `incomplete_settlement` and the instants check come before `insufficient_funds`, which comes before `historical_overdraft`. Historical overdraft is caught at a credit-after-debit boundary and at a hold-creation boundary, with atomic rejection and a reusable failed key.
  - An earlier snapshot is byte-identical after a 5-item zero-amount batch; every historical view still sums to the seeded total; batch visibility is atomic at `known_at` ±1 µs.
- **Snapshots and imports (D):**
  - Real stage-1 export: settlement membership retained (members correctable together by batch, partial set refused), payment refundable, settlement replay original.
  - Real stage-2 export: capture refundable; capture correction and batch immutable.
  - Real stage-3 export: correction preserved, settlement members correctable together.
  - Stage-4 round trip into another process: all 6 snapshot shapes (default, other user, past `known_at`, window, future, empty) page byte-identically at 5 page shapes; later payments/corrections/refunds/batches on the destination never leak into imported snapshots; tokens stay user-scoped and unknown tokens 404; new tokens do not collide; imported token + `from` gives 422; export -> import -> export is byte-stable; new payments after import sort after every imported instant.
  - Tampered imports (balance mismatch, null payments, missing users, negative balance, null/object/string snapshots, unknown user, garbage instants, missing/empty/numeric/duplicate token) give 422 with state unchanged; only the S4-1 cases pass.
- **Scale (E, container):** 30 000 seeded payments: 32-item batches avg 22 ms (max 46 ms); 50 concurrent 32-item batches on disjoint payments all 201 in < 5 s; 400 write+snapshot cycles avg 3 ms; 300 concurrent old-snapshot pages @50 and 300 concurrent refunds all fine; export 16.4 MB in 0.1 s and import < 10 s with the imported snapshot identical; container memory 148 MiB.
- **Fuzz and limits (F, container):**
  - 3000 fuzzed refund/batch/snapshot/revision requests @50: zero 5xx and invariants hold; garbage bodies give no 5xx; a body over 256 KiB gets 413; a 32-item batch with 200-character reasons fits the body cap.
  - Refunds stay < 1 s while 150 signups hash at 50 concurrency (no hashing under the lock).
  - The same key string on all ten idempotent write paths is a first use on each (201 x 10), and each replays independently.

### Regression of earlier stages on stage 4
Stage-1 suite 449/449; stage-2 API suite 227/227; stage-3 targeted suite 503/503 (`run-s3suite-on-s4.txt`); stage-2 UI suite 336/341 (the same 5 known low findings S2-1/S2-2, nothing new).
