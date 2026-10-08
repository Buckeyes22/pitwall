---
model_id: deepseek-ai/DeepSeek-V4-Flash-Vision-Exp
vendor: DeepSeek-AI
family: DeepSeek V4
release_date: '2026-08-31'
license:
  name: MIT
  url: https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 305
  params_active_b: unverified
  context_length_max: 1048576
  modalities:
  - text
  - image
  thinking_mode: optional
capabilities:
  tool_calling: yes; auto tool choice with deepseek_v4
  structured_outputs: unverified
  vision: true
  languages: English and Chinese (card); broader coverage unverified
pitwall:
  capability_name: llm.deepseek-v4-flash-vision-exp
  served_model_name: deepseek-v4-flash-vision-exp
confidence:
  overall: high
  notes: Official card and vLLM recipe supply the pinned image, flags, and a 202-GB
    minimum; the pinned image is the only runnable path because released wheels route
    the checkpoint to the text-only class.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:deepseekv4-flash-vision
  min_cuda: "12.8"
  repo: deepseek-ai/DeepSeek-V4-Flash-Vision-Exp
  file: null
  format: fp8
  min_vram_gb: 202
  context: 32768
  container_disk_gb: 220
  startup_min: unverified
  flags:
  - --enable-expert-parallel
  - --kv-cache-dtype
  - fp8
  - --block-size
  - '256'
  - --max-model-len
  - '32768'
  - --tool-call-parser
  - deepseek_v4
  - --enable-auto-tool-choice
  - --reasoning-parser
  - deepseek_v4
  - --reasoning-config
  - '{"reasoning_parser":"deepseek_v4","reasoning_start_str":"","reasoning_end_str":""}'
  - --speculative-config
  - '{"method":"dspark","model":"deepseek-ai/DeepSeek-V4-Flash-Vision-Exp","num_speculative_tokens":3,"draft_sample_method":"probabilistic","enable_adaptive_verification":true}'
  env: {}
  recommended_gpu_classes:
  - NVIDIA B300 SXM6 AC
  tool_call_parser: deepseek_v4
  reasoning_parser: deepseek_v4
  confidence: high
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp.yaml
---

# deepseek-ai/DeepSeek-V4-Flash-Vision-Exp

## Summary
DeepSeek-V4-Flash-Vision-Exp is the experimental multimodal sibling of DeepSeek-V4-Flash-0731: the same ~305B MoE chassis (43 layers, 256 routed plus 1 shared expert at 6 per token, FP4 experts plus FP8 attention, YaRN to 1,048,576 tokens, DSpark speculative decoding) extended with a 32-layer ViT and aligner for image input. It improves multimodal agent scores over the text model while keeping comparable text-agent results. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp)

## Deployment recipe

1. Use the pinned `vllm/vllm-openai:deepseekv4-flash-vision` image — released vLLM wheels route the checkpoint to the text-only class and fail (support PR vllm#54566), so the recipe disables pip installs and the pinned image is the only runnable path. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp)
2. The verified single-node cell is one GB200 NVL4 / GB300-class tray at TP4 with expert parallelism: `--enable-expert-parallel --kv-cache-dtype fp8 --block-size 256 --max-model-len 32768`, `--tool-call-parser deepseek_v4 --enable-auto-tool-choice --reasoning-parser deepseek_v4`, `--reasoning-config '{"reasoning_parser":"deepseek_v4","reasoning_start_str":"","reasoning_end_str":""}'`, and DSpark speculative decoding with adaptive verification. Four `NVIDIA B300 SXM6 AC` GPUs carry it with ample KV headroom. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp.yaml)
3. Expose 8000 and require `GET /v1/models` to list `deepseek-v4-flash-vision-exp` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp?blobs=true)
4. Not carried, per the recipe's own scoping: `VLLM_FLASHINFER_AUTOTUNE_SKIP_OPS="trtllm_fp4_block_scale_moe,flashinfer::trtllm_fp4_block_scale_moe"` (environment-specific), `--allowed-local-media-path` (lab mount), and the 8-GPU TP8+EP shape, which has no published vision run. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp.yaml)

## Hardware and quantization
Weights are 167.8 GB (48 shards; FP4 experts with FP8/BF16 attention and vision tower). The recipe publishes a 202-GB total minimum; four `NVIDIA B300 SXM6 AC` GPUs (1,152 GB) carry it with ample KV headroom, and the verified 8-GPU-node path (TP8+EP) has no published vision run. Images are billed as input tokens by dimensions. `unsloth/DeepSeek-V4-Flash-Vision-Exp-GGUF` exists without a validated llama.cpp command. [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp?blobs=true)

## Tool calling, reasoning, and chat template
`deepseek_v4` parsers. Thinking is optional: `thinking_mode="chat"` (non-thinking) or `"thinking"` with `reasoning_effort` low/high/max, matching the 0731 card. Unlike the 0731 repository there is no Jinja chat template — prompt encoding ships as a Python `encoding/` module, which is why the pinned image is mandatory. Sampling: `temperature=1.0, top_p=0.95` for agentic use. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp)

## Known issues and community notes
`num_nextn_predict_layers` is 3 here versus 1 on the 0731 text model (deeper DSpark draft); `rms_norm_eps` differs (1e-20). The "Exp" label means behavior may change; treat benchmark parity with 0731 as approximate.

## Sources
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp — MIT license, vision encoder description, `thinking_mode`/`reasoning_effort` controls, image-token billing, benchmark deltas vs 0731 — accessed 2026-09-07
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp/blob/main/LICENSE — MIT text — accessed 2026-09-07
- https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp?blobs=true — 304.6B parameters (296.4B I8/FP4 + 6.3B F8 + 1.9B BF16), 167.8 GB across 48 shards, `gated:false` — accessed 2026-09-07
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp/raw/main/config.json — 43 layers, 256+1 experts at 6/token, hyper-connections (hc_mult 4), DFlash indexer, DSpark `num_nextn_predict_layers` 3, 32-layer ViT with `vision_max_n_token` 384, YaRN factor 16 to 1,048,576 — accessed 2026-09-07
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp/tree/main — `encoding/` Python prompt module and `inference/` reference implementation (why the pinned image is mandatory: no Jinja chat template) — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp.yaml — pinned image, wheels-fail statement with PR reference, 202-GB minimum, verified GB200 NVL4 flags, env-scoped extras — accessed 2026-09-07
- https://huggingface.co/unsloth/DeepSeek-V4-Flash-Vision-Exp-GGUF — community GGUF existence; no validated command — accessed 2026-09-07

## Open questions
- Container disk (220 GB selected from the 167.8-GB checkpoint plus headroom) and cold-start time are unverified; measure a real Pod before setting a narrow timeout.
- The verified 32,768-token context cap is conservative; larger `--max-model-len` values against the 1M window are unverified for the vision path.
- Active-parameter count is unpublished; config exposes the expert layout but no official active figure.
- No validated GGUF/llama.cpp recipe for the vision checkpoint was found; no GGUF row ships.
