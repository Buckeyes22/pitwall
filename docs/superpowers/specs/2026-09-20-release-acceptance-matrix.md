# Release acceptance matrix and test plan

## Purpose and release claim

This plan defines the work required to prove every exposed Pitwall route, journey, and function
that is advertised for a release candidate. It is an implementation plan, not a release approval
and not an authorization to spend money, contact providers, publish artifacts, or change release
policy. The output of the work is a machine-readable acceptance dataset, a generated human matrix,
test and operator runbooks, retained evidence, and a defect/exception ledger tied to one immutable
candidate.

All repository paths in this plan resolve under the canonical workspace `~/git/pitwall`;
implementation records should preserve that absolute path (and any component checkout path) in
reproduction metadata rather than relying on the caller's current directory.

The release claim is bounded by the repository's manifest, support matrix, source documentation,
package manifests, and release configuration. It must not invent an operating system, provider,
transport, or deployment claim. Evidence from this Linux development machine is evidence for that
machine and its declared platform tuple; it is not universal OS evidence. Operator-owned live
evidence is separate from hermetic CI and is required when the published claim says that a real
provider, endpoint, host, or external harness works. A credential or quota gap is not a waiver.

The final terminal condition is:

- 100% of discovered surfaces are classified in both directions against declarations.
- 100% of required scenarios have valid matching evidence or an explicit, owner-approved
  exception with scope, risk, expiry, and excluded counts.
- There are zero unmapped surfaces, unknown scenarios, open release-blocking defects, or stale
  required evidence.
- Cleanup and restoration for resources actually created are independently verified; when an
  independent provider or harness is inaccessible, verification remains blocked.
- Published support claims accurately distinguish hermetic support, operator-live countersign,
  deferred/unavailable behavior, and unsupported combinations.
- A deliverable packet contains the candidate identity, matrix, runbook, logs, evidence checksums,
  defect ledger, exceptions, and known limits.

This plan deliberately preserves the current separate workbench and canonical histories. Candidate
identity must identify the exact repository commit, component commits where applicable, dirty
workbench state, dependency locks, and built artifacts. It must never silently merge or substitute
the archived control checkouts.

## Existing contracts and anchors

Implementers begin by reading and retaining the exact versions of these sources in the evidence
packet:

- `CONTRIBUTING.md` and the repository `AGENTS.md` rules.
- `docs/sdlc/17-testing-strategy.md`, especially the exact `-m release` selector, the 74% hermetic
  floor, 77% combined floor, risk coverage policy, 85% mutation floor, DNS/live gates, and
  production-bug regression rule.
- `docs/operator/release-testing-checklist.md` and `docs/release/external-release-gates.md`.
- `docs/support-matrix.md`, `docs/sdlc/01-architecture.md`, `docs/sdlc/02-api-rest.md`, `docs/sdlc/03-mcp-server.md`,
  `docs/sdlc/04-routing.md`, `docs/sdlc/05-cost-budget.md`, `docs/sdlc/06-leases.md`, `docs/sdlc/07-data-model-db.md`, `docs/sdlc/08-runpod-integration.md`,
  `docs/sdlc/09-webhooks.md`, `docs/sdlc/10-reconciler-lifecycle.md`, `docs/sdlc/11-rate-limiting.md`, `docs/sdlc/12-audit-readiness.md`,
  `docs/sdlc/13-observability.md`, `docs/sdlc/14-security.md`, `docs/sdlc/15-operations.md`, `docs/sdlc/16-core-config.md`, `docs/sdlc/18-cli.md`,
  `docs/sdlc/19-deployment.md`, `docs/sdlc/20-provider-plugins.md`, `docs/sdlc/21-autopilot.md`, `docs/sdlc/23-agent-routing.md`, and
  `docs/sdlc/22-recommendations.md`, `docs/sdlc/24-gateway.md`.
- `docs/api/openapi-baseline.json`, the live generated OpenAPI schema, and all route registration
  modules under `src/pitwall`.
- `pyproject.toml`, `uv.lock`, `packages/agent-routing/pyproject.toml`, its `packages/agent-routing/uv.lock`, and
  `packages/gateway/package.json` plus `packages/gateway/package-lock.json` and `packages/gateway/UPSTREAM.lock`.
- `.github/workflows/ci.yml`, `.github/workflows/release-readiness.yml`, gateway and Agent Routing workflows, release
  scripts under `scripts/release`, Dockerfiles and Compose files.
- `qa/README.md`, `qa/START-HERE.md`, the QA lesson/mission packet, and the evidence standard.

The source anchors already identify important independent surfaces: REST and local stdio MCP;
operational CLI and migrations; Textual views; Postgres/Redis/reconciler/webhook/cost-exporter;
provider registry and adapters; route-plan persistence; personal setup and gateway supervision;
Agent Routing's CLI, MCP, orchestrator channel, plugin manifests, harnesses, routes, workflow and
run-store modules; and Gateway's five loopback HTTP endpoints. The discovery phase must verify
these against code at the candidate rather than treating this paragraph as a fixed inventory.

Historical evidence is context only. Prior reports mention a broad run of 5,341 pass and 76 skip
before a final 124 focused plus 9 PostgreSQL cases, 83 coverage, and 23 gateway cases; the current
ledger must record the exact run rather than trusting those summaries. The `QA37 missions / 6 covered / 23 partial /
8 none` are feeder evidence, not the final candidate denominator. Previous J01–J26 passes and the
later unit repair of historical J27 do not substitute for a final full wrapper run: the candidate
must run wrapper J01–J27 and obtain 27/27 with exit 0. Historical evidence is retained and marked
stale or superseded when code, lock, artifact, environment, or test definition changes.

