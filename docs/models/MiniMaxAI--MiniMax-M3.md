---
model_id: MiniMaxAI/MiniMax-M3
vendor: MiniMax
family: MiniMax M
release_date: '2026-06-02'
license:
  name: MiniMax Community License
  url: https://huggingface.co/MiniMaxAI/MiniMax-M3/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 427
  params_active_b: 23
  context_length_max: 1048576
  modalities:
  - text
  - image
  - video
  thinking_mode: optional
capabilities:
  tool_calling: yes; auto tool choice with minimax_m3
  structured_outputs: unverified
  vision: true
  languages: English and Chinese (card); broader coverage unverified
pitwall:
  capability_name: llm.minimax-m3
  served_model_name: minimax-m3
confidence:
  overall: high
  notes: Official recipe supplies the dedicated image, mandatory block-size flag, parser
    pair, and published BF16/MXFP8 VRAM minima.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: bf16
  default: true
  engine: vllm
  image: vllm/vllm-openai:minimax-m3
  min_cuda: "12.8"
  repo: MiniMaxAI/MiniMax-M3
  file: null
  format: bf16
  min_vram_gb: 1025
  context: 131072
  container_disk_gb: 1000
  startup_min: unverified
  flags:
  - --block-size
  - '128'
  - --tool-call-parser
  - minimax_m3
  - --reasoning-parser
  - minimax_m3
  - --enable-auto-tool-choice
  env: {}
  recommended_gpu_classes:
  - NVIDIA H200
  - NVIDIA B200
  tool_call_parser: minimax_m3
  reasoning_parser: minimax_m3
  confidence: high
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M3.yaml
- id: mxfp8
  default: false
  engine: vllm
  image: vllm/vllm-openai:minimax-m3
  min_cuda: "12.8"
  repo: MiniMaxAI/MiniMax-M3-MXFP8
  file: null
  format: fp8
  min_vram_gb: 513
  context: 131072
  container_disk_gb: 560
  startup_min: unverified
  flags:
  - --block-size
  - '128'
  - --tool-call-parser
  - minimax_m3
  - --reasoning-parser
  - minimax_m3
  - --enable-auto-tool-choice
  env: {}
  recommended_gpu_classes: []
  tool_call_parser: minimax_m3
  reasoning_parser: minimax_m3
  confidence: medium
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M3.yaml
---

# MiniMaxAI/MiniMax-M3

## Summary
MiniMax-M3 is MiniMax's natively multimodal (text/image/video) MoE — 427B total BF16 parameters, ~23B active, 60 layers with the first three dense, 128 routed plus 1 shared expert at 4 experts per token — using MiniMax Sparse Attention to reach a 1,048,576-token context without rope scaling. Commercial use requires the Community License terms: "Built with MiniMax M3" attribution, and written authorization past $20M yearly revenue. [HF card](https://huggingface.co/MiniMaxAI/MiniMax-M3)

## Deployment recipe

1. Use the dedicated `vllm/vllm-openai:minimax-m3` image (AMD: `vllm/vllm-openai-rocm:minimax-m3`); support "has not yet shipped in a stable vLLM release" and the recipe disables pip installs — Docker is the only supported path. [vLLM recipe](https://recipes.vllm.ai/MiniMaxAI/MiniMax-M3)
2. Serve TP=8 with `--block-size 128` — mandatory on every platform per the recipe — plus `--tool-call-parser minimax_m3`, `--reasoning-parser minimax_m3`, and `--enable-auto-tool-choice`; the catalogue launcher derives tensor parallelism, served name, host, and port. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M3.yaml)
3. Expose 8000 and require `GET /v1/models` to list `minimax-m3` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/MiniMaxAI/MiniMax-M3?blobs=true)
4. Optional and not carried: `--enable-expert-parallel`, DP8 (`--data-parallel-size 8 --enable-expert-parallel`), `--kv-cache-dtype fp8` with an explicit `--max-model-len` for long context. AMD TP8 additionally needs `--attention-backend TRITON_ATTN --mm-encoder-tp-mode data --mm-encoder-attn-backend ROCM_AITER_FA` and `VLLM_USE_BREAKABLE_CUDAGRAPH=0`; MI3xx quant variants override to nightly ROCm images (MXFP4 pins `nightly-8a728663c1c3eeace834a95f5654fa653cc1998c`). [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M3.yaml)

