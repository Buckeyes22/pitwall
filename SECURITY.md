# Security Policy

## Supported Versions

| Version | Supported          | Notes                                    |
|---------|--------------------|------------------------------------------|
| Default branch (`main`) | :white_check_mark: | Security fixes are made here |
| `v0.3.0a1` (2026-10-07) | :white_check_mark: | Current alpha. Fixes ship in the next alpha; no backports to older alphas. |
| Earlier alphas (`v0.1.0a1`-`v0.2.0a1`) | :x: | Upgrade to the current alpha. |
| Component tags (`gateway/v*`, `agent-routing/v*`) | :x: | Historical snapshots of the pre-merge packages; fixes land on `main` only. Releases are `v*` tags of the one `pitwall` package. |

Pitwall is pre-1.0. The API may change in backwards-incompatible ways between minor releases. When a release reaches end-of-life, its security advisories are archived but not backported.

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Private disclosure is preferred and expected. You can report vulnerabilities through:

- **GitHub Private Vulnerability Reporting** — use the _Security_ tab on the repository, then "Report a vulnerability". This routes directly to the maintainers without exposing the details publicly. No public security mailbox has been approved yet, so use this channel until one is listed here.

The project targets acknowledgement within 72 hours and a substantive update
within 14 days. These are best-effort targets, not an SLA.

For non-sensitive security questions or process questions, open a regular GitHub Discussion.

## Scope

This policy covers the following Pitwall components and their security boundaries:

| Component | What is in scope |
|-----------|-----------------|
| **FastAPI control plane** (`src/pitwall/api/`) | Admin auth middleware, all `/v1/admin/*` routes including kill-switch, budget gates, and audit trails |
| **MCP server** (`src/pitwall/mcp/`) | Admin tooling and any tool that exercises privileged operations |
| **Kill-switch** (`src/pitwall/api/admin/emergency.py`, `src/pitwall/api/admin/kill_switch.py`) | `POST /v1/admin/kill-switch`; atomic termination and verification; optional network-revocation path |
| **Inbound webhook HMAC** (`src/pitwall/webhook_receiver/`, `src/pitwall/webhook_dispatcher/signer.py`) | `POST /webhooks/runpod`; constant-time signature verification with bounded replay window |
| **SSRF allow-list** (`src/pitwall/resolver/provider_urls.py`) | `runpod_endpoint_id` validation via `^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$` at `resolver/provider_urls.py::_require_endpoint_id`; outbound URL construction for serverless_lb / serverless_queue / public_endpoint providers |
| **Secrets management** | Central redaction for bearer/admin/webhook credentials, authorization headers, and credential-bearing URLs; AES-GCM storage for outbound webhook secrets |
| **Pitwall Agent Routing** (`src/pitwall/agents/`, `plugins/`) | CLI/shim argument handling, local command execution, profile and run-state permissions, lifecycle hooks, harness installers, install and migrate integrity, plugin packages, local endpoint materialization, and the loopback receiver |
| **Free-tier gateway** (`src/pitwall/gateway/`) | Loopback-only bind, bearer authentication, request-size and rate limits, route-table key handling, and error redaction |

## Known Security Model

Pitwall has two operational modes with fundamentally different trust requirements:

### Single-operator, private deployment

Pitwall has one operator, one RunPod account, and one shared budget; it is not a multi-tenant
authorization system. Bearer scopes limit what a credential can do but do not add tenant ownership
or row-level isolation.

**Production deployments must:**

1. Set `PITWALL_API_TOKEN` and `PITWALL_ADMIN_SECRET`; non-loopback API startup refuses to proceed
   without both.
2. Set `PITWALL_WEBHOOK_SECRET`; non-loopback webhook startup refuses to proceed without it.
3. Use scoped bearer tokens for delegated callers and reserve the all-scopes token for operators.
4. Bind published ports to loopback or a private interface and terminate TLS at a trusted proxy.

See the full trust model and security controls in:

- [README — Security and trust model](README.md#security-and-trust-model)
- [`docs/sdlc/14-security.md`](docs/sdlc/14-security.md)

### Personal serving

`pitwall serve` (the Quick Start's personal path) runs without Postgres, so the broker's budget
gate, pre-spend payload inspection, and database config audit do not apply to it. In their place:

- **Budget:** serve refuses unless `PITWALL_MONTHLY_BUDGET_USD` is set, refuses a lease whose
  maximum spend (hourly price times TTL) exceeds `PITWALL_PER_REQUEST_MAX_USD`, and refuses when this
  month's recorded spend plus running leases' maximum spend plus the new lease would exceed the
  monthly budget. Settled costs are kept in `ledger.json` in the state directory.
- **Audit:** every serve, budget refusal, stop, and failure is appended to `audit.jsonl` (mode 0600)
  in the state directory.
- **No payload scan:** harnesses call the pod's endpoint directly with the endpoint key, so no
  request payload passes through Pitwall to inspect. Treat the pod endpoint and its key like any
  other model API credential.

### MCP server

The alpha MCP server is supported over local stdio only. Every network transport is rejected
because HTTP authentication is not implemented. Local process access is therefore the MCP trust
boundary.

### Pitwall Agent Routing

Agent Routing delegates work to locally installed provider CLIs. Those children inherit the
operator's filesystem, process, credential, and network authority subject to each provider's own
sandbox. By default each child CLI keeps its own sandbox and approval policy. Setting
`PITWALL_AGENTS_UNRESTRICTED=1` permits unattended child execution and bypasses those prompts.
Treat prompts, generated patches, provider output, lifecycle
hooks, profile files, and workflow definitions as untrusted input.

Run records and output are private local state, but can contain source or secrets printed by a
child. Prompt bodies are retained only when explicitly requested. No automatic retention period is
imposed; inspect and remove records with the documented `pitwall agents runs` commands. Plugins and
`pitwall agents install` expose the same local execution boundary as direct shims. Optional harness
installation is an explicit, confirmed mutation surface and never performs harness login.

Custom and self-hosted endpoints remain operator-supplied. API keys stay in named environment
variables; `pitwall.toml` stores the variable name, never the value. The optional Pitwall event
receiver binds only to loopback and writes a user service only when explicitly requested; enabling
that service is a separate action.

Credential roles are deliberately separate:

- `PITWALL_API_TOKEN` is the broker's all-scope server credential. Agent Routing does not read it.
- `PITWALL_AGENTS_API_TOKEN` is the `read`/`spend` client token for profile metadata
  and proxy traffic.
- `PITWALL_AGENTS_SUBSCRIPTION_TOKEN` is the `webhook:admin` token used only to
  create a receiver subscription.
- `PITWALL_WEBHOOK_SECRET` signs inbound broker webhooks and is only a legacy receiver fallback.
- `PITWALL_AGENTS_WEBHOOK_SECRET` is the preferred receiver verification secret;
  `PITWALL_WEBHOOK_SECRET_ENV` may explicitly name a different environment variable.

The detailed component threat model is maintained in
[`docs/agents/SECURITY.md`](docs/agents/SECURITY.md), but disclosure and
advisory coordination remain here under one Pitwall security home.

## Security Features

| Feature | Implementation | Default |
|---------|---------------|---------|
| API authorization | Constant-time opaque bearer lookup; explicit `read`, `spend`, `lease:mutate`, `webhook:admin`, and `server:admin` scopes | Required for non-loopback API; optional with warning on loopback |
| Admin auth | `server:admin` bearer scope plus constant-time `X-Pitwall-Secret` comparison | Required for non-loopback API; admin routes fail closed if absent |
| Inbound webhook HMAC | `X-Pitwall-Webhook-Signature` verified by `webhook_dispatcher/signer.verify`; bounded timestamp window | Required for non-loopback receiver; optional on loopback |
| SSRF allow-list | `runpod_endpoint_id` validated by `^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$` at `resolver/provider_urls.py::_require_endpoint_id` | Always on |
| Kill-switch | `POST /v1/admin/kill-switch`; ordered network deny → device revoke → compute terminate; `< 30s` budget; audit-logged | Gated by admin auth |
| Fail-closed boot | Refuses to start when the runtime variables required by a service are unset | Always on |

## Out of Scope

- Multi-tenant ownership isolation is not provided; report authorization bypasses against the documented bearer scopes, but not the absence of tenant-specific row ownership.
- Third-party services that Pitwall calls (RunPod API, Tailscale, Redis, PostgreSQL). Report issues with those services to their respective vendors.
- Social-engineering attacks against operators.
- Provider-model behavior, provider subscription policies, and documented unrestricted local
  execution are out of scope unless Agent Routing broadens authority beyond the documented
  boundary or mishandles arguments, credentials, state, or installer verification.
