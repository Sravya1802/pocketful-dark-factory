# FACTORY

How this factory is built, why, what it cost, what it caught, and how to stand it up again.

## 1. Seats

| Seat | Harness | Model | Owns | Never does |
|---|---|---|---|---|
| coordinator | Claude Code | claude-sonnet-5-5 | routing, self-contained handoffs, stage log, final report | writes code, accepts work |
| analyst | Claude Code | claude-sonnet-5-5 | coverage matrix from the spec, black-box acceptance checks | reads product code |
| builder | Claude Code | claude-opus-5-5 | all product code, Dockerfile, RUN.md | accepts its own work |
| adversary | Claude Code | claude-sonnet-5-5 | attacks on the running container, reproducible attack scripts | reads or edits product code |
| gate | Claude Code | claude-sonnet-5-5 | reruns every check on the exact revision, accept or reject | edits anything but its verdict |

Mandates: `mandates/<seat>.md`. They name no track detail. Everything specific to pocketful lives in
the dispatched task (`factory/dispatch/pocketful-run.md`), which is the only place the spec is
referenced. The mandates are scanned against the vocabulary lists of every track before each run.

## 2. Design choices and why

- **No seat both produces and judges evidence.** The analyst derives checks from the requirements
  without seeing code. The adversary works black-box against the container. The gate reruns
  everything itself on the exact revision handed to it and accepts nothing on another seat's word.
- **Coverage matrix before code.** The shipped checks are a fraction of the judged suite (35% for
  stage 2, 9% for stage 3). The analyst maps every requirement sentence to a check and marks the
  rows no shipped check exercises. Stage 1: 214 rows, 58 not exercised and 39 only partly.
  Stage 2: 188 rows. The analyst also wrote a throw-away reference service and a mutation script
  (`evidence/stage-1/selftest/`) purely to prove its own checks are consistent and catch planted
  bugs. That file is a test instrument; the shipped service is the builder's `stage-N/` only.
- **One writer.** Only the builder touches `stage-N/`, so every product line traces to one seat's
  commits and to the handoff that asked for it. No human commit touches a stage folder.
- **The gate waits for the adversary.** Learned in rehearsal (see §5): the gate now gives no verdict
  until the adversary's report for the same revision has reached it, and every verdict cites it.
- **Lean, numbered handoffs.** Each handoff pastes the requirements the work item needs, split into
  numbered parts ("act only on the final part") when long, so no seat depends on room history.
- **Recovery ladder.** Three rejections per work item, then the coordinator splits it; three more on
  a split item and it is recorded as a blocker. A silent seat gets one resend.

## 3. Setup (stand it up yourself)

1. Band Desktop 0.4.12+, a BAND account, Claude Code CLI 2.1.286+ (older CLIs crash seats at
   start-up on an option BAND passes).
2. Create five seats as headless Claude Code agents named exactly coordinator, analyst, builder,
   adversary, gate. Instructions: the matching `mandates/*.md`. Working directory: the absolute
   path of your result repository. Tools allowed without prompts (an approval click is human input).
3. Before dispatching: charger connected, lid open, `caffeinate` on, and `band list` must show
   every seat Connected (seats can sit in "Reconnecting" after a sleep and silently drop work).
4. Create a fresh room, add the coordinator only, paste your task once, send nothing else.

## 4. How the factory catches and recovers from bad work

Loop per work item: analyse → build → attack and verify (in parallel; the gate holds its verdict for
the adversary) → rework → accept. Accepted stage folders are frozen and copied forward.

### Mistakes the factory caught (submitted run)

| # | Stage | Defect | Caught by | Confirmed by | Fixed in |
|---|---|---|---|---|---|
| G1 | 1 | Process crash (JS heap exhausted) under 50 concurrent large bodies | adversary F1 | gate, reproduced | `79b5f42` |
| G2 | 1 | Event loop stalled by large number-heavy bodies; 5 s budget exceeded | adversary F2 | gate, reproduced | `79b5f42` |
| G3 | 1 | Over-long Idempotency-Key returned 400 instead of 422 | adversary F5 | gate, reproduced | `79b5f42` |
| H1 | 1 | The fix for G1 capped bodies at 32 MiB, so the service refused its own export after ~28,000 payments | adversary R2-1 | gate, reproduced | `621342c` |

