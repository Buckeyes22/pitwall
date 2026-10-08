---
model_id: "google/gemma-4-31B-it"
vendor: "Google"
family: "Gemma 4"
release_date: "2026-04-02"
license:
  name: "Apache License 2.0"
  url: "https://ai.google.dev/gemma/terms"
  gated: false
architecture:
  kind: "dense"
  params_total_b: 30.7
  params_active_b: 30.7
  context_length_max: 262144
  modalities: ["text", "image", "video"]
  thinking_mode: "optional"
weights:
  format: "safetensors"
  dtype: "bf16"
  size_gb: 62.6
  quantized_variants:
    - repo: "google/gemma-4-31B-it-qat-w4a16-ct"
      method: "QAT W4A16 compressed-tensors"
      size_gb: 23.3
      url: "https://huggingface.co/google/gemma-4-31B-it-qat-w4a16-ct"
    - repo: "google/gemma-4-31B-it-qat-q4_0-gguf"
      method: "GGUF Q4_0"
      size_gb: 18.9
      url: "https://huggingface.co/google/gemma-4-31B-it-qat-q4_0-gguf/tree/main"
    - repo: "cyankiwi/gemma-4-31B-it-qat-AWQ-INT4"
      method: "QAT-AWQ INT4"
      size_gb: unverified
      url: "https://huggingface.co/cyankiwi/gemma-4-31B-it-qat-AWQ-INT4"
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-openai:v0.27.1"
  engine_min_version: "v0.24.0 (community-validated; official minimum unverified)"
  docker_start_cmd:
    - "google/gemma-4-31B-it"
    - "--served-model-name"
    - "gemma-4-31B-it"
    - "--port"
    - "8000"
    - "--tensor-parallel-size"
    - "2"
    - "--max-model-len"
    - "16384"
    - "--gpu-memory-utilization"
    - "0.90"
    - "--max-num-batched-tokens"
    - "4096"
    - "--enable-auto-tool-choice"
    - "--reasoning-parser"
    - "gemma4"
    - "--tool-call-parser"
    - "gemma4"
    - "--chat-template"
    - "examples/tool_chat_template_gemma4.jinja"
    - "--limit-mm-per-prompt"
    - "image=4"
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "gemma4"
  reasoning_parser: "gemma4"
  chat_template_notes: "Use vLLM's examples/tool_chat_template_gemma4.jinja for tools/reasoning; enable per request with chat_template_kwargs.enable_thinking=true. The upstream HF tokenizer config has no embedded chat_template, while the repo supplies chat_template.jinja."
hardware:
  min_vram_gb_native: "unverified"
  min_vram_gb_quantized: 24
  recommended_gpu_classes: ["NVIDIA H100 80GB HBM3", "NVIDIA H200", "NVIDIA A100 80GB", "NVIDIA RTX 4090", "NVIDIA GeForce RTX 4090"]
  tensor_parallel: 2
  container_disk_gb: "unverified"
  startup_time_estimate_min: "unverified"
capabilities:
  tool_calling: "supported"
  structured_outputs: "supported by vLLM guided decoding / json_schema"
  vision: true
  languages: "over 140"
pitwall:
  capability_name: "llm.gemma-4-31b-it"
  served_model_name: "gemma-4-31B-it"
  example_serve_model: >-
    pitwall serve --capability llm.gemma-4-31b-it --model google/gemma-4-31B-it
    --gpu-class "NVIDIA H100 80GB HBM3" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --max-model-len --start-arg 16384
confidence:
  overall: "medium"
  notes: "Official HF/vLLM sources verify the model, formats, parser flags, OpenAI API, and full-featured TP=2 recipe. The 24-GB floor applies only to community-validated INT4 TP=2 deployment, not native BF16. Native minimum VRAM and an official minimum vLLM version remain unverified."
accessed: "2026-08-27"
---

# google/gemma-4-31B-it

## Summary

Gemma 4 31B IT is Google's 30.7B-parameter dense multimodal model: it accepts text and images (and video as frames) and produces text, with a 256K text context. vLLM offers an OpenAI-compatible serving recipe with Gemma 4 tool and reasoning parsers. The native BF16 safetensors download is 62.6 GB; use a two-GPU 80-GB-class deployment for the conservative native recipe, while consumer 24-GB cards are viable only as a two-card INT4 deployment validated by club-3090.

## Deployment recipe

Use `vllm/vllm-openai:v0.27.1` with the YAML `docker_start_cmd` above on two `NVIDIA H100 80GB HBM3` GPUs; this combines vLLM's official full-featured TP=2 / 16,384-token recipe with a conservative GPU class. Make `GET /v1/models` return `gemma-4-31B-it` before proxying traffic. The three launch-critical feature flags are `--enable-auto-tool-choice`, `--tool-call-parser gemma4`, and `--reasoning-parser gemma4`; retain the template and `--limit-mm-per-prompt image=4` for tool/reasoning and bounded vision requests. HF's unauthenticated model API reports `gated:false`, and raw model files returned HTTP 200 without credentials, so this specific public repository needs neither a licence-acceptance flow nor `HF_TOKEN` (unlike older Gemma repositories that may be gated).

## Hardware and quantization

