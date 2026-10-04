# Stage 1 attacks (adversary)

All scripts are Python 3 stdlib only. They RESET the service state.

- `attack.py URL [A B C ...]` - 448 black-box checks in groups A-N (A races, B overdraft/settlement atomicity, C idempotency,
  D numbers/validation, E splits/request permissions, F feed/pagination/queries, G auth/hashing load, H settlements,
  I export/import/reset, J protocol, K fuzz 3000 ops @50, L invariants sampled during a write storm, M extra, N hardening).
  Exit 1 on any FAIL. `run-docker.txt` / `run-host-node.txt` are the recorded outputs.
- `bigbody_storm.py URL [N] [MB]` - N concurrent large JSON bodies (finding F1/F2).
- `reset_hash_cost.py URL [N ...]` - reset latency with N distinct-password seeded users (finding F3).

Image under test: `docker build` of revision df8f825 (`DOCKER_BUILDKIT=0` was needed locally only because BuildKit stalled on
registry metadata), run with `--cpus 2 --memory 2g`; separate container with `--network none` for the no-network start.
Reproduce F1: `docker run -d --name pf -e PORT=8080 -p 18080:8080 --cpus 2 --memory 2g pocketful && python3 bigbody_storm.py http://127.0.0.1:18080 50 40`
