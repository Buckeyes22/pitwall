---
model_id: MiniMaxAI/MiniMax-M2.7
vendor: MiniMax
family: MiniMax M
release_date: '2026-04-09'
license:
  name: MiniMax M2.7 Non-Commercial License
  url: https://github.com/MiniMax-AI/MiniMax-M2.7/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 229
  params_active_b: 10
  context_length_max: 196608
  modalities:
  - text
  thinking_mode: 'yes'
capabilities:
  tool_calling: yes; auto tool choice with minimax_m2
  structured_outputs: unverified
  vision: false
  languages: English and Chinese (card); broader coverage unverified
pitwall:
  capability_name: llm.minimax-m2-7
  served_model_name: minimax-m2.7
confidence:
  overall: high
  notes: Official vLLM recipe supplies the dedicated minimax27 image, TP=4 constraint,
    parser pair, and a published 276-GB FP8 minimum; the license is non-commercial.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:minimax27
  min_cuda: "12.8"
  repo: MiniMaxAI/MiniMax-M2.7
  file: null
  format: fp8
  min_vram_gb: 276
  context: 131072
  container_disk_gb: 280
  startup_min: unverified
  flags:
  - --tool-call-parser
  - minimax_m2
  - --reasoning-parser
  - minimax_m2
  - --enable-auto-tool-choice
  - --trust-remote-code
  env: {}
  recommended_gpu_classes:
  - NVIDIA H200
  - NVIDIA H100 80GB HBM3
  tool_call_parser: minimax_m2
  reasoning_parser: minimax_m2
  confidence: high
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M2.7.yaml
- id: nvfp4
  default: false
  engine: vllm
  image: vllm/vllm-openai:minimax27
  min_cuda: "12.8"
  repo: nvidia/MiniMax-M2.7-NVFP4
  file: null
  format: nvfp4
  min_vram_gb: 138
  context: 131072
  container_disk_gb: unverified
  startup_min: unverified
  flags:
  - --tool-call-parser
  - minimax_m2
  - --reasoning-parser
  - minimax_m2
  - --enable-auto-tool-choice
  - --trust-remote-code
  - --kv-cache-dtype
  - fp8
  env: {}
  recommended_gpu_classes:
  - NVIDIA B200
  - NVIDIA RTX PRO 6000 Blackwell Server Edition
  tool_call_parser: minimax_m2
  reasoning_parser: minimax_m2
  confidence: medium
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M2.7.yaml
---

# MiniMaxAI/MiniMax-M2.7

