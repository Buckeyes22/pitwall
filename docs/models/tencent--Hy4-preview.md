---
model_id: tencent/Hy4-preview
vendor: Tencent
family: Hy
release_date: '2026-08-27'
license:
  name: Apache-2.0
  url: https://huggingface.co/tencent/Hy4-preview/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 770
  params_active_b: 49
  context_length_max: 1048576
  modalities:
  - text
  thinking_mode: optional
capabilities:
  tool_calling: yes; auto tool choice with hy_v4
  structured_outputs: unverified
  vision: false
  languages: English and Chinese (card); broader coverage unverified
pitwall:
  capability_name: llm.hy4-preview
  served_model_name: hy4-preview
confidence:
  overall: high
  notes: Official vLLM recipe supplies the dedicated hy4-preview image, parser pair,
    and a 924-GB MXFP8 minimum on a single 8-GPU node.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:hy4-preview
  min_cuda: "12.8"
  repo: tencent/Hy4-preview-FP8
  file: null
  format: fp8
  min_vram_gb: 924
  context: 131072
  container_disk_gb: 950
  startup_min: unverified
  flags:
  - --speculative-config
  - '{"num_speculative_tokens":3,"method":"mtp"}'
  - --attention-backend
  - FLASHMLA_SPARSE
  - --tool-call-parser
  - hy_v4
  - --reasoning-parser
  - hy_v4
  - --enable-auto-tool-choice
  env:
    VLLM_ENABLE_HPC_OPS: '1'
  recommended_gpu_classes:
  - NVIDIA B300 SXM6 AC
  tool_call_parser: hy_v4
  reasoning_parser: hy_v4
  confidence: high
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy4-preview.yaml
---

# tencent/Hy4-preview

## Summary
Hy4-preview is the Tencent Hunyuan team's 770B-total/49B-active next-generation MoE (78 layers, 256 routed plus 1 shared expert top-8, gated DeepSeek Sparse Attention with cross-layer IndexCache reuse, identity Hyper-Connections) with one native 10B MTP layer, a 1,048,576-token context, and `reasoning_effort` control (`no_think` / `low` / `high`, default high). [HF card](https://huggingface.co/tencent/Hy4-preview)

## Deployment recipe

1. Use the dedicated `vllm/vllm-openai:hy4-preview` image (vLLM 0.29.0 floor, nightly-required, NVIDIA-only — the recipe marks AMD unsupported). [vLLM recipe](https://recipes.vllm.ai/tencent/Hy4-preview)
2. Serve the MXFP8 checkpoint `tencent/Hy4-preview-FP8` TP=8 on a single B300-class node with `VLLM_ENABLE_HPC_OPS=1` set in the environment, MTP speculative decoding (`--speculative-config '{"num_speculative_tokens":3,"method":"mtp"}'`), `--attention-backend FLASHMLA_SPARSE`, `--tool-call-parser hy_v4`, `--reasoning-parser hy_v4`, and `--enable-auto-tool-choice`; the catalogue launcher derives tensor parallelism, served name, host, and port. The B200 cell is TP=16 across two nodes — a topology a single RunPod pod cannot express. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy4-preview.yaml)
3. Expose 8000 and require `GET /v1/models` to list `hy4-preview` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/tencent/Hy4-preview-FP8?blobs=true)
4. The HF card's own quickstart passes `extra_body={"chat_template_kwargs": {"reasoning_effort": "no_think"}}` for direct responses; that is a request-side control, not a launch flag. [HF card](https://huggingface.co/tencent/Hy4-preview)

## Hardware and quantization
BF16 weights are 1,560.0 GB (131 files; recipe minimum 1,848 GB across 16x B200 or 8x B300) and the served FP8/MXFP8 sibling `tencent/Hy4-preview-FP8` is 813.8 GB with a 924-GB published minimum — eight `NVIDIA B300 SXM6 AC` GPUs (2,304 GB) are the listed single-pod class. Community GGUFs (`AngelSlim/Hy4-preview-GGUF`, others) exist without org or Unsloth publication; no llama.cpp row ships. [HF API](https://huggingface.co/api/models/tencent/Hy4-preview-FP8?blobs=true)

## Tool calling, reasoning, and chat template
`hy_v4` parsers. `reasoning_effort` defaults to `high` (deep chain-of-thought); `extra_body={"chat_template_kwargs": {"reasoning_effort": "no_think"}}` gives direct responses, with `low` between. Recommended sampling from the card: `temperature=0.9, top_p=1.0`. [HF card](https://huggingface.co/tencent/Hy4-preview)

## Known issues and community notes
"preview" means the checkpoint may be superseded by a final Hy4. AMD is unsupported in the recipe. The Hy team is part of Tencent Hunyuan (GitHub `Tencent-Hunyuan`), which also publishes Hy-MT translation models.

## Sources
- https://huggingface.co/tencent/Hy4-preview — Apache-2.0 license, gated DSA with IndexCache and iHC architecture, `reasoning_effort` `no_think`/`low`/`high` with `high` default, `extra_body` usage, `temperature=0.9, top_p=1.0` — accessed 2026-09-07
- https://huggingface.co/tencent/Hy4-preview/blob/main/LICENSE — Apache-2.0 text — accessed 2026-09-07
- https://huggingface.co/tencent/Hy4-preview/raw/main/config.json — 78+1 layers, 256+1 experts top-8, 10B MTP layer with 0.7B active, indexer 32x128 with top-k 2048, 4 residual streams, 1,048,576 context — accessed 2026-09-07
- https://huggingface.co/api/models/tencent/Hy4-preview-FP8?blobs=true — 813.8 GB FP8 across 130 shards, `gated:false`; BF16 main repo at 1,560.0 GB / 131 files — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy4-preview.yaml — hy4-preview image, 0.29.0 nightly floor, MXFP8 924 / BF16 1,848-GB minima, B300 TP8 and B200 TP16 cells, `VLLM_ENABLE_HPC_OPS` — accessed 2026-09-07
- https://github.com/Tencent-Hunyuan — vendor GitHub organization — accessed 2026-09-07
- https://huggingface.co/AngelSlim/Hy4-preview-GGUF — community GGUF existence (AngelSlim is the vendor's quantization toolkit); no validated command — accessed 2026-09-07

## Open questions
- Container disk (950 GB selected from the 813.8-GB checkpoint plus headroom) and cold-start time are unverified; measure a real Pod before setting a narrow timeout.
- "preview" naming means the checkpoint may be superseded by a final Hy4; re-verify the recipe and minima before relying on it long-term.
- Whether the BF16 repository (1,560 GB of weights) is servable on any single canonical pod is moot on the current class list; it is intentionally not shipped as a variant.
- No org or Unsloth GGUF publication exists; no llama.cpp row ships.
