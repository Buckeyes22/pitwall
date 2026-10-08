---
model_id: "MiniMaxAI/MiniMax-H3"
vendor: "MiniMaxAI"
family: "MiniMax H3"
release_date: "2026-08-02"
license:
  name: "MiniMax H3 Community License Agreement"
  url: "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE"
  gated: false
architecture:
  kind: "dense"
  params_total_b: 64
  params_active_b: 64
  context_length_max: unverified
  modalities: ["text", "image", "video", "audio"]
  thinking_mode: "none"
weights:
  format: "safetensors"
  dtype: "bf16"
  size_gb: 498.37
  quantized_variants:
    - repo: "MiniMaxAI/MiniMax-H3"
      method: "online FP8 (DiT only)"
      size_gb: unverified
      url: "https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-H3.yaml"
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-omni:minimax-h3"
  engine_min_version: "0.26.0"
  docker_start_cmd:
    - "MiniMaxAI/MiniMax-H3"
    - "--omni"
    - "--task-type"
    - "fl2va"
    - "--host"
    - "0.0.0.0"
    - "--port"
    - "8000"
    - "--trust-remote-code"
    - "--num-gpus"
    - "2"
    - "--tensor-parallel-size"
    - "2"
    - "--usp"
    - "1"
    - "--ring"
    - "1"
    - "--text-encoder-tp-size"
    - "2"
    - "--vae-patch-parallel-size"
    - "2"
    - "--vae-parallel-mode"
    - "tile"
    - "--vae-use-tiling"
    - "--enable-distributed-layerwise-offload"
    - "--dlo-no-use-allgather"
    - "--dlo-resident-layers"
    - "12"
    - "--enforce-eager"
    - "--diffusion-attention-backend"
    - "CUDNN_ATTN"
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "none"
  reasoning_parser: "none"
  chat_template_notes: "This is an omni video/audio diffusion model, not a chat model."
hardware:
  min_vram_gb_native: 202
  min_vram_gb_quantized: unverified
  recommended_gpu_classes: ["NVIDIA RTX 4090", "NVIDIA GeForce RTX 4090", "NVIDIA H200", "NVIDIA H100 80GB HBM3"]
  tensor_parallel: 2
  container_disk_gb: 135
  startup_time_estimate_min: unverified
capabilities:
  tool_calling: "no"
  structured_outputs: "no"
  vision: true
  languages: "unverified"
pitwall:
  capability_name: "llm.minimax-h3"
  served_model_name: "MiniMax-H3"
  example_serve_model: >-
    pitwall serve --capability llm.minimax-h3 --model MiniMaxAI/MiniMax-H3
    --gpu-class "NVIDIA RTX 4090" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --omni --start-arg --task-type --start-arg fl2va
confidence:
  overall: "medium"
  notes: "The vLLM recipe calls the full system 64B dense; the Hugging Face UI reports 33B, so 64B is retained as the serving-recipe figure and the discrepancy is recorded below. Pitwall's capability/proxy must accept the video-generation endpoint rather than assume chat/completions."
accessed: "2026-08-27"
---

# MiniMaxAI/MiniMax-H3

## Summary

