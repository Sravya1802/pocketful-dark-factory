# Stage 2 — manual UI review checklist

The automated UI checks (`run_ui_checks.py`) cover behaviour, structure and measurable accessibility. A person (or @gate in a real
browser) should still judge what a script cannot. Do it at **375 px** and at **1280 px**, signed in as a seeded user
(`ada`, 100.00 EUR) with at least: one open hold, one pending incoming request, one private and one public payment.
Mark each line pass / fail with a one-line reason.

## Product feel (spec: "coherent, presentation-ready consumer finance product, not a test harness")
- [ ] First screen after login: is the *available* amount unmistakably the headline? Are total and held visibly secondary yet legible?
- [ ] Is it obvious what "available", "held" and "total" mean without reading documentation (wording near the numbers)?
- [ ] One visual system: same type scale, spacing, colours, button styles and corner radii on all six routes.
- [ ] Calm, trustworthy character (no debug look: default browser buttons, raw tables, unstyled lists, lorem text, console-style output).
- [ ] The primary action on each screen is easy to find; secondary actions (decline, cancel, void, refresh) are visibly quieter.
- [ ] Money direction is understandable at a glance in the feed (who paid whom, in/out), plus visibility (public/private) as words or an icon with a text alternative.
- [ ] Timestamps are human ("Today 14:05", "24 Sep 2026, 13:20"), not raw ISO — **except** the `authorization-expires-*` element, which must be the RFC 3339 string; check that a human-friendly rendering sits next to it.
- [ ] People shown by display name and handle; no `u_…`/`p_…` identifiers on screen.
- [ ] Request rows read naturally ("Bob asks you for 12.00 EUR — taxi"), statuses are words with distinct colour *and* text (not colour alone).

## States (spec: pending, loading, successful, refused, uncertain must be visually distinct)
- [ ] Pending: pay, request, split, authorise and capture buttons show progress and cannot be double-submitted.
- [ ] Success: a confirmation that is clearly not an error (colour + text), and the form is **kept** on the pay form.
- [ ] Refused: error text says *why* in plain words (insufficient funds / unknown person / invalid amount) and is not styled like uncertainty.
- [ ] Uncertain (kill the network after pressing Pay — DevTools "Offline" while the request is in flight): says the outcome is *unknown*, offers a safe retry, form stays filled; it is clearly different from a refusal.
- [ ] Loading: first paint shows a loading indication, not a zero balance; empty states (`empty-activity`, `empty-requests`, `empty-authorizations`) are friendly and say what to do next.
- [ ] Network failure on reads: an understandable error with a way to retry, no silent blank screen.

## Layout and accessibility (spec: 375 px and desktop, no horizontal scrolling, visible labels, visible focus, contrast)
- [ ] 375 px: scroll the whole page on each route — never any sideways scrolling, nothing clipped, long notes/handles wrap.
- [ ] 1280 px (and ~768 px): sensible use of width; no stretched, hard-to-read single lines; no tiny islands.
- [ ] Keyboard only: Tab order follows the visual order; every control reachable; focus ring clearly visible on every control; Enter submits forms.
- [ ] Zoom to 200 %: still usable; no overlap.
- [ ] Screen-reader spot check (VoiceOver / NVDA): inputs announce their labels; errors/status messages are announced (role=alert / status); buttons have names; headings give structure.
- [ ] Colour-blind simulation (DevTools "Emulate vision deficiencies"): error / uncertain / success remain distinguishable.
- [ ] Contrast of disabled/placeholder text is not relied on for meaning.

## Runtime and delivery
- [ ] DevTools Network tab with cache disabled: every request is same-origin; fonts are system or bundled.
- [ ] Container started with `--network none`: all six screens load and work.
- [ ] No console errors on any screen.
- [ ] Navigation: the same links in the same order on every route; the current route is indicated; logout always reachable.
- [ ] Back/forward and direct URL entry work for `/`, `/requests`, `/split`, `/signup`, `/login`, `/authorizations`.

## Flows to walk once by hand (spec §Competing clients / §Existing clients after an upgrade)
- [ ] Two browsers as the same user: spend the balance in browser B, pay in browser A → refusal, balance and feed refreshed, A's inputs intact.
- [ ] Cancel a request in browser B while A's Pay button is visible; press Pay in A → `request-error`, list refreshes, button gone.
- [ ] Open hold: capture partially in the receiver's browser; the payer's `wallet-held` / `wallet-available` update after refresh.
- [ ] Export from the stage-1 service, import into stage 2 while a browser session is open; the session, the form and a pending retry survive.