The gate's round-1 verdict also checked for code shaped around the shipped tests and found none.
Non-blocking findings were recorded, not hidden: shared salt for identical seeded passwords (fixed
anyway in `621342c`), a silent failed Refresh and Accept q-values on stage 2 (follow-ups in
`evidence/stage-2/verdict.md`).

### Stage 3 (rejected in round 1, accepted by the gate in round 2)

| # | Defect | Caught by | Confirmed by | Fixed in |
|---|---|---|---|---|
| K1 | Statement snapshots retained memory per snapshot until the service died | adversary S3-1 | gate, reproduced | `c5b8629` |
| K2 | A seeded expired hold with a future expiry disagreed between historical and current views | adversary S3-2 | gate, reproduced | `c5b8629` |
| K3 | RFC 3339 lowercase `t`/`z` and leap seconds rejected | adversary S3-3 | gate, reproduced | `c5b8629` |

Before any fix, the adversary's oracle fuzz found 0 mismatches in ~94,000 comparisons of the
corrected ledger. The adversary's round-2 report (`6f62bfe`) confirmed K1–K3 fixed, and the gate's round-2 verdict
(`8737866`) ACCEPTED `c5b8629`. The adversary's turn had ended without messaging the gate, so the
owner sent a factual note (human input #5); the gate then issued its verdict. One low finding remains (paging a
very old snapshot recomputes the view and slows at very large histories).

### Stage 4 at submission close (included, review not finished)

The analyst committed the stage-4 matrix and checks (`589e5f2`) and the builder committed refunds,
correction batches and snapshots across import (`7cd430a`). An isolated harness run on a fresh clone at
01:22 CDT reports stages 1–4 pass and `claimed stage: 4`. The coordinator handed it to the adversary
and gate (`f89e4ea`). The gate's own checks all passed and it issued a PROVISIONAL ACCEPT (`f89a76e`,
`evidence/stage-4/`), pending the adversary's report, which becomes final or turns into a REJECT when
that report arrives. Read stage 4 as provisionally accepted.

## 5. What we tried that failed, and what we changed

- **Toy rehearsal (4/4 stages, 1 real rework).** The gate ruled before the adversary reported in 4 of
  5 verdicts; once, the adversary's late findings forced the gate to withdraw an ACCEPT. Fix: the
  gate waits for the adversary and cites its report. Confirmed working in the submitted run.
- **The builder chased a flaky check it did not own.** Fix: flaky or contradictory checks are reported
  and routed back to the seat that owns them.
- **The Mac slept and seats stayed "Reconnecting" for hours.** A dispatch sat unread for 20 h. An
  operator restart of BAND then interrupted a seat mid-turn, so that run was abandoned (development
  runs are not judged) and the run was restarted in a fresh room and repository. Fix: run only on AC
  power with all seats Connected; never restart BAND while any seat is still acting.
- **A seat can finish its work but end its turn without the handoff message** (stage 3: the
  adversary committed its report but never messaged the gate). Next: the coordinator should poll
  for a committed report when a verdict is overdue.
- **Claude usage limits stop seats and nothing wakes them.** All five seats share one subscription.
  See §6.

## 6. Human input in the submitted run (disclosed)

The room holds five human messages:
1. The dispatch (the task, all four stages).
2. 13:12 CDT Oct 4: a factual resume note. The builder had stopped on a Claude usage limit 25 minutes
   after the dispatch; the limit reset, but nothing re-woke the seat. Wording: the seat stopped on a
   usage limit, the limit has reset, continue, the task is unchanged.
3. 21:13 CDT Oct 4: the same note after the analyst stopped on a usage limit during stage 3.
4. 23:17 CDT Oct 4: a note that the adversary had committed its stage-3 report but its turn ended
   without messaging the gate, so the gate was still waiting.

