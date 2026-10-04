# Pocketful stage 1 - gate verdict, round 2

**VERDICT: REJECT**

Revision judged: `79b5f42ad91fb0daa491aa1f6ad6028d14ddebc7` (tree clean; `git diff 79b5f42 HEAD -- stage-1` is empty, later commits touch evidence only).
Adversary report considered: `evidence/stage-1/attack-report-r2.md`, commit `176cebc489f7a53264f316eba9e4b11d72bf6a29`, attacking `79b5f42...` (same revision).
Earlier verdict: `verdict.md` (9d0c224, REJECT for df8f825).

## What I ran myself
| Check | Result |
|---|---|
| Shipped harness, `--mode isolated --stage 1`, out dir `/Users/lakshmisravyarachakonda/darkfactory/band-work/checks/s1-gate-r2-1791141291` | stage 1 pass, "claimed stage: 1" |
| Analyst `docker_run_checks.sh` (build, 2 CPU/2 GiB, healthy 0 s, `--network none`, DOC-01..05, in-container suite, cross-container export->import) | 192/192 passed, all DOC ok |
| Prior G1 (50 x 42 MB / 50 x 10 MB / 1 x 21 MB bodies), in container | fixed: all 413, process alive, /health < 1 s |
| Prior G3 (20000-char Idempotency-Key) | fixed: 422 |
| Prior F3 reset cost, container | 2000 distinct-password users 2.99 s; seeded stored hashes now differ per user |
| Regression probes (bare node, same tree; Docker daemon failed mid-run) | 32-transfer settlement with 200-char notes (24 KB) 201, replay 200 identical; 100 KB padded body ok; 12.4 MB export -> import 204; seeded and signup login after import 200, wrong password 401, old token valid, settlement replay 200 |
| Adversary R2-1 reproduced | yes, see below |
| Code review of the diff vs spec | caps and header handling as described; no code shaped around tests |

Environment note: Docker Desktop's credential helper and later its daemon misbehaved on this host; builds used `DOCKER_BUILDKIT=0` with an empty docker config, Dockerfile unmodified. The adversary reported the same daemon failure and ran most of round 2 on bare node.

## Finding (must be fixed)

### H1 - The service refuses its own unchanged export once state is large (adversary R2-1) - reproduced
- Requirement: §10 "It must accept an unchanged export produced by this service." and "Import takes that entire object and atomically replaces the service's state"; test control calls have a 10 s timeout.
- Expected: `POST /_test/import` of any export this service produced returns 204.
- Actual: after 28000 API payments (200-char notes, 40-char keys) `GET /_test/export` returns 200 with 36,688,730 bytes; `POST /_test/import` of that body returns `413 payload_too_large` ("request body exceeds 33554432 bytes"). Roughly 1.3 KB per payment (payment + idempotency record), so about 25000 payments hit the cap. The export endpoint has no cap, so the service can produce a state it cannot take back.
- Reproduce: start the service; run the 28000 payment loop (8 keep-alive connections, `{"to_handle":"b","amount":1,"note":"n"*200}`, distinct 40+ char keys, payer balance 10^12); export; import it. My script: `/tmp/gate_r21.py` (output above: `payments 28000 export bytes 36688730`, `import 413`). Adversary's: `python3 evidence/stage-1/attacks/attack_r2_regress.py http://HOST:PORT http://HOST2:PORT G3`.
- Suggested: make the import cap track the largest export the service can produce (e.g. raise the control-path cap well above 32 MiB, keeping the shared budget), or bound exports so they never exceed what import accepts. The 413 caps on API bodies are fine and should stay.

## Non-blocking
- R2-2 (seeded users with the same password share the inner scrypt salt; stored strings differ via the per-user HMAC; equal passwords remain detectable and a cracker can reuse one scrypt across them; seeded cost N=4096 vs 16384 for signup): within the letter of §6, noted as hygiene.

## Adversary result summary
Round-1 findings F1, F2, F5 verified fixed by me and by the adversary; F3 improved (2.99 s for 2000 users in container, 1.2 s for 3000 on host unconstrained); F4 changed to R2-2. R2-1 reproduced (H1). The adversary's 449-check round-1 suite and its regression set (exact-cap bodies, 79 KB worst-case settlement, 5001-participant split, cross-process and cross-revision import, concurrency) found no further defect; I found none either.

Everything else passes. A new revision will be re-run in full.
