---
model_id: MiniMaxAI/MiniMax-H3
vendor: MiniMaxAI
family: MiniMax H3
release_date: '2026-08-02'
license:
  name: MiniMax H3 Community License Agreement
  url: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE
  gated: false
architecture:
  kind: dense
  params_total_b: 64
  params_active_b: 64
  context_length_max: unverified
  modalities:
  - text
  - image
  - video
  - audio
  thinking_mode: none
capabilities:
  tool_calling: 'no'
  structured_outputs: 'no'
  vision: true
  languages: unverified
pitwall:
  capability_name: llm.minimax-h3
  served_model_name: MiniMax-H3
confidence:
  overall: medium
  notes: The vLLM recipe calls the full system 64B dense; the Hugging Face UI reports
    33B, so 64B is retained as the serving-recipe figure and the discrepancy is recorded
    below. Pitwall's capability/proxy must accept the video-generation endpoint rather
    than assume chat/completions.
accessed: '2026-08-27'
openai_chat: false
variants:
- id: bf16
  default: true
  engine: vllm
  image: vllm/vllm-omni:minimax-h3
  repo: MiniMaxAI/MiniMax-H3
  file: null
  format: bf16
  min_vram_gb: 202
  context: unverified
  container_disk_gb: 135
  startup_min: 30
  flags:
  - --omni
  - --trust-remote-code
  - --num-gpus
  - '2'
  - --usp
  - '1'
  - --ring
  - '1'
  - --text-encoder-tp-size
  - '2'
  - --vae-patch-parallel-size
  - '2'
  - --vae-parallel-mode
  - tile
  - --vae-use-tiling
  - --enforce-eager
  - --diffusion-attention-backend
  - CUDNN_ATTN
  env: {}
  recommended_gpu_classes:
  - NVIDIA GeForce RTX 4090
  - NVIDIA H200
  - NVIDIA H100 80GB HBM3
  tool_call_parser: none
  reasoning_parser: none
  confidence: medium
  sources: []
---

# MiniMaxAI/MiniMax-H3

## Summary

