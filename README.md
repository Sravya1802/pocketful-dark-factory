# Pocketful Dark Factory: proof before ship

> A dark factory that refuses to ship unless another seat can reproduce, attack and explain the result.

Track: **pocketful** · Team: Sravya Rachakonda (solo) · WeAreDevelopers x BAND Dark Factory, October 2026

## For judges (90 seconds)

1. **What this is.** Five BAND Desktop seats (coordinator, analyst, builder, adversary, gate)
   built this wallet service from one dispatched message. Only the builder writes product code.
   The analyst turns the spec into a coverage matrix and black-box checks before any code exists.
   The adversary attacks the running container. The gate reruns everything on the exact revision
   and gives no verdict until the adversary's report for that revision is in.
2. **Stages complete.**

   | Stage | Folder | Accepted commit | Rounds | Shipped checks, isolated mode, fresh clone |
   |---|---|---|---|---|
   | 1 | `stage-1/` | `621342c` | 3 (2 real rejections) | claims stage 1 |
   | 2 | `stage-2/` | `92bbbf9` | 1 | claims stage 2 |

   Stage 3 was built and attacked but not accepted before submissions closed (see FACTORY.md §4), so it is not included.

3. **Run it.** Each folder has a `Dockerfile` and `RUN.md`. For example:
   ```sh
   docker build -t pocketful-s2 stage-2
   docker run --rm -p 8080:8080 pocketful-s2
   # open http://localhost:8080
   ```
4. **Factory evidence.**
   - `mandates/`: the five generic seat mandates (no track vocabulary; scanned against all tracks)
   - `room.json`: the full, unedited BAND room download
   - `evidence/stage-log.md`: every handoff, verdict and rework loop, kept by the coordinator
   - `evidence/stage-N/`: coverage matrix, acceptance checks, attack reports, gate verdicts
   - `FACTORY.md`: setup, design, measured cost, mistakes caught, honest limitations
   - `factory/`: the dispatch message and the operator scripts used around the run
5. **Video.** See the lablab submission.
6. **Known limitations.** Two stages of four. Three human messages in the room: the dispatch
   and two factual "resume after Claude usage limit" notes (details in FACTORY.md §6).
