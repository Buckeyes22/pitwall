# Intent versus implementation review (2026-09-22)

This review read the project documentation first (README, CHANGELOG, roadmap, ADRs 0001–0007,
the SDLC set, the specs, every plan, research, and evidence) to establish what Pitwall is meant
to be, then checked the code and its tests against that intent. It was run against `origin/main`
at `66f3231` and re-checked on 2026-09-23 against the local integration of every branch and
worktree (`integration/2026-09-22`). The remediation plan is
[`2026-09-23-review-remediation.md`](../superpowers/plans/2026-09-23-review-remediation.md); the
journey-coverage plan is
[`2026-09-23-journey-coverage.md`](../superpowers/plans/2026-09-23-journey-coverage.md).

## 1. Intended product

Pitwall began (0.1.0a2, 2026-07-18) as a single-operator RunPod GPU broker: capability routing,
Decimal cost admission before spend, an audit, a kill switch, and Postgres/Redis reconciliation
behind REST, MCP, and a CLI. On 2026-09-02 the personal-first design made the primary purpose
"spin up a GPU pod, serve an open-weight model, use it from a coding agent" with only a RunPod
credential, and renamed everything to `pitwall`. By 2026-09-10 the stated destination was a
three-pronged personal AI infrastructure: subscription harness routing (Agent Routing), a
free-tier model gateway so coding does not stop, and self-serve pods, with the budget gate and
kill switch above all three. The 2026-09-19 and 2026-09-20 documents add a Pi interactive
workbench and a release acceptance matrix that requires every advertised surface to be proven.

## 2. Verification performed

| Command (review worktree at `66f3231`) | Result |
| --- | --- |
| `uv run pytest -q -m "not integration and not slow"` | 5,327 passed, 2 failed, 52 skipped |
| `make test-int` equivalent with the test stack | 139 passed, 5 skipped |
| `scripts/release/run-user-journeys.sh` | 27 passed, 0 failed |
| Agent Routing `python3 -m unittest discover -s tests` | 717 tests OK (4 skipped) |
| `packages/gateway`: typecheck, lint, `npm test`, build | clean; 18 tests passed |
| Scheduled CI (`gh run list --workflow CI --event schedule`) | failed every week from 2026-08-17 to 2026-09-21 |

## 3. Findings and their status

