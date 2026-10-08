---
model_id: "meta-models/Muse-Glimmer-30B"
vendor: "meta-models (Meta Inc.)"
family: "Muse Glimmer"
release_date: "2026-08-09"
license:
  name: "Apache-2.0"
  url: "https://huggingface.co/meta-models/Muse-Glimmer-30B/blob/main/LICENSE"
  gated: false
architecture:
  kind: "dense"
  params_total_b: 29.776626688
  params_active_b: 29.776626688
  context_length_max: 131072
  modalities: ["text", "image", "video"]
  thinking_mode: "optional"
weights:
  format: "safetensors"
  dtype: "bf16"
  size_gb: 59.553253376
  quantized_variants:
    - repo: "meta-models/Muse-Glimmer-30B-GGUF"
      method: "GGUF-Q4_K_M (KQuant-17GB)"
      size_gb: 16.756683904
      url: "https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF"
    - repo: "meta-models/Muse-Glimmer-30B-GGUF"
      method: "GGUF-Q4_K_XL (KQuant-Dynamic)"
      size_gb: 19.653960832
      url: "https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF"
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-openai:muse-glimmer"
  engine_min_version: "unverified; use dedicated muse-glimmer image or a vLLM build containing merged PR #51655"
  docker_start_cmd:
    - "meta-models/Muse-Glimmer-30B"
    - "--served-model-name"
    - "muse-glimmer-30b"
    - "--port"
    - "8000"
    - "--tensor-parallel-size"
    - "1"
    - "--enable-auto-tool-choice"
    - "--tool-call-parser"
    - "muse_glimmer"
    - "--reasoning-parser"
    - "muse_glimmer"
    - "--generation-config"
    - "auto"
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "muse_glimmer"
  reasoning_parser: "muse_glimmer"
  chat_template_notes: "ATEM tool calls; set reasoning through `Reasoning strength: low|medium|high|xhigh` in the system prompt. Do not stop on <|eom|>; generation config uses <|end_of_text|> and <|eot|>."
hardware:
  min_vram_gb_native: 80
  min_vram_gb_quantized: 24
  recommended_gpu_classes: ["NVIDIA H100 80GB HBM3", "NVIDIA H200", "NVIDIA A100 80GB", "NVIDIA RTX 4090"]
  tensor_parallel: 1
  container_disk_gb: 75
  startup_time_estimate_min: unverified
capabilities:
  tool_calling: "yes"
  structured_outputs: "limited: current vLLM issue reports schema enforcement conflicts with muse_glimmer reasoning"
  vision: true
  languages: "more than 100 languages claimed"
pitwall:
  capability_name: "llm.muse-glimmer-30b"
  served_model_name: "muse-glimmer-30b"
  example_serve_model: >-
    pitwall serve --capability llm.muse-glimmer-30b --model meta-models/Muse-Glimmer-30B
    --gpu-class "NVIDIA H100 80GB HBM3" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --enable-auto-tool-choice --start-arg --tool-call-parser --start-arg muse_glimmer
confidence:
  overall: "medium"
  notes: "Core recipe is sourced from a community cookbook and current vLLM support page; the cookbook explicitly says its vLLM path is not end-to-end attested."
accessed: "2026-08-27"
---

# meta-models/Muse-Glimmer-30B

## Summary

Muse Glimmer-30B is Meta's public, Apache-2.0, dense multimodal (text/image/video input, text output) agentic model. The exact HF repository was confirmed public (HF API HTTP 200); use the dedicated `vllm/vllm-openai:muse-glimmer` image for its native model and parser support, with an 80-GB GPU for the BF16 checkpoint. [HF API](https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B) [vLLM supported models](https://docs.vllm.ai/en/latest/models/supported_models/)

## Deployment recipe

1. Launch `vllm/vllm-openai:muse-glimmer` with the listed `docker_start_cmd`. The official-community cookbook says the image entrypoint is `vllm serve`, this model needs no `trust_remote_code`, and ordinary `pip install vllm` was not then sufficient because support was an unmerged PR. [Cookbook](https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md)
2. Set `--enable-auto-tool-choice --tool-call-parser muse_glimmer --reasoning-parser muse_glimmer --generation-config auto`; use the explicit served ID `muse-glimmer-30b`. [Cookbook](https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md)
3. Choose `NVIDIA H100 80GB HBM3` (one GPU / TP=1). The Hugging Face release article calls one 80-GB H100 the practical BF16 inference floor; the cookbook budgets 72 GB and the model card's two BF16 weight shards total 59,553,253,376 bytes. [HF release article](https://huggingface.co/blog/muse-glimmer) [HF tree API](https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B/tree/main?recursive=false&expand=true)
4. No HF token is required for this public, ungated model. Allow at least 75 GB persistent disk for weights and cache. Startup-time evidence was not found; validate readiness with `GET /v1/models`, which must list `muse-glimmer-30b`. [HF API](https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B)

## Hardware and quantization

