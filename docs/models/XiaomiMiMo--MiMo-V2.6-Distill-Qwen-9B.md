---
model_id: XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B
vendor: Xiaomi
family: MiMo
release_date: '2026-09-21'
license:
  name: MIT
  url: https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B
  gated: false
architecture:
  kind: dense
  params_total_b: 9.41
  params_active_b: 9.41
  context_length_max: 262144
  modalities:
  - text
  - image
  - video
  thinking_mode: optional
capabilities:
  tool_calling: unverified; card tags tool-use and the chat template renders tool calls, but no tool-call parser is documented
  structured_outputs: unverified
  vision: true
  languages: unverified
pitwall:
  capability_name: llm.mimo-v2-6-distill-qwen-9b
  served_model_name: mimo-v2.6-distill-qwen-9b
confidence:
  overall: low
  notes: The card gives one SGLang command with no image tag, VRAM floor, tool-call parser, or hardware class. The
    image is the digest behind lmsysorg/sglang:latest on 2026-10-07 (checked to contain the Qwen3.5 architecture and
    the mimo reasoning parser); VRAM, disk, and startup are unverified.
accessed: '2026-10-07'
openai_chat: true
variants:
- id: bf16
  default: true
  engine: sglang
  image: lmsysorg/sglang:v0.5.21-cu130
  min_cuda: "13.0"
  repo: XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B
  file: null
  format: bf16
  min_vram_gb: unverified
  context: 262144
  container_disk_gb: unverified
  startup_min: unverified
  flags:
  - --reasoning-parser
  - mimo
  env: {}
  recommended_gpu_classes: []
  tool_call_parser: null
  reasoning_parser: mimo
  confidence: low
  sources:
  - https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B
  - https://hub.docker.com/v2/repositories/lmsysorg/sglang/tags/latest
---

# XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B

## Summary
MiMo-V2.6-Distill-Qwen-9B is a 9B dense agentic model that Xiaomi MiMo produced by supervised fine-tuning of [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) on MiMo-generated data (coding, general agents, visual coding, cybersecurity; 77.4B SFT tokens, 27.2B loss-bearing). It is an SFT checkpoint released as a starting point for agentic-RL research, not a MiMo-V2.6 MoE model: its architecture is `Qwen3_5ForConditionalGeneration` (32 layers, hidden 4096, linear-attention layers interleaved with full attention every 4th layer, a 27-layer vision tower, 262,144 positions). [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B), [config.json](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B/raw/main/config.json)

## Deployment recipe

