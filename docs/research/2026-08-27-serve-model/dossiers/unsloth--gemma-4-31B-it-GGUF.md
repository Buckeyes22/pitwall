---
model_id: "unsloth/gemma-4-31B-it-GGUF"
vendor: "Unsloth"
family: "Gemma 4"
release_date: "unverified"
license: {name: "Apache-2.0", url: "https://ai.google.dev/gemma/docs/gemma_4_license", gated: false}
architecture: {kind: "dense", params_total_b: 31, params_active_b: 31, context_length_max: 256000, modalities: ["text", "image"], thinking_mode: "unverified"}
weights:
  format: "gguf"
  dtype: "multiple GGUF quants; BF16"
  size_gb: unverified
  quantized_variants: [{repo: "unsloth/gemma-4-31B-it-GGUF", method: "Q4_K_M", size_gb: 18.4, url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/tree/main"}, {repo: "unsloth/gemma-4-31B-it-GGUF", method: "UD-Q5_K_XL", size_gb: 21.4, url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/tree/main"}, {repo: "unsloth/gemma-4-31B-it-GGUF", method: "UD-Q8_K_XL", size_gb: 34.8, url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/tree/main"}, {repo: "unsloth/gemma-4-31B-it-GGUF", method: "BF16", size_gb: 61.0, url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/tree/main"}]
serving:
  recommended_engine: "llama.cpp"
  fits_openai_proxy: true
  image: "ghcr.io/ggml-org/llama.cpp:server-cuda"
  engine_min_version: "unverified"
  docker_start_cmd: ["-hf", "unsloth/gemma-4-31B-it-GGUF:UD-Q5_K_XL", "--host", "0.0.0.0", "--port", "8000", "--ctx-size", "32768", "--n-gpu-layers", "all", "--flash-attn", "on", "--jinja", "--alias", "gemma-4-31b-it-gguf"]
  env: {HF_TOKEN: "not needed", HF_HUB_ENABLE_HF_TRANSFER: "optional"}
  tool_call_parser: "unverified"
  reasoning_parser: "none"
  chat_template_notes: "Re-download/use current embedded template: card reports Google chat-template and llama.cpp fixes."
hardware:
  min_vram_gb_native: 80
  min_vram_gb_quantized: 24
  recommended_gpu_classes: ["NVIDIA RTX 4090", "NVIDIA GeForce RTX 4090", "NVIDIA RTX A6000", "NVIDIA A100 80GB", "NVIDIA H100 80GB HBM3"]
  tensor_parallel: 1
  container_disk_gb: unverified
  startup_time_estimate_min: unverified
capabilities: {tool_calling: "unverified", structured_outputs: "llama.cpp response_format supports JSON/JSON Schema", vision: true, languages: "140+ (upstream card)"}
pitwall:
  capability_name: "llm.gemma-4-31b-it-gguf"
  served_model_name: "gemma-4-31b-it-gguf"
  example_serve_model: >-
    pitwall serve --capability llm.gemma-4-31b-it-gguf --model unsloth/gemma-4-31B-it-GGUF --gpu-class "NVIDIA RTX 4090" --ttl-minutes 120 --rate-per-second 0.002
confidence: {overall: "medium", notes: "Quant files, updated-template warning, generation settings and llama-server API are primary source; context VRAM is not."}
accessed: "2026-08-27"
---
# unsloth/gemma-4-31B-it-GGUF
## Summary
Unsloth’s public GGUF conversion of Gemma 4 31B-it has both conventional and Dynamic rungs plus vision projectors. This is a practical llama.cpp text-serving candidate, but 24 GB only supports a low-context, weight-headroom-sensitive quant.
## Deployment recipe
Use the CUDA llama-server image and YAML arguments. `-hf` selects the exact UD-Q5_K_XL file, `--flash-attn on` and full GPU offload follow llama.cpp convention, and `--alias` permits Pitwall’s `/v1/models` check. llama-server documents `/v1/chat/completions`, `/v1/models`, JSON and JSON-Schema response formats. [server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
## Hardware and quantization
Q4_K_M is 18.4 GB, UD-Q5_K_XL 21.4 GB, UD-Q8_K_XL 34.8 GB and BF16 totals 61.0 GB over two shards. Accordingly Q5 is the highest 24 GB weight-size candidate, Q8 is the largest <48 GB, and BF16 is the largest <80 GB; all three require KV/runtime validation at 32K. `mmproj-{BF16,F16,F32}.gguf` are vision files, and MTP is separate. [HF API](https://huggingface.co/api/models/unsloth/gemma-4-31B-it-GGUF?blobs=true)
## Tool calling, reasoning, and chat template
Use the current embedded template: the card explicitly says an April update requires re-download for Google chat-template and llama.cpp fixes. Upstream recommended sampling is temperature 1.0, top-p .95, top-k 64. Exact native tool parser/reasoning switches are unverified. [card](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF)
## Known issues and community notes
- Do not copy an old template: the card identifies a chat-template/llama.cpp correction. [card](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF)
- A separate QAT repo has a 17.4 GB UD-Q4_K_XL target and documented automatic MTP discovery with recent llama.cpp; it is a distinct model selection. [QAT card](https://huggingface.co/unsloth/gemma-4-31B-it-qat-GGUF)
## Sources
- https://huggingface.co/api/models/unsloth/gemma-4-31B-it-GGUF?blobs=true — files, bytes, public status — accessed 2026-08-27
- https://huggingface.co/unsloth/gemma-4-31B-it-GGUF — licence, template update and sampling — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — API/flags/schema responses — accessed 2026-08-27
## Open questions
- Verify 24 GB maximum context, vision projector command, and tool calls on the selected current image.
