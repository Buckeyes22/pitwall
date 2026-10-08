# Self-hosted endpoints

Self-hosted OpenAI-compatible servers are first-class route targets. This guide
covers swapper-class endpoints: a front end that exposes a model catalog and
loads or unloads backing engines on demand.

New to this? [Attach a locally hosted model](attach-local-endpoint.md) walks
from "something is running somewhere" to a verified route, with a detector
script. This page is the reference behind it.

## The capability class

A swapper makes latency bimodal. A warm model may answer in seconds, while a
cold model can take about 85 seconds to load even when its weights are only
about 3 GB; engine startup, not weight transfer, dominates that measured cold
floor. Larger models push it further: a 27B INT8 checkpoint served tensor-parallel
across two GPUs measured **145 seconds**. Treat 85 seconds as a floor you cannot
go below, not as a typical value — size your client timeouts against the largest
model the endpoint will serve. A healthy cold request blocks until the engine
is ready and then returns HTTP 200. It does not return 503 while warming.

Warmth is not sticky. A request for a sibling can evict a warm model, which is a
different failure mode from a retry attaching to the same model's in-progress
load. Eviction direction and scope come from the operator's group
configuration, not a fixed law of the swapper. In the measured configuration,
an exclusive-group model evicted everything; reloading a non-exclusive model
afterwards left that evictor running.

Do not equate a slow response with a healthy start. A broken engine
configuration can also block, but for about 20 seconds, and then return HTTP
500 with `upstream command exited prematurely`. Healthy cold startup and broken
startup therefore begin with the same symptom—a wait—but end with opposite
verdicts. Clients must inspect readiness and the final response rather than
treating “slow” as “starting.”

## Serving models for agents

An endpoint that handles a small chat completion may still be unable to serve
an agentic CLI. There are three separate sizing and configuration layers:

1. Stock `vllm serve` rejects agentic tool calls with HTTP 400 unless it has
   `--enable-auto-tool-choice` and a model-family-specific
   `--tool-call-parser`. Choose the parser for the served model family.
2. Cap output at the client. For example, Qwen Code otherwise sends
   `max_tokens=32000`, which can consume a small model's entire native window.
3. Leave headroom for the CLI itself. Qwen Code's measured main request was
   149,914 bytes: 64 tool schemas plus a 38,871-character (about 39 KB) system
   prompt, or approximately **~35k tokens, larger than a native 32k window**.
   A bigger window does not rescue an untrimmed agent—trim the tool schemas.
   Qwen Code supports a workspace `.qwen/settings.json` `tools.core`
   allow-list.

Beware two misleading signals. Qwen Code first sends a roughly 1.9 KB
preflight request, so a successful small probe does not prove that its real
agentic request fits. Also, vLLM's “contains at least N input tokens” error
reports the overflow threshold (`window - max_tokens + 1`), not the actual
prompt size.

## Harness timeouts and knobs

The installed harnesses have materially different cold-start behavior. A
caution about this table: the defaults were read from installed code, but the
verdicts only became trustworthy after live cold starts — two of seven changed
when exercised (one predicted-safe harness aborted its first attempt early;
one predicted-borderline harness passed repeatedly). Exercise your own stack
against a real cold start before relying on a verdict.

| Harness | Default | Verdict | Knob |
|---|---|---|---|
| qwen 0.21.15 | 120 s to headers | borderline: passed an 85 s cold start ×4, but against a 145 s cold start the request aborted at **exactly 120.002 s** and only its retry layers carried it to success (181 s end to end) | env `QWEN_CODE_API_TIMEOUT_MS` (silently ignored if `generationConfig.timeout` set); retries 4×7 layered |
| goose 1.48.0 | 600 s | safe; no HTTP retry | `OPENAI_TIMEOUT` env or config.yaml |
| hermes 0.20.6 | 1800 s request BUT 90 s TTFB stale kill | borderline + spelling asymmetry (below) | `providers.<id>.stale_timeout_seconds` |
| cline 3.0.60 | none (hangs forever) | cold-safe; dead-server unbounded | `providers.json` `timeout` is a NO-OP for OpenAI-compatible (only Ollama honors it) |
| dsh 0.1.1-rc.2 | 600 s SDK, BUT the first attempt aborts at a deterministic ~60 s (57 ms spread over four live runs, including one against a 145 s cold start; `timeoutMs` does not reach it — verified with 900 s set) | cold-start survivable only via its 5 harness retries — a retry re-attaches to the in-flight load, so the retry count is load-bearing | settings.yaml `llm-pi-ai.providers.<id>.timeoutMs` (whole request only), `streamIdleTimeoutMs` (300 s inter-chunk) |
| pi 0.84.3 | 300 s global undici idle | safe | settings.json `httpIdleTimeoutMs` (global only; TUI picker caps at 5 min) |
| opencode 1.17.7 | none | cold-safe; dead-server unbounded | `provider.<id>.options.headerTimeout`/`chunkTimeout` (retryable) — never `timeout` (aborts NON-retryably); 5xx session retry has NO attempt cap |

At a realistic model size the retry layers stop being a nicety. Against a 145 s
cold start, measured end to end with the endpoint's access log as the witness:
qwen aborted at 120 s and succeeded on a retry (181 s), dsh aborted at 60 s and
succeeded on a retry (160 s), and pi alone rode the load out on a single request
(164 s). All three reported success to the caller; only the server log showed
that two of them had thrown a connection away first. If you reduce a harness's
retry count, re-test your cold path — for qwen and dsh the retry is the only
reason the dispatch completes.

For Qwen, raise `QWEN_CODE_API_TIMEOUT_MS` above the cold floor and remember
that `generationConfig.timeout` takes precedence. Goose's `OPENAI_TIMEOUT` and
dsh's `timeoutMs` bound a request; dsh's `streamIdleTimeoutMs` separately bounds
gaps between chunks. Pi offers only the global `httpIdleTimeoutMs`.

