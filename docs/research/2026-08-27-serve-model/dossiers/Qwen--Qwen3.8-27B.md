---
model_id: "Qwen/Qwen3.8-27B"
vendor: "Qwen"
family: "Qwen3.8"
release_date: "2026-08-14"
license:
  name: "Apache-2.0"
  url: "https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/LICENSE"
  gated: false
architecture:
  kind: "dense"
  params_total_b: 27
  params_active_b: 27
  context_length_max: 262144
  modalities: ["text", "image", "video"]
  thinking_mode: "optional"
weights:
  format: "safetensors"
  dtype: "bf16"
  size_gb: 51.75
  quantized_variants:
    - repo: "Qwen/Qwen3.8-27B-FP8"
      method: "FP8"
      size_gb: 28.75
      url: "https://huggingface.co/Qwen/Qwen3.8-27B-FP8"
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-openai:latest"
  engine_min_version: "vLLM 0.17.0+"
  docker_start_cmd:
    - "Qwen/Qwen3.8-27B-FP8"
    - "--served-model-name"
    - "qwen3.8-27b"
    - "--port"
    - "8000"
    - "--tensor-parallel-size"
    - "1"
    - "--max-model-len"
    - "32768"
    - "--gpu-memory-utilization"
    - "0.85"
    - "--kv-cache-dtype"
    - "fp8"
    - "--reasoning-parser"
    - "qwen3"
    - "--enable-auto-tool-choice"
    - "--tool-call-parser"
    - "qwen3_coder"
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "qwen3_coder"
  reasoning_parser: "qwen3"
  chat_template_notes: "Thinking defaults on. Request chat_template_kwargs enable_thinking=false for direct answers; preserve_thinking defaults true; reasoning_effort is xhigh/medium/low. The template uses XML <tool_call> function blocks."
hardware:
  min_vram_gb_native: 80
  min_vram_gb_quantized: 80
  recommended_gpu_classes: ["NVIDIA H100 80GB HBM3", "NVIDIA H200", "NVIDIA A100 80GB", "NVIDIA RTX 6000 Ada"]
  tensor_parallel: 1
  container_disk_gb: 70
  startup_time_estimate_min: "unverified"
capabilities:
  tool_calling: "yes; recipe prescribes qwen3_coder for NVFP4; verify parser availability in the chosen vLLM image"
  structured_outputs: "json, regex (Qwen3 reasoning parser family documentation)"
  vision: true
  languages: "unverified"
pitwall:
  capability_name: "llm.qwen3-8-27b"
  served_model_name: "qwen3.8-27b"
  example_serve_model: >-
    pitwall serve --capability llm.qwen3-8-27b --model Qwen/Qwen3.8-27B-FP8
    --gpu-class "NVIDIA H100 80GB HBM3" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --max-model-len --start-arg 32768 --start-arg --reasoning-parser --start-arg qwen3
confidence:
  overall: "medium"
  notes: "Architecture and parser/reasoning behavior are well sourced. The conservative one-80GB recommendation reserves room for runtime/KV but is not an official exact H100 launch measurement; the exact qwen3_coder parser must be smoke-tested against the selected image."
accessed: "2026-08-27"
---

# Qwen/Qwen3.8-27B

## Summary

Qwen3.8-27B is a 27B dense hybrid-attention multimodal (text/image/video) model with a 262,144-token native context and built-in MTP head. Its official vLLM recipe is viable behind Pitwall’s OpenAI proxy; use official FP8 on a single 80-GB GPU with a conservative 32K serving cap, or use BF16 only where the remaining 80-GB runtime/KV budget is acceptable. [model card](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/README.md) [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)

## Deployment recipe

