# llama.cpp / llama-server — GGUF engine

## Decision

**Support now, as Pitwall's primary GGUF engine.** `llama-server` is a small CUDA-serving image with the exact readiness surface Pitwall needs: public `GET /health` (also `/v1/health`) and OpenAI-compatible `GET /v1/models` and `POST /v1/chat/completions`. `--alias` changes the model ID returned by `/v1/models`, so it is the essential bridge between a file/HF repo and Pitwall's `served_model_id`. [server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

Pitwall's `serve-model` contract derives the pod base URL as `https://{active_pod_id}-{openai_proxy_port}.proxy.runpod.net/v1` and makes a bounded `/v1/models` ID check after generic readiness; the implementation records those facts and the check explicitly (`src/pitwall/api/leases/launch.py:565-576`, `src/pitwall/serve.py:817-840,1502-1511`).

## Cadence, versioning, and images

llama.cpp is effectively a rolling-build project, not a conventional semver release line. The GitHub releases are sequential build tags (`b10644`, `b10643`, `b10642` on 2026-08-27) and GHCR publishes both moving family tags and build-pinned tags such as `server-cuda13-b10524`. Therefore: select a CUDA family for the host, **pin the tested build tag or image digest in a template**, and record `llama-server --version` in the launch evidence; never treat a bare moving tag as reproducible. [releases API](https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=8), [GHCR package](https://github.com/ggml-org/llama.cpp/pkgs/container/llama.cpp)

| Image | Contents / intended use | CUDA family and host floor |
|---|---|---|
| `ghcr.io/ggml-org/llama.cpp:server-cuda` | `llama-server` only; best production GGUF image. | CUDA 12 build. Use host driver **>=525**. |
| `:server-cuda12` | `llama-server` only; an explicit alias/build family for CUDA 12. | CUDA 12.8.1 build; driver **>=525**. |
| `:server-cuda13` | `llama-server` only, compiled with CUDA 13. | CUDA 13; driver **>=580**. |
| `:full-cuda` | `llama-cli`, `llama-completion`, conversion and quantization tools; do not use as the normal server image. | CUDA 12; driver **>=525**. |

The official Docker document defines `server-cuda` as the server-only CUDA-12 image and `full-cuda` as the full CUDA-12 image; it separately lists `server-cuda13` for CUDA 13. The Docker workflow publishes the aliases `cuda cuda12` from CUDA 12.8.1 and `cuda13` from CUDA 13.3.0 for each full/light/server target. NVIDIA’s compatibility table gives the major-family driver floors (12.x >=525; 13.x >=580). [Docker documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md), [Docker workflow](https://github.com/ggml-org/llama.cpp/blob/master/.github/workflows/docker.yml), [NVIDIA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)

## Pod launch and weight acquisition

The server-image entrypoint is `llama-server`; container arguments are therefore server flags, not `llama-server` again. For a pre-staged GGUF, pass `-m /models/model.gguf`. For Hub acquisition either use the compact selector `-hf org/repo:Q4_K_M`, or use `--hf-repo org/repo --hf-file exact-file.gguf` when the dossier must choose an exact filename. `-hf` defaults to Q4_K_M (falling back to the first file) and automatically downloads an available `mmproj`; `--hf-file` overrides the quant selector. `HF_TOKEN` is used by `--hf-token`/gated requests. [server arguments](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

Downloads go to the first configured cache location: `LLAMA_CACHE`, `HF_HUB_CACHE`, `HUGGINGFACE_HUB_CACHE`, `HF_HOME/hub`, `XDG_CACHE_HOME/huggingface/hub`, then `~/.cache/huggingface/hub`. Mount/persist the chosen cache path if a pod is reused; a fresh ephemeral disk re-downloads, so container disk must hold the selected GGUF, optional projector, and cache/download headroom. `HF_HUB_ENABLE_HF_TRANSFER` is **unverified for llama.cpp’s own downloader**; do not make it a required template env. [HF cache source](https://github.com/ggml-org/llama.cpp/blob/master/common/hf-cache.cpp)

Minimal text-only starter (replace IDs/size from the model dossier):

```text
image: ghcr.io/ggml-org/llama.cpp:server-cuda13-<tested-build>
docker_start_cmd:
  --hf-repo org/model-GGUF
  --hf-file model-Q4_K_M.gguf
  --alias pitwall-model-id
  --host 0.0.0.0
  --port 8000
  --n-gpu-layers all
  --ctx-size 32768
  --parallel 1
  --flash-attn on
  --cache-type-k q8_0
  --cache-type-v q8_0
  --jinja
env:
  LLAMA_CACHE: /workspace/llama-cache
  HF_TOKEN: <only-if-gated>
```

`--host` otherwise defaults to loopback and `--port` to 8080. Put RunPod's HTTP port at 8000 to match the template arguments and Pitwall provider `openai_proxy_port`; RunPod supports a custom image or a template and exposes `container_disk_gb`/a `/workspace` volume option. [server arguments](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [RunPod pod create reference](https://docs.runpod.io/runpodctl/reference/runpodctl-pod)

## Serving controls that `serve-model` must preserve

| Concern | Flags and operational meaning |
|---|---|
| Identity/network | `--alias ID`, `--host 0.0.0.0`, `--port 8000`. The alias is what Pitwall verifies. |
| Context and GPU placement | `-c`/`--ctx-size N` sets total server context (0 = model metadata); `-ngl`/`--n-gpu-layers N|auto|all` controls weight offload. `--tensor-split 3,1` proportions weights across GPUs; multi-GPU should additionally declare/benchmark split mode. |
| Output / concurrency | `-n`/`--n-predict` limits generated tokens (`-1` unbounded); `-np`/`--parallel` sets server slots; `--cont-batching` is dynamic batching and is enabled by default. Start Pitwall with `--parallel 1` unless concurrency has a separately sized KV budget. |
| Memory / throughput | `--flash-attn on|off|auto`; `--cache-type-k` and `--cache-type-v` select KV precision (f16 default; q8_0/q4_0 etc. are allowed); `--threads`; `-b`/`--batch-size` logical batch (2048 default); `-ub`/`--ubatch-size` physical batch (512 default). Lower batches are an OOM mitigation, not a model-size guarantee. |
| Template and reasoning | `--jinja` (enabled by default today but state it explicitly); model metadata template by default, `--chat-template` named/inline template, or `--chat-template-file` override. `--reasoning-format deepseek` returns thought text in `reasoning_content`; `none` leaves it in content. `--reasoning-budget -1|0|N` limits thought tokens. |
| Modalities | `--mmproj FILE` supplies a vision projector. With `-hf`, an available projector is automatically fetched unless `--no-mmproj`; test the model's supported projector/quant pairing. |
| Sampling | `--temp`, `--top-p`, `--top-k`, `--min-p`, and `--repeat-penalty`. Prefer the model's generation config until an evaluation justifies overrides. |

All names, defaults, accepted cache values, and semantics above are documented in the current server argument reference. [server arguments](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## API, tools, schema output, and maturity

Required readiness/API path: `GET /health` → HTTP 200, then `GET /v1/models` and require `data[].id == --alias`; send traffic to `POST /v1/chat/completions`. Useful diagnostics are `GET /props` (global properties; mutation via `POST /props` requires `--props`) and `GET /slots` (current slot state). `/v1/models` also carries model/runtime capability metadata, which clients should inspect before multimodal use. [server API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

OpenAI-style function calling is supported through `--jinja`, using a compatible model/template; the request option `parse_tool_calls` asks the server to parse emitted calls and `parallel_tool_calls` is template/model-dependent. Schema output is supported via `response_format` for JSON object and JSON Schema. This is usable but not a blanket interoperability guarantee: tool serialization, parser behavior, and parallel calls depend on the Jinja template, so tool-loop and JSON-schema tests are launch gates. The built-in `--tools`/`--agent` filesystem/shell tools are experimental and unsafe for an internet-facing pod—**do not enable them for Pitwall**. [function-calling documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md), [server chat API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## VRAM sizing

Do not size from GGUF file size alone. Community operational guidance expresses the baseline as:

```text
VRAM ~= fully-offloaded weights + KV cache(target context × parallel slots) + CUDA/activation/scratch headroom
KV bytes ~= 2 × layers × n_kv_heads × head_dim × context_tokens × bytes/value
```

For GQA models use `n_kv_heads`, not total attention heads; K and V may have different cache formats, and this is a planning formula rather than an allocator-exact promise. Q8 cache roughly halves f16 KV storage; q4 roughly quarters it, with model/quality implications. `--parallel` shares the total context budget but concurrent sequences and continuous batching need fragmentation headroom. Community sizing tools consistently separate weights, KV, and runtime overhead, while upstream documents the controls that determine them. [KV formula explanation](https://insiderllm.com/pdfs/kv-cache-optimization-guide.pdf), [GGUF VRAM calculator methodology](https://ggufvram.radicchio.page/), [llama.cpp batching discussion](https://github.com/ggml-org/llama.cpp/discussions/4130)

For the pre-existing cards: Qwen3.8's cited recipe is Q4_K_M, full GPU offload, Q8 K/V, 131,072 context, one slot on an RTX 4090; Ornith Q4_K_M is 21.7 GB before runtime and its card deliberately starts text-only at 32K on a 48-GB class. Treat these as model-specific starting points, not general 24-GB proofs. [Qwen dossier](../dossiers/unsloth--Qwen3.8-27B-GGUF.md), [Ornith dossier](../dossiers/ornith-ai--Ornith-1.5-35B-A3B-GGUF.md)

## Known failure modes

- A moving image tag changes server behavior; pin a build/digest and smoke test after every update.
- `-hf` silently chooses Q4_K_M/first matching file when no exact `--hf-file` is given; use explicit files for a dossier-backed launch.
- `-hf` may download an `mmproj`; unexpected projector download/storage/VRAM can break a text-only pod. Use `--no-mmproj` if intentionally text-only.
- Weight file fit does not imply context fit. OOM commonly comes from KV, batch/ubatch, projector, or multiple slots; reduce context/slots/batches, quantize KV, or choose a larger GPU.
- `--host` defaults to `127.0.0.1`; omit `0.0.0.0` and RunPod's proxy cannot reach it.
- Alias mismatch makes a healthy engine fail Pitwall readiness. The check must compare the exact string, including case.
- Embedded/model-template, tool parser, reasoning tags, schema constraints, and vision are model-specific. Qwen3.8's existing dossier identifies upstream tool-call regressions; keep those tests mandatory. [Qwen dossier](../dossiers/unsloth--Qwen3.8-27B-GGUF.md)
- CUDA 13 on a host driver below 580 (or CUDA 12 below 525) is incompatible by NVIDIA's published floor. [NVIDIA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)

## Launch checklist

1. Pin `server-cuda`/`server-cuda13` to a tested build or digest; match the RunPod host driver family.
2. Choose one exact GGUF and GPU/context/slot plan using weights + KV + headroom; set container disk/cache volume accordingly.
3. Set `LLAMA_CACHE` to persistent storage if reuse matters; inject `HF_TOKEN` only for a gated repo; use explicit `--hf-file`.
4. Use `--alias` equal to Pitwall `served_model_id`, `--host 0.0.0.0`, and the declared exposed port.
5. Start text-only by default (`--no-mmproj` when relevant); enable `--mmproj` only after a multimodal request test.
6. Send `/health`, then assert alias in `/v1/models`; confirm a non-streaming and streaming `/v1/chat/completions` request.
7. If advertised, test a real tool call, `response_format` JSON Schema, reasoning extraction/budget, and a multi-turn conversation before arming the capability.
8. Record image digest/build, command, GPU, free/peak VRAM, load time, cache location, and observed model ID in the lease evidence.

## Sources

- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — flags, APIs, aliases, HF download behavior, templates, tools, structured output, cache types, server diagnostics — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md — official image contents, CUDA families, CUDA Docker default — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/.github/workflows/docker.yml — `cuda`/`cuda12` aliases and CUDA 12.8.1 / CUDA 13.3.0 publishing matrix — accessed 2026-08-27
- https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=8 — rolling build-tag cadence and timestamps — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/pkgs/container/llama.cpp — GHCR build-pinned image-tag example — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/common/hf-cache.cpp — `LLAMA_CACHE` and cache precedence — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md — template-dependent function calling — accessed 2026-08-27
- https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html — CUDA 12/13 minimum driver families — accessed 2026-08-27
- https://docs.runpod.io/runpodctl/reference/runpodctl-pod — custom image/template, GPU and disk/volume inputs — accessed 2026-08-27
- https://insiderllm.com/pdfs/kv-cache-optimization-guide.pdf — community KV cache sizing formula — accessed 2026-08-27
- https://ggufvram.radicchio.page/ — community weights + KV + runtime sizing model — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/discussions/4130 — context/parallel/continuous-batching operational explanation — accessed 2026-08-27
- ../dossiers/unsloth--Qwen3.8-27B-GGUF.md — existing Qwen assumptions and known tool issues — accessed 2026-08-27
- ../dossiers/ornith-ai--Ornith-1.5-35B-A3B-GGUF.md — existing Ornith assumptions — accessed 2026-08-27

## Open questions

- Resolve and pin the registry digest of the selected `server-cuda*` tag at template creation; moving aliases are deliberately not reproducible.
- Measure first-download time and disk peak for each selected GGUF/projector on the target RunPod region; no reproducible RunPod timing source was found.
- Establish a model-family CI smoke matrix for Qwen3.8 and Ornith tool calls, schema output, reasoning, vision, and long-context peak VRAM before accepting image upgrades.
