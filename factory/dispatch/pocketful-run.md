<!-- The submitted run. Paste everything below the line ONCE into a FRESH room, addressed
     to @coordinator, with a FRESH band-work/result repository. Send nothing else until the
     coordinator's final report: any follow-up message is steering and costs the
     Agent Teamwork score. Start screen recording before you paste. -->
---
@coordinator Build the pocketful wallet service, all four stages in order, as a dark run.
This message is the only human input. Do not ask me anything until your final report.

Workspace root: /Users/lakshmisravyarachakonda/darkfactory
Kickoff package (read-only): /Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs
Result repository: /Users/lakshmisravyarachakonda/darkfactory/band-work/result
Specs, one per stage, in order:
  /Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/pocketful/spec/stage-1.md
  /Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/pocketful/spec/stage-2.md
  /Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/pocketful/spec/stage-3.md
  /Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs/pocketful/spec/stage-4.md

Order of work
- Finish stage N completely, including @gate's acceptance, before starting stage N+1.
- Paste the full text of the current stage's spec into every handoff for that stage.
  Work items within a stage may be smaller than the whole spec, but each handoff carries
  the complete spec sections its work item depends on.

Stage folders
- Stage 1 goes in stage-1/. The repository starts empty; the builder chooses the stack.
- When stage N is accepted, copy stage-N/ to stage-(N+1)/, delete any .git inside the
  copy, and extend the copy. Never edit an accepted stage folder again.
- Each stage folder is a complete service with its own Dockerfile and RUN.md, builds from
  a clean checkout, and solves its own stage only. A folder that also passes the next
  stage's whole suite claims nothing, so do not build later-stage features early.
- Each stage must import state exported by every earlier stage.

Build to the spec, not the tests
- The shipped checks are only part of the judged suite: about 79% of stage 1, 35% of
  stage 2, 9% of stage 3 and 16% of stage 4. Every judged test is written in the spec.
- Code shaped around the shipped tests disqualifies the entry. When a stage looks done,
  re-read its spec and ask what the shipped checks never asked for.

Engineering stance for this service
- Amounts are exact integer minor units end to end. No floating point anywhere near money.
- Idempotency records are stored per caller and key, with a fingerprint of the request
  and the full original response. A replay returns the original response; the same key
  with a different request is refused as the spec says. Concurrent identical requests
  with a fresh key move money exactly once.
- Every operation that moves or holds money runs under one lock or one transaction, so
  the three invariants hold at every read: balances sum to the seeded total, no balance
  is ever negative, and a request moves money at most once.
- Never hash a password while holding the state lock; hashing is slow on purpose and 50
  requests may be in flight inside a 5-second timeout.
- The HTTP server must accept at least 50 concurrent connections (a listen backlog well
  above 50) and start within 60 seconds on 2 vCPU and 2 GiB.
- Nothing at runtime reaches the network: no CDN fonts, scripts or images, no downloads
  at startup. Install everything while building the image.

Stage-2 product quality (the app is a quarter of the score)
- It must feel like a presentation-ready consumer finance product, not a test page.
- The spendable amount is the headline number; total and held amounts are secondary.
- Pay, request, split and authorization flows show pending, success, refused and
  uncertain states distinctly. A lost response shows the uncertain state, keeps the form
  filled, and retries with the same key and body.
- Refreshing never clears a filled form, and an older response never overwrites a newer one.
- Works at 375 CSS pixels and at desktop width with no horizontal scrolling; visible
  labels, visible keyboard focus, sufficient contrast. @gate checks both widths in a
  real browser.

Evidence
- Everything that is not product code goes in evidence/ at the repository root:
  evidence/stage-log.md (coordinator), evidence/stage-N/coverage.md and acceptance checks
  (analyst), evidence/stage-N/attack-report.md and attack scripts (adversary),
  evidence/stage-N/verdict.md (gate).
- Every seat commits its own work with its seat name as the Git author:
  git -c user.name=<seat> -c user.email=<seat>@factory.local commit -m "<message>"
- Never amend, rebase or squash. The history is evidence.

Checks (the gate runs these itself; the builder may run them too)
  cd /Users/lakshmisravyarachakonda/darkfactory/dark-factory-wearedevs
  .venv/bin/python -m harness run --track pocketful --repo /Users/lakshmisravyarachakonda/darkfactory/band-work/result --stage N --mode isolated --out /Users/lakshmisravyarachakonda/darkfactory/band-work/checks/sN-<unique>
A stage is accepted only when that run ends with "claimed stage: N" AND the analyst's
acceptance checks and the adversary's reproductions pass. Every --out directory must be new.

When all four stages are accepted, or a stage is blocked after the recovery ladder, send
me the final report: accepted revision per stage, blockers, rework loops per stage, and
total elapsed time.
