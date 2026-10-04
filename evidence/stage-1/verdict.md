# Pocketful stage 1 - gate verdict

**VERDICT: REJECT**

Revision judged: `df8f82520aad388c60c223bd2a4067ec2f79476a` (product code in `stage-1/`; the tree at the later commits
`1343fa7` and `e0f26b6` differs from it only in `evidence/`, `git diff df8f825 HEAD -- stage-1` is empty). Tree clean when checked.
Adversary report considered: `evidence/stage-1/attack-report.md`, commit `e0f26b6` (adversary: stage 1 attack report and scripts),
attacking `df8f825...`. Same revision.

## What I ran myself
| Check | Result |
|---|---|
| Shipped harness `python -m harness run --track pocketful --stage 1 --mode isolated` (out dir `/Users/lakshmisravyarachakonda/darkfactory/band-work/checks/s1-gate-1791140644`) | stage 1 pass, "claimed stage: 1" |
| Analyst `docker_run_checks.sh stage-1` (image build, 2 CPU / 2 GiB, healthy 0 s, `--network none`, DOC-01..05, in-container `run_checks.py`) | 192/192 passed; all DOC ok; export->import across containers ok |
| Earlier-stage checks | none exist (stage 1) |
| Adversary findings F1-F5 reproduced | F1, F2, F3 (as risk), F5 reproduced; see below |
| UI checks | not applicable (HTTP API only) |
| Matrix rows not covered by shipped checks (58 N, 52 P) | covered by analyst checks (all pass) plus code read of `src/app.js`, `src/state.js`, `src/json.js`, `src/passwords.js`; the 7 not-testable rows hold (no extra endpoints, no admin balance, no non-HTTP interface) |
| Code shaped around tests? | none found. The seed-password hash memo (`hashSeedPassword`) is a reset-speed optimisation, not test detection; noted under F4 |

Environment note: Docker Desktop's credential helper hung BuildKit's metadata step on this host, so image builds were run with
`DOCKER_BUILDKIT=0` and an empty docker config against the already-pulled `node:22-alpine`. The Dockerfile itself was used unmodified.

## Findings (all must be fixed before acceptance)

### G1 - Process crash under 50 concurrent large bodies (adversary F1) - reproduced
- Requirement: §2 up to 50 requests in flight within 2 GiB; §5 "Requests must not produce 5xx responses, including under concurrent load"; §1 in-memory state means a crash loses all state.
- Expected: oversized/large bodies are rejected (413) or handled in bounded memory; the service stays up.
- Actual: `server.js` allows 64 MB per body and buffers all of them (`MAX_BODY`), so 50 x 42 MB bodies exhaust the V8 heap: `FATAL ERROR: Reached heap limit ... out of memory`, container `Exited (139)`, connections reset, `/health` dead.
- Reproduce: `docker run -d -e PORT=8080 -p 18090:8080 --cpus 2 --memory 2g <image>`; `python3 evidence/stage-1/attacks/bigbody_storm.py http://127.0.0.1:18090 50 40`. I got statuses 201 / RemoteDisconnected / timeouts, then `ConnectionResetError`, exit 139.
- Suggested: cap request bodies at a few MB (413 `payload_too_large` per §5 shape), reject on `Content-Length` early, and stop reading once over the cap.

### G2 - Event loop stalled by large number-heavy bodies; 5 s budget exceeded (adversary F2) - reproduced
- Requirement: §2 per-request timeout 5 s at up to 50 in flight.
- Expected: requests complete within 5 s while others are in flight.
- Actual: 50 concurrent 10.5 MB bodies: max request latency 14.3 s, max `/health` latency 13.7 s (one 21 MB body alone stalls everything ~1.4 s) because the hand-written JSON parser runs synchronously on the single thread.
- Reproduce: `python3 evidence/stage-1/attacks/bigbody_storm.py http://127.0.0.1:18090 50 10`.
- Suggested: the body cap from G1 removes this; parsing a few-MB body is then sub-second.

### G3 - Idempotency-Key longer than Node's header limit gives 400, not 422 (adversary F5) - reproduced
- Requirement: §5 shared ranges: `Idempotency-Key` 1 to 255 characters, otherwise 422 `validation_failed`.
- Expected: a 20000-character key -> 422 `validation_failed`.
- Actual: 400 `malformed_request` ("malformed HTTP request") from the `clientError` handler (header exceeds Node's 16 KB limit). 300 and 5000 chars give 422 correctly.
- Reproduce: `curl -XPOST $URL/payments -H "Authorization: Bearer $T" -H "Idempotency-Key: $(head -c 20000 /dev/zero | tr '\0' k)" -d '{"to_handle":"b","amount":1}'` -> 400.
- Suggested: raise `maxHeaderSize` (e.g. 128 KB) so an over-long key reaches the 422 check, and/or map header-overflow `clientError` for an `Idempotency-Key` to 422.

## Non-blocking observations (not findings; recorded for the next stage)
- Adversary F3 (reset latency with many distinct-password users): reproduced, 1000 distinct passwords reset in 8.73 s here (limit 10 s). Not exceeded, but with a slower judge host a ~1200-user fixture could time out. Consider hashing with fewer rounds in parallel, lazily on first login, or a cheaper cost for seeded users.
- Adversary F4 (seeded users with the same password share salt+hash): within the letter of §6 (scrypt is used, signups get unique salts); a weakness in stored-data hygiene.
- Adversary's "observations" (0-amount pay allowed, 403 for third parties, huge `Content-Length` with no body held open, BOM accepted) agree with the spec's per-endpoint tables or are unspecified; I do not hold them against the build.

## Adversary result summary (every reported failure)
F1 reproduced (G1), F2 reproduced (G2), F3 reproduced as a risk only (not a spec failure at 8.73 s), F4 reproduced as observation (not a spec failure), F5 reproduced (G3). The adversary's 447-check suite otherwise passed per its report; the races, idempotency, number-parsing, split, feed, settlement and export/import attacks found no defect, and my own review of the handler code (single synchronous state mutation, per-user-per-path idempotency recorded only on success, atomic settlement netting) agrees.

Everything else in the spec (shipped harness, 192 analyst checks, deployment checks) passes. Re-check after the fixes will run the same set against the new revision.