## Identity, evidence, and status rules

### Immutable candidate identity

Before any candidate test, create a checksum and provenance identity record containing:

1. Broker repository commit SHA, branch/ref, remote, and merge base; Agent Routing and Gateway
   source commit SHAs if their histories are separate; exact tag/ref intended for release.
2. A complete dirty manifest: `git status --porcelain=v2`, path, object hash, mode, and content
   SHA-256 for every tracked/untracked relevant file. A dirty workbench is either explicitly part of
   the candidate or the candidate is rejected; it may not be silently cleaned, copied, or merged.
3. Python, uv, Node/npm, Docker/Compose, OS/kernel/architecture, and test-harness versions.
4. Exact `uv.lock`, Agent Routing lock, Gateway lock, image base digest,
  upstream `packages/gateway/UPSTREAM.lock`, and generated schema/catalog/config
  hashes from `` and its component paths.
5. Wheel, sdist, container image, Gateway package, and Agent Routing artifact SHA-256 digests;
   SBOM, provenance/attestation references, and build reproducibility comparison.
6. Test source, fixture, generator, configuration, model/provider adapter, and harness versions;
   provider endpoint identity without secrets; database migration checksum and schema version.
7. Run IDs, start/end times, command lines, environment allow-list (redacted), exit codes, stdout/
   stderr/log checksums, screenshots/video where used, and cleanup receipts.

No evidence is valid without a candidate ID and provenance. A draft row may be incomplete and
`not_run` while the matrix is being built; schema validation permits that state, while release
validation rejects incomplete required rows or missing evidence. A rerun after any identity input changes
creates a new run and invalidates dependent required evidence. Preserve failed originals; a fix is
green only after red-green proof and appropriate PostgreSQL, chaos, security, documentation, or
deployed reproof as applicable.

### Separate identifiers

Every record uses distinct IDs:

- `surface_id` identifies an exposed interface or callable function (for example, `rest:GET:/v1/...`,
  `mcp:tool:...`, `cli:pitwall:...`, `tui:view:Routes`, `gateway:POST:/v1/chat/completions`,
  `provider:runpod`, `routing:adapter:opencode`, `shim:claude-shim`, or `plugin:pitwall-codex`).
- `case_id` identifies one behavior assertion, including success, validation, auth, failure,
  idempotency, cost, timeout, stream, or unsupported-combination behavior.
- `journey_id` identifies a multi-step user/operator flow and its state transitions.
- `scenario_id` identifies applicability and an execution tuple: platform, install form, provider,
  backend, auth state, model/harness, data state, and external dependency mode.
- `run_id` identifies one execution against one candidate.

Do not count a surface as covered merely because a shared helper or one-shot task test passed. A
case can satisfy multiple surfaces only when the evidence records an actual invocation through each
surface and proves the surface-specific contract. A journey can use many cases, but journey success
requires the required sequence and state continuity.

Statuses are exactly `pass`, `fail`, `blocked`, `not_run`, `stale`, or `approved_exception`.
The illustrative schema record is explicitly a draft `not_run` record, not a passing result. `skip`
is forbidden as a result status. A missing test or not-attempted required case is `not_run`; an actual
assertion or execution failure is `fail`; absent provider credentials, quota, or external access is
`blocked`. Every such required status blocks release unless an explicit accepted exception is entered.
`blocked` records the exact external or environment dependency and the unaffected work that still ran.
`stale` records the identity or contract change that invalidated evidence. `approved_exception` requires
owner, scope, risk, expiry, compensating evidence, and excluded counts, and is never automatic.
Exceptions remain in the total denominator and are excluded from the passing numerator; reports show
accepted exceptions separately. `skip`, `xfail`, and deselection never pass.

### Minimum machine-readable record

Use a versioned JSON or JSONL canonical dataset under the proposed path
`release_acceptance/acceptance.v1.jsonl` (the implementation must choose and document the final
path). Human Markdown, HTML, CSV, and summary counts are generated from this dataset and are never
edited as an alternate source of truth. The minimum shape is:

```json
{
  "schema_version": "acceptance.v1",
  "candidate": {
    "candidate_id": "...",
    "broker_commit": "<git SHA>",
    "component_commits": {"agent_routing": "...", "gateway": "..."},
    "dirty_manifest_sha256": "...",
    "dependency_locks": {"root": "sha256:...", "agent_routing": "sha256:...", "gateway": "sha256:..."},
    "artifact_digests": {"wheel": "sha256:...", "images": {"api": "sha256:..."}},
    "platform": {"os": "...", "arch": "...", "python": "...", "node": "..."},
    "catalog_and_schema_digests": {"openapi": "...", "gateway_catalog": "..."}
  },
  "surface_id": "rest:GET:/health",
  "surface_kind": "rest|mcp|cli|tui|gateway|plugin|shim|config|install|ops|provider|service",
  "declared_source": ["docs/support-matrix.md:...", "src/pitwall/...:..."],
  "case_id": "REST-HEALTH-01",
  "journey_id": null,
  "scenario_id": "linux-wheel-loopback-noauth",
  "applicability": "required|optional|unsupported|deferred",
  "required_proof_lanes": ["hermetic", "integration", "release", "manual", "live"],
  "test_node_ids": [],
  "manual_steps": [],
  "expected_vs_actual": {"expected": "...", "actual": "..."},
  "cases": {"auth": "public", "state": "fresh", "cost": "none", "cleanup": "none"},
  "oracle": {"exit": null, "status": 200, "schema": "...", "invariants": ["..."]},
  "status": "not_run",
  "reviewer": null,
  "timestamps": {"created": "...", "started": null, "finished": null},
  "evidence_lane_results": {"hermetic": "not_run", "integration": "not_run", "release": "not_run", "manual": "not_run", "live": "not_run"},
  "run": {"run_id": null, "command": null, "exit_code": null, "artifacts": [], "evidence_sha256": null},
  "provenance": {"harness": "...", "provider": "...", "model": "...", "dependencies": "..."},
  "reproduction": {"command": "...", "preconditions": ["..."], "checksums": ["..."]},
  "defects": [],
  "exception": null,
  "notes": "..."
}
```

