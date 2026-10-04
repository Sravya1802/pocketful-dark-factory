# Pocketful stage 2 — coverage matrix

Source: the stage-2 requirements handed over by @coordinator (parts 1–3), on top of the stage-1 requirements (accepted at `621342c`,
matrix and 192 checks in `../stage-1/`, re-runnable against stage 2 with `run_checks.py --include-stage1`).
One row per requirement sentence. Check ids are the `[ID]` tags in the first line of each check's docstring:
`ME AZ AX AC NEG UPG` = API checks (`run_checks.py`, stdlib), `UI` = browser checks (`run_ui_checks.py`, Playwright). `check_matrix.py` verifies ids both ways.

**Shipped?** — does the shipped stage-2 harness (31 tests: 1 API-ish import test, 1 hold test, 29 UI) appear to exercise this requirement, judged
from test names only? `Y` yes · `P` partly · `N` **not exercised — these rows matter most** · `M` manual only (checklist) · `—` not testable.
**Risk**: CONC concurrency · RETRY retries/idempotency · PARTIAL partial failure · ORDER ordering · TIME clock/expiry · ROUND rounding · LIMIT limits · UPGRADE earlier data.

## Screens and routes

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| S-001 | Users can manage payments, requests and bill splits in a browser. | pay, request, split, requests screen all work end to end | UI-30 UI-65 UI-71 UI-83 | Y | |
| S-002 | They can also reserve money for a recipient to collect later, in one or more captures. | authorise, capture (one or many), void through API and UI | AZ-20 AZ-24 UI-92 UI-97 | P (one hold) | |
| S-003 | The following screens must be reachable by URL. Other screens must be reachable through the UI. | all six routes load by URL; `/authorizations` reachable by link from every screen | UI-01 UI-02 UI-03 NEG-01 | P (routes by URL only) | |
| S-004 | `/` — Balance, pay form, request form and the activity feed | testids of all four blocks present on `/` | UI-02 | Y | |
| S-005 | `/requests` — Incoming and outgoing requests, with pay, decline and cancel | `incoming-list`, `outgoing-list`, buttons | UI-02 UI-70 UI-71 | Y | |
| S-006 | `/split` — Split form | form + preview | UI-02 UI-80 | Y | |
| S-007 | `/signup` — Signup · `/login` — Login | forms reachable signed out | UI-01 | Y | |
| S-008 | The browser and the API share `/requests`. Return the UI for `Accept: text/html`; API requests without that header receive JSON. | HTML for browser Accept (with and without bearer), JSON for none / `application/json` / `*/*`, writes stay JSON, JSON 401 shape | NEG-01 NEG-02 NEG-03 NEG-04 | N | |
| S-009 | The UI must expose the `data-testid` attributes listed below for integration testing. | every listed testid exercised somewhere (see rows below) | UI-02 and flow checks | Y | |
| S-010 | Additional elements are permitted, and the visual implementation is the team's choice subject to the product-quality requirements below. | nothing asserted against extra elements | — | — | |

## Product and visual direction

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| P-001 | The browser experience must feel like a coherent, presentation-ready consumer finance product, not a test harness with controls attached. Aim for a calm, trustworthy character. | measurable proxies: primary action distinct, no raw ids/JSON/undefined, no console errors; the rest is human review | UI-132 UI-133 UI-131 manual-checklist.md | N | |
| P-002 | Available funds must be the clearest monetary value once holds exist, with total and held funds visibly secondary. | font size/weight/reading order: available > total, held | UI-13 UI-134 | N | |
| P-003 | Payments, requests, splits and authorisations should be easy to scan, and status, direction, privacy and money movement should be understandable without interpreting raw API data. | status/direction/privacy shown as words; no raw ids/ISO/JSON in text | UI-132 manual-checklist.md | N | |
| P-004 | Use a consistent visual system for typography, spacing, colour, controls and feedback. Primary actions must be easy to identify. | primary vs secondary styling differs; consistency is manual | UI-133 manual-checklist.md | M | |
| P-005 | Available, held, pending, loading, successful, refused and uncertain states must be visually distinct as well as satisfying the behavioural requirements below. | refused ≠ uncertain style; available ≠ held style; pending busy state; loading indication | UI-36 UI-13 UI-35 UI-135 | N | |
| P-006 | Format people, amounts and timestamps for people first; expose technical identifiers only where they help the user. | no raw ids / ISO timestamps in visible text (except required `authorization-expires`) | UI-132 | N | |
| P-007 | The required flows must remain clear and usable at a 375 CSS-pixel viewport and at conventional desktop widths, without horizontal page scrolling. | scrollWidth ≤ innerWidth and no element poking out, on every route, signed in/out, with long notes/handles/huge amounts/40 items/error+uncertain+preview open; 375 / 768 / 1280 | UI-120 UI-121 UI-122 UI-123 | N | LIMIT |
| P-008 | Inputs need visible labels, keyboard focus must be apparent, and text and controls need sufficient contrast. | every input has a visible `<label>`; Tab through every control and require an indicator; contrast ≥ 4.5 (3 large) incl. error and uncertain states | UI-126 UI-125 UI-127 | N | |
| P-009 | Provide considered empty, loading and error states, and keep navigation consistent across the required routes. | `empty-*` elements; loading indication on slow reads; understandable failure when reads fail; identical nav links on all routes | UI-63 UI-75 UI-99 UI-135 UI-136 UI-03 | P (empty only) | |
| P-010 | A custom illustration, brand asset or exact visual match to a reference is not required. | — | — | — | |
| P-011 | Nothing at runtime reaches the network: no CDN fonts/scripts/images; everything bundled in the image (system font stack or bundled fonts). (handoff + stage-1 §2) | static scan of served HTML/CSS/JS for external origins; browser records every request's origin on every route | NEG-05 UI-130 | N | |
| P-012 | responsive viewport declared, document language, titles (accessibility basics) | `meta viewport`, `html[lang]`, non-empty `<title>` | UI-124 | N | |
| P-013 | phone input zoom (usability at 375 px) | inputs ≥ 16 px at 375 | UI-123 | N | |

