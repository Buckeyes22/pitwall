# Attach a locally hosted model

You are running an LLM on your own hardware and want this router to dispatch to
it. This guide takes you from "something is running somewhere" to a verified
route, and it front-loads the failures that actually happen rather than the ones
that sound plausible.

If you are handing this to an agent, give it
[the skill](../../plugins/claude/skills/attach-local-endpoint/SKILL.md)
instead — same procedure, written for a tool-using assistant.

For the behaviour of swapper-class endpoints in depth — cold starts, readiness,
eviction, runaway clients — see [Self-hosted endpoints](self-hosted.md).

## 0. Make sure you have a harness

This router dispatches *through* an agentic CLI. If you have none installed,
nothing downstream will work:

```bash
pitwall agents harnesses                 # what is installed
pitwall agents setup harnesses --dry-run # what would be installed
pitwall agents setup harnesses           # interactive installer
```

The installer uses pinned per-platform recipes with checksum and redirect-host
controls, and skips anything already present. For a local endpoint you only need
one of the harnesses that accept a custom base URL (§7) — `goose` is the easiest
first choice.

## 1. Find the endpoint

```bash
python3 tools/agents/detect_local_endpoints.py
python3 tools/agents/detect_local_endpoints.py --host 192.0.2.10 --api-key-env MY_KEY
python3 tools/agents/detect_local_endpoints.py --json
```

It probes the ports local inference servers conventionally use, identifies the
product from how it answers (not from the port), reports whether a key is
required, lists the served models, and prints the commands that attach it.

**If it finds nothing but you know a server is running**, it is almost always
one of two things:

- **It is bound to one interface.** A server listening on `192.0.2.10:9292` does
  not answer on loopback, and vice versa. Check with
  `ss -ltnp | grep -iE 'vllm|ollama|llama|sglang'` and pass `--host`/`--port`.
- **It is in a container without a published port.** `docker ps` shows whether
  the port is mapped to the host.

## 2. Know what you are attached to

The detector names the product because the product decides what bites you.

| Server | Key facts |
|---|---|
| **Ollama** | OpenAI-compatible API at `/v1` on the same port. Usually no auth. Loads models on demand. |
| **vLLM** | One model per process. **Tool calling is off unless it was launched with the flags in §5.** |
| **llama.cpp** (`llama-server`) | One model per process. GGUF. Fast start. |
| **LM Studio** | OpenAI-compatible; the desktop app controls which model is loaded. |
| **SGLang** | One model per process. |
| **A swapper** (e.g. llama-swap) | Fronts many models and starts/stops engines on demand. Has a readiness endpoint; see §6. |

## 3. Attach it

```bash
export MY_KEY='...'          # if the endpoint requires one; never commit the value
pitwall agents profiles discover --base-url http://<host>:<port>/v1 --api-key-env MY_KEY --timeout 15
pitwall agents profiles add local-llm --model <model-id> --harness goose \
    --base-url http://<host>:<port>/v1 --api-key-env MY_KEY --seat local
pitwall agents profiles probe local-llm --timeout 120
```

`--api-key-env` stores the **name** of an environment variable, never the value.

**Attaching several models from one endpoint?** Define the endpoint once and
reference it by name, so a change of address is a single edit:

```toml
[agents.profiles.endpoints.mybox]
baseUrl = "http://<host>:<port>/v1"
apiKeyEnv = "MY_KEY"

[agents.profiles.models.local-llm]
endpoint = "mybox"
model = "<model-id>"
harness = "goose"
```

or `pitwall agents profiles add <name> --endpoint mybox ...`. See
[Agent profiles](routes.md).

## 4. Verify with a real dispatch

```bash
echo 'Reply with one sentence: what is the capital of France?' > /tmp/p.md
pitwall agents dispatch route local-llm /tmp/p.md
```

A probe proves reachability. Only a dispatch proves the harness, the model, the
tool schema, and the timeouts all agree.

## 5. Server-side prerequisites

**Tool calling must be enabled at launch.** A stock `vllm serve` answers
**HTTP 400** to any agentic request — every CLI here sends `tools` with
`tool_choice: "auto"`. The model's own ability is irrelevant; the server refuses
before inference:

```
--enable-auto-tool-choice --tool-call-parser <parser-for-your-model-family>
```

The parser is model-family specific. Pick the one matching your checkpoint.

**Context has to fit the agent, not just the conversation.** An agentic CLI
sends a large fixed payload before any user text — tool schemas plus a system
prompt, thousands of tokens. One measured CLI sent ~35k tokens by default, more
than a 32k model can accept at all. Three layers, each hiding the next:

