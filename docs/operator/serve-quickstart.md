# Serve a model: operator quickstart

## Personal serving

With a RunPod credential and no database, Pitwall manages local state and a
pod with a best-effort self-termination safeguard. Set up the endpoint key and
routing integration first:

```console
pitwall setup
```

Then serve a model, check the route, send a prompt through the shim, and stop
the pod when finished:

```console
pitwall serve --model ornith-ai/Ornith-1.5-35B-A3B-GGUF --gpu-class "NVIDIA GeForce RTX 3090" \
  --ttl-minutes 45 --max-usd-per-hour 1.00 --route ornith
pitwall status
pitwall agents dispatch route ornith prompt.md
pitwall stop ornith
```

`--max-usd-per-hour` is required and `--ttl-minutes` defaults to 60. The pod
carries its own deadline, and the route uses `PITWALL_ENDPOINT_KEY` for
authentication.

## With a database configured

This registry-backed path from a packaged catalogue entry to an
OpenAI-compatible endpoint needs Postgres, Redis, and the Pitwall API. Select the
registry backend with `[personal] backend = "registry"` in `pitwall.toml`; setting
`DATABASE_URL` alone leaves `pitwall serve`, `status`, and `stop` on the personal
backend, except that `--plan-only` needs neither.
Catalogue inspection, fit, and plan/dry-run previews are not live
verification: only a successful paid launch followed by the endpoint checks
below proves that a pod is serving the requested model.

### Catalogue

List the packaged dossiers. This is read-only and does not need a RunPod key.

```bash
uv run --frozen pitwall models list
```

Use either `org/model` or dossier-style `org--model` for `<model>`. To inspect
one dossier, use `pitwall models show <model>`; its `--json` option
is available when machine-readable output is useful.

### Fit

Choose a variant and GPU using the catalogue's hardware and cost estimate.
This is read-only; it needs RunPod pricing access when configured, but it does
not launch a pod. `<variant>` is optional and defaults to the published
variant; `<ttl-minutes>` is optional and defaults to 120.

```bash
uv run --frozen pitwall models fit <model> \
  --variant <variant> --ttl-minutes <ttl-minutes> --cloud secure
```

`--cloud` accepts `secure` or `community`. If pricing is unavailable, the
output says `unpriced`; a live launch must then provide `--rate-per-second`.

### Dry run

Use `--plan-only` for a database-free catalogue plan. It does not create a
capability, provider, lease, or pod, and does not require `DATABASE_URL`.
It reports the resolved image, exact server arguments, fit, timeout, estimate,
and whether pricing is `live` or `fallback`.

```bash
uv run --frozen pitwall serve --plan-only \
  --model <model> --variant <variant> --gpu-class <gpu-class> \
  --gpu-count <gpu-count> --engine <engine> --ttl-minutes <ttl-minutes>
```

`--gpu-count` defaults to 1. `--engine` accepts `vllm`, `llama.cpp`, or
`sglang`; `--gpu-class` may be a canonical RunPod name or a supported legacy
alias. `--plan-only` cannot be combined with `--capability`.

For a registry-backed validation preview, use `--dry-run` with the capability
and `DATABASE_URL`. It validates the launch against registry state but does
not launch a pod. The two preview modes are not proof that a real image starts
or that the model answers requests.

```bash
uv run --frozen pitwall serve --capability <capability> \
  --model <model> --variant <variant> --gpu-class <gpu-class> \
  --ttl-minutes <ttl-minutes> --dry-run
```

Both modes also support `--json`. Catalogue selection can be overridden with
`--image <image>`, `--served-model-name <served-model-name>`,
`--template-id <template-id>`, `--datacenter <datacenter>`,
`--container-disk-gb <container-disk-gb>`, repeatable `--env KEY=VAL` and
`--start-arg ARG`, plus `--idempotency-key <idempotency-key>`. `--gated` is
needed for gated model credentials. These flags are accepted by
`pitwall serve --help` (with the registry backend selected); use the help output to
confirm the current details.

### Serve

This is the paid step. It needs a read-write RunPod key, `DATABASE_URL`, and a
budget that permits the launch. It creates or replays a lease and waits for
the serving pod's readiness/model-identity check.

```bash
uv run --frozen pitwall serve --capability <capability> \
  --model <model> --variant <variant> --gpu-class <gpu-class> \
  --gpu-count <gpu-count> --engine <engine> --ttl-minutes <ttl-minutes>
```

If fit reported `unpriced`, add `--rate-per-second <usd-per-second>`. The
result contains `lease_id`, `expires_at`, `model_id`, and `proxy_base_url`.
Use the returned `model_id` for clients: it is the identity exposed by the
server and may differ from the requested catalogue ID.

