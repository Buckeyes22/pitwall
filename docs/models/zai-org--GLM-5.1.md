---
model_id: zai-org/GLM-5.1
vendor: zai-org
family: GLM-5
release_date: '2026-04-03'
license:
  name: MIT
  url: https://huggingface.co/zai-org/GLM-5.1/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 754
  params_active_b: unverified
  context_length_max: 202752
  modalities:
  - text
  thinking_mode: optional
capabilities:
  tool_calling: yes; auto tool choice with glm47
  structured_outputs: unverified
  vision: false
  languages: English and Chinese (HF tags); broader coverage unverified
pitwall:
  capability_name: llm.glm-5-1
  served_model_name: glm-5.1-fp8
confidence:
  overall: medium
  notes: Official vLLM guide supplies image, flags, and 8xH200 topology; no VRAM minima
    or active-parameter count is published.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:glm51
  min_cuda: "12.8"
  repo: zai-org/GLM-5.1-FP8
  file: null
  format: fp8
  min_vram_gb: unverified
  context: 131072
  container_disk_gb: 880
  startup_min: unverified
  flags:
  - --tool-call-parser
  - glm47
  - --reasoning-parser
  - glm45
  - --enable-auto-tool-choice
  - --chat-template-content-format=string
  env: {}
  recommended_gpu_classes:
  - NVIDIA H200
  tool_call_parser: glm47
  reasoning_parser: glm45
  confidence: medium
  sources:
  - https://github.com/vllm-project/recipes/blob/main/GLM/GLM5.md
---

# zai-org/GLM-5.1

## Summary
GLM-5.1 is the MIT-licensed April 2026 GLM-5 release: the same 754B-total DSA MoE family as GLM-5.2/5.3 (78 layers, 256 routed plus 1 shared expert, sparse-attention indexer) with a native 202,752-token context and optional thinking via the `enable_thinking` chat-template kwarg. The default repository is BF16; the deployable single-node path is the official `zai-org/GLM-5.1-FP8` sibling. [HF card](https://huggingface.co/zai-org/GLM-5.1), [vLLM guide](https://github.com/vllm-project/recipes/blob/main/GLM/GLM5.md)

## Deployment recipe

1. Use the dedicated `vllm/vllm-openai:glm51` image — `vllm/vllm-openai:glm51-cu130` when the host driver is CUDA 13+ — on vLLM 0.19.0 stable. The guide explicitly says to prefer the stable release over nightlies; vLLM `main` is needed only when combining tool calling with MTP. DeepGEMM is required for the FP8 path. [vLLM guide](https://github.com/vllm-project/recipes/blob/main/GLM/GLM5.md)
2. Serve `zai-org/GLM-5.1-FP8` with TP=8; the catalogue launcher derives tensor parallelism, served name, host, and port. The documented docker launch carries `--tool-call-parser glm47`, `--reasoning-parser glm45`, `--enable-auto-tool-choice`, and `--chat-template-content-format=string`; the bare-metal variant adds MTP (`--speculative-config.method mtp --speculative-config.num_speculative_tokens 3`), which is not carried in the shipped variant. [vLLM guide](https://github.com/vllm-project/recipes/blob/main/GLM/GLM5.md)
3. Expose 8000 and require `GET /v1/models` to list `glm-5.1-fp8` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/zai-org/GLM-5.1?blobs=true)

## Hardware and quantization
The default `zai-org/GLM-5.1` repository is BF16 at roughly 1.51 TB (282 shards) and does not fit a single validated 8-GPU pod; the guide's topology is 8xH200 (or H20) 141 GB against the FP8 sibling (approximately 756 GB of weights, by analogy with the GLM-5.3 FP8 twin — unverified for this repository). No numeric VRAM minimum is published, so the variant floor stays unverified. `unsloth/GLM-5.1` and `unsloth/GLM-5.1-GGUF` exist; no validated llama.cpp command was found. [HF API](https://huggingface.co/api/models/zai-org/GLM-5.1?blobs=true)

## Tool calling, reasoning, and chat template
Use `glm47`/`glm45` as for GLM-5.2. Unlike GLM-5.3, thinking is optional through `enable_thinking` (omit or false to disable); `clear_thinking` is also supported. There is no `reasoning_effort` control in this template. Generation defaults are `temperature=1.0, top_p=0.95`. Structured-output reliability is unverified. [HF card](https://huggingface.co/zai-org/GLM-5.1)

## Known issues and community notes
DeepGEMM is required for the FP8 path on vLLM. The config-update history on the HF repository (April–May 2026) suggests re-pulling weights if the checkout predates the latest tokenizer/config fixes.

## Sources
- https://huggingface.co/zai-org/GLM-5.1 — MIT license, `enable_thinking`/`clear_thinking` template controls, vLLM 0.19.0 floor, `transformers>=5.4.0` — accessed 2026-09-07
- https://huggingface.co/api/models/zai-org/GLM-5.1?blobs=true — 753.9B BF16 parameters, ~1.51 TB across 282 shards, `gated:false` — accessed 2026-09-07
- https://huggingface.co/zai-org/GLM-5.1/raw/main/config.json — `GlmMoeDsaForCausalLM`, `max_position_embeddings` 202,752, rope_theta 1e6, no scaling — accessed 2026-09-07
- https://huggingface.co/zai-org/GLM-5.1/raw/main/generation_config.json — `temperature 1.0, top_p 0.95` — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/GLM/GLM5.md — glm51 image and cu130 variant, stable-release guidance, docker and bare-metal commands, 8xH200/H20 topology, MTP 3 — accessed 2026-09-07
- https://huggingface.co/zai-org/GLM-5.1-FP8 — official FP8 sibling existence — accessed 2026-09-07
- https://huggingface.co/unsloth/GLM-5.1 and https://huggingface.co/unsloth/GLM-5.1-GGUF — community mirror and GGUF existence; no validated command — accessed 2026-09-07

## Open questions
- No numeric VRAM minimum is published; I searched the HF card, the vLLM GLM5 guide, and the recipe repository. The FP8 sibling sizing is inferred from the byte-identical GLM-5.3 FP8 twin and needs a measured Pod.
- Active-parameter count is unpublished; the config exposes the expert layout but not an official active figure.
- Container disk (880 GB selected from the inferred ~756-GB FP8 checkpoint plus headroom) and cold-start time are unverified.
- No validated structured-output, GGUF, or llama.cpp recipe was found after searching the HF card, Unsloth repositories, and the vLLM guide; no GGUF variant ships.
