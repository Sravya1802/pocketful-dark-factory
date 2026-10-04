# Pocketful stage 1 — coverage matrix

Source: the stage-1 requirements handed over by @coordinator (parts 1–3). One row per requirement sentence.
Check ids (`PAY-01` …) are the `[ID]` tags in the first line of each check's docstring under `checks/`; `python3 check_matrix.py`
verifies every id used here exists and every check is referenced.

**Shipped?** — does the shipped harness's stage-1 test set appear to exercise this requirement (judged from its test names only)?
`Y` yes · `P` partly (some inputs / one path) · `N` **not exercised — these rows matter most** · `D` deliverable, not an HTTP behaviour
(covered by `docker_run_checks.sh`, not by `run_checks.py`) · `—` not testable.
**Risk** flags concurrency (CONC), retries (RETRY), partial failure (PARTIAL), ordering (ORDER), time (TIME), rounding (ROUND), limits (LIMIT),
upgrade from earlier data (UPGRADE: export → import).

## §1 Scope

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-001 | Users can send money by handle, request money and split bills. | `/payments`, `/requests`, `/splits` exist and work by handle | PAY-01 REQ-01 SPL-01 | Y | |
| R-002 | Payments appear in an activity feed with public or private visibility. | feed shows payments with `visibility` | FED-01 FED-10 | Y | |
| R-003 | Authorized operators can submit groups of transfers as settlements. | operator settlement succeeds, others refused | SET-01 SET-04 | P (one happy 2-transfer) | |
| R-004 | Only the HTTP API is required. | no non-HTTP interface needed | — | — | |
| R-005 | The sum of wallet balances always equals the total seeded by the last `POST /_test/reset`. | conservation after every op, incl. concurrent, splits paid, import | INV-01 CON-03 CON-08 CON-09 CON-13 CON-14 SPL-12 EXP-12 EXP-13 SET-23 | P (payment+conservation fixture only) | CONC, PARTIAL |
| R-006 | No wallet balance may be negative, including transiently. | observer polling `/me` during drains/settlements never sees < 0; pass-through wallet in a settlement stays 0; 30 racing payments overdraw only floor(balance/amount) times | CON-01 CON-02 CON-03 CON-09 CON-11 | N | CONC |
| R-007 | A payment request may move money at most once. | paying one request many times / with many keys / racing cancel, decline | CON-04 CON-05 CON-06 CON-07 CON-12 CON-13 IDM-15 IDM-24 REQ-11 | P (sequential only) | CONC, RETRY |
| R-008 | All amounts are exact integer counts of minor units. | integers in, integers out, exact arithmetic near 2^53 | MDL-01 MDL-04 MON-01 | P | LIMIT |
| R-009 | Deposits, top-ups, withdrawals, cards and bank integrations are out of scope. Money moves only between existing wallets. | recipient must exist (404), no balance-minting path | PAY-13 SET-25 SPL-09 PAY-17 | P | |

## §2 Delivery and deployment

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-010 | Deliver an HTTP service, a `Dockerfile` and a `RUN.md` with a command that builds and starts the service without manual setup. | Dockerfile + RUN.md present in product dir; documented command builds and starts | DOC-01 | D | |
| R-011 | The image must run on its own with `-e PORT=<port>` and a port mapping. | container serves on the given PORT | DOC-02 | D | |
| R-012 | Runtime networking has no outbound access. All runtime dependencies, initialization and seed data must work within that single container. | works with `--network none` | DOC-03 | D | |
| R-013 | Runtime assets and dependencies must be included in the image. | no network needed at run time (same run) | DOC-03 | D | |
| R-014 | Start to first healthy response — 60 s | `/health` 200 within 60 s of `docker run` | DOC-02 | D | TIME |
| R-015 | Concurrent requests — up to 50 in flight | 50 simultaneous requests all succeed | LIM-01 LIM-02 CON-02 | P (10 clients) | CONC, LIMIT |
| R-016 | Per-request timeout — 5 s (10 s for `POST /_test/reset`) | every call has a 5 s client timeout (10 s on reset/export/import); timeouts fail the check | all checks (`lib.call`), LIM-03 LIM-04 LIM-05 | N | TIME |
| R-017 | 2 vCPU / 2 GiB memory | runs under `--cpus 2 --memory 2g` | DOC-02 | D | LIMIT |
| R-018 | Disk — ephemeral; state need not survive a container restart | nothing required | — | — | |