This illustrative draft row intentionally has no test node yet; the required health functional case
must receive a matching node before release and cannot borrow the unrelated OpenAPI snapshot.

Required lanes are scenario-specific and must be declared before execution. Each case records
authentication and authorization state, fixture/data state, provider/model tuple, expected cost and
budget ceiling, cleanup/rollback oracle, stream/cancellation/backpressure mode where relevant, and
the exact assertion. A passing unit test cannot satisfy a required installed, integration, manual,
live, packaging, or journey lane by implication.

## Phase 0 — candidate and declaration freeze

- [ ] Select the exact release candidate commit(s) from canonical public history. Record the
  workbench dirty manifest before changing anything. Preserve and identify the current separate
  histories; do not resume a historical merger plan or copy private history.
- [ ] Dirty workbench runs are exploratory only. The final publication candidate must satisfy the
  clean canonical policy in `docs/release/external-release-gates.md`; no
  exploratory dirty evidence can certify publication.
- [ ] Freeze versions, locks, generated OpenAPI/catalog/config, Docker base digests, package
  manifests, test code, QA packet, and support matrix. Derive `candidate_id` from these values.
- [ ] Extract every advertised supported, limited, pending, deferred, and unavailable statement
  from manifests and documentation into declaration records. Include platform boundaries (managed
  Python 3.14.7, Linux-only Compose/images, macOS Agent Routing hosted lane, and any other explicit
  support) only when declared by source.
- [ ] Mark existing historical evidence as feeder, candidate-valid, stale, or superseded. Require
  final J01–J27 wrapper 27/27 exit 0.
- [ ] Capture an initial acceptance-schema/report proposal and candidate snapshot. The final schema
  and generated-report version freeze occurs in Phase 6; any schema or declaration change then
  restarts identity-dependent release lanes.

## Phase execution, ownership, dependencies, and planned outputs

The following table is the implementation sequence. Luna is the implementing owner for matrix and
test changes; root is the independent reviewer and final-candidate owner.

| Phase | Owner | Dependencies | Named planned outputs | Exit criteria |
|---|---|---|---|---|
| 0. Candidate/declaration freeze | Luna implementer; root reviewer | Canonical commit, clean-policy source | `release_acceptance/acceptance.v1.jsonl` (proposed canonical data), `release_acceptance/candidate.json` (proposed identity), `release_acceptance/DECLARATIONS.md` (proposed declaration extract) | Exact git/component/artifact/lock identity captured; final candidate clean; dirty runs exploratory; declarations source-cited |
| 1. Exhaustive discovery | Luna implementer; root reviewer | Phase 0 identity and source tree | Existing drift/test locations `tests/api/test_route_inventory.py`, `tests/mcp/test_registry.py`; proposed `release_acceptance/generated-inventory.json` | Discovered and declared inventories match both directions; every row has surface/case/journey/scenario IDs and applicability |
| 2. Schema/drift/report guard | Luna implementer; root reviewer | Phase 1 inventory | Proposed validator/report tooling under `tools/release/`; generated `release_acceptance/acceptance-matrix.md` | Draft `not_run` rows validate; release validation rejects incomplete required evidence, unknown IDs, skip/xfail/deselect, stale required rows, and count mismatches |
| 3. Reuse and domain implementation | Luna implementer; root reviewer | Phase 2 node index and existing test suites | Existing trees `tests/`, `packages/agent-routing/tests/`, `packages/gateway/tests/`; proposed `release_acceptance/gap-ledger.json` | Every required row maps to a concrete node/manual procedure/live record; no shared-helper or aggregate-only substitution |
| 4. Execution and reproof | Luna implementer; root reviewer | Phases 0–3, isolated DB/Redis/services, approved operator live access where applicable | Proposed run bundle `release_acceptance/runs/<run_id>/`; defect ledger `release_acceptance/defects.jsonl` | Required lanes execute with exit codes, expected-vs-actual, cleanup receipts, and evidence checksums; failures fixed red-green and dependent evidence rerun |
| 5. Exceptions/evidence discipline | Luna implementer; root reviewer | Phase 4 results and defect ledger | Proposed `release_acceptance/exceptions.jsonl` and `release_acceptance/evidence-index.jsonl` | Exceptions owner-accepted, scoped, expiring, denominator-counted, numerator-excluded; no automatic credential/quota waiver |
| 6. Final freeze and independent review | Root reviewer; Luna supports reruns | All fixes, accepted exceptions, exact final candidate | Final `release_acceptance/final-packet/` and `release_acceptance/final-report.md` | Recompute identity after every fix, rerun mandatory gates on the exact final candidate, independently review counts/claims/digests, and reach zero blocking rows |
| 7. Maintenance/drift prevention | Luna implementer; root reviewer | Accepted final packet and merged policy | Proposed CI/report integration in `.github/workflows/` and notes in `release_acceptance/MAINTENANCE.md` | Future surface/manifest/schema/plugin changes fail without matrix/test/docs updates; stale evidence and exception expiry are detected |

