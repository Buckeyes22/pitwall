# MCP Alignment, Channel Coverage, and Run Store: Status

Tracks the plan `docs/superpowers/plans/2026-10-06-mcp-alignment-channel-and-run-store.md`: what is
done, what is in flight, and what remains. The detailed execution record (every ruling, review, and
fix round) is the local ledger
`.superpowers/sdd/2026-10-06-mcp-alignment-channel-and-run-store/progress.md`.

**As of:** 2026-10-07, release candidate 0.3.0a1 after the final release-readiness review.
**Integration branch:** `feat/zcode-harness` (local only, not pushed), carrying Task 31, the final-review
fixes, the changelog for this release, and every review fix.
**Progress:** 78 of 78 tasks complete (the plan's 74 numbered tasks plus 6b, 6c, 7b, 7c, which were added
for defects found during execution). Landing on `main` and publishing remain, each with operator approval.

## Completed

Every completed task passed a task review (spec compliance and quality) and every fix round passed a
scoped re-review. "Merged" means the lane reached the integration branch.

### Wave 0

| Task | What it delivered | Commits | Merged |
|---|---|---|---|
| 1 | Specs, merged plan, and the pending SDLC fix on the integration branch | `836c4fba..49457360` | yes |

### Lane G1: broker on MCP SDK 2

Merged as one signed merge commit, `8e1b68ff` (70 lane commits). A rebase was abandoned because the
admin-fuzz move of `RecordingBackend` conflicted on many of them; one merge resolves each conflict
once.

| Task | What it delivered | Commits |
|---|---|---|
| 2 | Broker on MCP SDK 2, serving both protocol eras (F01) | `49457360..88efc2b0` |
| 3 | Pitwall error codes moved out of the JSON-RPC reserved range (F11) | `88efc2b0..aa0a2d56` |
| 4 | Protocol errors, actionable argument feedback, rate limits (F03, F08, F09) | `aa0a2d56..2cadf1e9` |
| 5 | Title and annotations on every broker tool (F05, F06) | `2cadf1e9..00ebf47a` |
| 6 | Every broker tool parameter described (F06) | `00ebf47a..a49365db` |
| 6b | MCP provider update honours `enabled` (defect found in Task 6) | `a49365db..cc43159e` |
| 6c | REST provider PATCH honours `enabled` | `cc43159e..95cbf37b` |
| 7 | Broker instructions, cache hints, honest capabilities (F07, F10, F17) | `95cbf37b..66326c2f` |
| 7b | RunPod control-plane mutations replay on a repeated idempotency key; unknown pod-create outcomes resolved by attempt marker; no $0 settlement; no pod terminated without proven ownership (5 fix rounds, 8 addenda, closed by controller verification) | `66326c2f..0075bc6b` |
| 7c | A retried lease or serve never launches a second pod; Lambda and Vast spend reaches the budget; teardown, renewal, and launch share one settlement rate (5 fix rounds, an adjudication, a closing fix) | `38ad5cd3..93dcae27` |
| sweep | G1 parked minor findings closed (update toggles in one transaction, audits the locked old value) | `0075bc6b..431a26b3` |

### Lane G2: run store

| Task | What it delivered | Commits | Merged |
|---|---|---|---|
| 16 | One terminal-state set and one pid helper module (R14b) | `49457360..bc83eafe` | Merge 1 |
| 18 | Supervisor recorded; abandoned standalone runs reconciled (R2) | `bc83eafe..9818d1e4` | Merge 1 |
| 19 | A managed wait never orphans a live standalone run (R4) | `8dfb0aeb..50aa9aac` | Merge 1 |
| 22 | Steers to a run without the channel refused, except stop; refusals leave no trace (R6) | `50aa9aac..209d85a0` | Merge 1 |
| 23 | Unacknowledged steers reported at exit (including orphans); inbox skips finished runs; reads never create a mailbox (R7) | `209d85a0..67c5eb92` | Merge 1 |
| 20 | OpenCode exit 0 with empty output is a failure; reason recorded in `run.json`, ledger, and event (R3) | `67c5eb92..fdd728f5` | Merge 1 |
| 25 | A flag is never taken as the prompt source; `--help` for every harness; sentinel always last (R9) | `fdd728f5..a6564da6` | Merge 1 |
| 27 | Delivery prompts removed on every terminal path unless retained; unknown retention keeps them (R11) | `a6564da6..1b38ca22` | Merge 1 |
| sweep | G2 parked minor findings closed | `6db93565` | Merge 1 |
| 26 | Managed launches keep child output in the run logs only (R10) | `08702d02..ba963f4c` | Merge 2 (`ff45b5cf`) |
| 28 | Migration leftovers: every legacy branch prefix, moved worktrees (including a gone repository), current artifact paths (R12) | `ba963f4c..0781b307` | Merge 2 (`ff45b5cf`) |

