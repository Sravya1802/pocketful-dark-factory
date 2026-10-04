# Pocketful stage 1 - attack report (adversary)

Revision attacked: `df8f82520aad388c60c223bd2a4067ec2f79476a` (extracted with `git archive`, product code untouched).
Method: black-box over HTTP, from the spec only. Image built from `stage-1/Dockerfile`, run with `--cpus 2 --memory 2g`
(`-p 18080:8080`); a second container with `--network none` was started to prove start-up and operation with no network
(health 200, reset 204, login 200 via `docker exec`). The same suite was also run against `node server.js` from the same revision.
Scripts and recorded output: `evidence/stage-1/attacks/` (`attack.py`, `bigbody_storm.py`, `reset_hash_cost.py`, `run-docker.txt`, `run-host-node.txt`, `README.md`).
Result of `attack.py` against the container: **447 passed, 1 failed** (the one failure is the large-body stall, F2). Against bare node, the 383 checks that existed at that time all passed.

## Failures (reproduced)

### F1 - Process crash (JS heap exhausted, exit 139) under 50 concurrent large bodies - Medium
- Spec: §2 service must operate at up to 50 in flight within 2 GiB; §5 "Requests must not produce 5xx responses, including under concurrent load." State is in memory, so a crash loses everything.
- Expected: large bodies are rejected or handled with bounded memory; service stays up and answers `/health`.
- Actual: 50 concurrent ~42 MB JSON bodies (each under the builder's 64 MB cap, `{"to_handle":"b","amount":1,"pad":[1.5e3,...]}`) -> `FATAL ERROR: Reached heap limit ... JavaScript heap out of memory`, container exited 139, clients got connection resets, `/health` unreachable afterwards.
- Repro: `docker run -d --name pf -e PORT=8080 -p 18080:8080 --cpus 2 --memory 2g pocketful && python3 evidence/stage-1/attacks/bigbody_storm.py http://127.0.0.1:18080 50 40`
- Note: the spec states no body-size limit, so severity depends on whether the harness sends bodies this big; 64 MB x 50 > 2 GiB regardless.

### F2 - Event loop stalled by large bodies; per-request budget (5 s) blown - Medium/Low
- Spec: §2 per-request timeout 5 s at up to 50 in flight; stance "no work that blocks others".
- Expected: other requests keep their latency while a big body is parsed.
- Actual: a single 21 MB number-heavy body (3.5M `1.5e3` tokens) blocks all traffic for ~1.1 s (`/health` max 1.11 s). 50 concurrent 10.5 MB bodies: max request latency 32.0 s, `/health` 22.3 s (all eventually 201). A 30 MB string-padded body is fine (0.18 s).
- Repro: `python3 attacks/bigbody_storm.py http://127.0.0.1:18080 50 10`; single: `attack.py URL N` (check "N.number-heavy 20MB body < 5s and no >1s stall").

### F3 - Reset latency grows with distinct seeded passwords (eager scrypt) - Low (risk)
- Spec: §3.3 / §2 reset timeout 10 s; seeded users must log in immediately.
- Observed (container, 2 CPU, noisy host): 300 distinct-password users 3.67 s; 1000 users 8.72 s in one run (4.99 s in another). Same password for all users is instant (2000 users 0.03 s; shared hash, see F4). Close to the 10 s limit; a fixture of ~1200+ distinct-password users could time out.
- Repro: `python3 attacks/reset_hash_cost.py http://127.0.0.1:18080 100 300 600 1000 1200 1500 2000`. Did not exceed 10 s in my final run, so reported as risk, not a hard failure.

### F4 - Seeded users with the same password share salt and hash - Low (security hygiene)
- Spec §6: passwords stored with a password-hashing function. Builder disclosed memoisation.
- Actual: `GET /_test/export` shows `u_ada`, `u_bob`, `u_op` with byte-identical `password_hash` (`scrypt$16384$8$1$<same salt>$<same hash>`), so equal passwords are visible from the stored data. Signups get their own salt. Uses scrypt, so within the letter of §6.
- Repro: reset with two users, same password; `curl $URL/_test/export | python3 -c 'import sys,json;print([u["password_hash"] for u in json.load(sys.stdin)["state"]["users"]])'`.

### F5 - Idempotency-Key longer than the HTTP header limit gives 400 not 422 - Low
- Spec §7/§5 shared ranges: key of 256+ characters is 422 `validation_failed`.
- Actual: 300 and 5000 characters -> 422 (correct); 20000 characters -> `400 malformed_request` ("malformed HTTP request", Node header-size limit). Same for a 20 KB query string. The body is a proper error JSON.
- Repro: `curl -XPOST $URL/payments -H "Authorization: Bearer $T" -H "Idempotency-Key: $(head -c 20000 /dev/zero | tr '\0' k)" -d '{"to_handle":"bob","amount":1}'`.

## Observations (spec ambiguous or builder-disclosed; not counted as failures)
- Paying a request whose amount is 0 (from a split share of 0) returns 201 and creates a 0-amount payment, while `POST /payments` rejects amount < 1. Spec §9 says a 0 share "still produces a request", so the request side is correct; the pay side is unspecified.
- Third party calling pay/decline/cancel gets 403 `forbidden`; §5 also lists 404 for "not visible to this caller". The spec's per-endpoint tables say 403, which is what is returned.
- A request declaring `Content-Length: 99999999999` and sending nothing is not rejected with 413; the connection is held until the client gives up (3 s probe).
- `HEAD /health` -> 405, `/health/` -> 404.
- A UTF-8 BOM before a JSON body is accepted (RFC 8259 allows this).
- A balance can exceed 2^53 if the fixture itself seeds two wallets at 2^53-1 and money moves between them (no 5xx; outside spec range assumption).

## Attacks that passed (what I tried)
Counts are from `run-docker.txt`.
- **A races (9):** 50 concurrent identical fresh-key requests on each of the 5 write paths -> exactly one 201, 49 x 200, identical body, effect once (pay: balances 9300/3200). Pay with 50 distinct keys -> one 201, 49 x `request_not_pending`. 15 rounds of 24 mixed pay/cancel/decline racing on one request -> final status consistent with balances and at most one 201 pay, sum constant.
- **B overdraft/settlement (6):** 200 concurrent 333-unit payments from a 10000 wallet -> exactly 30 succeed, 170 `insufficient_funds`, balance never negative (watcher polling `/me`). 300 circular concurrent payments, no 5xx, sum constant. Settlement with a wallet that pays before it is paid (net ok) -> 201; net-unaffordable -> 409 and nothing moves; 20 concurrent competing settlements -> only the affordable one commits.
- **C idempotency (33):** reordered keys/whitespace/`100.0`/`1e2` replay -> 200 identical; extra field or different body -> 409; claimed key + invalid body/unknown handle/amount string/`note:null` -> 409 (before validation); other user's same key independent; same key on a different path and on `/requests/{id}/pay` succeeds; failed 4xx (422, 404, 409 insufficient funds) do not claim the key; key length 0/1/255/256; missing key 400; bad JSON/array body 400; no token 401; replay after cancel returns original pending body; pay `{}` vs `{"visibility":"public"}` -> 409, replay after paid -> 200 original, new key on paid -> `request_not_pending`; duplicate JSON key; explicit defaults (`visibility`, `note:""`) count as a different body; key case and Unicode normalisation (NFC/NFD) are distinct keys; settlement transfer order matters for equality; 5 different bodies on one key concurrently -> one commit.
- **D numbers/validation (78):** accepted: `1000.0`, `1e3`, `1E+3`, `10e-1`, `0.001e6`, `1000e-0`, 30-digit fractional zeros; rejected 4xx: `0`, `-0`, `-0.0`, `-0e0`, `1.5`, `1e-3`, `1e10`, `1e400`, 30-digit integers, `1e999999999`, 100000-digit number, `1e-999999999`, `0e999999999`, booleans, strings, null, arrays, objects, `NaN`, `Infinity`, `01`, `+1`, `.5`, `1.`, hex, Arabic digits. `note` 200/201 chars (code points), 200 emoji round trip, control chars/NUL/RTL/combining/HTML round trip verbatim, no trimming, lone surrogate no 5xx; handle case/space/NUL; visibility wrong values 422; self-payment precedence; non-JSON bodies, 5000-deep nesting, 5 MB bodies.
- **E splits (37):** 1000/3, 1/3, 10/3, 999/3, 5/5 vector; handle order moves the extra unit; zero shares still create requests; solo split -> `requests: []`; caller omitted; duplicates/empty/unknown/non-array/non-string; amount bounds; share property for amounts 1..39 (sum exact, spread <= 1, descending); split requests visible only to requester + payer; splits never in feed; third-party/requester/operator pay, cancel, decline -> 403; unknown id 404; decline twice 200; cancel after decline 409; over-balance request creates fine, pay while short 409 then pays after funding with the same key.
- **F feed (38):** public/private visibility matrix across 4 users (receiver sees private, third party and operator do not); `limit`/`offset` boundaries, `has_more`; 16 bad query forms (`1e2`, `4.0`, `+4`, empty, unicode digits, negative, repeated) -> 422; unknown query ignored; newest first; 401 variants; private request-payment visible to exactly 2 users; `/requests` direction/status filters and 422 forms.
- **G auth (24):** signup derivation, collisions (case-variant email, derived handle vs seeded handle, 20-char truncation), `handle_taken` creates no account, email/password validation, 50 concurrent signups with same email (1 x 201) and same derived handle (1 x 201), multiple tokens, tokens unique and >= 16 chars, 100 concurrent logins and 100 signups at 50 concurrency each < 5 s, health latency < 1 s during a 150-signup storm.
- **H settlements (32):** 401/403, shape, defaults, shared `created_at == committed_at`, `settlement_id` links, private members hidden from operator, entry-error precedence by input order (incl. before insufficient funds), 1..32 bounds, 300000-transfer body fast 422, invalid batch claims no key, replay 200 identical, other-body 409, non-operator with operator's key 403, operator cannot see others' requests, concurrent settlements keep the sum and no negatives.
- **I export/import/reset (55):** export shape; import restores balances, payments, requests (ids and timestamps equal), tokens, signup password login, replay of all four creating write paths (200 identical), operator rights, failed key reusable, pending request payable, no id collisions after new writes, import twice idempotent, export after import equals original, snapshot unaffected by later writes, 10 invalid imports (wrong track/version, missing/null/empty state, missing section, negative balance) -> 422 unchanged, bad JSON 400, no plaintext passwords in export, reset clears imported tokens and keys, exports taken under an 8-writer storm re-imported keep the sum, reset with negative balance 422 and state unchanged, 9 invalid fixtures, JPY/BHD, seeded payments/requests (private seeded payment hidden from operator, seeded balances not replayed), 2^53-1 balance arithmetic exact.
- **J protocol (19):** 404/405 error bodies, `Content-Type: application/json; charset=utf-8`, RFC 3339 offset timestamp, bad percent escapes, NUL and 5000-char paths, duplicate Idempotency-Key header, half-sent body, 300 idle sockets, chunked request body, huge Content-Length.
- **K fuzz (5):** 3000 mixed random operations at 50 concurrency (bad amounts, handles, visibilities, pays/cancels/declines) -> zero 5xx, balances non-negative and sum 13500, max latency < 5 s, each request paid at most once with exactly one payment.
- **L (2):** 40 concurrent writers while taking atomic exports for 6 s: every snapshot had balance sum 13500 and no negatives.
- **M extra (45):** responses lost after commit (client aborts, 5 duplicates, then retry) on all four creating paths -> one effect; reset during a 30-writer storm; reset/login/50 concurrent logins with 300/1000/2000 users; 20000 seeded payments reset < 10 s and 100 concurrent deep-offset feed reads; balances near 2^53.
- **N hardening (64):** exponent forms, auth header variants, error-body shape on all error classes, 50000/200000/1000000-deep array and object nesting -> 400 and alive, 100k-handle splits, 30 MB string body in 0.18 s, slow-drip body, 50 concurrent 5 MB writes all 201 < 5 s.
- **No outbound network:** container with `--network none` starts, answers health, resets and logs in.
