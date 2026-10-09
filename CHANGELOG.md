# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0a1] - 2026-10-07

### Added

- Repeating an `idempotency_key` is now safe on paid calls. RunPod control-plane applies replay the
  stored result for the same request and refuse a reused key with a different request as
  `idempotency_conflict`; a prior attempt of unknown outcome is `mutation_outcome_ambiguous`. Both
  codes and `mutation_in_progress` answer 409 on the RunPod resource routes. `POST /v1/leases`,
  `pitwall_lease_pod`, and `pitwall_serve_model` return the existing lease (`replayed: true` on
  `POST /v1/leases`) instead of launching a second pod, and a launch still in progress answers 409
  `mutation_in_progress`. Lambda Cloud provisioning replays the same way. Pods carry a
  `PITWALL_CREATE_ATTEMPT` marker, and the reconciler resolves a create whose outcome is unknown by
  that marker, or by the pod id a completed create recorded. A marked pod is leased and settled at
  accrued cost even if it has already terminated; a pod that only shares the create's name is held,
  never adopted, and no pod is terminated without proof that it is ours.
- ZCode joins as the fourteenth routing harness (`zcode`). It runs the installed ZCode CLI with
  its saved account and model: `zcode-default` labels that saved selection in a profile, dispatch
  passes `--mode yolo` (or `--mode build` when `PITWALL_AGENTS_UNRESTRICTED=0`), `pitwall agents
  setup mcp` registers `pitwall-channel` in `~/.zcode/cli/config.json`, and the harness installer
  reports manual installation when the CLI is missing.
- The `pitwall-channel` MCP server registers for Grok, Antigravity, Muse, Hermes, and goose, so each
  can run as a managed child that asks its parent. `pitwall agents setup mcp` writes their configs,
  adopts a Grok table that `grok mcp add` rewrote without markers, and edits Hermes and goose YAML
  as one managed line. The Codex and Copilot routing skills now send routed work through
  `dispatch_and_wait`. A channel entry the harness has disabled (`enabled: false`, or
  `disabled: true` for Antigravity) counts as unregistered, so a managed dispatch falls back instead of
  being admitted on a channel the harness will not load.
- A run records its supervisor (`pid` and start identity) in `run.json`, and a run whose supervisor
  was killed is recorded `failed` with an `abandoned.json` instead of staying `running`. `runs
  list`, `runs show`, `runs stop`, `runs cleanup`, and a managed wait all reconcile it. `runs
  cleanup` keeps a standalone run whose supervisor may still be alive, and a managed wait never
  declares one orphaned. The run also records its harness's process group, and reconciling ends a
  harness that outlived its killed supervisor before the run is recorded `failed`; `runs stop` ends
  one still running behind a terminal record. A managed run is judged by its own supervisor: a launch
  sidecar no longer keeps a run whose supervisor died from being reconciled.
- Claude Haiku 5.5 (`claude-haiku-5-5`) is in the model facts as the `claude-haiku-5` family and is
  registered under the `claude` harness as `haiku`. `claude-opus-5` joins the Opus facts, and
  `claude-mythos-5.1` is recorded as a verified-organization id of Fable 5.1 with no route.
- GPT-6.1 Sol (`gpt-6.1-sol`) is the registered Sol model for the `codex` harness. The codex model
  facts and the OpenAI prompting reference cover the GPT-6 family guidance: effort rules,
  `configuration_update`, async tool calling, mid-turn steering, caching changes, and migration.
- Model dossiers for the Xiaomi MiMo V2.6 releases (Pro-RL, Flash-RL, Pro-MOPD, Flash-MOPD, and
  Distill-Qwen-9B).

### Changed

- Agent Routing: harness CLIs keep their own sandbox and approval prompts unless
  `PITWALL_AGENTS_UNRESTRICTED=1` is set. Previously an unset variable meant unrestricted. Kimi Code
  and dsh refuse to dispatch without `=1`, and `pitwall agents doctor` reports an unset variable as
  restricted (PASS).
- Install from the GitHub release wheel. The `pitwall` name on PyPI belongs to an unrelated
  project, so no install instruction or recovery message names it.
- Dependabot version updates cover uv, GitHub Actions, Docker, Docker Compose, and the Pi npm lock;
  the Dockerfiles name their base image in `FROM` so it can be updated.
- MCP: the broker and the orchestrator channel serve protocol 2026-07-28 (stateless requests,
  `server/discover`, `resultType`, cache hints, `subscriptions/listen`) as well as the legacy
  `initialize` handshake. The broker now runs on the MCP Python SDK 2 (`mcp>=2.3.0,<3`).
- MCP: every broker and channel tool has a title and behavior annotations, and every broker
  parameter is described. Broker discovery carries instructions and advertises tools only.
  `pitwall_serve_model` and `pitwall_runpod_grow_volume` are annotated destructive.
- MCP: an unknown tool is JSON-RPC error -32602. The broker refuses an undeclared argument with
  `invalid_tool_arguments` and the allowed names, and the channel's argument errors name the
  declared parameters to fix. Tool calls are rate limited (broker: `rate_limited` with
  `retry_after_s`). Broker tool failures remain `isError` results carrying only the string `error`
  code, and `pitwall mcp relay` answers a request it cannot deliver with the retryable error code
  -31010, outside the JSON-RPC reserved range.
- Lease spend always reaches the budget. Lambda Cloud and Vast leases reserve their hourly rate
  for the lease TTL and settle at it. A RunPod lease with tagged or no pricing, or an uncapped raw
  pod, settles at a real rate and never at $0. Teardown, renewal, and launch share one rate
  resolver, so the amount reserved matches the amount settled.
- `gpt-6-sol` is no longer registered for the `codex` harness; GPT-6.1 Sol replaces it. Its model
  facts remain, and a second codex watch now reads OpenAI's model index so new GPT models are reported.
- `pitwall agents doctor` waits up to 30 seconds for a slow channel server, probes both roles and
  both protocol eras concurrently, and names why a probe failed.
- The fixture check and Pi stop grace in the workbench acceptance gates tolerate a loaded host:
  the fixture-check timer is a 120 second hang guard, and Pi gets the same stop grace as the native
  acceptance run.

### Fixed

- Installing from the release wheel uses `uv tool install --python 3.14`, so any 3.14 patch
  works, and the README states the uv floor (0.12.2). The CLI's install hints come from one
  module, so the README, release notes, and in-product hints give the same command.
- The pinned installer scripts for codex, claude, grok, hermes, agy, and qwen are re-verified against
  what the vendors serve today; codex may redirect to `releases.openai.com`. `pitwall agents setup
  --dry-run` downloads and checks each script's sha256 instead of only printing the plan.
- `pitwall agents dispatch` exits 73 with one line naming `XDG_STATE_HOME`/`HOME` when it cannot
  write run state, instead of a traceback. Empty `XDG_*` variables fall back to the defaults.
- `pitwall agents doctor` reports the exit code of a channel server that dies before the first
  request, instead of a protocol error. Process checks give up on a `ps` that does not answer
  within 5 s. A missing harness binary names how to install it, `pitwall agents migrate` runs
  without git, and installs write their files atomically.
- The plugin hooks run on Python 3.9, so a system `python3` older than the project's can run them,
  and the steer gate finds `pitwall` in `~/.local/bin` when that directory is not on `PATH`.
- Broker CLI commands that cannot reach Postgres, Redis, or the API print one line naming the
  endpoint and the fix, instead of a traceback. Clients find the API through `PITWALL_API_URL`,
  then `PITWALL_BASE_URL`, then `PITWALL_API_PORT` on loopback, so moving the API's port moves
  every client. The API and cost exporter exit with one line at startup when the database is
  unreachable or (for the exporter) no monthly budget is configured.
- `pitwall doctor` reports an invalid `pitwall.toml` as a failure. Personal-mode setup writes the
  `PATH` line to the profile of the shell in use. Stopping personal mode recognises the gateway it
  started on macOS, where there is no `/proc`. On a narrow terminal, tables keep identifier
  columns whole and wrap the others instead of cutting them off.
- The workbench keeps its state under `$XDG_STATE_HOME/pitwall`, `pitwall workbench launch` says it
  needs Linux on other systems, and the workbench doctor's `flock` row is a skip off Linux. The
  doctor's Node floor (22.22.1) is the one CI tests and the docs state.
- README: the broker quick start keeps its configuration in `.env.quickstart.local`, which both
  terminals load, and the README states where workload data goes, what the budget checks do and do
  not cap, and the RunPod prerequisites and charges for `serve`.
- Release: `SHA256SUMS` lists basenames, so `sha256sum -c SHA256SUMS` works in the download
  directory, and each release carries the image SBOMs, published digests, and Compose smoke
  evidence.
- Legal: `NOTICE` reproduces OmniRoute's MIT notice for the derived gateway catalog and disclaims
  affiliation with every named provider; the committed SBOM describes 0.3.0a1; the license check
  inventories the base, extras, dev, and npm graphs with extras followed transitively.
- CI installs the Pi test packages from `tools/pi-deps/package-lock.json`.
- `POST /v1/leases/{lease_id}/renew` and `POST /v1/leases` no longer return HTTP 500 when the
  broker's budget is unset or invalid (not a positive, finite decimal). They answer 503
  `budget_not_configured` with a remedy naming `PITWALL_MONTHLY_BUDGET_USD` and
  `PITWALL_PER_REQUEST_MAX_USD`, never echoing the value; the CLI and MCP report the same code,
  which an invalid value now also maps to. A same-key retry of an applied renewal still returns
  its stored result when the budget is unset or invalid.
- RunPod pods are created and terminated with the provider's own `credential_ref` credential. A
  routed launch, a REST stop, a cancelled create, and a launch that failed after creating its pod
  used the process RunPod key, so a pod belonging to another account was created or terminated
  against the wrong one; an unset credential now fails before any spend. Arming and disarming the
  OpenAI proxy decide on the locked live provider row, so a lease armed in between is no longer
  disarmed and neither path overwrites the other's config.
- Lambda Cloud provisioning requires `quantity` 1: any other value is refused before admission and
  egress, and a launch response naming more than one instance has every returned instance terminated
  before the error is raised. One malformed instance id no longer stops the others from being
  terminated.
- Budget accounting is exact under concurrency. A sub-budget (tag) check runs inside the admission
  lock, so two concurrent launches can no longer both fit one allocation. The monthly budget alert is
  sent once when two checks run together. A free quota pool shared by several providers is counted
  once in the free-tier comparison. A database-backed sub-budget spend
  resolver receives the admission's own connection and must accept it as `tag_mtd_spend(tag, conn)`;
  `SubBudgetGate` refuses a resolver that cannot, since it would wait on the pool while the admission
  holds it.
- An idempotency key replays only the same request. A different body sent while the first request was
  still being admitted used to be replayed; the key now stores a hash of the request body, and a
  different body is refused as `idempotency_mismatch`. Rows created before migration
  `0043_idempotency_body_hash.sql` keep the old comparison. A budget-refused synchronous request no
  longer binds its `Idempotency-Key`: a 402 used to leave the key tied to the refused body, so a
  later request with a different body got an idempotency mismatch.
- The capability audit checks the limits admission enforces. It reads the runtime budget limits and
  checks the quote's ceiling, not the displayed estimate, against the per-request cap and the monthly
  headroom. The headroom details key `required_estimate_usd` is renamed `required_ceiling_usd`. A
  blocked or empty audit no longer reads budget state, and a malformed timeout or image-pull setting
  (`PITWALL_IMAGE_PULL_TIMEOUT_S`, `PITWALL_AUDIT_STARTUP_TIMEOUT_S`, and the other audit timeouts)
  fails its check, naming the setting, instead of crashing the whole audit.
