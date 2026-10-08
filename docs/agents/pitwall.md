# Pitwall handoff

Pitwall can serve a model on a leased GPU, while model routing consumes the
resulting OpenAI-compatible endpoint as an ordinary named route. Routing does
not renew or tear down GPUs. It records the route, follows lease state, and can
request one capped serve during dispatch only when that route explicitly opts
in with `autoServe`.

## What Pitwall provides

1. **`serve` flow** (CLI `pitwall serve`, REST, and MCP tool `pitwall_serve_model`): given a capability name, a model id, a GPU class, and a TTL, Pitwall creates or reuses a template with a vLLM (or equivalent OpenAI-compatible) image and the model, launches a pod lease, waits for readiness (`GET <pod>/v1/models` lists the model), makes the capability's OpenAI proxy front that pod, marks the provider healthy, and returns `{capability, lease_id, expires_at, model_id, proxy_base_url}`.
2. **Proxy base URL:** `${PITWALL_API_URL}/v1/openai/<capability>/v1` speaks the OpenAI chat-completions protocol. Auth is `Authorization: Bearer $PITWALL_AGENTS_API_TOKEN` with the `spend` scope. `GET …/v1/models` passes through and lists the served model id.
3. **Capability metadata:** `GET ${PITWALL_API_URL}/v1/capabilities/<name>` (scope `read`) exposes `served_model_id` and, when a lease backs the capability, `active_lease: {lease_id, state, expires_at}` (ISO 8601 UTC). Routing uses these to fill `model` and `expiresAt`; when absent, `--model` is required and no expiry is recorded.
4. **Dead-lease semantics:** after expiry or teardown, requests through the proxy return `503` (`ProviderUnavailable`) unless the operator configured a fallback chain. Routing may revive the same capability under declared `autoServe` caps, but never silently substitutes another model.
5. **Environment names used by both sides:** `PITWALL_API_URL` is the client-side base URL. Agent Routing prefers scoped client credentials and accepts the broker's `PITWALL_API_TOKEN` only as a legacy fallback.

## Environment

Set `PITWALL_API_URL` to the Pitwall API base URL and
`PITWALL_AGENTS_API_TOKEN` to a bearer token. The token needs both the
`read` scope (capability metadata) and the `spend` scope (proxy traffic).
Pitwall's master `PITWALL_API_TOKEN` carries all scopes but is only a deprecated
fallback for Agent Routing. The route configuration stores only the
environment-variable name (`apiKeyEnv`), never the token value.

```bash
export PITWALL_API_URL=https://pitwall.example
export PITWALL_AGENTS_API_TOKEN=...
```

## The three commands

First serve a capability in Pitwall, then turn that capability into a route,
then dispatch its prompt. For example, this creates and uses a route named
`glimmer` for capability `llm.glimmer`:

```bash
pitwall serve --model meta-models/Muse-Glimmer-30B --gpu-class "<gpu-class>" --ttl-minutes <minutes> --max-usd-per-hour <usd> --route glimmer
pitwall agents dispatch route glimmer prompt.md
```

That is the default personal flow: `pitwall setup` once and `PITWALL_MONTHLY_BUDGET_USD` set first
(see [personal serving](../operator/personal-serving.md)). It needs no database, and `--route`
creates the route for you.

### Registry backend

With `[personal] backend = "registry"` in `pitwall.toml` (and `DATABASE_URL` set), `pitwall serve`
takes a capability name, and you turn the capability into a route yourself:

```bash
pitwall serve --capability llm.glimmer --model meta-models/Muse-Glimmer-30B --gpu-class <gpu-class> --ttl <ttl>
pitwall agents profiles add glimmer --from-pitwall llm.glimmer
pitwall agents dispatch route glimmer prompt.md
```

`pitwall agents profiles add --from-pitwall` reads the capability metadata, sets the endpoint to
`$PITWALL_API_URL/v1/openai/llm.glimmer/v1`, and records the served model and
lease expiry. Pass `--pitwall-url URL` to override `PITWALL_API_URL`; pass
`--model ID` when metadata does not provide a served model. It does not store
the preferred client token. As with other endpoint routes, run
`pitwall agents profiles sync` if the selected endpoint harness requires configuration sync.

