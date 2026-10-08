# 23 — Pitwall Agent Routing

Pitwall Agent Routing is `pitwall agents`, the `pitwall.agents` package: a local agentic-work router
that lives in the one `pitwall` distribution but stays outside the broker runtime. It delegates prompts
to installed agent harnesses, records private local results, supports dependency workflows and isolated
worktrees, and can consume a Pitwall-served model as an OpenAI-compatible profile. It is not a broker
service, remote coordinator, or background control plane. Terms: a **harness** is an agent CLI, an
**agent profile** is a `name@harness` binding, a **dispatch** is one harness run, and a **workflow** is
a dependency-ordered set of dispatches.

## Project and artifact boundary

Agent Routing shares the root `pyproject.toml`, `uv.lock`, version, and Apache-2.0 licence (the MIT
terms it was released under are reproduced in `NOTICE`). `pitwall.agents` never imports broker
services, repositories, or `pitwall.db`; it may import Pydantic models from `pitwall.api`. The entry
points `pitwall agents _shim`, `pitwall agents _steer-gate`, and the hook entry points import none of
`fastapi`, `uvicorn`, `asyncpg`, `redis`, `arq`, `textual`, `runpod`, `mcp`, or `prometheus_client`
(`tests/agents/test_dispatch_import_weight.py`).

The package ships canonical JSON config and schemas as package data. The resource API accepts only
named `config/*.json` and `schemas/*.json` resources and loads them with `importlib.resources`
(`pitwall.agents.resources`). `pitwall agents install` writes the harness shims (two-line wrappers
around `pitwall agents _shim`) and `route-shim.sh` under `~/.claude/scripts/`, copies the Claude Code,
Codex, and GitHub Copilot CLI plugins with one `pitwall-local` marketplace per host, and registers the
channel MCP server, recording each in an install manifest (`pitwall.agents.installation`).

## Execution modes

Recursive workflow and receiver execution share one descriptor: the running absolute interpreter with
`-m pitwall.agents` (`pitwall.agents.execution:child_execution`). Version output is the installed
distribution's metadata (`pitwall.agents.execution:distribution_version`). No checkout search or
ambient `PYTHONPATH` fallback is used.

Normal registry loading validates packaged semantics without opening extra files. Source CI and the
doctor add physical checks for shims, plugin packages, prompts, capability cards, generated assets, and
reference anchors; the doctor's installation-only mode skips harness probing
(`pitwall agents doctor --installation-only`).

## Live process output

The process pump forwards available stdout and stderr bytes to both private run logs and
the caller before child exit, including short writes without a newline. It uses buffered
`read1(65536)` rather than waiting for a full chunk; 64 KiB is a chunk maximum, not a
retention limit (`pitwall.agents.process`). Provider-side
buffering still requires the provider to flush. The real-child regression gates each stream
on observed log and terminal delivery before allowing the child to finish, then verifies
complete large-output retention (`tests/agents/test_process.py::test_small_output_reaches_logs_and_terminal_before_child_can_continue`).

## Local trust and state

Every dispatched provider process runs with the operator's local authority subject to its own
harness policy. The unrestricted profile is the unattended default; isolated Git worktrees reduce
accidental edits to the caller's checkout but are not security containers. Workflow prompts,
verification commands, lifecycle hooks, provider output, and generated patches remain untrusted.

`pitwall agents setup inventory` runs the explicit harness-capability inventory. The inventory
examines only known config surfaces for detected harnesses and stores capability names and source
paths in private JSON and Markdown under the agents config root (`~/.config/pitwall/agents/`); it does not retain config values,
environment values, credentials, or skill/agent file contents
(`pitwall.agents.capability_inventory`). Host skills load the
Markdown snapshot before profile selection so an orchestrator can mention relevant MCP servers,
plugins, skills, agents, commands, extensions, or tools when briefing a subagent. Inventory entries
describe availability, not authorization or authenticated readiness.

The doctor keeps that distinction for account readiness. Its `details.readiness` values are
`absent`, `configured`, `signed-in`, `verified-request`, and `unknown`
(`pitwall.agents.doctor`). A documented local
configuration probe or a known non-empty credential environment variable can establish
`configured`; a successful documented status command under `--live-auth` establishes
`signed-in`; only an explicitly requested bounded Antigravity pong request establishes
`verified-request`. Opaque auth-store presence is retained only as a boolean metadata marker and
remains `unknown`, because file presence does not establish a usable credential. Default doctor
execution never submits a provider prompt. Antigravity has no local status command, so its
`--live-auth` path is an explicitly disclosed inference request; the provider CLI may perform
its own refresh as part of that request. Doctor does not initiate login or credential changes and
does not retain probe output. Failed or unavailable checks remain `unknown` and do not trigger
automatic login guidance (`docs/agents/doctor.md`).

