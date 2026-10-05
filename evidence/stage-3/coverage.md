# Pocketful stage 3 — coverage matrix

Source: the stage-3 requirements handed over by @coordinator (parts 1–2), on top of stage 1 (accepted `621342c`) and stage 2 (accepted `92bbbf9`).
One row per requirement sentence. Check ids are the `[ID]` tags in the first line of each check's docstring:
`TS AO ST SN CR RV LK BT HH FZ IM CC RB` (all API checks, `run_checks.py`, Python 3 stdlib). `check_matrix.py` verifies ids both ways.
The stage-1 (192) and stage-2 (87) API checks and the stage-2 browser checks keep applying and are re-run, not duplicated.

**Shipped?** — does the shipped stage-3 harness (6 tests, judged from names only: *a payment carries an instant · me is unchanged without as_of ·
as_of in the future is the current balance · a statement closes its arithmetic · a statement walks the balance forward · a correction changes the
current balance*) exercise it? `Y` yes · `P` partly · `N` **not at all** · `—` not testable.
**Risk**: CONC concurrency · RETRY retries/idempotency · PARTIAL partial failure/atomicity · ORDER ordering/ties · TIME instants and clocks · ROUND exactness ·
LIMIT limits/boundaries · UPGRADE earlier data (imports).

## Payment timestamps

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| T-001 | Every payment's `created_at` is an RFC 3339 instant with an offset identifying when it moved money. | regex + parse + near-now on every payment-shaped response | TS-01 TS-07 | Y (one payment) | TIME |
| T-002 | Every endpoint returning a payment includes it. | create, pay-request, capture, settlement members, feed, statement entries | TS-01 ST-01 | P | |
| T-003 | `GET /activity` retains its existing ordering by this field. | newest first by created_at, also when seeded payments are listed out of order | TS-02 | N | ORDER |
| T-004 | Seeded payments may supply `created_at`; omission uses reset time, before subsequent API-created payments. | supplied instants (any offset) kept; omitted = reset time and ≤ later API payments | TS-03 TS-04 | N | TIME |
| T-005 | A seeded `created_at` in the future gives `422 validation_failed` from `POST /_test/reset`, with no state change. | +10 min / +1 h / +400 d; a future one after valid ones; previous world intact | TS-05 | N | PARTIAL |
| T-006 | A fixture's `balance` remains the balance after all seeded payments. Loading those payments must not change that balance. | balances equal the fixture numbers; total conserved | TS-06 | N | |

## `GET /me` as of an instant

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| M-001 | `as_of` is optional and is an RFC 3339 instant with an offset. Anything else — a naive local time, a bare date, an empty value — is 422 `validation_failed`. | 14 invalid forms incl. empty and valueless; offsets/impossible dates | AO-08 | N | LIMIT |
| M-002 | Without temporal query parameters the response retains the existing money fields and reports current corrected values. | unchanged shape; corrected current balance; unknown params ignored | AO-09 BT-08 | Y | |
| M-003 | With it, `balance` is the caller's balance as it stood at that instant: the balance after every payment of theirs with `created_at` at or before `as_of`, and before every payment after it. | walk-forward at hourly instants for 5 users | AO-01 AO-11 | N | TIME |
| M-004 | A payment made at exactly `as_of` counts as having happened. | exact instant in, one microsecond earlier out; ties at one instant combine; sub-second precision | AO-02 AO-12 AO-13 | N | TIME, ORDER |
| M-005 | An `as_of` at or after the latest payment returns the current balance. | latest, future, year-3650; after a new payment | AO-03 | Y (future) | |
| M-006 | An `as_of` before the earliest payment returns the opening balance — what the wallet held before anything moved. | epoch, year 1, 2000; new accounts open at 0 | AO-04 AO-05 | N | |
| M-007 | The response carries `as_of` back, exactly as given. | `Z`, `+00:00`, fractional, negative offsets; equal instants in other spellings give equal balances | AO-06 AO-07 | N | |
| M-008 | (auth) no token on a temporal read | 401 | AO-10 | N | |

