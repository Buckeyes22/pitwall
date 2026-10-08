---
model_id: tencent/Hy3
vendor: Tencent
family: Hy
release_date: '2026-07-02'
license:
  name: Apache-2.0
  url: https://huggingface.co/tencent/Hy3/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 295
  params_active_b: 21
  context_length_max: 262144
  modalities:
  - text
  thinking_mode: optional
capabilities:
  tool_calling: yes; auto tool choice with hy_v3
  structured_outputs: unverified
  vision: false
  languages: English and Chinese (card); broader coverage unverified
pitwall:
  capability_name: llm.hy3
  served_model_name: hy3
confidence:
  overall: high
  notes: Official vLLM recipe supplies the dedicated hy3 image, parser pair, and published
    BF16/FP8 VRAM minima; HPC-Ops optimizations are optional extras.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: bf16
  default: true
  engine: vllm
  image: vllm/vllm-openai:hy3
  min_cuda: "12.8"
  repo: tencent/Hy3
  file: null
  format: bf16
  min_vram_gb: 708
  context: 131072
  container_disk_gb: 700
  startup_min: unverified
  flags:
  - --tool-call-parser
  - hy_v3
  - --reasoning-parser
  - hy_v3
  - --enable-auto-tool-choice
  env:
    VLLM_FLASHINFER_ALLREDUCE_BACKEND: trtllm
  recommended_gpu_classes:
  - NVIDIA H200
  tool_call_parser: hy_v3
  reasoning_parser: hy_v3
  confidence: high
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy3.yaml
- id: fp8
  default: false
  engine: vllm
  image: vllm/vllm-openai:hy3
  min_cuda: "12.8"
  repo: tencent/Hy3-FP8
  file: null
  format: fp8
  min_vram_gb: 354
  context: 131072
  container_disk_gb: 380
  startup_min: unverified
  flags:
  - --tool-call-parser
  - hy_v3
  - --reasoning-parser
  - hy_v3
  - --enable-auto-tool-choice
  env:
    VLLM_FLASHINFER_ALLREDUCE_BACKEND: trtllm
  recommended_gpu_classes:
  - NVIDIA H200
  tool_call_parser: hy_v3
  reasoning_parser: hy_v3
  confidence: medium
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy3.yaml
---

# tencent/Hy3

## Summary
Hy3 is the Tencent Hunyuan team's 295B-total/21B-active MoE (80 layers, 192 experts top-8 plus 1 shared, sigmoid routing with expert bias, one MTP layer for speculative decoding) with a 262,144-token context and a three-level `reasoning_effort` control (`no_think` / `low` / `high`). The card stresses production-grade tool-call stability across CodeBuddy/Cline/KiloCode scaffoldings. [HF card](https://huggingface.co/tencent/Hy3)

## Deployment recipe

1. Use the dedicated `vllm/vllm-openai:hy3` image (AMD: a pinned `vllm/vllm-openai-rocm:nightly-cbe9c40f998f13975b967773ac7e7920e115387f`). The recipe requires vLLM 0.26.0-or-newer nightlies; the HF card itself documents only source builds, so the recipe image is the deployable path. [vLLM recipe](https://recipes.vllm.ai/tencent/Hy3)
2. Serve TP=8 on 8xH200/H20-3e (141 GB) with `VLLM_FLASHINFER_ALLREDUCE_BACKEND=trtllm` set in the environment, `--tool-call-parser hy_v3`, `--reasoning-parser hy_v3`, and `--enable-auto-tool-choice`; the catalogue launcher derives tensor parallelism, served name, host, and port. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy3.yaml)
3. Expose 8000 and require `GET /v1/models` to list `hy3` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/tencent/Hy3?blobs=true)
4. Not carried: the MTP variant (`--speculative-config.method mtp --speculative-config.num_speculative_tokens 2`), the HPC-Ops extras (`--attention-backend HPC_ATTN`; FP8 adds `--moe-backend hpc --kv-cache-dtype fp8_e4m3 --block-size 64`, which need PR vllm#47433 plus a separate wheel), and the AMD AITER env block with `--gpu-memory-utilization 0.90`. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy3.yaml)

## Hardware and quantization
BF16 weights are 597.6 GB (99 shards) and FP8 (`tencent/Hy3-FP8`) is 299.9 GB. The recipe publishes total minima of 708 GB (BF16), 354 GB (FP8), and 214 GB (NVFP4-FP8). Eight `NVIDIA H200` GPUs (1,128 GB) clear both listed variants; the card's own guidance ("H20-3e or other GPUs with larger memory capacity" for 8-GPU BF16) and the recipe both note that 8xH100/A100 80 GB does not fit BF16. Community GGUFs exist (`vcruz305/Hy3-GGUF` and others; AngelSlim is the vendor quantization toolkit) without org or Unsloth publication; no llama.cpp row ships. [HF API](https://huggingface.co/api/models/tencent/Hy3?blobs=true)

## Tool calling, reasoning, and chat template
`hy_v3` parsers (SGLang alternative: `--tool-call-parser hunyuan --reasoning-parser hunyuan`). `reasoning_effort` accepts `no_think` (direct answers), `low`, and `high` (deep chain-of-thought); the quickstart example defaults to `no_think`. Recommended sampling from the card: `temperature=0.9, top_p=1.0`. [HF card](https://huggingface.co/tencent/Hy3)

## Known issues and community notes
The recipe requires a nightly vLLM even at the 0.26.0 floor. AMD needs the AITER env block plus `--gpu-memory-utilization 0.90`. `Hy3-preview` and `Hy3-preview-Base` are earlier snapshots; the Hy-MT translation models are a separate family.

## Sources
- https://huggingface.co/tencent/Hy3 — Apache-2.0 license, `reasoning_effort` `no_think`/`low`/`high` semantics, tool-call stability notes across CodeBuddy/Cline/KiloCode, `temperature=0.9, top_p=1.0`, 8-GPU memory guidance — accessed 2026-09-07
- https://huggingface.co/tencent/Hy3/blob/main/LICENSE — Apache-2.0 text — accessed 2026-09-07
- https://huggingface.co/api/models/tencent/Hy3?blobs=true — 597.6 GB BF16 across 99 shards, `gated:false`; FP8 sibling at 299.9 GB — accessed 2026-09-07
- https://huggingface.co/tencent/Hy3/raw/main/config.json — 80 layers, 192 experts top-8 plus 1 shared, sigmoid routing with expert bias, GQA 64/8, one MTP layer, 262,144 context — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy3.yaml — hy3 image, 0.26.0 nightly floor, BF16 708 / FP8 354 / NVFP4-FP8 214-GB minima, token-for-token flags, HPC-Ops and AMD cells — accessed 2026-09-07
- https://huggingface.co/tencent/Hy3-FP8 — official FP8 sibling existence — accessed 2026-09-07
- https://github.com/Tencent-Hunyuan — vendor GitHub organization (Hy line and Hy-MT translation models) — accessed 2026-09-07

## Open questions
- Container disks (700/380 GB selected from published weight sizes plus headroom) and cold-start time are unverified; measure a real Pod before setting a narrow timeout.
- The recipe's NVFP4-FP8 variant (214-GB floor) is not shipped because the yaml does not name its repository; find the checkpoint id before adding a third variant.
- No org or Unsloth GGUF publication exists; community quants (`vcruz305/Hy3-GGUF` and others, quantized with the vendor's AngelSlim toolkit) lack validated commands, so no llama.cpp row ships.
- Structured outputs are undocumented beyond "output constraints" prose in the card.
