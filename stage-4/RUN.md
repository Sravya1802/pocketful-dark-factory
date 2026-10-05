# Pocketful — stage 4

Build and start (from this directory):

```sh
docker build -t pocketful . && docker run --rm -e PORT=8080 -p 8080:8080 pocketful
```

Then open http://localhost:8080/ in a browser (after `POST /_test/reset` has seeded users).

The service listens on `0.0.0.0:$PORT` (default `8080`) and answers `GET /health` with
`{"status": "ok"}` as soon as it is up (well under a second). It needs no network access,
volumes or configuration at run time; all state is in memory and starts empty until
`POST /_test/reset` seeds a fixture.

Without Docker (Node.js 22 or later, no packages to install):

```sh
PORT=8080 node server.js
```

## Layout

- `server.js` — HTTP listener, body reading, response writing.
- `src/app.js` — routing, validation, authentication, idempotency and every endpoint.
- `src/state.js` — in-memory state, fixture loading, export/import of the full state.
- `src/json.js` — strict JSON parser that keeps numbers exact (no binary floating point).
- `src/passwords.js` — scrypt password hashing on the thread pool.
- `src/time.js` — microsecond clock (strictly increasing) and RFC 3339 instant parsing.
- `src/ledger.js` — historical views: balances by effective and recorded time, hold
  timelines, and the boundary check used by corrections.
- `src/ui.js` — serves the browser screens (`/`, `/requests`, `/split`, `/authorizations`,
  `/signup`, `/login`) and their assets. `/requests` and `/authorizations` are shared with the
  API: HTML when `Accept` contains `text/html`, JSON otherwise.
- `public/` — the browser client (`app.js`, `app.css`, `icon.svg`): plain JavaScript and CSS,
  system fonts, nothing loaded from outside the service.

## Design notes

- Amounts and balances are `BigInt` minor units end to end. JSON numbers are parsed from
  their text, so `1000`, `1000.0` and `1e3` are the same integer and `1.5`, `"1000"`, `true`
  are refused without ever passing through a float.
- All state lives in one process and every change runs synchronously on the event loop,
  so each operation (payment, request payment, split, settlement, reset, import) is a single
  indivisible step. Password hashing runs off the event loop and never between a check and
  the write it guards.
- Idempotency records are keyed by caller, method + path and key, and hold the canonical
  request body and the exact original response text.
- Request bodies are capped at 256 KiB for API endpoints. Test-control calls (`/_test/reset`,
  `/_test/import`, `/_test/export`) accept bodies up to 448 MiB and are processed one at a
  time; only one control body over 16 MiB is transferred at a time (others wait under TCP
  back-pressure), and all buffered bodies share one budget. Larger bodies are drained and
  answered `413` with the error envelope. Headers may be up to 1 MiB so that an over-long
  `Idempotency-Key` still reaches validation (`422`).
- Export is serialized as an atomic snapshot and streamed in pieces, so its size is not
  limited by the engine's maximum string length. Measured in a 2 CPU / 2 GiB container with
  the heaviest payment shape (200-character notes, 40-character keys): 400 000 payments
  export as 444 MB and import into a fresh container in 6.5 s. The import limit is reached
  at roughly 420 000 such payments; that is the capacity of this in-memory design within
  2 GiB.
- Large control bodies are parsed by the native JSON parser with a reviver that keeps each
  number's source text, so fixture and import values stay exact.
- Passwords: signups use scrypt (N=16384, r=8, p=1) with a per-user salt. Seeded fixture
  users use scrypt (N=2048, r=8, p=1), also with a per-user salt, so a reset of a few
  thousand users fits the 10 s budget; repeated resets of the same fixture reuse the
  finished hashes from memory.

## Stage 2 notes

- Holds (authorizations): `held` is the sum of the remainders of a user's open holds and
  `available = total − held`. Every insufficient-funds check (payments, request payments,
  settlement net debits, new holds) uses `available`. Opening, capturing, voiding and expiring
  a hold each happen in the same single synchronous step as the money they affect.
- Expiry is applied at the start of every request, before anything reads or writes holds, so
  reads and writes reflect it even when nothing happened at the deadline.
- Captures: default final (releases the remainder); `"final": false` keeps the remainder
  held; capturing the whole remainder always closes the hold.
- Export/import carry authorizations, captures and `authorization_ttl_seconds`; exports from
  the stage-1 service (no authorizations, no lifetime) import with the defaults.
- Browser client: the session and every unfinished form live in `localStorage`, so a refresh
  (or an export/import upgrade) keeps them. Each submission keeps its idempotency key until
  its outcome is known: a lost response shows the uncertain state and a retry sends the same
  key and body; an unchanged resubmission after success replays the original instead of
  creating anything new. Reads carry a sequence number and an older response never
  overwrites a newer one. Typed amounts are converted to minor units with string arithmetic.

## Stage 3 notes

- Every payment keeps an append-only revision list: revision 1 is the original
  (`effective_at = recorded_at = created_at`); corrections append revisions with their own
  effective time and a server-assigned recorded time (strictly increasing). The original
  payment, `GET /activity` and every stored idempotent response are never changed.
- Each wallet has an opening balance (seeded balance minus the net effect of the seeded or
  imported payments). Any view `(as_of, known_at)` is: for each payment, the latest revision
  recorded at or before `known_at`, applied by effective time up to `as_of`. Holds follow
  their event timeline (creation, captures, void/final capture, expiry at `expires_at`).
- Corrections run in one synchronous step: checks (current `available` first, then every
  historical boundary for total and available of both parties), the new revision and the
  money movement between the same two wallets. Settlement members and captures are
  immutable (`422 linked_payment_immutable`).
- Statements are built from an immutable per-user view (sorted entries with prefix sums,
  cached until the user's ledger changes). A snapshot token records only the instant it
  was read as (`known_at`, capped at the read start) and the window: revisions are
  append-only and everything later is recorded after that instant, so paging recomputes
  exactly the same result. Each snapshot costs a few hundred bytes; they live until reset
  or import.
- Seeded (and older-export) closed holds without a recorded lifecycle hold nothing in any
  historical view.
- Export/import carry revisions, opening balances and how each hold closed; stage-1 and
  stage-2 exports import with revision 1 per payment and openings derived from balances.

## Stage 4 notes

- Refunds (`POST /payments/{id}/refunds`): a new payment from the original receiver back
  to the original sender, with the original note and visibility and `refund_of` naming the
  target, funded from the refunder's available balance in one step. Refunds of a payment
  never exceed its current corrected amount; refunds themselves cannot be refunded or
  corrected. Every payment object carries `refund_of` (null unless it is a refund).
- Corrections cannot take a payment below what has already been refunded, and captures and
  refunds are immutable (`linked_payment_immutable`).
- Correction batches (`POST /correction-batches`, settlement operators only): items are
  validated in input order, settlements must be corrected whole at one effective instant,
  then the combined effect of all revisions is checked against current available funds and
  every historical boundary of every wallet involved, and all revisions are appended in one
  step with one shared `recorded_at` and a `correction_batch_id`.
- Export/import also carry statement snapshots (each is an instant, a window and echoed
  text), so tokens minted before an export keep paging the same frozen entries after import
  into any process. After an import the clock never hands out an instant at or before any
  imported one. Stage-1/2/3 exports import as before; stage-3 exports carry no snapshot
  data, so stage-3 tokens cannot be restored.