## §3 Runtime contract

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-020 | Listen on `0.0.0.0` using the `PORT` environment variable, default `8080`. | reachable on mapped port; default 8080 without PORT | DOC-02 DOC-04 | D | |
| R-021 | `GET /health  ->  200  {"status": "ok"}` | exact body, no auth | HLT-01 | Y | |
| R-022 | Return 200 once the service and its data store can serve requests, within 60 seconds of container start. | health ready ⇒ reset/login work | DOC-02 | D | TIME |
| R-023 | Replace all service state with the fixture in the request body (§4). | after reset only fixture users/payments/requests/operators exist; tokens, signups, idempotency keys gone | RST-01 RST-02 RST-07 | P | UPGRADE |
| R-024 | When reset returns 204, subsequent requests must see only that fixture. | no leakage of earlier state (users, tokens, keys, payments) | RST-02 | N | |
| R-025 | Repeated resets are supported. | 5 resets in a row, no auth | RST-04 | N | |
| R-026 | This test endpoint must be enabled in the delivered image and requires no authentication. | works unauthenticated | RST-04 AUT-11 | P | |
| R-027 | Requests and responses are `application/json; charset=utf-8`. | content-type on success and on errors | CNV-01 | N | |
| R-028 | Timestamps in responses are RFC 3339 with an explicit offset, e.g. `2026-09-24T19:00:00+02:00`. | regex + parse + near-now for payments, requests, splits, settlements | CNV-01 PAY-01 REQ-01 SPL-01 SET-04 SET-16 | N | TIME |
| R-029 | Unknown fields in a request body are ignored, never an error. | on payments, requests, splits, settlements, signup/login, import, reset | CNV-02 PAY-16 REQ-08 SPL-14 SET-13 RST-06 EXP-16 | P (payments only) | |
| R-030 | Unknown query parameters are ignored. | on `/me`, `/activity`, `/requests`, `/health` | CNV-03 FED-08 REQ-16 | P | |
| R-031 | IDs are opaque strings of at most 64 characters. Their format is yours. | all ids are strings 1..64 chars | CNV-04 PAY-01 SPL-01 SET-04 | N | |

## §4 Model

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-040 | The service has **one currency**, declared in the fixture. | `/me` and all money objects carry fixture currency | MDL-05 MDL-06 MDL-07 | Y | |
| R-041 | Every amount in the API is an integer count of its minor units: `1000` in a `minor_units: 2` service is €10.00, and `1000` in a `minor_units: 0` service is ¥1000. | same integer in/out for EUR/JPY/BHD; no scaling | MDL-05 MDL-06 MDL-07 | Y | |
| R-042 | API amounts must have an integral numeric value: JSON `1000`, `1000.0` and `1e3` all represent the same valid minor-unit amount. | all three forms (and `1E3`, `10e2`) accepted and returned as integer 1000; on payments, settlements | MDL-01 SET-13 | P (one max-value form list) | |
| R-043 | Booleans and strings are not numbers here. | `true`, `"1000"` → 422 on every amount-taking endpoint | MDL-02 REQ-03 SPL-09 SET-13 | Y | |
| R-044 | Every user has a **handle**: unique across the service, matching `^[a-z0-9_]{1,20}$`, and never changing once set. | derived handles match regex; unique; `/me` handle stable across import | AUT-02 AUT-03 AUT-13 EXP-02 | P | |
| R-045 | Users identify recipients by handle. | `to_handle`, `payer_handle`, `participant_handles`, settlement handles | PAY-01 REQ-01 SPL-01 SET-04 | Y | |
| R-046 | Directory and user-search endpoints are out of scope. | none needed | — | — | |
| R-047 | Seeded users take their handle from the fixture. | fixture handle used (`ada`, `op2`, …) | RST-01 RST-07 | Y | |
| R-048 | A user created through `POST /auth/signup` (§6 — there is no `handle` field in the signup body) has one **derived** from their email: take the local part, lowercase it, replace every character outside `[a-z0-9_]` with `_`, and truncate to 20 characters. | `Ada.Lovelace+X` → `ada_lovelace_x`; 30-char local → 20; body `handle` ignored; derived handle usable as recipient | AUT-02 AUT-16 | P (truncation only) | |
| R-049 | If that handle is already taken the signup fails; see the signup table in §6. | 409 `handle_taken`, no account; truncation happens before the check | AUT-03 AUT-04 | P | |
| R-050 | New users start with a balance of `0`. They can receive money and be asked for money immediately. | `/me` 0; receive then spend; request from new user at once | AUT-01 AUT-14 REQ-04 | Y | |
| R-051 | A **payment** moves money from one wallet to another, immediately and atomically. It is either sent directly or created by paying a request. | direct and via-request payments debit/credit together | PAY-01 REQ-05 | Y | PARTIAL |
| R-052 | A **request** asks someone for money. The `requester` will receive; the `payer` is being asked. | requester = caller; funds flow payer → requester | REQ-01 REQ-05 | Y | |
| R-053 | A request is `pending`, and then exactly one of `paid`, `declined` or `cancelled`. | state machine; terminal states are final | REQ-11 REQ-12 REQ-13 | Y | CONC |
| R-054 | Only the payer may pay or decline it; only the requester may cancel it. | 403 for wrong party on each action | REQ-10 REQ-12 REQ-13 REQ-14 | Y | |
| R-055 | **A request may exceed the payer's balance.** That is a legal state, not an error at creation time: the request stays `pending` until it is paid, declined or cancelled, and an attempt to pay it while short is `409 insufficient_funds` and changes nothing. | create 201 for huge amount; pay while short 409, state unchanged | REQ-02 REQ-09 | Y | |
| R-056 | Money can arrive later and the same request then becomes payable. | fund payer, pay same request → 201 | REQ-09 IDM-12 | P | |
| R-057 | **Visibility belongs to the payment, not the request.** The payer chooses it when the money moves. | `visibility` on pay body; request exposes none | REQ-07 REQ-08 | P | |
| R-058 | A request carries no visibility of its own and never appears in anyone else's feed. | no `visibility` key on request objects; requests absent from every feed | REQ-01 REQ-08 REQ-19 | Y | |
| R-059 | `GET /activity` returns payments only. | feed items are payments (payment_id, no status) | REQ-19 FED-10 | Y | |
| R-060 | A payment appears for a caller **if and only if** its `visibility` is `public`, **or** the caller is its sender or its receiver. | full matrix incl. operators and strangers | FED-01 FED-03 FED-05 FED-02 | Y | |
| R-061 | There is no other rule, no follow graph and no mute list. | stranger sees exactly the public set | FED-05 | N | |
| R-062 | Requests never appear in the activity feed; they are read through `GET /requests`, which returns only requests where the caller is the requester or the payer. | scoping for parties, third parties, operators | REQ-16 REQ-19 SET-18 | Y | |
| R-063 | A split is not a feed item. The requests it creates are visible to their own two parties, and the payments that eventually fulfil them follow the rule above. | split adds nothing to feeds; split requests scoped; fulfilling payments obey visibility | SPL-11 REQ-07 | N | |
| R-064 | Visibility is **one value on the payment**, seen identically by both parties and by everyone else. | sender and receiver see equal objects | PAY-18 FED-11 | N | |
| R-065 | A `private` payment is hidden from third parties, not from its own receiver. | receiver sees private | FED-04 REQ-07 | Y | |
| R-066 | `amount` is at most `1000000000` on any single request, and no operation produces a balance outside ±2⁵³. | 1e9 ok, 1e9+1 → 422 on every endpoint; exact arithmetic with ~2^53 balances | PAY-11 MON-01 REQ-03 SPL-09 SET-13 | Y | LIMIT |
| R-067 | Monetary arithmetic must preserve exact minor-unit values without rounding error. | exactness near 2^53; split sums exact up to 1e9 | MON-01 SPL-03 | P | ROUND |

