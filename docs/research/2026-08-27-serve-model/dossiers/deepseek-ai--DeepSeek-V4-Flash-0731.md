---
model_id: "deepseek-ai/DeepSeek-V4-Flash-0731"
vendor: "deepseek-ai"
family: "DeepSeek V4"
release_date: "unverified"
license:
  name: "MIT"
  url: "https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/blob/main/LICENSE"
  gated: false
architecture:
  kind: "moe"
  params_total_b: 285
  params_active_b: 13
  context_length_max: 1048576
  modalities: ["text"]
  thinking_mode: "optional"
weights:
  format: "safetensors"
  dtype: "fp4+fp8 mixed"
  size_gb: 167
  quantized_variants:
    - {repo: "nvidia/DeepSeek-V4-Flash-NVFP4", method: "NVFP4", size_gb: unverified, url: "https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300"}
    - {repo: "ggml-org/DeepSeek-V4-Flash-0731-GGUF", method: "GGUF-MXFP4", size_gb: 155, url: "https://huggingface.co/ggml-org/DeepSeek-V4-Flash-0731-GGUF"}
    - {repo: "ggml-org/DeepSeek-V4-Flash-0731-GGUF", method: "GGUF-Q2_K_S", size_gb: 98.6, url: "https://huggingface.co/ggml-org/DeepSeek-V4-Flash-0731-GGUF"}
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-openai:v0.25.0"
  engine_min_version: "0.25.0"
  docker_start_cmd:
    - "deepseek-ai/DeepSeek-V4-Flash-0731"
    - "--served-model-name"
    - "DeepSeek-V4-Flash-0731"
    - "--port"
    - "8000"
    - "--trust-remote-code"
    - "--kv-cache-dtype"
    - "fp8"
    - "--block-size"
    - "256"
    - "--data-parallel-size"
    - "4"
    - "--enable-expert-parallel"
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
  capability_name: "llm.deepseek-v4-flash-0731"
  served_model_name: "DeepSeek-V4-Flash-0731"
  example_serve_model: >-
    pitwall serve --capability llm.deepseek-v4-flash-0731 --model deepseek-ai/DeepSeek-V4-Flash-0731 --gpu-class "NVIDIA H200" --ttl-minutes 120 --rate-per-second 0.002 --start-arg --data-parallel-size --start-arg 4 --start-arg --enable-expert-parallel --start-arg --tokenizer-mode --start-arg deepseek_v4 --start-arg --tool-call-parser --start-arg deepseek_v4 --start-arg --reasoning-parser --start-arg deepseek_v4
confidence:
  overall: "medium"
  notes: "Official recipe supports four-GPU DP+EP, not a validated single RunPod GPU."
accessed: "2026-08-27"
---

# deepseek-ai/DeepSeek-V4-Flash-0731

## Summary

Flash-0731 is DeepSeek's official post-trained V4 Flash release with a DSpark draft module: a text MoE, 285B total/13B active, with 1M context and optional low/high/max reasoning. [DeepSeek model card](https://fe-static.deepseek.com/chat/transparency/deepseek-V4-model-card-EN.pdf) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731) Deploy it with vLLM 0.25.0+ and the built-in DeepSeek-V4 tokenizer/tool/reasoning integration; no one-card listed-RunPod recipe is source-validated. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300)

## Deployment recipe

Use `vllm/vllm-openai:v0.25.0`; its essential arguments are the YAML command above: `--trust-remote-code`, FP8 KV cache/block 256, four-way DP plus expert parallelism, and the DeepSeek-V4 tokenizer/tool/reasoning parser settings. The official checkpoint requires vLLM 0.25.0 and its DSpark option is `{"method":"dspark","num_speculative_tokens":7,"draft_sample_method":"probabilistic"}`. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731) [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300)

It exposes OpenAI-compatible chat/completions, so fits Pitwall. Require `GET /v1/models` to contain `DeepSeek-V4-Flash-0731`; download/load timing is unverified. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731)

## Hardware and quantization

The native fused checkpoint is mixed FP4 expert + FP8 remaining weights, about 167 GB on disk; a vLLM validation loaded 148.66 GiB at only 4K context. The recipe says non-disaggregated deployment uses four-GPU DP+EP and uses four of an H200/B200/B300 eight-GPU host. Therefore H200 ×4 is the smallest source-backed RunPod deployment; a single B200 is plausible but unverified and must not be inferred as supported. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300)

