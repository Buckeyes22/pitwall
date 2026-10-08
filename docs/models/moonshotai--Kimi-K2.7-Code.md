---
model_id: moonshotai/Kimi-K2.7-Code
vendor: Moonshot AI
family: Kimi K2
release_date: '2026-06-11'
license:
  name: Modified MIT License
  url: https://huggingface.co/moonshotai/Kimi-K2.7-Code/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 1000
  params_active_b: 32
  context_length_max: 262144
  modalities:
  - text
  - image
  thinking_mode: always
capabilities:
  tool_calling: yes; auto tool choice with kimi_k2
  structured_outputs: unverified
  vision: true
  languages: unverified
pitwall:
  capability_name: llm.kimi-k2-7-code
  served_model_name: kimi-k2.7-code
confidence:
  overall: medium
  notes: Official deploy guidance and vLLM recipe supply flags and a 714-GB INT4 minimum,
    but the recipe image is the unpinned vllm/vllm-openai:latest tag.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: int4
  default: true
  engine: vllm
  image: vllm/vllm-openai:latest
  min_cuda: "12.8"
  repo: moonshotai/Kimi-K2.7-Code
  file: null
  format: int4
  min_vram_gb: 714
  context: 131072
  container_disk_gb: 700
  startup_min: unverified
  flags:
  - --mm-encoder-tp-mode
  - data
  - --trust-remote-code
  - --tool-call-parser
  - kimi_k2
  - --enable-auto-tool-choice
  - --reasoning-parser
  - kimi_k2
  env: {}
  recommended_gpu_classes:
  - NVIDIA H200
  tool_call_parser: kimi_k2
  reasoning_parser: kimi_k2
  confidence: medium
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/moonshotai/Kimi-K2.7-Code.yaml
---

# moonshotai/Kimi-K2.7-Code

## Summary
Kimi K2.7 Code is Moonshot's coding-tuned 1T-total/32B-active MoE (61 layers, 384 routed plus 1 shared expert, MLA) with a 400M MoonViT vision encoder and native INT4 compressed-tensors weights (4-bit group-32; attention, shared experts, lm_head, and vision unquantized). Thinking is always on (`preserve_thinking=True` cannot be disabled). [HF card](https://huggingface.co/moonshotai/Kimi-K2.7-Code)

## Deployment recipe

1. Serve with vLLM 0.19.1 or newer (the recipe's verified floor); SGLang 0.5.10.post1+ uses the same parser pair. The recipe publishes no version-pinned image — it references `vllm/vllm-openai:latest` — which is why the variant keeps medium confidence; pin an exact tag after a verified launch. [vLLM recipe](https://recipes.vllm.ai/moonshotai/Kimi-K2.7-Code)
2. Deploy on one 8xH200-class node; the catalogue launcher derives TP=8 from the GPU count. The official flags are `--mm-encoder-tp-mode data` (the MoonViT encoder shards by data, not tensor), `--trust-remote-code`, `--tool-call-parser kimi_k2`, `--reasoning-parser kimi_k2`, and `--enable-auto-tool-choice`. [deploy guidance](https://huggingface.co/moonshotai/Kimi-K2.7-Code/blob/main/docs/deploy_guidance.md), [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/moonshotai/Kimi-K2.7-Code.yaml)
3. Expose 8000 and require `GET /v1/models` to list `kimi-k2.7-code` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K2.7-Code?blobs=true)
4. AMD gfx942 adds `VLLM_ROCM_USE_AITER=1` plus `VLLM_ROCM_QUICK_REDUCE_QUANTIZATION=INT4`; gfx950 switches to `--moe-backend flydsl` with `--compilation-config '{"pass_config": {"fuse_allreduce_rms": false}}'` and forbids `--enable-expert-parallel`. Neither path is carried in the variant. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/moonshotai/Kimi-K2.7-Code.yaml)