- Unsupported Anthropic content blocks are refused. `POST /v1/messages` answers 400
  `invalid_request_error` naming the block type when a message, `system`, or a tool result holds
  anything other than text, `tool_use`, or `tool_result` blocks, before any budget or provider work;
  an image-only message was previously billed as an empty prompt. A Messages stream whose upstream
  sends an error frame or fails mid-stream now ends with one Anthropic `error` event and no
  `message_stop`, and the stream parser accepts CRLF and CR line endings. A 429 on a Messages stream
  now locks the provider model out, as on the OpenAI route. Upstream error text in a Messages
  response, streamed or not, is redacted and bounded, and a non-2xx upstream reply is answered in the
  Anthropic error envelope. A stream that ends without a finish reason, sends invalid UTF-8, or reports
  an error ends with one `error` event and settles its workload `failed`.
- The admin API answers hostile input with a 4xx. The schema fuzz now authenticates against the
  secret-gated admin operations, and it found server errors that are fixed: a non-ASCII admin secret
  or bearer token, a NUL in a provider id, provider config key, filter, or kill-switch reason, a
  storage-policy rejection, capability text Postgres cannot store, and out-of-range budget limits.
- The pre-spend payload inspection refuses a NUL in a mapping key as it does in a value, so such a
  payload is refused up front instead of failing in Postgres with a 500.
- Provider and capability updates apply `enabled` through the MCP tools and `PATCH
  /v1/admin/providers/{id}`, in one transaction that audits the toggle. A no-op update writes no
  audit row.
- `pitwall leases renew` and the reconciler's activity auto-renewal no longer fail on Postgres:
  the audit table now admits the `cli:lease` and `reconciler:activity` actors.
- The reconciler's lease-expiry sweep no longer aborts for every lease when one lease's renewal
  price cannot be computed. It logs and continues.
- A serve replay whose lease is active but does not list the served model answers 409
  `mutation_in_progress` with the reason `lease_not_serving`, the lease id, and a remedy: retry
  later, or end the lease with `pitwall_stop_lease` and serve again.
- RunPod templates are cached per account, container disk size, and registry auth id. A template from
  another account or with an old disk size was reused; all three now enter `config_sha` (the account
  only as an HMAC digest, never the key) and the display name. Existing cache rows miss once.
  `pitwall register-template` dry runs preview the name `ensure_template` creates.
- RunPod pod logs report `truncated` exactly at a byte or line cap, and a silent open stream at an
  exact cap no longer waits out the deadline. Ranged volume reads reject a server that ignored
  `Range`: a nonzero offset needs a 206 with a matching `Content-Range`.
- Embedding through the broker never sends the RunPod key to Pitwall: `ServerlessLBClient` with
  `via_pitwall` authenticates to Pitwall with `PITWALL_API_TOKEN` only.
- The personal RunPod key is resolved once. The dashboard, the TUI, and the MCP serve tool report a
  missing key as `credential_reference_unset` on the screen or the wire instead of crashing, and a
  failed lease refresh in the TUI keeps the last known pod count marked stale.
- Provider lockout state keeps the newest write: every transition carries a sequence number and a
  delayed older write never replaces a newer failure or success; a malformed persisted lockout still
  advances the sequence.
- The database pool is created once under concurrent first use, and its lock no longer keeps a closed
  event loop alive. `pitwall db migrate` and `pitwall db status` report an applied migration whose
  file is missing from the package as drift (one line per kind, filename case preserved, both kinds
  kept in JSON output) and `migrate` refuses it. Drill evidence is filtered by `drill_type` in SQL
  before the limit, including legacy double-encoded rows.
- Codex discovery follows `CODEX_HOME`. When `CODEX_HOME` is set, the Codex adapter, discovery, the
  capability inventory, usage accounts, and `pitwall mcp install` all read `$CODEX_HOME/config.toml`
  (a leading `~` expands against the home directory) and discovery no longer falls back to
  `~/.codex`. Unset, they use `~/.codex` as before. The generic OpenAI-compatible route probe reports
  a model list that is not a list of objects as down.
- `pitwall mcp install --scope project` registers the channel in project scope (`.mcp.json` for
  Claude, `opencode.json` for OpenCode) or refuses up front, exit 2, for any other harness, before
  writing anything. The channel step runs only for harnesses whose broker step succeeded, and the
  preview shows the path and the managed entry only, never the rest of the file.
- `pitwall mcp serve broker --json` no longer writes to stdout ahead of the protocol.
- `pitwall mcp relay` drops non-JSON server output instead of forwarding it, forwards only JSON-RPC
  objects, and restarts the server only when a damaged fragment swallowed a pending reply. When the
  server exits mid-write it answers the pending request with the retryable `-31010` restart error
  and restarts the server; every replay uses a fresh id so a client request can no longer collide
  with it, and the restart log names the real cause.
- The channel rejects undeclared tool arguments and null request ids, and no longer echoes
  exception text.
- The channel server no longer crashes at shutdown when the client has already closed its stdout:
  completing an open `subscriptions/listen` raised `BrokenPipeError` and the process exited 120 with a
  traceback. It now exits 0.
- The tier-2 steering gate no longer blocks Codex's own channel tools, which Codex names
  `mcp__pitwall_channel__*`; a blocking steer to a Codex child deadlocked it.
- OpenCode exiting 0 with nothing on stdout is recorded as exit 77, not success, and the reason is
  recorded as `softDenialReason` in `run.json` and returned as `soft_denial_reason` by a managed
  wait.
- An aborted run reports exit 143 even when the harness exits 0 on SIGTERM.
- Ctrl+C during a supervised run is always honoured and reported as `cancelled` (exit 130): no
  harness is left running when the interrupt arrives early, late, or during an abort's grace, and
  lifecycle hooks stop at a pending Ctrl+C.
- `steer` refuses directives a run without the orchestrator channel can never read, and a refused
  steer leaves no trace in the run directory. Blocking steers still unacknowledged when a run ends
  are reported; the all-runs inbox skips finished runs. The HTTP broker's steer endpoint refuses a
  finished run as the CLI and MCP paths do.
- Claude prompts are delivered on stdin, so prompts over 120 KiB dispatch.
- A flag in the prompt-source position prints usage instead of creating a failed run, and `--help`
  works for every harness.
- Managed launches no longer store every byte of child output a second time under `launches/`.
- `prompt.deliver.md` is removed when a run ends unless `--routing-retain-prompt` was passed.
- Launch-guard lock files are removed at session end and swept after a week, without splitting a
  lock a live caller holds.
- `pitwall agents runs discard` can remove a migrated isolated run whose old
  `pitwall-agent-routing/` branch no longer exists: `pitwall agents migrate` now records the current
  branch name for it, and discard skips the missing branch and removes only the run's own worktree. It refuses, and
  deletes nothing, when that worktree is checked out on another branch.
- `pitwall agents migrate` handles every earlier worktree branch prefix and follows moved worktrees
  whose branch is gone; managed terminal payloads report current artifact paths.
- Running `pitwall agents install` again no longer warns that Copilot plugin registration failed
  when the `pitwall-local` marketplace is already registered with Copilot.
- `pitwall agents profiles sync` covers routes that inherit their harness from the endpoint, and
  OpenCode status matches what its sync writes. Sync errors say "resolve to harness X".
- `grok-4.5` is no longer offered through the Grok CLI, which rejects it. The model itself is
  not retired; only its `grok` harness route is unregistered, and the routing skill cards no
  longer list it for that harness.
- A cherry-pick that git refuses (an empty commit, a bad sha) is recorded as conflicted, not applied,
  with git's first stderr line and the `git cherry-pick --skip` and `--abort` remedies. The open-ask
  limit holds under concurrent asks, and concurrent identical asks re-enter the same ask.
- Config parse and validation errors never echo file content or values. YAML, TOML, and settings
  validation errors name the file, line, and column (or the setting) and nothing else, across
  channel registration, doctor, seeding, the capability inventory, `pitwall config check`, policy
  documents, model dossiers, and local inventories; `load_settings_from_env` raises
  `ConfigFileError`. An invalid `[personal] backend` setting is named, not quoted, and the
  `registry.mode` doctor check names that setting when it is skipped.
- Rate-limit settings reject non-finite windows: `1/nan`, `1/inf`, and `1/1e999` are refused by
  `PITWALL_WEBHOOK_RATE_LIMIT` and `PITWALL_INBOUND_RATE_LIMIT`, and the token bucket refuses them.
- `pitwall doctor` runs its registry probes with the loaded settings, so database and Redis URLs set
  only in TOML are probed.
- `pitwall models fit --inventory` prints a table in human mode instead of nothing.
- Workbench: `reevaluate` refuses an output that is the source report, a symlink to it, or a hardlink
  to it, and writes through a private temp file and fsync. Child-session evidence counts only when its
  header names the expected child and parent. Hosted evaluation judges the last completed run of an
  allowed command and keeps earlier runs in the gate report. A terminal task record refuses late
  artifacts.
- Autopilot shadow mode counts the actions it would apply, so it reaches `max_actions_per_run` and
  `max_reserved_usd_per_run` where an apply run would, and signal and action snapshots are deeply
  immutable.
- Every container image applies the Debian security updates for OpenSSL and PCRE2 in its runtime
  stage (CVE-2026-75804, CVE-2026-84782, CVE-2026-103111), and `multidict` (transitive) is 6.9.1,
  past CVE-2026-104874.

### Security

- Streaming `POST /v1/messages` requests (`stream: true`) now run pre-spend payload inspection and
  budget admission before reaching a provider, and record a workload that settles as completed,
  failed, or cancelled when the stream ends or the client disconnects. They went straight to a paid
  provider with no inspection, admission, workload row, or cost record. Provider selection is now the
  planner's, so a provider without `supports_streaming` is skipped as on the OpenAI route.
- Upstream SSE error responses through the gateway are bounded and redacted: a status of 400 or more
  is answered as a bounded JSON error, not relayed as an event stream, and an oversized body gives
  502 `upstream_body_too_large`.
- Agent Routing profiles refuse credentials embedded in endpoint URLs: userinfo and query names
  ending in key, token, secret, password, `pwd`, or `passwd` are rejected, naming the field and never
  the URL.
- Policy violation evidence redacts credential keys written camel-case or without separators
  (`apiKey`, `accessKey`, `apikey`, `authtoken`, `clientsecret`) and a credential field whatever its
  value shape, while numbers, flags, and nulls (such as `max_tokens`) stay readable.
- `pitwall serve-model` reports failures as a fixed code and the exception class, never free-text
  detail. The catch-all handlers of `create-capability`, `seed`, `register-endpoint`,
  `set-provider-health`, and `init` do the same, and `pitwall doctor` reports a failed section as
  `the <section> checks did not run (<Class>)`; no exception text reaches the output. The `init`
  smoke command is shell-quoted.

### Migration

- `0041_config_audit_idempotency_key_index.sql` indexes the RunPod mutation journals' audit rows by
  idempotency key.
- `0042_config_audit_cli_reconciler_actors.sql` admits the `cli:lease` and `reconciler:activity`
  audit actors.
- `0043_idempotency_body_hash.sql` adds a nullable `body_hash` column to `pitwall.idempotency_keys`,
  used to refuse a reused idempotency key with a different request.

## [0.2.0a1] - 2026-09-30

The first unified release. Pitwall, Agent Routing, the gateway, and the Pi workbench are one
Python package with one `pyproject.toml`, one lock file, one version line, and `v*` tags only. This
is a clean break: commands, environment variables, paths, and the marketplace name change with no
aliases, and `pitwall agents migrate` moves an existing workstation across.

