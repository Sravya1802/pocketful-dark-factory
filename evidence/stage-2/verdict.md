# Pocketful stage 2 - gate verdict

**VERDICT: ACCEPT revision `92bbbf9a729a71eeed95934b8a72fdbda4ad297c`**

Tree clean; `git diff 92bbbf9 HEAD -- stage-2` is empty (later commits touch evidence only).
Adversary report considered: `evidence/stage-2/attack-report.md`, commit `0ef84d51aec7c431ae119870cf16692319c642b1`, attacking `92bbbf9...` (same revision).

## What I ran myself
| Check | Result |
|---|---|
| Shipped harness `--stage 2 --mode isolated`, out dir `/Users/lakshmisravyarachakonda/darkfactory/band-work/checks/s2-gate-1791149847` | stage 2 pass, "claimed stage: 2" |
| Analyst `docker_run_checks.sh` (stage-2 image build, healthy 0 s, `--network none` stays up, stage-1 image for upgrade checks) | API suite 278/278 (incl. `--include-stage1` and stage-1 export -> stage-2 import), UI suite 99/99, all container checks ok |
| Real browser (Playwright/Chromium), 375 px and 1280 px, all six screens: `/`, `/requests`, `/split`, `/authorizations`, `/login`, `/signup` | no horizontal overflow, zero requests off origin; screenshots reviewed. "Available to spend" is the dominant headline, total and "On hold" secondary; visible labels; visible keyboard focus ring; loading state; distinct Held/Waiting chips, direction/privacy tags and amounts; empty states; consistent nav and identity bar; mobile layout stacks cleanly. It reads as a presentation-ready consumer finance product, not a test page |
| Own API probes (holds, capture, expiry, races) | auth reduces available, balance == total; overspend vs available 409; extended capture; `{}` vs `{"amount":N}` replay 409; over-remainder 422 `capture_exceeds_authorization`; with no request at the deadline `/me` and list show the release and `authorization_expired` / `authorization_not_open`; 50 fresh-key authorizations -> one 201; 50 distinct 4000 holds vs 6000 available -> exactly one more; oversubscribed seeded holds 422; bad `ttl` 422 |
| Adversary S2-1 and S2-2 reproduced | both reproduced, see below |
| Spec-vs-code review of stage-2 (`state.js` holds/expiry, `app.js` capture/void/settlement net debits from available, idempotent scope, UI) | one lock-free synchronous step per money/hold operation; expiry applied at the start of every request; capture validation order as disclosed; no code shaped around the tests found |

## Adversary findings and treatment
- Adversary result: API 227/227, UI 336/341, stage-1 suite 449/449 against stage 2; races on all seven write paths, capture/void/expiry races, invariants at every read, export/import with holds and stage-1 export, decimal parsing, lost responses, latest-refresh-wins, XSS/CSP/session handling, layout at 320/375/1280, AA contrast, no network. I found nothing contradicting these.
- **S2-1 (Low, UI) reproduced:** with `/me` aborted and `/activity` returning 500, clicking `wallet-refresh` changes nothing on the page (no message, no "Updated" change). The spec requires `wallet-refresh` to refresh without clearing the form and asks for considered error states, but states no required element for a failed refresh. Not a stated-requirement failure; a product-quality gap.
- **S2-2 (Low, API) reproduced:** `Accept: application/json, text/html;q=0.5` and `text/html;q=0.1, application/json` get HTML on `/requests` (and `/authorizations`). The spec says UI for `Accept: text/html`, JSON for requests "without that header"; these headers contain `text/html`. Plain `text/html` (browsers) -> HTML, `application/json`, `*/*`, none -> JSON all correct. Defensible under the stated rule.
- Observations (seeded captured hold without `captured_amount` shows 0.00 captured; handle input is not lowercased; fully empty capture body gives 400): unspecified, not held against the build.

## Follow-ups (non-blocking)
1. Show a "couldn't refresh - numbers may be out of date" notice when an explicit refresh fails.
2. Honour Accept q-values (prefer JSON when it outranks text/html) on `/requests` and `/authorizations`.
3. Lowercase typed handles before sending.