Implementation must use isolated test resources: documented PostgreSQL `127.0.0.1:5444` and Redis
`127.0.0.1:6380` (or recorded per-run alternatives), unique Compose project names, and separate
worktrees only when needed. No test may reuse production databases, operator resources, or another
candidate's evidence. Normal authorized local execution needs no redundant approval; paid provider
use, external publication, destructive operations, and external communication remain outside this
plan and follow existing authorization.

Luna assignments are bounded to one file owner at a time, with root reviewing the resulting change
and evidence. Separate worktrees, Compose project names, databases, ports, temporary directories,
and run IDs prevent collisions between assignments. Before asking for sign-in, use existing service
CLIs, authenticated config, supported APIs, and documented SSH routes. For LAN or machine probing,
read `~/agents/context/homelab-fleet.md` first. Never print secrets; keep private raw
evidence outside the repository and publish only redacted evidence and checksums.

**Gate 0:** candidate identity is reproducible; declarations have source citations; no unexplained
dirty input, lock drift, or historical evidence is being counted as final.

## Phase 1 — exhaustive surface and scenario discovery

Build a repeatable static/runtime inventory generator with no new runtime framework or service
dependency. It must emit source provenance and compare discovered and declared inventories in both
directions:

- **REST/API:** import the application and enumerate every mounted FastAPI route, method, sub-app,
  middleware-visible path, health/admin/metrics/webhook route, OpenAPI operation, auth variant, and
  error/content/stream response. Start from `src/pitwall/api/app.py` and every
  `include_router`, then compare the generated schema with
  `docs/api/openapi-baseline.json`,
  `tests/api/test_route_inventory.py`, framework documentation, and declarations. Include mounted
  sub-apps, hidden routes, `OPTIONS`/`HEAD`, scoped-token/auth families, Messages/OpenAI wildcard
  proxy, onboarding plan/apply/status/resume/rollback, quota refresh, volume objects/logs, webhook
  rotation, and resource/provider CRUD. Retention/archive is inventoried separately as CLI/service/
  ops behavior. Documentation-only framework routes are
  excluded only when the root app explicitly rejects them as user-exposed; record that exclusion.
- **MCP:** enumerate local stdio server tools, names, descriptions, schemas, resources/prompts, and
  registration paths in broker and Agent Routing, starting from broker registry modules under
  `src/pitwall/mcp` and Agent Routing's
  `packages/agent-routing/runtime/model_routing/registry.py`, feature specs, and
  `tests/mcp/test_registry.py`. The observed broker count is 78, but the release denominator is
  generated from the candidate rather than hardcoded. Include admin, file, route, provider,
  orchestration, and error tools. `tools/list` is discovery only: every tool needs an invocation
  with appropriate arguments, roles, success state, failure envelope, and transport-separation case.
  Assert network MCP is explicitly unavailable if still so documented.
- **CLI and installs:** derive every console script and `python -m` entry point from both projects,
  every subcommand, option, confirmation, JSON/human output mode, exit code, shell completion,
  onboarding/bootstrap/doctor/install/uninstall/upgrade path, and migration/retention/backup command.
  Include the six root console entry points and `src/pitwall/cli.py` plus
  `src/pitwall/cli_*.py` dispatch groups: setup, serve,
  status, stop, models, db, leases, mcp, retention, init, create-capability, seed, config,
  register-template, register-endpoint, set-provider-health, terminate-pod, warm-volume, cost,
  burn-rate, guardrails, routing, runpod, runpod-onboard, provider-ops, volume-files, gateway,
  quotas, and dashboard. Treat no-argument dashboard and `serve --plan-only` as separate cases.
  Invoke actual installed commands later; `--help`, imports, and parser success are not functional
  coverage.
- **TUI:** enumerate all ten documented views (Overview, Providers, Leases, Models, Serve, Pods,
  Routes, Cost, Resources, Operations), actions, key bindings, empty/error/loading states, and
  resize behavior. Extract confirmation tiers and actions from
  `src/pitwall/tui/confirmation.py`; include Textual adapters for shared
  RunPod file operations.
- **Gateway:** enumerate the exact five documented endpoints, bind/token/body/rate-limit/error,
  inbound shape (`openai`, `claude`, `gemini`, `responses`), compression (`off`, `rtk`, `caveman`,
  `stacked`), request-id, streaming, and loopback refusal contracts.
- **Agent Routing:** enumerate runtime modules and public contracts for discovery, registry, route
  setup/sync/probe, orchestration/channel, workflow, scheduler, mailbox, run store, lifecycle hooks,
  provider setup, MCP server/tools, installer/bootstrap, endpoint discovery, all plugin manifests,
  marketplace entries, and every shipped shim/harness. Derive the candidate provider registry
  (currently 13 named providers: Codex, Claude, Grok, Kimi, OpenCode, Goose, Qwen, Hermes, Pi, Muse,
  Cline, Dsh, and Agy) and 14 shims including `route-shim`; do not hardcode these counts. Include
  Claude, Codex, and Copilot host plugins as distinct native ownership surfaces. Include Pi Workbench
  only if shipped and advertised by the candidate; Oh My Pi is excluded unless advertised.