## `GET /statement`

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| S-001 | `from` and `to` are optional; `from` defaults to the opening of the wallet and `to` to now. | defaults give the whole life; new payments appear in the next read | ST-01 ST-12 | P | |
| S-002 | `limit` and `offset` behave exactly as in `GET /requests`. | 1..200, offset ≥ 0, plain digits; defaults | ST-09 | N | LIMIT |
| S-003 | Returns the payments the caller sent or received in the half-open window `[from, to)`, **oldest first**, each with the caller's balance immediately after it. | payment at `from` in, at `to` out; empty/inverted/far windows | ST-03 ST-04 | N | LIMIT, TIME |
| S-004 | 1. Entries are ordered by `created_at` ascending, then payment `id` ascending for ties. (superseded by effective time below) | ties by id with progressive balance_after | ST-06 | N | ORDER |
| S-005 | 2. `opening_balance` is the balance immediately before `from`. `closing_balance` is the balance immediately before `to`. | all window shapes | ST-01 ST-03 ST-14 | P | |
| S-006 | 3. `opening_balance` plus all `delta` values in the full window must equal `closing_balance`. A sent payment has a negative `delta`; a received payment has a positive `delta`. | every user, running balance_after | ST-02 | Y | |
| S-007 | 4. Pagination must not change an entry's `balance_after` or the window's opening and closing balances. These values describe the full window regardless of `limit` and `offset`. | limits 1,2,3,5,n,n+5; windows with carried-in balance | ST-07 ST-08 | N | |
| S-008 | Only payments sent or received by the caller appear in their statement, including when other payments are public. The activity-feed visibility rules do not apply to statements. | strangers/operators empty; private appears in both parties' statements | ST-05 | N | |
| S-009 | `from`/`to` instants invalid | 422 for naive, date-only, empty, impossible | ST-10 | N | |
| S-010 | (auth) statement needs a token | 401 | ST-11 | N | |
| S-011 | (shape) entries embed the full payment incl. links | request_id, settlement_id, authorization_id, handles | ST-13 | N | |

