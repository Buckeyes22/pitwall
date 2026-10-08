---
model_id: zai-org/GLM-5.3
vendor: zai-org
family: GLM-5
release_date: '2026-08-25'
license:
  name: GLM-5.3 License (custom)
  url: https://huggingface.co/zai-org/GLM-5.3
  gated: false
architecture:
  kind: moe
  params_total_b: 753
  params_active_b: unverified
  context_length_max: 1048576
  modalities:
  - text
  thinking_mode: always
capabilities:
  tool_calling: yes; auto tool choice with glm47
  structured_outputs: unverified
  vision: false
  languages: English and Chinese (HF tags); broader coverage unverified
pitwall:
  capability_name: llm.glm-5-3
  served_model_name: glm-5.3
confidence:
  overall: medium
  notes: Official vLLM recipe supplies image, flags, and published FP8/BF16 VRAM minima;
    active-parameter count and container sizing remain unpublished.
accessed: '2026-09-07'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:v0.28.0
  min_cuda: "12.8"
  repo: zai-org/GLM-5.3
  file: null
  format: fp8
  min_vram_gb: 893
  context: 131072
  container_disk_gb: 880
  startup_min: unverified
  flags:
  - --kv-cache-dtype
  - fp8
  - --speculative-config.method
  - mtp
  - --speculative-config.num_speculative_tokens
  - '5'
  - --tool-call-parser
  - glm47
  - --reasoning-parser
  - glm45
  - --enable-auto-tool-choice
  env: {}
  recommended_gpu_classes:
  - NVIDIA H200
  - NVIDIA B200
  tool_call_parser: glm47
  reasoning_parser: glm45
  confidence: high
  sources:
  - https://github.com/vllm-project/recipes/blob/main/models/zai-org/GLM-5.3.yaml
---

# zai-org/GLM-5.3

## Summary
GLM-5.3 is Z.ai's 753B-total MoE flagship of the GLM-5 DSA family: 78 hidden layers, 256 routed plus 1 shared expert with 8 experts per token, sparse-attention indexer, native FP8 weights, and a native 1,048,576-token context. It is text-only and served through the official vLLM recipe on one 8-GPU node. [HF card](https://huggingface.co/zai-org/GLM-5.3), [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3)

## Deployment recipe

1. Use `vllm/vllm-openai:v0.28.0` on vLLM 0.28.0 or newer (`transformers>=5.15.0`); the recipe publishes this as the stable-tagged NVIDIA image (AMD uses `vllm/vllm-openai-rocm:v0.28.0`, Ascend has its own nightly). [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3)
2. Serve `zai-org/GLM-5.3` with TP=8; the catalogue launcher derives `--tensor-parallel-size` from the pod's GPU count, the served name, host, and port. The recipe's primary launch flags are `--kv-cache-dtype fp8` (Hopper; the Blackwell base args use `fp8_e4m3`), MTP speculative decoding (`--speculative-config.method mtp --speculative-config.num_speculative_tokens 5`), `--tool-call-parser glm47`, `--reasoning-parser glm45`, and `--enable-auto-tool-choice`. Do not add `--trust-remote-code`; the recipe does not use it. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/zai-org/GLM-5.3.yaml)
3. Expose 8000 and require `GET /v1/models` to list `glm-5.3` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/zai-org/GLM-5.3?blobs=true)
4. For the B200 full-1M-context cell the recipe adds `--max-num-seqs 32`; AMD adds the AITER backend flags and `--gpu-memory-utilization 0.80 --max-model-len 524288`. Neither is carried in the shipped variant. [recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/zai-org/GLM-5.3.yaml)

## Hardware and quantization
The default repository is native FP8: 753.3B parameters, 755.7 GB of safetensors by HF metadata. vLLM publishes total VRAM minima of 893 GB (FP8), 1,786 GB (BF16, multi-node), 558 GB (NVFP4, `Inferact/GLM-5.3-NVFP4`), and 447 GB (W4A8). Eight `NVIDIA H200` GPUs (1,128 GB) are the conservative minimum viable listed class; eight B200s are also appropriate. The B200 1M-context cell adds `--max-num-seqs 32`. `zai-org/GLM-5.3-BF16` exists for BF16 deployments beyond one node. Community GGUFs exist (`unsloth/GLM-5.3-GGUF`) with no validated llama.cpp command; do not put one behind Pitwall until validated. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3), [HF API](https://huggingface.co/api/models/zai-org/GLM-5.3?blobs=true)

## Tool calling, reasoning, and chat template
Use `glm47` for tool calls and `glm45` for reasoning with auto-tool choice; the template emits XML `<tool_call><arg_key>...` calls. Thinking is always on: the template opens `<think>` unconditionally; `reasoning_effort` accepts `low`/`high`/`max` (default max), and `clear_thinking=true` strips prior reasoning for chat scenarios. Multimodal inputs are explicitly rejected by the template. [HF card](https://huggingface.co/zai-org/GLM-5.3)

Structured-output reliability for this parser/model is unverified. Generation defaults are `temperature=1.0, top_p=0.95`.

## Known issues and community notes
The license tag is `other` (custom `glm-5.3` terms) — unlike the MIT-licensed GLM-5.1/5.2/5.3-Flash; review the repository license before commercial redeployment. `zai-org/GLM-5.3-Flash` covers the smaller 321B sibling and has its own dossier.

## Sources
- https://huggingface.co/zai-org/GLM-5.3 — custom `glm-5.3` license, always-on thinking and `reasoning_effort`/`clear_thinking`, multimodal rejection, tool syntax — accessed 2026-09-07
- https://huggingface.co/api/models/zai-org/GLM-5.3?blobs=true — 753.3B parameters (751.2B F8_E4M3 + 2.1B BF16), 755.7 GB across 141 shards, `gated:false` — accessed 2026-09-07
- https://huggingface.co/zai-org/GLM-5.3/raw/main/config.json — `GlmMoeDsaForCausalLM`, 78+1 layers, 256+1 experts at 8/token, hidden 6144, `max_position_embeddings` 1,048,576, rope_theta 8e6 with no scaling — accessed 2026-09-07
- https://huggingface.co/zai-org/GLM-5.3/raw/main/generation_config.json — `temperature 1.0, top_p 0.95` — accessed 2026-09-07
- https://recipes.vllm.ai/zai-org/GLM-5.3 — image and version floor, Hopper/Blackwell KV divergence, TP8 guidance, VRAM minima — accessed 2026-09-07
- https://github.com/vllm-project/recipes/blob/main/models/zai-org/GLM-5.3.yaml — token-for-token launch args, FP8 893 / NVFP4 558 / W4A8 447 / BF16 1,786-GB minima, `--max-num-seqs 32` B200 cell — accessed 2026-09-07
- https://huggingface.co/zai-org/GLM-5.3-BF16 — official BF16 twin existence for multi-node deployment — accessed 2026-09-07
- https://huggingface.co/unsloth/GLM-5.3-GGUF — community GGUF repository existence; no validated command — accessed 2026-09-07

## Open questions
- Active-parameter count is unpublished; I searched the HF card, config, vLLM recipe and yaml, and found only the 753B total.
- Container disk (880 GB selected from the 755.7-GB checkpoint plus headroom) and cold-start time are unverified; measure a real Pod before setting a narrow timeout.
- A per-rank H200 VRAM table for TP=8 at 131,072 context was not published; workload/concurrency needs a smoke test.
- No validated llama.cpp command, GGUF layout, or projector arrangement was found after searching the HF card, Unsloth's repository, and the vLLM/SGLang recipe pages; no GGUF variant ships.
