# Pocketful — stage 1

Build and start (from this directory):

```sh
docker build -t pocketful . && docker run --rm -e PORT=8080 -p 8080:8080 pocketful
```

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
- Request bodies are capped at 256 KiB for API endpoints and 32 MiB for `/_test/reset` and
  `/_test/import`, with one shared budget for all buffered bodies; larger bodies are drained
  and answered `413` with the error envelope. Headers may be up to 1 MiB so that an
  over-long `Idempotency-Key` still reaches validation (`422`).
- Passwords: signups use scrypt (N=16384, r=8, p=1) with a per-user salt. Seeded fixture
  users use scrypt (N=4096) computed once per distinct password, wrapped with a per-user
  salt (HMAC-SHA256), so resets of thousands of users fit the 10 s budget and no two stored
  hashes are equal.
