---
model_id: XiaomiMiMo/MiMo-V2.5
vendor: Xiaomi
family: MiMo
release_date: '2026-04-27'
license:
  name: MIT
  url: https://huggingface.co/XiaomiMiMo/MiMo-V2.5/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 311
  params_active_b: 15
  context_length_max: 1048576
  modalities:
  - text
  - image
  - video
  - audio
  thinking_mode: optional
capabilities:
  tool_calling: yes; auto tool choice with mimo
  structured_outputs: unverified
  vision: true
  languages: English and Chinese (card); broader coverage unverified
pitwall:
  capability_name: llm.mimo-v2-5
  served_model_name: mimo-v2.5
confidence:
  overall: medium
  notes: Official vLLM recipe supplies image, flags, and a 374-GB FP8 minimum; TP=8
    is broken for this checkpoint and the verified topology is TP=4.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:mimov25-cu129
  min_cuda: "12.9"
  repo: XiaomiMiMo/MiMo-V2.5
  file: null
  format: fp8
  min_vram_gb: 374
  context: 131072
  container_disk_gb: unverified
  startup_min: unverified
  flags:
  - --trust-remote-code
  - --gpu-memory-utilization
  - '0.95'
  - --max-model-len
  - auto
  - --reasoning-parser
  - mimo
  - --tool-call-parser
  - mimo
  - --enable-auto-tool-choice
  - --generation-config
  - vllm
  env: {}
  recommended_gpu_classes:
  - NVIDIA H200
  tool_call_parser: mimo
  reasoning_parser: mimo
  confidence: medium
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.5.yaml
---

# XiaomiMiMo/MiMo-V2.5

## Summary
MiMo-V2.5 is Xiaomi's natively omnimodal sparse MoE (310.7B total / 15B active; 48 layers, 256 routed experts top-8, hybrid SWA:GA attention with a learnable sink) with native FP8 weights, a 1,048,576-token context, and text/image/video/audio input through a 729M ViT and a 261M audio encoder. [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.5)

## Deployment recipe

1. Use the dedicated `vllm/vllm-openai:mimov25-cu129` image (vLLM 0.21.0 floor) — stable vLLM does not yet support MiMo V2.5, so the pinned image is the supported path. [vLLM recipe](https://recipes.vllm.ai/XiaomiMiMo/MiMo-V2.5)
2. Serve TP=4 — not TP=8: the recipe yaml documents that FP8 TP=8 hits an attention-projection shape mismatch and that expert parallelism does not fix it; the author-verified configuration is TP=4, so size the pod at exactly four GPUs. Flags: `--trust-remote-code --gpu-memory-utilization 0.95 --max-model-len auto --reasoning-parser mimo --tool-call-parser mimo --enable-auto-tool-choice --generation-config vllm`. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.5.yaml)
3. Expose 8000 and require `GET /v1/models` to list `mimo-v2.5` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.5?blobs=true)
4. Optional MTP speculative decoding adds `--speculative-config '{"method":"mtp","num_speculative_tokens":1}'`; it is not carried in the variant. The card's SGLang example (TP8/DP2 with `--moe-a2a-backend deepep`, `--reasoning-parser qwen3`) is a second engine option, also not carried. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.5.yaml)

## Hardware and quantization
Weights are native FP8 (E4M3, block 128x128, o_proj kept BF16): 315.7 GB across 18 shards. The recipe publishes a 374-GB total minimum, so four `NVIDIA H200` GPUs (564 GB) are the listed minimum viable class. The card's own SGLang example (TP8/DP2, `--moe-a2a-backend deepep`) is a second engine option not carried as a variant. `unsloth/MiMo-V2.5-GGUF` and other community GGUFs exist without a validated llama.cpp command. [HF API](https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.5?blobs=true)

## Tool calling, reasoning, and chat template
`mimo` parsers on vLLM (the card's SGLang cells use `--tool-call-parser mimo` with `--reasoning-parser qwen3`). Thinking segments are template-driven; a discrete thinking on/off parameter is not documented, and control semantics stay unverified. Sampling recommendation from the card: `temperature=1.0, top_p=0.95`. [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.5)

## Known issues and community notes
The card carries a config-update notice: re-pull `config.json`/`tokenizer_config.json` for downloads predating commit `4da2748`. `MiMo-V2.5-Base` is the 256K-context base; `MiMo-V2.5-ASR` and `MiMo-V2.5-DFlash` are separate skews.

## Sources
- https://huggingface.co/XiaomiMiMo/MiMo-V2.5 — MIT license, omnimodal claim (729M ViT, 261M audio encoder), `temperature=1.0, top_p=0.95` recommendation, config-update notice for commit `4da2748` — accessed 2026-09-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.5/raw/main/LICENSE — MIT text — accessed 2026-09-07
- https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.5?blobs=true — 306.7B F8_E4M3 + 4.05B BF16 parameters (310.7B total), 315.7 GB across 18 files, `gated:false` — accessed 2026-09-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.5/raw/main/config.json — 48 layers (1 dense), 256 experts top-8, hybrid SWA:GA 5:1 with learnable sink, native FP8 block 128x128 with o_proj BF16, 3 MTP layers, 1,048,576 context — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.5.yaml — mimov25-cu129 image, 0.21.0 floor, TP=4 caveat with the shape-mismatch explanation, 374-GB minimum, token-for-token flags — accessed 2026-09-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Base and …/MiMo-V2.5-ASR and …/MiMo-V2.5-DFlash — sibling skews (256K base, ASR, DFlash) — accessed 2026-09-07
- https://huggingface.co/unsloth/MiMo-V2.5-GGUF — community GGUF existence (with bartowski/mradermacher mirrors); no validated command — accessed 2026-09-07

## Open questions
- Container disk and cold-start time are unverified; measure a real Pod before setting a narrow timeout.
- Thinking-control semantics are undocumented beyond template segments: I searched the card, config, tokenizer template, vLLM recipe, and SGLang examples without finding an on/off parameter or effort dial.
- Structured outputs are undocumented in every consulted source.
- Whether the Unsloth/community GGUFs boot under llama-server for this omnimodal checkpoint (projector handling) is unverified; no GGUF row ships.
