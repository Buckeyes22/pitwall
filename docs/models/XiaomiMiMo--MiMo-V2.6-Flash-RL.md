---
model_id: XiaomiMiMo/MiMo-V2.6-Flash-RL
vendor: Xiaomi
family: MiMo
release_date: '2026-09-21'
license:
  name: MIT
  url: https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL
  gated: false
architecture:
  kind: moe
  params_total_b: 309
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
  capability_name: llm.mimo-v2-6-flash-rl
  served_model_name: mimo-v2.6-flash-rl
confidence:
  overall: medium
  notes: vLLM recipe for this exact checkpoint supplies image, flags, a 208-GB minimum and an H200 verification; container disk and startup time are unmeasured.
accessed: '2026-10-07'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:v0.31.0
  min_cuda: "13.0"
  repo: XiaomiMiMo/MiMo-V2.6-Flash-RL
  file: null
  format: fp8
  min_vram_gb: 208
  context: 131072
  container_disk_gb: 220
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
  - https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.6-Flash-RL.yaml
  - https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL
---

# XiaomiMiMo/MiMo-V2.6-Flash-RL

## Summary
MiMo-V2.6-Flash-RL is the efficiency-balanced checkpoint of Xiaomi's MiMo-V2.6 series: a natively omnimodal sparse MoE (309B total / 15B activated; text, image, video, audio; 1M-token context) trained with one mixed asynchronous-GRPO run across coding, general-agent, visual, and cybersecurity tasks, followed by MOPD2 distillation. [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL)

## Deployment recipe

