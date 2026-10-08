---
model_id: "deepseek-ai/DeepSeek-V4-Pro-0813"
vendor: "deepseek-ai"
family: "DeepSeek V4"
release_date: "unverified"
license:
  name: "MIT"
  url: "https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813/blob/main/LICENSE"
  gated: false
architecture:
  kind: "moe"
  params_total_b: 1600
  params_active_b: 49
  context_length_max: 1048576
  modalities: ["text"]
  thinking_mode: "optional"
weights:
  format: "safetensors"
  dtype: "fp4+fp8 mixed"
  size_gb: 893
  quantized_variants:
    - {repo: "nvidia/DeepSeek-V4-Pro-NVFP4", method: "NVFP4", size_gb: unverified, url: "https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro"}
    - {repo: "unsloth/DeepSeek-V4-Pro-0813-GGUF", method: "GGUF", size_gb: unverified, url: "https://huggingface.co/unsloth/DeepSeek-V4-Pro-0813-GGUF"}
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-openai:v0.25.0"
  engine_min_version: "0.25.0"
  docker_start_cmd:
    - "deepseek-ai/DeepSeek-V4-Pro-0813"
    - "--served-model-name"
    - "DeepSeek-V4-Pro-0813"
    - "--port"
    - "8000"
    - "--trust-remote-code"
    - "--kv-cache-dtype"
    - "fp8"
    - "--block-size"
    - "256"
    - "--data-parallel-size"
    - "8"
    - "--enable-expert-parallel"
    - "--max-model-len"
    - "800000"
    - "--tokenizer-mode"
    - "deepseek_v4"
    - "--tool-call-parser"
    - "deepseek_v4"
    - "--enable-auto-tool-choice"
    - "--reasoning-parser"
    - "deepseek_v4"
    - "--speculative-config"
    - '{"method":"dspark","num_speculative_tokens":7,"draft_sample_method":"probabilistic"}'
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "deepseek_v4"
  reasoning_parser: "deepseek_v4"
  chat_template_notes: "No Jinja template; use vLLM --tokenizer-mode deepseek_v4. reasoning_effort low/high/max is chat_template_kwargs."
hardware:
  min_vram_gb_native: unverified
  min_vram_gb_quantized: unverified
  recommended_gpu_classes: ["NVIDIA H200"]
  tensor_parallel: unverified
  container_disk_gb: unverified
  startup_time_estimate_min: unverified
capabilities:
  tool_calling: "native DSML tool calls via vLLM parser deepseek_v4"
  structured_outputs: "unverified"
  vision: false
  languages: "unverified"
pitwall:
  capability_name: "llm.deepseek-v4-pro-0813"
  served_model_name: "DeepSeek-V4-Pro-0813"
  example_serve_model: >-
    pitwall serve --capability llm.deepseek-v4-pro-0813 --model deepseek-ai/DeepSeek-V4-Pro-0813 --gpu-class "NVIDIA H200" --ttl-minutes 120 --rate-per-second 0.002 --start-arg --data-parallel-size --start-arg 8 --start-arg --enable-expert-parallel --start-arg --max-model-len --start-arg 800000 --start-arg --tokenizer-mode --start-arg deepseek_v4 --start-arg --tool-call-parser --start-arg deepseek_v4 --start-arg --reasoning-parser --start-arg deepseek_v4
confidence:
  overall: "medium"
  notes: "No single listed RunPod GPU is source-validated; source recipe requires 8×H200 DP+EP."
accessed: "2026-08-27"
---

# deepseek-ai/DeepSeek-V4-Pro-0813

## Summary

Pro-0813 is the official V4 Pro release with DSpark: a text MoE with 1.6T total/49B active parameters and 1M context. [DeepSeek model card](https://fe-static.deepseek.com/chat/transparency/deepseek-V4-model-card-EN.pdf) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813) It supports OpenAI-compatible vLLM/SGLang serving but is not a single-GPU RunPod deployment; the official recipe calls for eight H200 GPUs, DP+EP, and 800K context. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)

## Deployment recipe

Use `vllm/vllm-openai:v0.25.0` or later—the fused 0813 checkpoint requires vLLM 0.25.0. The critical command elements are eight-way DP+EP, FP8 KV cache and the 800K H200 context cap, and `--tokenizer-mode deepseek_v4` with both `deepseek_v4` parsers. The vendor base example also specifies `--trust-remote-code`, block 256, DeepGEMM MoE, FP4 indexer cache, and DSpark; hardware-specific kernel options must match the image/GPU. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)

