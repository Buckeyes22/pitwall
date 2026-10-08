---
model_id: "unsloth/Qwen3.8-Flash-Next-GGUF"
vendor: "Unsloth"
family: "Qwen3.8 Flash Next"
release_date: "unverified"
license:
  name: "Qwen Community License 1.0"
  url: "https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/blob/main/LICENSE"
  gated: false
architecture:
  kind: "unverified"
  params_total_b: unverified
  params_active_b: unverified
  context_length_max: 262144
  modalities: ["text", "image", "video"]
  thinking_mode: "optional"
weights:
  format: "gguf"
  dtype: "Dynamic 3.0 GGUF"
  size_gb: 533.6
  quantized_variants: [{repo: "unsloth/Qwen3.8-Flash-Next-GGUF", method: "UD-IQ1_S", size_gb: 72.2, url: "https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main"}, {repo: "unsloth/Qwen3.8-Flash-Next-GGUF", method: "UD-Q2_K_XL", size_gb: 78.9, url: "https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main"}, {repo: "unsloth/Qwen3.8-Flash-Next-GGUF", method: "UD-Q4_K_XL", size_gb: 111.1, url: "https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main"}]
serving:
  recommended_engine: "llama.cpp"
  fits_openai_proxy: true
  image: "ghcr.io/ggml-org/llama.cpp:server-cuda"
  engine_min_version: "unverified; card requires llama.cpp PR #27742"
  docker_start_cmd: ["-hf", "unsloth/Qwen3.8-Flash-Next-GGUF:UD-Q2_K_XL", "--host", "0.0.0.0", "--port", "8000", "--ctx-size", "32768", "--n-gpu-layers", "all", "--jinja", "--alias", "qwen38-flash-next-gguf"]
  env: {HF_TOKEN: "not needed", HF_HUB_ENABLE_HF_TRANSFER: "optional"}
  tool_call_parser: "none (embedded Jinja/llama.cpp request parsing)"
  reasoning_parser: "unverified"
  chat_template_notes: "enable_thinking=false and preserve_thinking=false are documented request template kwargs."
hardware:
  min_vram_gb_native: unverified
  min_vram_gb_quantized: 80
  recommended_gpu_classes: ["NVIDIA H100 80GB HBM3", "NVIDIA A100 80GB"]
  tensor_parallel: 1
  container_disk_gb: unverified
  startup_time_estimate_min: unverified
capabilities:
  tool_calling: "unverified"
  structured_outputs: "unverified"
  vision: true
  languages: "unverified"
pitwall:
  capability_name: "llm.qwen38-flash-next-gguf"
  served_model_name: "qwen38-flash-next-gguf"
  example_serve_model: >-
    pitwall serve --capability llm.qwen38-flash-next-gguf --model unsloth/Qwen3.8-Flash-Next-GGUF --gpu-class "NVIDIA H100 80GB HBM3" --ttl-minutes 120 --rate-per-second 0.002
confidence:
  overall: "low"
  notes: "The quant is below 80 GB but the card requires an unmerged llama.cpp PR, so this is not a paid-launch recommendation."
accessed: "2026-08-27"
---
# unsloth/Qwen3.8-Flash-Next-GGUF
## Summary
This is Unsloth’s Dynamic 3.0 GGUF conversion of the Qwen vision/video model. The card requires llama.cpp PR #27742 (or Unsloth Desktop), so a stock CUDA-server image is not verified despite llama-server’s OpenAI API.
## Deployment recipe
Use only after a current image contains the cited PR. The YAML command selects 78.9 GB UD-Q2_K_XL, full GPU offload, embedded Jinja, 32K context, and an alias. Poll `/v1/health`, then require `/v1/models` to list the alias. [card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF), [server API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
## Hardware and quantization
UD-IQ1_S is 72.2 GB and UD-Q2_K_XL is 78.9 GB across shards, so neither establishes a 24/48 GB launch; UD-Q2 is merely under 80 GB in download size. `mmproj-BF16.gguf` and `mmproj-F16.gguf` are vision companions. [HF API](https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF?blobs=true)
## Tool calling, reasoning, and chat template
Thinking requests use temperature 1.0, top-p .95, top-k 20; instruct/no-thinking uses .7/.8/20. The card documents `chat_template_kwargs.enable_thinking=false` and `preserve_thinking=false`; it does not establish exact llama.cpp tool/reasoning parser flags. [card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF)
## Known issues and community notes
- PR-required support is the principal launch blocker; pinning `server-cuda` without proving the PR is unsafe. [card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF)
## Sources
- https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF?blobs=true — public status and exact file bytes — accessed 2026-08-27
- https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF — support, context, sampling and template guidance — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — server API/flags — accessed 2026-08-27
## Open questions
- Whether the current `server-cuda` tag includes PR #27742; recheck before launch.
- KV-cache headroom at 32K/80 GB and vision-projector invocation are unverified.
