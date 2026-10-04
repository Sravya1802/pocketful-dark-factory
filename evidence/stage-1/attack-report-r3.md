# Pocketful stage 1 - attack report, round 3 (adversary)

Revision attacked: `621342c6d1d5651e863b24e8124601552874b4d7` (`git archive`, product code untouched).
Built with `DOCKER_BUILDKIT=0` and an empty `DOCKER_CONFIG`; run as containers with `--cpus 2 --memory 2g` (two of them, A on :18100 and B on :18101, for cross-process import), plus one `--network none` container (health 200 via `docker exec`). An old-revision process (df8f825, host node) supplied old-format exports.
Earlier evidence: attack-report.md/e0f26b6, attack-report-r2.md/176cebc. New files only (r3 suffix) in `evidence/stage-1/attacks/`; outputs `run-r3-*.txt`.

## H1 (R2-1, service refused its own export) - FIXED
- API-built state of 32 000 payments (36.2 MB export) and 60 000 payments (68 MB export) imports unchanged into a different container: 204 in 0.4-0.8 s; re-export from B is **byte-identical** to A's export.
- Fixture-built states: 400 000 payments (137 MB export): reset 1.9 s, export 0.7 s, import 2.7 s, re-export byte-identical, peak container memory 979 MiB. 700 000 payments (240 MB export): reset 3.8 s, export 1.4 s, import 7.2 s, byte-identical, peak 1373 MiB.

## Findings (reproduced)

### R3-1 - A stalled large control upload holds the single large-body slot indefinitely; a legitimate large import waits behind it - Low
- Spec §10: control calls have a 10 s timeout; §3.3 reset must be served. Builder design: only one control body > 16 MiB in transfer at a time.
- Expected: a client that stops sending cannot keep other control calls from completing (a read/idle timeout releases the slot).
- Actual: one client declares `Content-Length: 200000000` to `/_test/import`, sends 20 MiB and goes quiet. A second client's legitimate 40 MB import (alone: 0.7 s) waits and was still waiting after 200 s; it only proceeded once the stalled connection closed (30 s run: 204 after 30.7 s). No server-side body idle timeout was observed in 200 s.
- Not affected (passed): while the slot is held, `/health`, API payments/logins, small `reset` and `export` calls are served in ~10 ms; a stalled upload to `/_test/reset` behaves the same. The lane recovers as soon as the stalled client disconnects.
- Repro: `python3 evidence/stage-1/attacks/big_import_vs_stalled_r3.py http://HOST:PORT 40` (exit 1 = delayed), output `run-r3-bigimport-vs-stalled-docker.txt`. Small-call variant: `control_lane_r3.py` (all pass).
- Real-world exposure: a single sequential harness client that times out mid-upload closes its socket, which frees the slot, so this needs a client that stays connected and silent.

### R3-2 - State beyond roughly 700k payments: import slows past 10 s and a re-import into a loaded process crashes it - Low (outside the builder's claimed range)
- Spec §10 control calls 10 s; §2 2 GiB; §5 no 5xx/crash.
- Actual with a 1 000 000-payment fixture (231 MB body, 343 MB export): reset 7.0 s, export 2.2 s, **import into a fresh process 15.3 s**, and importing the same export back into the process that already held that state ended with the container exiting 139 (`FATAL ERROR: Reached heap limit ... JavaScript heap out of memory`, peak memory 1.6 GiB, OOM flag false). 700 000 payments passed (above).
- Reaching this state through the API means about 1 000 000 writes, so it is not realistic for the harness; reported as the measured ceiling.
- Repro: `python3 attacks/big_state_r3.py http://A http://B 1000000 <containerA>`; output `run-r3-bigstate-1M-docker.txt`.