### Fixture

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-070 | Seeded users must be able to log in with the given password immediately. | login 200 for all seeded users after reset | RST-07 | Y | |
| R-071 | `balance` is the wallet balance **after** every seeded payment has been applied. Seeded numbers are consistent; you do not replay seeded payments against balances. | balances equal fixture numbers despite seeded payments | RST-08 EXP-13 | P | UPGRADE |
| R-072 | A `balance` below zero in a fixture is a reset error: return `422 validation_failed` from `POST /_test/reset` and change nothing. | 422 and previous world intact | RST-03 | Y (change-nothing part: P) | PARTIAL |
| R-073 | `minor_units` is `0`, `2` or `3`. Fixtures use `EUR` (2), `JPY` (0) and `BHD` (3). | the three currencies work end to end | MDL-05 MDL-06 MDL-07 | Y | |
| R-074 | (fixture) `settlement_operator_ids` | fixture seeds operators; seeded payments/requests (incl. declined/cancelled) load with given ids | RST-07 SET-02 FED-02 REQ-06 | Y | |
| R-075 | An administrative balance endpoint is out of scope. | none needed | — | — | |

## §5 Errors

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-080 | Every 4xx and 5xx response carries this body: `{ "error": { "code": "insufficient_funds", "message": "human readable, any wording" } }` | body shape on every error check (`err()` asserts code+message string); also unknown routes/methods | ERR-01 and every `err()` call | P | |
| R-081 | 400 `malformed_request` — Unparseable body, or a field of the wrong JSON type | bad JSON on every write endpoint (incl. reset, import, settlements); wrong-typed handles / list / email; null/array/object amounts rejected 4xx | PAY-14 PAY-15 SPL-09 REQ-03 AUT-08 EXP-09 RST-05 SET-12 MDL-03 ROB-01 | P | |
| R-082 | 400 `missing_idempotency_key` — Required `Idempotency-Key` header absent or empty | all five paths, absent and empty | IDM-01 | P | |
| R-083 | 401 `unauthenticated` — Missing, malformed or unknown bearer token | every authenticated endpoint × 5 header variants | ERR-02 | P | |
| R-084 | 403 `forbidden` — Authenticated, but not permitted to touch this resource | wrong party on pay/decline/cancel; non-operator on settlements | REQ-10 REQ-12 REQ-13 SET-01 | Y | |
| R-085 | 404 `not_found` — No such resource, or not visible to this caller | unknown handle / request id; unrelated user acting on a request (403 or 404 accepted) | PAY-13 REQ-10 REQ-14 SET-25 | Y | |
| R-086 | 409 `idempotency_key_reuse` — Key already used by this caller with a different request body | all five paths | IDM-05 IDM-16 IDM-18 IDM-19 IDM-22 | Y (payments, pay) | |
| R-087 | 422 `validation_failed` — A required field or query parameter is missing, or a stated rule is violated with no more specific code | missing fields, rules | PAY-14 REQ-03 SPL-09 SET-11 | Y | |
| R-088 | A field of the correct JSON type with an invalid format or out-of-range value gives 422 `validation_failed`, unless an endpoint specifies a different error. This includes invalid dates, negative counts and values exceeding a stated maximum or length. | range/length cases on amount, note, key, limit, offset, participants | PAY-03 PAY-11 IDM-02 REQ-18 FED-08 SET-13 | Y | LIMIT |
| R-089 | Endpoint-specific field rules take precedence: invalid `amount` values (including strings and booleans), non-string `note` values (including `null`), and any `visibility` other than `public` or `private` are 422 `validation_failed`. | amount `"1000"`/`true`; note `null`/5; visibility variants | MDL-02 PAY-06 PAY-07 SET-13 | Y | |
| R-090 | Omission alone selects the optional-field defaults. Other wrong JSON types follow the rule below. | omitted → defaults; `note:null`/`visibility:null` are errors (not defaults) | PAY-02 PAY-06 PAY-07 SET-05 | P | |
| R-091 | An integer-valued **query parameter** is written as plain decimal digits: `1e9`, `4.0` and `+4` are 422 `validation_failed` whatever their numeric value. | on `/requests` and `/activity` limit/offset | REQ-18 FED-08 | P | |
| R-092 | Reserve 400 `malformed_request` for a body that does not parse or a field of the wrong type. | 400 only for those; value problems 422 | PAY-14 PAY-06 | Y | |
| R-093 | `Idempotency-Key` — 1 to 255 characters, otherwise 422 `validation_failed` | 255 ok / 256 → 422 | IDM-02 | P (10 KB) | LIMIT |
| R-094 | `limit` — integer 1 to 200, otherwise 422 | 0, 201, -1, non-numeric, empty | REQ-18 FED-08 | Y | LIMIT |
| R-095 | `offset` — integer 0 or more, otherwise 422 | -1, non-numeric, empty, `+0`, `1.0` | REQ-18 FED-08 | Y | LIMIT |
| R-096 | Requests must not produce 5xx responses, including under concurrent load. | hostile bodies/paths/headers; 50-way chaos mix | ROB-01 ROB-02 ROB-03 CON-03 | P (a few inputs) | CONC |