MiniMax H3 is a dense general-purpose multimodal diffusion system that jointly produces 24-FPS video with native stereo audio from text and multimodal references ([model card](https://huggingface.co/MiniMaxAI/MiniMax-H3)). It is supported by [vLLM-Omni](https://docs.vllm.ai/projects/vllm-omni/en/latest/models/supported_models/) and exposes an OpenAI-compatible video-generation surface (`/v1/videos` and `/v1/videos/sync`), rather than a chat/completions model; use it only if Pitwall proxies that endpoint.

## Deployment recipe

Use `vllm/vllm-omni:minimax-h3` with vLLM >= 0.26.0 and a current vLLM-Omni checkout on `PYTHONPATH`; the [recipe](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-H3.yaml) says the published image predates the modular H3 pipeline. For the lowest listed RunPod-compatible route, attach two 24-GB RTX 4090s, set `VLLM_WORKER_MULTIPROC_METHOD=spawn` and `VLLM_OMNI_VIDEO_SYNC_TIMEOUT=14400`, then use the YAML start arguments above. This loads FL2VA only, uses TP2 plus distributed layerwise offload, needs a 384-GiB-class host, and begins at 1024×576; `GET /health` is the documented readiness check, not `GET /v1/models`. H3's [video API](https://docs.vllm.ai/projects/vllm-omni/en/latest/serving/videos_api/) accepts `POST /v1/videos` (async jobs) and `/v1/videos/sync` (raw MP4); test the intended Pitwall reverse-proxy route before treating it as operational.

**Pinned-image reality (2026-08-28).** The catalogue variant launches the published `vllm/vllm-omni:minimax-h3` image as-is, and its parser rejects both `--task-type` and the distributed-layerwise-offload flags (see Known issues). The recipe's two-RTX-4090 route above therefore does **not** apply to this variant: the pinned image loads the complete root service (both DiTs) with no layerwise offload, so plan for the full-service memory figure in the front matter (`min_vram_gb: 202`, itself a floor until measured) rather than the recipe's 2 × 24 GB. Restore the recipe's route only when the image is rebuilt from a current vLLM-Omni checkout.

## Hardware and quantization

The [repository API](https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3?blobs=true) lists about 498.37 GB of LFS artifacts for the full original repo. The [vLLM recipe](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-H3.yaml) specifies 202 GB minimum for the complete two-DiT BF16 service; its consumer profile uses one FL2VA partition (135 GiB on disk), two RTX 4090s (48 GB aggregate) and host-memory DLO, rather than a weight quantization. Online FP8 is documented only for the DiT and leaves the text encoder and VAEs BF16; it is incompatible with DLO. There is no supported GGUF/llama.cpp path for the original H3 repository.

## Tool calling, reasoning, and chat template

No vLLM tool-call or reasoning parser applies: H3 is not a chat LLM. The repository contains processor chat-template artifacts for its text encoder, but they do not turn the model into a chat/completions service. The video API uses multipart form fields; the two-4090 route must include `aspect_ratio=16:9` in its request.

## Known issues and community notes

H3 support lives in vLLM-Omni rather than the ordinary `vllm` wheel, and the published image predates the modular pipeline, requiring a current source checkout ([recipe](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-H3.yaml)). The recipe notes that the complete root service loads both DiTs by default; constrained profiles must select `--task-type fl2va` or `ref2va`. Current limitations include one request per diffusion batch, a reduced serving reference matrix, and a documented U2×Ring2 attention-mask failure. [vLLM-Omni issue 5700](https://github.com/vllm-project/vllm-omni/issues/5700) tracks remaining H3 accuracy/performance CI and quantization work as not supported or unvalidated.

Launch-shape smoke (2026-08-28, pinned `vllm/vllm-omni@sha256:511ea7f8…`): the image ships no entrypoint, so Pitwall renders `vllm-omni serve …` whenever `--omni` is present. That image's parser rejects the recipe's `--task-type fl2va` (its `--task-type` choices are voice tasks such as `CustomVoice`/`VoiceDesign`), so the flag is omitted from `flags` until the image is rebuilt from a current vLLM-Omni checkout; re-add it then.
The same parser (checked with the engine smoke's real `vllm-omni serve` parser, not `--help`) also rejects the recipe's distributed layerwise offload flags `--enable-distributed-layerwise-offload --dlo-no-use-allgather --dlo-resident-layers 12`, so they are omitted too; `--usp`, `--ring`, `--text-encoder-tp-size`, the `--vae-*` flags, `--num-gpus`, `--enforce-eager` and `--diffusion-attention-backend` are accepted. The `min_vram_gb: 202` figure came from the recipe with offload enabled and is unverified without it — treat it as a floor until a live run measures it.

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

## Unsloth GGUF catalogue provenance

# Unsloth GGUF catalogue for Pitwall

All repository existence, gating, and byte sizes below come from the linked live Hugging Face API (`blobs=true`) accessed 2026-08-27. Sizes are decimal GB totals of all shards in that quant directory; companion projectors are separate. “Largest fit” means **largest published weight set whose total is below the named VRAM**, at 32K context only where the card supports a normal llama.cpp path; otherwise it is deliberately `unverified` (weights alone do not establish KV/cache/runtime fit).

| Upstream ID | Unsloth GGUF repo / gated | variants (GB; all published main rungs) | best 24 / 48 / 80 GB | guide / sampling / companions |
|---|---|---|---|---|
| Qwen/Qwen3.8-Flash-Next | [Qwen3.8-Flash-Next-GGUF](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF) / no | UD-IQ1_S 72.2, IQ1_M 74.4, Q2_K_XL 78.9, IQ3_XXS 81.8, Q3_K_XL 89.8, IQ4_XS 93.4, Q4_K_XL 111.1 (sharded) | unverified / unverified / UD-Q2_K_XL (weight-only; PR-required) | [card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF): think 1/.95/20; instruct .7/.8/20; BF16/F16 mmproj |
| Qwen/Qwen3.8-27B | [covered separately](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF) / no | see existing dossier | UD-Q4_K_M / UD-Q6_K_M / UD-Q8_K_XL (sourced in existing dossier) | existing dossier; mmproj/MTP |
| zai-org/GLM-5.2 | [GLM-5.2-GGUF](https://huggingface.co/unsloth/GLM-5.2-GGUF) / no | IQ1_S 216, IQ1_M 228, Q2_K_XL 253, IQ3_XXS 281, IQ3_S 308, Q3 342, IQ4 365–372, Q4 436–467, Q5 527–562, Q6 625–684, Q8 819, BF16 1507 (sharded) | none / none / none | no Unsloth run guide found; all exceed 80 GB |
| zai-org/GLM-5.3-Flash | [GLM-5.3-Flash-GGUF](https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF) / no | API returned no GGUF blobs (HTTP 200) | unverified / unverified / unverified | [card](https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF): eval 1/.95; repository needs recheck |
| MiniMaxAI/MiniMax-H3 | [MiniMax-H3-GGUF](https://huggingface.co/unsloth/MiniMax-H3-GGUF) / no | denoisers Q2 6.2, UD-Q2 7.5, Q3 8.2, UD-Q3 8.9, Q4 10.6, Q5 13.0, Q6 15.5, Q8 20.0; encoder Q2 12.2/Q4 17.0 | not applicable: video/audio system, no llama-server OpenAI API | [card](https://huggingface.co/unsloth/MiniMax-H3-GGUF): sd-cli, encoder + VAE/audio-VAE required |
| MiniMaxAI/MiniMax-Music3 | none found (HF author search `MiniMax-Music3`, HTTP 200) | — | — | audio model; no Unsloth GGUF found |
| deepseek-ai/DeepSeek-V4-Flash-0731 | [DeepSeek-V4-Flash-0731-GGUF](https://huggingface.co/unsloth/DeepSeek-V4-Flash-0731-GGUF) / no | IQ1_S 82, IQ1_M 86, Q2 90–96, IQ3 104–116, Q3 128, IQ4 136, Q4 155, Q8 161 (sharded) | none / none / none (smallest >80) | [card](https://huggingface.co/unsloth/DeepSeek-V4-Flash-0731-GGUF): agent eval 1/.95 |
| deepseek-ai/DeepSeek-V4-Pro-0813 | [DeepSeek-V4-Pro-0813-GGUF](https://huggingface.co/unsloth/DeepSeek-V4-Pro-0813-GGUF) / no | UD-Q4_K_XL 849, UD-Q8_K_XL 873 (20 shards each) | none / none / none | [card](https://huggingface.co/unsloth/DeepSeek-V4-Pro-0813-GGUF): 1/.95; no Jinja template supplied |
| moonshotai/Kimi-K3 | [Kimi-K3-GGUF](https://huggingface.co/unsloth/Kimi-K3-GGUF) / no | UD-Q1 466, TQ1 508, TQ2 551, IQ1_S 594, IQ1_M 648, IQ2 711, Q2 861, Q4 1508, Q8 1561 (sharded) | none / none / none | [card](https://huggingface.co/unsloth/Kimi-K3-GGUF): fork/Studio required; 1/.95 or agent 1/1.0; mmproj |
| meta-models/Muse-Glimmer-30B | [Muse-Glimmer-30B-GGUF](https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF) / no | IQ2 10–12, IQ3 13–14, Q2 12, Q3 13, Q4 15, Q5 19–21, Q6 26, Q8 29, BF16 55 (2 shards) | Q5_K_XL / Q8_K_XL / BF16 (weight-only) | [guide](https://unsloth.ai/docs/models/muse-glimmer); mmproj and dflash companion |
| google/gemma-4-31B-it | [gemma-4-31B-it-GGUF](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF) / no | IQ2 8–10, Q2 11, Q3 13–15, Q4 16–19, Q5 21, Q6 25–27, Q8 32–35, BF16 61 (2 shards) | Q5_K_XL / Q8_K_XL / BF16 (weight-only) | [guide](https://unsloth.ai/docs/models/gemma-4); temp 1/top-p .95/top-k 64; mmproj + MTP |
| ornith-ai/Ornith-1.5-35B-A3B-GGUF | none found (HF author search exact id, HTTP 200) | already upstream GGUF | unverified / unverified / unverified | no Unsloth publication found |

`gemma-4-31B-it-qat-GGUF` is also published: only UD-Q4_K_XL (17.4 GB) plus 0.2 GB projectors/MTP, and its card supplies `-hf …:UD-Q4_K_XL --spec-type draft-mtp --spec-draft-n-max 4 -ngl 999 -fa on`. [QAT API](https://huggingface.co/api/models/unsloth/gemma-4-31B-it-qat-GGUF?blobs=true), [QAT card](https://huggingface.co/unsloth/gemma-4-31B-it-qat-GGUF)

## Open questions

- “Weight-only” selections need a paid-image smoke test at the desired context; no uniform Unsloth VRAM formula was found.
- All `none found` results used the public HF author-search API, not an exhaustive search of every external mirror.

## Unsloth GGUF catalogue provenance

# Unsloth GGUF overview

Unsloth is the publisher behind the public [Hugging Face organisation](https://huggingface.co/unsloth), the [Unsloth GitHub organisation](https://github.com/unslothai), and documentation at [docs.unsloth.ai](https://docs.unsloth.ai/).  Its GGUF repositories are derivative quantizations, rather than a separate model licence: the MiniMax card explicitly calls them “Model Derivatives” and points to the upstream licence, while the individual cards expose the upstream licence metadata. [MiniMax card](https://huggingface.co/unsloth/MiniMax-H3-GGUF), [HF API example](https://huggingface.co/api/models/unsloth/gemma-4-31B-it-GGUF?blobs=true)

## Dynamic GGUF programme

`UD-` means an Unsloth Dynamic mixed-precision rung: its MiniMax card contrasts dynamic, mixed-precision `UD-` rungs with uniform rungs, and the Dynamic 2.0 documentation describes selecting quantization by layer/tensor rather than applying one uniform bit width. Thus names such as `UD-Q4_K_XL`, `UD-IQ2_M`, and `UD-Q8_K_XL` are repository selection labels, not llama.cpp flags. Unsloth’s Dynamic 2.0 page claims superior accuracy to competing quantizations; treat that as a vendor benchmark claim, not an independently established guarantee. [MiniMax card](https://huggingface.co/unsloth/MiniMax-H3-GGUF), [Dynamic 2.0 GGUFs](https://docs.unsloth.ai/basics/unsloth-dynamic-v2.0-gguf), [Dynamic 3.0 GGUFs](https://docs.unsloth.ai/basics/dynamic-3.0-ggufs)

Pitwall must select the exact repo revision and quant label. Cards can be re-uploaded for tokenizer/chat-template or llama.cpp corrections: the Gemma 4 card says to re-download after Google’s latest chat-template and llama.cpp fixes. Use embedded templates with `--jinja`; do not carry a template from an older conversion. [Gemma card](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF)

## Serving convention

For a supported text GGUF, llama.cpp documents `llama-server -hf owner/repo:quant`, `--host`, `--port`, `--ctx-size`, `--n-gpu-layers`, `--jinja`, and `--alias`; its server exposes OpenAI-compatible `/v1/models` and `/v1/chat/completions`. The documented CUDA image family is `ghcr.io/ggml-org/llama.cpp:server-cuda`. That is the applicable Pitwall path, with `--alias` mandatory for a stable served ID. [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [llama.cpp Docker docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md)

The official cards sometimes recommend `-hf unsloth/<repo>:<quant>` directly (Gemma QAT does so, together with `-ngl 999 -fa on`). Model-specific cards, not a universal default, supply sampling and thinking guidance: Qwen Flash documents thinking `temp=1.0, top_p=.95, top_k=20` and non-thinking `temp=.7, top_p=.8, top_k=20`; Gemma documents `temp=1.0, top_p=.95, top_k=64`. Pass request sampling parameters to the OpenAI API; do not assume llama-server has one global `--min-p`/`--top-p` configuration appropriate for every model. [Gemma QAT card](https://huggingface.co/unsloth/gemma-4-31B-it-qat-GGUF), [Qwen Flash card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF), [Gemma card](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF)

Ollama and LM Studio can consume many GGUFs, but this research found no Unsloth source establishing their OpenAI-compatible endpoint behaviour for every repository. Pitwall should therefore use llama-server, whose API is documented. Image/projector (`mmproj`) and MTP/drafter files are companions, not interchangeable main model weights; a text-only `-hf` pull must be smoke-tested before promising vision or speculative decoding. [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [Gemma QAT card](https://huggingface.co/unsloth/gemma-4-31B-it-qat-GGUF)

## Sources

- https://huggingface.co/unsloth — publisher inventory — accessed 2026-08-27
- https://github.com/unslothai — publisher GitHub — accessed 2026-08-27
- https://docs.unsloth.ai/basics/unsloth-dynamic-v2.0-gguf — Dynamic programme/vendor accuracy claims — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — API and flags — accessed 2026-08-27

## Open questions

- Dynamic 2.0/3.0 does not publish a reusable per-model VRAM-at-context formula; file size is not a VRAM guarantee.
- No source found that makes Ollama/LM Studio a safer Pitwall backend than llama-server.