- **Harness/shim/plugin:** inventory Claude Code, Codex, OpenCode, Grok, Antigravity, and every
  shipped adapter and shim present in source/manifests. Keep native plugin, direct shell, and route
  shim contracts as distinct surfaces with distinct setup, invocation, output, continuation, and
  cleanup cases.
- **Configuration/deployment/ops:** enumerate environment variables, config files, defaults,
  validation, secret references, Dockerfiles, Compose services, migration ordering, backup/restore,
  reconciler/autopilot timers, webhook receiver, cost exporter, observability, kill switch, and
  uninstall preservation rules.
- **Other services:** discover the webhook receiver, cost exporter, gateway, Agent Routing channel
  broker/MCP services, and optional storage, email, or tracing extras. Each advertised extra gets
  enabled, disabled, missing-configuration, default, and no-accidental-external-send cases.
- **Providers and modes:** enumerate every declared adapter/capability tuple: RunPod full broker,
  personal no-DB mode, LAN/self-hosted endpoints, actual RunPod registry pod, serverless LB, queue,
  vLLM, and every other promised provider/backend. Include provider capability negatives (unsupported
  compute, async, cost, or availability) as explicit reject cases.

The inventory must fail on discovered-but-undeclared and declared-but-undiscovered entries. Generate
scenario rows by crossing each supported surface with applicable auth, state, install form, platform,
provider/backend, model, stream, and cost mode. Test every advertised supported tuple. Pairwise
reduction is permitted only for explicitly justified noncritical axes; security, billing, lifecycle,
destructive, continuation, and compatibility axes remain exhaustive. Unsupported combinations get
their own deterministic rejection oracle and cannot be silently omitted.

**Gate 1:** bidirectional inventory is closed; all rows have applicability, proof lanes, case IDs,
or a documented unsupported/deferred disposition. Zero unknown or unmapped rows.

## Phase 2 — schema, drift guard, and report generation

- [ ] Implement schema validation for identifiers, status vocabulary, required provenance, lane
  applicability, oracles, and exception fields. Reject `skip`, missing test IDs, missing evidence,
  missing cleanup, and duplicate IDs.
- [ ] Add a static/runtime drift guard that compares generated REST/MCP/CLI/TUI/Gateway/Agent Routing
  inventories to declarations in both directions. It must fail CI on additions without a matrix row,
  rows for removed surfaces, changed method/schema/auth, changed plugin/shim manifest, or changed
  gateway upstream/catalog digest without re-review.
- [ ] Add a test-node index: every required row names concrete pytest/unittest/vitest/installed/manual
  node IDs or a planned operator evidence ID. A suite result without a matching row does not count.
- [ ] Generate the human matrix and summary from canonical data. Include per-surface, per-case,
  per-journey, per-scenario, per-lane counts; blocked/not-run/stale/exception counts; uncovered
  declarations; and links to evidence checksums and reproduction commands.
- [ ] Add a redaction check: logs and generated reports contain no credential values, tokens,
  provider secrets, or private paths beyond the declared candidate/workspace context.

**Gate 2:** a clean empty or intentionally incomplete dataset fails with actionable diagnostics;
rendered results are byte-identifiable to the canonical input and no separate hand-maintained count
can disagree.

## Phase 3 — reuse, gap analysis, and test implementation

Map existing tests and QA missions to matrix rows without inflating coverage. Reuse is valid only
when the node exercises the named surface and asserts the row's oracle. Identify gaps by domain and
implement the smallest complete tests in the existing project test structure; do not add a new
framework, service, or runtime dependency.

### Core behavior and persistence

- [ ] Unit/contract tests cover routing plans, cost/budget admission, leases, rate limiting,
  webhooks, retention, kill switch, provider registry/capability rejection, gateway catalog/quota,
  guardrails, auth/redaction, file bounds/idempotency, and worker boundaries.
- [ ] Property tests cover canonical plan bytes, priority/weight/tie-breaking, unsupported capability
  rejection, budget/guardrail ordering, bounded payloads/results/events, and state invariants.
- [ ] Integration tests use real PostgreSQL and Redis for migrations, repositories, transactions,
  locks/concurrency, Redis queues, backup/restore, retention, route-plan migration 0031 and JSONB/
  NUMERIC fidelity, and cleanup. Mock-only evidence cannot satisfy database correctness.
- [ ] Chaos tests cover provider timeout, retry, termination, partial outage, Redis/Postgres loss,
  ambiguous async writes, webhook failure, reconciler restart, cancellation, and recovery without
  duplicate spend or state corruption.
- [ ] Security tests cover auth scope, body bounds, SSRF, webhook HMAC, secret references,
  redaction, path traversal, confirmation/destructive controls, MCP/CLI parity, and all-operation
  Schemathesis fuzzing. Preserve the exact strict RunPod audit.
- [ ] Run mutation coverage on the configured high-risk pure-logic trio and require at least 85%
  kill. Combined coverage remains at least 77%; hermetic floor remains at least 74%; risk policy
  source matches cannot disappear.

An operation returning 401 or 422 under fuzzing is evidence of a rejection path, not handler-success
coverage. Every generated API operation also needs an authorized valid fixture with asserted payload,
durable state, side effects, provider-request count, and cleanup. A provider catalogue/model listing
is not inference proof; inference text is not tool-use proof; process launch is not healthy-service
proof; a stop request is not independent provider-absence proof; a backup file is not restore proof;
a present dotenv file is not authentication proof; and aggregate coverage is not surface coverage.
Required deselection, xfail, skip, or empty collection is a failure unless represented as a reviewed
`approved_exception`.

