---
model_id: moonshotai/Kimi-K2.6
vendor: Moonshot AI
family: Kimi K2
release_date: '2026-04-14'
license:
  name: Modified MIT License
  url: https://huggingface.co/moonshotai/Kimi-K2.6/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 1000
  params_active_b: 32
  context_length_max: 262144
  modalities:
  - text
  - image
  thinking_mode: optional
capabilities:
  tool_calling: yes; auto tool choice with kimi_k2
  structured_outputs: unverified
  vision: true
  languages: unverified
pitwall:
  capability_name: llm.kimi-k2-6
  served_model_name: kimi-k2.6
confidence:
  overall: medium
  notes: Official recipe supplies flags and published 714/600-GB minima; images are
    unpinned latest tags and active deployment guidance targets the NVFP4 B300 cell.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: int4
  default: true
  engine: vllm
  image: vllm/vllm-openai:latest
  min_cuda: "12.8"
  repo: moonshotai/Kimi-K2.6
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
  - https://github.com/vllm-project/recipes/blob/main/models/moonshotai/Kimi-K2.6.yaml
- id: nvfp4
  default: false
  engine: vllm
  image: vllm/vllm-openai:latest
  min_cuda: "12.8"
  repo: nvidia/Kimi-K2.6-NVFP4
  file: null
  format: nvfp4
  min_vram_gb: 600
  context: 131072
  container_disk_gb: unverified
  startup_min: unverified
  flags:
  - --trust-remote-code
  - --kv-cache-dtype
  - fp8
  - --block-size
  - '64'
  - --gpu-memory-utilization
  - '0.90'
  - --attention-backend
  - TOKENSPEED_MLA
  - --attention-config
  - '{"use_prefill_query_quantization":true}'
  - --compilation-config
  - '{"cudagraph_mode":"FULL_AND_PIECEWISE","custom_ops":["all"]}'
  - --max-cudagraph-capture-size
  - '2048'
  - --speculative-config
  - '{"method":"eagle3","model":"lightseekorg/kimi-k2.6-eagle3-mla","num_speculative_tokens":4}'
  env:
    VLLM_FLASHINFER_ALLREDUCE_BACKEND: trtllm
  recommended_gpu_classes:
  - NVIDIA B200
  tool_call_parser: kimi_k2
  reasoning_parser: kimi_k2
  confidence: medium
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/moonshotai/Kimi-K2.6.yaml
---

# moonshotai/Kimi-K2.6

## Summary
Kimi K2.6 is Moonshot's April 2026 flagship multimodal agentic model — the same 1T-total/32B-active INT4 MoE chassis as K2.7-Code (61 layers, 384+1 experts, MoonViT vision, 262,144-token context with YaRN baked in) — tuned for long-horizon coding and agent swarms. Unlike K2.7-Code it supports Instant (non-thinking) mode. [HF card](https://huggingface.co/moonshotai/Kimi-K2.6)

## Deployment recipe

1. For the INT4 path serve `moonshotai/Kimi-K2.6` with vLLM 0.25.0 or newer (the recipe's floor for this model) at TP=8 on one 8xH200-class node: `--mm-encoder-tp-mode data --trust-remote-code --tool-call-parser kimi_k2 --reasoning-parser kimi_k2 --enable-auto-tool-choice`. Neither cell pins a versioned image; pin a digest after a verified launch. [vLLM recipe](https://recipes.vllm.ai/moonshotai/Kimi-K2.6)
2. For the NVFP4 path serve `nvidia/Kimi-K2.6-NVFP4` on a 4-GPU B300/B200-class node with `VLLM_FLASHINFER_ALLREDUCE_BACKEND=trtllm`, `--kv-cache-dtype fp8 --block-size 64 --gpu-memory-utilization 0.90`, TOKENSPEED_MLA attention with prefill query quantization, full-and-piecewise cudagraphs, and Eagle3 speculative decoding against the `lightseekorg/kimi-k2.6-eagle3-mla` draft — carried token-for-token in the nvfp4 variant. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/moonshotai/Kimi-K2.6.yaml)
3. Expose 8000 and require `GET /v1/models` to list `kimi-k2.6` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K2.6?blobs=true)
4. Documented-but-not-carried extras: CPU KV offload via `--kv-transfer-config` with `SimpleCPUOffloadConnector` (verified run used `CPU_OFFLOAD_BYTES_PER_RANK=321854111744`) and optional `--decode-context-parallel-size 4`. AMD mirrors K2.7-Code's AITER/INT4 and FlyDSL cells. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/moonshotai/Kimi-K2.6.yaml)