## Signup and login

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| A-001 | `signup-email`, `signup-password`, `signup-display-name` — Inputs · `signup-submit` — Button | signup works through these | UI-01 UI-05 | Y | |
| A-002 | `login-email`, `login-password`, `login-submit` — Inputs and button | login works | UI-01 UI-06 | Y | |
| A-003 | `auth-error` — Error message. Present only when there is one | absent initially and after success; present on bad login, duplicate email, short password, bad email | UI-01 UI-07 | P (bad login) | |
| A-004 | `current-user` — Visible on every screen when signed in. Text contains the display name | on all five/six routes after signup and login; absent when signed out | UI-02 UI-05 UI-06 UI-04 | P | |
| A-005 | `current-handle` — Text is exactly the caller's handle, with no `@` and no surrounding words | `grace_hopper`, `ada`, `bob` exactly (derived handle from signup) | UI-02 UI-05 UI-06 | P | |
| A-006 | `logout-button` — Button | logs out; signed-out state | UI-06 | Y | |

## Balance and pay — `/`

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| B-001 | `wallet-balance` — Text is exactly the formatted amount. Carries `data-amount="{minor units}"` | text and attribute, all currencies, zero, tiny, large | UI-10 UI-11 | P | |
| B-002 | `pay-handle`, `pay-amount`, `pay-note` — Inputs. `pay-amount` is a **decimal** string as a person would type it, e.g. `15.00` | typing decimals works | UI-20 UI-30 | Y | |
| B-003 | `pay-visibility` — Selects `public` or `private`. Option values are those two strings | both option values; chosen value reaches the payment | UI-30 UI-31 UI-60 | P | |
| B-004 | `pay-submit` — Button | | UI-30 | Y | |
| B-005 | `pay-error` — Error message, when the payment is refused — including insufficient funds | insufficient, unknown handle, own handle, bad decimal; absent when not refused | UI-32 UI-21 UI-33 | Y | |
| B-006 | `request-handle`, `request-amount`, `request-note`, `request-submit` — The request form | creates a request; decimals | UI-65 UI-24 | N | |
| B-007 | `request-error` — Error message, when the request is refused | unknown handle, self, bad decimal | UI-65 UI-24 | N | |
| B-008 | Keep the pay form's values after success. | all four inputs unchanged after success | UI-30 | Y | |
| B-009 | Submitting it again without changing a field must not send another payment: `wallet-balance` falls once, the feed contains one payment and `pay-error` is absent. | double/triple click after success: one payment, balance once, no error | UI-30 | Y | RETRY |
| B-010 | Changing a field makes the next submission a new payment request. | each of note / amount / visibility / handle changes → new key, new payment | UI-31 | P | RETRY |
| B-011 | Retries follow §7. | same key + same body on retry (see uncertain) | UI-40 UI-42 | N | RETRY |
| B-012 | **Formatted amount.** `wallet-balance` is the decimal with exactly `minor_units` decimal places, a single space, then the currency code: `100.00 EUR`. | EUR/BHD | UI-11 | Y | |
| B-013 | For a `minor_units` of `0` there is no decimal point at all: `1200 JPY`. | JPY `1200 JPY`, `0 JPY`, `7 JPY` | UI-11 | Y | |
| B-014 | Balances are never negative, so there is no sign. | no `-`, no grouping separators (`1234567.89 EUR`) | UI-11 | N | |
| B-015 | The form accepts decimal amounts and submits minor units to the API. With `minor_units: 2`, `15.00` and `15` both submit `1500`; `15.5` submits `1550`. | request bodies captured: 1500 / 1500 / 1550; float traps 0.29→29, 1.15→115, 4.35→435, 8.2→820, 0.07→7 | UI-20 | P | ROUND |
| B-016 | Nonnumeric input or more than `minor_units` decimal places must show the form's error element without sending a request. | 11 invalid strings: `pay-error`, zero POSTs, balance unchanged; JPY `15.5`, BHD `1.2345`; same on request/split/authorize/capture | UI-21 UI-23 UI-24 UI-93 | P (15.005 only) | LIMIT |
| B-017 | For example, `15.005` is rejected rather than rounded. | | UI-21 UI-24 | Y | ROUND |
| B-018 | (amount 0) | refused (client or server), nothing moves | UI-22 | N | LIMIT |