## Hardware and quantization
The repository is 595.2 GB of INT4 safetensors (64 shards). The vLLM recipe publishes a 714-GB total minimum ("8x H200 or ~640 GB aggregate"), making eight `NVIDIA H200` GPUs (1,128 GB) the listed minimum viable class with KV headroom. No Moonshot FP8/NVFP4 variants exist; `unsloth/Kimi-K2.7-Code` mirrors the INT4 checkpoint and `unsloth/Kimi-K2.7-Code-GGUF` publishes IQ1_M (303.9 GB) through Q8_K_XL (594.5 GB) quants plus mmproj files, none validated for llama.cpp behind Pitwall. [vLLM recipe](https://recipes.vllm.ai/moonshotai/Kimi-K2.7-Code), [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K2.7-Code?blobs=true)

## Tool calling, reasoning, and chat template
`kimi_k2` covers both tool calls and reasoning parsing; the model interleaves thinking with multi-step tool calls, and the repository ships `tool_declaration_ts.py` for tool-schema declaration. Sampling for thinking mode is `temperature=1.0, top_p=0.95`. Context is 262,144 (YaRN factor 64 is baked into the config; no runtime YaRN needed). Video input is experimental and official-API only. [HF card](https://huggingface.co/moonshotai/Kimi-K2.7-Code)

## Known issues and community notes
AMD gfx942 needs `VLLM_ROCM_USE_AITER=1` plus `VLLM_ROCM_QUICK_REDUCE_QUANTIZATION=INT4`; gfx950 needs `--moe-backend flydsl` and forbids `--enable-expert-parallel`. `transformers>=4.57.1,<5.0.0` is the pinned range. KTransformers offers a CPU+GPU path (8xL20 plus dual Xeon) outside the catalogue engines.

## Sources
- https://huggingface.co/moonshotai/Kimi-K2.7-Code — Modified MIT license, thinking-only mode with forced `preserve_thinking`, video-input caveat, `tool_declaration_ts.py` — accessed 2026-09-07
- https://huggingface.co/moonshotai/Kimi-K2.7-Code/blob/main/LICENSE — Modified MIT text (MIT-style with commercial sale permitted) — accessed 2026-09-07
- https://huggingface.co/moonshotai/Kimi-K2.7-Code/blob/main/docs/deploy_guidance.md — vLLM 0.19.1 and SGLang 0.5.10.post1 floors, 8xH200 TP8 guidance, KTransformers CPU+GPU path — accessed 2026-09-07
- https://huggingface.co/api/models/moonshotai/Kimi-K2.7-Code?blobs=true — 595.2 GB INT4 across 64 shards, `gated:false` — accessed 2026-09-07
- https://huggingface.co/moonshotai/Kimi-K2.7-Code/raw/main/config.json — 61 layers (1 dense), 384+1 experts at 8/token, MoonViT 400M, compressed-tensors INT4 group-32, baked YaRN factor 64, 262,144 context — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/moonshotai/Kimi-K2.7-Code.yaml — 714-GB minimum ("8x H200 or ~640 GB aggregate"), token-for-token flags, AMD cells — accessed 2026-09-07
- https://huggingface.co/unsloth/Kimi-K2.7-Code-GGUF — community GGUF inventory: UD-IQ1_M 303.9 GB through UD-Q8_K_XL 594.5 GB plus 0.95–1.9-GB mmproj files — accessed 2026-09-07

## Open questions
- No pinned vLLM image tag is published; the recipe's `latest` reference means every launch re-resolves the engine. Pin a digest and re-verify parsers after engine upgrades.
- Container disk (700 GB selected from the 595.2-GB checkpoint plus headroom) and cold-start time are unverified — measure a real Pod before setting a narrow timeout.
- Structured outputs beyond tool-call JSON are undocumented; I searched the HF card, deploy guidance, and recipe without finding a guided-decoding statement.
- Whether the Unsloth GGUF rungs boot under llama-server with the mmproj projector is unverified; no GGUF variant ships until one is validated.