### Lane G3: channel protocol and CLI

| Task | What it delivered | Commits | Merged |
|---|---|---|---|
| 8 | Channel server serves both protocol eras; no shutdown crash; strict JSON-RPC ids (F02, F13) | `76141d66..ad1c207d` | Merge 1 |
| 9 | Channel tool annotations, argument checks, rate limit, sanitized errors, dual-era doctor probe (F04, F05, F09, F16, F18) | `87d8d35a..cba07eba` | Merge 1 |
| 11 | `serve broker --json` keeps stdout for the protocol (F14) | `cba07eba..9e0b6512` | Merge 1 |
| 10 | The channel relay drops non-MCP output and uses application error code -31010 (F12, F15) | `8e1b68ff..53ebb3f3` | Merge 2 (`5d692e5a`) |

### Lane G4: channel coverage

| Task | What it delivered | Commits | Merged |
|---|---|---|---|
| 12 | Grok channel registration (C1) | `49457360..58ed6664` | Merge 1 |
| 13 | Antigravity and Muse registration (C1) | `58ed6664..6f71e3c1` | Merge 1 |
| 14 | Hermes and goose registration (C1), plus the config-content leak class closed: YAML, TOML, and validation errors never echo file content anywhere in `src/` or `tools/` (`ConfigFileError`) | `7263f796..08323a53` | Merge 1 |
| 15 | Codex and Copilot skills teach the managed path (C2) | `08323a53..e72ff572` | Merge 1 |
| sweep | G4 parked minor findings closed; every TOML table header recognised | `735e8d65`, `eb6e74f0` | Merge 1 |

### Lane G5: independent fixes

| Task | What it delivered | Commits | Merged |
|---|---|---|---|
| 17 | Steering gate exempts Codex's channel tool names (R1) | `49457360..9e8641f3` | Merge 1 |
| 21 | An aborted run never reports exit 0 (R5) | `9e8641f3..411f0527` | Merge 1 |
| 24 | Claude prompts go to stdin (R8) | `411f0527..187e7ec2` | Merge 1 |
| 29 | Launch-guard lock files removed (R13) | `187e7ec2..c45c3278` | Merge 1 |
| flaky | Every flaky test root-caused; supervised runs interrupt-safe (no orphaned harness on Ctrl+C or thread-start failure); Ctrl+C always prompt and reported; G5 parked minors closed | `c45c3278..ab6183fa` | Merge 1 (`6676ef1e`) |

### Lane W2 grok

| Task | What it delivered | Commits | Merged |
|---|---|---|---|
| 30 | `grok-4.5` unregistered from the Grok CLI through the model-facts pipeline and left off the generated skill cards; the model itself stays current (R14a) | `08702d02..a4fa4ae7` | Merge 2 (`3278346a`) |

### Integration-branch fixes

| Change | What it delivered | Commit |
|---|---|---|
| CLI review hash | Release check refreshed after Task 11's test change | `c8a4fbd9` |
| Doctor probe | Doctor waits up to 30 s for a slow channel server, probes concurrently, and names why a probe failed | `316ba14a` |
| Merged-branch flakes | Ask-cap, dispatch-timeout, and baseline-runner tests fixed; workbench fixture check timer and Pi stop grace tolerate a loaded host | `71ecd9b6` |
| Admin-route fuzz | The schema fuzz authenticates and covers all 37 admin operations hermetically; eight server errors it found are fixed with regression tests | `316ba14a..261ed21a` |
| Documentation lanes | Agents docs, plugins, and the broader SDLC and operator docs brought current with the code | `63f74862`, `006146bb`, `7f51862b` |
| Admin fuzz isolation | Every admin fuzz test gets a fresh in-memory RunPod account; a fuzzed delete no longer makes a later create's read-back fail | `348a75ae` |
| Bindings hash | `tests/agents/test_registry.py` binding hash refreshed after Task 30 | `5a473977` |