Hermes needs special care. Its locality test treats loopback, private-network
addresses, addresses in the RFC 6598 CGNAT range, and dotless hostnames
as local, disabling the stale detector and raising stream timeouts. A dotted DNS name for the same machine is
treated as remote and retains the 90-second first-byte kill. A localhost test
can therefore pass while the production spelling fails. In addition, Hermes 0.20.6 does not attach authorization to a non-loopback
OpenAI endpoint configured only through `OPENAI_API_KEY` and `OPENAI_BASE_URL`;
it reports HTTP 401 on stdout and exits 0. The shim recognizes this soft failure
and exits 77.

This is a configuration requirement rather than a defect: Hermes resolves
credentials from its own provider configuration, so the endpoint has to be named
there. `pitwall agents profiles sync` does that for you — Hermes is a config-materialization
harness like opencode, pi, cline and dsh:

```bash
pitwall agents profiles add <route> --model <id> --harness hermes --endpoint <endpoint>
pitwall agents profiles sync --harness hermes
```

The sync merges a managed `providers:` block into `~/.hermes/config.yaml`
(honouring `HERMES_HOME`), preserving anything you put there yourself, and
dispatch selects it with `--provider <route>` automatically. The block records
`key_env` — the *name* of your key variable — so the secret itself never reaches
the file. Unlike cline and dsh, Hermes holds many providers at once, so several
routes can target it. A loopback endpoint works without any of this.

Hermes also treats loopback, private-network and CGNAT addresses as "local"
while a dotted DNS name for the same machine counts as remote, which changes its
stale-connection timeout — test with the spelling you will actually use.

Cline has no OpenAI-compatible request timeout: the `providers.json` `timeout`
field is a no-op for this provider kind. Its lack of a timeout tolerates cold
starts but leaves a dead server unbounded.

OpenCode likewise has no default bound. More importantly, a vLLM context error
whose text mentions “maximum context length” can trigger compaction and an
unbounded retry of the same `max_tokens=32000`, at about three requests per
second. Put `limit: {"context": N, "output": N}` on the materialized model;
`pitwall agents profiles sync` writes route `limits` there automatically. For retryable transport
bounds use `headerTimeout` and `chunkTimeout`. Never use `timeout`: it aborts
non-retryably.

## Operator hardening

Bound runaway clients in this measured order:

1. Use a client-side dispatch supervisor timeout. The shims'
   `PITWALL_AGENTS_TIMEOUT_SECS` defaults to 1140 seconds and is the whole safety net for
   harnesses without an inner bound, notably OpenCode and Cline.
2. Put a requests-per-minute rate limit in the reverse proxy in front of the
   endpoint.
3. Monitor access logs for fixed-cadence 4xx responses, because the client may
   show no useful signal. Client-side timing in general cannot distinguish
   "rode out the cold start" from "aborted and retried": a dispatch that
   reports a clean 85-second success may have silently burned a failed
   60-second first attempt — only the endpoint's log shows the abort.
4. Fix the client configuration—the output cap and trimmed tool set are the
   root-cause remedy.

A per-model concurrency cap is still cheap protection against parallel
overload, but it is blind to a serial retry loop. With a concurrency limit of
2, six parallel requests produced 2×200 + 4×429; 25 serial requests all
produced 200 at 15.2 requests per second.

Treat probe scripts as processes, not just terminal commands. A probe left as
a background job after its foreground timeout can resume after configuration
changes and contaminate the next phase. Kill its process group and verify by
process name before declaring the probe phase closed.

## Discovery and liveness

Discover the catalog directly from an endpoint or from an existing route:

```bash
pitwall agents profiles discover --base-url http://localhost:9292/v1 --api-key-env MY_ENDPOINT_KEY --timeout 10
pitwall agents profiles discover <name> --timeout 10
```

`pitwall agents profiles discover` reports model IDs and, when the swapper exposes a
`/running`-style endpoint, whether each model is `ready`, `starting`, or
`not-loaded`. `pitwall agents profiles probe <name> --timeout 120` checks a
configured model and reports `warming` distinctly. `pitwall agents doctor
--probe-routes` opts the otherwise-offline doctor into route probes.

`/v1/models` is an instant catalog response, not a readiness oracle: it reports
a model's status as `loaded` from the moment a start is initiated, while the
engine may still be minutes from serving. Prefer `/running` state where
available. A failed launch can appear as `starting`, then disappear from
`/running`, and only later surface as a 5xx. On llama-swap, an upstream
passthrough such as `GET /upstream/<id>/v1/models` reaches the backing engine
and provides real server metadata.

For operator troubleshooting—not automation—tailing the engine log stream can
surface a fatal startup error about 7× faster than the eventual 5xx. It is a
bad machine signal: healthy starts emitted 16 ERROR-containing lines in the
first 8 seconds in the measured run, and streams replay buffered history from
previous runs. Sudden log silence is cruder but more meaningful: the dead
engine emitted about 0.8 KB versus about 63 KB from the live engine in 8
seconds.

## Route recipe

Add a bounded route, then discover and probe it before dispatch:

```bash
pitwall agents profiles add <name> --model <id> --base-url http://localhost:9292/v1 --api-key-env MY_ENDPOINT_KEY --limits context=32768,output=1024 --env QWEN_CODE_MAX_OUTPUT_TOKENS=1024
pitwall agents profiles discover <name> --timeout 10
pitwall agents profiles probe <name> --timeout 120
```

Run `pitwall agents profiles sync` for harnesses with managed configuration. See
[Agent profiles](routes.md) for resolution and materialization details and
[Provider CLI setup](harness-cli-setup.md) for harness installation.