`served_model_id` is the capability's *serving* name, which for Pitwall
catalogue models is a short dossier name (for example `qwen3.8-27b`), not the
Hugging Face id passed to `serve`. The route records exactly the string
Pitwall reports, and `pitwall agents profiles probe` compares it string-exactly against the
proxy's `/v1/models` listing — so always work from the returned
`served_model_id`/`model_id`, never from the id you requested.

## Automation

A Pitwall route records `origin.state` as `active`, `stopped`, or `unknown`.
The optional `autoServe` object is the per-route spend opt-in and may declare
`maxUsdPerHour`, `ttlMinutes`, `idleTimeoutMinutes`, and
`readyTimeoutMinutes`. For example:

```bash
pitwall agents profiles add glimmer --from-pitwall llm.glimmer \
  --auto-serve max-usd-per-hour=2.5,ttl=60,idle=20,ready-timeout=15
```

For REST-driven serves, the `[agents.profiles.pitwall.autoRegister]` table in `pitwall.toml` can
map a capability to the route name created when its first `lease.ready` event
arrives. These fields contain policy and provenance only; tokens and webhook
secrets remain in environment variables.

The signed webhook receiver is loopback-only. Create the subscription with a
token carrying `webhook:admin`, export the returned signing secret, and run the
receiver:

```bash
export PITWALL_AGENTS_SUBSCRIPTION_TOKEN=...
pitwall agents broker subscribe --receiver-url http://127.0.0.1:8765/pitwall
export PITWALL_AGENTS_WEBHOOK_SECRET=...
pitwall agents broker receiver --port 8765
```

The Pitwall API host must allow that exact target with
`PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST=127.0.0.1:8765`. The receiver verifies
`X-Pitwall-Signature`, rejects stale or replayed deliveries, and updates route
state for ready, renewed, and stopped lease events; an expiring lease is logged
as a notification rather than applied to the route. Its secret is read from the variable named by
`PITWALL_WEBHOOK_SECRET_ENV` when that indirection is explicitly set; otherwise it prefers
`PITWALL_AGENTS_WEBHOOK_SECRET` and accepts the broker's `PITWALL_WEBHOOK_SECRET` only as a
legacy fallback. It is never stored. Subscription administration similarly prefers
`PITWALL_AGENTS_SUBSCRIPTION_TOKEN`; route/proxy access uses
`PITWALL_AGENTS_API_TOKEN`. `receiver --install` writes a user service without enabling it
unless `--enable` is also passed. Where a
service is unsuitable, `pitwall agents broker watch --interval 5m` periodically
runs the pull fallback; `pitwall agents profiles refresh --all-pitwall` performs a
single pass. Synchronization timestamps live in
`${XDG_STATE_HOME:-~/.local/state}/pitwall/agents/pitwall-sync.json`.

Before dispatch, a healthy active Pitwall route makes no Pitwall metadata
request. An expired, near-expiry, stopped, or unknown route takes this
self-heal ladder under a per-route lock:

1. Refresh capability metadata and use a live lease if Pitwall reports one.
2. If no lease is live and `autoServe` exists, request one capability-only
   serve with the declared caps, wait up to `readyTimeoutMinutes`, refresh, and
   probe once before dispatching.
3. If spending was not opted into, or Pitwall refuses or cannot ready the
   serve, exit `78` without dispatching or silently rerouting.

Self-heal never renews or stops a lease and sends at most one serve request per
dispatch. A 422 refusal is reported as `not revived: <code>`; the pinned codes
are `cap_exceeded`, `price_unknown`, `budget_exhausted`,
`kill_switch_engaged`, and `no_serve_history`. For `no_serve_history`, serve the
capability once with `pitwall serve` so Pitwall has a
configuration to replay. Cap precedence is: routing declares, Pitwall
enforces. Route caps bound the request, while Pitwall remains the authority for
price, budgets, lifetime, and the kill switch.