## Activity feed — `/`

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| F-001 | `activity-list` — Container. Its children are newest first in the DOM | order with spaced payments; new payment first | UI-61 UI-64 | Y | ORDER |
| F-002 | `activity-item-{payment_id}` — One per visible payment. Carries `data-visibility="public"` or `data-visibility="private"` | | UI-60 UI-62 | Y | |
| F-003 | `activity-parties-{payment_id}` — Text contains both handles | | UI-60 | Y | |
| F-004 | `activity-amount-{payment_id}` — Text is exactly the formatted amount | EUR/JPY/BHD | UI-12 UI-60 | P | |
| F-005 | `activity-note-{payment_id}` — Text is exactly the note. Present even when the note is empty | emoji, markup, double spaces verbatim, never rendered as HTML; empty note element exists | UI-60 | P | |
| F-006 | `empty-activity` — Shown instead of the list when nothing is visible | and absent after something arrives | UI-63 | Y | |
| F-007 | Two payments with equal timestamps may appear in either order. | (no assertion on same-second order) | UI-61 | — | ORDER |
| F-008 | privacy rule in the browser (stage-1 §4 feed contract) | private between others not rendered for a third party (also not in page source); receiver sees it | UI-62 | Y | |

## Requests — `/requests`

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| R-001 | `incoming-list`, `outgoing-list` — Containers | items sit in the right list per user; stranger sees none | UI-70 | P | |
| R-002 | `request-item-{request_id}` — One per request. Carries `data-status="{status}"` | all four statuses | UI-70 UI-71 | Y | |
| R-003 | `request-amount-{request_id}` — Text is exactly the formatted amount | | UI-70 | P | |
| R-004 | `request-pay-{request_id}` — Button. Present only on a `pending` incoming request | present for pending incoming; absent on outgoing, paid, declined, cancelled | UI-70 UI-71 | P | |
| R-005 | `request-decline-{request_id}` — Button. Present only on a `pending` incoming request | | UI-70 UI-71 | P | |
| R-006 | `request-cancel-{request_id}` — Button. Present only on a `pending` outgoing request | | UI-70 UI-71 | P | |
| R-007 | `request-error` — Shown when a pay, decline or cancel is refused | insufficient funds; stale pay; stale decline | UI-72 UI-73 UI-74 | P (pay w/o funds) | |
| R-008 | `empty-requests` — Shown when both lists are empty | and absent when not | UI-75 | Y | |

## Split — `/split`

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| X-001 | `split-amount` — Decimal input, same rule as `pay-amount` | `10.005`, `abc`, empty rejected | UI-24 UI-84 | N | |
| X-002 | `split-handles` — Text input: handles separated by commas, in order | whitespace trimmed; order drives remainder | UI-80 UI-85 | Y | ORDER |
| X-003 | `split-note`, `split-submit` — Input and button | creates requests with the note | UI-83 | Y | |
| X-004 | `split-preview` — Shows the computed shares before submitting. Contains one `split-share-{handle}` per participant | one element per participant incl. the caller when listed; nothing POSTed during preview | UI-80 UI-81 UI-85 | P | |
| X-005 | `split-share-{handle}` — Text is exactly the formatted share amount | | UI-80 UI-81 | Y | ROUND |
| X-006 | `split-error` — Error message, when the split is refused | unknown handle, duplicate, empty, bad decimal; nothing created | UI-84 | P (unknown) | |
| X-007 | `split-preview` must show the shares the server would compute, by the rule in `stage-1.md` §9, before anything is posted. | 10.00/3 → 3.34,3.33,3.33; 0.01/3 → 0.01,0.00,0.00; 0.05/5; 9.99/3; 1e9 minor; caller listed; JPY and BHD | UI-80 UI-81 UI-82 | P (one) | ROUND |
| X-008 | The preview and submitted split must have identical shares. | UI preview vs API requests | UI-83 | P | ROUND |
| X-009 | After any successful action, the balance, the feed and the request lists on the same page must show the new state without a manual reload. | pay → balance + feed; request actions → list; authorize/capture/void → list + wallet | UI-64 UI-71 UI-92 UI-94 UI-97 | P | |
| X-010 | Navigation must wait for the write to succeed before it refreshes the data. Any mechanism is fine, including a full navigation. | (observed through the state checks above; no separate assertion) | UI-64 | — | |
| X-011 | **There is no live-update requirement here** — another client may change state, but this browser need only refresh after its own action or an explicit refresh. | no polling is expected; nothing asserted on idle pages | — | — | |

