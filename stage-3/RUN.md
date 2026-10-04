# Pocketful — stage 2

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

