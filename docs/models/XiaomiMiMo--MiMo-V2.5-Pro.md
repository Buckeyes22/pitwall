---
model_id: XiaomiMiMo/MiMo-V2.5-Pro
vendor: Xiaomi
family: MiMo
release_date: '2026-04-27'
license:
  name: MIT
  url: https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 1023
  params_active_b: 42
  context_length_max: 1048576
  modalities:
  - text
  thinking_mode: optional
capabilities:
  tool_calling: yes; auto tool choice with mimo
  structured_outputs: unverified
  vision: false
  languages: English and Chinese (card); broader coverage unverified
pitwall:
  capability_name: llm.mimo-v2-5-pro
  served_model_name: mimo-v2.5-pro
confidence:
  overall: medium
  notes: Official vLLM recipe supplies image, flags, and a 1224-GB FP8 minimum; the
    card's reference deployment is multi-node SGLang, so single-pod B200 is the only
    listed class.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:mimov25-cu129
  min_cuda: "12.9"
  repo: XiaomiMiMo/MiMo-V2.5-Pro
  file: null
  format: fp8
  min_vram_gb: 1224
  context: 131072
  container_disk_gb: 1200
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
  - NVIDIA B200
  tool_call_parser: mimo
  reasoning_parser: mimo
  confidence: medium
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.5-Pro.yaml
---

# XiaomiMiMo/MiMo-V2.5-Pro

## Summary
MiMo-V2.5-Pro is Xiaomi's 1.02T-total/42B-active text MoE (70 layers, 384 routed experts top-8, SWA:GA hybrid attention) with native FP8 weights, a 1,048,576-token context, and heavy tool-calling training ("thousands of tool calls" trajectories). [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro)

## Deployment recipe

1. Use the dedicated `vllm/vllm-openai:mimov25-cu129` image (vLLM 0.21.0 floor) — stable vLLM does not yet support MiMo V2.5. [vLLM recipe](https://recipes.vllm.ai/XiaomiMiMo/MiMo-V2.5-Pro)
2. Serve TP=8 with the same flag set as MiMo-V2.5: `--trust-remote-code --gpu-memory-utilization 0.95 --max-model-len auto --reasoning-parser mimo --tool-call-parser mimo --enable-auto-tool-choice --generation-config vllm`. The recipe publishes a 1224-GB total minimum; eight `NVIDIA H200` GPUs (1,128 GB) sit below it, so eight `NVIDIA B200` GPUs (1,440 GB) are the listed class and an H200 deployment would need a context reduction no source publishes. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.5-Pro.yaml)
3. Expose 8000 and require `GET /v1/models` to list `mimo-v2.5-pro` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.5-Pro?blobs=true)
4. The card's reference deployment is multi-node SGLang via LeaderWorkerSet (`--tp-size 16 --ep-size 16`, EAGLE/MTP speculative decoding) — a topology a single RunPod pod cannot express, which is why the vLLM single-node recipe is the shipped path. [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro)

## Hardware and quantization
Weights are native FP8: 1,033.4 GB across 34 shards. The card's reference deployment is multi-node SGLang (`--tp-size 16 --ep-size 16`, EAGLE/MTP speculative decoding) via LeaderWorkerSet, which a single RunPod pod cannot express; the vLLM single-node recipe is the deployable path here. `MiMo-V2.5-Pro-FP4-DFlash` (569.8 GB) exists for DFlash stacks outside the catalogue engines. Community GGUFs (`unsloth/MiMo-V2.5-Pro-GGUF`, others) lack a validated llama.cpp command. [HF API](https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.5-Pro?blobs=true)

## Tool calling, reasoning, and chat template
`mimo` parsers on vLLM (SGLang cells use `--reasoning-parser mimo` there). `reasoning_effort`-style control is not documented; thinking segments are template-driven and control semantics stay unverified. Sampling recommendation from the card: `temperature=1.0, top_p=0.95`. Text-only inputs. [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro)

## Known issues and community notes
Trained on 27T tokens; `MiMo-V2.5-Pro-Base` is the 256K-context base. No discrete thinking toggle is documented.

## Sources
- https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro — MIT license, 1.02T/42B claim, "thousands of tool calls" training, `temperature=1.0, top_p=0.95` recommendation, 27T-token training — accessed 2026-09-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro/raw/main/LICENSE — MIT text — accessed 2026-09-07
- https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.5-Pro?blobs=true — 1,013.6B F8_E4M3 + 9.5B BF16 parameters, 1,033.4 GB across 34 files, `gated:false` — accessed 2026-09-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro/raw/main/config.json — 70 layers (1 dense), 384 experts top-8, hidden 6144, SWA:GA 6:1, 3 MTP layers, 1,048,576 context — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.5-Pro.yaml — mimov25-cu129 image, 0.21.0 floor, 1224-GB minimum, token-for-token flags — accessed 2026-09-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro-Base and …/MiMo-V2.5-Pro-FP4-DFlash — sibling skews (256K base; 569.8-GB FP4-DFlash for DFlash stacks) — accessed 2026-09-07
- https://huggingface.co/unsloth/MiMo-V2.5-Pro-GGUF — community GGUF existence (bartowski/AesSedai mirrors); no validated command — accessed 2026-09-07

## Open questions
- Container disk (1,200 GB selected from the 1,033.4-GB checkpoint plus headroom) and cold-start time are unverified; measure a real Pod before setting a narrow timeout.
- Whether an H200 node serves a reduced-context variant below the published 1224-GB floor is unverified; no source publishes a working `--max-model-len` for it.
- Thinking-control semantics are undocumented beyond template segments (searched: card, config, tokenizer template, vLLM recipe, SGLang example); there is no effort dial.
- No validated structured-output or GGUF/llama.cpp recipe was found; no GGUF row ships.
