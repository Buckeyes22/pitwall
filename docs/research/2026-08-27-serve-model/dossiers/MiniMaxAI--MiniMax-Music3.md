---
model_id: "MiniMaxAI/MiniMax-Music3"
vendor: "MiniMaxAI"
family: "MiniMax Music 3"
release_date: "unverified"
license:
  name: "MiniMax-Music3 Community License"
  url: "https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE"
  gated: false
architecture:
  kind: "dense"
  params_total_b: unverified
  params_active_b: unverified
  context_length_max: 10240
  modalities: ["music"]
  thinking_mode: "none"
weights:
  format: "safetensors"
  dtype: "unverified"
  size_gb: 57.35
  quantized_variants: []
serving:
  recommended_engine: "sglang"
  fits_openai_proxy: true
  image: "unverified"
  engine_min_version: "0.1.3"
  docker_start_cmd:
    - "sgl-omni"
    - "serve"
    - "--model-path"
    - "MiniMaxAI/MiniMax-Music3"
    - "--port"
    - "8000"
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "none"
  reasoning_parser: "none"
  chat_template_notes: "Uses SGLang-Omni's shared /v1/audio/speech API: lyrics are input and music description is instructions."
hardware:
  min_vram_gb_native: 24
  min_vram_gb_quantized: unverified
  recommended_gpu_classes: ["NVIDIA RTX 4090", "NVIDIA GeForce RTX 4090"]
  tensor_parallel: 1
  container_disk_gb: 60
  startup_time_estimate_min: unverified
capabilities:
  tool_calling: "no"
  structured_outputs: "no"
  vision: false
  languages: "unverified"
pitwall:
  capability_name: "llm.minimax-music3"
  served_model_name: "MiniMaxAI/MiniMax-Music3"
  example_serve_model: >-
    pitwall serve --capability llm.minimax-music3 --model MiniMaxAI/MiniMax-Music3
    --gpu-class "NVIDIA RTX 4090" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --port --start-arg 8000
confidence:
  overall: "medium"
  notes: "High confidence in the audio API and single-/dual-GPU serving path; no official container image or production startup-time estimate was found."
accessed: "2026-08-27"
---

# MiniMaxAI/MiniMax-Music3

## Summary

MiniMax Music 3 is a dense multi-stage text-to-music pipeline: a Qwen3 backbone, RVQ depth decoder, flow-matching acoustic DiT, and DAC-style decoder produce lyric-conditioned 32-kHz stereo WAV songs ([SGLang-Omni cookbook](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/minimax_music3.md?plain=1)). SGLang-Omni serves it at the standard OpenAI-compatible `/v1/audio/speech` endpoint, so it fits an audio-capable OpenAI proxy but not a chat/completions-only capability.

## Deployment recipe

Install [SGLang-Omni 0.1.3](https://github.com/sgl-project/sglang-omni) (or source main) and run `CUDA_VISIBLE_DEVICES=0 sgl-omni serve --model-path MiniMaxAI/MiniMax-Music3 --port 8000`; its documented [dual-GPU alternative](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/minimax_music3.md?plain=1) places AR on GPU 0 and DiT/DAV on GPU 1. Submit lyrics as `input` and the music description as `instructions` to `POST /v1/audio/speech`; only non-streaming generation is supported. A 60-GB disk allocation is a conservative planning floor based on the 57.35-GB [LFS artifact sum](https://huggingface.co/api/models/MiniMaxAI/MiniMax-Music3?blobs=true); official container image, readiness endpoint, and startup-time estimate are unverified. Do not add tool/reasoning-parser flags, and do not pass `--cuda-graph-max-bs` because this model computes that cap itself.

## Hardware and quantization

The card says full precision fits under 24 GB VRAM; automatic CPU offload uses about 22 GB, and layer-by-layer language-model streaming can fit 8 GB but is slower. These are offload routes, not an upstream quantized checkpoint, so `min_vram_gb_quantized` remains unverified. Community experience independently reports that the model consumes nearly all 24 GB on a single RTX 3090 and that a container needs IPC/shared-memory care. The original repo is safetensors; there is no GGUF/llama.cpp recommendation for the upstream checkpoint.

## Tool calling, reasoning, and chat template

No tool-call or reasoning parser applies. The API uses `input`, `instructions`, `response_format`, `seed`, and `max_new_tokens`; `max_new_tokens` limits audio frames at 25 frames/s, to 9,000 frames maximum. Section tags must be alone on their lines or text sharing the tag line is dropped. The SGLang-Omni cookbook demonstrates the official OpenAI Python client against this endpoint; the `voice` field is rejected for direct Music3 requests even though generic clients normally require it.

## Known issues and community notes

Inference requires CUDA; the card says prompt text is limited to 5,000 tokens and output to 9,000 acoustic frames, while the SGLang-Omni model guide identifies a 10,240-token backbone context. Treat the former as the API input limit. The card documents a diffusers PR commit rather than a released package, so pinning an alternative diffusers deployment needs care. Default concurrency is 16 requests but classifier-free guidance occupies two decode rows per request; increase `--max-running-requests` only after memory testing.

## Sources

- https://huggingface.co/MiniMaxAI/MiniMax-Music3 — architecture overview, SGLang-Omni command/API, output format, VRAM guidance, limits, and supported frameworks — accessed 2026-08-27
- https://huggingface.co/api/models/MiniMaxAI/MiniMax-Music3?blobs=true — public/not-gated API metadata and repository file list — accessed 2026-08-27
- https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE — MiniMax-Music3 Community License, commercial display requirement, and >US$20m authorization threshold — accessed 2026-08-27
- https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/minimax_music3.md?plain=1 — supported SGLang-Omni version/install, single/dual GPU commands, API contract, context, concurrency, request rejections, and OpenAI-client example — accessed 2026-08-27
- https://github.com/sgl-project/sglang-omni — 0.1.3 release note and OpenAI-compatible API surface — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vo8p8c/got_minimaxmusic3_running_as_a_live_tool_call_in/ — community 24-GB RTX 3090 deployment report and IPC/shared-memory/container caveat — accessed 2026-08-27

## Open questions

- No official SGLang-Omni Docker image/tag, readiness endpoint, or source-backed startup-time estimate was found after checking the model card, SGLang-Omni cookbook, README, and installation material.
- Pitwall's proxy compatibility for the standard audio/speech endpoint and the appropriate non-LLM capability naming are unverified; `fits_openai_proxy: true` reflects SGLang-Omni's documented standard OpenAI speech endpoint.