## Liveness and expiry

The manual liveness command `pitwall agents profiles probe glimmer` sends a
bounded request to the route's `/models` endpoint. Dispatch self-heal also runs
one bounded probe after a stopped or unknown route becomes live; ordinary
healthy dispatches do not probe.

| Status | Meaning | Exit |
|---|---|---|
| `reachable` | The endpoint returned 200 and lists the configured model. | 0 |
| `warming` | The endpoint returned 200, lists the configured model, and reports it is still starting. | 0 |
| `model-missing` | The endpoint returned 200 but does not list the configured model. | 1 |
| `unauthorized` | The endpoint returned 401 or 403. | 1 |
| `expired` | `expiresAt` is already past, so no request is made. | 1 |
| `down` | The request timed out, could not connect, or returned a 5xx response. | 1 |
| `not-applicable` | The route has no endpoint and uses its harness configuration instead. | 0 |

Use `--json` when automation needs the status, HTTP status, listed models,
expiry, and remedy. `expiresAt` is an optional UTC timestamp in the form
`YYYY-MM-DDTHH:MM:SSZ`; Pitwall-backed route creation copies it from the active
lease. The resolver allows 60 seconds of clock skew. Self-heal runs before
resolution and either refreshes/revives the route or refuses with exit `78`
without a ledger row. The doctor keeps its route-staleness decision offline and
warns when a Pitwall lease is within 15 minutes of expiry but has no sidecar
update in 30 minutes. It probes only the receiver's loopback `/health` endpoint
after the sidecar shows that a receiver has run.

## Failure semantics

When a lease has died or been torn down, the Pitwall proxy returns `503`
(`ProviderUnavailable`). Model routing does not silently reroute that work to a
different model. Dispatch first follows the self-heal ladder above. If it exits
`78`, use the reported `not revived` code and `pitwall agents profiles probe
<name>` to diagnose the route; an operator can then renew or serve manually.
After a manual action, run `pitwall agents profiles refresh <name>` so the route's
model, lease id, and expiry metadata are current. Refresh re-reads the capability
(via the recorded `origin.url` unless `--pitwall-url` overrides it) and updates
only `model`, `expiresAt`, `origin.leaseId`, `origin.state`, and `endpoint.baseUrl` — the
route's seat, effort, args, env, and `apiKeyEnv` are preserved, unlike a
re-`add`, which replaces the whole entry. Renewal keeps the same `lease_id`
and only moves `expires_at`; a later `serve` on the same capability can
swap the served model, which refresh also picks up. Probe output includes the
route's `leaseId` so results can be correlated with Pitwall's lease records.
Streaming responses (`stream: true`) pass through the proxy unbuffered as SSE;
on a mid-stream upstream failure the stream ends with exactly
`data: {"error":"upstream stream failure"}` (not an OpenAI-shaped chunk — no
`choices`), which consumers should treat as the terminal failure marker.
Pitwall pins both behaviors in its consumer-contract tests.

## Budget and kill switch

Traffic sent through a Pitwall route remains proxied through Pitwall. Its budget
gates and kill switch therefore apply to the subagent traffic as well as to
other proxy clients. Model routing does not bypass those controls or attempt to
manage the GPU lifecycle.

## Limitations

- Each capability represents one served model.
- Lease teardown does not automatically remove the profile from `pitwall.toml`.
  Remove it explicitly when it is no longer useful.

Existing broker capabilities backed by an always-running LAN endpoint do not need a GPU lease. Dispatch refreshes their metadata and requires the expected model to appear in the broker proxy models response on every call. They retain an unknown lease state rather than claiming an active lease. A route previously observed as lease-backed remains stopped after lease loss; it cannot silently become an unleased route. Remove the old route and add it again to adopt a deliberately reconfigured capability under a new route identity. Routes rejected by older versions solely for lacking a lease may also need that remove/add step before dispatch. No automatic serve occurs without explicit autoServe caps.
