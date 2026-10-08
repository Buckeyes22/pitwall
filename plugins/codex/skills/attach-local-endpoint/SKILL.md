---
name: attach-local-endpoint
user-invocable: true
description: Find a locally hosted LLM server (Ollama, vLLM, llama.cpp, LM Studio, SGLang, a swapper) and attach it as a route this router can dispatch to. Use when the user says they run a model locally, wants to use their own GPU/hardware, mentions a local endpoint or base URL, or asks to point the router at something self-hosted. Covers detection, attachment, verification, and the failures that actually occur — tool-calling flags, context sizing, cold-start timeouts, per-harness caveats.
---

# Attach a locally hosted model

Goal: turn "I run a model locally" into a route that survives a real dispatch.
Work through the phases in order. Do not skip verification — a reachable
endpoint is not a working one.

Reference for humans: `docs/agents/attach-local-endpoint.md`. Endpoint behaviour in
depth: `docs/agents/self-hosted.md`.

## Phase 0 — Ensure a harness exists

Dispatch happens through an agentic CLI. Check before anything else:

```bash
pitwall agents harnesses                  # which are installed
pitwall agents setup harnesses --dry-run  # what installing would do
```

If none of the endpoint-capable harnesses (Phase 5) are installed, offer to run
`pitwall agents setup harnesses` — an interactive installer with pinned recipes,
checksums and redirect-host controls. It is interactive, so it needs a terminal;
if you cannot drive one, tell the user the command to run rather than guessing.
For a local endpoint, `goose` is the simplest first harness.

## Phase 1 — Locate

```bash
python3 tools/agents/detect_local_endpoints.py                      # this machine
python3 tools/agents/detect_local_endpoints.py --host <ip> --api-key-env <VAR>
python3 tools/agents/detect_local_endpoints.py --json               # to parse
```

The script reports the URL, which product is answering, whether a key is
required, and the served model IDs.

**If it finds nothing**, do not conclude nothing is running. Check, in order:

```bash
ss -ltnp | grep -iE 'vllm|ollama|llama|sglang|lmstudio'   # what is listening, on which interface
docker ps                                                  # is the port published?
pgrep -af 'vllm|ollama|llama-server|llama-swap|sglang'     # is a process alive?
```

The usual cause is an interface bind: a server on `<lan-ip>:<port>` does not
answer on loopback. Re-run the detector with `--host`/`--port`.

Ask the user for the key if the endpoint returns 401 and you have no variable
name. Store it as an environment variable; never write a key into a config file
or a repo.

## Phase 2 — Attach

```bash
pitwall agents profiles discover --base-url <url> --api-key-env <VAR> --timeout 15
pitwall agents profiles add <name> --model <model-id> --harness goose \
    --base-url <url> --api-key-env <VAR> --seat local
pitwall agents profiles probe <name> --timeout 120
```

Use `goose` for the first route unless the user wants otherwise: it has a long
request budget and no retry layer, so a failure is a real failure rather than a
masked one.

Attaching several models from one endpoint: define it once under `endpoints` and
reference it by name (`--endpoint <name>`), so an address change is one edit.

## Phase 3 — Verify with a dispatch

```bash
echo 'Reply with one sentence: what is the capital of France?' > /tmp/verify.md
pitwall agents dispatch route <name> /tmp/verify.md
```

Exit 0 with a sensible answer is the only proof that counts. If it fails, go to
Phase 4 — do not report success, and do not guess at the cause.

## Phase 4 — Diagnose by symptom

Read the error before acting. These are the failures that actually occur:

| Symptom | Cause | Fix |
|---|---|---|
| Connection refused | Interface bind or unpublished container port | Re-detect with the right `--host` |
| `401` | Wrong/missing key — **or hermes**, see below | Check `$VAR` is exported |
| `400` about tool choice | Server started without tool-calling support | Server must relaunch with `--enable-auto-tool-choice --tool-call-parser <family>` |
| `400` about `max_tokens`/context | Client's output cap or prompt overhead exceeds the window | Set `--limits context=N,output=N`; trim the client's tool set |
| Empty reply, `finish_reason: length` | A reasoning model spent the budget on reasoning | Raise `max_tokens` |
| Failure at a round number of seconds (60/90/120) | Client timeout shorter than the cold start | Pre-warm, or raise that harness's knob |
| Slow first request, fast afterwards | Normal cold start | Pre-warm before real work |

**Server-side prerequisites you cannot fix from the client:** tool calling must
be enabled at launch, and the context window must hold the agent's fixed payload
(tool schemas plus system prompt — thousands of tokens before any user text).
If the server is the user's to restart, tell them exactly which flags to add.

**Cold starts:** the first request to an unloaded model blocks for the entire
load and then returns 200 — it does not return 503. Load time tracks engine
startup more than model size (~85 s for a 3 GB model; ~145 s for a 27B across
two GPUs). Measure it before choosing a harness:

```bash
time curl -s -o /dev/null -H "Authorization: Bearer $VAR" -H 'Content-Type: application/json' \
  -d '{"model":"<id>","messages":[{"role":"user","content":"hi"}],"max_tokens":8}' \
  <url>/chat/completions
```

**Readiness:** `/v1/models` is not a readiness oracle — a swapper can report a
model as loaded while it is still starting. Prefer the server's own state
endpoint (`/running` on llama-swap gives per-model `starting`/`ready`).

## Phase 5 — Harness-specific requirements

Only some harnesses accept a custom base URL:

- **env delivery** — `qwen`, `goose`: no sync step.
- **config materialization** — `opencode`, `pi`, `cline`, `dsh`, `hermes`: run
  `pitwall agents profiles sync --harness <name>` after adding the route.
- **vendor-bound** — `codex`, `claude`, `grok`, `kimi`, `muse`, `agy`: refuse
  custom endpoints by design. Do not try.

**hermes** resolves credentials from its own provider config, so an
`OPENAI_API_KEY` in the environment is not attached to a non-loopback endpoint —
it returns 401 and exits 0. `profiles sync --harness hermes` materializes the
provider and dispatch selects it automatically; no hand-editing. The materialized
block records the key variable's *name*, never its value.

**cline** and **dsh** hold one custom endpoint each; a second route pinning the
same harness refuses to sync. **opencode** needs `--limits`, or it can retry a
rejected oversized request indefinitely.

## Reporting back

Tell the user: the endpoint URL, which server it is, the model ID, the harness,
the measured cold-start and warm times, and any server-side change they still
need to make. If you could not make it work, say which phase failed and what the
error was — never report a probe as though it were a dispatch.
