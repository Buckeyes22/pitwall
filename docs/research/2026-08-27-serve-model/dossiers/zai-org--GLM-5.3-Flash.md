---
model_id: "zai-org/GLM-5.3-Flash"
vendor: "zai-org"
family: "GLM-5"
release_date: "2026-08-25"
license:
  name: "MIT"
  url: "https://huggingface.co/zai-org/GLM-5.3-Flash"
  gated: false
architecture:
  kind: "moe"
  params_total_b: 321
  params_active_b: 18
  context_length_max: 1048576
  modalities: ["text", "image", "video"]
  thinking_mode: "always"
weights:
  format: "safetensors"
  dtype: "fp8"
  size_gb: 328.3
  quantized_variants:
    - {repo: "zai-org/GLM-5.3-Flash-BF16", method: "BF16", size_gb: unverified, url: "https://huggingface.co/zai-org/GLM-5.3-Flash-BF16"}
    - {repo: "unsloth/GLM-5.3-Flash-GGUF", method: "GGUF", size_gb: unverified, url: "https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF"}
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-openai:glm53-flash"
  engine_min_version: "0.27.0"
  docker_start_cmd: ["zai-org/GLM-5.3-Flash", "--served-model-name", "glm-5.3-flash", "--port", "8000", "--tensor-parallel-size", "4", "--tool-call-parser", "glm47", "--reasoning-parser", "glm45", "--enable-auto-tool-choice"]
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "glm47"
  reasoning_parser: "glm45"
  chat_template_notes: "Thinking always opens; reasoning_effort accepts low/high, otherwise max. Text/image/video and XML tool calls."
hardware:
  min_vram_gb_native: 772
  min_vram_gb_quantized: 386
  recommended_gpu_classes: ["NVIDIA H200", "NVIDIA B200"]
  tensor_parallel: 4
  container_disk_gb: unverified
  startup_time_estimate_min: unverified
capabilities:
  tool_calling: "yes; auto tool choice with glm47"
  structured_outputs: "unverified"
  vision: true
  languages: "English and Chinese (HF tags); broader coverage unverified"
pitwall:
  capability_name: "llm.glm-5-3-flash"
  served_model_name: "glm-5.3-flash"
  example_serve_model: >-
    pitwall serve --capability llm.glm-5-3-flash --model zai-org/GLM-5.3-Flash
    --gpu-class "NVIDIA H200" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --tensor-parallel-size --start-arg 4 --start-arg --max-model-len --start-arg 131072
confidence:
  overall: "high"
  notes: "Official recipe supplies image, parser flags, version floor, and FP8/BF16 VRAM minima; one 80-GB GPU is not viable."
accessed: "2026-08-27"
---

# zai-org/GLM-5.3-Flash

## Summary
GLM-5.3-Flash is a natively multimodal 321B-total/18B-active MoE with hybrid KDA linear attention and sparse MLA, native FP8 weights, MTP, and a declared one-million-token context. It offers OpenAI-compatible chat via vLLM, but remains multi-GPU: use the dedicated GLM image with TP=4, not one 80-GB GPU. [HF card](https://huggingface.co/zai-org/GLM-5.3-Flash), [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)

## Deployment recipe
Use `vllm/vllm-openai:glm53-flash`; official vLLM documentation says this dedicated image is required until public-image integration lands. The floor is vLLM 0.27.0 and FlashInfer 0.6.17+ is needed for NoPE sparse MLA. Start TP=4 with served name `glm-5.3-flash`, `--tool-call-parser glm47`, `--reasoning-parser glm45`, and `--enable-auto-tool-choice`; do not add `--trust-remote-code`, which the official command does not use. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)