Run and workflow roots use private directory/file modes. Prompt content is not retained unless
requested, but stdout/stderr and dependency context can still contain sensitive data. Cleanup is
operator-driven. Profile configuration stores environment-variable names, never endpoint key values.
Harness setup (`pitwall agents setup harnesses`) is a separately confirmed installer surface and never
performs login. State lives under `~/.local/state/pitwall/agents/` and config under
`~/.config/pitwall/agents/` (`pitwall.agents.paths`).

## Pitwall consumer contract

Agent Routing consumes the broker through public HTTP contracts; it is not imported by the server.
Capability and proxy access use `PITWALL_AGENTS_API_TOKEN` (`pitwall.agents.broker`); the broker's own
`PITWALL_API_TOKEN` is not read as a fallback. Subscription creation uses a separate
`PITWALL_AGENTS_SUBSCRIPTION_TOKEN`. Receiver verification prefers `PITWALL_AGENTS_WEBHOOK_SECRET`,
with `PITWALL_WEBHOOK_SECRET` only a legacy fallback, and explicit `PITWALL_WEBHOOK_SECRET_ENV`
indirection is fail-closed (`pitwall.agents.broker`).

The optional event receiver is loopback-only. Service generation writes under the user's HOME,
uses the shared execution descriptor, and does not enable the unit unless `--enable` accompanies
`--install` (`pitwall.agents.cli`). Ready, renewed,
stopped, and expiring lease events update route-side synchronization state; Pitwall remains the
authority for spend, budgets, lease lifetime, and its kill switch.

Profiles may also target the free-tier gateway. `gateway` is a valid seat in the profile schema
alongside the original six (`pitwall.personal.routes`), and the
personal serving path attaches gateway-pinned routes with `--seat gateway` while the default
remains `local` (`pitwall.personal.routes`). A
gateway seat routes `gw/…` model ids through the loopback gateway, whose `model_id_map` pins the
serving provider. The seat is a routing label only: it grants no additional broker authority, and
gateway consumption uses the same API-token contract as every other consumer route.

## Subscription usage

`pitwall usage` reports one row per subscription plan and account
(`pitwall.agents.usage`). A row holds percent
used per window, the reset instant, and one of six statuses; `error`, `stale`, and `unknown` mean
no information (`pitwall.agents.usage.rows`).

Readers are read-only. Each makes one GET with the credential already on the machine, never
refreshes a token, and reports a failure as a status code plus fixed wording
(`pitwall.agents.usage.rows`). An account is a plan
plus a login directory; a profile whose `env` sets `CLAUDE_CONFIG_DIR` or `CODEX_HOME` declares a
second one (`pitwall.agents.usage.accounts`).
The last good row, the last failure, and the last attempt are kept per account under the state
directory, and the attempt is recorded before the request so a failing source is throttled too
(`pitwall.agents.usage.cache`).

The Claude Code host boundary reads the `[agents.profiles]` tables of `pitwall.toml` and lets a saved
Claude profile through when it sets `CLAUDE_CONFIG_DIR` (`plugins/claude/hooks/dag-tripwire.py`).

`pitwall usage serve [--host H] [--port N] [--interval SECONDS]` (defaults `127.0.0.1`, `8848`, `45`; see [CLI](18-cli.md)) samples on a schedule and serves the rows. It refuses an
address other than loopback without a bearer token, and an interval under 30 seconds
(`pitwall.agents.usage.serve` and
`pitwall.agents.usage.serve`). Every response carries
`Content-Length`, because the desk meter cannot read a chunked body
(`pitwall.agents.usage.serve`). `GET /usage` keeps the
payload the desk meter firmware reads: at most 7 rows, 3-character account tags, and five
statuses (`pitwall.agents.usage.serve`).

## Model facts

`docs/agents/model-facts/` records what vendors, harnesses, hosts, and released model
files state about each model family, with a source for every claim. Each unit lists its sources
with an observed and a reviewed hash; a source is pending when they differ, and a page type that
is not published is recorded with what was searched
(`tools/agents/validate_model_facts.py`).

`tools/agents/model_sources.py` fetches sources with no credential and hashes normalised text, so page
chrome does not read as a change (`tools/agents/model_sources.py`). `check`
reports pending, unreachable, new, retiring, and stale items and changes no file unless given
`--write` (`tools/agents/model_sources.py`). Only `review` records that a source
was read (`tools/agents/model_sources.py`). Five facts are extracted from
Hugging Face files without judgement (`tools/agents/model_sources.py`).

