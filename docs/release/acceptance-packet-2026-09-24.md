# Release acceptance packet (2026-09-24)

This packet records the candidate that the review-remediation and journey-coverage plans
produced, the gates it passed, and what remains open. It asserts no release approval; publishing
is a separate decision.

## Candidate

| Field | Value |
| --- | --- |
| Commit | `d770f46ff488572eb08dcabd34e2b5784c0ec59b` on `main` (152 commits ahead of `origin/main` when frozen) |
| Candidate id | `sha256:ea626cea9cee9c1b8c93705f713a1e41b529486d1b4c3b535ae392167acce664` |
| Snapshot | `uv run --frozen python -m tools.release_acceptance.candidate --root . --output $PITWALL_EVIDENCE_ROOT/release-acceptance/candidate-20260924/candidate.json` |
| State | Clean tree, no candidate issues, `release_ready` true (source identity only) |

This packet was committed after the freeze. It is documentation only and does not change the
code the gates ran.

## Gates on the frozen tree

| Gate | Command | Result |
| --- | --- | --- |
| Journeys and matrix | `scripts/release/run-user-journeys.sh` against the local test Postgres and Redis | 42 passed, 0 failed. Matrix: 1270 surfaces, 1483 bound cases, 0 unbound, 0 failing |
| Hermetic | `make test-fast` | 6452 passed, 432 skipped |
| Integration | `make test-int` | 168 passed, 2 skipped |
| Lint and format | `uv run --frozen ruff check .`; `ruff format --check .` | Pass; 1301 files formatted |
| Types | `uv run --frozen mypy --strict src/` | No issues in 317 source files |
| Security | `make sec` | Bandit, pip-audit, reviewed secret baseline, and license policy pass |
| Docs | `make docs-check` | 412 files pass |
| OpenAPI | `make openapi-check` | Compatibility passed |
| Workflows | `make ci-tools` | 17 passed |
| Agent Routing | `.venv/bin/python -m unittest discover -s tests` (in `packages/agent-routing`) | 824 tests OK, 4 skipped |
| Gateway | `tsc --noEmit`, `eslint .`, `vitest run` (in `packages/gateway`) | Pass; 132 tests |
| Pi Workbench | `tsc --noEmit`, `vitest run` (in `packages/pi-workbench`) | Pass; 277 passed, 1 skipped |

## Live evidence for this candidate

| Evidence | Result | Spend |
| --- | --- | --- |
| [Personal serve round trip](../evidence/2026-09-23-personal-serve-live.md) | Passed on an RTX 3090: serve, key check, route probe, and the in-pod timer terminating the pod at its TTL | About $0.46 over 13 attempts |
| [RunPod lifecycle](../evidence/2026-09-23-runpod-candidate-live.md) | Registry lease end to end with proxied chat, stop, and audit rows; endpoint and volume create, read, update, and delete | About $0.15 |
| [Free-pool benchmark](../research/2026-09-10-free-pool-benchmark.md) | 36 keyless pools, all `kill`; applied to the seed | $0 |
| [RA-050 stress](../evidence/2026-09-23-ra050.md) | 200 of 200 stressed runs passed | $0 |
| [Earlier receipts](../evidence/2026-09-23-release-acceptance-live.md) | Summaries of the 2026-09-20 and 2026-09-21 installed-candidate runs | — |

Against the [support matrix](../support-matrix.md), these runs add live evidence for two rows.
"RunPod pod leases and serve" now has live personal and registry serves. "RunPod resource
control" now has live pod, serverless endpoint, and volume operations; templates, registry auth,
and Hub were not exercised.

## Exceptions and open items

| Item | Status | Scope |
| --- | --- | --- |
| RA-050 | Open, not reproduced | The test's diagnostics now report the request's state at a readiness timeout; the next CI failure identifies the cause |
| Vast.ai, Together, and Lambda Cloud countersigns (plan Task 21) | Not authorized | Rows stay "Code-complete; countersign pending" |
| Orchestrator channel live exits per harness (plan Task 22) | Live | Ask, answer, and steer acknowledgement ran live with Claude Code, OpenCode, Codex, Qwen Code, Cline, and Kimi Code children and a GitHub Copilot CLI parent, and Phase D ran live on 2026-09-25 ([channel evidence](../evidence/2026-09-25-channel-live.md)) |
| Pi terminal-driving timeouts and comparison reruns (plan Tasks 24 and 25) | Closed | The historical timeouts were a test-driver defect, and the same journey passed twice on the current Workbench (`packages/pi-workbench/docs/baseline-results.md`); the comparison rerun gave 12/12 core tasks, and warm reuse and admission queue timing were measured live (`packages/pi-workbench/docs/comparison-suite.md`); M02 is recorded in `source-lock.md` |
| Keyless free pools | None usable | Every keyless pool fails anonymous callers; free routing needs keyed pools |

## Known limits

The limits are those in the [support matrix](../support-matrix.md): single-operator deployment,
local-stdio MCP, Linux-only broker images, and no in-repository GPU worker image. In addition,
RunPod's pod-create API can request CUDA only up to 13.0, so a GPU class offered only with newer
drivers is refused as `cuda_unavailable`.