NVFP4 exists from NVIDIA. A GGUF repo lists MXFP4 (155 GB) and Q2_K_S (98.6 GB); serve GGUF with llama.cpp (`llama-server`/`llama serve`), whose card shows an OpenAI-compatible endpoint, but qualify this separate engine/checkpoint before Pitwall use. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300) [GGUF card](https://huggingface.co/ggml-org/DeepSeek-V4-Flash-0731-GGUF)

Native vLLM is not a 24-GB-card path. `club-3090` reports an experimental non-mainline llama.cpp MoE-cache fork using a Q8 GGUF on two RTX 3090s and offering OpenAI API port 8030; it is not evidence for the production recipe. [club-3090](https://github.com/noonghunna/club-3090/discussions/951)

## Tool calling, reasoning, and chat template

There is no Jinja chat template; the release provides encoding helpers. Supply low/high/max `reasoning_effort` through `chat_template_kwargs`; Think Max needs ≥393,216 context and DeepSeek recommends up to 384K output for high/max. vLLM's `--tokenizer-mode deepseek_v4` supplies the built-in encoding. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731) [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300)

Use `deepseek_v4` for both `--tool-call-parser` and `--reasoning-parser`, with `--enable-auto-tool-choice`. vLLM documents it as one state machine for `<think>` output and DSML tool calls. Structured outputs are unverified; a vLLM issue reports an EngineCore crash for `response_format`. [vLLM parser](https://docs.vllm.ai/en/latest/api/vllm/parser/deepseek_v4/) [issue #51510](https://github.com/vllm-project/vllm/issues/51510)

## Known issues and community notes

- Parameter metadata conflict: DeepSeek's technical card says 285B total/13B active, while the Hugging Face page labels this fused release as 304B. This dossier uses the vendor's architectural 285B/13B figures; the difference may include DSpark/metadata accounting and is unverified. [DeepSeek model card](https://fe-static.deepseek.com/chat/transparency/deepseek-V4-model-card-EN.pdf) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731)
- Stock vLLM SM8x (A100/A800/RTX 30xx) support was open/unavailable when issue #50576 was filed. [issue #50576](https://github.com/vllm-project/vllm/issues/50576)
- A vLLM 0.26.0 H100 TP8+EP report says HTTP succeeded but text was corrupted; it is community evidence, so pin and smoke-test image/model pairs. [issue #51326](https://github.com/vllm-project/vllm/issues/51326)
- RTX PRO 6000 DSpark needs nightly vLLM/FlashInfer according to the recipe, not the stable generic image. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300)

## Sources

- https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731 — release, public/MIT status, no Jinja template, reasoning, base vLLM/SGLang instructions — accessed 2026-08-27
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/raw/main/config.json — architecture, FP8 config, 1,048,576 positions — accessed 2026-08-27
- https://fe-static.deepseek.com/chat/transparency/deepseek-V4-model-card-EN.pdf — MoE, 285B/13B, text, 1M, MIT — accessed 2026-08-27
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling,reasoning&hardware=b300 — vLLM version, flags, hardware, DSpark, parser names — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/api/vllm/parser/deepseek_v4/ — DSML and reasoning parser behavior — accessed 2026-08-27
- https://huggingface.co/ggml-org/DeepSeek-V4-Flash-0731-GGUF — GGUF sizes and llama.cpp OpenAI server — accessed 2026-08-27
- https://github.com/noonghunna/club-3090/discussions/951 — experimental 2×3090 community deployment — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/50576 — SM8x support limitation — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/51326 — H100 corruption report — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/51510 — structured-output crash report — accessed 2026-08-27

## Open questions

- No primary source validated official 0731 on one NVIDIA B200; searched HF, vendor card, vLLM recipe/docs, SGLang, community issues.
- Exact four-H200 KV/cache VRAM math, startup duration, and source-validated disk headroom are unverified.
- The 285B vendor figure conflicts with HF's 304B display for 0731; no source explains the accounting difference.
- Structured outputs need a pinned-image regression test.