### Unification

#### Commands

| Old | New |
|---|---|
| `pitwall-agent-routing <cmd>` | `pitwall agents <cmd>` |
| `pitwall-agent-routing _shim <id>` | `pitwall agents _shim <harness>` |
| `pitwall-agent-routing routes ...` | `pitwall agents profiles ...` |
| `pitwall-agent-routing usage ...` | `pitwall usage ...` |
| `pitwall-agent-routing doctor` | `pitwall doctor` (one report with broker, agents, gateway, and workbench sections) |
| `node dist/src/shim.js`, `pitwall-gateway` | `pitwall gateway serve` |
| `pi-workbench ...` | `pitwall workbench ...` |
| `pitwall-mcp`, `pitwall mcp` and `pitwall mcp serve --transport stdio` (broker) | `pitwall mcp serve broker` |
| Agent Routing channel MCP server | `pitwall mcp serve channel` |

- `pitwall` is the only user-facing console script. The service scripts (`pitwall-api`,
  `pitwall-reconciler`, `pitwall-webhook`, `pitwall-cost-exporter`) stay because container images
  and compose files invoke them.
- `pitwall agents install` writes `~/.claude/scripts/<harness>-shim.sh` and `route-shim.sh` as
  generated two-line wrappers around `pitwall agents _shim`. The `SHIM-DONE exit=N` contract is
  unchanged. `plugins/claude`, `plugins/codex`, and `plugins/copilot` install from the package's
  data; plugin identity stays `pitwall:*` and all three manifests carry the package version.
- `pitwall agents migrate` performs the clean break for an existing workstation, idempotently: it
  writes `routes.json` and the Agent Routing settings into `pitwall.toml` (`[agents]` and
  `[agents.profiles]`), moves the run store, mailbox, receipts, and usage cache to
  `~/.local/state/pitwall/agents/`, rewrites installed shims and plugin registrations, reports
  environment variables still set under old names, and removes the old install last.
- `pitwall agents _shim`, `pitwall agents _steer-gate`, and the hook entry points import none of
  the broker's runtime dependencies (`fastapi`, `uvicorn`, `asyncpg`, `redis`, `arq`, `textual`,
  `runpod`, `mcp`, `prometheus_client`), so a workstation with no broker starts them fast.

#### Environment variables

- `SUBAGENT_MODEL_ROUTING_*` and `PITWALL_AGENT_ROUTING_*` become `PITWALL_AGENTS_*`;
  `SHIM_TIMEOUT_SECS` becomes `PITWALL_AGENTS_TIMEOUT_SECS`; `SUBAGENT_MODEL_ROUTING_ROUTES`
  becomes `PITWALL_AGENTS_PROFILES`; `SUBAGENT_MODEL_ROUTING_PROVIDER` becomes
  `PITWALL_AGENTS_HARNESS`.
- The agents-side `PITWALL_API_TOKEN` fallback is removed: the agents broker client reads
  `PITWALL_API_URL` and `PITWALL_AGENTS_API_TOKEN`. The broker API itself still reads
  `PITWALL_API_TOKEN`.
- Agent commands refuse to run while a legacy name is set and print the replacement for each.

#### Paths

- One config file, `pitwall.toml` (located by `PITWALL_CONFIG_FILE`), with an `[agents]` table and an
  `[agents.profiles]` table replacing `routes.json`.
- One state root, `~/.local/state/pitwall/`, with `agents/` holding the run store, mailbox, receipts,
  and usage cache.
- The Claude Code marketplace is `pitwall-local`; the `subagent-model-routing-local` marketplace
  name is gone.

#### Behaviour changes

- The steer gate fails closed: blocking steers are always enforced, and the block message gives
  recovery steps that need no tool call.
- Workflows run on all 13 registered harnesses, not six.
- `pi` and `grok` receive their prompts through a private file instead of argv.
- `kimi` and `dsh` refuse restricted mode (`PITWALL_AGENTS_UNRESTRICTED=0`).
- Money on the wire is a decimal string, never a float.
- `pitwall status` names the backend it reports, and the personal backend is explicit through
  `[personal] backend`.
- Providers declare their own routing, lockout, seed, reconciler, and OpenAI-proxy behaviour in their
  adapters; the drift and wave-2 feasibility modules are gone.
- The gateway is a Python service (`pitwall gateway serve`), and its catalog is re-pinned to
  omniroute 3.8.50 (3.8.51 was unpublished).
- The dispatch prerequisite check for GNU `timeout`/`gtimeout` is gone; the timeout is enforced in
  Python.

#### Removals

- The routing primitives with no production caller (`routing/canary.py`, `prewarm.py`,
  `failover.py`, `semantic_cache.py`, `carbon.py`, `cascade.py`, `hedging.py`,
  `quality_routing.py`, `arbitrage.py`), `routing/planner.py`, `cost/threshold_alerts.py`,
  `cost/slo_governor.py`, `finops/bidding.py`, `finops/time_machine.py`, `providers/drift.py`,
  `providers/_wave2_feasibility.py`, `rate_limits/store.py`, `gitops.apply_plan`,
  `workers/vllm.py`, `workers/header_policy.py`, `workload_lifecycle.enqueue_submit_runpod_job`
  and `insert_passthrough_workload`, and their tests.
- `src/pitwall/live.py` moved to `tests/`.
- Extra removals approved during the work: the `src/pitwall/cost_exporter/` re-export (the console
  script and Dockerfile run `pitwall.cost.exporter`); the orphaned planner types in
  `routing/types.py` and helpers in `routing/constraints.py`; the `rate_buckets` table, the
  `RateBucket` model, `TokenBucketRateLimiter`, and `RateBucketStoreProtocol`.
- The legacy names `SUBAGENT_MODEL_ROUTING_*`, `PITWALL_AGENT_ROUTING_*`, the agents-side
  `PITWALL_API_TOKEN` fallback,
  `pitwall-agent-routing`, `pitwall-gateway`, `pi-workbench`, `pitwall-mcp`, and the
  `subagent-model-routing-local` marketplace.
- Agent Routing's `bootstrap.sh`, `install.sh`, launcher, the 14 committed shim scripts, and
  `parse-shim-result.py`, replaced by generated shims and the CLI.
- The vendored OmniRoute `open-sse/` files and `UPSTREAM.lock`, and the `packages/gateway` npm
  project. `packages/pi-workbench` is folded into the package as `pitwall workbench`.
- Smoke and benchmark tools with no callers, `tools/guards/forbidden_imports.py` and its hook, the
  `gateway-ci`, `gateway-release`, `agent-routing-ci`, and `agent-routing-release` workflows (their
  jobs moved into `ci.yml` and `release.yml`).

#### Package metadata

- The description, keywords, and URLs describe the one product. The licence stays Apache-2.0.
  Agent Routing's MIT-licensed code is attributed in `NOTICE`; the vendored OmniRoute code and its
  MIT notice left with the gateway port.

### Added

- Alibaba Cloud Model Studio provider (`model_studio`): a streaming OpenAI-compatible adapter
  for Token Plan and pay-as-you-go keys, seeded with catalog-derived base URL and pricing, a
  Credits quota window (`subscription-credits`) reconciled from OpenAPI stats or the configured
  tier, pay-as-you-go month-to-date spend, and proxy support through `openai_base_url`.
- Runtime budget limits: a single audited database row overrides the environment monthly
  budget and per-request cap without a restart, with read/change surfaces in
  `pitwall budget show|set`, `GET/PUT /v1/admin/budget`, and the MCP
  `pitwall_budget_status` / `pitwall_budget_set` tools. Every change requires a reason and is
  recorded in `config_audit`; routing, reconciliation, burn rate, audit, and the breach kill
  switch all read the effective limits at use time.
- `pitwall mcp relay -- CMD…`: a stdio relay that keeps MCP harnesses connected across broker
  restarts by restarting the server command, replaying the initialize handshake, and answering
  in-flight requests with a retryable `mcp_server_restarted` error
  (`PITWALL_MCP_RELAY_WAIT_SECONDS` bounds the readiness wait, default 30).
- Budget refusals now explain themselves across the MCP boundary: `budget_rejected`,
  `sub_budget_rejected`, and `budget_exhausted` payloads carry the server-computed `reason`,
  spend `snapshot`, and a `remedy`, and raw-pod previews report the `budget` verdict the apply
  will get (`admitted`, `reason`, `snapshot`, `estimate_basis`).
- The raw-pod view (`pitwall_runpod_get_pod`, `pitwall_runpod_list_pods`,
  `/v1/admin/runpod/pods…`) carries the pod's `public_ip` and `port_mappings` so an agent can
  reach its own pod.

- Personal serving (`pitwall serve`) enforces a local monthly budget and keeps an audit log:
  it refuses without `PITWALL_MONTHLY_BUDGET_USD` (`budget_not_configured`), above
  `PITWALL_PER_REQUEST_MAX_USD` (`per_request_cap`), or past the month's settled spend plus
  running leases' maximum spend (`monthly_budget`), and appends every serve, refusal, stop, and
  failure to an owner-only `audit.jsonl` beside a per-month `ledger.json`.
- The reconciler reaps workloads left `queued`/`running` with no provider job id and no lease an
  hour past their capability's execution timeout, closing them `timed_out` at their admitted
  ceiling (`reaped_unfinished`) so the daily rollup agrees with the budget gate.
- `docs/operator/live-drills.md` documents the `tools/` drill scripts, now named `.py`/`.sh`.
- `examples/desk-meter`: firmware for an ESP32 touch display that shows subscription usage from
  `pitwall usage serve`. It is an example and is not built in CI.

### Changed

- Anonymous `/v1/inference` requests share one upstream call only when the answer cannot differ:
  embedding and rerank by content; LLM, vision, and transcription only at `temperature: 0` with
  one choice. Idempotency-keyed requests are unchanged.
- A serve provider whose lease closed is `disarmed`, not `unhealthy`: routing still skips it, but
  doctor and `pitwall_providers_unhealthy` no longer count it as failing.
- Retention purges whole UTC days, deletes archived objects only after the database commits
  (failed deletions are recorded in `commit.json` and retried), and the daily rollup never
  recomputes a day a purge has reached.
- `pitwall.core.inference` and `pitwall.core.jobs` (no production caller) are removed, along
  with the `pitwall.core.transition_workload` re-export.
- Per-token pricing supports cached-input rates and context-length tiers, and usage estimates
  cap at the model's catalogued output ceiling.
- The lockout table honours explicit reset times instead of assuming calendar windows, and the
  generic 429 classifier no longer treats a bare "quota" message as a monthly lockout (a
  per-minute quota message now cools down 60 seconds).
- `GET /v1/jobs/{workload_id}` and `GET /v1/jobs/{workload_id}/result` show a job's `input` and
  `result` only to a token with the `spend` scope. A `read`-only token gets the job's metadata, a
  null `input` and `result`, and from `/result` `available: false` with `unavailable_reason:
  insufficient_scope`.
- `/health` and `/healthz` answer `{"ok": true}`; the hardcoded `"backend": "runpod"` field is
  gone, and the API starts without `RUNPOD_API_KEY`.
- The webhook receiver refuses to start without `PITWALL_WEBHOOK_SECRET` instead of accepting
  unsigned deliveries, and the cost exporter refuses to start without `PITWALL_MONTHLY_BUDGET_USD`
  instead of assuming a $1000 budget.
- The budget gate counts a month as a UTC month whatever time zone the database session uses.
- A provider's `openai_base_url` over HTTPS is refused when it names a private, loopback,
  link-local, or metadata address, unless the provider is explicitly local.