## Competing clients and uncertain outcomes

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| C-001 | Add `wallet-refresh`, a button on `/` that refreshes the balance and feed without clearing the pay form. | other client's payment appears; all pay inputs kept | UI-50 | P | |
| C-002 | **Latest refresh wins:** a delayed earlier read must not overwrite a later refresh, including when responses arrive out of order. | R1's `/me` + `/activity` held; other client changes state; R2 shows new state; R1 released → UI unchanged (balance, available, held, feed). Skipped with a reason if the UI does no browser-side reads. A lone slow refresh is still applied | UI-52 UI-53 | N | CONC, TIME |
| C-003 | Another client may spend the balance after this browser reads it. A refused payment shows `pay-error`, refreshes the balance/feed, and preserves all pay inputs. | other client drains; pay refused; balance and feed refreshed; inputs preserved | UI-34 UI-32 | Y (error shown) / N (refresh, preserved) | CONC |
| C-004 | A request cancelled elsewhere while its pay button is visible must show `request-error` when payment is refused and refresh the request list so the stale pay button disappears. | pay on cancelled-elsewhere → `request-error`, `data-status=cancelled`, button gone | UI-73 | N | CONC |
| C-005 | If a payment response is lost, including after `POST /payments` commits, show `pay-uncertain` (nonempty text), not `pay-error`. | lost after commit; lost before commit; no `pay-error` | UI-40 UI-41 | N | RETRY |
| C-006 | Keep the unchanged form retryable with the **same key and body**. | retry request carries the identical `Idempotency-Key` and JSON body; also after 3 consecutive losses; changing a field uses a new key | UI-40 UI-42 UI-43 | N | RETRY |
| C-007 | Successful retry removes both error/uncertainty elements, refreshes the balance and feed, and moves money exactly once. | elements gone, balance/feed refreshed, one payment in the API | UI-40 UI-41 UI-42 | N | RETRY |
| C-008 | Unknown outcomes are not confirmed rejections. | uncertain ≠ refused (text and style); a real refusal on retry replaces uncertainty with `pay-error` | UI-36 UI-44 | N | RETRY |
| C-009 | No background polling, live synchronization, or recovery across page reloads is required. | nothing asserted | — | — | |
| C-010 | The same balance refresh rules apply to the available and held amounts introduced below. | refresh updates `wallet-available`/`wallet-held`; latest-wins covers them | UI-51 UI-52 | N | |
| C-011 | (stale authorization controls — same pattern as requests) | capture of a hold voided elsewhere / void of a hold captured elsewhere → `authorization-error` + refreshed list | UI-95 UI-96 | N | CONC |

## Existing clients after an upgrade

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| U-001 | A stage-2 service must accept an export produced by the same team's stage-1 service. | stage-1 export (from `STAGE1_URL`) imports: tokens, balances, `available == total`, `held == 0`, replays, pending request, failed key, operators, feed | UPG-10 | P (one import test) | UPGRADE |
| U-002 | A browser signed in before that export/import upgrade must remain signed in afterwards. | import between browser requests: same page, same token, still works | UI-110 UI-112 UI-113 UPG-10 | N | UPGRADE |
| U-003 | Existing pending requests remain payable through the request screen. | pending `rq_1` payable after import | UI-112 UPG-10 | N | UPGRADE |
| U-004 | A payment whose response was lost before export remains retryable after import with the same body and key; the UI must recover the original payment and refresh the imported balance. | committed-then-lost: retry after import replays → original payment, balance once; never-committed variant creates once; API-level replay across stage 1 → 2 | UI-110 UI-111 UPG-11 UPG-12 | N | UPGRADE, RETRY |
| U-005 | These requirements apply when import completes between browser requests; migration during an in-flight request is not required. | imports are issued while the page is idle | UI-110 | — | |
| U-006 | No page reload or new screen is required. The form and pending retry identity must survive the upgrade. | form values and retry key survive; no reload performed | UI-110 | N | UPGRADE |
| U-007 | (stage-2 → stage-2 export/import with holds) | holds, partial captures, expiry instants, captures' replays survive; expired-while-exported stays expired | UPG-01 UPG-02 UPG-03 UPG-04 UI-114 | N | UPGRADE, TIME |