## §6 Authentication

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-100 | Authentication supports signup and login. | both endpoints work | AUT-01 AUT-10 | Y | |
| R-101 | Email verification, password reset, refresh tokens and role-management endpoints are out of scope. Permissions specified elsewhere in these requirements still apply. | signup cannot self-grant operator | SET-14 | N | |
| R-102 | `POST /auth/signup` … `->  201  { "user_id": "u_1", "display_name": "Ada", "token": "..." }` | 201 shape, token authenticates | AUT-01 | Y | |
| R-103 | `POST /auth/login` … `->  200  { "user_id": "u_1", "display_name": "Ada", "token": "..." }` | 200 shape for seeded + signed-up | RST-07 AUT-10 | Y | |
| R-104 | Email already registered — 409 `email_taken` | sequential and 12-way concurrent | AUT-05 AUT-12 | N | CONC |
| R-105 | Password shorter than 8 characters — 422 `validation_failed` | 7 → 422, 8 ok, empty | AUT-06 | N | LIMIT |
| R-106 | `email` not of the form `local@domain` — 422 `validation_failed` | six malformed forms | AUT-07 | N | |
| R-107 | Wrong password or unknown email on login — 401 `unauthenticated` | both, plus near-miss password | AUT-09 | N | |
| R-108 | The handle derived from the email (§4) is already taken — 409 `handle_taken`, and no account is created | seeded collision; no account afterwards (login 401, repeat still handle_taken); concurrent distinct emails same handle → one winner | AUT-04 AUT-13 AUT-03 | P | CONC |
| R-109 | Every other endpoint requires a bearer token, except `/health`, `/_test/reset` and the two above. Wallet API endpoints require authentication. | 401 on every authenticated endpoint; exempt ones open (export/import also open per §10) | ERR-02 AUT-11 EXP-14 | P | |
| R-110 | `Authorization: Bearer <token>` | scheme parsing; Basic/empty/garbage → 401 | ERR-02 | P | |
| R-111 | Tokens do not expire. An account may have multiple valid tokens and concurrent sessions. | several tokens from signup + logins valid together; still valid after import | AUT-10 EXP-02 | N | UPGRADE |
| R-112 | Passwords must be stored using a password-hashing function such as bcrypt, scrypt or Argon2, or an equivalent. Plaintext password storage is not permitted. | black-box proxy: neither export nor re-export contains the plaintext; login still works after import | AUT-15 EXP-15 | N | |
| R-113 | (stance) no password hashing under the state lock | hashing burst does not stall payments | CON-15 | N | CONC, TIME |

