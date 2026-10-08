---
model_id: "unsloth/Qwen3.8-27B-GGUF"
vendor: "Unsloth"
family: "Qwen3.8"
release_date: "2026-08-13"
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
  format: "gguf"
  dtype: "multiple GGUF quants; BF16"
  size_gb: 472.1
  quantized_variants:
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "Q4_0", size_gb: 16.1, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "Q4_1", size_gb: 17.5, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "Q8_0", size_gb: 29.0, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-IQ1_M", size_gb: 6.7, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-IQ1_S", size_gb: 6.2, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-IQ2_S", size_gb: 8.4, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-IQ2_XXS", size_gb: 7.3, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-IQ3_S", size_gb: 12.0, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-IQ3_XXS", size_gb: 10.9, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-IQ4_XS", size_gb: 14.3, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q2_K_XL", size_gb: 9.8, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q3_K_XL", size_gb: 13.1, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q4_K_M", size_gb: 16.5, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q4_K_S", size_gb: 15.4, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q4_K_XL", size_gb: 17.6, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q5_K_M", size_gb: 19.8, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q5_K_S", size_gb: 18.7, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q5_K_XL", size_gb: 20.9, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q6_K", size_gb: 22.0, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q6_K_L", size_gb: 24.2, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q6_K_M", size_gb: 23.1, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q6_K_XL", size_gb: 25.3, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q8_K_L", size_gb: 28.0, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
    - {repo: "unsloth/Qwen3.8-27B-GGUF", method: "UD-Q8_K_XL", size_gb: 31.5, url: "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main"}
serving:
  recommended_engine: "llama.cpp"
  fits_openai_proxy: true
  image: "ghcr.io/ggml-org/llama.cpp:server-cuda13"
  engine_min_version: "unverified"
  docker_start_cmd:
    - "--hf-repo"
    - "unsloth/Qwen3.8-27B-GGUF"
    - "--hf-file"
    - "Qwen3.8-27B-Q4_K_M.gguf"
    - "--alias"
    - "qwen3.8-27b-gguf"
    - "--host"
    - "0.0.0.0"
    - "--port"
    - "8000"
    - "--n-gpu-layers"
    - "999"
    - "--ctx-size"
    - "131072"
    - "--parallel"
    - "1"
    - "--cache-type-k"
    - "q8_0"
    - "--cache-type-v"
    - "q8_0"
    - "--flash-attn"
    - "on"
    - "--jinja"
    - "--reasoning"
    - "auto"
    - "--reasoning-format"
    - "deepseek"
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "unverified"
  tool_call_parser: "none (llama.cpp uses embedded Jinja and request parse_tool_calls)"
  reasoning_parser: "llama.cpp --reasoning-format deepseek; model template enable_thinking"
  chat_template_notes: "Template emits <think> by default; chat_template_kwargs enable_thinking=false disables it. Tool serialization is XML tool_call/function/parameter."
hardware:
  min_vram_gb_native: 80
  min_vram_gb_quantized: 24
  recommended_gpu_classes: ["NVIDIA RTX 4090", "NVIDIA GeForce RTX 4090", "NVIDIA RTX A6000", "NVIDIA A6000", "NVIDIA L40S", "NVIDIA A100 80GB", "NVIDIA H100 80GB HBM3"]
  tensor_parallel: 1
  container_disk_gb: unverified
  startup_time_estimate_min: "unverified"
capabilities:
  tool_calling: "yes; embedded template plus llama.cpp parse_tool_calls"
  structured_outputs: "yes; llama.cpp response_format supports JSON and JSON Schema"
  vision: true
  languages: "unverified"
pitwall:
  capability_name: "llm.qwen38-27b-gguf"
  served_model_name: "qwen3.8-27b-gguf"
  example_serve_model: >-
    pitwall serve --capability llm.qwen38-27b-gguf --model unsloth/Qwen3.8-27B-GGUF
    --gpu-class "NVIDIA RTX 4090" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --hf-repo --start-arg unsloth/Qwen3.8-27B-GGUF --start-arg --hf-file
    --start-arg Qwen3.8-27B-Q4_K_M.gguf --start-arg --n-gpu-layers --start-arg 999
    --start-arg --ctx-size --start-arg 131072
confidence:
  overall: "medium"
  notes: "Primary sources cover metadata, sizes, template, and server API. The exact 24 GB configuration is community evidence; no first-supported llama.cpp version was found."
