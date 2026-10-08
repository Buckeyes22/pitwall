---
model_id: meituan-longcat/LongCat-2.0
vendor: Meituan
family: LongCat
release_date: '2026-07-05'
license:
  name: MIT
  url: https://huggingface.co/meituan-longcat/LongCat-2.0/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 1775
  params_active_b: 48
  context_length_max: 262144
  modalities:
  - text
  thinking_mode: optional
capabilities:
  tool_calling: 'yes; chat-template tools with dict-valued arguments (not OpenAI string
    form)'
  structured_outputs: unverified
  vision: false
  languages: English and Chinese (card); broader coverage unverified
pitwall:
  capability_name: llm.longcat-2-0
  served_model_name: longcat-2.0
confidence:
  overall: low
  notes: SGLang main/nightly is the only documented engine; the cookbook image tags
    roll and no VRAM minima, vLLM support, or org GGUF exists.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: sglang
  image: lmsysorg/sglang:dev-cu13
  min_cuda: "13.0"
  repo: meituan-longcat/LongCat-2.0-FP8
  file: null
  format: fp8
  min_vram_gb: unverified
  context: 131072
  container_disk_gb: 2400
  startup_min: unverified
  flags:
  - --trust-remote-code
  - --ep
  - '8'
  - --max-running-requests
  - '64'
  - --mem-fraction-static
  - '0.92'
  - --chunked-prefill-size
  - '2048'
  - --nsa-prefill-backend
  - fa3
  - --kv-cache-dtype
  - bfloat16
  - --model-loader-extra-config
  - '{"enable_multithread_load":true,"num_threads":12}'
  env: {}
  recommended_gpu_classes:
  - NVIDIA B300 SXM6 AC
  tool_call_parser: null
  reasoning_parser: null
  confidence: low
  sources:
  - https://docs.sglang.io/cookbook/autoregressive/Meituan/LongCat-2.0
---

# meituan-longcat/LongCat-2.0

## Summary
LongCat-2.0 is Meituan's 1.6T-class sparse-attention MoE (38 layers, 768 routed plus 128 identity "zero experts", 12 experts per token, ~48B activated including a 135B N-gram embedding table) with a 262,144-token context and optional thinking. The deployable open-weight path is SGLang on multi-terabyte nodes; it is the largest dossier in the catalogue. [HF card](https://huggingface.co/meituan-longcat/LongCat-2.0)

The card states 1.6T total parameters while the HF shard metadata counts 1,775.3B (the delta is unexplained — likely MTP plus the N-gram table); the front matter records the shard count.

## Deployment recipe

1. Only SGLang documents serving, and only on SGLang `main`: the cookbook states "use a nightly wheel or rolling nightly Docker image until the next tagged release". The B300 image is `lmsysorg/sglang:dev-cu13`; B200/H200/H20 use `lmsysorg/sglang:dev`. Pin a digest before any paid launch — the tag rolls. [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Meituan/LongCat-2.0)
2. The verified single-node cell serves `meituan-longcat/LongCat-2.0-FP8` with TP8+EP8, `--trust-remote-code`, `--max-running-requests 64`, `--mem-fraction-static 0.92`, `--chunked-prefill-size 2048`, `--nsa-prefill-backend fa3`, `--kv-cache-dtype bfloat16`, and multithreaded loading (`--model-loader-extra-config '{"enable_multithread_load":true,"num_threads":12}'`); the launcher supplies `--model-path`, `--tp`, `--host`, and `--port`. [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Meituan/LongCat-2.0)
3. Expose the port and require the model id in the served list before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/meituan-longcat/LongCat-2.0?blobs=true)
4. The B200/H200/H20 cookbook cells are two-node TP16+EP16 topologies; a single RunPod pod cannot express them, and the AI-ASIC superpod cells (SGLang-FluentLLM on NPU) are outside the catalogue entirely. [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Meituan/LongCat-2.0)

## Hardware and quantization
Weights are 3,551.7 GB BF16, 2,051.2 GB FP8, or 2,060.2 GB INT8. No VRAM minimum is published; the FP8 variant needs 2,051 GB of weights alone, so eight `NVIDIA B300 SXM6 AC` GPUs (2,304 GB) at `--mem-fraction-static 0.92` is the only single-pod class with documented headroom, and the variant floor stays unverified until measured. The card also describes AI-ASIC superpod deployments outside the catalogue. Community low-bit quants exist (`pipenetwork/LongCat-2.0-2bit/3bit/4bit`) without org or Unsloth GGUF support; no llama.cpp row ships. [HF API](https://huggingface.co/api/models/meituan-longcat/LongCat-2.0?blobs=true)

## Tool calling, reasoning, and chat template
The chat template accepts `tools` and `tool_calls` with `enable_thinking=True/False` (plus `save_reasoning_content`); the card warns that tool-call `arguments` must be a dict, not the OpenAI string form. No SGLang parser values are published for the tool/reasoning parsers, so the variant leaves them null. Sampling defaults are not stated in the card. [HF card](https://huggingface.co/meituan-longcat/LongCat-2.0)

## Known issues and community notes
The rolling `lmsysorg/sglang:dev-cu13` tag is a reproducibility hazard; pin a digest before paid launches. vLLM support is absent from every consulted source.

## Sources
- https://huggingface.co/meituan-longcat/LongCat-2.0 — MIT license, 1.6T/~48B claim with 135B N-gram embedding, `enable_thinking`/`save_reasoning_content` template controls, dict-valued tool arguments, 1M-context training claim — accessed 2026-09-07
- https://huggingface.co/meituan-longcat/LongCat-2.0/raw/main/LICENSE — MIT text — accessed 2026-09-07
- https://huggingface.co/api/models/meituan-longcat/LongCat-2.0?blobs=true — 1,775.3B parameters across 194 BF16 files (3,551.7 GB), `gated:false`; FP8/INT8 siblings at 2,051.2/2,060.2 GB — accessed 2026-09-07
- https://huggingface.co/meituan-longcat/LongCat-2.0/raw/main/config.json — `LongcatCausalLM`, 38 layers, 768 routed + 128 identity experts at 12/token, MLA, LongCat Sparse Attention (index_topk 2048), 3 MTP layers, 262,144 context with DeepSeek-YaRN factor 120 — accessed 2026-09-07
- https://docs.sglang.io/cookbook/autoregressive/Meituan/LongCat-2.0 — nightly-only support statement, rolling image tags, B300 single-node TP8+EP8 cell, two-node TP16 cells, docker wrapper — accessed 2026-09-07
- https://huggingface.co/pipenetwork/LongCat-2.0-2bit (and -3bit/-4bit siblings) — community low-bit quant existence without org or Unsloth support — accessed 2026-09-07

## Open questions
- No published VRAM minimum anywhere: I searched the HF card, config, SGLang cookbook, and author searches. The 2,400-GB container disk is selected from the 2,051.2-GB FP8 checkpoint plus loading headroom (the multithread loader materializes shards before warm-up) and needs a measured Pod.
- The card's 1.6T parameter claim and the 1,775.3B shard count disagree with no published reconciliation (MTP layers plus the N-gram table are the likely delta); the front matter records the shard count.
- Served context beyond 262,144 (the card advertises 1M-context training data) is unverified; no engine publishes a longer configured run.
- No vLLM support, SGLang parser names, or validated GGUF/llama.cpp path was found after searching the card, cookbook, vLLM recipes, and HF author listings; the variant leaves both parsers null and ships no GGUF row.