## Effective time, recorded time, and corrections

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| C-001 | The service must distinguish **when money took effect** from **when it learned that fact**. | effective_at vs recorded_at in revisions, statements, known_at | RV-01 BT-01 BT-04 | N | TIME |
| C-002 | Every payment has a revision history. Revision 1 has `amount` as originally paid and `effective_at = recorded_at = created_at`. | revision 1 of API, seeded, settlement, captured payments | RV-01 RV-04 LK-01 | N | |
| C-003 | A seeded payment's supplied `created_at` is also its original recorded/effective time; omission uses reset time. | | RV-04 | N | |
| C-004 | Opening balances equal seeded ending balances minus the net effect of original seeded payments. Corrections must not change those opening balances. New accounts open at zero. Seeded history is consistent and nonnegative. | openings 10000/2000/…; stable after many corrections; new account 0 | AO-04 AO-05 BT-09 | N | |
| C-005 | `POST /payments/{payment_id}/corrections` requires an idempotency key and the original sender. | missing/empty key 400, 256 chars 422 | CR-12 CC-05 | N | |
| C-006 | An authenticated non-sender gets 403 `forbidden`; unknown payment gets 404. | receiver, stranger, operator 403 (public payment too); unknown/odd ids 404; no token 401 | CR-08 | N | |
| C-007 | All fields are required. Revision is a positive integer; amount is an integer 0..1000000000 (zero reverses the entire payment); reason is a string of 1..200 characters; effective time is an RFC 3339 instant not later than now. Invalid input is 422 `validation_failed`. | full validation matrix incl. boundaries, forms 400.0/4e2, future and naive instants | CR-05 CR-06 CR-07 | N | LIMIT |
| C-008 | Correction changes neither parties nor visibility. | statement shows same handles/visibility; feed membership unchanged | CR-04 | N | |
| C-009 | It appends an immutable revision, returning 201 with `payment_id`, `revision`, `amount`, `effective_at`, server-assigned `recorded_at`, and `reason`. | body fields; `recorded_at` in the body is ignored | CR-01 CR-13 | N | |
| C-010 | Recorded times for one payment strictly increase. | 8 rapid corrections; also over revision 1 | CR-14 | N | TIME |
| C-011 | A stale expected revision gives 409 `stale_revision`. | wrong/old/new revisions; key not claimed; nothing changes | CR-09 | N | RETRY |
| C-012 | Successful replay returns that original revision with 200 even after newer revisions. | replay after revisions 3 and 4, reordered/pretty JSON | CR-10 | N | RETRY |
| C-013 | Different body with the same key is 409 `idempotency_key_reuse`. | each field; invalid/stale bodies under a claimed key (key resolved first) | CR-11 | N | RETRY |
| C-014 | The difference from the previous amount moves between the **same two wallets** in the same atomic step. Increasing the amount debits the original sender; decreasing it debits the original receiver. | decrease, increase, to zero and back up; third wallets untouched | CR-01 CR-02 CR-03 CR-04 | Y (decrease) | PARTIAL |
| C-015 | A currently unaffordable debit gives 409 `insufficient_funds`. | receiver short; sender short against AVAILABLE; receiver's held funds do not pay | CR-15 CR-16 CR-17 | N | |
| C-016 | Otherwise, if any user's corrected balance is negative at any effective-time boundary, return 409 `historical_overdraft`. | decrease → receiver negative later; increase → sender negative then; earlier effective time; available at hold boundaries | CR-20 CR-21 CR-22 CR-25 HH-07 | N | TIME |
| C-017 | Balances at a boundary include the combined effect of all movements at that instant. | tied payee paid and paying at one instant is not overdrawn | CR-24 FZ-01 | N | ORDER |
| C-018 | Either failure preserves balances, revision history, statements and idempotency state. | after 409s: balances, revisions, statements unchanged; key reusable | CR-15 CR-20 RV-03 | N | PARTIAL |
| C-019 | Currently unaffordable debits take precedence over historical overdraft (precedence). | both violated → insufficient_funds | CR-23 | N | |
| C-020 | The sum of balances must equal the seeded total in every historical view. | sums at 10 instants; fuzz probes at random (as_of, known_at) | AO-11 ST-14 FZ-01 FZ-02 FZ-03 CC-08 | N | |
| C-021 | The original payment and every original idempotent response remain unchanged. `GET /activity` continues to display the original payment; correction records are not new feed payments. | feed deep-equal before/after; original POST /payments replay still shows the original amount | CR-30 BT-07 | N | RETRY |
| C-022 | `GET /payments/{payment_id}/revisions` returns `{"revisions": [...]}` in revision order, including revision 1 (`reason: ""`). | order, fields, revision 1 | RV-01 | N | ORDER |
| C-023 | Only the two parties can read it; a third party gets 404 even for a public payment. No token is 401. | sender/receiver 200; strangers/operators 404; 401 | RV-02 | N | |
| C-024 | (deadline shape) request-paid payments are ordinary payments | correctable, or refused only as linked | CR-31 | N | |
| C-025 | (no-op) same amount and instant | never 5xx, no money moves | CR-32 | N | |

## `known_at` (bitemporal)

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| K-001 | `GET /me` and `GET /statement` accept optional `known_at`, an RFC 3339 instant with offset. | both endpoints | BT-01 BT-03 | N | |
| K-002 | For each payment, select its latest revision recorded **at or before** `known_at`; if none was yet recorded, that payment contributes nothing. | exact-instant inclusion; before first record; API payment not yet known | BT-01 BT-02 BT-09 | N | TIME |
| K-003 | Omission means everything known when the read begins. | | BT-01 BT-08 | N | |
| K-004 | Then apply selected revisions according to their **effective** times. `as_of` retains its inclusive meaning; a statement retains its half-open window. | effective moved later/earlier; windows × known_at | BT-04 BT-06 | N | TIME |
| K-005 | Both query instants may be in the future. | future as_of and known_at | AO-03 BT-02 BT-03 | P | |
| K-006 | Invalid/empty instants are 422. Echo supplied `known_at` exactly. | `/me` and `/statement`, echo in several spellings | BT-03 ST-10 | N | |
| K-007 | Statement ordering is now by selected `effective_at`, then payment id. | corrections reorder entries; ties | BT-05 ST-06 FZ-01 | N | ORDER |
| K-008 | Each entry retains `payment`, `delta` and `balance_after`, and adds the selected `revision`, `effective_at` and `recorded_at`. | | ST-01 BT-05 | N | |
| K-009 | `payment.amount` is the selected amount for this statement. | per known_at | BT-05 BT-06 | N | |
| K-010 | Zero-amount revisions still appear as entries with zero delta. No correction is counted alongside the revision it replaces. | reversal appears with delta 0; one entry per payment; replaced revision never double counted | BT-05 BT-01 | N | |
| K-011 | With no corrections and no `known_at`, previous behavior is unchanged. | statements/feeds as before | ST-01 AO-09 | P | |
| K-012 | (model) every (as_of, known_at, window, page) view equals an independent oracle | 3 seeds × 30 random corrections incl. ties; all four outcomes exercised | FZ-01 FZ-02 FZ-03 FZ-04 | N | |