For a registered self-hosted endpoint, the same capability-only request works from the CLI or
the `pitwall_serve_model` MCP tool. It warms the model and never creates, renews, or stops a lease.
The result has `provider_kind: self_hosted` and a null `lease_id`. `created` means the model was
non-resident before this warm; residency remains revocable. Pitwall emits `lease.ready` with a
null `lease_id` so existing routing consumers receive the same readiness signal.

Agentic serving requires `--enable-auto-tool-choice` and `--tool-call-parser <family>` in the
model server's launch template. Without both flags, health checks can pass while tool-bearing
requests fail.

The routing hookup is optional. On the same host as `pitwall agents`, append
`--route <route>` to the paid serve command; Pitwall adds or refreshes the route
after readiness. `PITWALL_ROUTING_CLI` selects the executable and defaults to
`pitwall agents`. The `PITWALL_API_TOKEN` inherited by that process needs `read`
and `spend`; Pitwall performs every automated stop, so routing does not need
`lease:mutate`.

If registration exits 3, the lease remains live. Run the printed recovery
command manually:

```bash
pitwall agents profiles add <route> --from-pitwall <capability> \
  --pitwall-url <pitwall-api-url>
```

### Manual renewal

An operator with the `lease:mutate` scope can explicitly extend a mutable lease
when an operator-directed extension is needed:

```bash
curl -fsS -X POST -H "Authorization: Bearer <pitwall-api-token>" \
  -H 'content-type: application/json' \
  -d '{"extends_minutes":60,"idempotency_key":"<unique-key>"}' \
  "<pitwall-api-url>/v1/leases/<lease-id>/renew"
```

This is an explicit operator action, separate from automatic activity renewal.
The mutation validates the lease identifier and optional idempotency key,
accepts 1–43,200 minutes, locks the lease, permits only mutable lease states,
and enforces the 30-day expiry horizon. It does not run the reconciler's
automatic budget, live-price, or kill-switch renewal checks. Use the returned
`expires_at`; a renewal that reaches the lifetime ceiling leaves the persisted
lease unchanged.

### Automation

Routing automation uses a token with `read` and `spend`; it never needs
`lease:mutate` because Pitwall performs every stop. Start the loopback receiver,
install it if desired, and subscribe it to the lease events as described in the
[receiver contract][receiver-contract]:

```bash
pitwall agents broker receiver
pitwall agents broker subscribe \
  --receiver-url http://127.0.0.1:8765/pitwall
```

Use `--idle-timeout-min`, `--max-usd-per-hour`, and `--renewal activity` on
`pitwall serve`. The routing receiver keeps stopped routes, so an opted-in route
can request a capped auto-serve on the next dispatch. Pitwall re-applies price,
budget, kill-switch, and lifetime gates before spending.

### Verify `/v1/models`

This is the paid/live verification step. The proxy path is fixed and the model
ID must match the `model_id` returned by `pitwall serve`.

```bash
curl -fsS -H "Authorization: Bearer <pitwall-api-token>" \
  "<pitwall-api-url>/v1/openai/<capability>/v1/models"
```

Expect an OpenAI-shaped response whose `data` includes
`{"id":"<model-id>"}`. A plan or dry-run response does not establish this.

### Verify chat

Still on the paid/live pod, verify the exact proxy route with a small request:

```bash
curl -fsS -H "Authorization: Bearer <pitwall-api-token>" \
  -H 'content-type: application/json' \
  -d '{"model":"<model-id>","messages":[{"role":"user","content":"Reply with exactly: pong"}]}' \
  "<pitwall-api-url>/v1/openai/<capability>/v1/chat/completions"
```

For routing-side probing, run `pitwall agents profiles probe <route>` explicitly;
probing is not automatic during dispatch.

### Stop

Stop the lease through the API so the provider is disarmed and the pod is
terminated. This is a paid/live control-plane action and requires a token with
the `lease:mutate` scope (not only the routing `read`+`spend` token):

```bash
curl -fsS -X POST -H "Authorization: Bearer <pitwall-api-token>" \
  "<pitwall-api-url>/v1/leases/<lease-id>/stop"
```

With the all-scope master token, use this shell CLI alternative:

```bash
PITWALL_API_TOKEN=<master-token> curl -fsS -X POST \
  -H "Authorization: Bearer $PITWALL_API_TOKEN" \
  "<pitwall-api-url>/v1/leases/<lease-id>/stop"
```

If only a RunPod pod ID remains after an operator-side incident, the separate
CLI recovery command is `pitwall terminate-pod --pod-id <pod-id>`;
it supports `--no-verify`, `--verify-timeout-s <seconds>`, and `--json`.

After stopping, a later routing probe should report the endpoint down. Do not
interpret fit, plan-only, dry-run, or a successful route registration as live
model verification.

[receiver-contract]: ../webhooks.md
  "Outbound signed webhook and lease-event contract"