MiniMax H3 is a dense general-purpose multimodal diffusion system that jointly produces 24-FPS video with native stereo audio from text and multimodal references ([model card](https://huggingface.co/MiniMaxAI/MiniMax-H3)). It is supported by [vLLM-Omni](https://docs.vllm.ai/projects/vllm-omni/en/latest/models/supported_models/) and exposes an OpenAI-compatible video-generation surface (`/v1/videos` and `/v1/videos/sync`), rather than a chat/completions model; use it only if Pitwall proxies that endpoint.

## Deployment recipe

Use `vllm/vllm-omni:minimax-h3` with vLLM >= 0.26.0 and a current vLLM-Omni checkout on `PYTHONPATH`; the [recipe](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-H3.yaml) says the published image predates the modular H3 pipeline. For the lowest listed RunPod-compatible route, attach two 24-GB RTX 4090s, set `VLLM_WORKER_MULTIPROC_METHOD=spawn` and `VLLM_OMNI_VIDEO_SYNC_TIMEOUT=14400`, then use the YAML start arguments above. This loads FL2VA only, uses TP2 plus distributed layerwise offload, needs a 384-GiB-class host, and begins at 1024×576; `GET /health` is the documented readiness check, not `GET /v1/models`. H3's [video API](https://docs.vllm.ai/projects/vllm-omni/en/latest/serving/videos_api/) accepts `POST /v1/videos` (async jobs) and `/v1/videos/sync` (raw MP4); test the intended Pitwall reverse-proxy route before treating it as operational.

## Hardware and quantization

The [repository API](https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3?blobs=true) lists about 498.37 GB of LFS artifacts for the full original repo. The [vLLM recipe](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-H3.yaml) specifies 202 GB minimum for the complete two-DiT BF16 service; its consumer profile uses one FL2VA partition (135 GiB on disk), two RTX 4090s (48 GB aggregate) and host-memory DLO, rather than a weight quantization. Online FP8 is documented only for the DiT and leaves the text encoder and VAEs BF16; it is incompatible with DLO. There is no supported GGUF/llama.cpp path for the original H3 repository.

## Tool calling, reasoning, and chat template

No vLLM tool-call or reasoning parser applies: H3 is not a chat LLM. The repository contains processor chat-template artifacts for its text encoder, but they do not turn the model into a chat/completions service. The video API uses multipart form fields; the two-4090 route must include `aspect_ratio=16:9` in its request.

## Known issues and community notes

H3 support lives in vLLM-Omni rather than the ordinary `vllm` wheel, and the published image predates the modular pipeline, requiring a current source checkout ([recipe](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-H3.yaml)). The recipe notes that the complete root service loads both DiTs by default; constrained profiles must select `--task-type fl2va` or `ref2va`. Current limitations include one request per diffusion batch, a reduced serving reference matrix, and a documented U2×Ring2 attention-mask failure. [vLLM-Omni issue 5700](https://github.com/vllm-project/vllm-omni/issues/5700) tracks remaining H3 accuracy/performance CI and quantization work as not supported or unvalidated.

## Sources

- https://huggingface.co/MiniMaxAI/MiniMax-H3 — model modalities, BF16 safetensors, original checkpoint layout, SGLang/vLLM recommendations — accessed 2026-08-27
- https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-H3.yaml — vLLM-Omni engine/image/version, 64B dense architecture, `/v1/videos` API, current-source requirement — accessed 2026-08-27
- https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3?blobs=true — public/not-gated API metadata, license metadata, and file list — accessed 2026-08-27
- https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE — release date and territorial license restrictions (including USA/EU/UK/South Korea exclusion) — accessed 2026-08-27
- https://docs.vllm.ai/projects/vllm-omni/en/latest/serving/videos_api/ — OpenAI-compatible `/v1/videos` API and endpoint contract — accessed 2026-08-27
- https://docs.vllm.ai/projects/vllm-omni/en/latest/models/supported_models/ — MiniMaxH3Pipeline support status — accessed 2026-08-27
- https://github.com/vllm-project/vllm-omni/issues/5700 — currently unvalidated H3 work and support limitations — accessed 2026-08-27
- https://github.com/vllm-project/vllm-omni/pull/5850/files — 2×RTX 4090 DLO community/upstream-contributor recipe, 135-GiB partition and host-RAM guidance — accessed 2026-08-27

## Open questions

- `context_length_max` is not meaningful as an autoregressive context field for this diffusion system; the recipe calls it zero while describing task-dependent Qwen3-VL presentation lengths.
- The Hugging Face UI labels the model 33B while the serving recipe identifies the total/active parameter count as 64B; MiniMax has not supplied a reconciliation in the sources reviewed.
- Whether Pitwall can proxy `/v1/videos` and use a non-LLM capability name is outside the model sources and remains unverified.
