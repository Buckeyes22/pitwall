# System Architecture

## 1. Layered view

Pitwall is a layered control plane. Requests enter through a surface (REST or MCP), pass through a
routing → cost → audit → execution pipeline, and persist state to Postgres with Redis as the job
queue. Outbound effects (RunPod calls, webhooks) are the only things that leave the boundary.

```
                ┌──────────────────────────────────────────────────────┐
   clients ───► │  Surfaces                                             │
                │   REST API (pitwall-api)   MCP servers (pitwall mcp)  │
                │   CLI (pitwall)            background services         │
                └───────────────┬──────────────────────────────────────┘
                                │  (shared service layer)
        ┌───────────────────────┼───────────────────────────────────────┐
        │  Control / decision plane                                      │
        │   resolver ─► routing.production ─► cost.estimator ─► audit       │
        │   (provider URLs)   (Stage 1-4)   (per-sec/req/tok)  (19-check) │
        │                          │                                     │
        │                  cost.budget_gate  (advisory-lock admission)   │
        └───────────────────────┬───────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────────────────────┐
        │  Execution plane                                               │
        │   runpod_client (queue / lb / rest / graphql / templates)      │
        │   leases (pod lifecycle)   webhook_dispatcher (signed out)     │
        └───────────────────────┬───────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────────────────────┐
        │  State & reconciliation                                        │
        │   db.repository ─► Postgres   |   arq/redis (job queue)        │
        │   reconciler + workload_lifecycle   webhook_receiver (idemp.)  │
        └────────────────────────────────────────────────────────────────┘

   Cross-cutting: config (fail-closed boot) · security (admin auth / SSRF / HMAC /
   secret non-disclosure) · rate_limits · observability + cost.exporter · ops (kill-switch,
   retention, R2 cleanup)
```

## 2. Request data flow — inference

A `POST /v1/inference` (or the OpenAI proxy) flows through:

1. **Resolve** — look up the capability by name/id (`db.repository`), load its enabled providers.
2. **Plan** (`pitwall.routing.production:build_production_plan`) — Stage 1 hard-constraint filtering →
   Stage 2 health, cooldown, quota, and lockout gating → ranking by priority or by weighted
   cost and latency → fallback chain. Produces a `ProductionRoutePlan` with an ordered provider
   list. No network calls.
3. **Resolve URL** (`resolver.provider_urls`) — the chosen provider's outbound RunPod URL, with the
   `runpod_endpoint_id` SSRF allow-list enforced at the chokepoint.
4. **Estimate** (`cost.estimator`) — Decimal-exact USD estimate by cost mode (per-second /
   per-request / per-token), quantized to 6 dp.
5. **Admit** (`cost.budget_gate.try_launch`) — under a Postgres advisory lock, check the estimate
   against the per-request cap and month-to-date spend; insert the workload row (idempotent on
   `idempotency_key`). Rejection raises `BudgetRejected` (402).
6. **Execute** (`runpod_client`) — call the serverless queue/LB or OpenAI-compatible endpoint;
   stream SSE where applicable.
7. **Reconcile** — async jobs return via webhook (`webhook_receiver`, idempotent); the
   `reconciler` + `workload_lifecycle` converge terminal status. Sync calls persist inline within
   the sync-persist deadline.

`dry_run=true` runs steps 1-5 and stops before any paid call — the basis of the dry-run release
tier and the Locust load profile.

## 3. Persistence

**Postgres** (schema in `db/migrations/*.sql`, accessed via `db.repository`) holds capabilities,
providers, workloads, pod leases, webhook subscriptions + deliveries, cost/usage rollups, and the
kill log. JSONB columns carry config/payload blobs; NUMERIC carries money. Migrations are applied
in `discover_migrations` order (no runtime auto-migrate — discovery/drift only). See
`07-data-model-db.md`.

**Redis** backs the arq job queue for async job dispatch and webhook terminal-status processing.

## 4. Deployment topology

Five console entrypoints (`pyproject.toml [project.scripts]`) plus the MCP servers started by `pitwall mcp serve`, each a separate process/container:

| Service | Entrypoint | Role |
|---------|-----------|------|
| `pitwall-api` | `pitwall.api.__main__` | REST API (capabilities, inference, leases, jobs, admin) |
| `pitwall mcp serve broker` | `pitwall.cli.mcp` | MCP server over the same service layer (local stdio; `pitwall mcp serve channel` serves the Agent Routing channel) |
| `pitwall-reconciler` | `pitwall.reconciler.__main__` | state convergence loop |
| `pitwall-webhook` | `pitwall.webhook_receiver.__main__` | inbound RunPod callbacks |
| `pitwall-cost-exporter` | `pitwall.cost.__main__` | Prometheus cost metrics |

All share Postgres + Redis. See `19-deployment.md` for images/compose.

## 5. Cross-cutting concerns

- **Configuration / fail-closed boot** — the API/reconciler/webhook/cost-exporter call
  `require_runtime_env(...)` at import and refuse to boot (`os.EX_CONFIG`) without
  `RUNPOD_API_KEY` / `DATABASE_URL` / `REDIS_URL`. See `16-core-config.md`.
- **Security** — constant-time admin-secret gate on `/v1/admin/*`; `runpod_endpoint_id` allow-list
  (SSRF); mandatory inbound webhook HMAC; outbound signed delivery; secret non-disclosure. See
  `14-security.md`.
- **Rate limiting** — token-bucket admission (`rate_limits/`). See `11-rate-limiting.md`.
- **Observability** — Prometheus metrics + a dedicated cost exporter. See `13-observability.md`.
- **Operational safety** — the kill-switch severs Tailscale ACL → tagged devices → RunPod compute
  in <30s and logs every activation. See `15-operations.md`.

## 6. Design principles

- **Pre-spend safety** — release approval requires cost estimation, budget admission, and the
  19-check audit. `dry_run` short-circuits before spend.
- **Idempotency everywhere** — workload admission (idempotency keys), webhook ingestion
  (insert-or-skip), and terminate (404-as-success) are all idempotent.
- **Deterministic routing** — `build_production_plan` is pure (no I/O), making routing decisions reproducible
  and unit-testable.
- **Grounded outbound URLs** — every RunPod URL funnels through one resolver chokepoint that
  validates the endpoint id, so a hostile id can never redirect a credentialed call.