The source recipe requires vLLM 0.17.0+ and supports the Qwen3.5 conditional-generation architecture. For an allowed RunPod class, use `vllm/vllm-openai:latest` with the official `Qwen/Qwen3.8-27B-FP8`, TP1, `--max-model-len 32768`, `--gpu-memory-utilization 0.85`, `--kv-cache-dtype fp8`, `--reasoning-parser qwen3`, `--enable-auto-tool-choice`, and `--tool-call-parser qwen3_coder`; the 32K/0.85 choices are conservative operational settings, not an upstream validated H100-specific command. `HF_TOKEN` is not needed: the public Hub API response is HTTP 200 and `gated: false`. Confirm readiness only when `GET /v1/models` includes `qwen3.8-27b`. [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) [Hub API](https://huggingface.co/api/models/Qwen/Qwen3.8-27B?blobs=true) [vLLM supported models](https://docs.vllm.ai/en/latest/models/supported_models/)

## Hardware and quantization

The official BF16 Hub repo is 18 safetensors shards totaling 51.75 GiB; its official FP8 sibling totals 28.75 GiB. An 80-GB GPU is the conservative minimum in this dossier, leaving room for the vision tower, runtime allocation, and FP8 KV at 32K. The recipe demonstrates the 27B FP8 checkpoint at TP4/262K and consumer NVFP4 alternatives on RTX 5090, but that GPU class is not in Pitwall’s allowed list; it does not publish a direct H100/A100 single-card measurement. Do not use MXFP4 on NVIDIA: the recipe says its Nvidia linear-method support is missing. No official GGUF is listed by the vendor; third-party GGUFs require llama.cpp rather than this vLLM recipe and are not selected here. [BF16 file list](https://huggingface.co/api/models/Qwen/Qwen3.8-27B?blobs=true) [FP8 file list](https://huggingface.co/api/models/Qwen/Qwen3.8-27B-FP8?blobs=true) [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)

## Tool calling, reasoning, and chat template

The official recipe calls for `--reasoning-parser qwen3`; vLLM’s reasoning documentation identifies the `qwen3` parser, says Qwen3 reasoning is default-on, and documents `chat_template_kwargs: {"enable_thinking": false}` to turn it off. The exact model template supports `enable_thinking`, `preserve_thinking` (default true), and `reasoning_effort` values `xhigh`, `medium`, and `low`; it emits `<think>` and XML `<tool_call><function=...>` blocks. The recipe’s exact tool parser is `qwen3_coder`, while the current generic vLLM tool-parser page documents `qwen3_xml` rather than `qwen3_coder`; this is a release/image compatibility risk, so the tool parser must be verified by a smoke test before paid launches. The Qwen3 parser family documents JSON/regex structured-output support. [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) [vLLM reasoning outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling/) [template](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/chat_template.jinja)

## Known issues and community notes

The official recipe warns that MXFP4 does not load on Nvidia because vLLM lacks the needed linear-method support; choose FP8/NVFP4 instead. It reports RTX-5090-specific NVFP4 behavior, including a one-card `--enforce-eager` startup requirement, but that is not transferable evidence for the listed RunPod GPUs and has not been made a default. The recipe’s long-context section says 262K is native and shows a 1M override; this dossier does not enable it because its VRAM cost on a single allowed GPU is unsourced. Community reports supply two useful but non-authoritative signals: one vLLM user reports BF16/FP8 results on an RTX 6000 Pro using the same `qwen3_coder`/`qwen3` parser pair, and a Club-3090 benchmark on 2× RTX 3090 relied on an AutoRound INT4 model plus an unmerged/custom DFlash2 patch. Neither is a stock-vLLM RunPod recipe; do not copy the patch stack. Club-3090 exists and describes itself as local 3090/4090/5090 multi-engine recipes, but it currently ships no official Qwen3.8-27B profile. [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) [RTX 6000 Pro report](https://www.reddit.com/r/LocalLLaMA/comments/1vobpek/benchmark_qwen3827b_full_precision_fp8_rtx_6000/) [Club-3090 benchmark](https://www.reddit.com/r/LocalLLaMA/comments/1vsccit/qwen3827b_on_2x_3090_vllm_dflash2_218_toks_single/) [Club-3090](https://github.com/noonghunna/club-3090)

## Sources

- https://huggingface.co/Qwen/Qwen3.8-27B — model card: release, dense parameter count, multimodality, 262K/1M claims, thinking controls, compatible engines — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/config.json — architecture, BF16 dtype, position limit, vision/video configuration — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/generation_config.json — shipped sampling defaults — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/chat_template.jinja — tool XML and thinking switches — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/LICENSE — Apache License 2.0 — accessed 2026-08-27
- https://huggingface.co/api/models/Qwen/Qwen3.8-27B?blobs=true — HTTP 200; public/gated state and BF16 file list/sizes — accessed 2026-08-27
- https://huggingface.co/api/models/Qwen/Qwen3.8-27B-FP8?blobs=true — HTTP 200; official FP8 sibling file list/size — accessed 2026-08-27
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B — vLLM minimum version, launch commands, quantizations, context and MXFP4 limitation — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/models/supported_models/ — `Qwen3_5ForConditionalGeneration` support and multimodal capability — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/features/reasoning_outputs/ — exact `qwen3` reasoning parser, default thinking, structured outputs — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/features/tool_calling/ — current generic `qwen3_xml` tool-parser documentation, used to identify qwen3_coder documentation discrepancy — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vobpek/benchmark_qwen3827b_full_precision_fp8_rtx_6000/ — community RTX 6000 Pro benchmark/launch flags, treated as non-authoritative — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vsccit/qwen3827b_on_2x_3090_vllm_dflash2_218_toks_single/ — community 2×3090 DFlash2/custom-patch deployment; not a stock-vLLM recommendation — accessed 2026-08-27
- https://github.com/noonghunna/club-3090 — community local 3090/4090/5090 multi-engine recipes; no official Qwen3.8-27B profile found — accessed 2026-08-27

## Open questions

- Does the chosen `vllm/vllm-openai:latest` image expose `qwen3_coder` exactly as the official recipe states? The current generic docs page does not list it; smoke-test `--help`/one tool request before deployment.
- What exact FP8/BF16 GPU-memory and cold-start measurements apply to single H100 80GB, A100 80GB, and RTX 6000 Ada instances at this 32K cap?
- Whether the OpenAI proxy forwards image/video content and `chat_template_kwargs`; text chat compatibility is the deployment basis here.
- The Club-3090 results use nonstandard/custom patches and 3090 hardware, so whether a stock container has comparable behavior remains unverified.