### REST, MCP, CLI, TUI, and operations

- [ ] Exercise every generated REST operation with valid, invalid, missing-auth, wrong-scope,
  boundary, duplicate/idempotent, provider-error, and unexpected-error cases. Compare OpenAPI
  responses and redact sensitive output.
- [ ] Invoke every MCP tool through a real local stdio client, including schema validation, auth,
  success, error, bounded output, resources/prompts where declared, and cancellation. Prove tool
  calls are streamed where advertised and preserve request/session identity.
- [ ] Run installed broker and Agent Routing CLIs for every advertised command and mode, both human
  and machine output, confirmation and refusal, malformed input, nonzero exit, and clean environment.
- [ ] Drive all ten TUI views manually with the acceptance harness: fresh/empty/loaded/error states,
  keyboard navigation, actions, terminal resizing, slow/streamed updates, and database loss. Capture
  screenshots or screen logs plus an independent oracle for each view.
- [ ] Exercise backup, restore, migration, retention, uninstall, upgrade, service restart, and
  kill-switch operations. Verify unrelated user state survives uninstall and failed operations leave
  an auditable, recoverable state.

### Gateway and packaged components

- [ ] Reconcile canonical Gateway source before filing defects: the workbench may already contain
  auth/stream fixes. Run typecheck, lint, unit, build, lock/audit, deterministic double-build,
  upstream strip-list and `wreq` assertions, then invoke both the checkout supervisor path
  (`src/pitwall/personal/gateway.py`) and PATH-installed `pitwall-gateway` launcher. Treat tarball,
  `packages/gateway/dist/shim.js`, private package, SBOM, checksums, Node 22 workflow, and no-npm-publication rule as
  separate artifact cases.
- [ ] Test all five routes, token/public matrix, loopback-only binding, empty token, body cap before
  JSON parse, 429 rolling limit, request IDs, structured errors without stack traces, all four inbound
  shapes and four compression policies, streaming, cancellation, backpressure, and malformed/5xx
  responses.
- [ ] Build and install broker wheel, sdist, source checkout, each declared image, Agent Routing
  wheel/sdist, plugins, and Gateway package. Test fresh source/wheel/sdist/image bootstrap; upgrade
  from the previous supported artifact; backup/restore; and uninstall while preserving unrelated
  state. Verify files, entry points, permissions, migrations, defaults, and artifact digests.
- [ ] Run Agent Routing's independent standard-library runtime tests, plugin/marketplace manifests,
  install/bootstrap/doctor, source and installed artifact contracts, and macOS hosted portable lane
  where the declared CI environment supplies it. Do not infer broker Compose portability from this.

Gateway proof is limited to the exposed subset. Test the five routes and the candidate's actual
translation semantics for `openai`, `claude`, `gemini`, and `responses`; explicitly reject features
the candidate does not promise (Responses is not assumed full parity and Gemini is not assumed to
support every vendor field). Test `off`, `rtk`, `caveman`, and `stacked` according to the current
lite normalization/deduplication contract; do not claim full upstream compression engines unless
wired. Derive and boundary-test candidate defaults for loopback binding, 1 MiB body, 120 requests/
minute, and 30-second timeout. The catalog's 424 rows and 73 providers are historical metadata,
not proof of live connections or inference.

Reconcile Gateway's upstream executor authentication key with the separate Gateway bearer token;
they are distinct secrets and need separate configuration, redaction, rotation, and failure cases.
Reconcile `/health` auth behavior between canonical source and any README claim before certification.
`/v1/models` proves registered executor model IDs only; a catalog listing does not prove inference.
A startup smoke is insufficient: perform an authenticated round trip and applicable streamed or
translated behavior. If the candidate source differs from the workbench, record the candidate source
and test it; do not assume older buffering or newer streaming fixes.

### Sustained Agent Routing channel proof

For every advertised real host/plugin route, prove the bidirectional sequence in
`packages/agent-routing/docs/orchestrator-channel.md`:

1. Child sends `ASK`; orchestrator receives it and returns `ANSWER`; the child consumes that answer
   in the same session with correlation and parent identity.
2. Orchestrator sends `STEER`; child applies it and sends `ack_steer`, including duplicate,
   conflicting, unauthorized, expired, and out-of-order cases.
3. Child emits final `REPORT`; orchestrator interprets it and persists the structured result.
4. Exercise TTL/default/cap limits, crash/restart, pause/resume, replay, permissions, redaction,
   and recovery. Cover tier-1 stdio MCP (`ask_orchestrator`, `read_steering`, `ack_steer`) and
   tier-4 file fallback exit 75 semantics.

Use existing channel, mailbox, MCP, workflow, and resume tests as feeders, but fake integration does
not prove a real host plugin. Run each advertised native plugin, direct-harness shim, and route shim
separately, and assert `SHIM-DONE` plus optional `SHIM-RESULT` parsing. One-shot stdout, inference
text, or process launch cannot satisfy this journey.

### Full orchestrator acceptance journey

This is a required sustained journey whenever the relevant continuation mode is advertised. A
one-shot task or captured stdout is not proof of a conversation. If a harness cannot continue, its
unsupported continuation is an explicit reject result and the support matrix must say so.

For each supported harness/provider/platform tuple:

1. Provision or select the model and record model/provider identity, limits, cost ceiling, and
   cleanup owner.
2. Obtain the broker URL and auth/configuration through the declared setup route.
3. Register and verify the route in the Agent Routing registry; confirm the actual selected route and
   capability, not only a config file.