## Authorizations and captures — model and invariants

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| Z-001 | A payment may be **authorised** now and **captured** later, for the full amount or less. | create then capture full / partial | AZ-20 AZ-22 | P | |
| Z-002 | An authorisation places a *hold* on the payer's wallet: it reserves money without moving it. | held/available change, totals do not | ME-02 AZ-06 | Y | |
| Z-003 | Capturing moves the money; a final capture also releases whatever was not captured. | balances move; remainder released in the same step | AZ-20 AZ-22 AZ-25 | N | |
| Z-004 | Nonfinal captures keep the remainder held. | `final:false` keeps the remainder | AZ-24 | N | |
| Z-005 | An open authorisation expires and releases its remainder on its own. | clock expiry without any request | AX-10 AX-11 | N | TIME |
| Z-006 | 1. The sum of all wallet `total` values always equals the total seeded by the last reset. A hold moves no money; payments, settlements and captures transfer money between wallets. | sums after every sequence; per-read | AZ-06 AZ-20 AC-11 | N | CONC |
| Z-007 | 2. `available = total − held` must never be negative. Held funds cannot fund new payments, authorizations or settlement net debits. Captures may spend the money reserved for them. | observer on every read under load; every spending path refuses held funds; capture at available 0 | AZ-07 AZ-08 AZ-09 AZ-33 AC-07 AC-08 AC-09 AC-11 | P (hold test) | CONC |
| Z-008 | 3. Cumulative captures must not exceed the authorized amount. Each idempotent capture moves money once. A closed hold cannot be captured again. | 30-way nonfinal captures; mixed sizes; double capture; replay | AC-05 AC-06 AC-01 AC-02 AZ-23 AZ-34 | N | CONC |

## Existing API changes

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| E-001 | `GET /me` keeps `balance`, and `balance` **equals `total`**. `available` and `held` are new fields beside it. | all fields, equality | ME-01 ME-02 AC-11 | P | |
| E-002 | With no open holds, `balance`, `total` and `available` agree and `held` is zero, and every earlier behaviour is unchanged. | + whole stage-1 suite re-run | ME-01 ME-03 `--include-stage1` | N | UPGRADE |
| E-003 | `POST /payments` remains an immediate transfer. It must not leave an intermediate hold or require a separate capture. | no hold, no authorization record, `authorization_id: null` | ME-03 ME-04 | N | |
| E-004 | Every `409 insufficient_funds` in stage 1 — on `POST /payments`, `POST /requests/{id}/pay` and settlements — is now evaluated against `available`. | all three + authorizations, exact-available boundary, pass-through netting | AZ-07 AZ-08 AC-09 AC-10 UI-33 | P | |
| E-005 | With no open holds, the result is unchanged. | stage-1 suite | `--include-stage1` | — | |
| E-006 | Paying a request remains immediate. Authorizing a request is out of scope. | pay moves money at once; `authorization_id` null | ME-03 | N | |
| E-007 | `POST /splits` is unchanged. | stage-1 suite (SPL-xx) | `--include-stage1` | — | |
| E-008 | There are now seven idempotent write paths: stage 1's five, authorizations and captures. The same replay rules apply independently to each. | missing key on all seven; one key across all seven paths independent; replay / reuse / fresh-key concurrency on the two new ones | NEG-06 NEG-07 AZ-10 AZ-11 AZ-35 AZ-37 AC-02 AC-03 | N | RETRY, CONC |
| E-009 | Payments created without an authorisation carry `authorization_id: null`; their existing `request_id` semantics are unchanged. | key present and null in create response, request-pay response, feed | ME-03 AZ-32 | N | |

## Model (fixture)

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| M-001 | `authorization_ttl_seconds` applies to every authorisation created through the API. It defaults to 600 when omitted. | 600 default, 3600 custom, 900 with seeded alongside, 2 s | AZ-01 AX-05 AX-06 AX-10 | N | |
| M-002 | If supplied, it must be a positive integer number of seconds. | 0, −1, −600, 1.5, `"600"`, true, false, [], {} → 422 on reset, nothing changed | AX-05 | N | LIMIT |
| M-003 | Seeded authorisations carry their own absolute `expires_at` instead. | seeded expiry not shifted by ttl | AX-06 AX-15 | N | TIME |
| M-004 | A user's seeded `balance` is still `total`. **`available` is derived, never seeded** — the service subtracts the seeded open holds itself. | `/me` after reset with holds; headline in UI; seeded holds can be captured and voided | AX-01 AX-02 UI-13 | P | |
| M-005 | A sum of seeded unexpired open holds larger than that user's `balance` is a reset error: `422 validation_failed` from `POST /_test/reset`, changing nothing, exactly like a negative seeded balance. | over by 1, over across two holds, equal OK; world and tokens untouched | AX-03 | N | PARTIAL |
| M-006 | Seeded `status` is `open`, `captured`, `voided` or `expired`. Only `open` holds anything. | captured/voided/expired seeds hold nothing, excluded from oversubscription | AX-04 | N | |
| M-007 | An earlier fixture may omit `authorizations` altogether; omission means an empty list. | reset also clears holds and their keys | AX-01 AX-07 | N | |
| M-008 | An authorization whose `expires_at` is at or before now is `expired` and holds no funds. | seeded open-but-past → expired, not counted | AX-04 UI-100 | N | TIME |
| M-009 | Reads and writes must reflect expiry even if no request occurred at the deadline. | idle sleep past the deadline then first touch = list (either party), `/me`, a payment needing the funds | AX-10 AX-11 AX-13 UI-101 | N | TIME |
| M-010 | `GET /authorizations` must show `status: "expired"`, and `GET /me` must include the released remainder in `available`. | | AX-10 AX-12 | N | TIME |
| M-011 | Seeded expiry times are at least an hour from reset time, in the past or future; newly created authorizations may have shorter lifetimes. | seeded ±2 h; API ttl 1–5 s | AX-04 AX-10 AX-14 AX-15 | N | TIME |

