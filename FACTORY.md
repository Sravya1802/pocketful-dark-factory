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
  Stage 2: 188 rows, 87 API checks plus browser checks. Stage 3: 110 checks. Every stage re-runs all
  earlier checks. The analyst also wrote a throw-away reference service and a mutation script
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

### Generic in practice

The same five mandates built two unrelated products: the unscored `toy` track (a shared counter,
4/4 stages, one real rework loop) and this pocketful wallet (4/4 stages, seven defects caught). Between
the two runs only two generic rules changed (the gate waits for the adversary; flaky checks go back to
their owner). Nothing in `mandates/` names either problem: every track-specific word lives in the
dispatched task. To aim the factory at a different spec, change the dispatch, not the mandates.

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
(`8737866`) ACCEPTED `c5b8629`. In round 1 the adversary's turn ended without messaging the gate (human input #4
pointed that out, and the coordinator relayed the report). In round 2 the same happened; the owner
accepted stage 3 on the confirmed fixes (human input #5) and the gate then issued its formal verdict. One low finding remains (paging a
very old snapshot recomputes the view and slows at very large histories).

### Stage 4 (accepted by the gate in round 1)

The analyst committed the stage-4 matrix and checks (`589e5f2`); the builder committed refunds,
correction batches and snapshots across import (`7cd430a`). The gate's own checks passed and it held a
provisional verdict (`f89a76e`) until the adversary's report arrived (`202894b`): its oracle found
**0 failures in 59,610 comparisons** of refunds and batches. The gate reproduced the two low findings,
recorded them as non-blocking follow-ups, and issued its final ACCEPT (`eeea755`,
`evidence/stage-4/verdict-final.md`). The coordinator closed the run: all four stages accepted.

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

## 6. Human input, audited

Every human word in the run is on the record, and none of it steered the work. Of **2,312 messages**
in the room, **5 are human (0.2%)**. After the dispatch, the four notes total **220 words**, contain
**no technical instruction**, and each one restarts a seat whose stop is visible in the room itself.

| # | Time (CDT) | Trigger, visible in `room.json` | What the note said | Words | What the band did next |
|---|---|---|---|---|---|
| 1 | Oct 4 03:04 | none: the dispatch | the task, the four specs, the engineering stance | 737 | analyst committed the stage-1 matrix 22 min later |
| 2 | Oct 4 13:12 | builder: "You've hit your session limit · resets 5:50am" | builder stopped on a usage limit; limit reset; continue; task unchanged | 53 | coordinator re-sent the same handoff; builder committed stage 1 18 min later |
| 3 | Oct 4 21:14 | analyst: "You've hit your session limit · resets 6:10pm" | same note, for the analyst in stage 3 | 54 | coordinator re-sent the handoff; analyst committed in 23 min |
| 4 | Oct 4 23:28 | adversary committed its report (`af638e1`) but never messaged the gate | that fact, and that the gate was waiting | 51 | coordinator relayed the report; gate REJECTED stage 3 (K1–K3) on its own evidence |
| 5 | Oct 5 00:58 | same failure in round 2: report `6f62bfe` committed, gate not messaged | owner accepts stage 3 on the adversary's confirmed fixes; continue | 62 | gate issued its own formal ACCEPT one minute later; stage 4 began |

The test we hold ourselves to: **remove any note and the code the band wrote does not change, only
when it was written.** No note names a file, a function, a fix or a check. Every rejection and every
repair in §4 came from the seats.

One operator action outside the room: at 00:07 CDT Oct 5 the adversary's runtime turn was hung with
three messages queued behind it and was interrupted with `band runtime interrupt` (no message, no
content); the seat then picked up its queued handoff by itself.

### Each human input maps to a generic fix (the path to one message)

| Notes | Root cause | Generic fix for the next run |
|---|---|---|
| 2, 3 | A seat stopped by a usage limit is never re-woken | API-key seats (no subscription limit), or a runtime watchdog that re-delivers the last handoff after the reset time printed in the seat's own error |
| 4 | A seat ended its turn without its handoff message | coordinator mandate: when a verdict is overdue, look for a committed report in the repository and relay it |
| 5 | Same, one round later | gate mandate: when waiting on a report, also look for it in the repository |

None of these fixes names a track, so they belong in the mandates. With them, this run would have
needed only the dispatch.

### Working time versus stalls

The four stages took about **9 hours of working time**. About **30 hours** were stalls, all external:
Claude usage limits (9.5 h, 4 h) and the laptop sleeping (16.5 h). The run report shows every stall
on the timeline instead of hiding it: https://sravya1802.github.io/pocketful-dark-factory/

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

Time (CDT, Oct 4 to 5), excluding the usage-limit stall from 03:29 to 13:12:

| Step | Time |
|---|---|
| Dispatch → stage 1 analysis committed | 03:04 → 03:26 (22 min) |
| Stage 1 build → round 3 ACCEPT | 13:12 → 15:21 (2 h 9 min, 2 rejections) |
| Stage 2 analysis → ACCEPT | 15:21 → 17:01 (1 h 40 min, 0 rejections) |
| Stage 3 analysis → ACCEPT | 21:13 → 00:59 (1 rejection; includes ~1.5 h of stalls on a hung seat turn) |
| Stage 4 analysis → build → gate provisional ACCEPT → final ACCEPT | 00:59 → 01:32; final verdict after the adversary's report (laptop slept in between) |

## 8. Evidence map

| Claim | Evidence |
|---|---|
| Five distinct seats did the work | reciprocal `@handle` exchanges in `room.json`; commits authored per seat (`git shortlog -sn`) |
| Review changed the work | G1–G3 and H1 above: adversary report → gate reproduction → builder fix commit |
| The gate accepted exactly what ships | `evidence/stage-1/verdict*.md`, `evidence/stage-2/verdict.md`, `evidence/stage-3/verdict*.md` name the full revision; stage 4's final verdict is `evidence/stage-4/verdict-final.md` |
| The gate waited for the adversary | verdicts cite the adversary report on the same revision; gate "waiting for adversary" messages in the room |
| Stages reproduce from a clean clone | fresh GitHub clone, isolated harness: `stage-1/`…`stage-4/` each claim their stage; every stage serves `/health` with `--network none` (`evidence/final-check/`) |
| Costs are measured | §7 |
| Mandates are generic | no track vocabulary in `mandates/` (checked against all three track lists) |

## 9. Reproducibility pins

- Kickoff repository: `band-ai/dark-factory-wearedevs` @ `803560d`; harness from the same checkout
- Band Desktop / CLI 0.4.12, Claude Code CLI 2.1.286, Docker 29.1.3, macOS 26 on Apple silicon
- Submitted run: dispatched 2026-10-04 08:04 UTC in a fresh room and fresh repository
- Accepted commits: stage 1 `621342c`, stage 2 `92bbbf9`, stage 3 `c5b8629`, stage 4 `7cd430a`
