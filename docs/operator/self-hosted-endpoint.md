# Self-hosted OpenAI-compatible endpoints

Pitwall can route to an operator-managed OpenAI-compatible endpoint by attaching a
`self_hosted` profile to a `public_endpoint` provider. Pitwall probes, warms, budgets,
and reports this capability class. It does not manage the host, model downloads, GPU
drivers, swapper configuration, or telemetry.

## Tool-calling launch flags

Configure tool calling before exposing an endpoint to agents. For a vLLM-family launch,
include both flags and select the parser required by the model family:

```text
--enable-auto-tool-choice --tool-call-parser <family>
```

The parser is family-specific; do not copy a parser name from an unrelated model. Without
these launch flags, a stock server can return HTTP 400 for `tool_choice: auto` while health
and ordinary chat probes remain green. Declare `tool_calling: disabled` until the launch is
corrected, or `unknown` when it has not been verified.

## Provider profile

Keep the bearer value outside configuration. `api_key_env` names the environment variable
that contains it. The base URL may be supplied with
`${PITWALL_SELFHOSTED_BASE_URL}` or a loopback development value such as
`http://localhost:9292/v1`; never commit an instance-specific address or credential.

```yaml
capabilities:
  - name: llm.local
    class: llm
    cost_mode: zero
    served_model_id: <model-id>
providers:
  - name: self-hosted-local
    capability: llm.local
    provider_type: public_endpoint
    gpu_class: NVIDIA GeForce RTX 4090
    health_status: unknown
    config:
      openai_base_url: ${PITWALL_SELFHOSTED_BASE_URL}
      api_key_env: <api-key-env-name>
      self_hosted:
        readiness:
          kind: llama-swap
        cold_start_timeout_s: 600
        warmup:
          prompt: ping
          max_tokens: 1
        models:
          - id: <model-id>
            slot_group: gpu0
            exclusive: false
            vram_gb: 22
            context_length: 32768
            tool_calling: enabled
            tool_call_parser: <family>
        idle_unload_s: 1800
        strict_slots: false
        cost:
          mode: zero
          watts: 300
          usd_per_kwh: "0.20"
```

Apply the seed and ask the ordinary serve path to warm the registered capability:

```bash
uv run --frozen pitwall seed <seed-file>
uv run --frozen pitwall serve --capability <capability>
```

A capability-only serve request warms a self-hosted model and never creates, renews, or stops a
lease. `created` means the model was non-resident before this warm; residency remains revocable.
Pitwall emits `lease.ready` with a null `lease_id` so existing routing consumers receive the same
readiness signal. Its result also has `provider_kind: self_hosted`. The endpoint's own
`idle_unload_s` remains informational and Pitwall never renews, stops, or unloads it.

## Readiness oracle

Choose the strongest endpoint contract available:

- `llama-swap` reads `GET {origin}/running` and distinguishes `starting` from `ready` for each
  model. Prefer it for a compatible swapper.
- `openai-models` checks model presence in `GET {base}/models`. This is weak: some swappers
  list a model from load start, so presence does not prove inference readiness.
- `http-health` accepts a 2xx from `GET {origin}{path}`. It proves process liveness only, not
  per-model readiness.

Providers without a profile use `openai-models`. Warming is decided only by an oracle's
`starting` state or an in-flight first request within `cold_start_timeout_s`; latency never
decides warming versus unhealthy.

`PITWALL_ENDPOINT_PROBE_TIMEOUT_S` defaults to 10 seconds. It bounds one periodic probe, not
a cold model request. `cold_start_timeout_s` bounds a non-resident request; resident requests
keep the global timeout.

## Slots, residency, and cost

Residency is revocable and is re-read after every probe. A non-resident request is normally
admitted and may evict a slot peer. With `strict_slots: true`, Pitwall rejects a request that
would evict a resident exclusive model. A resident exclusive arrival may evict everything;
the reverse eviction need not be symmetric.

Weights plus the estimated context-length KV cache must fit within the configured usable memory:
`weights + kv_cache(context_length) <= gpu_memory_utilization * total card memory`.
When the dossier lacks architecture data, Pitwall uses a conservative 256 KiB/token KV
fallback. A real engine's KV usage can exceed this estimate, so treat a `fits` verdict as
advisory and leave operational headroom.
Cost is always a `Decimal` and never affects provider ranking. `mode: zero` estimates zero
unless both watts and electricity price are present, in which case Pitwall reports an energy
estimate.

## Runaway consumers

Apply mitigations in this order:

1. Give the client-side dispatch supervisor a timeout derived from `cold_start_s.p95`.
2. Rate-limit the consumer token at the reverse proxy.
3. Monitor fixed-cadence 4xx signals by token and capability.
4. Fix the retrying client.

`PITWALL_SATURATION_WINDOW_S` defaults to 60 seconds and
`PITWALL_SATURATION_4XX_THRESHOLD` defaults to 30. A swapper concurrency cap and HTTP 429
bound parallel overload only; they do not stop a serial retry loop. Warming,
misconfiguration, and saturation never trip provider cooldown.

## Agent context

`context_length` reports the engine launch setting. A model may fit and support tools but
still be unusable by an agent whose own client context budget is too small. Pitwall cannot
repair client-side truncation or planning limits server-side.

## Opt-in live verification

The live test is skipped unless both `PITWALL_SELFHOSTED_BASE_URL` and
`PITWALL_SELFHOSTED_API_KEY_ENV` are set. The second value is the name of the environment
variable containing the bearer, never the bearer itself. CI sets neither. Evidence is written
under the gitignored `artifacts/selfhosted/` runtime directory and must never be committed.

Stage the normal run with a cold model whose load exceeds 330 seconds. Set the endpoint to
`${PITWALL_SELFHOSTED_BASE_URL}`, set `PITWALL_SELFHOSTED_API_KEY_ENV` to the name of the
environment variable holding the bearer, and set that named variable separately. Then run:

```bash
uv run --frozen pytest -q -m live --run-live tests/live/test_selfhosted_endpoint.py
cp artifacts/selfhosted/live-evidence.json artifacts/selfhosted/live-evidence-normal.json
```

`--run-live` selects the self-hosted live test; it does not authorize RunPod control-plane
egress. The RunPod DNS guard remains installed unless a separate explicit RunPod-live environment
gate is truthy.

The normal artifact must contain `survived_global_timeout: true` and readiness `ready`. Copy it
out as shown before the next stage overwrites the runtime artifact. Never put the bearer directly
in `PITWALL_SELFHOSTED_API_KEY_ENV`, provider configuration, commands, or artifacts.

Run the mutually exclusive empty-catalogue stage separately after staging the endpoint with no
models:

```bash
uv run --frozen pytest -q -m live --run-live tests/live/test_selfhosted_endpoint.py
cp artifacts/selfhosted/live-evidence.json artifacts/selfhosted/live-evidence-empty.json
```

That artifact records `empty_catalogue: true` and `staged_scenario: empty_catalogue`. It does not
stand in for the normal cold-load evidence. Copy both artifacts out for release evidence, then
leave `artifacts/selfhosted/` uncommitted. No endpoint identity or model ID belongs in the repo.