## API — `GET /me`, `POST /authorizations`

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| H-001 | `balance` and `total` are always equal. `held` is the sum of open holds, and `available` is `total − held`, never negative. | on every poll under load | ME-02 AC-11 AZ-53 | N | CONC |
| H-002 | `POST /authorizations` — `Idempotency-Key` is required. The caller is the payer. | | AZ-10 NEG-06 | N | |
| H-003 | `note` and `visibility` are optional with the same defaults as `POST /payments`. | `""` / `public` | AZ-01 | N | |
| H-004 | 201 body: `authorization_id, from_user_id, from_handle, to_user_id, to_handle, amount, captured_amount: 0, currency, note, visibility, status: "open", expires_at, payment_id: null, created_at` (+ `remaining_amount`, `payment_ids`) | shape and types | AZ-01 | P | |
| H-005 | `expires_at` is `created_at` plus `authorization_ttl_seconds`. | exact difference | AZ-01 AX-05 | N | TIME |
| H-006 | The caller's `available` is below `amount` — 409 `insufficient_funds` | against available, boundary exact, held counts | AZ-04 | N | |
| H-007 | `amount` below 1, above 1000000000, or not an integer — 422 `validation_failed` | 0, −1, 1e9+1, strings, bools, fractions; 1e9 ok | AZ-02 AZ-03 | N | LIMIT |
| H-008 | `to_handle` is the caller's own handle — 422 `self_payment` | | AZ-02 | N | |
| H-009 | `note` over 200 characters, or `visibility` neither `public` nor `private` — 422 `validation_failed` | | AZ-02 | N | LIMIT |
| H-010 | No user has that handle — 404 `not_found` | | AZ-02 | N | |
| H-011 | An open authorisation is **not** a feed item and never appears in `GET /activity`. | not in either party's feed, nor `/requests` | AZ-05 | N | |
| H-012 | wrong types / missing fields / unparseable body / unknown fields | 400 / 422 / 400 / ignored (as stage 1) | AZ-02 AC-12 | N | |
| H-013 | authentication on every new endpoint | 401 | AZ-12 | N | |