## Hardware and quantization
INT4 weights are 595.2 GB (byte-identical sizing to K2.7-Code); NVFP4 halves the floor to 600 GB total. AMD paths mirror K2.7-Code (AITER + INT4 QuickReduce on gfx942; FlyDSL on gfx950), and CPU KV offload via `--kv-transfer-config` is documented but not represented here. `unsloth/Kimi-K2.6-GGUF` publishes BF16 (2,053.2 GB) down to UD-Q2_K_XL (340.5 GB) quants plus mmproj; none are validated for Pitwall llama.cpp serving. [vLLM recipe](https://recipes.vllm.ai/moonshotai/Kimi-K2.6), [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K2.6?blobs=true)

## Tool calling, reasoning, and chat template
`kimi_k2` parsers; interleaved thinking with multi-step tool calls. Thinking is the default (`temperature=1.0, top_p=0.95`); Instant mode is enabled with `{'chat_template_kwargs': {'thinking': False}}` and recommended `temperature=0.6`. `preserve_thinking` is optional and off by default. Video input is experimental and official-API only. [HF card](https://huggingface.co/moonshotai/Kimi-K2.6)

## Known issues and community notes
Benchmark footnotes used top_p 1.0 at the full 262,144 context. Structured outputs beyond tool-call JSON are undocumented.

## Sources
- https://huggingface.co/moonshotai/Kimi-K2.6 — Modified MIT license, Instant-mode switch and temperatures, swarm-oriented training (300 sub-agents / 4,000 steps), video caveat — accessed 2026-09-07
- https://huggingface.co/moonshotai/Kimi-K2.6/blob/main/LICENSE — Modified MIT text — accessed 2026-09-07
- https://huggingface.co/moonshotai/Kimi-K2.6/blob/main/docs/deploy_guidance.md — vLLM/SGLang commands matching the K2.7-Code chassis — accessed 2026-09-07
- https://huggingface.co/api/models/moonshotai/Kimi-K2.6?blobs=true — 595.2 GB INT4 across 64 shards, `gated:false` — accessed 2026-09-07
- https://huggingface.co/moonshotai/Kimi-K2.6/raw/main/config.json — 1T/32B layout, MoonViT encoder, baked YaRN, 262,144 context — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/moonshotai/Kimi-K2.6.yaml — 714/600-GB minima, NVFP4 B300 cell with Eagle3 draft and TOKENSPEED_MLA, CPU-offload numbers — accessed 2026-09-07
- https://huggingface.co/nvidia/Kimi-K2.6-NVFP4 — official NVFP4 twin existence — accessed 2026-09-07
- https://huggingface.co/unsloth/Kimi-K2.6-GGUF — community GGUF inventory: BF16 2,053.2 GB, UD-Q2_K_XL 340.5, UD-Q4_K_XL 583.7, UD-Q8_K_XL 594.5 GB plus mmproj — accessed 2026-09-07

## Open questions
- No pinned vLLM image for either variant; both cells reference `latest`. Pin a digest and re-verify after upgrades.
- The NVFP4 cell is author-verified on B300; B200 at TP=4 (720 GB aggregate) clears the 600-GB floor on paper only and needs a measured run.
- INT4 container disk (700 GB selected from the 595.2-GB checkpoint plus headroom) and cold-start time are unverified; measure a real Pod.
- Structured outputs beyond tool-call JSON are undocumented in the card, deploy guidance, and recipe.
