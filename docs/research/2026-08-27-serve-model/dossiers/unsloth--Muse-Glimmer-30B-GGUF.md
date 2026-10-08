---
model_id: "unsloth/Muse-Glimmer-30B-GGUF"
vendor: "Unsloth"
family: "Muse Glimmer"
release_date: "2026-08"
license: {name: "Apache-2.0", url: "https://huggingface.co/meta-models/Muse-Glimmer-30B/blob/main/LICENSE", gated: false}
architecture: {kind: "dense", params_total_b: 29.6, params_active_b: 29.6, context_length_max: 131072, modalities: ["text", "image"], thinking_mode: "optional"}
weights:
  format: "gguf"
  dtype: "multiple Dynamic 2.0 GGUF quants"
  size_gb: unverified
  quantized_variants: [{repo: "unsloth/Muse-Glimmer-30B-GGUF", method: "UD-Q5_K_XL", size_gb: 20.6, url: "https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF/tree/main"}, {repo: "unsloth/Muse-Glimmer-30B-GGUF", method: "UD-Q8_K_XL", size_gb: 31.5, url: "https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF/tree/main"}, {repo: "unsloth/Muse-Glimmer-30B-GGUF", method: "BF16", size_gb: 54.8, url: "https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF/tree/main"}]
serving:
  recommended_engine: "llama.cpp"
  fits_openai_proxy: true
  image: "ghcr.io/ggml-org/llama.cpp:server-cuda"
  engine_min_version: "unverified"
  docker_start_cmd: ["-hf", "unsloth/Muse-Glimmer-30B-GGUF:UD-Q5_K_XL", "--host", "0.0.0.0", "--port", "8000", "--ctx-size", "32768", "--n-gpu-layers", "all", "--jinja", "--alias", "muse-glimmer-30b-gguf"]
  env: {HF_TOKEN: "not needed", HF_HUB_ENABLE_HF_TRANSFER: "optional"}
  tool_call_parser: "unverified"
  reasoning_parser: "unverified"
  chat_template_notes: "Use embedded Jinja; thinking toggles are documented in Unsloth, exact server controls unverified."
hardware:
  min_vram_gb_native: unverified
  min_vram_gb_quantized: 24
  recommended_gpu_classes: ["NVIDIA RTX 4090", "NVIDIA GeForce RTX 4090", "NVIDIA RTX A6000", "NVIDIA A100 80GB"]
  tensor_parallel: 1
  container_disk_gb: unverified
  startup_time_estimate_min: unverified
capabilities: {tool_calling: "yes (card claim)", structured_outputs: "unverified", vision: true, languages: "100+ languages (card claim)"}
pitwall:
  capability_name: "llm.muse-glimmer-30b-gguf"
  served_model_name: "muse-glimmer-30b-gguf"
  example_serve_model: >-
    pitwall serve --capability llm.muse-glimmer-30b-gguf --model unsloth/Muse-Glimmer-30B-GGUF --gpu-class "NVIDIA RTX 4090" --ttl-minutes 120 --rate-per-second 0.002
confidence: {overall: "medium", notes: "Files and llama.cpp API are primary-source verified; 24 GB runtime headroom and exact template behaviour need a smoke test."}
accessed: "2026-08-27"
---
# unsloth/Muse-Glimmer-30B-GGUF
## Summary
Muse Glimmer is Meta’s 29.6B text/image agentic model, published by Unsloth in Dynamic GGUF rungs. Use llama-server for Pitwall’s OpenAI-compatible text API; vision needs a matching projector.
## Deployment recipe
Start the CUDA server with the YAML arguments. `-hf repo:UD-Q5_K_XL` chooses 20.6 GB weights, `--n-gpu-layers all` requests full offload, `--jinja` uses the conversion’s template, and `--alias` makes `/v1/models` stable. [llama server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
## Hardware and quantization
Published main files range from 10.0 GB UD-IQ2_XXS through 31.5 GB UD-Q8_K_XL; BF16 is two 54.8 GB shards. File sizes make Q5 the conservative 24 GB candidate, Q8 the weight-only 48 GB choice, and BF16 the weight-only 80 GB choice. `mmproj-*` and `dflash-kquant.gguf` are companions. [HF API](https://huggingface.co/api/models/unsloth/Muse-Glimmer-30B-GGUF?blobs=true)
## Tool calling, reasoning, and chat template
The upstream card claims reliable tool use and controllable effort; Unsloth links a Muse guide and says it has thinking toggles. Exact llama.cpp tool-call parsing and request fields were not verified, so smoke-test tool calls before exposing them. [card](https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF), [guide](https://unsloth.ai/docs/models/muse-glimmer)
## Known issues and community notes
- No sourced 24 GB context/KV measurement was found; the 24 GB entry is an intentionally conservative weight-size selection, not a guarantee.
## Sources
- https://huggingface.co/api/models/unsloth/Muse-Glimmer-30B-GGUF?blobs=true — files/status — accessed 2026-08-27
- https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF — model, licence and guide link — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — OpenAI endpoints/flags — accessed 2026-08-27
## Open questions
- Verify 24 GB VRAM at 32K and the exact vision/tool request format in a launch test.