## API — `POST /authorizations/{id}/capture`

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| K-001 | `Idempotency-Key` is required. Only the receiver (the `to` party) may capture. | missing/empty 400; 256 chars 422; payer/stranger/operator 403 | AZ-38 AZ-30 | N | |
| K-002 | `amount` is optional and defaults to the authorisation's remaining amount. | `{}` captures the remainder; with prior nonfinal captures it is the remaining amount; explicit full amount equals the default | AZ-20 AZ-21 AZ-26 | N | |
| K-003 | As on `POST /requests/{id}/pay`, **a replay must send the identical body** — `{}` and `{"amount": 2000}` are different JSON values even when they mean the same capture, so reusing a key across the two is 409 `idempotency_key_reuse` per `stage-1.md` §7. | both directions; also `{final:true}`; replay of `{}` is 200 | AZ-35 | N | RETRY |
| K-004 | Returns `201` with the created **payment**, in exactly the shape `POST /payments` returns, with `authorization_id` set to this authorisation and `request_id: null`. | payment shape, ids | AZ-20 | N | |
| K-005 | The payment's `amount` is the captured amount; its `note` and `visibility` are copied from the authorisation; it appears in the activity feed by the ordinary visibility rule. | private hidden from third parties; both parties see it | AZ-20 AZ-32 | N | |
| K-006 | By default the authorisation becomes `captured`, carries `captured_amount` and `payment_id`, and **releases the uncaptured remainder immediately**: capturing 1500 of 2000 returns 500 to the payer's `available` in the same step. | 2000/1500 example; `remaining_amount` 0 | AZ-22 | N | |
| K-007 | **Default: one final capture per authorisation.** A second capture after a final capture is `409 authorization_not_open`. | fresh keys | AZ-23 | N | |
| K-008 | **Extended capture mode.** To keep the remainder held, send `{"amount": 700, "final": false}`. `final` is boolean, default `true`, so earlier single-capture requests retain their behavior. | open stays open, held 1300; wrong-typed `final` rejected | AZ-24 AZ-28 | N | |
| K-009 | With `final: false` and an uncaptured remainder, status stays `open`; further captures are allowed up to that remainder. | | AZ-24 | N | |
| K-010 | Capturing the entire remainder closes it even with `final: false`. | explicit and omitted amount | AZ-24 AZ-26 | N | |
| K-011 | A final capture closes it and releases any remainder. | after nonfinal ones too | AZ-25 | N | |
| K-012 | `capture_exceeds_authorization` compares with the **remaining** amount; omitted amount defaults to that remainder. | 2001 / 1301 after 700 / 2000 after 700 | AZ-27 | N | |
| K-013 | `captured_amount` is cumulative; `payment_id` is the latest capture; `payment_ids` lists every capture in order. | after 3 captures; after void; after expiry | AZ-24 AZ-44 AX-12 | N | ORDER |
| K-014 | Every authorization response adds `remaining_amount`: the amount still held, zero when closed. | open / captured / voided / expired | AZ-01 AZ-24 AZ-40 AX-10 | N | |
| K-015 | Void and expiry can close a partially captured authorization, release only the remainder, and preserve all capture records. | void after 600 of 2000; expiry after 500 | AZ-44 AX-12 | N | PARTIAL |
| K-016 | New fields do not change idempotency body equality. | replay/reuse judged on the request body only; replay after the authorization closed returns the original | AZ-10 AZ-35 AZ-36 | N | RETRY |
| K-017 | The authorisation is not `open` — 409 `authorization_not_open` | voided, captured | AZ-31 AZ-23 | N | |
| K-018 | `expires_at` is at or before now — 409 `authorization_expired` | clock-expired and seeded-past (either of the two 409 codes is accepted for a status already `expired`) | AX-04 AX-10 | N | TIME |
| K-019 | `amount` above the authorisation's uncaptured remainder — 422 `capture_exceeds_authorization` | | AZ-27 AC-05 | N | |
| K-020 | `amount` below 1, or not an integer — 422 `validation_failed` | 0, −1, 1.5, true, `"100"`; integral forms 1000.0 / 1e3 accepted | AZ-28 AZ-29 | N | LIMIT |
| K-021 | The caller is not the receiver — 403 `forbidden` | | AZ-30 | N | |
| K-022 | Unknown authorisation — 404 `not_found` | | AZ-30 | N | |

## API — void, list

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| V-001 | **Only the payer may void** — the `from` party releasing their own hold. No idempotency key, like decline and cancel. | receiver/stranger/operator 403; works with no key | AZ-43 AZ-40 | N | |
| V-002 | `200` with the authorisation, `status: "voided"`, the hold released. | released funds spendable at once | AZ-40 AZ-45 | N | |
| V-003 | Voiding an already-voided authorisation is `200` with the current state. | | AZ-41 | N | |
| V-004 | A `captured` or `expired` one is `409 authorization_not_open`. | | AZ-42 AX-04 AX-10 | N | |
| V-005 | For an existing authorization, capture and void return 403 `forbidden` when the caller is not the permitted party, including callers who are neither party. | | AZ-30 AZ-43 | N | |
| V-006 | `GET /authorizations` returns only authorizations involving the caller. | payer, receiver, strangers, operators | AZ-50 | N | |
| V-007 | Authorisations where the caller is the payer or the receiver, and no others. Newest first by `created_at`. | spaced creations | AZ-50 AZ-52 | N | ORDER |
| V-008 | `direction` is `outgoing` (the caller is the payer), `incoming` (the caller is the receiver), or absent for both. | | AZ-50 | N | |
| V-009 | `status` is one of the four statuses, or absent for all. An authorisation expired by the clock matches `expired`, never `open`. | clock-expired excluded from `open` | AZ-50 AX-10 AX-04 | N | TIME |
| V-010 | `limit`, `offset` and `has_more` behave exactly as on `GET /requests`. | range/format 422; paging; has_more; envelope key `authorizations` (ambiguity A-02) | AZ-51 AZ-52 | N | LIMIT |
| V-011 | (list invariant) held == Σ remaining of open outgoing; captured + remaining == amount | | AZ-53 | N | |

