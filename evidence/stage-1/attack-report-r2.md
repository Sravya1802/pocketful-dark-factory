# Pocketful stage 1 - attack report, round 2 (adversary)

Revision attacked: `79b5f42ad91fb0daa491aa1f6ad6028d14ddebc7` (`git archive`, product code untouched).
Previous round: report e0f26b6 (revision df8f825). Scripts and recorded output are new files in `evidence/stage-1/attacks/`:
`attack_r2.py` (the round-1 suite with body-cap expectations updated), `attack_r2_regress.py` (re-verification + regression attacks),
`stalled_uploads.py`, `cross_process_import.py`, plus the unchanged `bigbody_storm.py` and `reset_hash_cost.py`; outputs `run-r2-*.txt`.

**Environment limit (disclosed):** the Docker daemon on this host returned 500 on every call midway through the round, so the
79b5f42 image run (`--cpus 2 --memory 2g`, `--network none`) could not be repeated. The image did build from the 79b5f42 Dockerfile and
a first `attack_r2.py` pass ran against that container (443 passed; the 5 failures were the expected 413 changes, then fixed in the script).
Everything else ran against `node server.js` of the same revision on the host (no CPU/memory cap), which is why timings are faster than in
the builder's container. Node 24 on the host; image is node:22.

## Verdict on the round-1 findings

| Finding | Status at 79b5f42 | Evidence |
|---|---|---|
| F1 crash, 50 x ~42 MB bodies | **Fixed** | `bigbody_storm.py URL 50 40` -> all 413 in 0.4 s, `/health` 0.1 s, alive. 50 stalled 31 MB reset uploads (`stalled_uploads.py`): service answered health, payment and login while stalled (RSS 427 MB). 12 uploads that really sent 30 MB each and stalled: payment, login and a small reset still served, recovery after close. |
| F2 event-loop stall | **Fixed** | 50 concurrent near-cap (240 KB) number-heavy bodies: max request latency < 2 s, `/health` < 1 s. 21 MB / 30 MB bodies: 413 in 0.04 s. |
| F5 long Idempotency-Key -> 400 | **Fixed** | keys of 256, 20000, 500000, 900000 chars -> 422 `validation_failed`; 1.1 MB and 3 MB headers -> 431; 5000 headers and 100 KB query -> no crash. |
| F3 reset latency with distinct passwords | **Improved** | host: 3000 distinct-password users reset in 1.2 s (was 8.7 s for 1000 in the container before). Not re-measured under the 2-CPU container limit (Docker down); builder reports 2.9 s for 1000. |
| F4 shared salt/hash | **Partly** | see R2-2. |

## New findings (reproduced)

### R2-1 - An export of a state built through the API can exceed the 32 MiB import cap, so the service refuses its own export - Low/Medium
- Spec §10: import "must accept an unchanged export produced by this service"; §10 control calls have a 10 s timeout.
- Expected: 204.
- Actual: after 28 000 successful `POST /payments` (200-char notes, 40-char keys, one wallet funded to 10^12) the export is 37.4 MB (about 1336 bytes per payment, so the cap is reached at about 25 000 such payments, which took 4 s to write). `POST /_test/import` with that unchanged export returns `413 payload_too_large` ("request body exceeds 33554432 bytes"). Export itself returned 200 in 0.2 s. A state of 24 000 payments (32.1 MB) still imports (204 in 0.2 s).
- Repro: `python3 evidence/stage-1/attacks/attack_r2_regress.py http://HOST:PORT http://OLDHOST:PORT G3` (the `OLD` URL is only needed for group G4; any live URL works for G3). Output in `run-r2-regress-host-node.txt`.
- Note: a harness is unlikely to write 25 000 records, but the cap is a limit the service imposes on itself on data it produced; the import cap should be at least as large as anything export can emit, or export should be bounded.