## Hardware and quantization
BF16 weights are 854.2 GB (59 shards); the recipe publishes total minima of 1025 GB (BF16), 513 GB (MXFP8), and 257 GB (MXFP4/NVFP4). Eight `NVIDIA H200` GPUs (1,128 GB) or eight `NVIDIA B200` GPUs (1,440 GB) clear the BF16 floor. `MiniMaxAI/MiniMax-M3-MXFP8` (443.7 GB, F8_E4M3+U8) targets AMD MI3xx-class hardware (TP4 on gfx950); no canonical AMD class is listed for it. `unsloth/MiniMax-M3-GGUF` publishes MXFP4_MOE (256.5 GB) through Q8_0 (452.7 GB) quants; none are validated for Pitwall llama.cpp serving. [HF API](https://huggingface.co/api/models/MiniMaxAI/MiniMax-M3?blobs=true)

## Tool calling, reasoning, and chat template
`minimax_m3` parsers. Thinking is a tri-state `thinking` parameter: `enabled` / `adaptive` / `disabled`. Sampling defaults from the card: `temperature=1.0, top_p=0.95`. The M3 chat syntax matches the M2 tool-call format, for which MiniMax ships a dedicated tool-calling guide. [HF card](https://huggingface.co/MiniMaxAI/MiniMax-M3)

## Known issues and community notes
AMD TP8 needs `--attention-backend TRITON_ATTN --mm-encoder-tp-mode data --mm-encoder-attn-backend ROCM_AITER_FA` plus `VLLM_USE_BREAKABLE_CUDAGRAPH=0`; MI3xx quants override to nightly ROCm images. Long-context deployments should pair `--kv-cache-dtype fp8` with an explicit `--max-model-len`.

## Sources
- https://huggingface.co/MiniMaxAI/MiniMax-M3 — Community License terms (attribution; $20M authorization threshold), native multimodality, tri-state `thinking`, MSA 9x/15x claims, `temperature=1.0, top_p=0.95` — accessed 2026-09-07
- https://huggingface.co/MiniMaxAI/MiniMax-M3/blob/main/LICENSE — MiniMax Community License text — accessed 2026-09-07
- https://huggingface.co/api/models/MiniMaxAI/MiniMax-M3?blobs=true — 427.04B BF16 parameters, 854.2 GB across 59 shards, `gated:false` — accessed 2026-09-07
- https://huggingface.co/MiniMaxAI/MiniMax-M3/raw/main/config.json — 60 layers (first 3 dense), 128+1 experts at 4/token, sigmoid routing with bias, MTP nextn layer, CLIP-style vision tower, 1,048,576 context with rope_theta 5e6 (no YaRN) — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M3.yaml — minimax-m3 image, 0.24.0 floor, Docker-only statement, mandatory block size, BF16 1025 / MXFP8 513 / MXFP4-NVFP4 257-GB minima, AMD cells — accessed 2026-09-07
- https://huggingface.co/MiniMaxAI/MiniMax-M3-MXFP8 — official MXFP8 sibling (443.7 GB, F8_E4M3+U8, gfx950-targeted) — accessed 2026-09-07
- https://huggingface.co/unsloth/MiniMax-M3-GGUF — community GGUF inventory: MXFP4_MOE 256.5 GB through Q8_0 452.7 GB — accessed 2026-09-07

## Open questions
- Container disks (1,000/560 GB selected from published weight sizes plus headroom) and cold-start time are unverified; measure a real Pod before setting a narrow timeout.
- Structured outputs beyond tool-call JSON are undocumented (searched: card, tool-calling guide lineage, recipe, yaml).
- The MXFP8 variant ships with no recommended canonical GPU class: it targets AMD gfx950 (MI3xx), which the validated class list does not include; the 513-GB floor stands on paper only.
- No validated GGUF/llama.cpp recipe was found; no GGUF row ships.