- The MCP cost tools reject a datetime without a time zone instead of reading it as local time.
  The descriptions of the admin MCP tools no longer claim an "admin-only" enforcement the server
  does not have: any local process that can start the stdio server has full access.

### Fixed

- Every month-to-date reader (the 80% alert, threshold alerts, the cost exporter, the TUI, and
  chargeback) uses the budget gate's definition, including admitted ceilings and raw-pod spend;
  the exporter and the 80% alert use the runtime budget limit.
- The 80% budget alert could never send: its Redis dedupe check was not awaited. Alert failures
  after the daily rollup are now logged instead of swallowed.
- The kill switch writes its `kill_log` latch before any destructive step, so admission closes
  first and stays closed if activation fails part-way.
- Probe and proxy health writes are compare-and-set, so overlapping writers no longer lose a
  failure count.
- A request that lost an idempotency race returned the winner's workload unchecked; it is now
  validated like a replay (payload, capability, provider, webhook), and provisioning replays
  refuse a different request (`ProvisionReplayConflict`).
- Doctor reads the gate's month-to-date spend for the budget check and reports burn-rate read
  failures, and the daily rollup counts raw-pod spend.
- The gateway no longer relays upstream error bodies byte for byte; credentials echoed by a
  provider are redacted.
- `.env.example` lists `POSTGRES_PASSWORD` and `REDIS_PASSWORD`, which `docker-compose.yml`
  requires; `SECURITY.md` states the support status of the alpha and component tags.
- Installed shims exit 127 with `pitwall: command not found` when `pitwall` is missing, on every
  platform; previously macOS could exit 126 when the command search met a non-executable entry.
  Reinstalling (or `pitwall agents migrate`) rewrites existing shims.
- `pitwall gateway serve` answers `--help` and rejects unknown flags (exit 2) before reading its
  configuration; `pitwall workbench` and each workbench subcommand answer `-h`/`--help`.
- `pitwall agents migrate` treats dispatch records whose runner is gone as abandoned instead of
  refusing forever, adopts the channel registration the old install wrote, removes the legacy
  `model-routing` alias, and reports moves per directory.
- `pyjwt` (transitive, via `mcp` and `redis`) is 2.15.1, past the vulnerabilities fixed in 2.14.0.
- `urllib3` (transitive, via `requests`, `botocore`, `runpod`, and `sentry-sdk`) is 2.8.0, past
  CVE-2026-97687 and CVE-2026-97689.
- A managed dispatch no longer refuses a healthy child channel on a busy machine: the handshake
  probe waits up to 30 s (was 2 s) and still refuses a wrong or hung executable.
- A dispatch's event log records `steer.sent` before the steer reaches the running agent, so an
  acknowledgement can no longer appear before its send; a publish that does not happen logs
  `steer.failed`.
- The pre-spend payload scan's 50 ms limit counts the scan's own CPU time, so a busy or
  descheduled machine no longer refuses valid input (e.g. `pitwall init`); a scan that burns CPU
  still hits the limit. It no longer charges garbage-collector time on the scanning thread to the
  scan, so a full collection on a large heap cannot block even a two-field payload with
  `limit_reason: timeout`.
- `pitwall agents migrate` removes the old install's Claude Code `pitwall` marketplace from every
  scope. It ran `claude plugin marketplace remove --scope user pitwall`, which fails for a
  marketplace declared in `known_marketplaces.json` yet exits 0, so the migration reported the
  removal while the old marketplace stayed registered.
- `pitwall agents migrate` keeps each moved directory's modification time. It moved state file by
  file into new directories, so every migrated run looked new and `pitwall agents runs cleanup
  --older-than` removed nothing.
- Webhook delivery retries work: a failed delivery records `next_retry_at` and the reconciler
  redelivers due retries, an egress rejection is not retried, every resolved address is tried, and
  `PitwallWebhookRetriesDue` can fire. The receiver marks a delivery seen only after it is queued.
- Every job name the reconciler and webhook receiver enqueue is one a worker registers; a
  terminal-status path used an unregistered name. The lease-expiry sweep continues past a
  teardown that fails, and the budget breaker keeps its cooldown between jobs.
- The backup and retention jobs run in the reconciler image: it ships `pg_dump` and `pg_restore`,
  the backup drill no longer blocks the worker's event loop, the default `archive-purge` retention
  succeeds (rows with object-storage keys are skipped when no adapter is configured, and the
  archive directory is owned by the service user), and the healthcheck no longer targets PID 1.
- An unauthenticated request with a large body is refused before the body is buffered, idle
  inbound rate-limit buckets are evicted, and a lease teardown that completed is no longer reported
  as failed when its audit write or provider disarm errors.
- The OpenAI proxy path (`/v1/openai/<capability>/v1/*`) fails over to the next provider on a 429
  and records a lockout, as the adapter path does.
- A serve that fails after its pod launches (lease not saved, no proxy URL, warm-cache update,
  cancellation, or a verification error) tears the pod down instead of leaking a paid pod.
- RunPod calls: `POST /run` is not retried after a read timeout or a 5xx, every RunPod client uses
  one retry policy and resolves the key the same way (including a key saved by `runpodctl`), a slow
  pod create is audited with its pod id instead of leaving an unaudited pod, and RunPod `infer`
  refuses a request that is not an embedding instead of mis-sending it.
- Model Studio availability respects the automation gate and honours `Retry-After`, and a
  malformed or error stream chunk is a typed Model Studio error.
- A lease renewal made through the CLI or MCP publishes the renewal event the API publishes. `pitwall
  cost` commands print a fixed message on failure, never raw exception text, and the per-request
  rejection snapshot reports real month-to-date spend. Concurrent personal-backend updates keep both
  records.
- An invalid `PITWALL_AGENTS_TIMEOUT_SECS` (`abc`, `0`, `-5`, `nan`) exits 64 with `SHIM-DONE
  exit=64` instead of becoming a 1 ms timeout. Muse accepts `-m`, secrets passed as positional
  arguments are redacted from recorded arguments, and a harness installer with no pinned SHA-256 is
  refused.
- `pitwall gateway serve` no longer cuts a healthy event stream at 30 seconds: an idle stream or a
  slow first byte ends with an error event and cancels the upstream, as does a client disconnect.
  Bearer tokens are compared in constant time, the rate limit also covers `/v1/models` and
  telemetry, the 413 message reports the configured cap, and a malformed `Host` header is not a
  server error.
- Policy YAML files are read with a real YAML parser instead of a hand-written one that mis-read
  valid syntax.

### Migration

- `0035_model_studio.sql` adds the `model_studio` provider type, adapter id, and quota-window
  fields for Token Plan Credits.
- `0036_budget_limits.sql` adds the singleton `pitwall.budget_limits` row and admits the
  `budget_limits` audit entity type, the `budget_limits.set` action, and the
  `api:admin`/`cli`/`test` audit actors.
- `0037_workloads_submitted_at_index.sql` replaces the partial `idx_workloads_month_spend`,
  which the gate's month-to-date query could not use, with `idx_workloads_submitted_at`.
- `0038_disarmed_provider_health.sql` reclassifies serve providers a closed lease left
  `unhealthy` as `disarmed`.
- `0039_money_column_constraints.sql` adds `leases_cost_accrued_nonnegative` and widens
  `volumes.monthly_cost_usd` to `NUMERIC(12,6)`.
- `0040_drop_rate_buckets.sql` drops the unused `pitwall.rate_buckets` table.

## [0.1.0a3] - 2026-09-25

### Added

- `pitwall gateway sync --apply-verdicts DOSSIER [--seed FILE]` disables the pools a free-pool
  benchmark dossier marks `kill`; the dossier named this command, but only the repository tool
  accepted it.
- The reconciler runs the budget-breach kill escalation every minute. `PITWALL_BUDGET_BREACH_KILL_MODE=armed`
  now fires the kill switch once per month when the monthly budget is exhausted; `shadow` logs
  what would fire. The default, `disabled`, is unchanged.
- `packages/pi-workbench`: an independently installable TypeScript package that runs upstream Pi
  as an interactive harness with exact model profiles, isolated state, a native subagent
  backend, planning and restricted modes, and redacted accounting (SDLC 25).
- Release-acceptance tooling under `tools/release_acceptance` (surface discovery, reviewed
  bindings, test index, matrix assembly, evidence recording) and its records in
  `release_acceptance/`.
- The free-tier gateway routes each provider to its own upstream through a generated route
  table (`config/gateway-routes.json`); the broker names the route in `x-pitwall-route`.

- `pitwall doctor` prints an installation readiness report (install, config, services, and
  spend controls), in personal or registry mode, with `--strict`, `--json`, and an opt-in
  `--canary` dry-run inference; the same report is exposed as the `pitwall_doctor` MCP tool.
- `pitwall mcp install`/`pitwall mcp uninstall` register or remove the Pitwall MCP server
  with Claude Code, Codex, and OpenCode, forwarding `RUNPOD_API_KEY`, `DATABASE_URL`,
  `REDIS_URL`, and `PITWALL_CONFIG_FILE` by reference in each harness's own syntax.
- `docs/agents/`: a public install guide for coding agents, covering prerequisites, the
  personal and registry paths, MCP registration, and the full `pitwall doctor` check
  catalogue.
- Personal-first serving: `pitwall setup`, `serve`, `status`, `stop` work with only a RunPod
  credential, keep state in `$XDG_STATE_HOME/pitwall`, protect the endpoint with a generated key,
  and give every pod an in-pod deadline; the console gains Serve, Pods, and Routes views.
- Provider-neutral runtime contracts, static adapter registry, and credential references
  (migrations `0028` and `0030`), with structured Decimal cost quotes, ceilings, and
  reconciliation provenance (migration `0029`) and persisted production route plans
  (migration `0031`).
- RunPod catalogue, balance, billing, Pod/endpoint/template/volume/registry administration,
  bounded volume-object and Pod-log operations with a durable mutation journal, and a
  resumable plan/apply/status/resume/rollback onboarding workflow across REST, MCP, CLI, and TUI.
- Vast.ai and Lambda Cloud compute adapters, a Together sync-inference adapter, and exact
  OpenAI fallback chains behind deterministic routing with async job lifecycle and result paging.
- Decimal burn-rate forecasts with atomic owner-token alert reservations, and bounded pre-spend
  guardrail status and preview surfaces.
- REST operations and MCP tools for these features (the API now serves 102 operations and the
  MCP server 79 tools), and the `cost`, `burn-rate`, `guardrails`, `routing`, `runpod`,
  `runpod-onboard`, `provider-ops`, and `volume-files` CLI groups.
- Migration `0032` rewrites route-plan cost quotes persisted before the canonical-shape fix.
- Added opt-in self-hosted live evidence and hermetic J25 probe, warm, proxy, eviction, and
  re-warm coverage; the README testing-command journey is now J27.
- Capability-only CLI, REST, and MCP serving can warm self-hosted models without lease ownership;
  results identify `provider_kind: self_hosted`, and nullable `lease.ready` events preserve the
  routing-consumer readiness signal.
- Local GPU inventories can now evaluate catalogue variants with KV-cache headroom,
  architecture compatibility, tensor-parallel fit, and non-NVLink warnings.
- Documented the self-hosted endpoint capability class, readiness tradeoffs, tool-calling launch
  requirements, runaway-consumer mitigations, per-unit support boundaries, and the opt-in live
  verification contract.
- Serve-model automation adds activity renewal, idle teardown, hourly price caps,
  signed lease lifecycle webhooks, capability/TUI/CLI visibility, optional route
  registration, and hermetic J24 renewal-stop-revival coverage.