5. 00:58 CDT Oct 5: an owner note accepting stage 3 on the adversary's confirmed fixes and asking
   the coordinator to continue to stage 4 (the gate's formal ACCEPT followed).

One operator action outside the room: at 00:07 CDT Oct 5 the adversary's runtime turn was hung with
three messages queued behind it, and was interrupted with `band runtime interrupt` (no message, no
content). The seat then picked up its queued handoff by itself.

No message steered the work, approved anything, or hinted at a fix. Every rejection and repair above
came from the seats alone.

## 7. Measured cost and time

Claude Code session logs, one per seat, priced at Claude API list prices (Opus 5.5 $4/$20, Sonnet 5.5
$2/$10 per million input/output tokens; cached reads $0.20; cache writes 1.25x input). The seats ran on
a subscription, so this is an API-equivalent figure. Script: `factory/scripts/seat-costs.py`.

| Seat | Model | Turns | Input tokens | of which cached | Output tokens | API-equiv $ | Share |
|---|---|---|---|---|---|---|---|
| builder | claude-opus-5-5 | 206 | 67,873,876 | 64,678,124 | 321,980 | $44.94 | 36% |
| adversary | claude-sonnet-5-5 | 233 | 84,576,310 | 81,509,139 | 438,204 | $32.95 | 27% |
| analyst | claude-sonnet-5-5 | 147 | 60,589,256 | 57,910,452 | 598,836 | $28.29 | 23% |
| gate | claude-sonnet-5-5 | 168 | 36,700,919 | 35,597,147 | 119,739 | $12.73 | 10% |
| coordinator | claude-sonnet-5-5 | 80 | 9,083,194 | 8,430,896 | 67,104 | $4.97 | 4% |
| **total, stages 1–4** | | 834 | 258,823,555 | 248,125,758 | 1,545,863 | **$123.87** | |

Two thirds of the spend went on checking (analyst, adversary, gate), one third on building.
96% of input tokens were cache reads, which is what lean, self-contained handoffs buy.
Toy rehearsal for comparison: $19.65, 343 turns, 4 stages.

Time (CDT, Oct 4), excluding the usage-limit stall from 03:29 to 13:12:

| Step | Time |
|---|---|
| Dispatch → stage 1 analysis committed | 03:04 → 03:26 (22 min) |
| Stage 1 build → round 3 ACCEPT | 13:12 → 15:21 (2 h 9 min, 2 rejections) |
| Stage 2 analysis → ACCEPT | 15:21 → 17:01 (1 h 40 min, 0 rejections) |
| Stage 3 analysis → ACCEPT | 21:13 → 00:59 (1 rejection; includes ~1.5 h of stalls on a hung seat turn) |
| Stage 4 analysis → build → gate provisional ACCEPT | 00:59 → 01:32 (33 min) |

## 8. Evidence map

| Claim | Evidence |
|---|---|
| Five distinct seats did the work | reciprocal `@handle` exchanges in `room.json`; commits authored per seat (`git shortlog -sn`) |
| Review changed the work | G1–G3 and H1 above: adversary report → gate reproduction → builder fix commit |
| The gate accepted exactly what ships | `evidence/stage-1/verdict*.md`, `evidence/stage-2/verdict.md` name the full revision |
| The gate waited for the adversary | verdicts cite the adversary report on the same revision; gate "waiting for adversary" messages in the room |
| Stages reproduce from a clean clone | isolated harness on a fresh clone: `stage-1/` claims 1, `stage-2/` claims 2, `stage-3/` claims 3 |
| Costs are measured | §7 |
| Mandates are generic | no track vocabulary in `mandates/` (checked against all three track lists) |

## 9. Reproducibility pins

- Kickoff repository: `band-ai/dark-factory-wearedevs` @ `803560d`; harness from the same checkout
- Band Desktop / CLI 0.4.12, Claude Code CLI 2.1.286, Docker 29.1.3, macOS 26 on Apple silicon
- Submitted run: dispatched 2026-10-04 08:04 UTC in a fresh room and fresh repository
- Accepted commits: stage 1 `621342c`, stage 2 `92bbbf9`, stage 3 `c5b8629`; stage 4 `7cd430a` (unreviewed)
