# Pocketful stage 3 — ambiguity notes

Dark run: no questions asked. `Strict` = the check fails an implementation that reads it differently; `Lenient` = either reading passes.
Stage-1 and stage-2 notes still apply to everything inherited.

| # | Question | Choice and reason | Strict / lenient | Checks |
|---|---|---|---|---|
| D-01 | Time precision. Spec gives instants with offsets; examples are whole seconds. | Instants are compared at microsecond precision. Checks probe `±1 µs` around exact instants returned by the service (never assume sub-µs), so a service that truncates to ms or µs consistently passes; one that stores whole seconds only fails the "strictly increasing recorded_at" check (CR-14), as required. | Strict | AO-02 AO-12 CR-14 |
| D-02 | Which timestamp grammar is an "RFC 3339 instant with an offset"? | `YYYY-MM-DDTHH:MM:SS[.fraction](Z|±HH:MM)`. Rejected (422): naive, date-only, empty, no seconds (`13:20Z`), offsets with hour > 23, impossible dates. Not asserted either way: lowercase `t`/`z`, space separator, `+0000` without colon, leap seconds. | Strict for the listed | AO-08 BT-03 ST-10 CR-05 |
| D-03 | Valueless or empty `as_of=` / `known_at=` / `from=` / `to=`. | Empty value is 422 (spec: "an empty value"). `?as_of` with no `=` is the same empty value. | Strict | AO-08 ST-10 |
| D-04 | Echo "exactly as given". | `as_of` and `known_at` are returned byte for byte (`Z` stays `Z`, fraction digits kept). Correction `effective_at`/`recorded_at` are compared as instants, not strings. | Strict (echo) / Lenient (corrections) | AO-06 BT-03 CR-01 |
| D-05 | `/me?known_at=K` without `as_of`. | `as_of` defaults to the instant the read began (spec for historical holds); response has no `as_of` key. | Strict | BT-03 |
| D-06 | Statement tie order "payment `id` ascending". | Plain string comparison of ids; checks use zero-padded ids (`p_007 < p_008 < p_009 < p_010`) so numeric and string order agree. | Strict (zero-padded) | ST-06 FZ-01 |
| D-07 | Statement ties: `balance_after` inside one instant. | Progressive in (effective_at, id) order; overdraft checks use the combined balance at the boundary. | Strict | ST-06 CR-24 |
| D-08 | Default `to` = "now". | The instant the read began; every payment effective ≤ now is included. | Strict | ST-12 |
| D-09 | `from` after `to`. | Either an empty statement (200) or 422; never 5xx. | Lenient | ST-04 |
| D-10 | Order of checks in `POST /payments/{id}/corrections`. | 401 → 404 unknown → 403 non-sender → 400 missing key → 400 bad JSON → *claimed key resolved* → 422 validation → 422 linked → 409 stale → 409 insufficient → 409 historical. Checks only pin pairs the spec orders (key before validation/state; insufficient before historical; permission before linkage). | Strict on those pairs | CR-08 CR-11 CR-23 LK-04 |
| D-11 | Wrong JSON types in correction fields. | `amount` of any non-number-form (strings, booleans, fractions) is 422 (as stage 1). `expected_revision`, `reason`, `effective_at` of the wrong type: 400 **or** 422. `null`/array/object amount: 400 or 422. Missing/empty/out-of-range: 422. | Lenient (types) / Strict (values) | CR-05 |
| D-12 | `expected_revision` `1.0`/`0.0`. | `0`, `-1`, `1.5`, `0.0` are 422. `1.0` not asserted. | Strict for the listed | CR-05 |
| D-13 | A correction that changes nothing (same amount and instant). | 201 (new revision, no money) or 422; never 5xx and no money moves. | Lenient | CR-32 |
| D-14 | Is a payment that paid a *request* correctable? | Spec only exempts settlement members and captures. Correctable (201) is expected; a service that treats it as linked must answer 422 `linked_payment_immutable`; the request stays `paid` either way. | Lenient | CR-31 |
| D-15 | "A currently unaffordable debit": measured against `available` (stage 2) or total? | `available` (stage 2: every insufficient_funds is against available); the receiver's held funds cannot pay a decrease. | Strict | CR-16 CR-17 |
| D-16 | Retroactive reversal of money the receiver already spent. | Judged by the boundary rule: it is `historical_overdraft` even when the receiver could pay back today — "preserve the past" (e.g. correcting to 0 a payment whose proceeds were spent earlier, with money since returned). Only a *current* shortfall gives `insufficient_funds`. | Strict | CR-15 CR-20 CR-23 |
| D-17 | Boundaries considered. | Every effective instant of the latest revisions of all payments plus every hold event (creation, each capture, close, `expires_at`) **not later than now**; opening balances must be ≥ 0; all users are checked, balances at a boundary include every movement at that instant; both `total` and `available` must be ≥ 0. | Strict | CR-20..25 FZ-01.. |
| D-18 | "Recorded times for one payment strictly increase" includes revision 1. | Yes: revision 2's `recorded_at` is strictly after revision 1's (= `created_at`), even for an immediate correction. | Strict | CR-14 |
| D-19 | `effective_at` earlier than the payment's `created_at`, or equal to the previous revision's. | Allowed (only "not later than now" is a limit). | Strict | CR-01 CR-20..22 |
| D-20 | Statement `payment.created_at`. | Remains the original `created_at` (the receipt); the selected instant is the entry's `effective_at`. | Strict | ST-01 BT-05 |
| D-21 | Revisions endpoint element shape. | At least `revision`, `amount`, `effective_at`, `recorded_at`, `reason` (extra keys such as `payment_id` allowed); revision 1 has `reason: ""`. | Lenient (extras) | RV-01 |
| D-22 | Snapshot response shape. | Same fields as a first read; the `snapshot` token may or may not be repeated when paging. The token is a non-empty string ≤ 4096 chars; distinct per read. | Lenient (repeat) | SN-01 |
| D-23 | 404 vs 422 when a snapshot is unknown AND `from`/`to` is supplied. | Not asserted (only known-token + window is pinned: 422; unknown token alone: 404). | n/a | SN-03 SN-04 |
| D-24 | Snapshot and `as_of` / unknown params. | `as_of` is not a statement parameter: ignored (stage-1 general rule) even with a snapshot. | Strict | SN-03 |
| D-25 | Snapshot lifetime across import. | Not asserted (import replaces state; only "until reset" is specified). | n/a | — |
| D-26 | Opening balance after importing a stage-1/2 export. | Imported ledger: opening = current balance − net effect of **all** imported payments (they all carry `created_at`); equals what a native stage-3 reset of the same fixture reports. | Strict | IM-10 IM-11 |
| D-27 | `closed_at` for imported / seeded closed authorizations. | Not asserted (spec: "need not reconstruct"); only the key's presence is. For API-created ones: void → the void's event time; final capture → within 50 ms of the capture payment's `created_at`; expiry → exactly `expires_at`; open → `null` (also on the create response). | Strict (API-created) | HH-04 HH-05 IM-20 |
| D-28 | Hold timeline instants. | A hold exists in a view iff `T ≥ created_at` and creation is known (`K ≥ created_at`); a nonfinal capture reduces it iff `capture time ≤ T` and `≤ K`; void/final capture release iff event time ≤ T and ≤ K; expiry releases iff `T ≥ expires_at` (known as soon as creation is). All inclusive. | Strict | HH-01..03 |
| D-29 | `available` in a view when `held` exceeds `total` (corrections cannot create this). | Not constructed. | n/a | — |
| D-30 | Seeded open hold with `created_at` in the future / invalid. | Not asserted. | n/a | HH-06 |
| D-31 | "Statement contains money movements only". | No entries for authorize/void/expiry; each capture exactly once, with `authorization_id`; settlement members keep `settlement_id`. | Strict | LK-03 IM-20 |
| D-32 | Stage-2 `ME-01` (exact `/me` shape without temporal params). | Still applies: temporal fields appear only when requested (`as_of` / `known_at` echoes), no other new keys. | Strict | AO-09 |
| D-33 | Many snapshots / memory. | "Bounded memory" is not black-box observable; the check asserts only that 300 snapshots later the first one is still valid and identical. | n/a | CC-06 |
| D-34 | Extreme instants (year 0001, 9999, huge years). | Accepted (200) or rejected (422) cleanly; never 5xx; valid ones give exact integer balances. | Lenient | RB-01 RB-02 |
| D-35 | Time of `/_test/reset` and seeded `created_at` equal to now. | Seeded instants are always ≥ 2 s in the past in the checks; the "future" boundary itself is not probed. | n/a | TS-05 |
