# Single cohesive project: design

Date: 2026-09-28. Branch: `feat/single-project`, cut from local `main` at `a04acd62`.
Input: [`docs/evidence/2026-09-28-repo-systems-evaluation.md`](../../evidence/2026-09-28-repo-systems-evaluation.md).

## Goal

This repository becomes one product: Pitwall. The maintainer chose all four outcomes:

1. **One install.** Installing the release wheel provides the broker, CLI,
   TUI, MCP servers, agent routing (shims, harness adapters, channel, host plugins), the gateway,
   and the workbench.
2. **One codebase, no duplication.** One Python package, one `pyproject.toml`, one `uv.lock`, one
   test tree. Each concern is implemented once.
3. **One version and release.** One version number, one changelog, one tag scheme, one CI
   workflow, one release workflow.
4. **One product identity.** One name, one command (`pitwall`), one config file, one state
   directory, one set of docs.

### Decisions already made

| Topic | Decision |
|---|---|
| TypeScript | Port `packages/gateway` and `packages/pi-workbench` (including its comparison and acceptance runners) to Python. The Pi extensions that Pi loads in its own Node process (about 1,060 lines) ship as package data. All npm projects are retired. |
| Install footprint | Everything in one dependency set. Agent Routing's standard-library-only runtime contract is retired. |
| Compatibility | Clean break. No aliases for old commands, environment variables, or paths. One migration command carries existing installs over. |
| Evaluation findings | Phase 0 fixes the verified defects on the current structure first, each with a regression test. Duplication and dead code are resolved by the unification. One spec, one plan, one integration branch. |
| Structure | Approach A: fold everything into the `pitwall` package, organised by domain. Rejected: a multi-distribution uv workspace (fails "one codebase" and "one install"); moving agent dispatch state into the broker's Postgres (makes local routing depend on a running broker). |

## Current state

Four build units share the repository and meet only over HTTP and subprocesses:

| Unit | Toolchain | Version | Release tags | CI workflows |
|---|---|---|---|---|
| Pitwall broker (`src/pitwall`) | uv, Python 3.14 | `0.1.0a3` | `v*` | `ci.yml`, `release-readiness.yml`, `release.yml` |
| Agent Routing (`packages/agent-routing`) | separate uv project, stdlib-only runtime | `0.12.0` | `agent-routing/v*` | `agent-routing-ci.yml`, `agent-routing-release.yml`, `agent-routing-model-facts.yml` |
| Gateway (`packages/gateway`) | npm, TypeScript | `0.2.0` | `gateway/v*` | `gateway-ci.yml`, `gateway-release.yml` |
| Pi Workbench (`packages/pi-workbench`) | npm, TypeScript | `0.1.0` | none | job in `ci.yml` |

The evaluation ran at `f59e971e`. Local `main` is 134 commits ahead of that point (71 files changed
under `src/`, `packages/agent-routing/runtime`, `packages/gateway/src`, and `.github`), including
`fix(cost): one month-to-date spend definition, served by an index`, runtime budget limits
(migration 0036), and the Agent Routing `usage` command and serve mode. Some findings may already be
closed. Phase 0 therefore starts by re-verifying every finding against the branch head.

## Terms

Once merged, each of these words has exactly one meaning in code, CLI, and docs:

- **provider**: a compute or inference backend (RunPod, Vast, Lambda, Together, Model Studio, the
  gateway). This is the broker's existing meaning and keeps `src/pitwall/providers/`.
- **harness**: an agent CLI that Pitwall drives (claude, codex, kimi, grok, opencode, qwen, pi,
  hermes, cline, goose, agy, muse, dsh). Agent Routing currently calls these "providers".
- **agent profile**: a named binding of a model to a harness and optional endpoint, dispatched as
  `name@harness`. Agent Routing currently calls these "routes"; the word "route" stays with the
  broker's `routing/` (provider selection) and the gateway's route table.
- **dispatch**: one run of a harness for one prompt. **workflow**: a dependency-ordered set of
  dispatches.

## Target architecture

### Package layout