## §7 Idempotency

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-120 | Five write paths require an idempotency key (§8 and §11): **`POST /payments`**, **`POST /requests`**, **`POST /requests/{id}/pay`**, **`POST /splits`** and **`POST /settlements`**. Everything below applies to each of them independently. | missing key 400 on all five; replay/reuse/concurrency on each | IDM-01 IDM-03 IDM-14 IDM-15 IDM-18 IDM-19 IDM-20..24 | P (4 paths; no settlements) | RETRY |
| R-121 | `Idempotency-Key: <client-chosen string, 1..255 characters>` | 1, 255 char keys; odd characters | IDM-02 | P | |
| R-122 | The key is scoped to **the authenticated user**. Two different users may use the same key string with no interaction between them. | same key, two users, all bodies | IDM-07 IDM-08 IDM-19 | Y | |
| R-123 | A replay means the same user sending the **same method, the same path and the same body**. The same key with the same body on a different path is a different request, not a replay, and must succeed normally. | key reused across `/payments`,`/requests`,`/splits`,pay (also two different request ids) | IDM-09 IDM-10 | Y | |
| R-124 | Header absent or empty — 400 `missing_idempotency_key` | see R-082 | IDM-01 | Y | |
| R-125 | First use of the key — The normal response, **201** | 201 | IDM-03 | Y | |
| R-126 | Replay: same key, same body — **200**, body identical to the original response as a JSON value | 200, `==` original on all five paths | IDM-03 IDM-14 IDM-15 IDM-18 IDM-19 | P | RETRY |
| R-127 | Same key, different body — 409 `idempotency_key_reuse` | amount, handle, note, visibility, extra field | IDM-05 | Y | |
| R-128 | Key reused after the original request failed with 4xx — Treated as a first use | after 409/404/422/self_payment/400/403 | IDM-11 IDM-12 IDM-25 SET-09 SET-15 SET-22 | P (insufficient_funds only) | RETRY |
| R-129 | "Same body" means the same JSON value after parsing — key order and whitespace do not matter. | reordered keys + whitespace replay → 200 | IDM-04 | N | |
| R-130 | For concurrent identical requests with an unused key, exactly one returns 201. The others return 200 with the same body. The operation takes effect only once. | N = 2/10/30 identical payments; also requests, splits, settlements, pay | IDM-20 IDM-21 IDM-22 IDM-23 IDM-24 | N | CONC |
| R-131 | A successful replay returns the original response, even after the resource changes or is cancelled. It makes no further state changes. | replay after balance changed / request cancelled / request paid / balance now insufficient | IDM-13 IDM-14 IDM-15 SET-17 | P | RETRY |
| R-132 | After the body has parsed as a JSON object and the caller is authenticated, an already claimed key is resolved before endpoint field validation or current-resource checks. Thus changing a successful request to an invalid body with the same key still returns `409 idempotency_key_reuse`. | invalid / unknown-handle / self / oversize bodies with a claimed key → 409; pay on paid request with bad visibility → 409 | IDM-06 IDM-17 SET-22 | N | RETRY |

