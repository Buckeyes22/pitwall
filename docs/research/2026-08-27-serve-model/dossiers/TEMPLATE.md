---
model_id: "<org>/<model>"                 # exact Hugging Face id
vendor: "<org>"
family: "<model family>"
release_date: "YYYY-MM-DD or unverified"
license:
  name: "<SPDX or license name>"
  url: "<license url>"
  gated: false                            # true if the HF repo requires accepting terms / token
architecture:
  kind: "dense | moe | unverified"
  params_total_b: unverified
  params_active_b: unverified             # MoE active params; same as total for dense
  context_length_max: unverified          # tokens, from config.json / model card
  modalities: ["text"]                    # e.g. ["text","image"], ["audio"], ["music"]
  thinking_mode: "none | optional | always | unverified"
weights:
  format: "safetensors | gguf | unverified"
  dtype: "bf16 | fp8 | int4 | ... | unverified"
  size_gb: unverified                     # total download size
  quantized_variants: []                  # [{repo: "...", method: "AWQ|GPTQ|FP8|GGUF-Q4_K_M", size_gb: n, url: "..."}]
serving:
  recommended_engine: "vllm | llama.cpp | sglang | other | unverified"
  fits_openai_proxy: true                 # false for models without an OpenAI-compatible chat/completions API
  image: "vllm/vllm-openai:<tag> | ghcr.io/ggml-org/llama.cpp:server-cuda | unverified"
  engine_min_version: "unverified"
  docker_start_cmd:                       # arguments appended to the image entrypoint (`vllm serve` for vllm/vllm-openai:
                                          # the model id is POSITIONAL — `vllm serve` rejects `--model`; llama-server uses -hf/-m)
    - "<org>/<model>"
    - "--served-model-name"
    - "<served id>"
    - "--port"
    - "8000"
    # add every flag the sources require: --max-model-len, --tensor-parallel-size, --gpu-memory-utilization,
    # --dtype/--quantization, --trust-remote-code, --enable-auto-tool-choice, --tool-call-parser <name>,
    # --reasoning-parser <name>, --limit-mm-per-prompt, --enable-expert-parallel, etc.
  env:
    HF_TOKEN: "required | optional | not needed"   # gated repos need it
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "unverified"          # exact vLLM --tool-call-parser value, or "none"
  reasoning_parser: "unverified"          # exact vLLM --reasoning-parser value, or "none"
  chat_template_notes: "unverified"
hardware:
  min_vram_gb_native: unverified          # bf16/fp8 weights + KV cache at a stated context length
  min_vram_gb_quantized: unverified
  recommended_gpu_classes: []             # canonical RunPod names only, best first
  tensor_parallel: 1
  container_disk_gb: unverified           # weights + HF cache + image headroom
  startup_time_estimate_min: unverified   # download + load, for the readiness timeout
capabilities:
  tool_calling: "unverified"
  structured_outputs: "unverified"
  vision: false
  languages: "unverified"
pitwall:
  capability_name: "llm.<slug>"
  served_model_name: "<served id>"
  example_serve_model: >-
    pitwall serve --capability llm.<slug> --model <org>/<model>
    --gpu-class "NVIDIA H100 80GB HBM3" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --max-model-len --start-arg 32768
confidence:
  overall: "low | medium | high"
  notes: ""
accessed: "2026-08-27"
---

# <org>/<model>

## Summary
Two or three sentences: what the model is, what it is good for, and the one-line deployment verdict.

## Deployment recipe
Step-by-step: image, exact start command, required env, GPU choice, expected startup, readiness check
(`GET /v1/models` must list `<served id>`).

## Hardware and quantization
VRAM math at the recommended context length; which quantized variants exist and which engine serves each;
consumer-GPU notes (24 GB class) from community sources.

## Tool calling, reasoning, and chat template
Parser names, thinking-mode switches, template quirks, structured-output support.

## Known issues and community notes
Bugs, version constraints, throughput notes, gotchas — each with a citation.

## Sources
- <url> — what it supported — accessed 2026-08-27

## Open questions
- ...