1. tool calling enabled server-side;
2. the client's default output cap, which can exceed a small model's window;
3. the client's own prompt overhead, which needs headroom the window must have.

A bigger window does not rescue an untrimmed agent. Trim the tool set at the
client (most CLIs support an allow-list) and serve the largest window you can.

**A reasoning model spends the output budget on reasoning.** If a reply comes
back with empty content and `finish_reason: length`, raise `max_tokens` — the
reasoning trace consumed it.

## 6. Cold starts, and the timeout that bites

If your endpoint loads models on demand, the first request blocks for the whole
load and then returns 200. It does **not** return 503 while warming, so a client
that gives up early turns a healthy warm-up into a spurious failure.

Load time is dominated by engine startup, not weight size: a 3 GB model measured
~85 s, a 27B INT8 model across two GPUs measured ~145 s. Measure yours:

```bash
time curl -s -o /dev/null -H "Authorization: Bearer $MY_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"<model-id>","messages":[{"role":"user","content":"hi"}],"max_tokens":8}' \
  http://<host>:<port>/v1/chat/completions
```

Then compare against the per-harness budgets in
[Self-hosted endpoints](self-hosted.md#harness-timeouts-and-knobs). Two harnesses
abort their first attempt against a 145 s cold start and succeed only because
they retry — if you reduce a harness's retry count, re-test your cold path.

**The simplest fix is to pre-warm.** One throwaway request before real work
removes the whole problem class.

**Do not use `/v1/models` as a readiness signal.** On at least one swapper it
reports a model as loaded from the moment a start is *initiated*, while requests
still block for minutes. Use the server's own state endpoint where it has one
(`/running` on llama-swap gives per-model `starting`/`ready`).

## 7. Per-harness caveats

Only some harnesses accept an arbitrary base URL:

- **Environment delivery** — `qwen`, `goose`: nothing to sync.
- **Config materialization** — `opencode`, `pi`, `cline`, `dsh`, `hermes`: run
  `pitwall agents profiles sync --harness <name>` after adding a route.
- **Vendor-bound** — `codex`, `claude`, `grok`, `kimi`, `muse`, `agy`: these
  refuse a custom endpoint by design.

**Hermes resolves credentials from its own provider config**, not from
environment variables — an `OPENAI_API_KEY` in the environment is not attached to
a non-loopback endpoint, and hermes answers 401 while exiting 0. `pitwall agents profiles sync`
handles this: it merges a managed `providers:` block into `~/.hermes/config.yaml`
and dispatch selects it automatically.

```bash
pitwall agents profiles add local-hermes --model <model-id> --harness hermes --endpoint mybox
pitwall agents profiles sync --harness hermes
```

The block records `key_env` — the name of your key variable — so the secret never
reaches the file. Your own providers are preserved. Hermes holds many providers
at once, so several routes can target it. Hermes also treats loopback, private
and CGNAT addresses as "local" while a DNS name for the *same machine* counts as
remote, which changes its stale-connection timeout; test with the spelling you
will actually use.

**Cline and dsh hold one custom endpoint at a time.** Two routes pinning either
harness will refuse to sync; keep one route per harness or switch between them
deliberately.

**opencode needs explicit limits.** Without `context`/`output` limits it can send
an output cap larger than a small model's window, misread the resulting 400 as a
context overflow, compact, and retry the same request indefinitely. `pitwall agents profiles sync`
writes the limits from `--limits context=N,output=N`.

## 8. Troubleshooting by symptom

| Symptom | Cause |
|---|---|
| Connection refused, but the server is running | Bound to a different interface, or an unpublished container port |
| `401` from the endpoint | Missing/incorrect key — or Hermes, which needs §7's provider entry |
| `400` mentioning tool choice | Server launched without the tool-calling flags (§5) |
| `400` mentioning `max_tokens` or context length | Client's output cap or prompt overhead exceeds the window (§5) |
| Empty reply, `finish_reason: length` | Reasoning consumed the output budget — raise `max_tokens` |
| Client reports failure at a round number of seconds | Client-side timeout shorter than the cold start (§6) |
| A warm model went cold with nobody asking | A sibling request evicted it; the endpoint's grouping decides this |
| Repeating identical 4xx in the server log, silent client | A client retry loop — see [Self-hosted endpoints](self-hosted.md) |

When a client and a server disagree about what happened, the endpoint's access
log is the only account that is not self-reported. Check it first.