## §8 API

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-140 | `GET /me` → `{ "user_id": "u_ada", "display_name": "Ada", "handle": "ada", "balance": 10000, "currency": "EUR", "minor_units": 2 }` | exact key set and values | API-01 | Y | |
| R-141 | `POST /payments` … `note` is optional and defaults to `""`. `visibility` is optional and defaults to `"public"`. | defaults | PAY-02 | Y | |
| R-142 | 201 payment body: `payment_id, from_user_id, from_handle, to_user_id, to_handle, amount, currency, note, visibility, request_id: null, created_at` | all fields, types, handles, `request_id` null | PAY-01 | Y | |
| R-143 | The caller's balance is below `amount` — 409 `insufficient_funds` | strict `>`; boundary exact balance ok | PAY-08 PAY-09 PAY-10 | Y | |
| R-144 | `amount` below 1, above 1000000000, or not an integer — 422 `validation_failed` | 0, -1, 1e9+1, 2^53, 10.5, strings, bools | PAY-11 MDL-02 | Y | LIMIT |
| R-145 | `to_handle` is the caller's own handle — 422 `self_payment` | and nothing moves | PAY-12 | Y | |
| R-146 | `note` longer than 200 characters — 422 `validation_failed` | 200 ok / 201 → 422; counted in characters (emoji) | PAY-03 PAY-04 | Y | LIMIT |
| R-147 | `visibility` is neither `public` nor `private` — 422 `validation_failed` | case variants, null, numbers, arrays | PAY-07 | Y | |
| R-148 | No user has that handle — 404 `not_found` | unknown, upper-case, oversize | PAY-13 | Y | |
| R-149 | The debit and the credit are one atomic step. A payment is never visible in one wallet and not the other, and a failed payment leaves no trace in either. | failed payment: balances, both feeds unchanged; concurrent mixed load conserves | PAY-08 CON-03 CON-08 | P | PARTIAL, CONC |
| R-150 | `note` is stored and returned verbatim: no trimming, no escaping, no normalisation. Unicode and emoji survive a round trip byte for byte. | whitespace, markup, ZWJ emoji, combining marks, backslashes; both in response and in feed | PAY-05 | Y | |
| R-151 | `POST /requests` … The caller is the requester. 201 request body (`request_id, requester_id, requester_handle, payer_id, payer_handle, amount, currency, note, status:"pending", payment_id:null, created_at`) | shape | REQ-01 | Y | |
| R-152 | `amount` below 1, above 1000000000, or not an integer — 422 `validation_failed` | | REQ-03 | Y | LIMIT |
| R-153 | `payer_handle` is the caller's own handle — 422 `self_request` | | REQ-03 | Y | |
| R-154 | `note` longer than 200 characters — 422 `validation_failed` | | REQ-03 | Y | LIMIT |
| R-155 | No user has that handle — 404 `not_found` | | REQ-03 | Y | |
| R-156 | **The payer's balance is not checked here.** A request for more than the payer holds is created normally and sits `pending`. | | REQ-02 | Y | |
| R-157 | `POST /requests/{id}/pay` … Only the payer may call it. | requester → 403; unrelated → 403/404; operator → 403/404 | REQ-10 SET-18 | Y | |
| R-158 | The body carries `visibility` only, optional, default `"public"`. It is the payer's choice, not the requester's. | default public; private honoured; bad values 422 | REQ-07 | Y | |
| R-159 | **A replay must send the identical body** — `{}` and `{"visibility": "public"}` are different JSON values, so reusing a key across the two is `409 idempotency_key_reuse`, per §7. | both directions, plus private | IDM-16 | Y | RETRY |
| R-160 | Returns `201` with the created **payment**, exactly as `POST /payments` returns one, with `request_id` set to this request. The request becomes `paid` and carries the new `payment_id`. | payment shape; request listing shows paid + payment_id | REQ-05 | Y | |
| R-161 | The request is not `pending` — 409 `request_not_pending` | declined / cancelled / paid, fresh key | REQ-11 IDM-15 | Y | |
| R-162 | The payer's balance is below `amount` — 409 `insufficient_funds` | state unchanged, becomes payable once funded | REQ-09 | Y | |
| R-163 | The caller is not the request's payer — 403 `forbidden` | | REQ-10 | Y | |
| R-164 | Unknown request — 404 `not_found` | incl. oversize / odd ids | REQ-10 ROB-02 | Y | |
| R-165 | Replaying a successful payment returns 200 with its original payment body, including when the request is already `paid`. It moves no additional money and must not return `409 request_not_pending`. | 3 replays after paid | IDM-15 | N | RETRY |
| R-166 | `POST /requests/{id}/decline` — Only the payer. No idempotency key. Returns `200` with the request, `status: "declined"`. | | REQ-12 REQ-15 | Y | |
| R-167 | Declining an already-declined request is `200` with the current state — declining twice is not an error. | | REQ-12 | P | |
| R-168 | A `paid` or `cancelled` request is `409 request_not_pending`. Not the payer is `403 forbidden`. | | REQ-12 | Y | |
| R-169 | `POST /requests/{id}/cancel` — Only the requester. No idempotency key. Returns `200` with the request, `status: "cancelled"`. | | REQ-13 REQ-15 | Y | |
| R-170 | Cancelling an already-cancelled request is `200`. A `paid` or `declined` request is `409 request_not_pending`. Not the requester is `403 forbidden`. | | REQ-13 | Y | |
| R-171 | (race) pay, decline, cancel are mutually exclusive on one request | pay‖cancel, pay‖decline × 8 rounds; money moves iff paid | CON-05 CON-06 | N | CONC |
| R-172 | `GET /requests` — Requests where the caller is the requester or the payer, and no others. Newest first by `created_at`. | scoping; order with spaced creations | REQ-16 REQ-17 | P (order untested) | ORDER, TIME |
| R-173 | `direction` is `incoming` (the caller is the payer), `outgoing` (the caller is the requester) or absent for both. | | REQ-16 | Y | |
| R-174 | `status` is one of the four statuses, or absent for all. | | REQ-16 | Y | |
| R-175 | `limit` defaults to 50, range 1 to 200. `offset` defaults to 0 and must be 0 or more. Outside either range is 422 `validation_failed`. An unknown `direction` or `status` value is also 422. | | REQ-16 REQ-18 | Y | LIMIT |
| R-176 | `has_more` is true when items exist beyond the last one returned. | exact-fit page false; short page; beyond end | REQ-17 | P | |
| R-177 | `{ "requests": [ { ...request... } ], "has_more": false }` | envelope | REQ-17 REQ-16 | Y | |
| R-178 | `POST /splits` … Splits an amount the caller already paid, and asks each of the other participants for their share by creating one `pending` request each. | no money moves; requests pending | SPL-01 SPL-08 | Y | |
| R-179 | The caller may be included in `participant_handles` or omitted. | first / middle / omitted | SPL-05 | P | |
| R-180 | Shares follow the equal-split rule in §9, in the order the handles are given. | | SPL-02 SPL-04 | Y | ROUND, ORDER |
| R-181 | **A request is created for every participant except the caller**, each for that participant's share, with the caller as requester. | requests list, order, requester, amounts | SPL-01 SPL-05 | Y | |
| R-182 | Split 201 body: `split_id, amount, currency, note, shares, requests, created_at` | shape | SPL-01 | Y | |
| R-183 | `shares` covers every participant including the caller, in the order given, and always sums to `amount`. `requests` covers every participant except the caller, in the same order. | | SPL-01 SPL-03 SPL-05 | Y | ROUND |
| R-184 | `amount` below 1, above 1000000000, or not an integer — 422 `validation_failed` | | SPL-09 | Y | LIMIT |
| R-185 | `participant_handles` empty, or containing a duplicate handle — 422 `validation_failed` | | SPL-09 SPL-10 | Y | |
| R-186 | `note` longer than 200 characters — 422 `validation_failed` | | SPL-09 | Y | LIMIT |
| R-187 | Any handle is unknown — 404 `not_found` | and no requests created for the valid ones (all-or-nothing) | SPL-09 | P | PARTIAL |
| R-188 | A split whose only participant is the caller is **valid**: it computes one share, creates zero requests, and returns `"requests": []`. | | SPL-06 | Y | |
| R-189 | Nothing about a split checks anyone's balance. | broke caller + broke participants, amount 1e9 | SPL-08 | Y | |
| R-190 | `GET /activity` — Payments visible to the caller by the feed contract in §4, newest first by `created_at`. | spaced payments newest first | FED-06 FED-01 | P (order) | ORDER, TIME |
| R-191 | `{ "payments": [ { ...payment... } ], "has_more": false }` | envelope; empty feed | FED-09 FED-10 | Y | |
| R-192 | The relative order of two payments created within the same second is unspecified. Stable pagination during concurrent writes is not required for this endpoint. | (no check — we avoid asserting same-second order; paging check is sequential) | FED-07 | — | ORDER |
| R-193 | `limit` and `offset` behave exactly as in `GET /requests`. | paging, has_more, validation | FED-07 FED-08 | Y | LIMIT |

