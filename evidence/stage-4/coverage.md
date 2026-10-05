# Pocketful stage 4 — coverage matrix

Source: the stage-4 requirements handed over by @coordinator (single part), on top of stages 1–3 (all continue to apply, incl. the stage-2 UI).
One row per requirement sentence. Check ids are the `[ID]` tags in the first line of each check's docstring:
`RF` refunds · `BC` batches · `IM4` imports · `FZ4` model fuzz (all API checks, `run_checks.py`, Python 3 stdlib). `check_matrix.py` verifies ids both ways.
The stage-1 (192), stage-2 (87) and stage-3 (110) API checks are re-run with `--include-previous` (not duplicated); the stage-2 browser suite is re-run separately.

**Shipped?** — does the shipped stage-4 harness (5 tests, judged from names only: *other payments carry refund_of null · a refund is a linked reverse
payment · a refund replay returns the original body · operator can correct a payment in a batch · a batch needs a settlement operator*) exercise it?
`Y` yes · `P` partly · `N` **not at all** · `—` not testable. **Risk**: CONC · RETRY · PARTIAL (atomicity) · ORDER (precedence/ties) · TIME · LIMIT · UPGRADE (imports).

## Intro and write paths

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| W-001 | Recipients can refund payments. | receiver refunds direct, request, capture, settlement payments | RF-01 RF-09 | P | |
| W-002 | Settlement operators can correct several payments in one request, including payments that belong to a settlement. | batch of ordinary, request and settlement payments | BC-02 BC-04 BC-05 | P (one payment) | |
| W-003 | Existing receipts and saved statements must remain available in their original form. | original POST/settlement retries return original bodies; feed unchanged; old snapshots page the frozen entries | BC-20 BC-21 RF-01 | N | RETRY |
| W-004 | All requirements from stages 1–3 continue to apply. | previous suites re-run | `--include-previous` | — | UPGRADE |
| W-005 | There are ten idempotent write paths: stage 1's five, authorizations and captures from stage 2, corrections from stage 3, and refunds and correction batches in this stage. | one key string is a first use on each of the ten paths, each replays independently; missing key 400 on the new two | BC-22 RF-04 BC-01 | N | RETRY |

## Refunds and corrected history

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-001 | `POST /payments/{payment_id}/refunds`, body `{"amount": 200}`, requires an idempotency key. | missing/empty key 400, 256 chars 422 | RF-04 | N | |
| R-002 | Only the original receiver may refund, else 403 `forbidden`; unknown payment is 404. | sender, strangers, operators 403; no token 401; unknown/odd ids 404; settlement members | RF-03 RF-20 | N | |
| R-003 | The target may be a direct payment, request payment or capture, but never a refund. | all three succeed; refund → 422 | RF-09 RF-08 | N | |
| R-004 | Invalid amount is 422 `validation_failed`. | 0, negatives, fractions, strings, booleans, missing, >1e9; integral forms OK | RF-05 | N | LIMIT |
| R-005 | Refunds cumulatively may not exceed the payment's current corrected amount: 422 `refund_exceeds_payment`. | exact cap ok, +1 rejected; cap follows corrections up and down; corrected-to-0 payment | RF-06 RF-07 RF-14 | N | |
| R-006 | Refunds of refunds give 422 `invalid_refund_target`. | by the refund's receiver | RF-08 | N | |
| R-007 | A refund is a new payment in the opposite direction, with `refund_of` naming the target, `request_id: null`, `authorization_id: null`, and the original note/visibility. | shape, direction, note/visibility copied, ids, settlement_id null | RF-01 RF-09 | Y | |
| R-008 | Return 201 with that payment; replay returns 200 with the original body. | 201, 200 identical, no money moves; different body 409; claimed key before validation; fresh-key concurrency | RF-04 RF-17 | Y (replay) | RETRY, CONC |
| R-009 | It moves existing money from the receiver's **available** funds, or fails 409 `insufficient_funds`, atomically. | spent or held money; nothing changes; key reusable | RF-10 | N | PARTIAL |
| R-010 | Refunds never reopen a request or authorization or restore a released hold. | request stays `paid` with its payment_id; authorization stays `captured`; `held` unchanged | RF-09 | N | |
| R-011 | Other payments have `refund_of: null`. | every payment object (create, pay, capture, settlement, feed, statements) | RF-02 | Y | |
| R-012 | (ordinary rules) refund payments appear in activity and statements per the ordinary rules | visibility copied: public visible to third parties, private to parties; statement entries revision 1; historical views include the refund at its created_at | RF-11 RF-15 | N | TIME |
| R-013 | (races) refunds racing refunds / corrections | 20 concurrent refunds respect the cap; refund ‖ correction never both; identical fresh-key refunds once | RF-16 RF-17 RF-18 | N | CONC |
| R-014 | (robustness) hostile bodies/ids | 4xx, never 5xx | RF-19 | N | |
| R-015 | (interplay) refund payments are linked payments | correction of a refund 422 after the permission check; revisions readable by parties only | RF-12 | N | |