4. Generate the harness configuration through the native plugin, direct shell, or route shim under
   test; keep those contracts separate.
5. Start the orchestrator and prove discovery and selection of the actual shim/harness by recording
   structured events and process identity.
6. Submit a task that requires real tool use and code/test work; capture tool-call frames, streamed
   deltas, exit statuses, generated files, and structured result parsing.
7. Have the orchestrator interpret the result and send a follow-up to the same child/session when
   the tuple claims continuation. Assert the child sees prior context and can act on the previous
   artifact. In this same full journey, assert the observed effect of `ASK` in the child's next
   action, the observed effect of `ANSWER` in the child's consumed context, the observed effect of
   `STEER` in changed child behavior, and the observed `ack_steer` before completion. Record the
   unsupported oracle when continuation is not promised.
8. Exercise completion, explicit cancellation, timeout, backpressure, stream truncation, malformed
   result, child crash, broker outage, route loss, and recovery. Confirm no duplicate task, leaked
   process, orphan lease, or unbounded output.
9. Clean up child/session, route registration, broker resources, files, temporary credentials,
   provider resources, and logs according to the declared cleanup oracle; independently verify.

The journey must be executed through actual Claude, Codex, OpenCode, Grok, Antigravity, and every
shipped adapter that the support matrix claims. Native plugin, direct shell, and route shim each get
their own journey IDs. A fake adapter may prove the deterministic route contract but cannot satisfy
the actual installed/live harness row.

### Provider and deployment proof lanes

- [ ] Hermetic lane: all provider adapters and unsupported capability combinations use fakes/static
  adapters with no credentials, billing, or external egress. Verify canonical route identity across
  REST, MCP, CLI, and TUI.
- [ ] Full broker lane: real local Postgres/Redis topology and reconciler/autopilot behavior.
- [ ] Personal no-DB lane: first-run setup, endpoint key, profile, plugin attachment, gateway
  supervision, route lifecycle, process restart, and no accidental database requirement.
- [ ] LAN/self-hosted lane: only the declared authenticated/probed endpoint boundary; verify health,
  capacity, budget, timeout, cleanup, and host-state limits.
- [ ] Actual RunPod registry-pod lane: operator-owned credential and resource, explicit allow-list,
  bounded time/token/byte/spend ceiling, workload identity mapping, logs, cancellation, and teardown.
- [ ] Serverless LB, serverless queue, vLLM, and every other declared provider/backend tuple: test
  supported operations, stream/error/cancel semantics, cost and cleanup. Unsupported provider
  capabilities must be rejection cases with no provider write.
- [ ] Separate operator-live evidence from public CI. Public CI remains credential-free. Live
  evidence is required for advertised live behavior or must have an explicit owner exception with
  expiry and changed support wording. No policy is changed silently.

Cleanup is independently verified for every resource actually created by a run. When a provider is
inaccessible, absence of cleanup evidence remains `blocked`; the plan cannot claim that an
unavailable provider was cleaned up. Hermetic cleanup of local fixtures and processes still runs and
is recorded independently.

### Upgrade, recovery, and operational limits

- [ ] Read and reconcile `docs/operator/upgrade-recovery.md` with the release gates before writing
  upgrade cases. Prove `pg_dump`/restore on the same supported PostgreSQL major, passwords containing
  reserved characters, per-table row counts and checksums, encryption-key handling, Redis-state
  preservation, forward-only migrations, safe refusal of unsafe down-migrations, and recovery after
  interrupted upgrade. A backup-file existence check is insufficient.
- [ ] Verify each separate release workflow and artifact contract: broker tags/images/wheel/sdist,
  Agent Routing namespaced tags/wheel/sdist/plugin artifacts, and Gateway tarball/launcher/SBOM/
  checksums. Do not apply a blanket broker publication policy to independent components. Reconcile
  any upgrade-document mention of yanking a Python package with the external-gates statement that
  PyPI/TestPyPI are not current channels; record a documentation defect rather than inventing a
  release channel.
- [ ] If real load is advertised, run a bounded operator-owned load lane with declared latency,
  concurrency, timeout, memory/CPU, queue, and cleanup ceilings and retain measurements as product
  SLO evidence. `make load-smoke`'s import check is not load proof, and this plan introduces no
  arbitrary performance gate.
- [ ] Exercise stateful async queues, webhook delivery, reconciler restart, retention/archive/R2,
  worker boundaries, SSRF/path traversal, credential rotation, and no-downgrade configuration when
  each behavior is advertised.

## Phase 4 — execution order and gates

Run in dependency order, preserving logs and failed originals:

1. Candidate identity, static declarations, inventory/drift gate, schema validation.
2. Hermetic unit/contract/property/security/fuzz suites and J01–J27 wrapper.
3. Exact release envelopes: `uv run pytest -q -m release tests/release`; do not use a selector that
   causes release fixtures to skip.
4. Strict RunPod audit: `uv run python -m pitwall.audit.checks --strict`.
5. PostgreSQL/Redis integration, chaos, backup/restore, retention, and combined coverage/risk floors.
6. Mutation gate at 85% and all static/security/policy/OpenAPI/workflow checks.
7. Build/package/image/Gateway/Agent Routing artifact tests and deterministic reproducibility.
8. Installed CLI/MCP/TUI/Gateway/manual browser runs, including Swagger, ten TUI views and resize/
   DB-loss cases.