## §9 Money and rounding

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-200 | Shares must be whole minor units, sum exactly to `amount` and differ by at most one minor unit. | property over amounts 1..40 × n 1..7 and 999999999/1e9 | SPL-03 | P | ROUND |
| R-201 | When the amount does not divide evenly, the larger shares go to the first participants in `participant_handles` order. | remainder to earliest, incl. caller position | SPL-02 SPL-03 SPL-05 | Y | ROUND |
| R-202 | table: 1000/3 → 334,333,333 · 1/3 → 1,0,0 · 10/3 → 4,3,3 · 999/3 → 333,333,333 · 5/5 → 1,1,1,1,1 | all five rows verbatim | SPL-02 | P | ROUND |
| R-203 | Splitting the same amount among the same people in a different `participant_handles` order gives the extra unit to a different person. | | SPL-04 | Y | ROUND, ORDER |
| R-204 | A share of `0` is legal and still produces a request for that participant. | amount-0 requests created and listed | SPL-07 | Y | ROUND |
| R-205 | Each split's shares are independent of previous splits. | 4 identical splits → identical shares | SPL-13 | N | ROUND |
| R-206 | After any number of splits have been paid in full, wallet balances must still sum exactly to the seeded total. | pay every non-zero request of several splits; conserve; caller received sum of others' shares | SPL-12 CON-14 | N | ROUND, PARTIAL |

## §10 Export and import

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-210 | The service must support `GET /_test/export` and `POST /_test/import`. Like reset, these are unauthenticated test endpoints. | no token needed | EXP-01 EXP-14 | P | |
| R-211 | Exports may contain credentials and session tokens; handle them as private test artifacts. | (handling guidance; evidence files contain no exports) | — | — | |
| R-212 | Return 200 from export with a JSON object containing `track: "pocketful"`, `format_version: 1` and `state` (an implementation-defined JSON object). | | EXP-01 | P | |
| R-213 | The state format is opaque to the caller and must be accepted unchanged by import. | export → import verbatim | EXP-02 EXP-03 | P | UPGRADE |
| R-214 | Import takes that entire object and atomically replaces the service's state, returning 204. | 204 empty body; replacement not merge | EXP-14 EXP-04 | P | UPGRADE |
| R-215 | It must accept an unchanged export produced by this service. | after arbitrary later activity | EXP-02 EXP-05 | P | UPGRADE |
| R-216 | No dependency on the source process, files, volume, port or network address is allowed. | import into a world reset to a different fixture / currency (proxy; a real second process needs the docker script) | EXP-04 EXP-17 DOC-05 | N | UPGRADE |
| R-217 | Import is replacement, not merge; repeating it restores the exported state without duplicating anything. | 3× import, snapshots equal | EXP-03 EXP-13 | N | UPGRADE |
| R-218 | Invalid JSON follows §5; missing fields, wrong track/version or an invalid state give 422 `validation_failed` without changing the destination. | 19 invalid envelopes; garbage → 400; damaged state parts | EXP-08 EXP-09 EXP-10 | N | PARTIAL |
| R-219 | Test control calls have a 10-second timeout. | timeouts enforced on export/import/reset; ~360 payments | LIM-05 LIM-03 | N | TIME |
| R-220 | Export is an atomic, read-only snapshot; subsequent source writes do not change it. | no side effects; older export restores older world; exports under 8 writer threads are internally consistent | EXP-11 EXP-12 | N | CONC |
| R-221 | Preserve accounts and hashed-password login, existing bearer tokens, currency, balances, payments, requests, permissions, all completed idempotent request bodies and original responses. | full snapshot equality (me, feed, requests) for 8 users; tokens; password login; replays of all 5 paths | EXP-02 EXP-06 EXP-15 | N | UPGRADE |
| R-222 | Identities, timestamps and monetary records must not be regenerated or replayed against an already-net balance. | feed objects equal incl. ids/timestamps; balances not double-applied; new ids never collide | EXP-02 EXP-05 EXP-13 | N | UPGRADE |
| R-223 | Failed request keys remain reusable. | key of a 409 before export reusable after | EXP-02 | N | UPGRADE, RETRY |
| R-224 | Existing receipts, tokens and retries must remain valid after import; replacing the state with a fresh fixture does not satisfy this requirement. | replays 200 identical; tokens work; a post-export key is first-use again | EXP-02 EXP-06 | N | UPGRADE |
| R-225 | Import removes all previous destination data and credentials. | destination-only users/tokens gone | EXP-04 | N | UPGRADE |
| R-226 | Reset clears all state, including imported state. | reset after import | EXP-07 | N | UPGRADE |
| R-227 | State need not survive an abrupt container restart. | nothing required | — | — | |