The upstream BF16 safetensors repository reports 62.6 GB total, split into 49.8 GB and 12.8 GB shards. The official vLLM example uses TP=2, `--max-model-len 16384`, and 0.90 GPU-memory utilization, but does not state a GPU type or native memory floor; that field is therefore `unverified`. The official Google QAT GGUF is 18.9 GB total (17.7-GB Q4_0 model plus 1.2-GB multimodal projector) and is served by `llama-server` / `llama serve`, which exposes an OpenAI-compatible endpoint. For Pitwall's standard vLLM path, club-3090 documents a QAT-AWQ INT4 TP=2 profile on two 24-GB RTX 3090s (the 4090 has the same 24-GB capacity); it reports 224K context at 0.95 utilization. Treat this as community evidence for the quantized floor, not a native-BF16 guarantee. A single 24-GB card OOMs in that community recipe; its reported single-card threshold is at least 32 GB.

## Tool calling, reasoning, and chat template

The vLLM parser values are exactly `gemma4` for both `--reasoning-parser` and `--tool-call-parser`; tool choice additionally requires `--enable-auto-tool-choice`. Reasoning is optional: request it with `chat_template_kwargs: {"enable_thinking": true}` (or set `--default-chat-template-kwargs '{"enable_thinking": true}'`), and the parser moves the thought channel into the API `reasoning` field. Use `examples/tool_chat_template_gemma4.jinja`; the official recipe says this is in the vLLM container. vLLM also documents `response_format: {type: json_schema}` guided decoding, but its own recipe warns that schema descriptions are not visible to the model, so state semantic requirements in the system prompt. 31B is vision-capable; cap requests with `--limit-mm-per-prompt image=4`. Do not declare audio support for this 31B model: the HF card limits native audio to E2B/E4B/12B.

## Known issues and community notes

vLLM issue #42687 reports that, on versions including 0.20.0, sub-70-GB devices can auto-select `max_num_batched_tokens=2048`, below Gemma 4's 2,496 tokens per multimodal item; it gives `--max-num-batched-tokens 4096` as the workaround, hence that flag in this recipe. The project also has a report of repetition loops, especially with JSON-schema constrained decoding; bound output tokens and test structured-output workloads before production. For GGUF vision, a llama.cpp CUDA issue reported a SIGABRT while loading the 31B `mmproj` on a 5090 with older builds, with `--no-mmproj` as a text-only workaround; therefore validate vision separately after any llama.cpp upgrade. The relevant operator repo does exist: club-3090 has Gemma 4 31B recipes; its current default is QAT-AWQ INT4 / TP=2, with MTP disabled on vLLM 0.24.0 because tools break when speculative decoding is enabled.

r/LocalLLaMA reports vary substantially by quantization, KV type, runtime, and whether vision is loaded: one Q4 GGUF user reports fitting 131K on a 24-GB GPU, while another reports a 31B Q4_K_M configuration leaves under 1 GB at 14K context. These are anecdotal llama.cpp results and do not supersede the vLLM/club-3090 TP=2 recommendation.

## Sources

- https://huggingface.co/google/gemma-4-31B-it/tree/4797d2888d4e7a1450a92a7d426967eeca6f3d7e — HF metadata, Apache-2.0 label, safetensors file list and 62.6 GB total — accessed 2026-08-27
- https://huggingface.co/api/models/google/gemma-4-31B-it — public model metadata (`gated: false`) and repository file listing (HTTP 200 without authentication) — accessed 2026-08-27
- https://huggingface.co/google/gemma-4-31B-it/raw/main/config.json — architecture (`Gemma4ForConditionalGeneration`), BF16 configs, dense text configuration, and 262,144 text positions (HTTP 200) — accessed 2026-08-27
- https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html — Gemma 4 OpenAI serving, thinking/tool parser names, chat template, and sample context flags — accessed 2026-08-27
- https://build.nvidia.com/google/gemma-4-31b-it/modelcard — 2026-04-02 release date, 30.7B parameters, modalities, 256K context, >140 languages, function-calling capability — accessed 2026-08-27
- https://huggingface.co/google/gemma-4-31B-it-qat-q4_0-gguf/tree/main — official Google QAT GGUF, 17.7-GB Q4_0 model + 1.2-GB mmproj (18.9 GB), and llama.cpp OpenAI-compatible `llama serve` command — accessed 2026-08-27
- https://github.com/noonghunna/club-3090/blob/master/models/gemma-4-31b/README.md — community Gemma 4 31B consumer-GPU recipes, QAT quant paths, TP=2 24-GB floor and single-card caveat — accessed 2026-08-27
- https://raw.githubusercontent.com/noonghunna/club-3090/master/models/gemma-4-31b/vllm/compose/dual/qat-awq-int4/base.yml — community-validated 2x 24-GB QAT-AWQ INT4 deployment, vLLM image/flags, 224K context and MTP/tool caveat — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/42687 — multimodal batch-token startup failure and `--max-num-batched-tokens 4096` workaround — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/40080 — reported repeated-generation failure with JSON-schema constrained decoding — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/21402 — reported Gemma 4 31B/26B CUDA mmproj load crash in older llama.cpp builds and text-only workaround — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1sgsl35/planning_a_local_gemma_4_build_is_a_single_rtx/ — conflicting community 24-GB GGUF/context experiences; treated as anecdotal — accessed 2026-08-27

## Open questions

- The exact native-BF16 VRAM floor at 16K context: the official TP=2 recipe does not state its hardware, so the conservative 2x80-GB recommendation is an operational choice, not an official minimum.
- An official vLLM image-tag/minimum-version guarantee. club-3090 validates v0.24.0/v0.27.1 quantized profiles, but this is not vendor compatibility policy.
- The supported/current llama.cpp Docker image tag and whether the current release has resolved the cited historical CUDA mmproj crash; not used by the primary vLLM recipe.
- No SGLang-specific Gemma 4 parser/tool/reasoning recipe was located in the searched official HF serving guidance and SGLang results; SGLang remains an alternative OpenAI endpoint, not this dossier's recommended engine.
