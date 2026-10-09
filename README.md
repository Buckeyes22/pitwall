# Pitwall

[![CI](https://github.com/Buckeyes22/pitwall/actions/workflows/ci.yml/badge.svg)](https://github.com/Buckeyes22/pitwall/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.14-blue.svg)](pyproject.toml)

Pitwall provides GPU provisioning, inference routing, and coding-agent dispatch in a single
Python distribution. It is designed for a single operator managing cloud compute, model access,
and agent execution across multiple providers and harnesses.

The broker checks estimated costs against monthly budgets and per-request limits before
authorizing paid work. Agent Routing dispatches tasks to configured model and harness profiles
and maintains execution records. Personal model serving uses a separate RunPod workflow with
local budget and lease controls.

> **Project status:** Pre-1.0, preparing for the first public alpha. APIs and configuration may
> change between minor versions. The [support matrix](docs/support-matrix.md) identifies supported
> capabilities, deferred features, and provider paths verified with live services.

[Capabilities](#capabilities) · [Architecture](#architecture) · [Getting started](#getting-started) ·
[Configuration](#configuration) · [Security](#security-and-trust-model) · [Documentation](#documentation)

## Capabilities

The distribution includes five components:

| Component | Interface | Responsibilities |
| --- | --- | --- |
| **Broker** | REST API, CLI, Textual console, broker MCP | Capability-based inference, embeddings, and compute; cost admission, provider routing and fallbacks, pod leases, audit records, and an emergency kill switch. |
| **Agent Routing** | `pitwall agents` | Dispatch individual prompts or dependency-ordered workflows to 14 agent harnesses; manage profiles, isolated worktrees, and run records. |
| **MCP servers** | `pitwall mcp serve broker` and `pitwall mcp serve channel` | Expose 81 broker tools and a separate orchestrator channel for subagent questions, answers, and steering. Both servers use local stdio. |
| **Pi workbench** | `pitwall workbench` | Launch the pinned Pi coding agent with configured profiles and isolated state. |
| **Free-tier gateway** | `pitwall gateway` | Provide a quota-aware catalog and loopback sidecar for free model pools configured with the operator's credentials. |

Provider adapters cover RunPod, Vast.ai, Together, and Lambda Cloud. Capabilities and live
verification vary by adapter; consult the [support matrix](docs/support-matrix.md) before choosing
a provider. The gateway makes configured free pools available to coding runs so they can use
available quota before paid compute.

## Architecture

### Broker

Requests reach the broker through REST, the CLI, the Textual console, or broker MCP. These
interfaces share the broker's request inspection, budget admission, routing, cost accounting,
audit, and execution layers.

```mermaid
flowchart TB
    clients["REST · CLI · console · broker MCP"] --> broker["Broker"]
    broker --> providers["Provider services"]
    broker --- postgres[("Postgres state")]
    broker --- redis["Redis queues"]
    reconciler["Reconciler"] --- postgres
    reconciler -.-> providers
```

Postgres stores operational state. Redis supports background job queues. The reconciler
converges workload state, runs health probes, expires leases, and aggregates costs. Solid
arrows show request routing; the dotted arrow shows reconciliation activity. Plain connections
indicate supporting infrastructure.

See the [architecture reference](docs/sdlc/01-architecture.md) for subsystem details and the
[reconciler lifecycle](docs/sdlc/10-reconciler-lifecycle.md) for background processing.

### Agent Routing

Agent Routing launches local harness CLIs through shims and configured profiles. Supported
harnesses include Codex, Claude Code, Kimi, OpenCode, Grok Build, and Qwen Code. Workflows add
dependency ordering, retries, verification, and resumable execution.

```mermaid
flowchart TB
    host["Orchestrating coding-agent host"] --> routing["Agent Routing"]
    routing --> harnesses["Dispatched harnesses"]
    routing -->|HTTP| broker["Broker API"]
    host <-->|Local stdio| channel["Orchestrator channel (MCP)"]
    channel <-->|Supported MCP clients| harnesses
```

The orchestrator channel supports communication during a dispatch: a subagent can ask a
blocking question, the orchestrator can answer it, and the orchestrator can steer a running task.
The channel is separate from broker MCP.

Agent Routing is part of the same distribution but communicates with the broker only over HTTP.
Its shim and hook entry points do not load the broker's web, database, or queue dependencies.
The HTTP connection shown above does not make a broker deployment a prerequisite for installing
Agent Routing.

There are **14 dispatch harnesses** and **13 channel registrations**. Channel support includes
the dispatch harnesses other than Pi and dsh, which have no MCP client, plus GitHub Copilot CLI.
The channel connection in the diagram applies only to supported MCP clients.

| Term | Meaning |
| --- | --- |
| **Harness** | An agent CLI that executes a task. |
| **Agent profile** | A `name@harness` binding configured in `pitwall.toml`. |
| **Dispatch** | One execution of a harness. |
| **Workflow** | A dependency-ordered set of dispatches. |

See [Agent Routing](docs/agents/routing-readme.md),
[workflows](docs/agents/workflows.md), and the
[orchestrator channel](docs/agents/orchestrator-channel.md).

### Cost and usage visibility

| Interface | Information |
| --- | --- |
| `pitwall cost`, `pitwall burn-rate`, `pitwall budget` | Persisted workload costs, spending rate, and budget consumption. |
| `pitwall usage` | Subscription usage for routed plans. |
| `pitwall-cost-exporter` | Prometheus cost and health metrics, with Grafana definitions in [`dashboards/`](dashboards). |

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

## Getting started

### Install Pitwall

Install the release wheel with [`uv`](https://docs.astral.sh/uv/) 0.12.2 or newer to add
`pitwall` and the service commands to your `PATH`. The `pitwall` package on PyPI is an
unrelated project; install from the release URL below:

```bash
uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl
```

If uv warns that `~/.local/bin` is not on your `PATH`, run `uv tool update-shell` and restart
your shell.

Alternatively, install from a source checkout:

```bash
git clone --branch v0.3.0a1 https://github.com/Buckeyes22/pitwall.git
cd pitwall
uv tool install --python 3.14 .
```

Choose the setup path that matches the work you want to run:

| Path | Runtime requirements | State and control scope |
| --- | --- | --- |
| [Agent Routing](#set-up-agent-routing) | Local agent harnesses and their configuration; no database required for installation. | Local dispatch and workflow records; routed-plan usage. |
| [Personal model serving](#serve-a-model-on-runpod) | RunPod credentials and local configuration; the default backend requires no database. | Local monthly budget, per-lease cap, lease deadline, and audit log. Inference goes directly to the pod. |
| [Broker services](#run-the-broker-locally) | Postgres, Redis, and broker runtime credentials. | Broker admission, routing, audit, reconciliation, and persisted workload costs. |

### Set up Agent Routing

Install the shims and host integrations:

```bash
pitwall agents install
```

The installer writes one shim per harness and `route-shim.sh` under `~/.claude/scripts/`,
installs the Claude Code, Codex, and GitHub Copilot CLI plugins, and registers the channel MCP
server. `pitwall agents uninstall` removes the files and integrations installed by this command.

The [installation guide](docs/agents/install.md) provides an agent-guided setup procedure and
uses `pitwall doctor` to report readiness. The
[Agent Routing overview](docs/agent-routing/README.md) introduces the available workflows.

#### Before you dispatch

Agent Routing runs the harness CLIs you have installed, as you, with your files, credentials,
and network. By default each CLI keeps its own sandbox and approval prompts. For unattended
runs, `export PITWALL_AGENTS_UNRESTRICTED=1` passes each CLI's bypass flag (for Codex,
`--dangerously-bypass-approvals-and-sandbox`), so the child can run commands without asking.
Kimi Code and dsh run only with that setting. See
[Agent Routing security](docs/agents/SECURITY.md).

#### Dispatch a prompt

Install a harness CLI, sign in to it yourself, write a prompt file, and dispatch it. Codex is
the example:

```bash
pitwall agents setup harnesses      # installs missing harness CLIs after you confirm; never logs in
codex login                         # authenticate Codex yourself
pitwall agents doctor --harness codex --live-auth
printf 'List the files in the current directory and summarize what this project does in three sentences.\nDo not modify any files.\n' > first-dispatch.md
pitwall agents dispatch codex first-dispatch.md
```

The doctor prints `codex read-only auth probe succeeded` (PASS) when Codex is signed in. The
probe runs `codex login status` and makes no inference request. Each dispatch ends its output
with `SHIM-DONE exit=<n>`.

To use a named profile, configure it as described in the
[routing guide](docs/agents/routing-readme.md), then dispatch to it by name. For a profile
named `ornith`:

```bash
pitwall agents dispatch route ornith first-dispatch.md
```

#### Execute a workflow

A workflow is a JSON document that assigns tasks to harnesses and models and defines their
dependencies, retries, and verification. Runs execute in the foreground, and stopped runs can
be resumed. See the [workflow format](docs/agents/workflows.md).

```bash
pitwall agents workflow run workflow.json --host copilot
pitwall agents workflow list
pitwall agents runs list
```

If the workstation previously used the standalone Agent Routing tool, run
`pitwall agents migrate` once. See the [migration guide](docs/operator/agents-migration.md).

### Serve a model on RunPod

The default personal-serving path provisions a RunPod model endpoint using local state. Set
`RUNPOD_API_KEY` before provisioning; this path does not require Postgres or Redis.

RunPod starts charging when the pod is created, before the model answers, and charges until the
pod is terminated. `pitwall stop <route>` terminates it. The pod also carries its own deadline
from `--ttl-minutes`, and RunPod treats that deadline as best effort. If `pitwall status` or the
RunPod console still shows a pod you did not expect, follow
[orphan cleanup](docs/operator/troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner).

First, inspect the model catalog and estimate GPU fit and lease cost:

```bash
pitwall models list
pitwall models fit ornith-ai/Ornith-1.5-35B-A3B-GGUF --ttl-minutes 45
```

With a RunPod key configured, `fit` uses live prices. Without a key, the pricing table displays
`unpriced`.

Initialize the local endpoint key, set a monthly budget, and provision a model endpoint:

```bash
pitwall setup
export PITWALL_MONTHLY_BUDGET_USD=20
pitwall serve --model ornith-ai/Ornith-1.5-35B-A3B-GGUF --gpu-class "NVIDIA GeForce RTX 3090" \
  --ttl-minutes 45 --max-usd-per-hour 1.00 --route ornith
pitwall status
pitwall stop ornith
```

Personal serving applies the following controls:

- A monthly budget is required before a lease can launch.
- A lease's maximum spend counts against that budget until the lease stops.
- `PITWALL_PER_REQUEST_MAX_USD` caps an individual lease and defaults to USD 10.
- The pod receives its own self-termination deadline.
- Serve requests, refusals, stops, and failures are appended to an owner-only `audit.jsonl`
  in the local state directory.

Harnesses send inference directly to the pod. Broker payload inspection, broker budget
admission, and broker configuration auditing do not apply to this local serving path.

Setting `DATABASE_URL` alone does not select the registry backend. To select that backend for
`serve`, `status`, and `stop`, configure `pitwall.toml` explicitly:

```toml
[personal]
backend = "registry"
```

The [personal-serving guide](docs/operator/personal-serving.md) documents setup changes,
state locations, backend configuration, and cleanup.

### Run the broker locally

This example starts Postgres and Redis, applies migrations, loads demo configuration, and
exercises a dry-run inference request. It requires `git`, `uv`, and Docker.

The API keys and secrets below are **local development placeholders**, written to
`.env.quickstart.local`, which Git ignores (`.env.*.local`). The dry run performs registry
lookup and routing without live GPU discovery or paid RunPod work.

#### 1. Initialize the environment

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

`pitwall init` creates or updates the `embedding.demo` capability and `demo-runpod-lb` provider
from [`seed/capabilities.yaml`](seed/capabilities.yaml) and
[`seed/providers.yaml`](seed/providers.yaml), then marks the provider `healthy`.

For different seed data, use `pitwall init --from-seed path/to/seed-dir` or options such as
`--capability-name`, `--endpoint-id`, `--provider-type`, `--region`, `--gpu-class`, and
`--per-second-active`. The committed seed directory is [`seed/`](seed).

#### 2. Start the API

In a second terminal, start the API from the same checkout. It loads the same file:

```bash
cd pitwall
set -a; . ./.env.quickstart.local; set +a
uv run pitwall-api
```

When `PITWALL_API_TOKEN` is set, all routes except health checks require
`Authorization: Bearer $PITWALL_API_TOKEN`. This includes `/docs`. For loopback-only
development, start the API with `env -u PITWALL_API_TOKEN uv run pitwall-api` instead if you
need to browse `/docs` without bearer authentication. The API listens on `127.0.0.1:8080`; if
that port is taken, start it with `PITWALL_API_PORT=<port>` and use that port below.

#### 3. Submit a dry-run request

From the first terminal, run the smoke command printed by `pitwall init`, or use this equivalent:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/inference \
  -H 'Authorization: Bearer local-api-token' \
  -H 'Content-Type: application/json' \
  -d '{"capability":"embedding.demo","texts":["hello"],"dry_run":true}'
```

The response contains `result.dry_run=true` and a route plan with the selected provider and
structured cost information. The plan does not include the request payload.

For a complete RunPod topology, follow the default-plan
[`runpod-onboard` workflow](docs/operator/runpod-onboarding.md). Applying resources and sending
live inference are separate operations that require explicit authorization.

#### 4. Connect a coding agent to broker MCP

Register broker MCP with Claude Code, Codex, or OpenCode. Use `--dry-run` to inspect the proposed
configuration changes without writing them:

```bash
pitwall mcp install claude-code --dry-run
pitwall doctor
```

To apply the configuration after reviewing the preview, run the install command without
`--dry-run`. Broker MCP requires `DATABASE_URL`, `REDIS_URL`, and `RUNPOD_API_KEY` in the
environment where the harness launches the server. `pitwall doctor` checks these prerequisites.

## Services

The distribution defines five console entry points in [pyproject.toml](pyproject.toml).
MCP servers, the gateway, Agent Routing, and the workbench are subcommands of `pitwall`.

| Command | Responsibility | Reference |
| --- | --- | --- |
| `pitwall` | Operational CLI for database management, RunPod resources, MCP servers, gateway, workbench, and agents. | [CLI](docs/sdlc/18-cli.md) |
| `pitwall-api` | REST API for capabilities, inference, leases, jobs, the OpenAI proxy, and administration. | [REST API](docs/sdlc/02-api-rest.md) |
| `pitwall-reconciler` | Background convergence of workload, lease, health, idempotency, and cost state. | [Reconciler](docs/sdlc/10-reconciler-lifecycle.md) |
| `pitwall-webhook` | Inbound RunPod webhook receiver. | [Webhooks](docs/sdlc/09-webhooks.md) |
| `pitwall-cost-exporter` | Prometheus cost and health metrics. | [Observability](docs/sdlc/13-observability.md) |

Run `pitwall --help` for the command list. See [deployment](docs/sdlc/19-deployment.md) for
container and service deployment details.

## Configuration

The API, broker MCP server, and reconciler fail closed when required runtime variables are
missing. Requirements depend on the selected operating path; the channel MCP server is separate
from broker MCP.

### Runtime and model serving

| Variable | Applies to | Purpose or default |
| --- | --- | --- |
| `RUNPOD_API_KEY` | Broker API, broker MCP, reconciler, RunPod operations | Required RunPod credential for outbound provider calls. |
| `DATABASE_URL` | Broker API, broker MCP, database CLI, reconciler, exporter | Required Postgres connection string. |
| `REDIS_URL` | Broker API, broker MCP, reconciler | Required Redis/arq queue connection string. |
| `PITWALL_MONTHLY_BUDGET_USD` | Personal serving | Required monthly budget for the local serving path. |
| `PITWALL_PER_REQUEST_MAX_USD` | Personal serving | Maximum cost of an individual lease; defaults to USD 10. |
| `PITWALL_ENDPOINT_KEY` | Authenticated registry pod serving | Independent model-endpoint credential required for serving pod launches; injected only at launch time. |
| `PITWALL_CLOUD_WORKER_IMAGE` | Warm-volume and generated-template flows | Operator-supplied, independently reviewed RunPod pod image. Pitwall does not publish one. |

### API access and request limits

| Variable | Applies to | Purpose or default |
| --- | --- | --- |
| `PITWALL_API_TOKEN` | Required for non-loopback API binding; recommended locally | Operator bearer token with all scopes. When enabled, authentication is required on every non-health route. |
| `PITWALL_API_SCOPED_TOKENS` | Delegated API callers | JSON mapping from opaque tokens to `read`, `spend`, `lease:mutate`, `webhook:admin`, and/or `server:admin` scopes. |
| `PITWALL_ADMIN_SECRET` | Administrative routes and non-loopback API binding | Shared secret checked in constant time for `/v1/admin/*`. Missing configuration causes admin routes to return `401`. |
| `PITWALL_INBOUND_RATE_LIMIT` | API request limiting | Defaults to `120/60s`; throttled requests receive `429` and `Retry-After`. Use `off` only for loopback development. |
| `PITWALL_API_MAX_BODY_BYTES` | REST API | Defaults to 8 MiB; limits both fixed-length and chunked request bodies. |
| `PITWALL_API_MAX_CONCURRENCY` | API process | Defaults to 100 in-flight Uvicorn connections/tasks. |

### Webhooks and metrics

| Variable | Applies to | Purpose or default |
| --- | --- | --- |
| `PITWALL_WEBHOOK_SECRET` | Inbound webhook receiver | Required HMAC secret for RunPod webhook verification. The receiver refuses to start without it. |
| `PITWALL_WEBHOOK_ENCRYPTION_KEYS` | Outbound webhook subscriptions | JSON mapping from key versions to URL-safe base64 32-byte AES-GCM keys. |
| `PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY` | Outbound webhook subscriptions | Key version used for new and rotated signing secrets. |
| `PITWALL_WEBHOOK_MAX_CONCURRENCY` | Webhook process | Defaults to 50 in-flight Uvicorn connections/tasks. |
| `PITWALL_COST_EXPORTER_MAX_CONCURRENCY` | Metrics process | Defaults to 20 in-flight Uvicorn connections/tasks. |

Additional settings cover budgets, tracing, R2 log staging, lease TTLs, RunPod registry
authentication, audit parameters, and service ports. See [.env.example](.env.example) and
[core models and configuration](docs/sdlc/16-core-config.md).

## Security and trust model

Pitwall is a **single-operator control plane**: one operator, one RunPod key, and one shared
budget. It does not implement tenancy or resource ownership. The project security policy is
maintained in [SECURITY.md](SECURITY.md).

### API authentication and authorization

Non-loopback API startup requires both `PITWALL_API_TOKEN` and `PITWALL_ADMIN_SECRET`.
The operator token grants all scopes. Delegated callers can use tokens from
`PITWALL_API_SCOPED_TOKENS` with narrower permissions. Missing credentials return `401`;
a valid token without the required scope returns `403`.

When bearer authentication is enabled, administrative operations require both:

- A bearer token granting `server:admin`.
- An `X-Pitwall-Secret` header matching `PITWALL_ADMIN_SECRET`.

Loopback-only development may run without bearer authentication and logs an explicit warning.
The inbound rate limiter defaults to `120/60s` and requires an explicit setting to disable it.

### Network deployment and webhooks

Bind services to a private interface; the default is `127.0.0.1`. Terminate TLS at a trusted
reverse proxy before network exposure. Do not expose the webhook receiver or cost exporter
directly to the public internet.

The inbound webhook receiver requires `PITWALL_WEBHOOK_SECRET` at startup and verifies RunPod
webhook signatures. The canonical Compose stack requires both admin and webhook secrets.

### Personal-serving boundary

The default `pitwall serve` path uses local state and does not require a database. Its local
monthly budget, per-lease cap, deadline, and audit log are separate from broker controls.
Harnesses send inference directly to the pod, so the broker does not inspect those payloads or
apply its budget gate and configuration audit to that path. See
[personal-serving security](SECURITY.md#personal-serving).

### MCP transport and local execution

Both MCP servers use local stdio. Network MCP transports are rejected, and authenticated HTTP
MCP is outside the alpha scope. See [MCP server security](SECURITY.md#mcp-server).

Agent Routing launches harness CLIs with the operator's user privileges. Treat generated code,
child output, profile and workflow documents, and lifecycle hooks as local command-execution
inputs that require review. The [Agent Routing threat model](docs/agents/SECURITY.md) covers
these risks in more detail. Broker and Agent Routing reports use the same
[private security reporting policy](SECURITY.md).

## Documentation

| Topic | References |
| --- | --- |
| System design | [Overview](docs/sdlc/00-overview.md), [architecture](docs/sdlc/01-architecture.md), and the [SDLC index](docs/sdlc/README.md), which lists 26 subsystem documents. |
| Provider support | [Support matrix](docs/support-matrix.md), including adapter capabilities and live verification. |
| Agent setup and routing | [Agent Routing overview](docs/agent-routing/README.md), [installation](docs/agents/install.md), and [routing reference](docs/agents/routing-readme.md). |
| Workflows and communication | [Workflows](docs/agents/workflows.md) and [orchestrator channel](docs/agents/orchestrator-channel.md). |
| Model serving and onboarding | [Personal serving](docs/operator/personal-serving.md) and [RunPod onboarding](docs/operator/runpod-onboarding.md). |
| Workbench and migration | [Pi workbench](docs/operator/pi-workbench.md) and [standalone Agent Routing migration](docs/operator/agents-migration.md). |
| Operations | [CLI reference](docs/sdlc/18-cli.md), [configuration](docs/sdlc/16-core-config.md), [observability](docs/sdlc/13-observability.md), and [deployment](docs/sdlc/19-deployment.md). |

The public repository history begins with a single commit. Commit hashes and pull request
numbers in plans, evidence, and changelogs may refer to earlier development history that is
not included in the public repository.

## Testing and quality

Common local checks are shown below. Additional commands, including `make test`, `make test-int`
after `make up`, and `make docs-check`, are documented in
[Contributing: quality gates](CONTRIBUTING.md#quality-gates).

```bash
# Unit and other fast tests
uv run pytest -q -n auto -m "not integration and not slow"

# Integration tests with local Postgres and Redis
docker compose -f docker-compose.testinfra.yml up -d
PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test \
PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/0 \
  uv run pytest -q -m integration

# Security and fuzz tests
uv run pytest -q -m "security and not fuzz" tests/security
uv run pytest -q -m fuzz tests/security
```

The test program includes unit, property, integration, API contract, concurrency, chaos,
security, mutation, performance, and release-readiness checks. The
[testing strategy](docs/sdlc/17-testing-strategy.md) defines markers, gates, and release tiers.

[CI](.github/workflows/ci.yml) runs linting, type checks, security checks, tests, and integration
coverage on GitHub Actions. The
[release-readiness workflow](.github/workflows/release-readiness.yml) runs the public-alpha
readiness checks.

## Project layout

| Path | Contents |
| --- | --- |
| `src/pitwall/` | API, MCP, routing, cost, leases, database, reconciler, gateway, agents, and workbench packages. |
| `db/migrations/` | Ordered SQL migrations. |
| `tests/` | Unit, integration, API, property, security, chaos, performance, and release tests. |
| `docs/sdlc/` | Technical subsystem documentation. |
| `docs/agents/` | Agent Routing documentation. |
| `docs/operator/` | Operator guides. |
| `plugins/` | Claude Code, Codex, and GitHub Copilot CLI plugin bundles. |
| `docker/`, `config/` | Service images, Prometheus configuration, and the gateway catalog. |
| `dashboards/` | Grafana dashboard definitions. |
| `scripts/`, `tools/` | Release tooling, mutation-score tooling, and policy guards. |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution requirements and quality gates.
Testers can start with the [agent-guided QA program](qa/README.md). This project follows the
[Code of Conduct](CODE_OF_CONDUCT.md); the
[Agent Routing contribution guide](docs/agents/CONTRIBUTING.md) provides additional guidance
for that component.

## License

Pitwall, including Agent Routing, is licensed under [Apache-2.0](LICENSE). The MIT terms under
which Agent Routing was released before joining Pitwall are reproduced in [NOTICE](NOTICE).
The historical MIT license is retained in [docs/agents/LICENSE](docs/agents/LICENSE).

## Legal

### Trademark and non-affiliation

Pitwall is an independent open-source project. It is not affiliated with, endorsed by,
sponsored by, or approved by RunPod, Vast.ai, Together AI, Lambda, OpenAI, Anthropic, Google,
GitHub, or any other provider, model vendor, or tool named in this repository. Those names are
trademarks or registered trademarks of their respective owners and are used only as
interoperability references, to identify the services and CLIs Pitwall works with. The
Apache-2.0 license grants no rights to any third-party trademark. See [NOTICE](NOTICE).
