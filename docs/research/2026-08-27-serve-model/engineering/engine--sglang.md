# Engine dossier: SGLang and SGLang-Omni

**Research date:** 2026-08-27. This is an operations dossier for Pitwall's `serve-model` pod lease, not a claim that every model supports every SGLang feature.

## Decision summary

Use the pinned SGLang runtime image for a text, vision, embedding, or supported autoregressive model when its cookbook recipe (or the model vendor) names SGLang and the exact model/parser combination has been smoke-tested. The current documented release branch is **v0.5.18**; pin `lmsysorg/sglang:v0.5.18` (or its matching `-runtime` variant) instead of mutable `latest`/`dev`. SGLang documents CUDA 13 as the default image environment and offers CUDA-12/CUDA-12.9-suffixed variants. The current Dockerfile’s base is CUDA 13.0.3, so treat CUDA 13.0 as the image’s CUDA userspace floor; the required host NVIDIA driver is **unverified** and must be checked against the selected image manifest/RunPod host. [install](https://docs.sglang.io/docs/get-started/install), [Dockerfile](https://raw.githubusercontent.com/sgl-project/sglang/main/docker/Dockerfile), [tags](https://hub.docker.com/r/lmsysorg/sglang/tags)

SGLang-Omni is a separate project/package for multi-stage audio and omni models. It exposes OpenAI-compatible `/v1/audio/speech` and `/v1/chat/completions`, but the published MiniMax-Music3 material does **not** establish a maintained `lmsysorg/sglang-omni` image, `/health` contract, image CUDA floor, or startup estimate; all four are unverified. [SGLang-Omni README](https://github.com/sgl-project/sglang-omni), [MiniMax-Music3 cookbook](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/minimax_music3.md?plain=1)

## Pod launch contract

The current Dockerfile has no serving `ENTRYPOINT` and ends in `CMD ["/bin/bash"]`; the official Docker examples instead append an explicit server command. For a normal SGLang pod, `docker_start_cmd` therefore begins with `python3 -m sglang.launch_server`; the model is supplied by `--model-path <Hugging Face repo ID or mounted path>`, never as a vLLM-style positional model argument. Use `--host 0.0.0.0`, because the SGLang default host is loopback, and use the Pod’s exposed port (Pitwall convention below uses 8000; SGLang’s native default is 30000). [Dockerfile](https://raw.githubusercontent.com/sgl-project/sglang/main/docker/Dockerfile), [install](https://docs.sglang.io/docs/get-started/install), [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments)

```yaml
image: lmsysorg/sglang:v0.5.18-runtime
docker_start_cmd:
  - python3
  - -m
  - sglang.launch_server
  - --model-path
  - org/model-id
  - --served-model-name
  - model-id
  - --host
  - 0.0.0.0
  - --port
  - "8000"
env:
  HF_TOKEN: "<only for gated/private Hub repos>"
```

`--served-model-name model-id` is required operationally: it overrides the identifier returned by `/v1/models`, which is the exact model identity Pitwall must match after readiness. Probe `GET /health_generate` (SGLang’s own runtime test waits for HTTP 200 there), then `GET /v1/models`; accept the pod only if the listed ID is `model-id`. `/health` is also implemented and is used for non-rank-0 distributed health checks, but `/health_generate` is the stronger model-serving probe. [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments), [runtime endpoint](https://github.com/sgl-project/sglang/blob/main/python/sglang/lang/backend/runtime_endpoint.py), [compile health-check code](https://github.com/sgl-project/sglang/blob/main/python/sglang/compile_deep_gemm.py)

The normal OpenAI-compatible surface is `/v1/chat/completions` and `/v1/completions`; SGLang also documents vision and embeddings APIs. Chat applies the Hub tokenizer template when present, and supports a `--chat-template` override. Reasoning parsers return `reasoning_content` separately from final `content`. [OpenAI APIs](https://docs.sglang.io/docs/basic_usage/openai_api_completions), [reasoning parser](https://docs.sglang.io/docs/advanced_features/separate_reasoning)

## Flags to materialize in `serve-model`

Only pass a model-specific value when its model recipe/vendor card supports it; do not turn every optional flag on by default.

| Flag | Operational meaning / Pitwall guidance |
|---|---|
| `--served-model-name` | Stable public ID returned from `/v1/models`; set it on every pod. |
| `--host`, `--port` | HTTP bind host (default `127.0.0.1`) and port (default `30000`); use `0.0.0.0`, `8000` for a RunPod proxy template. |
| `--context-length` | Maximum context; omit to honor `config.json`, or lower deliberately to fit KV cache. |
| `--mem-fraction-static` | Static model/KV fraction. Lower it after OOM; automatic sizing falls back to 0.88 when GPU memory is undetectable. |
| `--tp` | Tensor parallel degree; use only across peer-visible GPUs. The documented `--tp 2` launch form enables two-GPU TP (the long form is `--tensor-parallel-size`/`--tp-size`). |
| `--dp` | Data-parallel degree (documented example uses the router launcher); use replicas for throughput only when a single replica fits. The server’s long form is `--data-parallel-size`/`--dp-size`. |
| `--ep` | Expert-parallel degree for MoE; long forms are `--expert-parallel-size`/`--ep-size`. Match it to the model recipe and all-to-all topology. |
| `--quantization` | Select only a checkpoint-supported method; official choices include `awq`, `fp8`, `gptq`, `gguf`, `bitsandbytes`, `modelopt*`, and others. |
| `--kv-cache-dtype` | Use documented `fp8_e4m3` or `fp8_e5m2` only after accuracy validation; provide scaling factors through `--quantization-param-path` when required. |
| `--trust-remote-code` | Off by default; it permits custom Hub modeling code. Enable only for a reviewed, model-required repository. |
| `--tool-call-parser` | Match model wire format: current server-argument names include `deepseekv3`, `deepseekv31`, `glm`, `glm45`, `glm47`, `gpt-oss`, `kimi_k2`, `llama3`, `mistral`, `pythonic`, `qwen`, `qwen25`, `qwen3_coder`, `step3`, and `gigachat3`; parser documentation also identifies model/template pairings. |
| `--reasoning-parser` | Current names: `deepseek-r1`, `deepseek-v3`, `glm45`, `gpt-oss`, `kimi`, `qwen3`, `qwen3-thinking`, `step3`; it controls separation into `reasoning_content`. |
| `--chat-template` | Built-in name or Jinja/template file path for the OpenAI server; use it where the recipe says so (notably some DeepSeek tool formats). |
| `--speculative-algorithm`, `--speculative-draft-model-path` | Optional speculative decode. Algorithms are `EAGLE`, `EAGLE3`, `NEXTN`, `STANDALONE`, or `NGRAM`; the draft argument accepts a local directory or HF repo ID. Treat target/draft pairing as recipe-specific. |
| `--json-model-override-args` | JSON dictionary that overrides model configuration; keep model-specific overrides in an auditable template rather than silently changing defaults. |

Flag names, defaults, aliases, and supported enumerations above are from the [server-arguments reference](https://docs.sglang.io/docs/advanced_features/server_arguments); FP8 KV/cache caution is also in its [common launch guidance](https://docs.sglang.io/docs/advanced_features/server_arguments).

## Environment and persistence

Mount a persistent volume at the image’s documented `/root/.cache/huggingface` cache path and use `HF_TOKEN` for gated/private Hub models. `HF_HUB_ENABLE_HF_TRANSFER` is a Hugging Face transfer optimization, not a SGLang requirement; leave it model/template-controlled. SGLang’s own runtime environment variables use the `SGLANG_` prefix (`SGL_` is deprecated). Examples worth making explicit in a template are `CUDA_VISIBLE_DEVICES` for GPU selection, `SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION` only if its health semantics are deliberately changed, and `SGLANG_WAIT_PORT_TIMEOUT` only in test/launcher contexts. Exact environment-variable behavior must be pinned to the image version. [install](https://docs.sglang.io/docs/get-started/install), [environment variables](https://docs.sglang.io/docs/references/environment_variables), [health issue](https://github.com/sgl-project/sglang/issues/30770)

For container process coordination, include `--ipc=host` or equivalent adequate shared memory; SGLang explicitly calls out shared memory for Docker/Kubernetes multi-process communication and its official Docker example uses both `--shm-size 32g` and `--ipc=host`. [install](https://docs.sglang.io/docs/get-started/install)

## Startup timeline and readiness

No portable minute estimate exists: checkpoint size, cache state, Hub access, GPU count, kernel compilation/warm-up, and model architecture dominate it. Record `startup_time_estimate_min: unverified` for a generic engine template; set a model-specific estimate only from measured cold-pod evidence.

1. RunPod schedules the pod, pulls the pinned image, mounts disk, and injects environment variables.
2. `launch_server` downloads or resolves `--model-path`, loads tokenizer/config and weights, starts distributed workers where selected, allocates static/KV memory, then warm-ups unless `--skip-server-warmup` was chosen.
3. Poll `/health_generate` until 200; do not equate an open TCP port with readiness.
4. Poll `/v1/models` and require the configured `--served-model-name`; only then arm Pitwall’s pod lease proxy.

The warm-up flag/default and health behavior are documented in [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments) and [SGLang’s readiness implementation](https://github.com/sgl-project/sglang/blob/main/python/sglang/lang/backend/runtime_endpoint.py). Pitwall’s own tests show its pod proxy probes `/health`; keep the stronger model-list identity test in `serve-model`. Local evidence: `tests/runpod_client/test_pods.py:262`.

## Multi-GPU and RunPod fit

Use one-node TP first for a model that does not fit on a single canonical Pitwall GPU; SGLang documents `--tp 2` and advises `--enable-p2p-check` if peer access fails. DP is a throughput deployment that may be combined with TP; its documented example launches `sglang_router` with `--dp 2 --tp 2` (four GPUs). EP is for MoE expert placement and needs its recipe’s all-to-all backend/topology. Multi-node requires `--dist-init-addr`, `--nnodes`, and `--node-rank`; a normal one-pod lease should therefore be treated as single-node unless the template is expressly distributed. [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments)

Choose only Pitwall’s canonical names for a template: `NVIDIA H100 80GB HBM3`, `NVIDIA H100 NVL`, `NVIDIA H200`, `NVIDIA H200 NVL`, `NVIDIA B200`, `NVIDIA A100 80GB`, `NVIDIA A100 80GB PCIe`, `NVIDIA A100 40GB`, `NVIDIA A6000`, `NVIDIA RTX A6000`, `NVIDIA A40`, `NVIDIA L40`, `NVIDIA L40S`, `NVIDIA L4`, `NVIDIA RTX 6000 Ada`, `NVIDIA RTX 4090`, `NVIDIA GeForce RTX 4090`, `NVIDIA RTX A5000`, `NVIDIA RTX A4500`, `NVIDIA RTX A4000`, `NVIDIA RTX 5000 Ada Generation`, or `NVIDIA RTX 4000 Ada Generation`. GPU sizing is model/checkpoint/quantization/context-specific and is not supplied by this generic engine dossier.

## Cookbook-first workflow

The [SGLang Cookbook](https://docs.sglang.io/cookbook/intro) is a community-maintained repository intended to answer how to deploy a model on specified hardware for a specified task. Before enabling an engine entry, browse its Autoregressive, Diffusion, or VLA sections; search the model vendor/repo name in the cookbook and use that recipe’s image/version, parallelism, parser, attention backend, and hardware guidance. Examples already relevant to this research wave are the [GLM-5.3-Flash recipe](https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash) (H100/H200/B200/B300/GB200/GB300 starting points) and the separate [SGLang-Omni MiniMax-Music3 recipe](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/minimax_music3.md?plain=1). Cookbook hardware guidance is a starting point, not a substitute for a cold-start and representative-request acceptance test.

## SGLang-Omni: audio/music serving

For MiniMaxAI/MiniMax-Music3, the documented command is `CUDA_VISIBLE_DEVICES=0 sgl-omni serve --model-path MiniMaxAI/MiniMax-Music3 --port 8000`; `CUDA_VISIBLE_DEVICES=0,1` is the documented dual-GPU option (AR on GPU 0; DiT/DAV on GPU 1). Send `POST /v1/audio/speech`, with `model`, lyric `input`, and musical-description `instructions`; the cookbook documents a 32-kHz stereo WAV response and non-streaming Music3 generation. This is an audio-speech endpoint, not `/v1/chat/completions`; verify that the deployed Pitwall proxy permits this path before advertising the capability. [MiniMax-Music3 cookbook](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/minimax_music3.md?plain=1), [SGLang-Omni README](https://github.com/sgl-project/sglang-omni)

## Known failure modes and mitigations

- **Pod never becomes reachable:** SGLang defaults to `127.0.0.1`; set `--host 0.0.0.0` and expose the chosen `--port`. [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments)
- **False-ready model service:** a listening port or `/health` alone does not prove weight load/model identity. Gate on `/health_generate` and then exact `/v1/models` ID. [runtime endpoint](https://github.com/sgl-project/sglang/blob/main/python/sglang/lang/backend/runtime_endpoint.py)
- **OOM at load or request time:** lower `--mem-fraction-static`; for long-prompt prefill lower `--chunked-prefill-size`; do not blindly increase context. [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments)
- **FP8 accuracy regression:** FP8 KV cache may need a `--quantization-param-path`; without scaling factors SGLang warns it defaults to 1.0 and can harm accuracy. [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments)
- **Two GPUs cannot communicate:** add `--enable-p2p-check` to diagnose the documented peer-access failure; choose a suitable one-host interconnect. [server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments)
- **FlashInfer/kernel failure:** its default backend supports sm75+; SGLang advises `--attention-backend triton --sampling-backend pytorch` when FlashInfer fails. [install](https://docs.sglang.io/docs/get-started/install)
- **Parser/template mismatch:** tools or reasoning may be malformed/silent if parser names and required template do not match the model. Use the parser table and model cookbook, then contract-test tool responses. [tool parser](https://docs.sglang.io/docs/advanced_features/tool_parser), [reasoning parser](https://docs.sglang.io/docs/advanced_features/separate_reasoning)
- **Health starvation under synchronous preprocessing:** a current upstream issue reports `/health` and `/health_generate` sharing a generation-based handler when `SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION` is true. Keep readiness retries bounded and investigate sustained probe latency rather than treating it as a pure network failure. [upstream issue](https://github.com/sgl-project/sglang/issues/30770)
- **SGLang-Omni Music3 output shape:** an open upstream report says compressed formats downmix stereo Music3 output; request WAV and re-encode outside the service until fixed/verified on the pinned commit. [issue #1549](https://github.com/sgl-project/sglang-omni/issues/1549)

## When SGLang is preferable to vLLM

Prefer SGLang only where evidence is model/workload-specific: (1) the vendor or SGLang cookbook supplies a tested recipe unavailable in vLLM, particularly a supported SGLang-Omni multi-stage audio pipeline; (2) the workload benefits from SGLang’s session/radix-cache or its supported speculative/parallelism path and a like-for-like benchmark verifies the gain; or (3) a supported model requires one of SGLang’s parser/template integrations. The original SGLang paper reports up to 6.4× throughput and 3.7× lower latency on its evaluated structured-program workloads versus baselines including vLLM; this is vendor/project research evidence, not a universal engine ranking. [SGLang paper](https://openreview.net/attachment?id=VqkAKQibpq&name=pdf), [SGLang cookbook](https://docs.sglang.io/cookbook/intro), [SGLang-Omni README](https://github.com/sgl-project/sglang-omni)

Retain vLLM when its official model recipe is the tested deployment path, when the required SGLang parser/recipe is absent, or when an apples-to-apples benchmark for the actual model, GPU, context mix, concurrency, accuracy/tool success, TTFT, and throughput does not show an SGLang advantage. This is a validation criterion, not an unsupported performance opinion.

## Sources

- https://docs.sglang.io/docs/get-started/install — release branch, image use/tags, Docker command/cache/env/shared-memory, CUDA image variants, FlashInfer troubleshooting — accessed 2026-08-27
- https://hub.docker.com/r/lmsysorg/sglang/tags — current published image tag families and architectures — accessed 2026-08-27
- https://raw.githubusercontent.com/sgl-project/sglang/main/docker/Dockerfile — current default CUDA 13.0.3 Dockerfile base — accessed 2026-08-27
- https://docs.sglang.io/docs/advanced_features/server_arguments — launch flags, defaults, parser names, parallelism, quantization, speculative decoding, warm-up — accessed 2026-08-27
- https://docs.sglang.io/docs/basic_usage/openai_api_completions — OpenAI-compatible chat/completions, template behavior and streaming — accessed 2026-08-27
- https://docs.sglang.io/docs/advanced_features/tool_parser — tool parser/model/template combinations — accessed 2026-08-27
- https://docs.sglang.io/docs/advanced_features/separate_reasoning — reasoning parser names, model behavior, OpenAI `reasoning_content` shape — accessed 2026-08-27
- https://docs.sglang.io/docs/references/environment_variables — `SGLANG_` environment-variable namespace — accessed 2026-08-27
- https://github.com/sgl-project/sglang/blob/main/python/sglang/lang/backend/runtime_endpoint.py — `/health_generate` readiness polling — accessed 2026-08-27
- https://github.com/sgl-project/sglang/blob/main/python/sglang/compile_deep_gemm.py — `/v1/models` rank-0 and `/health` non-rank-0 readiness checks — accessed 2026-08-27
- https://docs.sglang.io/cookbook/intro — cookbook purpose and model/hardware/task workflow — accessed 2026-08-27
- https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash — GLM SGLang cookbook reference used by the adjacent GLM dossier — accessed 2026-08-27
- https://github.com/sgl-project/sglang-omni — Omni project and OpenAI-compatible audio/chat surface — accessed 2026-08-27
- https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/minimax_music3.md?plain=1 — MiniMax Music3 commands, multi-GPU split, audio API and response — accessed 2026-08-27
- https://github.com/sgl-project/sglang/issues/30770 — current health endpoint starvation report — accessed 2026-08-27
- https://github.com/sgl-project/sglang-omni/issues/1549 — current Music3 compressed-output stereo downmix report — accessed 2026-08-27
- https://openreview.net/attachment?id=VqkAKQibpq&name=pdf — SGLang paper’s bounded throughput/latency comparison — accessed 2026-08-27

## Open questions

- Validate the exact digest, CUDA userspace and host-driver floor for the selected `v0.5.18`/`-runtime` image before creating a production RunPod template; the generic docs establish CUDA variant selection, not a RunPod driver floor.
- Measure cold-cache and warm-cache startup per model/GPU/image, including model download and warm-up; no generic, sourced minute estimate was found.
- Verify Pitwall’s upstream proxy path allowlist for `/v1/audio/speech` and define a non-LLM capability convention for Omni audio. The cited adjacent Music3 dossier also marks this unverified.
- Verify a maintained, reproducible SGLang-Omni container image and its health endpoint before supporting Omni via `serve-model`; source install rather than an official image is documented.
- Reconcile the generic SGLang parser-name list against the exact pinned image: the server-arguments and tool-parser pages are evolving and their parser tables are not identical.