| # | Finding | Evidence | Status on 2026-09-25 |
| --- | --- | --- | --- |
| 1 | The free-tier gateway relays every request to one upstream (`PITWALL_GATEWAY_UPSTREAM_URL`, default `http://127.0.0.1:65535/v1`), while all 41 enabled seeded providers (Pollinations, OVHcloud, AI Horde, uncloseai, SparkDesk, Groq) point at it with different model ids | `packages/gateway/src/config.ts:120`, `seed/gateway-providers.yaml` | Closed: each route names its own upstream through `PITWALL_GATEWAY_ROUTES`, and the placeholder default is removed (remediation Task 8) |
| 2 | The Phase 0 free-pool benchmark carries placeholder numbers whose `keep` verdicts back enabled rows | `docs/research/2026-09-10-free-pool-benchmark.md` | Closed: rerun on 2026-09-23 with real numbers; all 36 keyless pools are `kill` and the verdicts are applied to the seed (Task 19) |
| 3 | The in-pod deadline timer calls `DELETE /v2/pods/{id}` and `POST /v2/pods/{id}/stop`; neither is in the captured v2 contract, which routes lifecycle through `pods/{id}/action`; its failure output is discarded | `src/pitwall/personal/deadline.py:8-15`, `tests/fixtures/runpod_v2_contract_2026-08-31.json` | Closed: the timer posts to `pods/{id}/action` and keeps its output (Task 7); the live run in finding 4 terminated at its TTL |
| 4 | The personal live test that decides whether the pod key may terminate its own pod has no recorded run | `tests/live/test_personal_serve_live.py` | Closed: [live run of 2026-09-23](2026-09-23-personal-serve-live.md) (Task 18) |
| 5 | Ten dossiers declare `min_cuda: \"12.8"`, which loads as a string containing a backslash and quotes; `Variant.min_cuda` accepts any string and registry serve raises `ValueError` on it | `docs/models/*.md`, `src/pitwall/models/schema.py:78` | Closed: catalogue load rejects an invalid `min_cuda` and the dossiers are corrected (Task 5) |
| 6 | The personal path sends the minimum CUDA version as the only allowed version; registry serve does the same when live CUDA data is unavailable. RunPod treats `allowedCudaVersions` as an exact set | `src/pitwall/personal/service.py:365`, `src/pitwall/serve.py:860-864` | Closed: both serve paths allow every CUDA version at or above the floor (Task 6) |
| 7 | Real-database regression tests for live defects 12 and 17 were hard-skipped | `tests/leases/*` on `main` | Closed: `tests/integration/test_provisioning_budget_lifecycle.py::test_arm_provider_rolls_back_when_audit_insert_is_rejected` and `tests/integration/test_lease_acceptance.py` |
| 8 | Scheduled CI red: Trivy HIGH/CRITICAL in the pinned `python:3.14.7-slim` digest; mutation-smoke cannot find Python 3.14.7; highest-resolution schemathesis fails to import `starlette_testclient`; external link check 404s on the private repository; `Retry-After` timing flake | CI run of 2026-09-21 | Fixed in Tasks 9–12; see section 5 for the local run of each scheduled-only job |
| 9 | Two gateway-supervisor tests read the real `PATH` and fail wherever `pitwall-gateway` is installed and the checkout is unbuilt | `tests/personal/test_gateway_supervisor.py`, `src/pitwall/personal/gateway.py:29-42` | Closed: the tests no longer read the real `PATH` (Task 4) |
| 10 | Pi route sync replaced enriched provider blocks and ignored limit drift (F1/F2); the workflow runner rejected Pi (F5) | `providers/pi.py`, `scheduler.py` | Closed by the pi-workbench merge; validation rejects non-workflow providers through `workflow_supported` |
| 11 | Documentation drift: CHANGELOG counts 49 MCP tools and 52 REST operations; the OpenAPI baseline and testing strategy say 99 operations (102 live before the first-wave merge); the overview says a 16-point audit (19 checks); J09 expects six screens (ten views); three merged plans show every box unticked; the RunPod live-findings register has no disposition | `CHANGELOG.md`, `docs/sdlc/*`, `docs/operator/user-journey-catalog.md`, `docs/superpowers/plans/*`, `docs/evidence/2026-08-30-runpod-live-findings.md` | Closed: counts, screen names, plans, and the findings register match (Tasks 13, 14, 17) |
| 12 | 151 references to absolute local paths under `/home/<user>` in 30 tracked files | `git grep -cE '/home/[a-z]+/'` | Closed: no operator home paths remain, and the text-policy guard rejects them (Task 15) |
| 13 | The journey harness covers 27 journeys of the registry broker; the personal no-database path, the gateway, `/v1/messages`, Agent Routing dispatch and the channel, all ten console views, and most REST/MCP/CLI surfaces have no journey. The release-acceptance draft gap ledger lists 1,433 entries | `scripts/release/run-user-journeys.sh`, release-acceptance handback | Closed: 42 journeys pass and the matrix binds 1,270 surfaces with 0 unbound ([acceptance packet](../release/acceptance-packet-2026-09-24.md)) |
| 14 | Provider countersigns (Vast.ai, Together, Lambda Cloud) and RunPod lifecycle for the current candidate have no live evidence | `docs/support-matrix.md` | RunPod lifecycle closed ([live run](2026-09-23-runpod-candidate-live.md), Task 20). Vast.ai, Together, and Lambda Cloud countersigns stay open: live spend not authorized (Task 21) |
| 15 | `packages/pi-workbench` arrived in two diverged copies; the merged package keeps the pi-workbench copy plus one release-acceptance-only test file whose two cases fail; `tests/restricted.test.ts` times out under machine load | `integration/2026-09-22` | Closed: one reconciled copy, and the restricted-tool test no longer depends on wall-clock time (Tasks 2, 3) |
| 16 | Release-acceptance open items: RA-050 intermittent API failure, two undiagnosed Pi terminal-driving timeouts, Pi comparison items (14/18 core tasks, warm reuse, queue timing, M02), final candidate freeze and acceptance packet | `~/.local/state/pitwall-handoffs/OUTSTANDING-WORK.md` (summarised here) | Candidate frozen and packet written (Task 27); RA-050 not reproduced in 200 stressed runs (Task 23); channel live with six child harnesses, a Copilot parent, and Phase D ([channel evidence](2026-09-25-channel-live.md)); Pi Workbench verified live; timeouts resolved as a test-driver defect, comparison rerun 12/12, warm reuse and queue timing measured, M02 recorded |

## 4. Live evidence that exists outside the repository

The release-acceptance lane recorded live evidence under the operator's readiness directory:
installed broker journeys, a real broker → gateway → LAN Qwen round trip (`PITWALL_OK`,
`PITWALL_RESTART_OK`), DeepSeek and Claude Sonnet ask/answer and steer-acknowledgement journeys,
Pi session continuation, and installed first-run setup. None of it is in the repository; the
remediation plan brings sanitized summaries into `docs/evidence/`.

## 5. Scheduled-only CI jobs, run locally on 2026-09-25

The scheduled jobs cannot be dispatched by hand, and none had run since Tasks 9–12. Each was run
locally with the workflow's own commands on the release-preparation branch.

| Job | Result |
| --- | --- |
| External Markdown links (`check_markdown_links.py --external`) | Passed: 413 files |
| Fixed-vulnerability image scan (Trivy, HIGH and CRITICAL, fixed only) | Passed: 0 findings in each of the five service images |
| Mutation smoke (`make mutation-gate`) | Passed: 86.7% against the 85% floor |
| Dependency compatibility, `highest` and `lowest-direct` | Failed at first: `lowest-direct` resolves `redis` 5.0.0, which has no async `aclose()`. The floor is now 5.0.1; both resolutions then passed 6,462 tests |
| Combined coverage and risk floors | Failed at first: webhooks branch coverage 64.63% against 65%, because no test reached the delivery retry policy. New dispatcher tests raise it to 71.95%; total coverage 85% against 77% |