The base repo is BF16 safetensors in two shards, 59.553 GB/29.7766B parameters. Its `config.json` specifies 131,072 positions; its full-context KV-cache allocation is workload dependent, so the safe documented native floor is one 80-GB H100 rather than a derived VRAM claim. [config.json](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/config.json) [safetensors index](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/model.safetensors.index.json) [HF release article](https://huggingface.co/blog/muse-glimmer)

The vendor also publishes `meta-models/Muse-Glimmer-30B-GGUF`, for llama.cpp: `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` is 16.757 GB, `Muse-Glimmer-30B-KQuant-Dynamic-Q4_K_XL.gguf` is 19.654 GB, the image mmproj is 1.400 GB, and the optional DFlash drafter is 1.631 GB. The vendor calls the first an approximately 20-GB (weights plus working context) vision+drafter path for 24-GB VRAM, and the second a 23-GB/32-GB-VRAM path. Its documented OpenAI-compatible alternative is `llama-server -m Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf --mmproj mmproj-Muse-Glimmer-30B-Q4_K_M.gguf -a muse-glimmer-30B -ngl 99 -c 131072 -np 4 --host 0.0.0.0 --port 8000 --jinja --temp 1.0 --top-p 0.95 --top-k 64`, requiring llama.cpp `b10353` or newer; it therefore fits Pitwall’s chat/completions proxy, but needs a CUDA llama.cpp server image rather than the default vLLM image. (The exact published container image was not found.) [GGUF model card](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF) [GGUF tree API](https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B-GGUF/tree/main?recursive=false&expand=true)

A LocalLLaMA user independently reported Q4_K_XL plus mmproj and DFlash at 262,144 context using about 22–23 GB on an RTX 3090. Treat this as community evidence, not a launch guarantee; the 3090 is not a permitted Pitwall class, and the nearest listed consumer class is `NVIDIA RTX 4090`. [LocalLLaMA report](https://www.reddit.com/r/LocalLLaMA/comments/1vkm42m/muse_glimmer_actually_fits_on_a_single_rtx_3090/)

## Tool calling, reasoning, and chat template

The vLLM parser values are exactly `muse_glimmer` for both tool calls and reasoning. The Jinja template implements ATEM markup (`<atem:function_calls>`) and accepts optional reasoning strengths `low`, `medium`, `high`, and `xhigh` via the system prompt. Its official generation config has EOS IDs 200001 (`<|end_of_text|>`) and 200008 (`<|eot|>`); the cookbook warns not to add `<|eom|>` as a stop token because it is an end-of-message rather than an end-of-turn token. [Cookbook](https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md) [chat template](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/chat_template.jinja) [generation config](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/generation_config.json)

## Known issues and community notes

The cookbook labels its vLLM server route “unverified end to end,” despite describing the dedicated image and parsers; do not treat it as production-validated without a paid-pod smoke test. It also notes one tool call per assistant message, so multiple tool calls may arrive as consecutive assistant messages rather than one `tool_calls` array. [Cookbook](https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md)

Current vLLM documentation lists both Muse architectures as supported and says the vision-config checkpoint accepts images and video, with the assistant checkpoint served through DFlash. However, a current vLLM issue reports that structured outputs and `--reasoning-parser muse_glimmer` conflict: enabling schemas can suppress reasoning, while the default can silently fail to enforce the schema. Avoid advertising reliable JSON-schema output until a fixed release is tested. [vLLM supported models](https://docs.vllm.ai/en/latest/models/supported_models/) [vLLM issue #52594](https://github.com/vllm-project/vllm/issues/52594)

For llama.cpp, versions at or below `b10344` did not recognize the `muse-glimmer` architecture; the vendor GGUF card says `b10353` was the first supporting release. [llama.cpp issue #26858](https://github.com/ggml-org/llama.cpp/issues/26858) [GGUF model card](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF)

`noonghunna/club-3090` exists and the linked LocalLLaMA discussion points to its Muse Glimmer discussion; it is relevant consumer-GPU community context but does not validate the vLLM/Pitwall recipe. [club-3090 discussion](https://github.com/noonghunna/club-3090/discussions/976) [LocalLLaMA report](https://www.reddit.com/r/LocalLLaMA/comments/1vkm42m/muse_glimmer_actually_fits_on_a_single_rtx_3090/)

## Sources

- https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B — exact public ID, ungated state, metadata, sibling files, parameters — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B — model card: architecture, modalities, local quantization claims, license — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/config.json — architecture, BF16 dtype, 131,072 context — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/generation_config.json — generation defaults and EOS IDs — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/chat_template.jinja — ATEM/tool/reasoning template behavior — accessed 2026-08-27
- https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B/tree/main?recursive=false&expand=true — file list and exact artifact sizes — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF — GGUF files, llama.cpp version floor, server recipe, VRAM claims, OpenAI API behavior — accessed 2026-08-27
- https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B-GGUF/tree/main?recursive=false&expand=true — exact GGUF/mmproj/drafter artifact sizes — accessed 2026-08-27
- https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B-assistant — public DFlash assistant checkpoint metadata — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/models/supported_models/ — native vLLM Muse architecture, multimodal and DFlash support — accessed 2026-08-27
- https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md — dedicated vLLM image, exact parser/start flags, caveats — accessed 2026-08-27
- https://huggingface.co/blog/muse-glimmer — vLLM transformers-backend and llama.cpp/DFlash examples; 80-GB HF inference floor — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vkm42m/muse_glimmer_actually_fits_on_a_single_rtx_3090/ — reported consumer-GPU GGUF deployment — accessed 2026-08-27
- https://github.com/noonghunna/club-3090/discussions/976 — relevant operator community repository/discussion — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/52594 — reported current reasoning/structured-output conflict — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/26858 — legacy llama.cpp architecture-recognition failure — accessed 2026-08-27

## Open questions

- Exact minimum released vLLM version/tag and whether the dedicated `:muse-glimmer` image is still required; searched current vLLM supported-models page, HF release article, cookbook, and the merged-PR timeline, which do not identify a confirmed tagged wheel.
- Exact CUDA `llama-server` container image/tag carrying llama.cpp `b10353` or newer; searched the GGUF card, HF release article, llama.cpp sources, and cookbook.
- A source-backed startup-time estimate and a tested full Pitwall readiness run.
- Whether vLLM structured outputs are explicitly validated for this model; searched model card, HF release article, vLLM supported-model list, and cookbook.