```text
src/pitwall/
  api/ core/ db/ leases/ reconciler/ routing/ providers/ cost/ finops/ mcp/ tui/ personal/ ...
                           broker modules, unchanged locations
  agents/                  Agent Routing runtime
    cli.py                 `pitwall agents ...` command group
    dispatch.py process.py run_store.py mailbox.py events.py result.py errors.py
    scheduler.py workflow.py workspace.py execution.py
    channel.py managed_channel.py channel_policy.py steer_gate.py hooks.py
    installation.py        shim and plugin install, uninstall, migrate
    doctor.py              agent checks, called from `pitwall doctor`
    profiles.py            was routes.py, route_sync.py, route_probe.py, routes_setup.py
    endpoints.py           was endpoint_slots.py, endpoint_discovery.py
    setup.py               was provider_setup.py (harness installer recipes)
    usage/                 subscription usage (the `usage` command and serve mode)
    broker.py              was pitwall.py, pitwall_sync.py (HTTP client to a remote broker)
    harnesses/             the 13 adapters, was model_routing/providers/
    resources/             config JSON and templates, was model_routing/resources/
  providers/model_studio/  the single Model Studio implementation
  gateway/                 Python port of packages/gateway, merged with gateway_catalog/
  workbench/               Python port of packages/pi-workbench
    pi_extensions/         Pi extension sources, package data
plugins/
  claude/                  was packages/agent-routing/plugins/pitwall
  codex/                   was plugins/pitwall-codex
  copilot/                 was plugins/pitwall-copilot
docs/prompting/            the 19 model prompting references
```

`packages/` is deleted. Module names above are targets; the plan may merge or split them further
where a move reveals a clearer boundary, as long as every module keeps one purpose.

### Dependencies and startup cost

- One dependency list: the current root runtime dependencies. Agent Routing adds none, because its
  runtime is stdlib-only today. The `storage`, `email`, and `tracing` extras stay optional
  integrations; `dev` stays the development extra.
- Shims and host hooks invoke the CLI on every dispatch and tool call, so startup cost matters:
  `pitwall agents _shim`, `pitwall agents _steer-gate`, and every hook entry point must not import
  `fastapi`, `uvicorn`, `asyncpg`, `redis`, `arq`, `textual`, `runpod`, `mcp`, or `prometheus_client`.
  `pitwall.cli:main` dispatches to command groups by lazy import. A guard test runs each entry path
  in a subprocess with `-X importtime` and fails if any of those modules loads.
- `pitwall.agents` keeps its stdlib-first style: it may use the shared dependencies where they
  remove duplication (for example PyYAML, `httpx` for the broker client), but the startup guard
  above is the enforced contract.

### Commands

| Old | New |
|---|---|
| `pitwall-agent-routing <cmd>` | `pitwall agents <cmd>` |
| `pitwall-agent-routing _shim <id>` | `pitwall agents _shim <harness>` |
| `pitwall-agent-routing routes ...` | `pitwall agents profiles ...` |
| `pitwall-agent-routing usage ...` | `pitwall usage ...` (top level: it is operator-facing and not agent-specific) |
| `pitwall-agent-routing doctor` | `pitwall doctor` (one report with broker, agents, gateway, workbench sections) |
| `node dist/src/shim.js` / `pitwall-gateway` | `pitwall gateway serve` |
| `pi-workbench ...` | `pitwall workbench ...` |
| `pitwall-mcp`, `pitwall mcp` (serves the broker today) | `pitwall mcp serve broker` |
| Agent Routing channel MCP server | `pitwall mcp serve channel` |

The service console scripts (`pitwall-api`, `pitwall-reconciler`, `pitwall-webhook`,
`pitwall-cost-exporter`) stay, because container images and compose files invoke them; `pitwall-mcp`
is replaced as shown. `pitwall` is the only user-facing console script.

### MCP servers

Two MCP servers remain, both served by the one CLI, because they have different lifecycles and
requirements:

- **broker** (79 `pitwall_*` tools): requires broker configuration (`DATABASE_URL` and friends),
  exposes provider, lease, cost, and RunPod operations.