1. Use SGLang. The card's only serving instruction is `sglang serve --model-path XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B --reasoning-parser mimo --host 0.0.0.0 --port 30000` on "a recent SGLang build with Qwen3.5 support"; it names no image tag. [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B)
2. The image is `lmsysorg/sglang:v0.5.21-cu130`. The sibling MiMo-V2.6 cards name `lmsysorg/sglang:latest`, and on 2026-10-07 Docker Hub shows `latest`, `latest-cu130`, `v0.5.21`, and `v0.5.21-cu130` on the same digest (`sha256:b1259f3e...`), so the pinned tag is that image without a rolling reference. The `v0.5.21` source contains `Qwen3_5ForConditionalGeneration` (`python/sglang/srt/models/qwen3_5.py`) and a `mimo` reasoning parser (`python/sglang/srt/parser/reasoning_parser.py`). The CUDA 13.0 floor is taken from the `-cu130` tag name. [Docker Hub tag](https://hub.docker.com/v2/repositories/lmsysorg/sglang/tags/latest), [SGLang v0.5.21 reasoning_parser.py](https://github.com/sgl-project/sglang/blob/v0.5.21/python/sglang/srt/parser/reasoning_parser.py)
3. Carry only `--reasoning-parser mimo` as a flag; Pitwall's SGLang launcher (`src/pitwall/serve.py`) supplies `--model-path`, `--served-model-name`, `--host 0.0.0.0`, `--port 8000`, and `--tp` from the GPU count. The card shows no tensor-parallel setting, so a single GPU is assumed. Require `GET /v1/models` to list `mimo-v2.6-distill-qwen-9b` before routing. `HF_TOKEN` is not required: the HF API reports `gated:false`. [HF API](https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B?blobs=true)
4. Thinking is enabled by request: the card's client example passes `extra_body={"chat_template_kwargs": {"enable_thinking": True}}` and reads `reasoning_content` separately from `content`. [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B)

## Hardware and quantization
Weights are BF16: 9,409,813,744 parameters, 18.85 GB across 4 safetensors shards. The card publishes no VRAM floor, GPU class, or context cap, so `min_vram_gb`, disk, and startup are `unverified` rather than estimated; an unverified VRAM floor is shown as unfit. The 262,144 `context` is the config's `max_position_embeddings`, not a served value. Community and official GGUF re-publications exist: [`ggml-org/MiMo-V2.6-Distill-Qwen-9B-GGUF`](https://huggingface.co/ggml-org/MiMo-V2.6-Distill-Qwen-9B-GGUF) (Q8_0, 9.53 GB, plus a 0.62-GB Q8_0 mmproj; its card gives `llama serve -hf ...` and a chat-template-patch TODO but no llama-server flags or VRAM floor) and `bartowski/MiMo-V2.6-Distill-Qwen-9B-GGUF`. No `llama.cpp` variant ships because no validated llama-server command exists. [HF API](https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B?blobs=true), [ggml-org GGUF API](https://huggingface.co/api/models/ggml-org/MiMo-V2.6-Distill-Qwen-9B-GGUF?blobs=true)

## Tool calling, reasoning, and chat template
`mimo` reasoning parser (card). `generation_config.json` carries `temperature: 0.6, top_k: 20, top_p: 0.95`; the card states no sampling recommendation of its own. The chat template renders tool calls as `<tool_call><function=NAME><parameter=KEY>VALUE</parameter></function></tool_call>` and appends an empty `<think></think>` to the assistant prompt when `enable_thinking` is false, so thinking is on unless disabled. The card documents no tool-call parser flag; the block format matches the Qwen3-coder XML style, but no source ties it to an SGLang `--tool-call-parser` value, so `tool_call_parser` stays null. Image and video parts render as vision placeholders in the template, and the repository ships `preprocessor_config.json` and `video_preprocessor_config.json`; the card itself only demonstrates text. [chat_template.jinja](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B/raw/main/chat_template.jinja), [generation_config.json](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B/raw/main/generation_config.json)

## Known issues and community notes
The card reports the SFT checkpoint against Qwen3.5-9B (for example SWE Verified 61.1 vs 60.0, SWE Pro 44.6 vs 32.0, Terminal Bench 2.1 37.1 vs 27.0, Toolathlon-Verified 35.2 vs 25.9); several rows are Xiaomi-internal "mini" sets and no independent evaluation was consulted. The ggml-org GGUF card notes an interim chat-template patch pending an upstream template fix, which hints the shipped template may change. [HF card](https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B), [ggml-org GGUF card](https://huggingface.co/ggml-org/MiMo-V2.6-Distill-Qwen-9B-GGUF/raw/main/README.md)

## Sources
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B — MIT license (card metadata), Qwen3.5-9B finetune, SFT data mix, SGLang command, `enable_thinking` example, benchmark table — accessed 2026-10-07 (repo sha `2367e865d009c13ac81713a2878291d33ab28177`)
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B/raw/main/LICENSE — returns 404 (no LICENSE file); the license is the `license: mit` card metadata — accessed 2026-10-07
- https://huggingface.co/api/models/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B?blobs=true — `gated:false`, 9,409,813,744 BF16 parameters, 18.85 GB across 17 files, created 2026-09-21 — accessed 2026-10-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B/raw/main/config.json — `Qwen3_5ForConditionalGeneration`, 32 layers, hidden 4096, `full_attention_interval` 4, `max_position_embeddings` 262,144, 27-layer vision tower — accessed 2026-10-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B/raw/main/generation_config.json — `temperature` 0.6, `top_k` 20, `top_p` 0.95 — accessed 2026-10-07
- https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B/raw/main/chat_template.jinja — tool-call block format, `enable_thinking is false` branch, image/video placeholders — accessed 2026-10-07
- https://hub.docker.com/v2/repositories/lmsysorg/sglang/tags/latest and …/tags?page_size=40&ordering=last_updated — `latest` digest equal to `v0.5.21-cu130` — accessed 2026-10-07
- https://github.com/sgl-project/sglang/blob/v0.5.21/python/sglang/srt/models/qwen3_5.py and …/parser/reasoning_parser.py — Qwen3.5 architecture and `mimo` reasoning parser present at the pinned tag — accessed 2026-10-07
- https://huggingface.co/ggml-org/MiMo-V2.6-Distill-Qwen-9B-GGUF — official llama.cpp-org GGUF: Q8_0 and mmproj sizes, `llama serve -hf` command, template-patch note — accessed 2026-10-07
- https://huggingface.co/api/models?search=MiMo-V2.6&limit=50 — existence of other GGUF re-publications (bartowski and others) — accessed 2026-10-07

## Open questions
- VRAM floor, container disk, and cold-start time are unverified; the card and the SGLang cookbook publish none for this 9B checkpoint. Measure a real Pod, then replace the three `unverified` values and add `recommended_gpu_classes`.
- No tool-call parser is documented for SGLang; the template's block format suggests a Qwen3-coder-style parser, which a real tool-call round trip would need to confirm.
- Structured-output behaviour and the served context length are undocumented in every consulted source.
- No `llama.cpp` row ships: the `ggml-org/MiMo-V2.6-Distill-Qwen-9B-GGUF` card has no llama-server flags or VRAM floor, and its template patch is unresolved upstream.