This model's documented vLLM and SGLang routes call OpenAI-compatible `/v1/chat/completions`, so it fits Pitwall. Before traffic, `GET /v1/models` must include `DeepSeek-V4-Pro-0813`; startup duration is unverified. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)

## Hardware and quantization

The fused FP4+FP8 checkpoint is about 893 GB on disk. The recipe specifically recommends 8×H200 DP+EP and caps it to 800K because dense parameters replicate across ranks. That is the smallest source-backed RunPod configuration; none of the listed single GPU classes has a source-validated recipe, so native/quantized VRAM floors remain `unverified` instead of being guessed. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)

NVIDIA's NVFP4 re-quantization exists for Blackwell and uses the default rather than DeepGEMM MoE backend. A community GGUF upload exists, but size, quality, exact llama.cpp support, and hardware floor were not verified, so it is not recommended for Pitwall. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro) [GGUF repo](https://huggingface.co/unsloth/DeepSeek-V4-Pro-0813-GGUF)

No source-backed 24-GB consumer deployment was found. The searched `club-3090` material applies to Flash only, not Pro.

## Tool calling, reasoning, and chat template

The release has no Jinja template and instead ships encoding helpers. `reasoning_effort` is low/high/max; Think Max needs at least 393,216 context and high/max may need up to 384K output. Use `--tokenizer-mode deepseek_v4` for this OpenAI endpoint. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813) [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)

The exact tool and reasoning parser names are `deepseek_v4`, with `--enable-auto-tool-choice`; the parser handles `<think>` and DSML calls together. Structured-output support is unverified. [vLLM parser](https://docs.vllm.ai/en/latest/api/vllm/parser/deepseek_v4/) [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)

## Known issues and community notes

- Parameter metadata conflict: DeepSeek's technical card says 1.6T total/49B active, while the Hugging Face page labels the fused release 1.7T. This dossier uses the vendor's architectural figures; the accounting difference is unverified. [DeepSeek model card](https://fe-static.deepseek.com/chat/transparency/deepseek-V4-model-card-EN.pdf) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)
- The HF card's 4×GB300 example is not transferable to Pitwall because GB300 is not an allowed class; use its H200 eight-GPU vLLM recipe. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813) [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)
- Community reports say the HF repo briefly returned 404 near release. Current primary page was accessible; pin revision after qualification. [LocalLLaMA report](https://www.reddit.com/r/LocalLLaMA/comments/1vndovb/unslothdeepseekv4pro0813gguf_hugging_face/) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)
- Hosted-API reports of variable/broken reasoning are not proof that the open checkpoint behaves that way and are not part of this deployment recipe. [r/DeepSeek report](https://www.reddit.com/r/DeepSeek/comments/1vn5y49/deepseeks_v4_pro_0813_official_release_last_night/)

## Sources

- https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813 — official release, public/MIT status, OpenAI snippets, template/reasoning, flags — accessed 2026-08-27
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813/raw/main/config.json — DeepSeek-V4 config and 1,048,576 positions — accessed 2026-08-27
- https://fe-static.deepseek.com/chat/transparency/deepseek-V4-model-card-EN.pdf — MoE, 1.6T/49B, text, context, MIT — accessed 2026-08-27
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro — vLLM 0.25.0, ~893GB checkpoint, 8×H200, parsers, NVFP4 — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/api/vllm/parser/deepseek_v4/ — DeepSeek-V4 DSML/reasoning parsing — accessed 2026-08-27
- https://huggingface.co/unsloth/DeepSeek-V4-Pro-0813-GGUF — unqualified GGUF alternative — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vndovb/unslothdeepseekv4pro0813gguf_hugging_face/ — repository availability report — accessed 2026-08-27
- https://www.reddit.com/r/DeepSeek/comments/1vn5y49/deepseeks_v4_pro_0813_official_release_last_night/ — community API-only report — accessed 2026-08-27

## Open questions

- Exact eight-H200 VRAM/KV calculation, disk headroom, and startup duration were not published; searched HF files/card, vendor card, vLLM docs/recipe, SGLang snippets, community reports.
- No primary source validated a native single-B200 or a smaller-than-eight-H200 Pro configuration.
- The vendor 1.6T figure conflicts with HF's 1.7T display for 0813; no source explains the accounting difference.
- Structured outputs and a production-qualified Pro GGUF llama.cpp command remain unverified.