- **channel**: the orchestrator channel for dispatches (`dispatch_and_wait`, `inbox`, `answer_ask`,
  and so on). It must work on a workstation with no broker.

`pitwall mcp install` registers both with claude-code, codex, and opencode, by environment
reference as today. The host plugin's `.mcp.json` points at `pitwall mcp serve channel`.

### Shims and host plugins

- `pitwall agents install` writes `~/.claude/scripts/<harness>-shim.sh` and `route-shim.sh` (the
  names Claude Code agents already reference) as generated two-line wrappers:
  `exec pitwall agents _shim <harness> "$@"`. The shim contract (`SHIM-DONE exit=N`, last-two-line
  result parsing) is unchanged.
- `plugins/claude`, `plugins/codex`, and `plugins/copilot` are installed from the package's data by
  `pitwall agents install`. Plugin identity stays `pitwall:*`. Generated references
  (`routes.generated.md`, `provider-registry.generated.json`) are generated once into the package and
  copied into each plugin at install time instead of being committed three times.
- `scripts/bootstrap.sh` and `scripts/install.sh` are replaced by installing the release wheel followed
  by `pitwall agents install`.

### Environment variables, config, and state

- `SUBAGENT_MODEL_ROUTING_*` and `PITWALL_AGENT_ROUTING_*` become `PITWALL_AGENTS_*`
  (for example `PITWALL_AGENTS_UNRESTRICTED`, `PITWALL_AGENTS_TIMEOUT_SECS` replacing
  `SHIM_TIMEOUT_SECS`). The legacy `PITWALL_API_TOKEN` alias is removed; the broker client reads
  `PITWALL_API_URL` and `PITWALL_AGENTS_API_TOKEN`.
- One config file, `pitwall.toml` (located by `PITWALL_CONFIG_FILE` as today), with an
  `[agents]` table for what Agent Routing kept in its own config and an `[agents.profiles]` table
  replacing `routes.json`.
- One state root, `~/.local/state/pitwall/` (already used by personal mode), with `agents/` holding
  the run store, mailbox, receipts, and usage cache. XDG overrides apply to the root.

### Broker and agents coupling

The agents runtime talks to a broker only as a remote HTTP client (`agents/broker.py`), because the
broker is a separately deployed service. In one package it imports the broker's Pydantic request and
response models from `pitwall.api` instead of hand-building dicts, so the two sides cannot drift.
No agents module imports broker services, repositories, or the database layer; a guard test enforces
that direction.

## Gateway port

`packages/gateway` becomes `src/pitwall/gateway/`, merged with `src/pitwall/gateway_catalog/`.

**Behaviour kept.** A loopback-only OpenAI-compatible server started by personal mode
(`personal/gateway.py`) on port 20130 with a bearer token from the environment, the route table from
`PITWALL_GATEWAY_ROUTES` (`config/gateway-routes.json`), the `x-pitwall-route` and
`x-pitwall-compression` headers, the five routes (chat completions, models, embeddings, health,
telemetry), inbound shape translation, request compression, per-token rate limiting, a body cap,
and sanitised error bodies.

**Implementation.** An ASGI app served by uvicorn from `pitwall gateway serve`, supervised by personal
mode as a child process exactly as the Node process is today. It reuses, rather than re-implements:
`pitwall.security.redaction` for error sanitising (replacing the vendored OmniRoute sanitiser and
its `open-sse/` files), the broker's OpenAI proxy helpers in `routing/openai.py` and
`routing/fallback.py` for upstream request shaping and SSE relay, and `rate_limits.TokenBucket`.

**Fixes carried by the port** (from the evaluation, lane E):

- Streaming uses a first-byte deadline and an idle deadline instead of one 30 s total deadline. When
  a stream is cut short by a deadline or upstream error, the server sends a terminal SSE error event
  and aborts the connection; it never ends a truncated stream as if complete.