## Corrections interplay

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| I-001 | Stage-3 corrections remain available for ordinary direct/request payments. | single corrections of direct and request payments by the sender | RF-07 BC-03 BC-12 | N | |
| I-002 | Captures and refund payments cannot themselves be corrected: 422 `linked_payment_immutable`. | single (stage-3 captures included) and batch | RF-12 BC-12 LK-03 | N | |
| I-003 | A correction cannot reduce a payment below its already-refunded amount: 422 `refund_exceeds_payment`. | exactly refunded is fine, one below rejected (single and batch); raising is fine | RF-07 BC-13 | N | |
| I-004 | Correction debits are checked against available funds. | receiver's held funds do not pay a decrease | RF-13 | N | |

## Batch corrections

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| B-001 | `POST /correction-batches` requires a settlement operator and an idempotency key, with the same 401/403 rules as settlements. | no token 401; non-operators (even the payments' sender) 403 before the body is read; key 400/422 after authorisation | BC-01 | Y (403 only) | |
| B-002 | corrections contains 1..32 objects with distinct payment_ids, else 422 `validation_failed`. | 0, 33, duplicates, non-objects, non-list; exactly 32 | BC-08 BC-09 | N | LIMIT |
| B-003 | Every item has the ordinary correction fields and validation. | per-field matrix incl. future instants, naive instants, boundary reasons/amounts | BC-10 BC-23 | N | LIMIT |
| B-004 | Unknown payment is 404; a stale expected revision is 409 `stale_revision`. | neither claims the key | BC-11 BC-14 | N | RETRY |
| B-005 | The operator may correct ordinary, request and settlement payments, but captures and refunds remain immutable. | all three correctable; captures/refunds 422 | BC-04 BC-05 BC-12 | N | |
| B-006 | Correcting any settlement member requires including every member of that settlement, else 422 `incomplete_settlement`. | subsets, a subset with a non-member, two settlements | BC-06 BC-07 | N | |
| B-007 | Members of one settlement must have identical effective instants (offset spellings may differ), else 422 `validation_failed`. | 1 µs apart rejected; `+00:00`/`+05:30`/`Z` spellings accepted | BC-05 BC-06 | N | TIME |
| B-008 | Ordinary single-payment corrections remain available for nonmembers. | by the sender, no batch id | BC-03 | P | |
| B-009 | Unknown fields are ignored. | top-level and item | BC-08 | N | |
| B-010 | Error precedence is: item errors in input order, settlement completeness, resulting current available funds, then historical total and available funds at every effective/event boundary. | validation/404/stale/immutable ordered by input position; item errors beat completeness, funds and history; completeness beats funds; funds beat history | BC-14 BC-15 BC-16 | N | ORDER |
| B-011 | The existing codes apply: `linked_payment_immutable`, `refund_exceeds_payment`, `insufficient_funds`, `historical_overdraft`. | each reachable through a batch | BC-12 BC-13 BC-16 BC-17 BC-25 | N | |
| B-012 | Affordability is determined by the combined effect of all proposed revisions. | one wallet debited by one item and credited by another: net decides; boundary −100 vs 99 | BC-17 | N | |
| B-013 | A rejected batch leaves history, balances and idempotency records unchanged. | revisions, balances, statements, feeds identical after 409/422; key reusable | BC-18 BC-09 | N | PARTIAL |
| B-014 | Return 201 with `correction_batch_id`, `recorded_at` and `revisions` in input order. | shape, order, 32 items | BC-02 BC-04 BC-09 | Y (201 only) | ORDER |
| B-015 | All new revisions share recorded_at, strictly later than the previous recorded_at of every member; each revision also exposes correction_batch_id. | shared instant, later than every member's previous revision; revisions endpoint exposes the id | BC-02 BC-04 BC-30 | N | TIME |
| B-016 | Effective times cannot be later than now. | any item, any offset spelling | BC-23 BC-10 | N | TIME |
| B-017 | Original payments and receipts never change. Original payment and settlement retries return their original bodies. | replays of POST /payments and /settlements after a batch | BC-20 | N | RETRY |
| B-018 | New statements reflect the new revisions; earlier snapshot tokens continue to page their frozen entries. | selected revision, shared recorded_at, known_at before/after; old snapshot identical | BC-21 | N | |
| B-019 | Replays return the original batch response with 200. This adds one idempotent write path. | after newer revisions; reordering/changing items 409; invalid body under a claimed key 409; per-operator scope | BC-19 BC-28 | N | RETRY |
| B-020 | Concurrent corrections sharing any expected payment revision cannot both succeed. | 12-way batches sharing p2: one 201, rest stale; identical fresh-key batches once; batch ‖ single ‖ refund | BC-27 BC-28 BC-29 | N | CONC |
| B-021 | (history) combined items at past boundaries; available at hold boundaries | outflow moved before its funding; innocent companion item; hold boundary | BC-25 BC-26 | N | TIME |
| B-022 | (storm) mixed refunds, batches, single corrections, settlements | no 5xx, sums conserved, no negative balances, revision lists consistent | BC-30 | N | CONC |
| B-023 | (robustness) hostile bodies | 4xx never 5xx | BC-24 | N | |

## Settlement refunds, imports, snapshots

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| S-001 | A settlement payment may be refunded under the existing refund rules, but refunds never change settlement membership. | refund of a member: member keeps `settlement_id`, revisions, receipts; the refund is not a member; a later batch needs exactly the original members | RF-09 RF-20 IM4-10 | N | |
| S-002 | Concurrent corrections sharing any expected payment revision cannot both succeed. (see B-020) | | BC-27 | N | CONC |
| M-001 | A stage-4 service must accept exports produced by the same team's stages 1–3, retaining settlement membership, corrections and snapshots. | stage-1, stage-2, stage-3 exports imported; membership (batch completeness), corrections (revisions, replays), snapshots | IM4-10 IM4-20 IM4-30 | N | UPGRADE |
| M-002 | Statement snapshot tokens must be retained by import: a token minted before export pages the same frozen entries after import (also into another process); tokens still die on reset. | same-process (state mutated between export and import), cross-process (`ALT_URL`), stage-3-minted tokens | IM4-01 IM4-02 IM4-30 | N | UPGRADE |
| M-003 | (stage-4 round trip) refunds, batches, corrections, replays, membership preserved | replays of refund/batch/correction/payment/settlement keys 200 with original bodies; revisions with batch ids; refund caps from imported refunds; immutability of imported refunds/captures | IM4-01 IM4-02 | N | UPGRADE |
| M-004 | (captures/holds after import) | captures immutable, refundable without restoring holds; open holds alive; statements show captures once | IM4-20 | N | UPGRADE |

## Model fuzz and invariants

| Row | Requirement | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| Z-001 | invariants at every read and every historical view: sum of balances == seeded total; no negative total/available at any boundary under the latest known revisions | 3 seeds × 45 and 5–6 seeds × 40 random payments, settlements, captures, refunds, single corrections, batches (errors only where the spec pins the precedence); an independent oracle predicts each status/code and every probed `/me` and `/statement` view | FZ4-01 FZ4-02 FZ4-03 FZ4-04 | N | CONC |
| Z-002 | engineering stance: money moves once under concurrency, batches and refunds are single atomic steps, ≥50 connections | RF-16.., BC-27..30 | RF-16 BC-27 BC-30 | N | CONC |

## Counts

Generated by `python3 check_matrix.py`:

- matrix rows: **55**
- shipped stage-4 harness (5 tests, judged from names): fully exercises 5 rows · partly 3 · **not at all 46** · not testable 1
- checks: **59 stage-4 API checks** (4 need `STAGE1_URL`/`STAGE2_URL`/`STAGE3_URL`/`ALT_URL`) plus the earlier stages' API checks via `--include-previous` (190 + 87 + 110); the stage-2 browser suite is re-run separately
- self-validation against the analyst's reference service (`selftest/refimpl4.py`): 446/446 pass (59 + 190 + 87 + 110, all import checks included); all 29 injected defects (`selftest/mutate_s4.py`) are caught