## UI — authorizations

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| W-001 | A new route `/authorizations`, and the wallet gains two numbers. | route loads; two numbers on `/` | UI-02 UI-13 NEG-01 | N | |
| W-002 | The UI and the API share `/authorizations`: serve HTML for `Accept: text/html` and JSON otherwise, as for `/requests`. | | NEG-01 NEG-02 NEG-03 | N | |
| W-003 | `wallet-balance` — Formatted `total`, retaining the existing display and `data-amount` | with a hold: total, not available | UI-13 | N | |
| W-004 | `wallet-available` — Formatted `available`, with `data-amount`. **Present this as the headline number** — it is what the user can actually spend | text, attribute, visual dominance, wording | UI-13 UI-134 UI-10 | N | |
| W-005 | `wallet-held` — Formatted `held`, with `data-amount`. Absent when `held` is zero | present with hold; absent at zero, after void, after expiry | UI-13 UI-14 UI-94 UI-101 | N | |
| W-006 | `authorize-handle`, `authorize-amount`, `authorize-note`, `authorize-visibility`, `authorize-submit` — The authorise form. Same input rules as the pay form | decimals, visibility, form kept after success, unchanged resubmit creates no second hold; located on `/` or `/authorizations` (not specified) | UI-97 UI-24 | N | RETRY |
| W-007 | `authorize-error` — Shown when the authorisation is refused, including insufficient available funds | against available (held counts), unknown handle, self | UI-98 | N | |
| W-008 | `authorization-list` — Container on `/authorizations`. Children newest first in the DOM | | UI-99 | N | ORDER |
| W-009 | `authorization-item-{authorization_id}` — Carries `data-status="{status}"` | open / captured / voided / expired | UI-90 UI-100 | N | |
| W-010 | `authorization-amount-{id}` — Text is exactly the formatted authorised amount | | UI-90 UI-92 | N | |
| W-011 | `authorization-captured-{id}` — Formatted captured amount. Present only when `status` is `captured` | absent when open; present with `15.00 EUR` after capture | UI-90 UI-92 UI-96 | N | |
| W-012 | `authorization-expires-{id}` — Text is the RFC 3339 `expires_at` | equals the API string exactly, for both parties | UI-90 | N | TIME |
| W-013 | `authorization-capture-amount-{id}` — Decimal input, pre-filled with the remaining amount. Present only on an incoming `open` authorisation | `20.00`; `13.00` after a nonfinal capture; absent for payer and non-open | UI-90 UI-91 UI-100 | N | |
| W-014 | `authorization-capture-{id}` — Button. Present only on an incoming `open` authorisation | | UI-90 UI-92 UI-100 | N | |
| W-015 | `authorization-void-{id}` — Button. Present only on an outgoing `open` authorisation | | UI-90 UI-94 | N | |
| W-016 | `authorization-error` — Shown when a capture or a void is refused | bad decimal (no request), over remaining, stale void, stale capture | UI-93 UI-95 UI-96 | N | |
| W-017 | `empty-authorizations` — Shown when the list is empty | | UI-99 UI-90 | N | |
| W-018 | The UI must reflect seeded and newly created holds. Show available funds as the user's spending balance, including immediately after reset with open holds. | seeded holds (open, expired, voided) in wallet and list; new hold shows after submit | UI-13 UI-14 UI-100 UI-97 | P | |

## Concurrency

| Row | Requirement (exact) | Behaviour | Check | Shipped? | Risk |
|---|---|---|---|---|---|
| Q-001 | Concurrent requests must produce the same results as executing them one at a time in some order, and the requirements above hold at every read. | double capture (distinct and fresh keys), capture‖void ×10, nonfinal captures ×30, mixed sizes, overdraft vs holds (40-way), capture‖payment on reserved money, settlements‖holds, pay‖authorize, 50-way mixed with a watcher asserting every `/me` and `/authorizations` read; capture/void/expiry races across the deadline | AC-01 … AC-15 | N | CONC |
| Q-002 | (engineering stance) never hash while holding the state lock; ≥50 concurrent connections | stage-1 CON-15/LIM-01 re-run; 50-way mixed run | `--include-stage1` AC-11 | N | CONC |
| Q-003 | (engineering stance) expiry reflected on every read/write even with no request at the deadline | first touch after idle = list / me / write | AX-13 | N | TIME |
| Q-004 | (engineering stance) 5xx-free on the new endpoints under hostile input | garbage bodies, bad ids | AC-12 | N | |

## Counts

Generated by `python3 check_matrix.py`:

- matrix rows: **188**
- shipped stage-2 harness (31 tests, judged from names): fully exercises 29 rows · partly 33 · **not at all 116** · manual-only 1 · not testable 9
- checks: **87 API** (`run_checks.py`; 3 need `STAGE1_URL`) + the 192 accepted stage-1 API checks via `--include-stage1` · **99 browser tests** (83 check ids, layout tests run at 375, 768 and 1280 px)
- self-validation against the analyst's reference service + reference UI: 278/278 API (87 + 191 stage-1) and 99/99 UI pass; all 15 injected API defects and all 14 injected UI defects are caught
