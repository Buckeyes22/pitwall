# vLLM: default `serve-model` engine

## Decision

Use vLLM for a Pitwall `pod_lease` when the selected model has a recipe or a
validated ordinary `vllm/vllm-openai` image launch.  It is the default because
`vllm serve` presents the required OpenAI-style server and supports the dense,
MoE, multimodal, tool-call, reasoning, FP8/MXFP4/NVFP4 and distributed cases
represented by the current dossiers.  Pin an immutable release image/tag or
digest per model; do not use `latest` as a production model recipe.

At this research date the newest GitHub **stable release is v0.28.0**
(2026-08-26).  Releases in the preceding six weeks include 0.27.1/0.27.0,
0.26.0, 0.25.1/0.25.0, 0.24.0, 0.23.0, and 0.22.1/0.22.0: roughly weekly
feature/minor releases, sometimes with a patch a day later.  This is a fast
cadence, not an API-stability promise: model dossiers must pin their verified
minimum/image and be regression-smoked before advancing.  [GitHub releases]
(https://github.com/vllm-project/vllm/releases)

## Image and exact command contract

The supported NVIDIA serving image is `vllm/vllm-openai`.  Its Dockerfile's
final serving stage is exactly `ENTRYPOINT ["vllm", "serve"]`; therefore
RunPod/Pitwall `docker_start_cmd` is *only the arguments after* `vllm serve`.
The very first argument is the model repository/path:

```text
image: vllm/vllm-openai:v0.28.0     # pin model-specific image/tag instead where required
docker_start_cmd:
  - Qwen/Qwen3.8-27B
  - --served-model-name
  - qwen3.8-27b
  - --host
  - 0.0.0.0
  - --port
  - "8000"
```

This is deliberately **not** `--model Qwen/...`.  vLLM's parser still rewrites
that legacy spelling to a positional argument but logs that `--model` will be
removed; it also raises, when `--served-model-name` is supplied without a
positional/config model, `` `model` should be provided as the first positional
argument when using `vllm serve` ``.  This explains the otherwise conflicting
older model dossiers: normalize their launch arrays before implementation.
[Dockerfile](https://github.com/vllm-project/vllm/blob/main/docker/Dockerfile#L1069-L1078)
[argument parser](https://github.com/vllm-project/vllm/blob/main/vllm/utils/argparse_utils.py#L270-L315)

`vllm/vllm-openai:<version>`, `latest`, CUDA-specific release tags such as
`cu129-…`, and `nightly`/commit tags are published; only the first is a stable
engine pin.  The live `latest` layer reports v0.27.1 built on CUDA 13.0.2 and
requires CUDA >=13.0 / NVIDIA driver >=535.  That is a property of that tag,
not a universal floor: inspect the selected tag's layers/manifest before
choosing a RunPod host.  Dedicated images can impose a higher floor: the Kimi
K3 recipe says `:kimi-k3` is CUDA-13-only and needs r580+.
[tag list](https://hub.docker.com/r/vllm/vllm-openai/tags)
[latest layer metadata](https://hub.docker.com/layers/vllm/vllm-openai/latest?tab=layers)
[Kimi recipe](https://recipes.vllm.ai/moonshotai/Kimi-K3?hardware=b300)

## API/readiness contract

`vllm serve` is OpenAI-compatible, but endpoint availability still depends on
the loaded task/model.  Pitwall can use:

| Probe/API | Pitwall use and limit |
|---|---|
| `GET /health` | Liveness of the API's engine client: 200 only after `check_health()` succeeds; 503 on `EngineDeadError`. It does **not** promise that the requested model ID, chat template, tool parser, or every endpoint is usable. A render-only server returns 200 without an engine. |
| `GET /v1/models` | Mandatory second readiness gate. Require HTTP 200 and `data[].id == --served-model-name`; it validates the externally routed served ID rather than mere worker liveness. |
| `POST /v1/chat/completions` | Primary Pitwall LLM route; tool/reasoning output needs the model-specific parser switches below. |
| `POST /v1/completions` | Legacy prompt-completions surface for generative models. |
| `POST /v1/embeddings` | Only for a compatible embedding/pooling model; do not infer it from `/health` or from a chat model. |
| `GET /metrics` | Prometheus metrics endpoint; use for observability, never as the sole ready check. |

[health implementation](https://docs.vllm.ai/en/latest/api/vllm/entrypoints/serve/instrumentator/health/)
[OpenAI-compatible server](https://docs.vllm.ai/en/stable/serving/openai_compatible_server/)
[security endpoint inventory](https://docs.vllm.ai/en/stable/usage/security/)

Pitwall's intended routing is a `pod_lease` URL derived from the armed pod id
and proxy port, not a static configured URL
([`src/pitwall/routing/openai.py:322`](../../../../src/pitwall/routing/openai.py#L322)).
The engineering brief additionally specifies the gate sequence: `/health`,
then `/v1/models` listing the served id.  Preserve that two-step distinction.

## Flags that `serve-model` must preserve

All values are model-specific; do not manufacture defaults in the CLI beyond
vLLM's own defaults.  The current CLI reference is the authoritative exact
schema and choices.  `--host 0.0.0.0` plus `--port 8000` is normally required
inside a RunPod container; use the actual mapped port in the lease.

| Flag | Operational meaning / Pitwall rule |
|---|---|
| `--served-model-name ID` | Public model id emitted by `/v1/models`; must be stable and exactly match Pitwall capability metadata/readiness check. |
| `--port`, `--host` | Bind vLLM to the container port/interface. Do not confuse this with the RunPod external mapping. |
| `--max-model-len` | Maximum context used for allocation. Lower it before retrying OOM/KV failures. Raising beyond model config requires the explicit escape hatch below and model validation. |
| `--gpu-memory-utilization` | Fraction of each GPU used by vLLM; it changes KV-cache budget after weights/runtime reservation. Leave safety headroom; it is not a promise that weights fit. |
| `--tensor-parallel-size` (TP) | Shards individual layers across GPUs. On a single pod require enough visible GPUs and normally `TP × PP` not greater than local GPU count. |
| `--pipeline-parallel-size` (PP) | Splits layers into pipeline stages; combine with TP only when a validated model/hardware recipe requires it. |
| `--enable-expert-parallel` | Shards MoE experts; pair with an explicitly validated TP/DP topology (e.g. DeepSeek V4's DP+EP), not merely because a model is MoE. |
| `--data-parallel-size` (DP) | Replicas/engine cores for throughput; it multiplies weight memory and startup work. It is not a way to make one model fit. |
| `--dtype {auto,half,float16,bfloat16,float,float32}` | Weight/activation execution dtype. `auto` follows model config; do not use this to pretend a checkpoint's quantization changed. |
| `--quantization` | Explicit override only when the checkpoint/recipe calls for it. Otherwise vLLM reads `quantization_config` from model config. |
| `--kv-cache-dtype {auto,fp8,fp8_e4m3,fp8_e5m2}` | KV precision, not weight quantization. It can free VRAM but is model/backend/hardware dependent; GLM-5.3-Flash's recipe, for example, limits FP8 KV to Blackwell. |
| `--trust-remote-code` | Executes model-repository Python during loading. Set only where the model recipe requires it; treat it as a supply-chain decision and pin revision/image. |
| `--enable-auto-tool-choice` + `--tool-call-parser NAME` | Both are needed for automatic OpenAI tool-call extraction. Do not set a parser generically: select exactly the model's parser and smoke-test arguments. |
| `--reasoning-parser NAME` | Extracts reasoning into OpenAI-formatted `reasoning_content`; this is independent of a tool parser. |
| `--chat-template PATH_OR_JINJA` | Overrides/sets chat rendering. Needed only when the model dossier says so; a wrong template silently changes tokens/tool syntax. |
| `--default-chat-template-kwargs JSON` | Server defaults supplied to templates (for example model-specific thinking controls); request kwargs may still be model API policy. |
| `--limit-mm-per-prompt JSON` | Per-modality item limits for multimodal models (e.g. `{"image": 4}`); required to cap preprocessing/token/VRAM exposure. |
| `--speculative-config JSON` | Model-specific speculative decoding config; preserve it byte-for-byte from a recipe (e.g. MTP/DSpark) and validate baseline first. |
| `--hf-overrides JSON` | Model configuration overrides. Only carry upstream recipe values; it can alter architecture/config interpretation. |
| `--download-dir PATH` | Hugging Face download/cache location; put this on the intended persistent volume/cache path when used. |
| `--load-format` | Loader selection, typically `auto`; preserve required values such as K3's `fastsafetensors`. It is not a universal performance tuning flag. |
| `--enable-prefix-caching` | Reuses common prompt prefixes. Keep explicit when the model recipe requires it (K3 does); measure its memory/throughput effect rather than assuming it is harmless. |
| `--tokenizer-mode` | Selects a model-specific tokenizer path. Preserve a recipe's value such as DeepSeek V4's `deepseek_v4`; a generic Hugging Face tokenizer is not necessarily equivalent. |
| `--block-size` | KV-cache block size. Preserve a dedicated recipe's value (DeepSeek V4 uses 256); it is not a generic context-length knob. |

[current `serve` CLI reference](https://docs.vllm.ai/en/latest/cli/serve/)
[engine arguments](https://docs.vllm.ai/en/stable/configuration/engine_args/)

### Built-in parser inventory (v0.28.0 source at access time)

This is the complete registered built-in name list, not a claim every parser
works for every model. Plugins can add more names.  These lists intentionally
cover dossiers' `kimi_k3`, `glm47`, `deepseek_v4`, `gemma4`, and
`muse_glimmer` selections.

* `--tool-call-parser`: `dots`, `deepseek_v3`, `deepseek_v31`,
  `deepseek_v32`, `deepseek_v4`, `cohere_command3`, `cohere_command4`,
  `ernie45`, `glm45`, `glm47`, `ling3`, `granite-20b-fc`, `granite`,
  `granite4`, `hermes`, `poolside_v1`, `hunyuan_a13b`, `hy_v3`, `internlm`,
  `jamba`, `lfm2`, `kimi_k2`, `kimi_k3`, `llama3_json`, `llama4_json`,
  `llama4_pythonic`, `longcat`, `mimo`, `minimax_m2`, `minimax_m3`,
  `minicpm5`, `mistral`, `olmo3`, `muse_glimmer`, `openai`,
  `phi4_mini_json`, `pythonic`, `qwen3_coder`, `qwen3_xml`, `seed_oss`,
  `step3`, `step3p5`, `inkling`, `xlam`, `gigachat3`, `functiongemma`,
  `gemma4`, `apertus`.
* `--reasoning-parser`: `deepseek_r1`, `deepseek_v3`, `deepseek_v4`,
  `poolside_v1`, `cohere_command3`, `cohere_command4`, `ernie45`, `gemma4`,
  `glm45`, `glm47`, `ling3`, `openai_gptoss`, `granite`, `holo2`,
  `hunyuan_a13b`, `hy_v3`, `kimi_k2`, `kimi_k3`, `mimo`, `minimax_m2`,
  `minimax_m2_append_think`, `minimax_m3`, `mistral`, `nemotron_v3`,
  `olmo3`, `muse_glimmer`, `qwen3`, `seed_oss`, `step3`, `step3p5`,
  `inkling`.

[tool parser registry](https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/tool_parsers/__init__.py)
[reasoning parser registry](https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/reasoning/__init__.py)

## Quantization: supported does not mean deployable

vLLM's current quantization documentation includes FP8, INT8/INT4,
GPTQ/AWQ, GGUF, compressed-tensors, ModelOpt and more.  Its compatibility table
states AWQ works from Turing through Hopper, GPTQ from Volta through Hopper,
FP8 W8A8 on Ada/Hopper/AMD, and GGUF on NVIDIA Volta–Hopper and AMD.  It also
warns the chart changes.  Therefore model checkpoint format, GPU architecture,
kernel/backend and vLLM tag are all launch inputs.

* **FP8:** source-native/LLM-Compressor FP8 is supported, with the documented
  W8A8 hardware constraints above.  Preserve source `quantization_config` or
  the dedicated recipe; do not force `--quantization fp8` just because a GPU
  supports it.
* **AWQ/GPTQ:** supported weight-only families.  Their supported *hardware*
  does not validate a particular model architecture, tensor layout or context
  fit; model-specific smoke testing remains required.
* **GGUF:** vLLM supports GGUF, but its own GGUF page describes it as a
  single-file model format and requires tokenizer conversion/selection details.
  For the provided GGUF dossiers, llama.cpp is generally the safer dedicated
  engine; use vLLM GGUF only after a model-specific launch proves architecture,
  tokenizer and multimodal pieces.  It is not proof that every GGUF variant is
  vLLM-ready.
* **NVFP4/MXFP4 (and MXFP8):** present in the current vLLM capability surface
  and need current Blackwell-oriented kernels/images in many recipes.  Kimi K3
  uses a dedicated CUDA-13 image; DeepSeek V4 recipes use specialized
  configurations.  Treat these as dedicated-image/recipe territory, not a
  generic v0.28.0 inference that automatically fits older GPUs.
* **`compressed-tensors`:** supported loader/quantization family including
  MoE schemes; the checkpoint config determines its exact W4A4/W4A8/W8A8 and
  FP8/MXFP4/NVFP4 semantics.  K3's model config is a concrete example.  Pin
  the model recipe and test, especially for huge multi-GPU MoEs.

[quantization overview and hardware table](https://docs.vllm.ai/en/stable/features/quantization/)
[GGUF support](https://docs.vllm.ai/en/stable/features/quantization/gguf/)
[K3 recipe](https://recipes.vllm.ai/moonshotai/Kimi-K3?hardware=b300)
[DeepSeek V4 recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300)

## Environment and cache contract

| Variable | Pitwall treatment |
|---|---|
| `HF_TOKEN` | Inject only for gated/private Hugging Face model access. Keep it secret; do not put it in `docker_start_cmd`. Public models do not need it. |
| `HF_HOME` | Set to the persistent volume's HF cache root when a volume is selected; otherwise cold pods redownload. Make `--download-dir` consistent with it rather than creating competing caches. |
| `VLLM_ALLOW_LONG_MAX_MODEL_LEN=1` | Enables `--max-model-len` exceeding the limit inferred from `config.json`. Exceptional, never an automatic fix: it increases KV allocation and needs model correctness validation. |
| `VLLM_ENGINE_READY_TIMEOUT_S` | Engine-core-start deadline, default 600 seconds. Set a documented model-specific higher value before starting very large/cold/JIT-heavy models; it is not the outer Pitwall lease timeout. |
| `VLLM_USE_V1` | **Do not set in a v0.28.0 recipe.** It was an old V0/V1 engine selector and is absent from the current `envs.py`; current stable uses the V1 architecture. Leaving it in older recipes risks an ignored or incompatible setting. |
| `VLLM_ATTENTION_BACKEND` | Advanced backend override (examples include `FLASHINFER`/`XFORMERS` in historical vLLM discussions). Leave unset unless the exact image/model recipe requires it: unsupported combinations produce “Invalid attention backend for cuda …”. |
| `VLLM_WORKER_MULTIPROC_METHOD` | `fork` (default) or `spawn`. Use only to address a diagnosed platform/library start-method issue; it is process-creation behavior, not a performance setting. |

[vLLM env source](https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/envs.py)
[HF environment variables](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables)
[attention-backend failure example](https://github.com/vllm-project/vllm/issues/14320)

## Cold-start budget and multi-GPU pod operation

Startup has four observable phases: (1) Hugging Face resolves/downloads model,
tokenizer and processor into cache; (2) workers deserialize/shard and load
weights; (3) vLLM profiles available memory, initializes distributed groups and
KV cache, then `torch.compile`/kernel compilation and CUDA-graph capture warm
up; (4) API engine reports ready, so `/health` can return 200.  There is **no
universal minute value**: source-validated timings depend on cache warmth,
checkpoint shard/tensor count, network/volume bandwidth, CPU cores, GPU
architecture, TP/DP and compilation cache.  Record per model/GPU/cache-state
measurements and set the lease timeout from p95 cold start plus margin.

The vLLM default inner ready deadline is 10 minutes.  A recent reported DP=4,
block-quantized example took 1,822 seconds merely for model loading because of
CPU thread oversubscription; a cold FlashInfer compilation example exceeded
600 seconds.  These are operational evidence, not a general estimate.  For
large K3/GLM/DeepSeek deployments choose 3,600 seconds only where their recipe
or measured cold start supports it, and ensure Pitwall's own readiness window
is longer.
[ready timeout source](https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/envs.py#L793-L797)
[DP loading failure report](https://github.com/vllm-project/vllm/issues/52330)
[cold JIT timeout report](https://github.com/vllm-project/vllm/issues/48031)

For multiple GPUs **in one pod**, expose exactly the selected GPUs via
`CUDA_VISIBLE_DEVICES`, make TP/PP/DP/EP match that topology, and keep all
ranks on the same host.  A co-located DP replica normally requires its own
TP×PP group, so verify the full recipe's aggregate GPU count rather than only
that TP×PP fits.  vLLM selects `mp` for a single-host TP×PP layout that fits
available GPUs; otherwise its documented distributed-executor rules apply.
NCCL must be able to discover/use the pod's interfaces;
capture `NCCL_DEBUG=INFO` on a failed first launch and, only when needed, set
the provider-appropriate `NCCL_SOCKET_IFNAME` / `NCCL_IB_HCA` and transport
switches according to NVIDIA's NCCL documentation.  Do not cargo-cult IB
variables onto a pod without InfiniBand.

When GPUs lack NVLink (common PCIe/consumer configurations), pass
`--disable-custom-all-reduce` so vLLM falls back to NCCL; it may be slower but
avoids the custom all-reduce path's topology assumption.  vLLM itself disables
custom all-reduce on multi-node.  This does not repair a bad NCCL topology or
make an insufficient-memory model fit.
[parallel CLI semantics](https://docs.vllm.ai/en/latest/cli/serve/#parallelconfig)
[parallel config/source](https://docs.vllm.ai/en/latest/api/vllm/config/parallel/)
[NCCL environment guide](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html)

## Recipes and dedicated images

`recipes.vllm.ai` is vLLM's per-model deployment catalog.  Its model page is
the first place to discover the correct supported engine version, hardware,
command, parser/template flags, quantization caveats and whether the normal
image is insufficient.  Search the model's exact Hugging Face id on the site,
open the recipe's source/YAML, and copy its `image`/variant command; then pin
that exact dedicated tag/digest in the model dossier.  If no recipe exists,
start with the pinned ordinary image only after the model is on vLLM's supported
model list and a local-equivalent smoke test succeeds.

Examples: `vllm/vllm-openai:kimi-k3` is the documented Kimi K3 dedicated image;
`vllm/vllm-openai:glm53-flash` is required by the GLM-5.3-Flash recipe until
ordinary-image integration.  Do not replace either with `latest` merely to get
a newer core engine.
[recipes catalog](https://recipes.vllm.ai/)
[K3](https://recipes.vllm.ai/moonshotai/Kimi-K3?hardware=b300)
[GLM-5.3-Flash](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)

## Failure signatures and first response

| Symptom/string | Meaning and safe first action |
|---|---|
| `CUDA out of memory` / `OutOfMemoryError` during load | Weights/runtime/parallel shard do not fit. Select the recipe's GPU/topology or a validated compatible quant; lower `--max-model-len`/memory utilization only after checking whether the failure is allocation vs weight fit. |
| `No available memory for the cache blocks` / `The model's max seq len … is larger than the maximum number of tokens that can be stored in KV cache` | Weights loaded but remaining KV cache cannot honor configured context. Lower `--max-model-len`, increase GPU count/VRAM, use a source-supported KV dtype, or lower concurrency-related limits. |
| `Timed out waiting for engine core processes to start. This is often caused by slow weight loading for large models` | Inner default 600-second deadline expired. Inspect phase logs/CPU/cache first; then choose a measured appropriate `VLLM_ENGINE_READY_TIMEOUT_S`, rather than blind retries. |
| `ValueError: Model architectures [...] are not supported` / `Unsupported model architecture` | vLLM does not recognize the checkpoint architecture in this image. Locate a recipe/dedicated image or choose another engine; `--trust-remote-code` is not a universal remedy. |
| `` `model` should be provided as the first positional argument `` | Pitwall supplied flags before the model; make model ID `docker_start_cmd[0]`. |
| `Invalid attention backend for cuda` | Remove unvalidated `VLLM_ATTENTION_BACKEND` or use the image/model recipe's supported backend. |
| `/health` is 200 but routing fails/model mismatch | `/health` proves engine health only. Enforce `/v1/models` contains the exact `--served-model-name`; then perform a small capability-appropriate request. |

[KV-cache troubleshooting](https://docs.vllm.ai/en/stable/configuration/optimization/#kv-cache-memory-management)
[supported-models documentation](https://docs.vllm.ai/en/stable/models/supported_models/)
[argument parser](https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/utils/argparse_utils.py#L270-L315)

## Pitwall launch checklist

1. Select the model dossier's exact HF revision, vLLM recipe/image/tag (or mark
   it unverified); establish whether `HF_TOKEN` is required and set persistent
   `HF_HOME`/`--download-dir` plus enough disk for checkpoint, cache and image.
2. Verify the selected RunPod GPU class/driver against the **chosen image**, not
   just generic CUDA support.  For TP/PP/DP/EP, verify the single pod can expose
   the exact GPU count/topology.  Do not turn a multi-node recipe into a
   single-pod claim without evidence.
3. Build argument array with positional `MODEL` at index zero, then
   `--served-model-name`, `--host 0.0.0.0`, mapped `--port`, and every
   model-specific dtype/quantization/parallel/parser/template/MM/speculative
   flag.  Remove legacy `--model` from existing dossiers.
4. Set only required environment variables.  Choose a recorded cold-start
   `VLLM_ENGINE_READY_TIMEOUT_S`; never automatically enable long context,
   remote code, attention override or an old `VLLM_USE_V1` toggle.
5. For local multi-GPU PCIe/no-NVLink pods, include
   `--disable-custom-all-reduce`; retain startup logs with NCCL diagnostics for
   failures.  Confirm cache, CUDA graph/JIT artifacts and disk are writable.
6. Wait for runtime and port mapping, then poll `/health`.  Do not arm the
   `pod_lease` yet.  Poll `/v1/models` until it contains exactly the served id.
7. Before routing, make one bounded real request: chat for LLM, embeddings for
   embedding model; for tool/reasoning models validate parsed `tool_calls` and
   `reasoning_content`, and for multimodal models one constrained input.
8. Arm Pitwall's `pod_lease` only after all gates.  Export `/metrics`/logs and
   record cold/warm phase durations, actual VRAM/KV capacity and model/image
   digest.  Terminate/disarm on fatal worker health or model-id mismatch.

## Sources

- https://github.com/vllm-project/vllm/releases — v0.28.0 current release and recent release cadence — accessed 2026-08-27
- https://github.com/vllm-project/vllm/blob/v0.28.0/docker/Dockerfile — `vllm/vllm-openai` serving entrypoint — accessed 2026-08-27
- https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/utils/argparse_utils.py — positional-model enforcement and `--model` deprecation/rewrite — accessed 2026-08-27
- https://hub.docker.com/r/vllm/vllm-openai/tags — available ordinary/nightly/CUDA tag families — accessed 2026-08-27
- https://hub.docker.com/layers/vllm/vllm-openai/latest?tab=layers — live latest image CUDA 13.0.2 and driver constraint metadata — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/cli/serve/ — current `vllm serve` flag reference and parallel semantics — accessed 2026-08-27
- https://docs.vllm.ai/en/stable/serving/openai_compatible_server/ — OpenAI-compatible serving surface — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/api/vllm/entrypoints/serve/instrumentator/health/ — `/health` implementation and status semantics — accessed 2026-08-27
- https://docs.vllm.ai/en/stable/usage/security/ — endpoint inventory and health endpoint exposure — accessed 2026-08-27
- https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/tool_parsers/__init__.py — complete built-in tool-parser registry — accessed 2026-08-27
- https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/reasoning/__init__.py — complete built-in reasoning-parser registry — accessed 2026-08-27
- https://docs.vllm.ai/en/stable/features/quantization/ — supported quantization families and hardware compatibility table — accessed 2026-08-27
- https://docs.vllm.ai/en/stable/features/quantization/gguf/ — GGUF limitations/serving guidance — accessed 2026-08-27
- https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/envs.py — ready timeout, long-length and multiprocessing environment semantics; absence of legacy `VLLM_USE_V1` — accessed 2026-08-27
- https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables — `HF_TOKEN` and `HF_HOME` cache/auth settings — accessed 2026-08-27
- https://recipes.vllm.ai/ — recipe catalog/discovery program — accessed 2026-08-27
- https://recipes.vllm.ai/moonshotai/Kimi-K3?hardware=b300 — Kimi dedicated image/CUDA-driver requirements and launch settings — accessed 2026-08-27
- https://recipes.vllm.ai/zai-org/GLM-5.3-Flash — GLM dedicated image and FP8-KV caveat — accessed 2026-08-27
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300 — DeepSeek V4 special topology/quantization/parser recipe — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/52330 — observed 1,822-second DP/block-quant weight-load case and ready-timeout string — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/48031 — cold FlashInfer compilation exceeding the default ready deadline — accessed 2026-08-27
- https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html — NCCL environment-variable reference — accessed 2026-08-27
- https://docs.vllm.ai/en/stable/models/supported_models/ — architecture support discovery — accessed 2026-08-27
- ../../../../src/pitwall/routing/openai.py#L322 — Pitwall derives the active pod lease OpenAI base URL — accessed 2026-08-27

## Open questions

- `vllm/vllm-openai:v0.28.0` tag-specific CUDA base/driver floor was not established from a directly inspected v0.28.0 manifest; inspect the immutable tag/digest before a launch.  The reported CUDA 13.0.2/driver>=535 datum is only for live `latest` at access time.
- No source supports a universal phase-by-phase cold-start duration.  Pitwall needs measured p50/p95 download, load, compile/graph-capture and ready times by model, GPU class, cache warmth and parallel topology.
- The full parser lists are current registries, but parser/model compatibility is not automatically discoverable.  Each model dossier still needs a real structured-tool/reasoning smoke test after image upgrades.
- `VLLM_ATTENTION_BACKEND` remains requested in historical recipes/discussions but is not an exposed current `envs.py` entry; dedicated recipes may consume it through backend selection.  Treat it as unverified unless a pinned recipe requires it.
