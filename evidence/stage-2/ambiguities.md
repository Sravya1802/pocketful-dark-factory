# Pocketful stage 2 — ambiguity notes

Dark run: no questions asked. `Strict` = the check fails a service/UI that reads it differently. `Lenient` = either reading passes.
Stage-1 notes (`../stage-1/ambiguities.md`) still apply to everything inherited.

## API

| # | Question | Choice and reason | Strict / lenient | Checks |
|---|---|---|---|---|
| B-01 | Envelope key of `GET /authorizations`. Stage 1 uses `requests` / `payments` plus `has_more`. | `{"authorizations": [...], "has_more": bool}` — same pattern, "behave exactly as on `GET /requests`". | Strict | AZ-50.. NEG-02 |
| B-02 | Capture of an authorization that is *already* expired: `authorization_expired` ("`expires_at` is at or before now") or `authorization_not_open` (status is not `open`)? | A hold whose clock has run out reads as `expired`; both rows of the spec table apply. Checks accept **either 409 code** for expired holds; `captured` / `voided` strictly `authorization_not_open`. | Lenient | AX-04 AX-10 |
| B-03 | Void of an `expired` authorization. | Spec is explicit: `409 authorization_not_open`. | Strict | AX-04 AX-10 |
| B-04 | Capture/void by someone who is neither party: 403 or 404? | Spec: "capture and void return 403 `forbidden` … including callers who are neither party" ⇒ 403 for payer-captures, receiver-voids, strangers and operators; 404 only for unknown ids. | Strict | AZ-30 AZ-43 |
| B-05 | Wrong-typed `final` (`"yes"`, `1`, `null`, `[]`). | Rejected with 4xx; 400 `malformed_request` (wrong JSON type) or 422 both accepted. Wrong-typed `amount` follows stage 1 (strings/booleans 422). `amount: null` accepted as 400 or 422. | Lenient | AZ-28 |
| B-06 | Order of checks inside capture (404 → 403 → validation → state → exceeds). | Only independent cases are asserted; the spec's one explicit order (claimed key resolved before validation and state) is asserted. | n/a | AZ-36 |
| B-07 | `capture` with no body at all. | Always sent as `{}` or with a body; replay rules compare bodies as in `/requests/{id}/pay`. | n/a | AZ-35 |
| B-08 | `authorization_ttl_seconds: null`, `600.0`, extremely large values. | Not asserted. Asserted invalid: 0, negatives, 1.5, `"600"`, true/false, `[]`, `{}` → 422 and no change. | Strict for the listed | AX-05 |
| B-09 | RFC 3339 forms of `expires_at`. | `Z` and numeric offsets both accepted; UI text must equal the API string byte for byte. | Lenient (format) / Strict (UI equals API) | AZ-01 UI-90 |
| B-10 | Seeded holds' `captured_amount`. | Fixture example has none: not asserted for seeded rows; `remaining_amount` is `amount` for live open seeds, 0 otherwise. | n/a | AX-01 AX-04 |
| B-11 | Seeded authorization id returned as `authorization_id`. | The fixture `id` becomes `authorization_id` (as seeded payments/requests keep their ids). | Strict | AX-01 AX-02 |
| B-12 | Does expiry count at exactly `expires_at`? | "at or before now" ⇒ expired; checks never sit on the boundary second (sleep ≥ 1 s past it, capture ≥ 3 s before it). | n/a | AX-10 AX-14 |
| B-13 | `available` for a user who is *receiver* of holds. | Unaffected until capture (receivers' `held`/`available` change only by their own holds and captures). | Strict | ME-02 AZ-09 |
| B-14 | Capturing more than `available` while holds exist. | Captures spend reserved money: allowed even at `available == 0`. | Strict | AZ-33 AC-08 |
| B-15 | Content negotiation precedence. | `text/html` anywhere in `Accept` ⇒ UI for `GET /requests` and `GET /authorizations` (browser header with `*/*;q=0.8`); missing / `application/json` / `*/*` ⇒ JSON. POST/other methods always JSON. | Strict (GET); POST lenient (JSON expected) | NEG-01..04 |
| B-16 | Who may load the UI pages without a token. | HTML shells load unauthenticated (browsers cannot send bearer tokens on navigation); data is fetched with the token. JSON on the same path still needs the token (401). | Strict | NEG-01 NEG-02 |
| B-17 | Stage-1 check `API-01` (exact `/me` shape) cannot hold in stage 2. | Skipped with `--include-stage1`; replaced by `ME-01`. Everything else from stage 1 must still pass. | — | ME-01 |
| B-18 | Stage-1 export import (UPG-10..12). | Needs a running **stage-1** service (`STAGE1_URL`). Without it these checks skip, and `UPG-01..04` (stage-2 → stage-2) still run. | — | UPG-10 |

## UI

| # | Question | Choice and reason | Strict / lenient | Checks |
|---|---|---|---|---|
| B-30 | Which screen hosts the authorise form? | Unspecified. Checks look on `/` first, then `/authorizations`, and fail only if neither has `authorize-handle`. | Lenient | UI-24 UI-97 UI-98 |
| B-31 | `authorization-list` / `incoming-list` / `outgoing-list` when there is nothing to list. | Either the container or the matching `empty-*` element satisfies the route check; item checks use data that exists. | Lenient | UI-02 UI-99 |
| B-32 | "exactly the formatted amount" with surrounding whitespace in the DOM. | Playwright's whitespace-normalised text match; no trimming of inner characters, no grouping separators, no sign. | Strict | UI-10..12 |
| B-33 | Lost response. | Simulated at the network layer: request reaches the server (and commits) then the connection is reset, or it is dropped before the server. The UI must therefore use `fetch`/XHR for `POST /payments` (a form navigation cannot show `pay-uncertain`). | Strict | UI-40..44 |
| B-34 | "must not send another payment" after an unchanged resubmit. | No request at all, **or** a replay with the *same* `Idempotency-Key` is fine; assertions are on money, feed and `pay-error`. | Lenient | UI-30 |
| B-35 | Latest-refresh-wins observability. | Needs browser-side reads (`GET /me`, `GET /activity`). If none are observed (server-rendered refresh) the check is **skipped with a reason**; a lone slow refresh must still be applied (UI-53). | Skip if SSR | UI-52 UI-53 |
| B-36 | "Upgrade by import" in the browser. | Stage 1 has no UI, so the same-origin proxy is used: sign in, make state, export, scramble the service (reset to another fixture), import the export, continue in the **same page**. Stage 1 → 2 is covered at API level with real stage-1 tokens (UPG-10..12). | Strict | UI-110..114 |
| B-37 | Pending state ("pending ... distinct"). | While a slow `POST /payments` is in flight the submit must be disabled, `aria-busy`, or another `role=status/progressbar/aria-busy` indicator must exist; a second click sends nothing. | Lenient (any one indicator) | UI-35 |
| B-38 | Loading state. | With reads held back, the page shows a loading indication (text "load…", `aria-busy`, progressbar, skeleton/spinner class) or no content at all; it must not show a zero balance. Skipped→fail if the UI does no browser-side reads (not observable). | Lenient (indicator forms) | UI-135 |
| B-39 | Error state when reads fail (503). | Page is not blank (> 10 chars of text) and does not present a fake `data-amount="0"`. | Lenient | UI-136 |
| B-40 | "Visually distinct" states. | Machine proxies only: refused vs uncertain have different colour / background / border; available is ≥ 1.25× the font size of total and held, first in reading order; primary vs secondary button style differs. Taste is in `manual-checklist.md`. | Strict (proxies) | UI-36 UI-13 UI-134 UI-133 |
| B-41 | Contrast. | WCAG ratios 4.5 / 3 (large) measured from computed colours over solid ancestor backgrounds; elements on images/gradients, disabled controls and `opacity<1` are skipped. | Strict (measurable) | UI-127 |
| B-42 | "Visible focus". | After a keyboard Tab the focused element has an outline ≥ 2 px or a box-shadow, **or** any of outline/shadow/border/background/colour changes versus the blurred state. | Lenient (any visible change) | UI-125 |
| B-43 | "Visible labels". | An associated `<label>` (for/wrapping) or `aria-labelledby` with visible text; placeholder or `aria-label` alone does not count. | Strict | UI-126 |
| B-44 | Horizontal scroll. | `documentElement.scrollWidth ≤ innerWidth+1`, body likewise, and no visible element outside an `overflow` scroller extends past the right edge. Run at 375, 768 and 1280 px with stress data (200-char unbroken notes, 20-char handles, ≈ 1.2 e9 EUR balances, 40 feed items, errors/preview open). | Strict | UI-120..123 |
| B-45 | "Format timestamps for people first". | Visible text must not contain ISO timestamps, except `authorization-expires-*` which the spec fixes to RFC 3339; raw ids (`u_…`, `p_…`), JSON, `undefined`, `NaN`, `null` are rejected anywhere. | Strict | UI-132 |
| B-46 | Signed-out visit to a protected route. | Redirect to login or a login prompt are both fine; only "no `current-user` and no wallet shown" is asserted. | Lenient | UI-04 |
| B-47 | Amount `0` typed in a form. | Refused somewhere (client or server) with the form's error element; nothing moves. | Lenient | UI-22 |
| B-48 | Seeded `captured` authorizations in the UI. | Not used (fixture gives no `captured_amount`); captured display is checked after a real capture. | n/a | UI-92 UI-96 |
| B-49 | Browser/engine. | Chromium via Playwright 1.63 (harness venv). Timeouts: 6 s per UI expectation. | — | all UI |