## Stable statement pagination

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| P-001 | Every first `GET /statement` response additionally returns an opaque `snapshot` token. | string token on every read, distinct per read | SN-01 | N | |
| P-002 | It freezes the caller's selected revisions, window, balances, entries and default `to` at that read. | frozen after payments, corrections, hold lifecycle | SN-02 SN-05 SN-06 SN-07 | N | |
| P-003 | `GET /statement?snapshot=<token>&limit=...&offset=...` pages that exact result, even after payments or corrections. | all pages equal the full result | SN-01 SN-02 | N | |
| P-004 | Only limit and offset may accompany a snapshot; supplying `from`, `to` or `known_at` with it gives 422 `validation_failed`. | each of the three; invalid limit/offset 422 | SN-03 | N | |
| P-005 | Unknown token, another user's token, or a token from before reset gives 404 `not_found`. | unknown, altered, bob/eve using ada's, pre-reset | SN-04 | N | |
| P-006 | Tokens last until reset. No storage survival across container restarts is required. | still valid after 300 other snapshots | CC-06 SN-04 | N | LIMIT |
| P-007 | Paging changes neither balances nor entries; the final partial page and offsets beyond the end must report `has_more` correctly. | exact-fit, partial, beyond-end | SN-01 ST-07 | N | LIMIT |
| P-008 | Unrecognized query parameters remain ignored under stage 1's general rule. | extra params on snapshot reads | SN-03 | N | |
| P-009 | A correction may move a payment into or out of a statement window. | in/out with and without known_at | BT-06 | N | |
| P-010 | Existing snapshots remain unchanged during concurrent payments or corrections. | 4 writer threads while every page is compared | SN-08 | N | CONC |
| P-011 | Concurrent corrections using the same expected revision cannot both succeed. | 20-way: one 201, rest stale_revision, money once | CC-01 | N | CONC |
| P-012 | (load) 50 concurrent first reads and snapshot pages | | CC-07 | N | CONC |

## Settlement history, linked payments, imports

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| L-001 | Stage-1 settlements retain their original receipts and privacy rules. | members keep visibility; revisions readable only by parties | LK-02 | N | |
| L-002 | Each member's original revision uses its shared committed_at as both effective_at and recorded_at. | every member; also after import | LK-01 TS-01 IM-10 | N | |
| L-003 | Single-payment corrections reject settlement members with 422 `linked_payment_immutable`. | each member, by its sender; balances/history unchanged; permission (403) checked first | LK-01 LK-04 | N | |
| L-004 | A stage-3 service must accept exports produced by the same team's stage-1 or stage-2 service. | stage-1 export; stage-2 export (tokens, balances, keys, requests, operators) | IM-10 IM-11 IM-20 | N | UPGRADE |
| L-005 | The ledger must import and account for authorizations and captures. | imported holds keep holding; captures once in statements; open hold capturable after import; historical views after import | IM-20 IM-01 | N | UPGRADE |
| L-006 | Captures are immutable linked payments: a correction of a capture gives 422 `linked_payment_immutable`. | nonfinal and final captures; imported captures | LK-03 IM-20 | N | |
| L-007 | (imported opening balances) equal what a native stage-3 reset reports | opening = ending − net effect of ALL imported payments | IM-10 IM-11 | N | UPGRADE |
| L-008 | (stage-3 export/import) ledger survives | revisions, recorded times, views, correction replays | IM-01 | N | UPGRADE |