- Bearer comparison is constant-time (`hmac.compare_digest`) in one shared dependency.
- Rate limiting applies to every authenticated route.
- The 413 message reports the configured cap. Non-SSE upstream bodies are size-capped.
- The executor registry test hooks are not part of the production module.
- `/v1/models` behaviour in route-table mode is documented as it behaves.

**Parity.** The 132 vitest cases in `packages/gateway/tests/` are the behavioural spec. Each is
translated to a pytest case against the Python server before the TypeScript package is deleted.
Cases that encode a defect listed above are inverted to assert the fixed behaviour.

**Catalog sync.** `tools/gateway/extract_upstream.mjs` (a regex parser) is ported to Python.
`gateway_catalog/sync.py` downloads the pinned `omniroute` tarball from the npm registry over HTTPS,
verifies it against the registry's published integrity hash, and extracts it in Python. `npm` and
`node` are no longer invoked. The committed outputs (`config/gateway-catalog.json`,
`config/gateway-catalog.lock.json`, `config/gateway-routes.json`, `seed/gateway-providers.yaml`) and
the drift check are unchanged.

## Workbench port

`packages/pi-workbench` becomes `src/pitwall/workbench/`, exposed as `pitwall workbench ...`.

**Ported to Python** (about 3,500 lines of TypeScript): the CLI, launcher, exact provider and model
profiles (`profile`, `native-profile`, `hosted-profiles`, `runtime-settings`), the isolated
`PI_CODING_AGENT_DIR`, planning mode, restricted mode (bubblewrap plus the seccomp filter), the
handoff workspace (worktree, patch capture, review, approved checks, integration), task records,
accounting and account budget, shared admission, timeouts, doctor, and the comparison and acceptance
runners (`comparison-*`, `hosted-*`, `scripts/`).

**Kept as JavaScript package data** in `workbench/pi_extensions/`: `provider-extension`,
`native-extension`, `extension`, `restricted-extension`, and the comparison lifecycle observer. Pi
loads these into its own process through `--extension <path>`; the Python launcher passes the
installed file paths. They are compiled once to plain `.js` and committed, so no build step is
needed at install time. A test asserts each committed `.js` file matches its checked-in source
compiled with the recorded TypeScript version, and CI runs that compile with a pinned `npx tsc`
only in that one test job.

**External requirement.** Using the workbench requires the `pi` binary
(`@earendil-works/pi-coding-agent` 0.84.4 and `@tintinweb/pi-subagents` 0.19.0), exactly as the codex
harness requires the `codex` binary. `pitwall workbench doctor` reports a missing or mismatched `pi`.
`pitwall agents setup pi` installs it using the existing harness installer recipes.

**Seccomp.** The BPF program is generated in Python (`struct`-packed `sock_filter` entries) with the
same policy as `restricted.ts`: deny the x86_64 socket syscalls, `accept4`, `recvmmsg`, `sendmmsg`,
and io_uring, reject x32 and unexpected ABIs, and never fall back to unrestricted mode. A test decodes
the generated program and asserts the policy instruction by instruction.

**Fixes carried by the port** (from lane E): approved checks run with a scrubbed environment using
the same credential filter as restricted tool children; the flaky timeout test waits for the pid
file; type checking is `mypy --strict` like the rest of the package.

## Deduplication

Each item ends with exactly one implementation; callers move to it; the others are deleted.