1. Use `vllm/vllm-openai:v0.31.0` (vLLM 0.31.0 floor). The V2.6 recipe states 0.31.0 is the first stable release that can load this mixed mxfp4-stored / FP8-compute checkpoint ([vllm#57784](https://github.com/vllm-project/vllm/pull/57784)) and that earlier stable releases cannot. The card's own vLLM block names the older `vllm/vllm-openai:mimov25-cu129` image and the V2.5 recipe; the V2.6 recipe (updated 2026-10-07) is newer and checkpoint-specific, so it wins and `mimov25-cu129` is not carried. [vLLM recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.6-Flash-RL.yaml)
2. Serve TP=4 with `--trust-remote-code --gpu-memory-utilization 0.95 --max-model-len auto --reasoning-parser mimo --tool-call-parser mimo --enable-auto-tool-choice --generation-config vllm`, token-for-token the card's and recipe's command. The recipe lists a 208-GB minimum and marks H200 verified; four `NVIDIA H200` GPUs (564 GB) clear it. [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL), [vLLM recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.6-Flash-RL.yaml)
3. Expose 8000 and require `GET /v1/models` to list `mimo-v2.6-flash-rl` before routing through Pitwall. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.6-Flash-RL?blobs=true)
4. DFlash speculative decoding is an opt-in recipe feature (`--speculative-config` with `"method":"dflash"`, 7 tokens). Its drafter ships in the checkpoint's `dflash/` folder but vLLM needs a concrete local snapshot path (`.../snapshots/<hash>/dflash`), which cannot be a static variant flag, so it is not carried. [vLLM recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.6-Flash-RL.yaml)

## Hardware and quantization
Weights are stored as MXFP4 (block 32) for the MoE experts and computed as FP8 (E4M3, dynamic activations, block 128x128; `o_proj` kept unquantized), with the router in BF16. The repository is 177.8 GB across 90 files, including the `dflash/` drafter and the audio tokenizer; the recipe quotes 173 GB for the checkpoint. The HF API's 310.8B safetensors total counts packed `U8` MXFP4 bytes as parameters, so the card's 309B total / 15B activated figure is used for the architecture fields. The card's reference engine is SGLang (single-node TP8 (`--tp 8 --dp 2 --enable-dp-attention`), EAGLE multi-layer speculative decoding, `lmsysorg/sglang:latest`); the vLLM single-node recipe is the shipped path. The official llama.cpp organisation publishes [`ggml-org/MiMo-V2.6-Flash-RL-GGUF`](https://huggingface.co/ggml-org/MiMo-V2.6-Flash-RL-GGUF) (MXFP4, 167.4 GB in 2 shards; Q2_K, 126.2 GB; plus mmproj, MTP and DFlash sidecars). Its card gives only `llama serve -hf ggml-org/MiMo-V2.6-Flash-RL-GGUF` and no llama-server flags, VRAM floor, or image, so no `llama.cpp` variant ships (see Open questions). [HF API](https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.6-Flash-RL?blobs=true), [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL), [config.json](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/raw/main/config.json)

## Tool calling, reasoning, and chat template
`mimo` tool-call and reasoning parsers on both vLLM and SGLang. Thinking is on by default and is a template switch: the chat template appends an empty `<think></think>` to the assistant prompt only when `enable_thinking` is false, and the recipe's client example passes `chat_template_kwargs: {"enable_thinking": true}` and says `false` (or omitting it) disables thinking. There is no documented effort dial. Sampling recommendation from the card: `temperature=1.0, top_p=0.95`; `generation_config.json` carries the same values with `do_sample: false` and `max_new_tokens: 2048`. Tool calls use a `<tool_call>` template block. Structured-output (JSON schema) behaviour is not documented. [chat_template.jinja](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/raw/main/chat_template.jinja), [generation_config.json](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/raw/main/generation_config.json), [vLLM recipe yaml](https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.6-Flash-RL.yaml)

## Known issues and community notes
The V2.6 technical report PDF ships in the repository. Its MOPD successor, `XiaomiMiMo/MiMo-V2.6-Flash-MOPD` (2026-09-27), is a separate checkpoint with its own dossier. The card's evaluation tables compare against closed models and are vendor-reported; no independent benchmark was consulted.

## Sources
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL — MIT license (card metadata), 309B total / 15B activated, 1M context, modalities, SGLang and vLLM commands, `temperature=1.0, top_p=0.95` recommendation — accessed 2026-10-07 (repo sha `5711b268169967567844e1e560e8a3966da959b1`)
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/raw/main/LICENSE — returns 404 (no LICENSE file); the license is the `license: mit` card metadata — accessed 2026-10-07
- https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.6-Flash-RL?blobs=true — `gated:false`, safetensors parameter counts by dtype, 177.8 GB across the listed files — accessed 2026-10-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/raw/main/config.json — 48 layers (1 dense), 256 routed experts top-8, hidden 4096, `max_position_embeddings` 1,048,576, fp8 `quantization_config` with `store_dtype: mxfp4` — accessed 2026-10-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/raw/main/generation_config.json — `temperature` 1.0, `top_p` 0.95, `do_sample` false — accessed 2026-10-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/raw/main/chat_template.jinja — `enable_thinking is false` branch, `<tool_call>` block — accessed 2026-10-07
- https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.6-Flash-RL.yaml — `vllm/vllm-openai:v0.31.0`, 0.31.0 floor, 208-GB minimum, H200 verified, token-for-token flags, DFlash opt-in, `enable_thinking` client example — accessed 2026-10-07
- https://github.com/vllm-project/vllm/blob/v0.31.0/docker/Dockerfile — `ARG CUDA_VERSION=13.0.3` default, the basis for `min_cuda: "13.0"` — accessed 2026-10-07
- https://hub.docker.com/v2/repositories/vllm/vllm-openai/tags/v0.31.0 — tag exists (amd64 and arm64, pushed 2026-10-04); `-cu129` variants are separate tags, which suggests the plain tag is the CUDA 13 build — accessed 2026-10-07
- https://huggingface.co/ggml-org/MiMo-V2.6-Flash-RL-GGUF — official llama.cpp-org GGUF re-publication: file sizes, `llama serve -hf` command, no llama-server flags — accessed 2026-10-07
- https://github.com/vllm-project/vllm/pull/57784 — mxfp4 MoE / bf16 router / DFlash support for MiMo V2, cited by the recipe — accessed 2026-10-07

## Open questions
- Container disk (220 GB selected from the 177.8-GB checkpoint plus headroom) and cold-start time are unverified; measure a real Pod before setting a narrow timeout.
- The CUDA 13.0 floor is inferred from the v0.31.0 Dockerfile default and the existence of separate `-cu129` tags, not from an image manifest inspection.
- The planning context of 131072 matches the sibling MiMo dossiers; `--max-model-len auto` lets vLLM choose the largest length that fits, and no source publishes the resulting value on four `NVIDIA H200` GPUs.
- Structured-output behaviour is undocumented in every consulted source.
- No `llama.cpp` row ships: the `ggml-org/MiMo-V2.6-Flash-RL-GGUF` card publishes no llama-server flags, VRAM floor, or pinned image, and whether the pinned `ghcr.io/ggml-org/llama.cpp` server images load this architecture is unverified. Add a variant only after a real llama-server launch.