- Generated-template image references must be credential-free Docker references; `serve-model` rejects token-shaped start arguments, and template diagnostics are redacted before logging.
- Added an operator quickstart for the catalogue-to-live `serve-model` flow,
  including routing hookup, endpoint verification, paid-step boundaries, and stop.
- Added keyboard-only `:` command palette and `/` transient list search to the Textual console.
- Added database-free `serve-model --plan-only` catalogue previews with launch argv, image,
  volume-cache environment, hardware fit, startup timeout, estimate, and price provenance.
- Verified `warm-volume` runs now persist variant- and volume-specific warm-cache state, exposed as warm/cold fit guidance.
- Added `models evidence` for schema-validated, body-preserving recording of measured variant
  observations, and sourced GGUF companion metadata for Gemma, Qwen, and Muse dossiers.
- Added optional `PITWALL_PRICE_MAX_AGE_S` GPU-price freshness policy, with fit/preview/console status and pre-create stale-price refusal for non-dry serve launches.
- Added the additive `tight` hardware-fit verdict for single-GPU options with positive headroom below 10% of aggregate VRAM.
- J23 is now a hermetic composed serve-model journey that verifies dry-run, fake pod readiness,
  OpenAI chat proxying, and teardown disarm without a database or RunPod credentials.
- Added a pinned engine-image launch-shape smoke gate with vLLM/SGLang parser checks, llama.cpp catalogue parsing, and a llama.cpp CPU OpenAI-compatible HTTP cycle.
- Landed the dated 2026-08-27 serve-model research corpus under `docs/research/`.
- SGLang joins vLLM and llama.cpp as a serve-model engine, with `/health_generate` readiness and shared CLI, REST, and MCP enum support.
- Catalogue variants can declare strict, source-authored mmproj, MTP, and draft companions; `fit_options` exposes their inclusive disk allocation while the console identifies companion-bearing and non-chat rows.
- Catalogue variants can attach measured or research evidence with canonical GPU, observed VRAM/startup, and date metadata; the console labels the evidence kind beside confidence.
- Console lease/provider tables expose served model, engine, variant, and armed pod facts without adding mutation paths.
- The Textual model catalogue now launches a selected hardware row only after a mandatory redacted dry-run preview and exact capability-name type-to-confirm modal.
- The read-only Textual console adds model catalogue and dossier views plus an advisory hardware-fit table with live, stale, and unpriced GPU-cost states.
- A packaged dossier-backed model catalogue now supports `models list`, `models show`, and `models fit`, including live/fallback RunPod GPU fit and TTL cost views.
- Packaged model catalogue: `pitwall.models` loads validated dossiers (YAML front matter, one `variants:` entry per quant/format) from `docs/models/`, shipped in the wheel as `pitwall/models/data/`; `PITWALL_MODELS_DIR` overrides the directory. `pyyaml` becomes a runtime dependency; the `pitwall.models` public re-exports are unchanged.
- `GPU_VRAM_GB` exposes the RunPod gpuTypes snapshot 2026-08-27 VRAM values, including the newly catalogued GPU classes.
- `pod_lease` providers can front the OpenAI proxy: the lease readiness hook arms the provider with the live pod's id/port and teardown disarms it, so `/v1/openai/<capability>/v1/*` follows the lease and returns 503 after it dies.
- Capability reads expose `served_model_id` and `active_lease: {lease_id, state, expires_at}`; `PATCH /v1/admin/capabilities/{id}` accepts `served_model_id` (migration 0022).
- Consumer-contract tests pin subagent-model-routing metadata and proxy compatibility, and `serve-model --ttl` is an alias of `--ttl-minutes`.
- `serve-model`, `POST /v1/serve`, and `pitwall_serve_model` launch or replay vLLM/llama.cpp pod leases with catalogue variants, caller-supplied templates, bounded served-model verification, dry-run cost output, and launch-only gated-model credentials.

### Changed

- API errors from RunPod resources, onboarding, webhook subscriptions, and route-class
  validation are top-level `{"error", "detail"}` bodies, like every other route.
- `POST /v1/leases` resolves the capability by name, then id, and returns 404
  `capability_not_found` for an unknown one; REST and MCP lease creation share one planned path.
- A failed lease teardown returns 502 `teardown_failed` and leaves the lease `stopping`; the
  reconciler retries it each minute, and only one teardown of a lease runs at a time.
- Job cancel returns 409 `job_not_cancellable` or 502 `job_cancel_failed`.
- MCP tools refuse undeclared arguments as `invalid_tool_arguments`, and report `unknown_model`
  and `unknown_variant` as typed codes.
- `POST /v1/inference` refuses misspelled control fields (`dryRun`, `dry-run`, `idempotencyKey`, …)
  with 422 instead of passing them to the capability.
- Route plans include `provider_constraint` and `escape_hatch`; the planner no longer offers
  non-`pod_lease` RunPod providers for compute (`provider_type_unsupported`).
- `pitwall-api`, `pitwall-mcp`, `pitwall-reconciler`, `pitwall-webhook`, and
  `pitwall-cost-exporter` answer `--help` without configuration and refuse unknown arguments
  (exit 2) before any service code loads.
- Services and the reconciler refuse to start (exit 78, naming the variable, never its value) on
  an invalid value of a key they read directly, including `PITWALL_INBOUND_RATE_LIMIT` and
  `PITWALL_LEASE_ADVANCE_WARNING_MIN`; enabled retention requires its archive settings.
- RunPod pod reads are strict: an unreachable RunPod is never read as an absent pod by lease
  teardown, the reconciler, personal `status`, or the CLI.
- A personal `serve` interrupted after its pod exists records the failure `interrupted`.
- `serve` (personal and registry) refuses a TTL at or within the model's startup budget
  (`ttl_below_startup`) before any pod exists: the deadline would end the pod before the model
  answered, as a live 15-minute serve of a 30-minute-startup model did.
- `pitwall stop` for an unknown route prints `refused: unknown_route`; lease commands report the
  stable lifecycle codes.
- `.env.example` names the Hugging Face token `PITWALL_HF_TOKEN`.
- The console script is `pitwall`; `serve-model` is `serve`; bare `pitwall` opens the console;
  container images are `pitwall/<service>`; the plugin family is `pitwall`, `pitwall-codex`,
  `pitwall-copilot` with agent types `pitwall:<shim>` and one script `pitwall-agent-routing`.
- Per-token admission ceilings now use the larger of the estimated token count and the UTF-8
  byte count of the input, and energy-priced capabilities are admitted at their full
  execution-timeout ceiling. Both raise the conservative ceiling used by `per_request_max_usd`.
- The Cost screen renders against the configured monthly budget setting (default `50.0`)
  instead of requiring `PITWALL_MONTHLY_BUDGET_USD`; a zero budget shows an immediate projected
  breach.
- The routing summary's capacity figure is labelled `capacity dropped` and counts candidates
  eliminated for capacity.
- Notification delivery failures report `notification_delivery_failed` and log only the
  exception class, so no transport detail can leak.
- The Providers screen availability hotkey moved from `a` to `v`; `a` is the global Operations
  key again.
- Volume-file uploads are inspected by an inspector sized to the transfer limit; those
  inspections no longer count toward the process-wide guardrail status counters.

- The broker support floor is now Python 3.14 (`>=3.14,<3.15`), with 3.14.7 pinned across local
  development, CI, and the five service images; Python 3.12 and 3.13 are no longer supported.
- Generated RunPod templates are cached by image, start command, ports, and sorted non-secret environment key names instead of image identity alone.
- `warm-volume` now warms a network volume by running the catalogue's real serve launch through readiness and `/v1/models`, then immediately tearing the lease down.
- The console uses responsive Models and hardware-fit tables at 100 columns and provides binding-derived `?` help overlays.
- CLI and MCP model-serving help now explains catalogue selection, canonical GPUs, custom-image launches, unpriced rates, dry runs, and stable errors.
- Canonical GPU names follow the live RunPod catalog; five legacy names are accepted as aliases.
- Proxied requests served by a pod lease are recorded at $0 in the workload ledger (the lease carries the spend).
- Pod-lease launches now reserve `rate_per_second × lease TTL` against the monthly budget (previously `rate × execution_timeout_ms`, ~60 s), so pre-spend admission matches what the lease accrues.
- Bare `pitwall-gpu-broker mcp` (no subcommand) is now an argparse usage error (exit 2) instead of silently starting the MCP server; use `pitwall-gpu-broker mcp serve` or the `pitwall-mcp` entry point.

### Fixed

- The `redis` floor is 5.0.1, the first release with the async `aclose()` Pitwall calls; an
  install that resolved 5.0.0 failed when a service closed its Redis client.
- Pi Workbench restricted mode refuses before launch, and `doctor` reports
  `setpriv-lacks-seccomp-filter`, when `/usr/bin/setpriv` predates util-linux 2.41. Every tool call
  used to fail at runtime with `unrecognized option '--seccomp-filter'` (Ubuntu 24.04 ships 2.39).
- CI: the hermetic jobs install the pinned TypeScript compiler the node surface inventory parses
  with, and the Pi Workbench job runs on Ubuntu 26.04, whose `setpriv` supports seccomp filters.
- `PITWALL_ROUTING_CLI` is read from the environment, as the serve quickstart and SDLC 16
  document. It was never read, so an override was silently ignored and the routing CLI on `PATH`
  ran instead.
- Personal serve refuses `cuda_unavailable` before launch when a GPU class is offered only with
  CUDA versions RunPod's pod-create API cannot request (above 13.0). It launched and failed as
  `create_failed`.
- Personal serve prints the pod's last 40 log lines (redacted) when the model never becomes ready
  (`readiness_timeout`, `container_restarting`, `pod_gone`). It reads them before terminating the
  pod, because afterwards they are gone. The console shows them as text, not markup; a log line
  with `[/]` crashed its serve screen.
- `pitwall leases stop`, the MCP stop tool, and `serve`'s warm-complete and model-mismatch
  teardowns publish `lease.terminated` when `REDIS_URL` is set; only the REST stop did.
- A Redis outage no longer fails a lease stop that already terminated the pod and closed the lease;
  the lost `lease.terminated` announcement is logged instead.
- The free-pool benchmark sends a unique prompt per request. Pollinations answered the repeated
  `ping` from its cache without a key, so pools that can no longer generate read as `keep`.
- `pitwall warm-volume` asks for a lease five minutes longer than the model's startup budget. It
  asked for exactly the budget, which serve now refuses as `ttl_below_startup`.
- The free-pool benchmark names the route and sends the gateway token for loopback pools, and
  probes every pool at once, so `--minutes` is the length of the whole run.
- Agent Routing's route probe and endpoint discovery send a `pitwall-agent-routing/1` user agent.
  RunPod's proxy rejects urllib's default agent with 403, so every RunPod route probed as
  `unauthorized` and personal serve failed with `route_attach_failed`.
- Personal serve's readiness probe now sends the endpoint key. The pod's vLLM requires it, so
  every unauthenticated probe got 401 and a healthy model ended in `readiness_timeout`.
- A personal `serve` whose pod create RunPod rejects (no capacity, a RunPod 500) is refused as
  `create_failed` instead of ending in a traceback.
- The restore drill computes row checksums on the server, so tables holding `jsonb` no longer
  fail with a false content mismatch when the drill runs from the application pool.
- Inbound rate-limit windows written with plural units (`hours`, `minutes`, `mins`, `secs`,
  `hrs`) were rejected; the API and `pitwall config check` share one parser.
- `pitwall-webhook --help` started the server.
- Volume-file object tools report a typed error when object storage is not configured; pod logs
  need only the RunPod key.