## Observations
- Over the new 448 MiB control cap the reply is a clean `413 payload_too_large` (460 MB reset and import, 0.9 s, memory 781 MiB).
- A 33 MiB reset body (which round 2 refused with 413) is now accepted/validated normally (204/422 depending on content); round-1/2 expectations for the 32 MiB cap are obsolete by design.
- Under 16 concurrent 28-40 MB control calls, each call took 0.7-7.6 s (queued behind the single lane) but all returned 204, peak container memory 901 MiB; this is within the 10 s control timeout, but the 8th-16th callers wait several seconds behind each other.
- 5000 request headers now give 431 (round 2: 200).
- Round-1/2 observations unchanged (0-amount request pay allowed, 403 vs 404 for third parties, HEAD /health 405, BOM accepted). R2-2 (shared first salt) is superseded: seeded hashes are now `scrypt$2048$8$1$...` with per-user salts; signups `scrypt$16384$...`. N=2048 is a low work factor; hygiene only.

## What passed
- `attack_r2.py` against the container: **449 passed, 0 failed** (`run-r3-docker.txt`) - the whole round-1/2 suite: races on all 5 write paths (identical fresh keys, pay vs cancel/decline), overdraft storms, settlement atomicity, idempotency semantics, number parsing, split vectors, feed visibility, query validation, auth/signup races, settlements, export/import/reset errors, fuzz of 3000 ops at 50 concurrency with zero 5xx, invariants during writer storms, lost-response retries, header edges, big/odd bodies.
- `attack_r2_regress.py` (cap/regression suite): 54 passed, 0 failed (`run-r3-regress-docker.txt`), including 262144/262145 byte API cap, 32 transfers x 200-emoji notes, 5001-participant split, 50 concurrent 250 KB bodies, 50 x 42 MB storm (all 413, 0.4 s, alive), stalled-upload budget probes, and a 36 MB API-built export round trip.
- `attack_r3.py` (`run-r3-attack-docker.txt`): **62 passed, 0 failed** with container memory sampling:
  - export of a 60k-payment state (68 MB) is valid JSON `{track, format_version, state}` in 0.56 s; imports into separate process B in 0.76 s; byte-identical re-export; B accepts A's bearer tokens, seeded login, balances equal.
  - Replays after import: same key + same body -> 200 identical; reordered keys and `5.0` amount -> 200; same key with different amount / note / extra field / invalid body / missing default field -> 409 `idempotency_key_reuse`; other user's same key is a first use; request replay 200 identical.
  - 5 rounds of pay/decline/cancel racing on one request after import: status consistent with balance, at most one pay; imported pending request payable once, replay 200; 50 identical fresh-key writes on the imported big state -> 1 x 201 + 49 x 200.
  - **Old-format import**: an export from an older revision imports into 621342c (204); old seeded and signup logins work, wrong password 401, old tokens valid, replays of old payment, request, split and settlement return 200 identical (stored bodies digested on import), different body or different split order or invalid body on the same key -> 409, old pending request payable once, re-export then re-import stable.
  - **Export atomicity**: 303 exports under a 40-writer storm: every one has balance sum 112 500, no negatives, unique payment ids and balances equal to seed plus the payment ledger; a later write changes only later exports.
  - **Memory/starvation**: 16 concurrent large reset/import bodies: all 204, none over 8 s, peak 901 MiB, `/health` and `/me` max 0.58 s during; 460 MB over cap -> 413; 40 stalled 60-MB-of-400-MB import uploads: health and login served, memory 1.19 GiB, reset works after they close.
  - Invalid imports (empty, wrong track/version, missing/array/empty/string state) and tampered state (idempotency section dropped, users dropped, null payments, negative balance, duplicate user id) -> 422 with state unchanged; unparseable -> 400; 200 mixed concurrent export/import/payment/feed calls: no 5xx and consistent state; 30 concurrent exports identical; reset clears tokens.
- `cross_process_import.py`: export from container A imported into B: all logins, tokens, replay OK, re-export equal apart from tokens minted by the logins.
- `control_lane_r3.py`: with one stalled >16 MiB upload to import or reset, small reset/export, payments and health are all served in ~10 ms and the lane recovers on disconnect.
- `stalled_uploads.py`: 50 stalled ~31 MB reset uploads: health, payment, login served.
- No-network container: health 200.