For H200/Hopper, leave KV cache BF16; FP8 KV is Blackwell-only for this model. Start without MTP for the simplest paid launch. After base verification, Blackwell may add `--kv-cache-dtype fp8` and `--speculative-config '{"method":"mtp","num_speculative_tokens":5}'`. Set `VLLM_ENGINE_READY_TIMEOUT_S=3600`; HF reports `gated:false`, so no token is needed. Require `GET /v1/models` to list `glm-5.3-flash`. [recipe source](https://github.com/vllm-project/recipes/blob/main/models/zai-org/GLM-5.3-Flash.yaml), [HF API](https://huggingface.co/api/models/zai-org/GLM-5.3-Flash)

## Hardware and quantization
The default repository is native FP8: 328.3 GB of safetensors by HF metadata, described by vLLM as about 306 GiB before runtime/KV overhead. vLLM publishes a 386-GB FP8 minimum and 772-GB BF16 minimum. Every allowed single GPU tops out at 80 GB, so a one-GPU pod is not viable. Four `NVIDIA H200` GPUs (TP=4, 564 GB total) are the conservative minimum viable listed class; four B200s are also appropriate and enable FP8 KV cache. The recipe verifies H100/B200/GB200/MI355X, but does not publish an exact minimum H100-card count, making H200 safer. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)

An official BF16 sibling exists. HF links community GGUFs, but the searched Unsloth API presently exposes no GGUF file metadata; llama.cpp compatibility, multimodal projector requirements, and a working `llama-server` command are unverified. Do not put a GGUF behind Pitwall until validated. [HF card](https://huggingface.co/zai-org/GLM-5.3-Flash), [Unsloth GGUF](https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF)

[club-3090](https://github.com/noonghunna/club-3090) exists but advertises much smaller-model recipes and none for GLM-5.3-Flash; 24-GB RTX 3090/4090 cards are below the published 386-GB minimum.

## Tool calling, reasoning, and chat template
Use `glm47` for tool calls and `glm45` for reasoning with auto-tool choice. The vLLM recipe says these produce `message.tool_calls` and `message.reasoning_content`. Thinking is always enabled: the template opens `<think>` unconditionally; `reasoning_effort` accepts `low`/`high`, otherwise Max. It accepts OpenAI-style text/image/video parts and emits XML `<tool_call><arg_key>...` calls. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash), [template](https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/chat_template.jinja), [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash)

Structured-output reliability for this exact parser/model is unverified. vLLM warns that auto tools without strict constraints can produce malformed arguments. [vLLM tools](https://docs.vllm.ai/en/stable/features/tool_calling/)

## Known issues and community notes
The image requirement is material; sparse-MLA initialization errors require FlashInfer 0.6.18+. Hopper does not support FP8 KV cache for this model, so do not copy Blackwell's KV flag to H200. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)

SGLang has H100/H200/B200/B300/GB200/GB300 recipes but calls them starting points needing workload validation. Video serving needs `torchcodec` and has a 240,000-visual-token cap. [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash)

## Sources
- https://huggingface.co/zai-org/GLM-5.3-Flash — public/MIT card, multimodal claim and framework links — accessed 2026-08-27
- https://huggingface.co/api/models/zai-org/GLM-5.3-Flash?blobs=true — gating, creation date and safetensor bytes — accessed 2026-08-27
- https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/config.json — architecture and 1,048,576 context — accessed 2026-08-27
- https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/chat_template.jinja — reasoning, multimodal and tool syntax — accessed 2026-08-27
- https://recipes.vllm.ai/zai-org/GLM-5.3-Flash — image/version, flags, memory, KV caveat — accessed 2026-08-27
- https://github.com/vllm-project/recipes/blob/main/models/zai-org/GLM-5.3-Flash.yaml — readiness timeout and variant metadata — accessed 2026-08-27
- https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash — OpenAI-compatible multimodal serving/video constraints — accessed 2026-08-27
- https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF — community GGUF repository existence — accessed 2026-08-27
- https://github.com/noonghunna/club-3090 — consumer-repo scope — accessed 2026-08-27

## Open questions
- Container disk and measured cold-start time are unverified; use the official 3,600-second ready timeout and measure a real Pod.
- A per-rank H200 VRAM table for TP=4/131,072 context was not published; workload/concurrency needs a smoke test.
- No validated llama.cpp command, multimodal projector layout, or exact GGUF files/sizes was found after searching HF, llama.cpp, vLLM/SGLang, Reddit, and club-3090.