`tools/agents/sync_model_facts.py` places text and never writes it. It fills the blocks between
`MODEL-FACTS` markers and refuses a missing, doubled, or crossed marker
(`tools/agents/sync_model_facts.py`). In the registry it changes only a model's
`displayName`, `effortValues`, and `provenance` and a family's context, sampling, licence, model
card, and provenance; it removes nothing and never changes a default
(`tools/agents/sync_model_facts.py`). The validator stops a default that names a
model retiring within 30 days (`tools/agents/validate_model_facts.py`) and
rejects guidance that repeats twelve words of its cached source
(`tools/agents/validate_model_facts.py`).

## Pi endpoint configuration ownership

Pi route sync updates a selected model by exact ID, preserving other models, provider extras,
and model metadata such as reasoning, image input, sampling and compatibility settings.
The managed fields are provider `baseUrl`, `api`, an explicitly supplied `apiKeyEnv` reference,
and the selected model's context/output limits. Missing route limits retain the documented
32,768/4,096 defaults; they are configuration defaults, not measured server capabilities
(`pitwall.agents.harnesses.pi`).

Conflicting endpoints or effective provider/model API types fail planning rather than silently
retargeting an existing profile. Duplicate selected model IDs also require repair. Sync status
compares managed token limits as well as endpoint/API/auth references
(`pitwall.agents.harnesses.pi`).

Omitting `apiKeyEnv` preserves an existing Pi-owned authentication source and does not manufacture
a credential. Intentionally keyless endpoints need an explicit Pi-supported dummy/auth source
chosen by the operator. Structural sync status is neither credential verification nor a live
model test. Use `PI_CODING_AGENT_DIR` for an isolated profile; existing plan/diff/backup handling
and all-endpoint destination sync remain unchanged.

The legacy Pi shim still runs a one-shot print-mode process without a persisted Pi conversation.
An interactive Pi session has separate ownership and must not reinterpret replay receipts as
native session resume.

## CI and release

Agent Routing runs in the same CI and release as the rest of Pitwall: `tests/agents/`, the
`tools/agents/` validators, and the plugin checks are jobs in `ci.yml`, and the release is the one
`v*` tag (see the root `RELEASING.md`). Repository-wide Markdown, text, secret, DCO, and workflow
policies cover `src/pitwall/agents/`, `plugins/`, and `docs/agents/`. The earlier `agent-routing/v*`
tags are historical snapshots of the standalone component.

## Detailed documentation

- [Agent Routing landing page](../agent-routing/README.md)
- [Migrating from standalone Agent Routing](../operator/agents-migration.md)
- [Agent Routing README](../agents/routing-readme.md)
- [Agent Routing security model](../agents/SECURITY.md)
- [Agent profiles](../agents/routes.md)
- [Subscription usage](../agents/usage.md)
- [Workflows](../agents/workflows.md)
- [Pitwall handoff](../agents/pitwall.md)

Hermes one-shot dispatch sets `TERMINAL_CWD` to the prepared dispatch workspace, including an isolated worktree. This overrides an inherited terminal directory for the child process only; it does not change the user's Hermes configuration. Without this setting, Hermes one-shot tools can resolve relative files against the home directory instead of the task workspace.

The Grok doctor help contract invokes `grok --no-auto-update --help` to verify the supported hidden flag through parser acceptance; output must still include `--output-format`. Help/version checks remain local and do not establish authenticated inference (`src/pitwall/agents/doctor.py` in the Agent Routing component).

## ZCode account route

`pitwall.agents.harnesses.zcode:ZCodeAdapter` dispatches a headless ZCode prompt
using its saved model/account. The registry labels this `zcode-default`; no model,
effort or endpoint override is exposed. The adapter supplies explicit yolo/build
modes and the dispatch workspace, and redacts prompt text from receipt arguments.
ZCode authentication remains owned by ZCode. Missing binaries use the manual
installer disposition (`pitwall.agents.setup:install_selected`); installed binaries
remain available without an installer download. See [ZCode](../agents/zcode.md).

The Claude tripwires recognize both `zcode-shim` and `pitwall:zcode-shim`
(`plugins/claude/hooks/dag_tripwire_shims.py:SHIM_TYPES`,
`plugins/claude/hooks/ledger-tripwire.py:SHIM_TYPES`). Distillation keeps saved-model
observations in the harness card rather than assigning them to a GLM model family.
The release acceptance denominator adds the provider, shim and adapter surfaces,
bound to the hermetic registry and journey tests (`release_acceptance/surface-test-map.json`).