### Merge 1 (head `8e1b68ff`)

- Static gates clean; `make test-fast` twice (9303 passed, 0 failed each); MCP release journey 83
  passed; `make test-int` 296 passed.

### Merge 2 (head `348a75ae`)

- Tasks 10, 26, 28, and 30 merged; static gates clean; `make test-fast` twice (9338 passed, 0 failed
  each, after the fuzz-isolation fix); MCP release journey 83 passed; `make test-int` 296 passed.

### Task 31

| Lane | What it delivered |
|---|---|
| 31A docs | Channel registration and steering docs reconciled with the code, SDK 2 wording sweep, error-class table, reconciler and security docs, steer kinds in sorted order everywhere, one changelog entry for the plan, this page |
| 31B release artifacts | `bind_surfaces` re-lines hand-reviewed bindings (149 drifted lines to 0) and stops on a missing bound test; per-family binding files refreshed and checked; secrets baseline regenerated and audited; 81 MCP surfaces in the inventory |
| CI parity | Every `ci.yml` job run with its exact commands, including the scheduled-only jobs. Fixed: a real home path in a test fixture (repository text policy), multidict 6.9.1 for CVE-2026-104874 (pip-audit), the mutation config listing a removed module |
| 31M mutation | Behaviour tests raise the core-trio mutation score from 83.0% to 96.9% (floor 85%) |
| 31S scheduled jobs | `worker.py` entry point covered (risk coverage), schemathesis 4.29 ASGI client (newest dependencies), str subtest paths (oldest dependencies), Debian security updates in every image (Trivy) |
| 31J journeys | J10 on the MCP SDK 2 field names, J24's fake follows the renewal query order, lease launch and renew answer 503 `budget_not_configured` for an unset or invalid budget instead of 500 |
| Compatibility | A nested `uv run` in a test re-locked the lowest-direct environment mid-run; it now uses the session's interpreter |

### Final whole-branch review

- Verdict "with fixes": 1 Important, 4 Minor, all fixed and confirmed by a scoped re-review. The
  harness of a killed supervisor is now stopped (on reconcile and by `runs stop`); a terminated pod
  carrying the create's marker is charged, never closed at $0; a same-key renewal retry returns its
  stored result even when the budget became unset; docstring and changelog wording corrected.
- The re-review's three minors (never signal the caller's own process group, a portable liveness
  check in the regression test, accurate `runs stop` wording) are closed.

### Comprehensive Sol review and fixes (Tasks 33-74)

| Step | Result |
|---|---|
| Review | Ten GPT-6 Sol passes over every source file in five areas (r1-r5, gap-fill r1b, r2b, r4b, r4c): 65 findings, 1 Critical, every one reproduced by the controller |
| Plan | Tasks 33-73, each grounded in the code (file, function, line) and the official documentation of every library or protocol it relies on; r4c-3 ruled the documented keyless-gateway design |
| Fixes | Ten Sonnet lanes (A-J) with exclusive file ownership, test-first, one signed commit per task; each lane reviewed (spec and quality) and every review finding fixed |
| Critical | Streaming `POST /v1/messages` now runs pre-spend inspection and budget admission and records a workload before any provider call (Task 50) |
| Re-review | Scoped Sol re-review of the merged range: 64 of 65 fixed (r4c-3 by design), cross-lane interactions clean; its two regressions (Messages fallback stream at the deadline, sub-budget spend after a failed commit) fixed in the final lane |
| Release | Changelog: last release is GitHub `main` (0.2.0a1); `[Unreleased]` covers everything local, including 29 fix entries. Release-acceptance pins re-keyed and re-reviewed; secrets baseline audited |
| Load failures | Transient failures seen while five lanes ran full suites at once were traced to host disk exhaustion (ENOSPC); not reproduced in seven runs at the same CPU load with free disk |