## §11 Atomic net settlements

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-230 | The reset fixture may include `settlement_operator_ids`, an array of user ids, default []. | default empty ⇒ nobody operates; listed ids operate | SET-02 | N | |
| R-231 | An operator may execute a settlement across any wallets. | operator not party to transfers; signed-up users can be parties | SET-19 SET-20 | P | |
| R-232 | This permission does not grant access to another user's requests or private activity items. | operator sees no others' requests/private payments, cannot pay/decline/cancel | SET-18 REQ-14 FED-03 | N | |
| R-233 | `POST /settlements` requires an operator and an idempotency key. No token gives 401; authenticated non-operator gives 403 `forbidden`. | | SET-01 SET-03 IDM-01 | P | |
| R-234 | `transfers` contains 1..32 objects. | 0, 33 → 422; 1 and 32 ok; wrong shapes | SET-11 | N | LIMIT |
| R-235 | Each uses ordinary payment amount, note and visibility rules (defaults: empty note, public). | amount range/type, note 200, visibility; defaults | SET-13 SET-05 | N | LIMIT |
| R-236 | Unknown handle is 404; self-transfer is 422 `self_payment`; malformed batch shape is 422 `validation_failed`. | | SET-25 SET-11 | N | |
| R-237 | Entry errors take precedence in input order, before insufficient funds. | mixed error orderings | SET-25 | N | ORDER |
| R-238 | Unknown fields are ignored. | on body, transfer entries | SET-13 | N | |
| R-239 | A settlement is affordable when every wallet's balance after all incoming and outgoing transfers is nonnegative. | net rule: pass-through, cycles, exact zero, −1 | SET-07 SET-08 SET-10 | N | PARTIAL |
| R-240 | Insufficient collective funds gives 409 `insufficient_funds`. | | SET-09 | N | |
| R-241 | Either all movements commit together or none do; failed validation claims no idempotency key and creates no payment or revision. | no payments, balances unchanged, key reusable; observer never sees a partial batch | SET-09 SET-15 CON-10 CON-11 | N | PARTIAL, CONC |
| R-242 | Return 201 with `settlement_id`, `committed_at` and `payments` in input order. | | SET-04 | N | ORDER |
| R-243 | Every member is an ordinary payment with `settlement_id` linking the batch; nonmembers expose null for that field. | members carry id; direct payments and feed items null; settlements leave requests alone (request_id null) | SET-04 SET-06 SET-21 FED-10 | N | |
| R-244 | Members have null request_id and the same server-assigned created_at, equal to committed_at. | all 20 members equal; feed copy equal | SET-04 SET-16 | N | TIME |
| R-245 | Constituents follow ordinary activity-feed visibility. | private member hidden from third party/operator, visible to its parties | SET-06 | N | |
| R-246 | The settlement response contains every member's receipt. | feed objects == receipts | SET-04 SET-06 | N | |
| R-247 | Replays return 200 with the original complete response. This is the fifth idempotent write path in stage 1. | | IDM-19 IDM-23 SET-17 | N | RETRY, CONC |
| R-248 | A reset/import must preserve settlement operator permissions, original payments, requests, settlement membership and retry responses. | operator after import (even after reset to no operators); membership; replay | EXP-02 EXP-06 | N | UPGRADE |
| R-249 | (competition) settlements and direct payments racing for one wallet | exactly funds/amount succeed, no negative | CON-09 | N | CONC |

## Stance requirements (from the handoff)

| Row | Requirement | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-260 | integer minor units only | JSON numbers integral everywhere, no `.0` in output | MDL-04 | N | |
| R-261 | per-user idempotency records (fingerprint + full original response) | replay equal to original even when state differs | IDM-13 IDM-14 IDM-15 | P | RETRY |
| R-262 | concurrent identical requests with fresh key move money once | | IDM-20 | N | CONC |
| R-263 | request moves money at most once | | CON-04 CON-05 CON-06 | N | CONC |
| R-264 | ≥50 concurrent connections | | LIM-01 LIM-02 | P | CONC |
| R-265 | start <60s; no runtime network | | DOC-02 DOC-03 | D | TIME |
| R-266 | no password hashing under the state lock | | CON-15 | N | CONC |

## Counts

Generated by `python3 check_matrix.py`:

- matrix rows: **214**
- shipped harness exercises fully (Y): 88 · partly (P): 52 · **not at all (N): 58** · deliverable/docker-only (D): 9 · not testable (—): 7
- acceptance checks: **192** HTTP checks (`run_checks.py`) + 5 deployment checks DOC-01..05 (`docker_run_checks.sh`)
- self-validation: all 192 pass against the analyst's reference reading (`selftest/refimpl.py`); all 15 injected defects (`selftest/mutate.py`) are caught.