| Concern | Today | One implementation |
|---|---|---|
| Model Studio catalog and client | `src/pitwall/providers/model_studio/` and `packages/agent-routing/runtime/model_routing/model_studio.py`, `model_studio_openapi.py`, two `catalog.json` copies | `pitwall/providers/model_studio/`; agents usage and harness setup import it |
| Route planning | `routing/planner.plan_route` (simulator, time machine), `routing/production.build_production_plan` (live), `resolver.select_stage12_provider` | `build_production_plan`; the simulator calls it with injected state; `select_stage12_provider` is folded into it |
| Month-to-date spend | gate, alerts, exporter, TUI queries (partly unified on `main`) | one function in `cost/`, used by every reader |
| REST and MCP serializers | capability, provider, lease responses copied in up to three places | one `api/serializers.py` used by REST and MCP |
| RunPod HTTP retry | `queue.py`, `lb.py`, `serverless.py`, `serverless_lb.py` loops | one retry helper in `runpod_client/` with an explicit "safe to retry" predicate |
| RunPod credentials | `resolve_runpod_api_key` in three clients, env-only reads in four | `resolve_runpod_api_key` everywhere |
| YAML | `seed._parse_simple_yaml` and PyYAML | PyYAML only |
| Budget alerts | 80% alert and unwired `threshold_alerts` | the 80% alert |
| Doctor | `pitwall doctor` and Agent Routing `doctor` | one `pitwall doctor` |
| Harness model flag parsing | re-implemented per adapter | one helper in `agents/harnesses/base.py` |
| Release scripts | `scripts/release/*` and `packages/agent-routing/tools/release/*` | `scripts/release/*` |
| Generated references | three committed copies | generated once, copied at install |
| Error sanitising | vendored OmniRoute sanitiser and `security/redaction` | `security/redaction` |
| OpenAI-compatible proxying | gateway shim and broker `routing/openai.py` | broker helpers, used by the gateway |
| Provider-specific branches in shared layers | `routing/fallback.py`, `routing/lockout.py`, seed, reconciler, provider schemas | a provider declares its lockout config shape, fallback behaviour, and seed data through its adapter; shared layers read the declaration |

## Removals requiring approval

The maintainer's standing rule is that features, modules, endpoints, and tests are not deleted
without a listed yes. Approving this spec approves exactly this list; anything else found during
implementation is listed and asked about separately.

Code with no production caller (evaluation lanes A, B, C, F):

1. Routing primitives and their unit and property tests: `routing/canary.py`, `prewarm.py`,
   `failover.py`, `semantic_cache.py`, `carbon.py`, `cascade.py`, `hedging.py`,
   `quality_routing.py`, `arbitrage.py` (about 3.7k lines). `autopilot` references only the
   `prewarm` type; that reference is removed with it.
2. `routing/planner.py` after the simulator moves to the production planner.
3. `cost/threshold_alerts.py`, `cost/slo_governor.py`, `finops/bidding.py`,
   `finops/time_machine.py`, and their tests.
4. `providers/drift.py`, `providers/_wave2_feasibility.py`, and the recommendations that depend on
   `drift` in `recommendations/engine.py`.
5. `rate_limits/store.py` (`RateBucketStore`, `halved_capacity`) and the unwired `on_429` hook.
6. `gitops.apply_plan` and its tests (`build_reconcile_plan` stays; the MCP copilot uses it).
7. `workers/vllm.py`, `workers/header_policy.py`, and `tests/workers/` (ADR 0002 deferred the worker;
   `worker.py`, the fail-closed tombstone, stays).
8. `workload_lifecycle.enqueue_submit_runpod_job` and `insert_passthrough_workload`.
9. `src/pitwall/live.py` moves to `tests/` (it is a test helper shipped in the wheel).
10. `tools/smoke_fallback_drill`, `smoke_kill_drill`, `smoke_model_council`,
    `smoke_openai_proxy`, `smoke_pitwall_route`, `smoke_runpod_url`,
    `tools/benchmark_embedding_latency`.
11. `tools/guards/forbidden_imports.py` (guards names that no longer exist) and its pre-commit hook.
12. `packages/pi-workbench/scripts/two-tui-native.mts` (no references).
13. `packages/agent-routing/scripts/bootstrap.sh`, `install.sh`, the `pitwall-agent-routing`
    launcher, the 14 committed shim scripts, and `parse-shim-result.py` (replaced by generated shims
    and the CLI).
14. The GNU `timeout`/`gtimeout` prerequisite check in dispatch (the timeout is enforced in Python).
15. The vendored OmniRoute `open-sse/` files and `UPSTREAM.lock`, with the gateway port.
16. All legacy names: `SUBAGENT_MODEL_ROUTING_*`, `PITWALL_AGENT_ROUTING_*`, `PITWALL_API_TOKEN`,
    `pitwall-agent-routing`, `pitwall-gateway`, `pi-workbench`, `pitwall-mcp`, the
    `subagent-model-routing-local` marketplace name.