- The in-pod deadline calls the RunPod v2 pod action endpoint and logs its outcome to the
  container's stderr; previously it used endpoints outside the v2 contract and discarded errors.
- Personal serving waits for a model across the dossier's startup budget instead of one
  60-second window, so real models are no longer terminated as `readiness_timeout` mid-load.
- A crash-looping container fails fast on both serve paths after two restarts, with its own log
  tail, instead of billing out the startup budget.
- Both serve paths allow every CUDA driver version at or above a variant's floor (RunPod treats
  `allowedCudaVersions` as an exact set) and refuse before launch when none is offered; ten
  dossiers with a quoted `min_cuda` are repaired and the value is validated at load.
- The OpenAI proxy and streaming `/v1/messages` authenticate to the loopback gateway with its
  token and route header; they previously sent the pool key, or nothing, to the gateway.
- The release-acceptance run recorder no longer loses child output flushed before SIGINT.
- CI: every job that syncs the root project provisions Python 3.14.7; the fuzz lane declares
  `starlette-testclient`; the external link check resolves this repository's own URLs locally.
- The gateway supervisor resolves its program without reading the caller's working directory,
  and its tests no longer depend on an installed launcher.

- Route-plan admission quotes are persisted in the canonical `CostQuote` shape, so the cost
  workload reads on REST, MCP, and CLI no longer fail after a production-routed workload.
- The capability audit, budget gate, and Operations screen share one month-to-date spend
  expression that prefers actual cost, then ceiling, then estimate.
- Volume-file lookups of a missing key that prefixes other keys return not-found instead of a
  provider error; range reads past end of file return an empty chunk and missing keys return
  404 against real S3; unconfigured S3 credentials return the documented 503 on REST.
- Text uploads between 256 KiB and the 4 MiB transfer limit are accepted, and inspection runs
  off the event loop.
- Non-overwrite downloads publish on filesystems without hard links; log parsing keeps
  present-but-empty values.
- TUI: a second volume-file action keeps its own cancellation; a cancelled onboarding task no
  longer overwrites a newer task's output; the Providers screen shows the persisted armed state
  and names the error class on refresh failure; the Resources screen names failing sections and
  labels retained rows with their original refresh time; an unknown capability maps to
  `route_not_found`; the guardrail preview keeps its input when validation fails; limited
  decisions without a reason render `limit unspecified`; leases keep the pod id visible and
  searchable.
- The MCP recent-workloads tool validates `limit` before acquiring the pool; the market-backed
  GPU price snapshot is cached for CLI and TUI callers.
- The staging-store import isolation test restores every `boto3`/`botocore` module, so the
  hermetic suite no longer depends on test order.

- Integration-fixture teardown now restores the migration ledger for its raw schema rebuild,
  so a subsequent `db migrate` does not attempt to recreate existing relations.
- Engine launch-shape parser probes now use real parser-only entry points, enforce bogus-flag
  negative controls, distinguish substitute images, and pin the llama.cpp CPU image by digest.
- Backup/restore drills now reject PostgreSQL client/server major-version skew before dumping,
  with an actionable matching-client installation message.
- Hermetic tests now neutralize exported request-authentication, middleware, and budget-breach kill-switch settings; `warm-volume` reports its resolved GPU class, count, and default-selection price source.
- Catalogue launches now use dossier `served_model_name` consistently for launch, verification, replay, and returned model IDs.
- Capability lists fetch active leases in one batched query.
- `warm-volume` resolves its promised cheapest single-GPU fit before dry-run planning and reports no-fit errors without selecting an arbitrary GPU.
- Omitting `serve-model --engine` now preserves the catalogue variant's resolved engine, including llama.cpp GGUF variants.
- RunPod `gpuTypes` parsing now normalizes null list fields, preserving live GPU pricing instead of silently falling back.
- `serve-model --dry-run` no longer creates RunPod templates; image-based plans report a cached template ID or `dry-run` without credentials.
- `serve-model` validated the GPU class after creating the capability; junk names could be persisted and the fuzz lane could hit a 500.
- `pitwall-gpu-broker mcp serve` failed with `unrecognized arguments: serve`; `serve` is now a real subcommand and the documented form works.
- `pitwall-gpu-broker db --help` (also `-h`, `help`) reported an unknown command; it now prints usage to stdout and exits 0.
- `pitwall-gpu-broker db <command> --help` (also `-h`, `help`) executed the command instead of printing usage; help tokens now print usage to stdout and exit 0 without running anything.
- `pitwall-gpu-broker mcp serve --json` lost its JSON output when stdout was a pipe; stdout is now flushed before the MCP stdio transport takes over the stream.

### Deprecated



### Removed

- The unused TUI confirmation tier table (`pitwall.tui.confirmation`); each console action keeps its
  own typed confirmation.
- The unread settings `PITWALL_TAILSCALE_IP`, `PITWALL_WEBHOOK_PUBLIC_URL`, and
  `PITWALL_GATEWAY_COMPRESSION`, with `config check`'s warning about pairing the first two.
- Thirteen unused nested configuration models whose every field duplicated a live
  `PitwallSettings` field.
- `pitwall-gpu-broker` and `model-routing` command names.
- The `warm-volume` marker script and its separate worker-image path.
- Unused direct `click` dependency declaration; `click` remains installed transitively via `arq`.

### Security

- Locked dependencies moved past published advisories: aiohttp 3.14.1 -> 3.14.3 (PYSEC-2026-3545/3546/3547) and cryptography 49.0.0 -> 50.0.1 (PYSEC-2026-3552; the runtime pin is now `cryptography>=45,<51`). `make sec` passes end to end.


## [0.1.0a2] - 2026-07-18

First public-alpha candidate: secure-by-default interfaces, scoped API
authorization, atomic lease mutations, hardened signed webhooks, installed
migrations, encrypted retention, five non-root service images, and a gated
artifact-first release workflow. The GHCR path is normalized for Docker's
lowercase repository-name requirement. The in-repository GPU worker is deferred.

## [0.1.0a1] - 2026-07-18

Unpublished candidate, superseded before publication by 0.1.0a2.

## History before unification

Before 0.2.0a1 Agent Routing and the gateway kept their own changelogs and version lines. They are
preserved here as they stood at unification.

### Agent Routing (last version `agent-routing/v0.12.0`)

All notable changes to this project will be documented in this file.

The project adheres to **semantic versioning intent** with the following public contract. The items below are considered the stable public API and will only change in a **MAJOR** release:

- The `SHIM-DONE` sentinel format emitted by the transport shims.
- The opt-in `SHIM-RESULT` receipt format and its position immediately before the final `SHIM-DONE`.
- The shim environment variable names: `PITWALL_AGENTS_TIMEOUT_SECS`, `SHIM_RESULT`, `PITWALL_AGENTS_UNRESTRICTED`, and `PITWALL_AGENTS_LEDGER`.
- The namespaced agent types used for routing.
- The versioned workflow JSON schema and persisted task-state names.

New capabilities bump the **MINOR** version; fixes bump the **PATCH** version.

#### [Unreleased]

##### Added

- Model facts: `model-facts/` records what vendors, harnesses, hosts, and Hugging Face files state
  about each model family, with a source for every fact and statement. `tools/agents/model_sources.py`
  fetches and checks the sources; `tools/agents/sync_model_facts.py` generates the registry's model
  fields, marked blocks in the routing skills and ledger cards, and a facts sheet per unit; and
  `tools/agents/validate_model_facts.py` checks them. `/pitwall:model-facts <family>` reviews changed
  sources, and a weekly workflow reports changes in one pull request.
- `pitwall-agent-routing usage [--json]`: one row per subscription plan and account, with
  percent used per window, reset time, and status. Claude, Codex, GLM, MiniMax, and Model
  Studio are read; every read is one GET with the credential already on the machine, and
  nothing refreshes a token.
- Route entries accept `account`, and `routes add` accepts `--account`. A route whose `env`
  sets `CLAUDE_CONFIG_DIR` or `CODEX_HOME` declares a second account of that plan.
- The routing skills read usage before choosing a route and keep a 10 percent reserve.
- The Claude Code host boundary lets a saved Claude route through when it sets
  `CLAUDE_CONFIG_DIR`.
- The `model-studio` endpoint kind for Alibaba Cloud Model Studio: `routes
  add-model-studio-endpoint` creates an endpoint whose base URL, per-route limits, and effort
  vocabulary derive from a committed catalog, and saved endpoints round-trip with the derived
  base URL persisted.
- OpenCode and Pi route sync for `model-studio` endpoints; `routes add` without `--harness`
  pins Model Studio routes to `defaults.endpointHarness`.
- A Token Plan automation gate: headless dispatch through a Token Plan endpoint is refused
  until the endpoint records `tokenPlanAutomation: "accept"` (or the environment accepts for
  the installation); interactive sessions are not gated.
- Per-endpoint concurrency slots (`flock`-based, `endpoint_busy` exits 75) and a local
  Token Plan exhaustion lockout that lifts at the recorded renewal date.
- Model Studio readiness: `routes probe` reports `misconfigured` for key/plan/URL refusals and
  `quota-exhausted` with the renewal date, using an ACS3-signed OpenAPI subscription-stats read
  when an Alibaba Cloud AccessKey is configured.
- `pitwall-agent-routing usage serve`: samples usage on a schedule, computes burn rate, time
  to full, and history, and answers the desk meter payload at `GET /usage` with
  `Content-Length` on every response. It binds to loopback by default and requires
  `PITWALL_AGENTS_USAGE_TOKEN` on any other address.

##### Fixed

- A dispatch that asks and exits 75 pauses even when the ask was answered before the
  supervisor saw the exit. Asks are answered while an attempt runs, and on a busy machine the
  answer could arrive first; the run was then recorded as failed and never resumed.

#### [0.12.0] - 2026-09-25

##### Added

- Event-driven orchestrator channel: managed parent tools (`dispatch_and_wait`,
  `answer_and_wait`, `wait_dispatch`, `steer_and_wait`) return child questions, defaults, and
  results as events, and a Claude launch guard blocks external launches outside managed dispatch.
- Durable workflows dispatch Pi tasks; providers without workflow support are rejected at
  validation.
- `setup mcp` registers `pitwall-channel` for Qwen Code (`~/.qwen/settings.json`, honouring
  `QWEN_CODE_HOME`), so `qwen` routes can run as managed interactive dispatches.

##### Fixed

- `setup mcp` keeps Codex tables that landed inside its managed `config.toml` block. Codex appends
  new tables at the end of the file, before the block's end marker, and a resync deleted them.
- Workflows answer asks from tier-1 children. A child with the channel registered waits inside
  `ask_orchestrator` instead of pausing, and the scheduler only handled paused dispatches, so its
  asks were never policy-answered or escalated with an `answer` command; they ran out to their
  defaults. The scheduler now handles them while the dispatch runs.
- The policy answerer allows the route's output limit (default 1,024 tokens) instead of 64. A
  reasoning model spent all 64 tokens thinking and returned no choice; a reply cut off by the
  limit now says so.
- `routes probe` and endpoint discovery send a `pitwall-agent-routing/1` user agent. RunPod's
  proxy rejects urllib's default agent with 403, so every RunPod route probed as `unauthorized`.
- `doctor` no longer reports a false `WARN` on an installed Grok Build CLI. `grok --help`
  hides `--no-auto-update` even though the parser accepts it, so the provider help contract now
  proves acceptance by invoking `grok --no-auto-update --help` and requires only the visible
  `--output-format` flag. The adapter still emits `--no-auto-update` for dispatch.

#### [0.11.1] - 2026-09-18