### Final CI parity (Task 74)

| Gate | Head | Result |
|---|---|---|
| Every non-database `ci.yml` job, five Docker builds | `1537aa83` | exit 0, except semgrep (host io_uring limit; the same command with `EIO_BACKEND=posix`: 221 rules, 511 files, 0 findings) and `python-policy` (nine missing `# reason:` comments, fixed in `1465664b`) |
| Trivy on all five images; dependency compatibility, highest and lowest-direct | `1537aa83` | exit 0 |
| ruff, format, `mypy --strict`, `python-policy`, DCO, internal links, secrets | `4de910a7` | exit 0 |
| test-cov, integration-cov, combined coverage with risk coverage, MCP journey (83 passed) | `4de910a7` | exit 0 |
| User journeys | `4de910a7` | 42 passed, 0 failed; matrix 1322 surfaces, 1536 bound cases, 0 unbound, 0 failing |

The first database pass (on `1537aa83`) found two integration failures and three journey failures; all
were fixed in `4de910a7`. The plan's Task 61 rule wrongly refused replay of a legacy workload that has no stored
digest; journeys J23, J24, and J33 used test fakes and a monkeypatch seam that predated the row lock and the
shared Messages client.

### Task 32: live proof and workstation cleanup (2026-10-07)

| Step | Result |
|---|---|
| Install | `releases/20261007-mcp-alignment` from the branch; `pitwall agents install` refreshed the shims and plugins |
| Registration | 13 harnesses `PASS` in `pitwall agents doctor` |
| Stack | Images rebuilt as `mcp-2026-07-28-20261007`; all services healthy; migrations 0041-0043 applied |
| Both MCP eras | Broker and channel answer the 2026-07-28 discovery with `resultType: complete`; the legacy handshake passes; `pitwall_health` all true |
| Managed ask | Grok verified live (dispatch db420b68). Antigravity, Muse, Hermes, and goose are registered only: no account on this workstation |
| RunPod create marker | A real pod's env carried `PITWALL_CREATE_ATTEMPT` equal to the key's digest; pod terminated, lease closed, actual cost $0.011 |
| Codex steer, Claude stdin | Steer acknowledged (`STEERED`); 135 KB prompt delivered over stdin |
| Cleanup | 63 stuck runs to 2 live; 556 duplicate launcher logs emptied (692 MB to 37 MB); 701 kept prompts removed; worktree records migrated; two orphaned worktrees removed (187 MB to 71 MB); lock files 260 to 158 |

### Release candidate 0.3.0a1

| Step | Result |
|---|---|
| Model updates | GPT-6.1 Sol replaces GPT-6 Sol in the registry; Claude Haiku 5.5 registered; Claude Opus 5 and Mythos 5.1 in the facts; OpenAI GPT-6 model guide in the codex facts and prompting reference; MiMo V2.6 dossiers |
| README | Rewritten: pitch, five parts, runnable examples, architecture, quick start |
| Release-readiness review | Five GPT-6.1 Sol reviewers: 4 BLOCKERs, 6 Important, 4 Minor, all fixed (branchless discard on another branch, dead-supervisor managed runs, Messages credential redaction, version stamp; sub-budget resolver connection, stream failure settlement, and the rest) |
| Version | 0.3.0a1 in pyproject, uv.lock, plugin and marketplace manifests; changelog `[0.3.0a1] - 2026-10-07`; `docs/releases/v0.3.0a1.md`; `scripts/release/validate_candidate.py --tag v0.3.0a1` passes |
| CI parity (`2888780a`, secrets baseline `7d7ce51d`) | Every `ci.yml` job, five Docker builds, Trivy on five images, dependency compatibility highest and lowest-direct, test-cov, integration-cov (308 passed), combined coverage with risk coverage, MCP journey, user journeys 42 passed 0 failed (matrix 1322 surfaces, 1536 bound, 0 unbound, 0 failing), semgrep (posix) 221 rules 0 findings |

## Remaining

- **Land:** land the integration branch on `main` and publish the public single-commit repository.
  Requires operator approval; nothing is pushed or published before then.