17. The npm projects `packages/gateway` and `packages/pi-workbench` after parity, and the
    `gateway-ci.yml`, `gateway-release.yml`, `agent-routing-ci.yml`, `agent-routing-release.yml`
    workflows after their jobs move into `ci.yml` and `release.yml`.

## Phase 0: defect fixes

Phase 0 runs on the current structure, before anything moves, so each fix is testable where the
defect lives and cannot be confused with a move error.

**Step 0.1: re-verify.** For every finding in the evaluation, check it against the branch head and
record one of: still present, already fixed on `main` (cite the commit), or not reproducible (cite
the evidence). The plan's finding ledger starts from this table.

**Step 0.2: fix every still-present finding of high or medium severity** with a failing test first.
The verified high-severity findings, which must each have a regression test:

- Webhook terminal-status job name: the receiver enqueues the registered name; a test asserts every
  enqueued job name appears in `WorkerSettings.functions` by registered name.
- Lease expiry sweep: each lease's teardown is isolated so one failure is logged, counted, and left
  to `_retry_stuck_teardowns` while the sweep continues; test with an injected failing teardown.
- `serve_model`: any exception after the lease exists (including `CancelledError`) tears the lease
  down before re-raising; tests for each post-launch failure path.
- RunPod queue `/run`: retried only when the request provably was not accepted (connect errors, 429);
  read timeouts and 5xx after send are not retried; the "safe to retry" predicate is unit-tested.
- Control-plane `create_pod`: no outer cancellation that can orphan a pod; the backend's own attempt
  timeouts bound the call and the result is always audited, including on timeout.
- Budget: the exporter refuses to start without a configured monthly budget instead of assuming
  $1000; the gate's month boundary is computed with the same UTC-safe expression as the other
  readers; alerts use the gate's month-to-date function.
- MCP "Admin-only" descriptions: the unused `scope` field is removed and tool descriptions state the
  actual trust model (local stdio process); no enforcement is claimed that does not exist.
- CI: the release journey harness runs in CI against the disposable service database, and
  `gateway-catalog-drift` is in the required aggregate. (Superseded 2026-09-30: the maintainer moved the journey harness out of pull-request CI; it runs locally and in `release-readiness.yml`. See the plan's Decisions.)

Medium findings are fixed the same way (for example webhook retry scheduling is completed with the
existing `next_retry_at` column and repository methods; the backup drill gets `postgresql-client`
in the reconciler image and runs its subprocesses without blocking the event loop; retention purge
skips rows whose object keys it cannot delete instead of failing, and the image creates the archive
directory for the service user; the policy loader uses PyYAML; `_docker_psql` honours
`DATABASE_URL`; CLI and MCP lease renewal publish the renewal event; MCP rejects naive datetimes;
money is serialised as strings; the proxy path fails over and records lockout on 429; Model Studio
availability respects the automation gate and its stream parser rejects malformed and error
chunks; kimi and dsh honour the unrestricted setting; an invalid timeout setting is a usage error;
the 8 unpinned harness installer recipes get pinned SHA-256 values).

**Step 0.3: low-severity findings** are fixed in Phase 0 when they are local to the defect's file,
and otherwise closed by the phase that moves or deletes their code. Gateway and workbench findings
are closed by their ports. The ledger records which.

## Version, release, CI, and docs

- **Version.** The unified package continues the root version line. The first unified release is
  `0.2.0a1`: a pre-1.0 minor bump, because commands, environment variables, and paths change. Tags
  are `v*` only.
- **Changelog.** One `CHANGELOG.md`. The Agent Routing and gateway changelogs are folded in under a
  "History before unification" section, with their last versions (`agent-routing/v0.12.0`,
  `gateway/v0.2.0`) recorded.
- **CI.** One `ci.yml` with one job set: lint, format, typecheck, docs, security, hermetic tests
  (now including agents, gateway, and workbench tests), integration, the release journey harness,
  the gateway catalog drift check, and the Pi extension compile check. Model facts
  (`agent-routing-model-facts.yml`) keeps its schedule as a job or workflow of the one project.
  `release-readiness.yml` stops re-running jobs the tagged commit already passed: it verifies the
  commit's `ci.yml` result and runs only release-specific steps (artifact build, inspection, smoke,
  journeys). (Superseded 2026-09-30: the maintainer moved the journey harness out of pull-request CI; it runs locally and in `release-readiness.yml`. See the plan's Decisions.)
- **Release.** One `release.yml` builds and publishes the one wheel and sdist, the container images,
  and the release notes.
- **Docs.** `README.md`, `CONTRIBUTING.md`, `RELEASING.md`, and `docs/sdlc/` describe the one
  product. The Agent Routing docs move into `docs/agents/` and the prompting references into
  `docs/prompting/`. SDLC docs reference code as `module:symbol`, not `file:line`, so moves do not
  invalidate them. `RELEASING.md` names the journey harness as a release requirement.
- **Workspace guidance.** The local `AGENTS.md` rules that require separate uv projects and a
  stdlib-only Agent Routing runtime are replaced by this design's rules (one project, the startup
  import guard, the agents-to-broker import direction).

