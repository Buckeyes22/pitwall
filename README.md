# Pitwall

[![CI](https://github.com/Buckeyes22/pitwall/actions/workflows/ci.yml/badge.svg)](https://github.com/Buckeyes22/pitwall/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.14-blue.svg)](pyproject.toml)

Pitwall is one broker for the GPU and inference spend of AI-assisted development. For requests it
brokers, it estimates the cost and checks it against a monthly budget and a per-request cap before
the provider call. It routes inference to a provider that can serve it. It also routes your coding
work to the model and agent harness that suits the task, and tracks what all of it costs.

These limits are Pitwall's own admission checks, not a spending ceiling on your provider accounts.
Coding harnesses bill their own subscriptions or API keys directly. Serving a model personally keeps
a separate local ledger. Automatic kill-switch escalation on a budget breach is off by default
(`PITWALL_BUDGET_BREACH_KILL_MODE=disabled`; see [Configuration](#configuration)).

One install and one command, `pitwall`, carry five parts:

- **Broker.** A capability API for inference, embeddings, and compute over RunPod, Vast.ai,
  Together, and Lambda Cloud, with pre-spend cost gates, routing with fallbacks, pod leases, and an
  emergency kill-switch. The [support matrix](docs/support-matrix.md) lists exactly what each
  adapter does and which paths have been verified live.
- **Agent Routing** (`pitwall agents`). Dispatches a prompt or a dependency graph of prompts to
  fourteen agent harnesses, including Codex, Claude Code, Kimi, OpenCode, Grok Build, and Qwen Code,
  and keeps a run record of each dispatch.
- **MCP servers.** A broker server (81 tools) and an orchestrator channel server, both over local
  stdio. A dispatched subagent can ask the orchestrator a blocking question, and the orchestrator
  can steer a running dispatch.
- **Pi workbench** (`pitwall workbench`). Launches the pinned Pi coding agent with exact profiles
  and isolated state.
- **Free-tier gateway** (`pitwall gateway`). A quota-aware catalog of free model pools and a
  loopback sidecar, so coding runs can use pools you hold keys for before paid compute.

Pitwall is pre-1.0 software preparing for its first public alpha. APIs and configuration may change
between minor versions. The [support matrix](docs/support-matrix.md) lists the supported and
deferred surfaces. The public history starts at a single commit, so commit hashes and pull request
numbers cited in plans, evidence, and changelogs refer to earlier development history that is not
published.

### Before you dispatch

Agent Routing runs the harness CLIs you have installed, as you, with your files, credentials, and
network. By default each CLI keeps its own sandbox and approval prompts. For unattended runs,
`export PITWALL_AGENTS_UNRESTRICTED=1` passes each CLI's bypass flag (for Codex,
`--dangerously-bypass-approvals-and-sandbox`), so the child can run commands without asking. Kimi
Code and dsh run only with that setting. See [Agent Routing security](docs/agents/SECURITY.md).

## Where your data goes

- **Model providers.** Prompts, and any code or files a harness reads, go to the provider behind
  that harness or route (OpenAI, Anthropic, Google, a RunPod pod you serve, and so on) under that
  provider's terms.
- **Local run records.** Each dispatch's stdout and stderr are kept under
  `~/.local/state/pitwall/agents/runs/` (mode 0600), with no automatic deletion. Remove them with
  `pitwall agents runs cleanup --older-than <days>`. Prompt bodies are stored only with
  `--routing-retain-prompt`.
- **Process listings.** Kimi Code, Antigravity, Qwen Code, Hermes, Cline, ZCode, and dsh receive
  the prompt as a command-line argument, so other processes of the same user (`ps`) can see it
  while the child runs.
- **Optional destinations, off unless configured:** Langfuse tracing (when
  `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set; without `LANGFUSE_HOST` it sends to
  `https://cloud.langfuse.com`), Resend email alerts (`RESEND_API_KEY`), Cloudflare R2 pod-log
  forwarding (`R2_ENDPOINT`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`), and the signed outbound webhooks
  described in [webhooks](docs/webhooks.md), which go to URLs you register.

## What you can do with it

**Check what a model costs on a GPU before you rent one.** The catalogue lists the models Pitwall
can serve, and `fit` shows which GPUs hold a model and what a lease of a given length costs. With a
RunPod key set, prices are live. Without one, the table shows `unpriced`.

```bash
pitwall models list
pitwall models fit ornith-ai/Ornith-1.5-35B-A3B-GGUF --ttl-minutes 45
```

**Serve a model with hard limits.** The pod carries its own self-termination deadline, `serve`
refuses to launch without a monthly budget, and every serve, refusal, and stop lands in a local
audit log. You need a RunPod account with billing and an API key in `RUNPOD_API_KEY`, and RunPod
charges while the pod exists. See [personal serving](docs/operator/personal-serving.md).

```bash
pitwall setup
export PITWALL_MONTHLY_BUDGET_USD=20
pitwall serve --model ornith-ai/Ornith-1.5-35B-A3B-GGUF --gpu-class "NVIDIA GeForce RTX 3090" \
  --ttl-minutes 45 --max-usd-per-hour 1.00 --route ornith
pitwall status
pitwall stop ornith
```

**Send a prompt to another model.** `pitwall agents dispatch` sends a prompt file to a harness, or
to a named route profile such as the pod served above. See
[Agent Routing](docs/agents/routing-readme.md) for the shims, profiles, and worktree isolation.

```bash
pitwall agents dispatch codex examples/prompts/first-dispatch.md
pitwall agents dispatch route ornith examples/prompts/first-dispatch.md
```

`pitwall agents install` also writes `codex-shim.sh` and the other shims to `~/.claude/scripts/`,
which is not on `PATH`. Coding-agent hosts call them by full path. To run them yourself,
`export PATH="$HOME/.claude/scripts:$PATH"`.

**Run a dependency graph of tasks across models.** A workflow is a JSON document of tasks, each
routed to a harness and model, with ordering, retries, and verification. It runs in the foreground,
and a stopped run can be resumed. See [workflows](docs/agents/workflows.md) for the
document format.

```bash
pitwall agents workflow run workflow.json --host copilot
pitwall agents workflow list
pitwall agents runs list
```

**Give your coding agent the broker.** Register the broker MCP server with Claude Code, Codex, or
OpenCode. `--dry-run` prints what would change and writes nothing. The broker server needs
`DATABASE_URL`, `REDIS_URL`, and `RUNPOD_API_KEY` where the harness runs, and `pitwall doctor`
checks them.

```bash
pitwall mcp install claude-code --dry-run
pitwall doctor
```

## Architecture

```text
  your agent / terminal                 coding-agent hosts
  REST - CLI - Textual console - MCP    (Claude Code, Codex, Copilot CLI)
              |                                    |
              v                                    v
  +-----------------------------+      +--------------------------------+
  | Broker                      |      | Agent Routing                  |
  | pre-spend inspection        |      | shims + route-shim.sh          |
  | routing + fallbacks         |      | profiles (name@harness)        |
  | budget and cap admission    |      | workflows, run records,        |
  | audit, kill-switch          |      | isolated worktrees             |
  +--------------+--------------+      +----------------+---------------+
                 |                                      |
                 v                                      v
  RunPod - Vast.ai - Together -         14 agent harnesses (codex, claude,
  Lambda Cloud - free-tier gateway      kimi, opencode, grok, qwen, pi, ...)
                 |                                      |
                 v                                      v
  Postgres + Redis + reconciler         orchestrator channel (MCP): ask,
  cost rollups, Prometheus metrics      answer, steer
```

The broker is a layered control plane. Requests enter through REST, MCP, the CLI, or the Textual
console, then pass through shared guardrail, routing, cost, audit, execution, and reconciliation
layers. Postgres holds state, Redis feeds the background workers, and the reconciler converges
workload state, runs health probes, expires leases, and rolls up cost.

Agent Routing is a subpackage of the same distribution. It talks to the broker only over
HTTP, and its shim and hook entry points do not load the broker's web, database, or queue
dependencies. A **harness** is an agent CLI, an **agent profile** is a `name@harness` binding in
`pitwall.toml`, a **dispatch** is one harness run, and a **workflow** is a dependency-ordered set
of dispatches. The channel server registers with thirteen channel harnesses: the dispatch harnesses
except Pi and dsh, which have no MCP client, plus GitHub Copilot CLI. See the
[architecture](docs/sdlc/01-architecture.md) and the
[orchestrator channel](docs/agents/orchestrator-channel.md).

Spend is visible in three places: `pitwall cost`, `pitwall burn-rate`, and `pitwall budget` read
persisted workload costs and budget use, `pitwall usage` shows subscription usage for every routed
plan, and the cost exporter publishes Prometheus metrics with Grafana dashboards under
[`dashboards/`](dashboards).

## Quick Start

Install the CLI with [`uv`](https://docs.astral.sh/uv/). This puts `pitwall` and the service
commands on your `PATH`:

```bash
uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl
```

To install from a checkout instead:

```bash
git clone --branch v0.3.0a1 https://github.com/Buckeyes22/pitwall.git && cd pitwall && uv tool install --python 3.14.7 .
```

### Route work to other models

Install the shims and plugins once so a coding-agent host can dispatch work through Pitwall:

```bash
pitwall agents install
```

This writes one shim per harness and `route-shim.sh` under `~/.claude/scripts/`, installs the Claude
Code, Codex, and GitHub Copilot CLI plugins, and registers the channel MCP server. It needs no
database. `pitwall agents uninstall` removes exactly what it wrote. A coding agent can do the whole
setup itself by following [`docs/agents/install.md`](docs/agents/install.md), which runs
`pitwall doctor` for a readiness report. Start with the
[Agent Routing landing page](docs/agent-routing/README.md).

Then install a harness CLI, sign in to it yourself, and send a first prompt. Codex is the example:

```bash
pitwall agents setup harnesses      # installs missing harness CLIs after you confirm; never logs in
codex login                         # authenticate Codex yourself
pitwall agents doctor --harness codex --live-auth
pitwall agents dispatch codex examples/prompts/first-dispatch.md
```

The doctor prints `codex read-only auth probe succeeded` (PASS) when Codex is signed in. The probe
runs `codex login status` and makes no inference request. A WARN means the status is unknown, not
that you are logged out.

### Serve a model on a RunPod pod

You need a RunPod account with billing set up and an API key in `RUNPOD_API_KEY`
(`pitwall setup` checks for it). RunPod starts charging when the pod is created, before the model
answers, and charges until the pod is terminated. `pitwall stop <route>` terminates it. The pod
also carries its own deadline from `--ttl-minutes`, and RunPod treats that deadline as best effort.
If `pitwall status` or the RunPod console still shows a pod you did not expect, follow
[orphan cleanup](docs/operator/troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner).

For a personal RunPod-backed model server, initialize the local endpoint key with `pitwall setup`,
then follow the serve example above. This path needs no database. It keeps local state, enforces a
local monthly budget (a lease's maximum spend counts until it stops, and
`PITWALL_PER_REQUEST_MAX_USD`, default 10 USD, caps one lease), and appends every serve, refusal,
stop, and failure to an owner-only `audit.jsonl` in the state directory. See
[personal serving](docs/operator/personal-serving.md) for what `pitwall setup` changes on your
machine, where state lives, and how to clean up. Setting `DATABASE_URL` does not switch this path.
The registry backend for `serve`, `status`, and `stop` is chosen with `[personal] backend =
"registry"` in `pitwall.toml`.

### Run the broker locally

This path uses `uv`, Docker, and the first-class CLI. It creates local Postgres and Redis services,
applies migrations, seeds a demo capability and provider from [`seed/`](seed), and finishes with a
dry-run `POST /v1/inference`. A dry run exercises registry lookup and routing without live GPU
discovery or paid RunPod work. The values below are fake placeholders, written to
`.env.quickstart.local`, which Git ignores (`.env.*.local`):

```bash
git clone --branch v0.3.0a1 https://github.com/Buckeyes22/pitwall.git
cd pitwall
uv sync --frozen --extra dev --python 3.14.7
docker compose -f docker-compose.testinfra.yml up -d --wait

cat > .env.quickstart.local <<'EOF'
DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test
REDIS_URL=redis://127.0.0.1:6380/0
RUNPOD_API_KEY=local-dry-run-key
PITWALL_ADMIN_SECRET=local-admin-secret
PITWALL_API_TOKEN=local-api-token
EOF
set -a; . ./.env.quickstart.local; set +a

uv run pitwall db migrate
uv run pitwall init --non-interactive
```

`pitwall init` creates or updates the `embedding.demo` capability and the `demo-runpod-lb` provider
from the committed [`seed/capabilities.yaml`](seed/capabilities.yaml) and
[`seed/providers.yaml`](seed/providers.yaml), then marks the provider `healthy`. To seed different
values, use `pitwall init --from-seed path/to/seed-dir` or flags such as `--capability-name`,
`--endpoint-id`, `--provider-type`, `--region`, `--gpu-class`, and `--per-second-active`.

In a second terminal, start the API from the same checkout. It loads the same file:

```bash
cd pitwall
set -a; . ./.env.quickstart.local; set +a
uv run pitwall-api
```

With `PITWALL_API_TOKEN` exported, every route except the health checks needs
`Authorization: Bearer $PITWALL_API_TOKEN`, including the `/docs` page. To browse `/docs` on
loopback, start the API with `env -u PITWALL_API_TOKEN uv run pitwall-api` instead. The API listens
on `127.0.0.1:8080`; if that port is taken, start it with `PITWALL_API_PORT=<port>` and use that
port in the command below.

Then run the smoke command printed by `pitwall init`, or this equivalent command:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/inference \
  -H 'Authorization: Bearer local-api-token' \
  -H 'Content-Type: application/json' \
  -d '{"capability":"embedding.demo","texts":["hello"],"dry_run":true}'
```

The response contains `result.dry_run=true` plus a payload-free route plan with the selected
provider and structured cost. For a complete RunPod topology, use the default-plan
[`runpod-onboard` workflow](docs/operator/runpod-onboarding.md). Applying resources or sending real
inference remains an explicitly authorized live operation.

## Services

Pitwall has five console entry points in [pyproject.toml](pyproject.toml). The MCP servers and the
gateway start from the `pitwall` command. `pitwall --help` lists every command, and the
[CLI reference](docs/sdlc/18-cli.md) documents each one.

| Command | Role | Docs |
| --- | --- | --- |
| `pitwall-api` | REST API for capabilities, inference, leases, jobs, OpenAI proxy, and admin routes | [REST API](docs/sdlc/02-api-rest.md) |
| `pitwall-reconciler` | Workload, lease, health, idempotency, and cost convergence loop | [Reconciler](docs/sdlc/10-reconciler-lifecycle.md) |
| `pitwall-webhook` | Inbound RunPod webhook receiver | [Webhooks](docs/sdlc/09-webhooks.md) |
| `pitwall-cost-exporter` | Prometheus cost and health metrics | [Observability](docs/sdlc/13-observability.md) |
| `pitwall` | The operational CLI: database, RunPod resources, MCP servers (`pitwall mcp serve broker`, `pitwall mcp serve channel`), gateway, workbench, and agents | [CLI](docs/sdlc/18-cli.md) |

Container and deployment details are in [deployment](docs/sdlc/19-deployment.md).

## Documentation

- [System overview](docs/sdlc/00-overview.md) and [architecture](docs/sdlc/01-architecture.md) are
  the place to start. The [SDLC index](docs/sdlc/README.md) lists all 26 source-grounded subsystem
  docs, from the REST API and MCP server to routing, cost and budget, leases, security, and testing.
- [Agent Routing](docs/agents/routing-readme.md): shims, profiles, workflows, run records, and
  doctor. The [orchestrator channel](docs/agents/orchestrator-channel.md) covers ask, answer, and
  steer.
- [Operator guides](docs/operator/personal-serving.md): personal serving, RunPod onboarding,
  budget limits, troubleshooting, and the [Pi workbench](docs/operator/pi-workbench.md).
- [Support matrix](docs/support-matrix.md): what each provider adapter supports and what has been
  verified live.

## Testing and Quality

The common local checks are below. `make test`, `make test-int` after `make up`, `make docs-check`,
and the other gates are listed in [CONTRIBUTING.md](CONTRIBUTING.md#quality-gates).

```bash
uv run pytest -q -n auto -m "not integration and not slow"
docker compose -f docker-compose.testinfra.yml up -d
PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test \
PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/0 \
  uv run pytest -q -m integration
uv run pytest -q -m "security and not fuzz" tests/security
uv run pytest -q -m fuzz tests/security
```

The test program covers unit, property, integration, API contract, concurrency, chaos, security,
mutation, performance, and release-readiness lanes. See the
[testing strategy](docs/sdlc/17-testing-strategy.md) for markers, gates, and release tiers.
[ci.yml](.github/workflows/ci.yml) runs linting, type checks, security checks, tests, and
integration coverage on GitHub Actions, and
[release-readiness.yml](.github/workflows/release-readiness.yml) runs the public-alpha readiness
lane.

## Configuration

The API, MCP server, and reconciler fail closed when required runtime variables
are missing.

| Env var | Required for | Purpose |
| --- | --- | --- |
| `RUNPOD_API_KEY` | API, MCP, reconciler, RunPod operations | RunPod credential used for outbound calls |
| `DATABASE_URL` | API, MCP, DB CLI, reconciler, exporter | Postgres connection string |
| `REDIS_URL` | API, MCP, reconciler | Redis/arq queue connection string |
| `PITWALL_ADMIN_SECRET` | Admin routes; any non-loopback API bind | Constant-time shared secret for `/v1/admin/*`; without it admin routes answer 401, and a non-loopback API bind also needs `PITWALL_API_TOKEN` |
| `PITWALL_WEBHOOK_SECRET` | Webhook receiver (always) | HMAC secret for inbound RunPod webhook verification; the receiver refuses to start without it |
| `PITWALL_WEBHOOK_ENCRYPTION_KEYS` | Outbound webhook subscriptions | JSON mapping key versions to URL-safe base64 32-byte AES-GCM keys |
| `PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY` | Outbound webhook subscriptions | Key version used for new and rotated signing secrets |
| `PITWALL_API_TOKEN` | Non-loopback API; recommended locally | All-scopes operator bearer token required on every non-health route |
| `PITWALL_ENDPOINT_KEY` | Authenticated registry pod serving | Independent model endpoint credential; required for `serve` pod launches, injected only at launch time |
| `PITWALL_API_SCOPED_TOKENS` | Delegated API callers | JSON object mapping opaque tokens to `read`, `spend`, `lease:mutate`, `webhook:admin`, and/or `server:admin` scopes |
| `PITWALL_INBOUND_RATE_LIMIT` | API abuse control | Defaults to `120/60s`; throttles callers with `429` + `Retry-After`; set `off` only for loopback development |
| `PITWALL_API_MAX_BODY_BYTES` | REST API | Defaults to 8 MiB and bounds fixed-length and chunked request bodies |
| `PITWALL_API_MAX_CONCURRENCY` | REST API process | Defaults to 100 in-flight Uvicorn connections/tasks |
| `PITWALL_WEBHOOK_MAX_CONCURRENCY` | Webhook process | Defaults to 50 in-flight Uvicorn connections/tasks |
| `PITWALL_COST_EXPORTER_MAX_CONCURRENCY` | Metrics process | Defaults to 20 in-flight Uvicorn connections/tasks |
| `PITWALL_CLOUD_WORKER_IMAGE` | Warm-volume and generated template flows | Operator-supplied and independently reviewed RunPod pod image; Pitwall does not publish one |

Optional settings cover budgets, tracing, R2 log staging, lease TTLs, RunPod registry auth, audit
parameters, and service ports. See [.env.example](.env.example) and
[core models and config](docs/sdlc/16-core-config.md).

## Security and Trust Model

Read the project security policy in [SECURITY.md](SECURITY.md).

Pitwall is a **single-operator** control plane. There is no tenancy or ownership model: one
operator, one RunPod key, one shared budget.

**Non-loopback API startup fails unless API and admin credentials are configured.**
`PITWALL_API_TOKEN` is the all-scopes operator token. Delegated clients can instead receive tokens
from `PITWALL_API_SCOPED_TOKENS`, restricted to read, spend, lease mutation, webhook administration,
or server administration. Missing credentials return 401, and a valid token without the required
scope returns 403. Loopback-only development may run without bearer auth and logs an explicit
warning. The inbound limiter defaults to `120/60s` and can be disabled only by an explicit setting.

**Administrative operations use two gates when API bearer auth is enabled:** the bearer token must
grant `server:admin`, and `X-Pitwall-Secret` must match `PITWALL_ADMIN_SECRET`. Inbound webhook HMAC
is required whenever its receiver binds beyond loopback. The canonical Compose stack requires both
admin and webhook secrets.

**Bind to a private interface** (`127.0.0.1` default). Terminate TLS at a trusted reverse proxy
before any network exposure. Do not expose the webhook receiver or cost exporter directly to the
public internet.

**Personal serving is outside the broker's controls.** `pitwall serve` has no database, so the
broker's budget gate, pre-spend payload scan, and config audit do not apply. It enforces a local
monthly budget and per-lease cap and keeps a local audit log instead. There is no payload to scan,
because harnesses send requests to the pod directly and never through Pitwall. See
[SECURITY.md](SECURITY.md#personal-serving).

**Unauthenticated MCP is restricted to local stdio.** Pitwall rejects every network MCP transport,
and authenticated HTTP MCP is not an alpha feature. See [SECURITY.md](SECURITY.md#mcp-server).

**Agent Routing starts local harness CLIs with your user authority.** Review generated code, child
output, profile and workflow documents, and lifecycle hooks as local command-execution inputs.
Security reports for the broker and Agent Routing use this repository's single
[private reporting policy](SECURITY.md). The [agent threat model](docs/agents/SECURITY.md) adds the
Agent Routing specifics.

## Project Layout

```text
src/pitwall/          api, mcp, routing, cost, leases, db, reconciler, gateway, agents, workbench
db/migrations/        ordered SQL migrations
tests/                unit, integration, API, property, security, chaos, perf, release tests
docs/sdlc/            source-grounded technical documentation
docs/agents/          Agent Routing documentation
plugins/              Claude Code, Codex, and GitHub Copilot CLI plugin bundles
docker/  config/      service images; Prometheus configuration and the gateway catalog
dashboards/           Grafana dashboard definitions
scripts/  tools/      release and mutation-score tooling; policy guards
```

A workstation that ran the standalone Agent Routing tool runs `pitwall agents migrate` once. See
the [migration guide](docs/operator/agents-migration.md).

## License

Pitwall, including Agent Routing, is licensed under [Apache-2.0](LICENSE). The MIT terms under which
Agent Routing was released before it joined Pitwall are reproduced in [NOTICE](NOTICE).
[docs/agents/LICENSE](docs/agents/LICENSE) keeps the historical MIT file.

## Contributing

Contributions are welcome under the guidelines in [CONTRIBUTING.md](CONTRIBUTING.md). Testers start
with the agent-guided QA program in [qa/README.md](qa/README.md). This project follows the
[Code of Conduct](CODE_OF_CONDUCT.md), and the [Agent Routing contribution
guide](docs/agents/CONTRIBUTING.md) adds the specifics for that area.

## Legal

### Trademark and Non-Affiliation

Pitwall is an independent open-source project. It is not affiliated with, endorsed by, sponsored
by, or approved by RunPod, Vast.ai, Together AI, Lambda, OpenAI, Anthropic, Google, GitHub, or any
other provider, model vendor, or tool named in this repository. Those names are trademarks of their
respective owners and are used only as interoperability references, to identify the services and
CLIs Pitwall works with. The Apache-2.0 license grants no rights to any third-party trademark. See
[NOTICE](NOTICE).