accessed: "2026-08-27"
---

# unsloth/Qwen3.8-27B-GGUF

## Summary
Unsloth distributes this 27B dense Qwen3.8 vision-language model as public GGUFs. Its native context is 262,144 tokens, thinking is optional, and its template describes tool calls; deploy it with CUDA `llama-server`, whose `/v1/models`, `/v1/completions`, and `/v1/chat/completions` are OpenAI-compatible. [HF card](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF), [llama.cpp API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## Deployment recipe
Use `NVIDIA RTX 4090` with `ghcr.io/ggml-org/llama.cpp:server-cuda13`; a Qwen3.8 4090 community recipe uses this exact image. Use the YAML command verbatim: the three critical choices are `--hf-repo`/`--hf-file` to select standard 16.1 GB Q4_K_M, `--n-gpu-layers 999` for full offload, and `--ctx-size 131072`. `--alias` makes `/v1/models` report Pitwall's served name. [4090 recipe](https://www.reddit.com/r/unsloth/comments/1vobqk6/qwen3827b_serving_configs_dgx_spark_vllm_nvfp4/), [official Docker docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md), [server arguments/API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

The Hugging Face API returned HTTP 200 and `gated: false`; no HF token is needed. Container-disk and startup-time floors are unverified because no RunPod download/cache timing source was found. Poll `/v1/health` to 200, then require `/v1/models` to list `qwen3.8-27b-gguf`. [HF API](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true), [health and alias](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## Hardware and quantization
All GGUFs in the repo total 472.1 GB, but deployment fetches one main file. Optional artifacts are `MTP/mtp-Qwen3.8-27B-Q4_0.gguf` (1.37 GB), `mmproj-BF16.gguf` (0.93 GB), and `mmproj-F16.gguf` (0.928 GB). BF16 is two shards totaling 54.7 GB. [HF API file list](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true)

| GGUF file / method | Size GB | 24 GB GPU disposition |
|---|---:|---|
| BF16 (2 shards) | 54.7 | No; select 80 GB class. |
| Q4_0 / Q4_1 / Q8_0 | 16.1 / 17.5 / 29.0 | First two weight-fit; Q8_0 does not. Context unverified. |
| UD-IQ1_M / IQ1_S | 6.7 / 6.2 | Weight-fit; context/quality deployment unverified. |
| UD-IQ2_S / IQ2_XXS | 8.4 / 7.3 | Weight-fit; context deployment unverified. |
| UD-IQ3_S / IQ3_XXS | 12.0 / 10.9 | Weight-fit; context deployment unverified. |
| UD-IQ4_XS | 14.3 | Community report: 90k ctx on 5070 Ti; directional only. |
| UD-Q2_K_XL / Q3_K_XL | 9.8 / 13.1 | Weight-fit; context deployment unverified. |
| UD-Q4_K_M / Q4_K_S | 16.5 / 15.4 | Q4_K_M at Q8 K/V, 131,072 ctx is sourced on RTX 4090. |
| UD-Q4_K_XL | 17.6 | 3090 Ti report: 130k ctx at 23,817/24,564 MiB. |
| UD-Q5_K_M / Q5_K_S / Q5_K_XL | 19.8 / 18.7 / 20.9 | Bare weights fit; no sourced runtime/KV headroom. |
| UD-Q6_K / Q6_K_M / Q6_K_L / Q6_K_XL | 22.0 / 23.1 / 24.2 / 25.3 | Do not launch on 24 GB; last two weight files exceed it. |
| UD-Q8_K_L / Q8_K_XL | 28.0 / 31.5 | No full-GPU fit. |

Every name/size is from the live HF listing. The Q4_K_M result is for full offload, one slot, Flash Attention, Q8 K/V cache, and 131,072 context: it is a sourced viable floor, not a universal VRAM formula. Native BF16's 80 GB recommendation is conservative: a community observation puts BF16 at “70-something GB” resident at 256k context. [HF API](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true), [4090 config](https://www.reddit.com/r/unsloth/comments/1vobqk6/qwen3827b_serving_configs_dgx_spark_vllm_nvfp4/), [24 GB report](https://www.reddit.com/r/LocalLLaMA/comments/1vqea0n/qwen_38_27b_in_24gb_of_vram/), [BF16 observation](https://www.reddit.com/r/LocalLLaMA/comments/1vo9mj4/its_out/)

## Tool calling, reasoning, and chat template
Do not pass vLLM `--tool-call-parser` or `--reasoning-parser`: this is llama.cpp. The upstream template defaults `enable_thinking` on, accepts `reasoning_effort` `low`, `medium`, or `xhigh`, supports `preserve_thinking`, and emits `<think>`. Tools are serialized as nested XML `<tool_call><function=...><parameter=...>`. [upstream tokenizer config](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/tokenizer_config.json)

For comparison only, current vLLM documents the Qwen3 parser as handling both this XML tool syntax and `<think>` reasoning, and documents `Qwen3_5ForConditionalGeneration` for Hugging Face weights. That does not make this GGUF repository a vLLM deployment target: use llama.cpp as above. [vLLM Qwen3 parser](https://docs.vllm.ai/en/latest/api/vllm/parser/qwen3/), [vLLM Qwen3.5 model support](https://docs.vllm.ai/en/stable/api/vllm/model_executor/models/qwen3_5/)

Use `--jinja`. llama.cpp chat accepts `chat_template_kwargs` (including `enable_thinking: false`), `reasoning_effort`, `reasoning_format`, and `parse_tool_calls`; the Qwen3.8 community recipe uses `--reasoning auto --reasoning-format deepseek`. `response_format` supports JSON and JSON Schema. Vision requires adding an mmproj file; the given recipe is deliberately text-only. [llama.cpp chat API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [community recipe](https://www.reddit.com/r/unsloth/comments/1vobqk6/qwen3827b_serving_configs_dgx_spark_vllm_nvfp4/)

## Known issues and community notes
- Upstream issue [#27615](https://github.com/ggml-org/llama.cpp/issues/27615) reports Qwen3.8 tool-trigger performance degradation on build 10488 using UD-Q4_K_M and an external template; make multi-tool tests a launch gate.
- Upstream issue [#27588](https://github.com/ggml-org/llama.cpp/issues/27588) reports trailing assistant `tool_calls` can be dropped in rendered prompts, reproduced with Qwen3.8 GGUF and embedded/custom templates.
- The operator's “club 3090” project exists: [noonghunna/club-3090](https://github.com/noonghunna/club-3090) provides multi-engine consumer CUDA recipes, but its indexed catalog does not validate Qwen3.8 GGUF specifically.

## Sources
- https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true — HTTP 200, public/non-gated status, date, Apache metadata, complete file bytes — accessed 2026-08-27
- https://huggingface.co/unsloth/Qwen3.8-27B-GGUF — GGUF card and llama.cpp instructions — accessed 2026-08-27
- https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/raw/main/config.json — architecture, vision, BF16 source dtype, 262,144 context — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/tokenizer_config.json — exact template/tool/thinking behavior — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/generation_config.json — default sampling — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md — CUDA server image and GPU-layer command — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — selectors, health/models/OpenAI APIs, alias, structured output, template controls — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/api/vllm/parser/qwen3/ — current vLLM Qwen3 XML-tool/reasoning parser, considered but not used for GGUF — accessed 2026-08-27
- https://docs.vllm.ai/en/stable/api/vllm/model_executor/models/qwen3_5/ — vLLM Qwen3.5 Hugging Face-weight support, considered but not used for GGUF — accessed 2026-08-27
- https://www.reddit.com/r/unsloth/comments/1vobqk6/qwen3827b_serving_configs_dgx_spark_vllm_nvfp4/ — 4090 llama.cpp launch and context recipe — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vqea0n/qwen_38_27b_in_24gb_of_vram/ — 24 GB deployments — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/27615 — tool-trigger performance issue — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/27588 — trailing tool-call rendering issue — accessed 2026-08-27
- https://github.com/noonghunna/club-3090 — relevant community operations project — accessed 2026-08-27

## Open questions
- No primary source identified the first llama.cpp release/image digest supporting Qwen3.8 GGUF, embedded template, MTP, and vision together. I searched the model cards, llama.cpp docs/releases/issues, and Qwen repo; pin a current digest and smoke-test before paid deployment.
- No reproducible KV-cache VRAM formula or measured maximum 24 GB context exists for every quant. Only Q4_K_M at 131,072 and UD-Q4_K_XL around 130k are directly sourced; other contexts are unverified.
- Exact vision projector command/request validation and all language coverage were not verified from Qwen3.8-specific llama.cpp primary material.
- The complete LICENSE text was not separately fetched; Hugging Face metadata reports Apache-2.0.