0.11.0 was tagged on the same code but never published: its tag carried no dated changelog entry
or release note, so the release workflow refused it. 0.11.1 is that release with the metadata.

##### Added

- The orchestrator ↔ subagent channel (Phases 0/A and B–D): per-dispatch mailboxes with asks,
  answers, steers, and acks; deadlines, defaults, and per-dispatch ask caps; the `inbox`,
  `answer`, `steer`, `runs resume`, and `runs stop` verbs; broker channel endpoints; the stdio MCP
  server `pitwall-agent-routing mcp` with tier-1 asking and the orchestrator inbox tools; a tier-2
  PreToolUse steering gate for the Claude Code and Codex bundles; graceful abort with a receipt;
  workflow `askSupport`, `maxAsks`, `autoAnswer`, and `wait_for_answer`; the D3 policy answerer
  with `policy:<model>` provenance; channel aggregates on the finished ledger row.
- `setup mcp` registers `pitwall-channel` for six harnesses; `install.sh --with-mcp`; uninstall
  removes the registrations; `doctor` verifies the MCP handshake and per-harness registration.
- Bootstrap inventories configured MCP servers, plugins, skills, agents, commands, extensions, and
  tools for detected harnesses as a bounded, names-only snapshot that the host routing skills load
  before choosing or briefing an external subagent.
- A gateway seat for Pitwall free-tier routes.

##### Changed

- Renamed the plugin family to `pitwall`; agent types are now `pitwall:<shim>`; the
  `model-routing` script is removed in favour of `pitwall-agent-routing`. Ledger paths and
  environment variable names are unchanged.
- Dispatcher identity variables stay with the dispatcher so a harness can dispatch again.
- SIGINT is taken back from an inherited ignore so shims stay interruptible.

##### Fixed

- Concurrent mailbox writers no longer lose messages (exclusive-create sequencing); broker
  retries after a crash are idempotent; a crashed attempt with a pending ask pauses so resume can
  default it; dead-letter quarantine is capped at the newest 20 files per box.
- The scheduler recognises a paused run by its recorded state, not only exit 75, so a crash with
  a pending ask is resumed instead of retried as a transport failure.
- The ask cap is enforced before pending asks are counted, and the workflow resume loop is
  bounded by `maxAsks`.
- Ask and steer ids must be four-digit sequence ids that match their file name; anything else is
  quarantined instead of crashing the cap check or reaching `write_answer` as a path component.
- Mailbox reads no longer rewrite `mailbox.json` on every poll.
- The broker drains an oversized body before answering 413, so the client sees the status instead
  of a broken pipe.
- `runs resume` re-emits the model positional for opencode.

#### [0.10.0] - 2026-08-31

##### Changed
- The Agent Routing support floor is now Python 3.14 (`>=3.14,<3.15`), with 3.14.7 used by the
  managed Linux and macOS lanes; the prior Python 3.11–3.13 lanes are retired.

##### Added
- `tools/agents/detect_local_endpoints.py`: probes the ports local inference servers use, identifies the product from how it answers, reports whether a key is required, lists served models, and prints the commands that attach it. Supports `--host`, `--port`, `--api-key-env`, `--all-listening`, and `--json`.
- `docs/attach-local-endpoint.md`: end-to-end guide from finding a locally hosted model to a verified dispatch, with the server-side prerequisites and per-harness caveats that actually bite.
- `attach-local-endpoint` skill in all three plugin packages, so an agent can carry out the same procedure. Both the guide and the skill start from `model-routing setup providers`, so a machine with no harness installed has a first step.
- Per-route `limits.context` and `limits.output`, materialized into harness model configuration by `routes sync`.
- Repeatable `routes add --env NAME=VALUE` and parsed `--limits context=N,output=N` route tuning.
- `model-routing routes discover` for bounded endpoint catalog and swapper-state discovery.
- Probe `--timeout`, distinct `warming` results, and opt-in `model-routing doctor --probe-routes` liveness checks.
- Self-hosted endpoint documentation covering cold starts, agent payload sizing, harness timeouts, liveness, and operator hardening.
- Signed loopback Pitwall automation commands: `pitwall receiver`, `pitwall subscribe`, and the periodic `pitwall watch` pull fallback, with lease-event synchronization and auto-registration mappings.
- Per-route Pitwall `origin.state` and opt-in `autoServe` caps, plus dispatch-time self-heal that refreshes stale leases and requests at most one capability-only serve under those caps.
- Offline `routes.<name>.pitwall_sync` doctor warnings and the local-only `pitwall.receiver` health check.
- `model-routing routes refresh <name>` re-reads a Pitwall-origin route's capability metadata after a lease renewal or re-serve, updating model/expiry/lease in place while preserving the route's seat, effort, args, and env (re-`add` replaces the entry); expiry remedies now name it. `routes probe` reports the route's `leaseId`.
- Provider adapters can flag silently soft-denied runs (`detect_soft_denial`); Antigravity headless runs whose tools were auto-denied now exit `77` (EX_NOPERM) instead of `0`.
- `model-routing harnesses` (with JSON output) for the local harness inventory, including installed versions and effort controls.
- The doctor `provider.summary` check for installed and missing harnesses and routes pinned to missing harnesses.
- Typed route `effort`, top-level `routes.json` `harnesses` defaults, `routes add --effort`, and the setup-routes effort prompt.
- `model-routing routes add <name> --from-pitwall <capability>` for creating a route from Pitwall capability metadata, and `model-routing routes probe <name>` for explicit endpoint liveness checks.
- Optional route fields `expiresAt` and `origin` for lease-expiry awareness and non-secret route provenance.
- The offline `routes.<name>.expiry` doctor check for expired and soon-to-expire routes.
- Pitwall handoff documentation, including the serve → add → dispatch → probe workflow and lease-failure semantics.

##### Fixed
- Hermes HTTP/authentication errors printed on stdout despite exit 0 are now soft failures with exit 77.
- The doctor result schema enumerates every harness.

##### Fixed
- `setup providers` reported a hard-coded "All ten provider CLIs are already detected" regardless of the roster size; the count is now derived (13 today).
- Documentation freshness pass. Stale harness counts ("seven transports", "seven shims", "six transport shims") replaced with the real roster or with the rule that produces it; Hermes described as environment delivery in the README, three plugin READMEs, three skills, the shim agent definition, `routes.md`, `provider-registry.md` and `provider-cli-setup.md`; dsh still described as writing a generated `smr` profile rather than merging into `settings.yaml`, including a dead `configSync.path` in the provider registry; Antigravity missing from the README prerequisites, authentication list and `doctor --discover-models` description; `doctor --probe-routes` undocumented, its check order reversed, and `routes.json` failures wrongly mapped to exit 2 (only `runtime.registry*` failures exit 2); the Pitwall probe table missing `warming`, `routes refresh` under-reporting the fields it updates, and the receiver credited with applying `lease.expiring` events it only logs; workflow host-permission lists enumerating 5-6 of the available harnesses instead of stating the rule.

##### Changed
- **Hermes is now a config-materialization harness** (`endpointDelivery: config-sync`) instead of env delivery. Hermes resolves credentials from its own provider configuration, so an `OPENAI_API_KEY` in the environment was never attached to a non-loopback endpoint; `routes sync --harness hermes` now merges a managed `providers:` block into `~/.hermes/config.yaml` (honouring `HERMES_HOME`), preserving user-defined providers, and dispatch selects it with `--provider <route>` automatically. The block records `key_env` — the variable's name — so the secret never reaches the file. Unlike cline and dsh, Hermes holds several managed providers at once. Existing hermes routes need one `routes sync --harness hermes`; the soft-denial message names that command.
- dsh endpoint sync now merges managed providers into `settings.yaml` using the supported `llm-pi-ai` schema instead of generating a plugin profile.

#### [0.9.0] - 2026-08-27

##### Added
- Pi coding agent harness with argv prompt delivery and managed endpoint sync; API keys remain environment-variable references in `models.json`.
- Hermes Agent harness with argv prompt delivery and per-process environment endpoint delivery; custom endpoints use the documented provider environment variables.
- Cline CLI harness with argv prompt delivery and command-backed endpoint sync; it supports one custom endpoint and persists the API key value in Cline's store.
- Muse Code harness with file prompt delivery for Meta-hosted Muse Spark; it is model-bound and does not support custom endpoints.
- goose harness with stdin prompt delivery and per-process environment endpoint delivery; its installer uses the moving `stable` release and has no pinned checksum.
- DeepSeek Harness (`dsh`) with argv prompt delivery and a managed profile endpoint; it is experimental and has no per-invocation model selector.
- An explicit npm installer recipe kind with pinned packages, plus installer recipe `env` support for script recipes.
- `promptDelivery: "file"` support, endpoint resolver hooks, and omission of the model argument when a harness declares no model selectors.
- A parity audit test covering every registered harness across adapters, shims, agents, tripwires, installers, and documentation surfaces.

##### Changed
- The `provider-installers.json` schema now accepts npm recipes in addition to script recipes.

#### [0.8.0] - 2026-08-27

##### Added
- Antigravity CLI (`agy`) as a seventh vendor harness for Gemini 3.x (`agy-shim.sh`, `subagent-model-routing-claude:agy-shim`, installer recipe, `agy models` discovery, live-auth pong).
- `route-shim.sh`, the `routes` CLI verbs, and `setup routes` for named route profiles.
- `config/model-catalog.json` plus the registry's `harnessKind`, `endpointDelivery`, `endpointEnv`, `configSync`, and `modelFamilies` fields.
- Exit `78` (EX_CONFIG) for incomplete route configuration, with no ledger row.
- Route metadata in ledger rows, `result.json`, and `dispatch.created` events.
- Seed cards for Muse Glimmer, DeepSeek V4, and Gemma 4.

##### Changed
- Ledger rows advance to `schema_version` 4 for the additive `route` field.

##### Fixed
- Qwen dispatches now trigger Claude Code's Stop-hook guardrails: `dag-tripwire.py` and `ledger-tripwire.py` recognize `qwen-shim.sh` invocations and the `subagent-model-routing-claude:qwen-shim` agent type, completing the tripwire coverage the v0.7.0 Qwen design called for.
- The Claude skill's routing gate (helper block, Layer 0/1 rules, mechanical audit regexes, allowed-route and task-shape tables) and the `dag-routing` command now include the `qwen` helper and agent type, and the Qwen prompt card no longer claims the route goes through opencode.
- Documentation that still described four Claude-side shims or five providers (runtime architecture, workflow and provider-setup docs, prompting index, package READMEs and skills, CONTRIBUTING and CI sentinel checks) now counts Qwen; `validate_plugins.py` requires the Qwen route in the Claude and Copilot skills.
- The Qwen adapter and registry declare `$HOME/.local/bin/qwen` — the standalone installer's documented wrapper location and npm's global bin under a user prefix — as a fallback binary candidate when `qwen` is not on `PATH`.
- `route-shim` resolves model ids claimed by a harness's route family (effort-suffixed Antigravity slugs, `qwen*` ids) to that vendor harness instead of exiting `64` as unknown routes.

#### [0.7.1] - 2026-08-20

##### Changed
- Roster refresh from the current model cards: Kimi's registry model is now `kimi-code/k3` (Kimi K3 — 2.8T MoE/104B active, 1M context, always-on thinking steered by `reasoning_effort`, preserved-thinking multi-turn contract) and the GLM route example moves to `zai-coding-plan/glm-5.3` (same 744B-A40B base post-trained, 1M context/128K output, mandatory reasoning with `reasoning_effort`, text-only). Rankings blocks, prompt cards, capability-card seeds, and the canonical Kimi/GLM prompting references carry the new model-card facts.