## Migration

`pitwall agents migrate` performs the clean break for an existing workstation, idempotently:

1. Reads the old config (`routes.json` and Agent Routing settings) and writes the `[agents]` and
   `[agents.profiles]` tables into `pitwall.toml`, refusing to overwrite differing existing values.
2. Moves the run store, mailbox, receipts, and usage cache into `~/.local/state/pitwall/agents/`.
3. Rewrites installed shims and plugin registrations to the new commands.
4. Reports environment variables still set under old names, with the new name for each (it cannot
   edit the user's shell profile).
5. Removes the old install only after the steps above succeed, and prints what it removed.

A test runs the migration against a fixture of the current Agent Routing layout and asserts the
resulting config, state, and shims, and that a second run is a no-op.

## Phases

All phases land on the one integration branch `feat/single-project`, one commit series, one pull
request, with the full CI run on the branch rather than per phase.

| Phase | Content | Exit condition |
|---|---|---|
| 0 | Re-verify findings; fix defects on the current structure | Ledger complete; every high and medium finding fixed with a test or recorded as already fixed |
| 1 | Move Agent Routing into `pitwall.agents`: one `pyproject.toml` and `uv.lock`, tests into `tests/agents/`, `pitwall agents` commands, new names, config, state, plugins at top level, generated shims, migration command, startup import guard, agents-to-broker import guard | Agent Routing's full test suite passes under the root project; `packages/agent-routing` deleted |
| 2 | Deduplication and approved removals | Every row of the deduplication table resolved; removals list executed |
| 3 | Gateway port | 132 translated parity cases plus fix cases pass; personal mode serves through the Python gateway; `packages/gateway` deleted |
| 4 | Workbench port | Ported tests pass; seccomp policy test passes; Pi extension compile check passes; `packages/pi-workbench` deleted |
| 5 | Version, changelog, CI, release, docs, workspace guidance | One CI workflow green; release dry run builds one wheel; docs link check passes; full journey harness passes |

The plan decomposes each phase into tasks small enough for one implementation session each, and
maps every evaluation finding to the task that closes it.

## Testing

- Every Phase 0 fix starts with a failing test.
- Agent Routing's unittest suite runs under pytest unchanged in Phase 1 before any refactor, so the
  move is proven behaviour-preserving before Phase 2 changes behaviour.
- Gateway and workbench ports are driven by translated versions of their existing TypeScript tests.
- New guard tests: registered Arq job names; startup import set for shim and hook paths; agents
  modules never import broker services or DB; seccomp program decode; Pi extension compile match;
  migration fixture.
- Before the branch lands: `make test-fast`, `make test-int` (serialised, never alongside other DB
  suites), the full release journey harness, and the docs link check, each with its output recorded
  in the plan's completion evidence.