## Summary
MiniMax-M2.7 is the April 2026 M2-series coding/agentic text model: a 230B-total/10B-active MoE (62 layers, 256 experts at 8 per token, MTP modules) shipping native FP8 block-quantized weights with a 196,608-token per-sequence context. The license is non-commercial: MIT-style terms for non-commercial use, with any commercial use requiring prior written authorization from MiniMax. [HF card](https://huggingface.co/MiniMaxAI/MiniMax-M2.7)

## Deployment recipe

1. Use the dedicated `vllm/vllm-openai:minimax27` image (floor vLLM 0.20.0). MiniMax verified M2.7 accuracy at vLLM commit `0f3ce4c74b1875791d6604e006b6e905fde9f698`; nightly builds after commit `cf3eacfe58fa9e745c2854782ada884a9f992cf7` fix reported garbled output on older builds. [vLLM recipe](https://recipes.vllm.ai/MiniMaxAI/MiniMax-M2.7)
2. Serve TP=4 — pure TP=8 fails on the FP8 block-quantized gate_up weights (intermediate_size/8 = 192 is not divisible by block_n = 128); the recipe pins TP=4 and recommends TP+EP or DP+EP (`--data-parallel-size 8 --enable-expert-parallel`) for full-node utilization. Flags: `--tool-call-parser minimax_m2 --reasoning-parser minimax_m2 --enable-auto-tool-choice --trust-remote-code`. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M2.7.yaml)
3. Expose 8000 and require `GET /v1/models` to list `minimax-m2.7` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. The HF deploy guide additionally sets `SAFETENSORS_FAST_GPU=1` and documents `--reasoning-parser minimax_m2_append_think` as an alternative reasoning parser; the recipe's `minimax_m2` pair is carried. [deploy guide](https://huggingface.co/MiniMaxAI/MiniMax-M2.7/blob/main/docs/vllm_deploy_guide.md)
4. Not carried: the GB300-cell overrides (`--kv-cache-dtype fp8 --gpu-memory-utilization 0.92 --load-format fastsafetensors --max-num-batched-tokens 8192 --enable-chunked-prefill --enable-prefix-caching`), the 2x RTX PRO 6000 Blackwell cell's load-format overrides, and the documented CUDA-error workaround `--compilation-config '{"cudagraph_mode": "PIECEWISE"}'`. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M2.7.yaml)

## Hardware and quantization
The repository is 230.1 GB of FP8 safetensors; the recipe publishes a 276-GB total minimum ("4x H200/H20/H100 or 4x A100/A800 for weights, plus KV cache headroom") with H100, H200, MI300X, and 2x RTX PRO 6000 Blackwell verified cells. KV cache runs about 240 GB per 1M context tokens (4x96 GB gives ~400K aggregate KV; 8x144 GB up to ~3M). The NVFP4 variant (`nvidia/MiniMax-M2.7-NVFP4`, 138-GB minimum) targets Blackwell and adds `--kv-cache-dtype fp8`. `unsloth/MiniMax-M2.7-GGUF` publishes BF16 (457.5 GB) down to UD-IQ1_M (60.7 GB) quants; none are validated for Pitwall llama.cpp serving. [HF API](https://huggingface.co/api/models/MiniMaxAI/MiniMax-M2.7?blobs=true)

## Tool calling, reasoning, and chat template
`minimax_m2` parsers. The model emits thinking blocks; the card documents the same tool-call syntax as MiniMax-M2 with a dedicated `docs/tool_calling_guide.md` covering both the OpenAI `tools` API and manual tag parsing. Sampling defaults from the card: `temperature=1.0, top_p=0.95, top_k=40`. Config `max_position_embeddings` is 204,800 while deployable per-sequence context is 196,608. [HF card](https://huggingface.co/MiniMaxAI/MiniMax-M2.7)

## Known issues and community notes
Nightly vLLM after commit `cf3eacfe58fa9e745c2854782ada884a9f992cf7` fixes garbled-output reports on older builds. A CUDA-error workaround `--compilation-config '{"cudagraph_mode": "PIECEWISE"}'` is documented. The 2x RTX PRO 6000 Blackwell verified cell relies on the load-format/prefix-caching/chunked-prefill overrides from the recipe.

## Sources
- https://huggingface.co/MiniMaxAI/MiniMax-M2.7 — non-commercial license, 230B/10B claim, `temperature=1.0, top_p=0.95, top_k=40`, 196K per-sequence context — accessed 2026-09-07
- https://github.com/MiniMax-AI/MiniMax-M2.7/blob/main/LICENSE — non-commercial license text (commercial use requires written authorization; "Built with MiniMax M2.7" attribution) — accessed 2026-09-07
- https://huggingface.co/MiniMaxAI/MiniMax-M2.7/blob/main/docs/vllm_deploy_guide.md — TP4/TP+EP/DP+EP commands, `SAFETENSORS_FAST_GPU=1`, KV sizing (240 GB per 1M tokens; 96Gx4 → 400K; 144Gx8 → 3M), nightly-fix commit — accessed 2026-09-07
- https://huggingface.co/MiniMaxAI/MiniMax-M2.7/blob/main/docs/tool_calling_guide.md — OpenAI `tools` API plus manual tag parsing — accessed 2026-09-07
- https://huggingface.co/api/models/MiniMaxAI/MiniMax-M2.7?blobs=true — 230.1 GB of FP8 safetensors, `gated:false` — accessed 2026-09-07
- https://huggingface.co/MiniMaxAI/MiniMax-M2.7/raw/main/config.json — 62 layers, 256 experts at 8/token, MTP modules, `max_position_embeddings` 204,800 — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/MiniMaxAI/MiniMax-M2.7.yaml — minimax27 image, 0.20.0 floor, 276/138-GB minima, TP=4 constraint with the divisibility explanation, verified hardware cells — accessed 2026-09-07
- https://huggingface.co/nvidia/MiniMax-M2.7-NVFP4 — official NVFP4 twin existence (Blackwell) — accessed 2026-09-07
- https://huggingface.co/unsloth/MiniMax-M2.7-GGUF — community GGUF inventory: BF16 457.5 GB down to UD-IQ1_M 60.7 GB — accessed 2026-09-07

## Open questions
- Container disk (280 GB selected from the 230.1-GB checkpoint plus headroom) and cold-start time are unverified; measure a real Pod before setting a narrow timeout.
- Whether `minimax_m2` versus `minimax_m2_append_think` reasoning parsing changes response shape for proxy clients is undocumented; the recipe and deploy guide disagree in passing.
- Config `max_position_embeddings` is 204,800 while every deployable path documents 196,608 per sequence; the front matter records the deployable figure.
- No validated GGUF/llama.cpp recipe was found; no GGUF row ships.