#### [0.7.0] - 2026-08-20

##### Added
- Qwen as a first-class provider: `qwen-shim.sh` over the Qwen Code CLI, a `QwenAdapter` with `qwen-config` model attribution (env `QWEN_MODEL`, `~/.qwen/settings.json`, `~/.qwen/.env`), registry/doctor/discovery/installer coverage, a dedicated `qwen-shim` Claude Code agent, and Qwen3.8 model-card prompting guidance (`reasoning_effort`, `preserve_thinking`, updated sampling defaults). The qwen route family moves off OpenCode; package versions bump to 0.7.0.
- Optional `SHIM_RESULT=1` transport receipts, emitted as the exact `finished` ledger record on stdout immediately before the final `SHIM-DONE` sentinel. Failures that write no ledger record — usage errors and a missing process supervisor — continue to emit only the sentinel.
- `scripts/parse-shim-result.py`, a reference parser that reads only the trailing receipt/sentinel pair, so receipts a dispatched child printed into its own stdout cannot be mistaken for the shim's.
- An active execution-policy `profile` on routing-ledger records, distinguishing a genuine sandbox/approval bypass from a run where the child CLI kept its own policy.

##### Changed
- Routing-ledger records advance to `schema_version` 3 for the additive `profile` field.
- Ruff's lint selection is pinned in `ruff.toml` rather than inherited from the tool's shifting defaults, and `tools/check_requirements_lock.py` fails CI when `requirements-dev.lock` no longer matches the pins declared in `requirements-dev.txt`.

#### [0.6.0] - 2026-07-17

##### Added
- A dedicated `kimi-shim.sh` transport backed by the Kimi Code CLI, including registry, doctor, workflow, installer, host-package, tripwire, and test-suite integration. Kimi now uses its configured CLI default or an explicit `--model` override instead of routing through OpenCode.
- Read-only Kimi configuration validation through `kimi doctor config` and explicit, credential-safe configured-model discovery through bounded `kimi provider list --json` parsing.
- A dependency-free `model-routing setup providers` checkbox installer for missing Codex, Claude Code, Grok Build, Kimi Code, and OpenCode CLIs, including `/dev/tty` bootstrap support, dry-run review, first-party redirect/size validation, partial-failure recovery, and offline PTY/bootstrap tests.

##### Changed
- Kimi model attribution now follows the CLI's documented precedence (`-m`/`--model`, `KIMI_MODEL_NAME`, then `default_model`). Prompt-mode-incompatible permission flags and shim-owned prompt/output flags fail as usage errors before provider startup, and the registry now declares that Kimi has no per-invocation effort control.
- Vendor system cards are now cited at their official hosted URLs instead of being redistributed as complete converted copies.
- Public release candidates are assembled from an explicit allowlist and checked for private maintainer data before publication.
- CI actions are immutable SHA references, checkout credentials are not persisted, and development dependencies install from a hash-locked file.
- Provider installer downloads verify maintainer-pinned SHA-256 digests when available and report the resolved source, digest, and installed CLI version.

#### [0.5.0] - 2026-07-10

##### Added
- Explicit `model-routing doctor --discover-models` checks. OpenCode uses its bounded documented model-list command, Codex reads its CLI-managed local cache, and unsupported discovery surfaces report `SKIP`; failures and output drift remain non-blocking warnings.
- A versioned JSON workflow schema and semantic validator with cycle detection, native-host route checks, alias resolution, safe prompt paths, explicit context selection, retry validation, and argv-only verification commands.
- A persistent foreground workflow scheduler with global/per-provider concurrency, deterministic dependency release, fail-fast/continue policies, bounded context handoff, fresh-worktree retries, post-dispatch verification, Ctrl+C/external cancellation, and resume without rerunning successful tasks.
- `model-routing workflow run|list|show|resume|cancel`, workflow/task/attempt lineage in dispatch records/events/ledger entries, private atomic workflow state, and dependency/failure-resume examples.

##### Changed
- Codex and Copilot packages now document executable dependency workflows. Claude continues to prefer native Workflow and retains tripwire enforcement; the runner's self-declared `--host` validation is advisory.
- All three package versions and public documentation now reflect the completed Phase 6 scope.
- Discovery, verification, and lifecycle-hook subprocess output is drained with fixed memory bounds; lifecycle output events are emitted after provider pipe drainage so hook latency cannot deadlock a child.
- Workflow cancellation now uses an active scheduler lease plus locked state merging instead of trusting a persisted PID, resume can verify the persisted host, and Claude's Stop hook blocks shared-runner commands that declare a non-Claude host.
- CI now enforces Ruff, strict mypy, JSON instance/schema validation, plugin structure/native-host boundaries, local Markdown links, clean diffs, and GitHub Actions syntax.

#### [0.4.0] - 2026-07-10

##### Added
- A versioned, non-destructive `model-routing doctor` with runtime, provider, plugin, and security checks; provider filtering; JSON output; installation-only mode; and explicitly opted-in read-only authentication probes. Default doctor and dispatch preflight perform no live model discovery.
- Opt-in `shared`, `isolated`, and declared-task `auto` workspace modes. Isolated writes run on owned `model-routing/<dispatch-id>` Git worktrees below the private state root and retain binary-safe patches without changing the caller's worktree.
- Explicit `runs diff`, `runs apply`, and `runs discard` integration commands. Application validates repository identity and a path manifest, preserves terminal dispatch state, and records applied/conflicted/discarded integration metadata separately.
- Worktree, doctor, no-network/default-probe, patch application, conflict, ownership, installer-doctor, and cleanup-retention tests.

##### Changed
- The installer now runs `model-routing doctor --installation-only`; bootstrap output points users to the full doctor.
- Run cleanup preserves active isolated worktrees until an explicit discard, and run records include workspace/change artifacts when present.
- Discard now requires an exact live Git worktree/branch/owner/path match and refuses raw filesystem fallback deletion, including after metadata tampering or repository moves.

#### [0.3.0] - 2026-07-10

##### Added
- GPT-5.6 Sol, Terra, and Luna routing guidance for `codex-shim`, including inline `--model=` attribution and focused forwarding tests.
- A `grok-shim` transport for xAI Grok Build, defaulting to Grok 4.5, with routing guidance across the Claude Code, Codex, and GitHub Copilot CLI packages.
- A `claude-shim` transport for Claude Code print mode, defaulting to the `sonnet` alias, as a target route for the Codex and GitHub Copilot CLI packages.
- Officially linked Claude Sonnet 5, Opus 4.8, and combined Fable 5/Mythos 5 system cards, with separate evidence-grounded prompting references and capability cards for Sonnet 5, Opus 4.8, and Fable 5. Mythos intentionally has no route-specific reference or card.
- A Python 3.11+ standard-library runtime shared by all four compatibility shims, with process-group timeout/cancellation, streamed and retained output, private atomic run records, lifecycle events, fail-open portable hooks, and `model-routing runs` inspection/cleanup commands.
- A canonical provider registry, semantic validator, JSON schemas, and generated host-specific route catalogs that enforce Claude/Codex native-provider boundaries.
- The additive `CODEX_BIN` executable override, bringing Codex to parity with the other shim transports.

##### Changed
- The Codex-native package no longer routes back through `codex-shim`; Codex work stays native/inline there, while `codex-shim` remains available to Claude Code and Copilot.
- The public Bash shims are now thin wrappers over the shared runtime while retaining the v0.2 sentinel bytes, exit codes, prompt delivery, provider argv, telemetry, and per-shim ledger asymmetries.

#### [0.2.0] - 2026-07-08

##### Added
- Standalone `codex` and `opencode` transport shims, formalized around the `SHIM-DONE` contract.
- Provider-agnostic routing: supports any opencode provider, including local OpenAI-compatible endpoints.
- Routing skill with flat one-shot dispatch and Workflow DAG orchestration for Claude Code, plus direct-shell packages for Codex and GitHub Copilot CLI.
- Fail-open guardrail hooks for tripwire-style safety checks.
- Clone-optional bootstrap installer (`scripts/bootstrap.sh`, with `scripts/install.sh` for cloned checkouts).
- Opt-in OTLP observability.
- Quantitative dispatch ledger with seed capability cards.
- Continuous integration covering syntax, sentinels, manifests, and hook pipe tests.

### Gateway (last version `gateway/v0.2.0`)

#### Unreleased

- Upstream error responses (status 400 and above) are no longer relayed byte for byte: an
  eligible 4xx JSON body keeps its wording after the recursive sanitizer, and every other error,
  including any body that echoes a credential, becomes the gateway's own envelope with the
  redacted upstream message.

#### 0.2.0 (2026-09-25)

- The fork keeps only the eight `open-sse/` files the shim imports. The import script deletes
  the other 1,448 upstream files after the strip-list copy, including fingerprinting,
  Turnstile, and web-session modules that ADR 0007 bars and the path strip-list had missed.
- Per-provider routing: `PITWALL_GATEWAY_ROUTES` names a route table; requests carry
  `x-pitwall-route`, and missing, unknown, or unkeyed routes are refused with `400
  route_required`, `404 route_not_found`, or `503 upstream_key_missing` without an upstream
  call. Boot fails closed without a route table or `PITWALL_GATEWAY_UPSTREAM_URL`; the
  `127.0.0.1:65535` placeholder default is removed.
- Upstream bearer authentication, incremental SSE relay, client-disconnect cancellation, and
  caller-shape `400` validation; `/v1/models` lists routes (or the single upstream's catalog).
- One start path: the launcher and a direct `node dist/src/shim.js` both start the server; a bare
  import never does.

#### 0.1.2 (2026-09-18)

0.1.1 was tagged but never published: the release workflow's SBOM step pointed at a directory
without a manifest, and the release step would have uploaded every vendored file as a separate
asset. 0.1.2 is the same shim; the workflow now generates the SBOM from a runtime-only install of
the shipped manifest and publishes one reproducible tarball, its SBOM, and `SHA256SUMS`.

#### 0.1.1 (2026-09-18)

0.1.0 was tagged as `gateway/v0.1.0` but never published: the release workflow compared builds
before building. 0.1.1 is the same shim with that workflow fixed, plus:

- `dedupSystemPrompt` removes only consecutive duplicate lines, so code fences and repeated bullets
  survive the rtk, caveman, and stacked compression policies.
- vitest 5 and vite 7 (dev dependencies), clearing the advisories `npm audit --audit-level=high`
  reported.
- The release workflow requires the tag version to match `package.json`, builds before the
  byte-identity check, and fails the launcher smoke unless the shim reports it is listening.

#### 0.1.0 (2026-09-10)

Initial fork of OmniRoute's `open-sse/` subset as `@pitwall/gateway`. Implements
the loopback shim surface called out in
`docs/superpowers/plans/2026-09-10-free-tier-gateway-integration.md` §Task 18,
Steps 1–3 plus the npm part of Step 6.

- Strip-list applied per the plan (Step 1).
- Import script copies `open-sse/` minus the strip-list, rewrites wreq-js
  references to `pitwallFetch`, fixes `@/` imports in kept files that the
  shim typechecks, and writes `UPSTREAM.lock`.
- Shim exposes `POST /v1/chat/completions`, `GET /v1/models`,
  `POST /v1/embeddings`, `GET /health`, `GET /internal/telemetry`.
- Required `PITWALL_GATEWAY_TOKEN`, loopback-only bind, 1 MiB body cap, 120 rpm
  per-token rate limit, structured error envelope via upstream `buildErrorBody`,
  Claude→OpenAI translation, lite compression policy.
- Vitest covers the six hardening cases from the plan plus routing smoke
  tests.