### R2-2 - "Per-user salt" holds for the stored value but equal passwords remain detectable, and the work factor is lower - Low (hygiene)
- Spec §6: stored with a password-hashing function. Not a spec violation by letter.
- Observed in `GET /_test/export`: seeded hashes are `scrypt-hmac$4096$8$1$<saltA>$<saltB>$<mac>`. Two seeded users with the same password have the same `<saltA>` field (`elYJzZ8dKKof+U/cA2Yu4g==` for both `u_ada` and `u_op`) and different `<saltB>`/`<mac>`. So the full stored strings differ (the round-1 check "no two users share a hash" passes), but the shared first salt shows who has the same password and lets an offline attacker amortise one scrypt per candidate across those users. Seeded cost is N=4096 (signups use N=16384).
- Repro: `python3 attacks/attack_r2_regress.py URL URL G4` prints the hash formats; or reset with two users sharing a password and read the export.

## Observations (not failures)
- 413 `payload_too_large` is not in the §5 table; it carries the standard error body, which satisfies "every 4xx carries this body".
- A client that is still uploading a body over the cap can see a connection reset/broken pipe instead of the 413 (the server answers and closes); my client reads the 413 when it can. The 1 MB keep-alive case worked (413, then the connection is either reusable or cleanly closed).
- Everything noted as an observation in round 1 (0-amount request pay, 403 vs 404, HEAD /health 405, BOM accepted) is unchanged.

## What passed (regression and re-verification)
`attack_r2.py` (449 checks, `run-r2-host-node.txt`): **449 passed, 0 failed** against 79b5f42. This repeats every round-1 attack group: races on all 5 write paths, overdraft storms, settlement atomicity and affordability, idempotency semantics (claimed key before validation, 4xx/409 not claiming, path scoping, per-user scope, key length 0/1/255/256), number parsing (`1e3`, `1000.0`, `-0`, huge exponents, strings, booleans), splits and rounding vectors, feed visibility and pagination, query validation, auth and signup races, settlements, export/import/reset errors, protocol oddities, 3000-op fuzz at 50 concurrency with zero 5xx, invariants sampled during a 40-writer storm, lost-response retries, 20 000-payment fixture, 2^53 balances. The only edits to that suite are the new 413 expectations.

`attack_r2_regress.py` (`run-r2-regress-host-node.txt`): 53 passed, 1 failed (R2-1).
- **Body caps (13):** body of exactly 262144 bytes -> 201, 262145 -> 413 JSON; declared Content-Length 99999999999 answered 413 immediately; chunked over cap -> 413; keep-alive after 413 usable; legitimate maximum shapes still work: 32 transfers x 200-emoji notes in worst-case `\uXXXX` escaped form (79 791 bytes) -> 201 with notes verbatim and 200 on replay; split with 5001 participants (200-char note, amount 10^9) -> 201, shares sum, replay 200 identical; 50 concurrent 250 KB payments all 201, each < 5 s, `/health` < 1 s.
- **Reset/import caps and budget (8 + 4):** a 12.5 MB fixture (60 000 payments) resets in well under 10 s; 6 concurrent large resets all 204 in < 1 s each; reset and import over 32 MiB -> 413; 12 stalled declared-32 MiB uploads and 60 stalled 260 KB uploads never block or refuse normal traffic; recovery after they close.
- **Passwords and cross-version (13):** seeded hashes are distinct per user; import then login works for seeded (N=4096 format) and signup (N=16384 format) users; wrong passwords 401; 100 concurrent logins and 200 signups at 50 concurrency all succeed under 5 s; 100 payments during a 150-signup hashing storm all 201 and < 1.5 s. **Export from df8f825 imported into 79b5f42:** 204, old-format seeded login, signup login, old tokens, replay of an old payment (200) and settlement (200 identical), old pending request payable once with correct balances, re-export lossless.
- **Cross-process (`cross_process_import.py`, `run-r2-crossproc-host-node.txt`):** export from one 79b5f42 process imported into a second separate process: 204, all four logins (seeded x3 incl. the shared-password pair, signup x1), wrong password 401, A's bearer tokens valid on B, A's payment replay 200 identical, re-export equals A's apart from tokens minted by the logins. This confirms the per-process HMAC does not break import.
- **Headers (9):** see F5 row above.
- **Core smoke (4):** fresh-key identical x50 -> 1 x 201 + 49 x 200; zero-balance overdraft storm all 409; `1e3` accepted; `-0` rejected.