9. Orchestrator journeys and declared operator-live provider runs, with spend/cleanup receipts.
10. Independent review of generated matrix, evidence links, limits, exceptions, and publication
    candidate digests.

Every phase consumes the prior phase's candidate ID and fails closed on mismatch. Phase 6 is a final
freeze: after all fixes, regenerate the clean candidate identity and rerun every mandatory gate,
including inventory/drift, exact release marker suite, strict audit, J01–J27, coverage/risk,
mutation, security, artifact reproducibility, and required manual/installed/live rows, on that exact
final candidate. A failed case is
fixed inline with a red-green regression at the lowest meaningful layer, then rerun through the
appropriate integration/chaos/security/documentation/deployed lane. Dependent evidence is marked
stale until rerun. Continue unaffected branches when a provider or credential is unavailable; record
that branch honestly as blocked/fail and do not claim release completeness.

**Release gates:** exact release suite and strict audit exit 0; J01–J27 27/27 exit 0; no required
`fail`, `blocked`, `not_run`, `stale`, or unresolved exception; static/security/fuzz/OpenAPI gates
green; 74% hermetic, 77% combined, and risk floors green; mutation at least 85%; artifacts byte-
reproducible and digest-linked; all required manual/installed/live rows evidenced; cleanup verified;
and bidirectional inventory closed. Existing gates are not weakened.

## Phase 5 — exception, defect, and evidence discipline

The canonical defect ledger records ID, candidate, discovery row, severity, first failing evidence,
reproduction command, affected surfaces/cases/journeys, spend or data risk, fix commit, regression
node, retest runs, and invalidated dependent evidence. Security, billing, lifecycle, and destructive
defects block release unless the owner explicitly accepts the precise risk under the exception rule.

An exception record must include owner identity, exact surface/scenario/case scope, reason, missing
proof and why it cannot be obtained, risk analysis, compensating controls, total denominator retained,
passing numerator excluded, and separate accepted-exception counts, expiration/review date, replacement support wording, and a command/evidence
link. It must not be generated automatically for quota, missing credentials, provider absence,
flaky infrastructure, or a skipped test. An expired exception is a failure.

Evidence checksums cover raw logs, screenshots, browser exports, structured event traces, provider
receipts, cleanup reports, and generated summaries. Store credentials only in operator-controlled
secret stores; store redacted references and checksums in the packet. Every evidence item has a
reproduction command and preconditions. Browser, TUI, and live traces need an independent oracle,
not visual confidence alone.

## Phase 6 — independent review and release packet

- [ ] Reviewer reruns the inventory/drift generator and verifies no declaration was added solely to
  make a discovered surface pass.
- [ ] Reviewer samples each proof lane and every exception, traces each generated summary count to
  canonical rows, checks status semantics, and confirms no `skip=pass` or missing-test pass.
- [ ] Reviewer verifies exact candidate/artifact/lock/digest identity across CI, operator runs,
  published support wording, and any staging deployment.
- [ ] Reviewer confirms live provider evidence is operator-owned and bounded, hermetic CI contains no
  provider credentials, and no publication or paid action occurred as part of planning.
- [ ] Reviewer confirms failed originals and stale dependencies remain retained, and that fixes have
  red-green plus appropriate deeper reproof.

The packet contains, at minimum:

- candidate identity and dirty manifest;
- declaration and bidirectional generated inventories;
- canonical acceptance dataset and generated matrix/report;
- test-node index and commands/runbook;
- hermetic, integration, chaos, security, fuzz, mutation, release, package, artifact, manual,
  installed, and live logs with checksums;
- browser/Swagger/TUI evidence and full J01–J27 wrapper output;
- orchestrator event traces for each advertised harness/mode;
- provider spend/resource/cleanup receipts and independent cleanup checks;
- defect ledger, stale evidence map, exceptions, and support-claim limits;
- final artifact/SBOM/provenance/digest set and exact release gate exits.

Broker publication remains governed by `docs/release/external-release-gates.md`:
clean canonical `main`, protected tag rules, version/changelog agreement, security/dependency/license
checks, artifact and container smoke, and the existing release enable policy. Agent Routing and
Gateway retain their own namespaced tags, versions, artifact formats, provenance, and workflow gates;
do not apply broker publication rules blanketly across components. This plan does not publish,
purchase, contact, or destructively alter external resources.

## Phase 7 — maintenance and drift prevention

- [ ] Make the inventory/drift guard a required CI/release-readiness check for every interface,
  manifest, schema, plugin, shim, gateway, provider, route, config, and install change.
- [ ] Require a matrix row, test node, QA lesson update, and SDLC/source citation in the same change
  whenever a user-facing surface changes. `make docs-check` and QA heading links remain enforced.
- [ ] Recompute candidate identity and rerun affected rows whenever source, lock, generated catalog/
  schema, image digest, upstream Gateway files, harness version, model, provider configuration, or
  platform changes.
- [ ] Retire evidence only by marking it stale with reason; never delete failed or superseded runs.
- [ ] Review operator-live evidence and exceptions at expiry; remove unsupported claims rather than
  silently extending them.
- [ ] Preserve the distinction between broker releases and Agent Routing releases, and between
  source, wheel, sdist, image, plugin, and Gateway artifacts. A component release cannot inherit
  proof from a different artifact identity.

Completion of this plan is demonstrated by the terminal condition above and the packet's machine-
checked counts, not by a large aggregate test number. Aggregate counts remain useful diagnostics;
surface, case, journey, scenario, lane, provenance, and cleanup coverage are the release decision
records.
