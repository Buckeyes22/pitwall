# Other serving engines — Pitwall disposition

Pitwall requires an HTTP server reachable through a RunPod pod proxy, then `/health` and `GET /v1/models` whose ID matches its declared served model. The verdicts below are intentionally about that contract, not whether an engine is generally useful (`src/pitwall/serve.py:817-840,1502-1511`).

## TensorRT-LLM — support later

TensorRT-LLM's `trtllm-serve <model> --host <host> --port <port>` starts an OpenAI-compatible server. Its documented endpoints include `/v1/models`, `/v1/completions`, `/v1/chat/completions`, `/health`, `/metrics`, and `/version`, so it fits Pitwall's proxy and readiness check directly. The model argument may be an HF checkpoint path or TensorRT engine path; NVIDIA distributes a prebuilt NGC release container. [trtllm-serve reference](https://nvidia.github.io/TensorRT-LLM/commands/trtllm-serve.html), [quick start](https://nvidia.github.io/TensorRT-LLM/quick-start-guide.html)

It can be single-GPU friendly when the selected model/quant/GPU is supported, but its value is optimized NVIDIA deployment and the operational burden is materially higher than GGUF: supported architecture, CUDA/TensorRT/container alignment, conversion or a compatible pre-optimized checkpoint, and per-model validation. Support later as a distinct engine adapter once Pitwall needs performance on validated safetensors/FP8 checkpoints; do not route GGUF dossiers to it.

## Ollama — support later, not a default RunPod engine

Ollama's official `ollama/ollama` image sets `OLLAMA_HOST=0.0.0.0:11434`, exposes 11434, and starts `ollama serve`. Its documented partial OpenAI compatibility includes `/v1/chat/completions` at that port; it also has native model-list/pull APIs. The standard model store on Linux is `/usr/share/ollama/.ollama/models`, relocatable through `OLLAMA_MODELS`; `OLLAMA_HOST` changes the bind address. [image Dockerfile](https://github.com/ollama/ollama/blob/main/Dockerfile), [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility), [FAQ](https://docs.ollama.com/faq)

It is friendly to a single GPU and convenient for an interactive pod: `ollama pull model:tag` or `POST /api/pull` fetches an Ollama-library model, and it can report whether a model is fully on GPU. But it introduces a second model registry/format lifecycle, is optimized for local convenience rather than a pinned, high-throughput pod appliance, and the current official OpenAI page does not establish `/v1/models` as the readiness endpoint. A wrapper/native-tags probe could make it work, but that is not Pitwall's minimal contract. Support later only if an Ollama adapter explicitly performs the native model readiness check and proxies known-compatible `/v1` traffic; do not use it for the existing exact-GGUF cards. [Ollama FAQ](https://docs.ollama.com/faq)

## KTransformers — support later

KTransformers is a heterogeneous CPU/GPU inference framework intended especially for large models where GPU VRAM is deliberately supplemented by substantial host RAM. Its server documentation says its ChatCompletion interface is compatible with OpenAI and Ollama, and its project documents examples that run very large quantized MoE models with 14–21 GB of VRAM but hundreds of GB of system DRAM. [server documentation](https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/api/server/server.md), [project README](https://github.com/kvcache-ai/ktransformers)

That makes it technically interesting for a one-GPU RunPod pod with a huge-RAM requirement, but not a good current `serve-model` default: model-specific operator configs, CPU/RAM dependence, and unverified `/v1/models`/`/health` contract require an adapter and benchmarks. Treat “OpenAI-compatible” as ChatCompletion compatibility, not proof of Pitwall's strict models readiness. Support later for named large-MoE configurations; not for ordinary single-GPU GGUF deployment.

## TokenSpeed — never for now (revisit after production release)

TokenSpeed is the LightSeek engine referenced in the newer Qwen cards: it describes itself as an agentic-workload engine targeting TensorRT-LLM-level performance with vLLM-like usability. Its repository states plainly that the current release is a preview, under heavy development, and **not for production deployments**; at the research date its documented support work targets Blackwell/Hopper-class systems and future Qwen/DeepSeek/MiniMax work. [TokenSpeed README](https://github.com/lightseekorg/tokenspeed), [TokenSpeed release](https://github.com/lightseekorg/tokenspeed/releases)

It may expose compatible APIs through its own server/gateway ecosystem, but the current primary evidence is insufficient to establish the exact `GET /health` plus `GET /v1/models` contract for Pitwall, and it is not a consumer/single-GPU general engine: a project issue says supported NVIDIA GPUs are H100, H200, B200, B300, GB200, and GB300 and calls for CUDA 13/driver >=580. Verdict: **never for current `serve-model`** (not a permanent ban); reconsider only after a stable production release with a pinned image, documented native OpenAI/models/health endpoints, and a validated Qwen recipe. [support issue](https://github.com/lightseekorg/tokenspeed/issues/521)

## SGLang-Omni — pointer only; covered by the SGLang dossier

SGLang-Omni is not an additional default engine choice in this unit. Treat it as the multimodal/omni branch of SGLang and use the dedicated SGLang engineering dossier for supported images, launch flags, GPU sizing, and endpoint qualification. This avoids duplicating rapidly changing server details here. **Later** for `serve-model`, contingent on that dossier's documented OpenAI `/v1/models` and health readiness contract; no independent engine adapter decision is made in this document.

## RunPod-specific images/templates — support as bases, not engines

RunPod supports pods created from either a custom image or a template. Its documented `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` base includes PyTorch 2.8.0, CUDA 12.8.1, and Ubuntu 24.04; the CLI reference also demonstrates custom `runpod/pytorch:*` images, container disk, and a `/workspace` volume. These are useful substrate images for a custom vLLM/SGLang/TensorRT build, not model servers: they do not by themselves provide OpenAI endpoints, `/health`, or `/v1/models`. [custom-template guide](https://docs.runpod.io/pods/templates/create-custom-template), [pod create reference](https://docs.runpod.io/runpodctl/reference/runpodctl-pod)

Therefore, **support now as base-image/template input only**. Prefer the engine vendor's official serving image whenever one exists (llama.cpp's `server-cuda*`, vLLM, SGLang, or NVIDIA NGC); use `runpod/pytorch` only when building a pinned custom image. No RunPod-owned generic “serving image” was verified as satisfying Pitwall readiness, so it must not be advertised as an engine.

## Fit matrix

| Engine | OpenAI-compatible? | Single-GPU friendly? | Fits strict Pitwall proxy + `/v1/models` readiness? | `serve-model` verdict |
|---|---|---|---|---|
| TensorRT-LLM | Yes, documented | Yes when model/GPU supported | Yes, documented `/health` + `/v1/models` | Later |
| Ollama | Partial `/v1` compatibility | Yes | Unverified for `/v1/models`; native tags endpoint exists | Later, adapter needed |
| KTransformers | ChatCompletion-compatible | Conditional; often CPU/RAM hybrid | Unverified | Later, named configs only |
| TokenSpeed | Unverified by current primary server docs | No general consumer-GPU claim | Unverified | Never now / revisit |
| SGLang-Omni | See SGLang dossier | See SGLang dossier | See SGLang dossier | Later / dossier-owned |
| RunPod PyTorch/templates | No (base only) | N/A | No | Base image only |

## Sources

- https://nvidia.github.io/TensorRT-LLM/commands/trtllm-serve.html — `trtllm-serve`, OpenAI endpoints, health/models, host/port and model argument — accessed 2026-08-27
- https://nvidia.github.io/TensorRT-LLM/quick-start-guide.html — release container and serving quick start — accessed 2026-08-27
- https://github.com/ollama/ollama/blob/main/Dockerfile — official image entrypoint, bind env, exposed port — accessed 2026-08-27
- https://docs.ollama.com/api/openai-compatibility — documented OpenAI-compatible surface — accessed 2026-08-27
- https://docs.ollama.com/faq — model location, host configuration, GPU status and pull/network behavior — accessed 2026-08-27
- https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/api/server/server.md — KTransformers server/API claims — accessed 2026-08-27
- https://github.com/kvcache-ai/ktransformers — heterogeneous/large-MoE examples — accessed 2026-08-27
- https://github.com/lightseekorg/tokenspeed — preview/not-production disposition and development scope — accessed 2026-08-27
- https://github.com/lightseekorg/tokenspeed/releases — 0.1.0 release evidence — accessed 2026-08-27
- https://github.com/lightseekorg/tokenspeed/issues/521 — supported GPU/CUDA/driver statement — accessed 2026-08-27
- https://docs.runpod.io/pods/templates/create-custom-template — RunPod PyTorch image contents/templates — accessed 2026-08-27
- https://docs.runpod.io/runpodctl/reference/runpodctl-pod — template/custom image, disk and volume options — accessed 2026-08-27
- ../../../../src/pitwall/serve.py — implemented Pitwall pod readiness and `/v1/models` verification contract — reviewed 2026-08-30

## Open questions

- Pin and smoke-test exact vendor image digests before adding TensorRT-LLM or any SGLang configuration to an adapter catalog.
- Verify whether the installed Ollama version exposes `GET /v1/models`, or retain a native `/api/tags` readiness adapter instead of weakening Pitwall's engine contract.
- Re-evaluate TokenSpeed only after its project removes the preview/not-for-production warning and publishes a complete server endpoint/image guide.
