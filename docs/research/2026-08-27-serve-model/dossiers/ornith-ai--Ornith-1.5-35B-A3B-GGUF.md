---
model_id: "ornith-ai/Ornith-1.5-35B-A3B-GGUF"
vendor: "ornith-ai"
family: "Ornith-1.5"
release_date: "2026-08-18"
license:
  name: "MIT"
  url: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF/blob/main/LICENSE"
  gated: false
architecture:
  kind: "moe"
  params_total_b: 36
  params_active_b: 3
  context_length_max: 262144
  modalities: ["text", "image", "video"]
  thinking_mode: "optional"
weights:
  format: "gguf"
  dtype: "Q4_K_M (recommended)"
  size_gb: 186
  quantized_variants:
    - {repo: "ornith-ai/Ornith-1.5-35B-A3B-GGUF", method: "GGUF-Q4_K_M", size_gb: 21.7, url: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF/tree/main"}
    - {repo: "ornith-ai/Ornith-1.5-35B-A3B-GGUF", method: "GGUF-Q5_K_M", size_gb: 25.3, url: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF/tree/main"}
    - {repo: "ornith-ai/Ornith-1.5-35B-A3B-GGUF", method: "GGUF-Q6_K", size_gb: 29.2, url: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF/tree/main"}
    - {repo: "ornith-ai/Ornith-1.5-35B-A3B-GGUF", method: "GGUF-Q8_0", size_gb: 37.8, url: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF/tree/main"}
    - {repo: "ornith-ai/Ornith-1.5-35B-A3B-GGUF", method: "GGUF-BF16", size_gb: 71.1, url: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF/tree/main"}
serving:
  recommended_engine: "llama.cpp"
  fits_openai_proxy: true
  image: "ghcr.io/ggml-org/llama.cpp:server-cuda"
  engine_min_version: "unverified (use current llama.cpp: current docs document -hf, --jinja, OpenAI API, and multimodal support)"
  docker_start_cmd:
    - "-hf"
    - "ornith-ai/Ornith-1.5-35B-A3B-GGUF:Q4_K_M"
    - "--alias"
    - "Ornith-1.5-35B-A3B"
    - "--host"
    - "0.0.0.0"
    - "--port"
    - "8000"
    - "--n-gpu-layers"
    - "all"
    - "--ctx-size"
    - "32768"
    - "--parallel"
    - "1"
    - "--flash-attn"
    - "on"
    - "--jinja"
    - "--no-mmproj"
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "none (llama.cpp uses --jinja; vLLM upstream-safetensors parser: qwen3_xml)"
  reasoning_parser: "none (vLLM upstream-safetensors parser: qwen3)"
  chat_template_notes: "Native Jinja uses <think>, XML <tool_call>/<function>/<parameter>, and supports enable_thinking=false. Primary recipe is text-only because --no-mmproj is set."
hardware:
  min_vram_gb_native: "80 (inference from 71.1-GB BF16 file plus runtime/KV; vendor recommends 2x80 GB at 262K)"
  min_vram_gb_quantized: "24 (nominal Q4_K_M file floor only; low confidence for fully GPU-resident 32K service)"
  recommended_gpu_classes: ["NVIDIA L40S", "NVIDIA RTX 6000 Ada", "NVIDIA A40", "NVIDIA RTX A6000", "NVIDIA RTX 4090"]
  tensor_parallel: 1
  container_disk_gb: "30 (estimated operational allowance; Q4 weights are 21.7 GB)"
  startup_time_estimate_min: "unverified"
capabilities:
  tool_calling: "yes, llama.cpp --jinja/template-native OpenAI function calling; validate this model template"
  structured_outputs: "yes, llama.cpp documents JSON-object and JSON-schema response_format"
  vision: true
  languages: "unverified"
pitwall:
  capability_name: "llm.ornith-1-5-35b-a3b-gguf"
  served_model_name: "Ornith-1.5-35B-A3B"
  example_serve_model: >-
    pitwall serve --capability llm.ornith-1-5-35b-a3b-gguf --model ornith-ai/Ornith-1.5-35B-A3B-GGUF
    --gpu-class "NVIDIA L40S" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg -hf --start-arg ornith-ai/Ornith-1.5-35B-A3B-GGUF:Q4_K_M --start-arg --alias --start-arg Ornith-1.5-35B-A3B
    --start-arg --ctx-size --start-arg 32768 --start-arg --n-gpu-layers --start-arg all --start-arg --jinja --start-arg --no-mmproj
confidence:
  overall: "medium"
  notes: "GGUF route, files, and upstream vLLM recipe are sourced. Exact 32K VRAM and tested llama.cpp version are not."
accessed: "2026-08-27"
---

# ornith-ai/Ornith-1.5-35B-A3B-GGUF

## Summary

Public MIT-labelled Ornith-1.5 35B-A3B is a 36B-total, roughly 3B-active multimodal MoE with a 262,144-token native setting. This GGUF repository belongs on CUDA llama.cpp, not vLLM. Use text-only Q4_K_M on a 48-GB card as the practical one-GPU Pitwall deployment; 24 GB is only a nominal, low-headroom weight-file floor. An upstream safetensors repository, `ornith-ai/Ornith-1.5-35B-A3B`, exists for vLLM.

## Deployment recipe

Use `ghcr.io/ggml-org/llama.cpp:server-cuda` and the YAML arguments verbatim. `-hf` fetches the chosen GGUF; `--alias` makes `GET /v1/models` return `Ornith-1.5-35B-A3B`, which must be the readiness assertion after `/v1/health` returns 200. llama.cpp documents OpenAI-compatible `/v1/models` and `/v1/chat/completions`, so this fits Pitwall's OpenAI proxy. The key launch flags are `--n-gpu-layers all`, `--ctx-size 32768`, and `--jinja`; `--parallel 1` avoids multiple KV slots. The model is public, so `HF_TOKEN` is not needed. Startup time is unverified; set readiness from an observed first download.

The primary recipe disables the 903-MB projector with `--no-mmproj`, preserving VRAM for text. Remove it only after testing vision/video; llama.cpp says multimodal is experimental. Do not initially add `--spec-type draft-mtp`: a community report alleges the release's MTP head is untrained, an unverified but material performance risk.

Alternative, not the GGUF path: vendor documents vLLM >=0.19.1 for `ornith-ai/Ornith-1.5-35B-A3B`: `--tensor-parallel-size 2 --max-model-len 262144 --gpu-memory-utilization 0.90 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser qwen3_xml --reasoning-parser qwen3 --trust-remote-code`. It recommends two 80-GB GPUs for that 262K configuration: choose two `NVIDIA H100 80GB HBM3`, `NVIDIA H200`, or `NVIDIA A100 80GB` cards.

## Hardware and quantization

Listed files: Q4_K_M 21.7 GB, Q5_K_M 25.3 GB, Q6_K 29.2 GB, Q8_0 37.8 GB, BF16 71.1 GB, plus the 903-MB BF16 projector. Thus Q4 is the only listed quant that nominally fits a 24-GB GPU; Q5+ do not from file size alone. This is not a measured VRAM claim: CUDA allocations, KV cache, and projector make 24 GB fragile, so this dossier recommends 48 GB and 32K context. The vendor calls BF16 about 70 GB and recommends 2x80 GB for 256K context; one-GPU BF16 and exact 32K VRAM remain unverified.

A community 12-GB RTX 4070 Ti report used Q4 at 32K, but its command kept 28 MoE layers on CPU (`--n-cpu-moe 28`): it proves a CPU/RAM-offload route, not a fully GPU-resident 12-GB configuration. The operator-mentioned `club-3090` repository is relevant general guidance only: its FAQ says its tooling does not serve GGUF and directs operators to manual llama.cpp.

## Tool calling, reasoning, and chat template

By default the model emits `<think>...</think>`. Vendor's vLLM safetensors recipe uses `qwen3_xml` to parse tool calls and `qwen3` to parse reasoning into `reasoning_content`. These are not llama.cpp flags.

For this GGUF server use `--jinja`; llama.cpp documents OpenAI-style function calling through it and warns a compatible chat template may be needed. Ornith's supplied template injects tools and requires XML `<tool_call><function=...><parameter=...>` output, wraps tool results in `<tool_response>`, and emits an empty think block when `enable_thinking=false`. llama.cpp documents `response_format` support for both JSON object and JSON schema. Its experimental multimodal OAI endpoint can accept `image_url`, but this primary deployment intentionally disables vision.

## Known issues and community notes

- The exact-GGUF LocalLLaMA run on a 12-GB RTX 4070 Ti used CUDA llama.cpp, 32K context, CPU MoE offload, and MTP speculation; reported performance is anecdotal rather than a RunPod sizing target.
- A LocalLLaMA post alleges the MTP head is randomly initialized. Vendor confirmation was not found, so treat it as an unverified reason to avoid speculative decoding until benchmarked.
- A CUDA llama.cpp issue for Qwen3.5-35B-A3B reports silent corruption in multi-turn tool loops with prompt caching, including stock Q4_K_M at 61,440 context. Ornith's `qwen3_5_moe` hybrid architecture makes this relevant by inference, not proof it affects Ornith. Test multi-turn tool loops and disable prompt-cache reuse if reproduced.
- Current llama.cpp docs label multimodal support experimental; start Pitwall text-only.

## Sources

- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF — public model card: MIT label, GGUF, model-size display, quant sizes, and llama.cpp command — accessed 2026-08-27
- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF/tree/main — file list: all GGUF sizes and 903-MB mmproj — accessed 2026-08-27
- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B/raw/main/config.json — architecture, 262,144 context, expert count, BF16 and image/video config — accessed 2026-08-27
- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B/blob/main/README.md — vendor vLLM/SGLang recipes, runtime minima, 2x80-GB 256K guidance, parsers and reasoning behavior — accessed 2026-08-27
- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B/raw/main/chat_template.jinja — tool, think, and multimodal Jinja behavior — accessed 2026-08-27
- https://huggingface.co/api/models/ornith-ai/Ornith-1.5-35B-A3B-GGUF — public metadata and 2026-08-18 creation timestamp — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md — CUDA server image — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — HF loading, alias/models/health/API, `--jinja` function calls, JSON schema, experimental multimodal — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vt6hwc/ornith1535ba3b_q4_running_60tks_on_4070ti/ — 12-GB GPU CPU-offload deployment report — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vtu555/if_you_are_wondering_why_ornith_15_35b_a3b_with/ — unverified MTP allegation — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/21681 — Qwen3.5 hybrid CUDA prompt-cache corruption report — accessed 2026-08-27
- https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md — club-3090 says its tooling does not serve GGUF — accessed 2026-08-27

## Open questions

- The linked Hugging Face `LICENSE` URL returned HTTP 404 on 2026-08-27 although metadata says MIT; obtain actual license text/vendor confirmation for legal review.
- Exact llama.cpp build support for this Ornith GGUF, its Jinja tool template, and `reasoning_content` extraction is unverified; model card, current server docs, and Qwen3.5 issues were searched.
- Exact fully-GPU VRAM at 32K/64K/262K and a guaranteed 24-GB Q4 configuration remain unverified; sources covered file sizes, vendor 2x80 guidance, and a 12-GB CPU-offload run.
- BF16 mmproj interoperability with Q4, stability of vision/video through Pitwall, image tag/digest, and first-download startup time are unverified.