## Historical holds

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| H-001 | For `GET /me?as_of=T&known_at=K`, all four money fields describe that same view: `balance = total`, `available = total - held`. | invariants on every probe | HH-01 AO-06 | N | |
| H-002 | A hold starts at authorization creation; nonfinal capture reduces it at capture time; final capture, void or expiry releases the remainder at that event's time. | timeline with two nonfinal captures and a void, probes at ±1 µs/±0.6 s | HH-01 | N | TIME |
| H-003 | Expiry takes effect at `expires_at`. | inclusive at the deadline, released at/after | HH-03 HH-04 | N | TIME |
| H-004 | Events other than clock expiry are known at their server-assigned event time. | void/captures unknown before their time | HH-02 | N | TIME |
| H-005 | Once creation is known, the expiry deadline is known too. | released at deadline even with an early known_at | HH-02 | N | TIME |
| H-006 | For queries beyond now, an open hold expires at its deadline. | as_of past the deadline → 0 | HH-03 | N | TIME |
| H-007 | Without `as_of`, use the instant the request began. | current view with holds | HH-01 HH-03 | N | |
| H-008 | Authorizations expose `closed_at` (null while open; event time when closed). | create, list, void, final capture, expiry (= expires_at); idempotent void keeps it | HH-05 HH-04 | N | |
| H-009 | Historical `total` follows stage-3 effective/recorded-time rules. | corrections change historical totals under holds | FZ-01 HH-07 | N | |
| H-010 | A correction is rejected with 409 `historical_overdraft` if it makes either total or available negative at any past effective/event boundary, under the latest known revisions. | available negative at a past hold boundary while total ≥ 0 | CR-25 HH-07 | N | |
| H-011 | Current unaffordable debits still take precedence as `insufficient_funds`. | | CR-23 | N | |
| H-012 | Seeded open holds are assumed created at reset unless `created_at` is supplied; seeded closed holds need not reconstruct a prior lifecycle. | supplied created_at honoured; reset time otherwise; closed seeds hold nothing | HH-06 | N | |
| H-013 | `GET /statement` still contains money movements only: authorization, release and expiry are not payments. Captures appear exactly once with their links. | no hold entries; captures once with `authorization_id` | LK-03 IM-20 | N | |
| H-014 | Old snapshots remain unchanged after any lifecycle action or correction. | snapshot before/after capture, void, correction | SN-07 | N | |

## Concurrency, limits, robustness (engineering stance)

| Row | Requirement | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| Q-001 | eight idempotent write paths | missing key; one key across all eight is a first use on each | CC-05 CR-12 | N | RETRY |
| Q-002 | concurrent identical corrections with a fresh key | one 201, rest 200 identical, one revision | CC-02 | N | CONC, RETRY |
| Q-003 | money moves once under concurrency; invariants at every read | 18 racing corrections on 12 payments: balances = Σ latest amounts, none negative, total conserved | CC-03 | N | CONC |
| Q-004 | correction racing the receiver spending the money | one wins, invariants hold, outcomes consistent | CC-04 | N | CONC |
| Q-005 | mixed storm: payments, corrections, authorizations, captures, statements, historical reads | no 5xx; per-user shape on every read; sums conserved | CC-08 | N | CONC |
| Q-006 | 5xx-free on the new endpoints | hostile bodies/ids/instants, huge/odd instants | RB-01 RB-02 | N | |
| Q-007 | integer minor units end to end | corrected/historical balances are exact integers; amount bound 1e9 | RB-02 CR-06 | N | ROUND |
| Q-008 | stage-1/2 requirements continue to apply | `--include-previous` re-runs 192 + 87 API checks; stage-2 browser suite re-run separately | (runner) | — | UPGRADE |

## Counts

Generated by `python3 check_matrix.py`:

- matrix rows: **104**
- shipped stage-3 harness (6 tests, judged from names): fully exercises 5 rows · partly 5 · **not at all 93** · not testable 1
- checks: **110 stage-3 API checks** (3 need `STAGE1_URL`/`STAGE2_URL`) plus the 192 stage-1 and 87 stage-2 API checks via `--include-previous`; the stage-2 browser suite is re-run separately
- self-validation against the analyst's reference service (`selftest/refimpl3.py`): 388/388 (110 + 191 + 87) pass; all 30 injected defects (`selftest/mutate_s3.py`) are caught
