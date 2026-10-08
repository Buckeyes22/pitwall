---
model_id: Qwen/Qwen3.8-27B
vendor: Qwen
family: Qwen3.8
release_date: '2026-08-14'
license:
  name: Apache-2.0
  url: https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/LICENSE
  gated: false
architecture:
  kind: dense
  params_total_b: 27
  params_active_b: 27
  context_length_max: 262144
  modalities:
  - text
  - image
  - video
  thinking_mode: optional
capabilities:
  tool_calling: yes; recipe prescribes qwen3_coder for NVFP4; verify parser availability
    in the chosen vLLM image
  structured_outputs: json, regex (Qwen3 reasoning parser family documentation)
  vision: true
  languages: unverified
pitwall:
  capability_name: llm.qwen3-8-27b
  served_model_name: qwen3.8-27b
confidence:
  overall: medium
  notes: Architecture and parser/reasoning behavior are well sourced. The conservative
    one-80GB recommendation reserves room for runtime/KV but is not an official exact
    H100 launch measurement; the exact qwen3_coder parser must be smoke-tested against
    the selected image.
accessed: '2026-08-27'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:latest
  min_cuda: "12.8"
  repo: Qwen/Qwen3.8-27B-FP8
  file: null
  format: fp8
  min_vram_gb: 80
  context: 32768
  container_disk_gb: 70
  startup_min: 30
  flags:
  - --max-model-len
  - '32768'
  - --gpu-memory-utilization
  - '0.85'
  - --kv-cache-dtype
  - fp8
  - --reasoning-parser
  - qwen3
  - --enable-auto-tool-choice
  - --tool-call-parser
  - qwen3_coder
  env: {}
  recommended_gpu_classes:
  - NVIDIA H100 80GB HBM3
  - NVIDIA H200
  - NVIDIA A100-SXM4-80GB
  - NVIDIA RTX 6000 Ada Generation
  - NVIDIA A100 80GB PCIe
  tool_call_parser: qwen3_coder
  reasoning_parser: qwen3
  confidence: medium
  sources: []
- id: gguf:UD-Q4_K_XL
  default: false
  engine: llama.cpp
  image: ghcr.io/ggml-org/llama.cpp:server-cuda13
  min_cuda: "12.8"
  repo: unsloth/Qwen3.8-27B-GGUF
  file: Qwen3.8-27B-UD-Q4_K_XL.gguf
  format: gguf
  min_vram_gb: 24
  context: 131072
  container_disk_gb: unverified
  startup_min: 30
  flags:
  - --ctx-size
  - '131072'
  companions:
  - kind: mmproj
    repo: unsloth/Qwen3.8-27B-GGUF
    file: mmproj-F16.gguf
    flags:
    - --mmproj
    - mmproj-F16.gguf
  env: {}
  recommended_gpu_classes:
  - NVIDIA H100 80GB HBM3
  - NVIDIA H200
  - NVIDIA A100-SXM4-80GB
  - NVIDIA RTX 6000 Ada Generation
  - NVIDIA A100 80GB PCIe
  - NVIDIA GeForce RTX 4090
  - NVIDIA RTX 5000 Ada Generation
  tool_call_parser: qwen3_coder
  reasoning_parser: qwen3
  confidence: medium
  sources:
  - https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true
---

# Qwen/Qwen3.8-27B

## Summary

Qwen3.8-27B is a 27B dense hybrid-attention multimodal (text/image/video) model with a 262,144-token native context and built-in MTP head. Its official vLLM recipe is viable behind Pitwall’s OpenAI proxy; use official FP8 on a single 80-GB GPU with a conservative 32K serving cap, or use BF16 only where the remaining 80-GB runtime/KV budget is acceptable. [model card](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/README.md) [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)

## Deployment recipe

The source recipe requires vLLM 0.17.0+ and supports the Qwen3.5 conditional-generation architecture. For an allowed RunPod class, use `vllm/vllm-openai:latest` with the official `Qwen/Qwen3.8-27B-FP8`, TP1, `--max-model-len 32768`, `--gpu-memory-utilization 0.85`, `--kv-cache-dtype fp8`, `--reasoning-parser qwen3`, `--enable-auto-tool-choice`, and `--tool-call-parser qwen3_coder`; the 32K/0.85 choices are conservative operational settings, not an upstream validated H100-specific command. `HF_TOKEN` is not needed: the public Hub API response is HTTP 200 and `gated: false`. Confirm readiness only when `GET /v1/models` includes `qwen3.8-27b`. [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) [Hub API](https://huggingface.co/api/models/Qwen/Qwen3.8-27B?blobs=true) [vLLM supported models](https://docs.vllm.ai/en/latest/models/supported_models/)

## Hardware and quantization

The official BF16 Hub repo is 18 safetensors shards totaling 51.75 GiB; its official FP8 sibling totals 28.75 GiB. An 80-GB GPU is the conservative minimum in this dossier, leaving room for the vision tower, runtime allocation, and FP8 KV at 32K. The recipe demonstrates the 27B FP8 checkpoint at TP4/262K and consumer NVFP4 alternatives on RTX 5090, but that GPU class is not in Pitwall’s allowed list; it does not publish a direct H100/A100 single-card measurement. Do not use MXFP4 on NVIDIA: the recipe says its Nvidia linear-method support is missing. No official GGUF is listed by the vendor; third-party GGUFs require llama.cpp rather than this vLLM recipe and are not selected here. [BF16 file list](https://huggingface.co/api/models/Qwen/Qwen3.8-27B?blobs=true) [FP8 file list](https://huggingface.co/api/models/Qwen/Qwen3.8-27B-FP8?blobs=true) [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)

## Tool calling, reasoning, and chat template

The official recipe calls for `--reasoning-parser qwen3`; vLLM’s reasoning documentation identifies the `qwen3` parser, says Qwen3 reasoning is default-on, and documents `chat_template_kwargs: {"enable_thinking": false}` to turn it off. The exact model template supports `enable_thinking`, `preserve_thinking` (default true), and `reasoning_effort` values `xhigh`, `medium`, and `low`; it emits `<think>` and XML `<tool_call><function=...>` blocks. The recipe’s exact tool parser is `qwen3_coder`, while the current generic vLLM tool-parser page documents `qwen3_xml` rather than `qwen3_coder`; this is a release/image compatibility risk, so the tool parser must be verified by a smoke test before paid launches. The Qwen3 parser family documents JSON/regex structured-output support. [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) [vLLM reasoning outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling/) [template](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/chat_template.jinja)

## Known issues and community notes

The official recipe warns that MXFP4 does not load on Nvidia because vLLM lacks the needed linear-method support; choose FP8/NVFP4 instead. It reports RTX-5090-specific NVFP4 behavior, including a one-card `--enforce-eager` startup requirement, but that is not transferable evidence for the listed RunPod GPUs and has not been made a default. The recipe’s long-context section says 262K is native and shows a 1M override; this dossier does not enable it because its VRAM cost on a single allowed GPU is unsourced. Community reports supply two useful but non-authoritative signals: one vLLM user reports BF16/FP8 results on an RTX 6000 Pro using the same `qwen3_coder`/`qwen3` parser pair, and a Club-3090 benchmark on 2× RTX 3090 relied on an AutoRound INT4 model plus an unmerged/custom DFlash2 patch. Neither is a stock-vLLM RunPod recipe; do not copy the patch stack. Club-3090 exists and describes itself as local 3090/4090/5090 multi-engine recipes, but it currently ships no official Qwen3.8-27B profile. [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) [RTX 6000 Pro report](https://www.reddit.com/r/LocalLLaMA/comments/1vobpek/benchmark_qwen3827b_full_precision_fp8_rtx_6000/) [Club-3090 benchmark](https://www.reddit.com/r/LocalLLaMA/comments/1vsccit/qwen3827b_on_2x_3090_vllm_dflash2_218_toks_single/) [Club-3090](https://github.com/noonghunna/club-3090)

## Sources

- https://huggingface.co/Qwen/Qwen3.8-27B — model card: release, dense parameter count, multimodality, 262K/1M claims, thinking controls, compatible engines — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/config.json — architecture, BF16 dtype, position limit, vision/video configuration — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/generation_config.json — shipped sampling defaults — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/chat_template.jinja — tool XML and thinking switches — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/LICENSE — Apache License 2.0 — accessed 2026-08-27
- https://huggingface.co/api/models/Qwen/Qwen3.8-27B?blobs=true — HTTP 200; public/gated state and BF16 file list/sizes — accessed 2026-08-27
- https://huggingface.co/api/models/Qwen/Qwen3.8-27B-FP8?blobs=true — HTTP 200; official FP8 sibling file list/size — accessed 2026-08-27
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B — vLLM minimum version, launch commands, quantizations, context and MXFP4 limitation — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/models/supported_models/ — `Qwen3_5ForConditionalGeneration` support and multimodal capability — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/features/reasoning_outputs/ — exact `qwen3` reasoning parser, default thinking, structured outputs — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/features/tool_calling/ — current generic `qwen3_xml` tool-parser documentation, used to identify qwen3_coder documentation discrepancy — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vobpek/benchmark_qwen3827b_full_precision_fp8_rtx_6000/ — community RTX 6000 Pro benchmark/launch flags, treated as non-authoritative — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vsccit/qwen3827b_on_2x_3090_vllm_dflash2_218_toks_single/ — community 2×3090 DFlash2/custom-patch deployment; not a stock-vLLM recommendation — accessed 2026-08-27
- https://github.com/noonghunna/club-3090 — community local 3090/4090/5090 multi-engine recipes; no official Qwen3.8-27B profile found — accessed 2026-08-27

## Open questions

- Does the chosen `vllm/vllm-openai:latest` image expose `qwen3_coder` exactly as the official recipe states? The current generic docs page does not list it; smoke-test `--help`/one tool request before deployment.
- What exact FP8/BF16 GPU-memory and cold-start measurements apply to single H100 80GB, A100 80GB, and RTX 6000 Ada instances at this 32K cap?
- Whether the OpenAI proxy forwards image/video content and `chat_template_kwargs`; text chat compatibility is the deployment basis here.
- The Club-3090 results use nonstandard/custom patches and 3090 hardware, so whether a stock container has comparable behavior remains unverified.

## Folded Unsloth GGUF research

# unsloth/Qwen3.8-27B-GGUF

## Summary
Unsloth distributes this 27B dense Qwen3.8 vision-language model as public GGUFs. Its native context is 262,144 tokens, thinking is optional, and its template describes tool calls; deploy it with CUDA `llama-server`, whose `/v1/models`, `/v1/completions`, and `/v1/chat/completions` are OpenAI-compatible. [HF card](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF), [llama.cpp API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## Deployment recipe
Use `NVIDIA RTX 4090` with `ghcr.io/ggml-org/llama.cpp:server-cuda13`; a Qwen3.8 4090 community recipe uses this exact image. Use the YAML command verbatim: the three critical choices are `--hf-repo`/`--hf-file` to select standard 16.1 GB Q4_K_M, `--n-gpu-layers 999` for full offload, and `--ctx-size 131072`. `--alias` makes `/v1/models` report Pitwall's served name. [4090 recipe](https://www.reddit.com/r/unsloth/comments/1vobqk6/qwen3827b_serving_configs_dgx_spark_vllm_nvfp4/), [official Docker docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md), [server arguments/API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

The Hugging Face API returned HTTP 200 and `gated: false`; no HF token is needed. Container-disk and startup-time floors are unverified because no RunPod download/cache timing source was found. Poll `/v1/health` to 200, then require `/v1/models` to list `qwen3.8-27b-gguf`. [HF API](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true), [health and alias](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## Hardware and quantization
All GGUFs in the repo total 472.1 GB, but deployment fetches one main file. Optional artifacts are `MTP/mtp-Qwen3.8-27B-Q4_0.gguf` (1.37 GB), `mmproj-BF16.gguf` (0.93 GB), and `mmproj-F16.gguf` (0.928 GB). BF16 is two shards totaling 54.7 GB. [HF API file list](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true)

| GGUF file / method | Size GB | 24 GB GPU disposition |
|---|---:|---|
| BF16 (2 shards) | 54.7 | No; select 80 GB class. |
| Q4_0 / Q4_1 / Q8_0 | 16.1 / 17.5 / 29.0 | First two weight-fit; Q8_0 does not. Context unverified. |
| UD-IQ1_M / IQ1_S | 6.7 / 6.2 | Weight-fit; context/quality deployment unverified. |
| UD-IQ2_S / IQ2_XXS | 8.4 / 7.3 | Weight-fit; context deployment unverified. |
| UD-IQ3_S / IQ3_XXS | 12.0 / 10.9 | Weight-fit; context deployment unverified. |
| UD-IQ4_XS | 14.3 | Community report: 90k ctx on 5070 Ti; directional only. |
| UD-Q2_K_XL / Q3_K_XL | 9.8 / 13.1 | Weight-fit; context deployment unverified. |
| UD-Q4_K_M / Q4_K_S | 16.5 / 15.4 | Q4_K_M at Q8 K/V, 131,072 ctx is sourced on RTX 4090. |
| UD-Q4_K_XL | 17.6 | 3090 Ti report: 130k ctx at 23,817/24,564 MiB. |
| UD-Q5_K_M / Q5_K_S / Q5_K_XL | 19.8 / 18.7 / 20.9 | Bare weights fit; no sourced runtime/KV headroom. |
| UD-Q6_K / Q6_K_M / Q6_K_L / Q6_K_XL | 22.0 / 23.1 / 24.2 / 25.3 | Do not launch on 24 GB; last two weight files exceed it. |
| UD-Q8_K_L / Q8_K_XL | 28.0 / 31.5 | No full-GPU fit. |

Every name/size is from the live HF listing. The Q4_K_M result is for full offload, one slot, Flash Attention, Q8 K/V cache, and 131,072 context: it is a sourced viable floor, not a universal VRAM formula. Native BF16's 80 GB recommendation is conservative: a community observation puts BF16 at “70-something GB” resident at 256k context. [HF API](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true), [4090 config](https://www.reddit.com/r/unsloth/comments/1vobqk6/qwen3827b_serving_configs_dgx_spark_vllm_nvfp4/), [24 GB report](https://www.reddit.com/r/LocalLLaMA/comments/1vqea0n/qwen_38_27b_in_24gb_of_vram/), [BF16 observation](https://www.reddit.com/r/LocalLLaMA/comments/1vo9mj4/its_out/)

## Tool calling, reasoning, and chat template
Do not pass vLLM `--tool-call-parser` or `--reasoning-parser`: this is llama.cpp. The upstream template defaults `enable_thinking` on, accepts `reasoning_effort` `low`, `medium`, or `xhigh`, supports `preserve_thinking`, and emits `<think>`. Tools are serialized as nested XML `<tool_call><function=...><parameter=...>`. [upstream tokenizer config](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/tokenizer_config.json)

For comparison only, current vLLM documents the Qwen3 parser as handling both this XML tool syntax and `<think>` reasoning, and documents `Qwen3_5ForConditionalGeneration` for Hugging Face weights. That does not make this GGUF repository a vLLM deployment target: use llama.cpp as above. [vLLM Qwen3 parser](https://docs.vllm.ai/en/latest/api/vllm/parser/qwen3/), [vLLM Qwen3.5 model support](https://docs.vllm.ai/en/stable/api/vllm/model_executor/models/qwen3_5/)

Use `--jinja`. llama.cpp chat accepts `chat_template_kwargs` (including `enable_thinking: false`), `reasoning_effort`, `reasoning_format`, and `parse_tool_calls`; the Qwen3.8 community recipe uses `--reasoning auto --reasoning-format deepseek`. `response_format` supports JSON and JSON Schema. Vision requires adding an mmproj file; the given recipe is deliberately text-only. [llama.cpp chat API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [community recipe](https://www.reddit.com/r/unsloth/comments/1vobqk6/qwen3827b_serving_configs_dgx_spark_vllm_nvfp4/)

## Known issues and community notes
- Upstream issue [#27615](https://github.com/ggml-org/llama.cpp/issues/27615) reports Qwen3.8 tool-trigger performance degradation on build 10488 using UD-Q4_K_M and an external template; make multi-tool tests a launch gate.
- Upstream issue [#27588](https://github.com/ggml-org/llama.cpp/issues/27588) reports trailing assistant `tool_calls` can be dropped in rendered prompts, reproduced with Qwen3.8 GGUF and embedded/custom templates.
- The operator's “club 3090” project exists: [noonghunna/club-3090](https://github.com/noonghunna/club-3090) provides multi-engine consumer CUDA recipes, but its indexed catalog does not validate Qwen3.8 GGUF specifically.

## Sources
- https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true — HTTP 200, public/non-gated status, date, Apache metadata, complete file bytes — accessed 2026-08-27
- https://huggingface.co/unsloth/Qwen3.8-27B-GGUF — GGUF card and llama.cpp instructions — accessed 2026-08-27
- https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/raw/main/config.json — architecture, vision, BF16 source dtype, 262,144 context — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/tokenizer_config.json — exact template/tool/thinking behavior — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/generation_config.json — default sampling — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md — CUDA server image and GPU-layer command — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — selectors, health/models/OpenAI APIs, alias, structured output, template controls — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/api/vllm/parser/qwen3/ — current vLLM Qwen3 XML-tool/reasoning parser, considered but not used for GGUF — accessed 2026-08-27
- https://docs.vllm.ai/en/stable/api/vllm/model_executor/models/qwen3_5/ — vLLM Qwen3.5 Hugging Face-weight support, considered but not used for GGUF — accessed 2026-08-27
- https://www.reddit.com/r/unsloth/comments/1vobqk6/qwen3827b_serving_configs_dgx_spark_vllm_nvfp4/ — 4090 llama.cpp launch and context recipe — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vqea0n/qwen_38_27b_in_24gb_of_vram/ — 24 GB deployments — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/27615 — tool-trigger performance issue — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/27588 — trailing tool-call rendering issue — accessed 2026-08-27
- https://github.com/noonghunna/club-3090 — relevant community operations project — accessed 2026-08-27

## Open questions
- No primary source identified the first llama.cpp release/image digest supporting Qwen3.8 GGUF, embedded template, MTP, and vision together. I searched the model cards, llama.cpp docs/releases/issues, and Qwen repo; pin a current digest and smoke-test before paid deployment.
- No reproducible KV-cache VRAM formula or measured maximum 24 GB context exists for every quant. Only Q4_K_M at 131,072 and UD-Q4_K_XL around 130k are directly sourced; other contexts are unverified.
- Exact vision projector command/request validation and all language coverage were not verified from Qwen3.8-specific llama.cpp primary material.
- The complete LICENSE text was not separately fetched; Hugging Face metadata reports Apache-2.0.

## Unsloth GGUF catalogue provenance

# Unsloth GGUF catalogue for Pitwall

All repository existence, gating, and byte sizes below come from the linked live Hugging Face API (`blobs=true`) accessed 2026-08-27. Sizes are decimal GB totals of all shards in that quant directory; companion projectors are separate. “Largest fit” means **largest published weight set whose total is below the named VRAM**, at 32K context only where the card supports a normal llama.cpp path; otherwise it is deliberately `unverified` (weights alone do not establish KV/cache/runtime fit).

| Upstream ID | Unsloth GGUF repo / gated | variants (GB; all published main rungs) | best 24 / 48 / 80 GB | guide / sampling / companions |
|---|---|---|---|---|
| Qwen/Qwen3.8-Flash-Next | [Qwen3.8-Flash-Next-GGUF](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF) / no | UD-IQ1_S 72.2, IQ1_M 74.4, Q2_K_XL 78.9, IQ3_XXS 81.8, Q3_K_XL 89.8, IQ4_XS 93.4, Q4_K_XL 111.1 (sharded) | unverified / unverified / UD-Q2_K_XL (weight-only; PR-required) | [card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF): think 1/.95/20; instruct .7/.8/20; BF16/F16 mmproj |
| Qwen/Qwen3.8-27B | [covered separately](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF) / no | see existing dossier | UD-Q4_K_M / UD-Q6_K_M / UD-Q8_K_XL (sourced in existing dossier) | existing dossier; mmproj/MTP |
| zai-org/GLM-5.2 | [GLM-5.2-GGUF](https://huggingface.co/unsloth/GLM-5.2-GGUF) / no | IQ1_S 216, IQ1_M 228, Q2_K_XL 253, IQ3_XXS 281, IQ3_S 308, Q3 342, IQ4 365–372, Q4 436–467, Q5 527–562, Q6 625–684, Q8 819, BF16 1507 (sharded) | none / none / none | no Unsloth run guide found; all exceed 80 GB |
| zai-org/GLM-5.3-Flash | [GLM-5.3-Flash-GGUF](https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF) / no | API returned no GGUF blobs (HTTP 200) | unverified / unverified / unverified | [card](https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF): eval 1/.95; repository needs recheck |
| MiniMaxAI/MiniMax-H3 | [MiniMax-H3-GGUF](https://huggingface.co/unsloth/MiniMax-H3-GGUF) / no | denoisers Q2 6.2, UD-Q2 7.5, Q3 8.2, UD-Q3 8.9, Q4 10.6, Q5 13.0, Q6 15.5, Q8 20.0; encoder Q2 12.2/Q4 17.0 | not applicable: video/audio system, no llama-server OpenAI API | [card](https://huggingface.co/unsloth/MiniMax-H3-GGUF): sd-cli, encoder + VAE/audio-VAE required |
| MiniMaxAI/MiniMax-Music3 | none found (HF author search `MiniMax-Music3`, HTTP 200) | — | — | audio model; no Unsloth GGUF found |
| deepseek-ai/DeepSeek-V4-Flash-0731 | [DeepSeek-V4-Flash-0731-GGUF](https://huggingface.co/unsloth/DeepSeek-V4-Flash-0731-GGUF) / no | IQ1_S 82, IQ1_M 86, Q2 90–96, IQ3 104–116, Q3 128, IQ4 136, Q4 155, Q8 161 (sharded) | none / none / none (smallest >80) | [card](https://huggingface.co/unsloth/DeepSeek-V4-Flash-0731-GGUF): agent eval 1/.95 |
| deepseek-ai/DeepSeek-V4-Pro-0813 | [DeepSeek-V4-Pro-0813-GGUF](https://huggingface.co/unsloth/DeepSeek-V4-Pro-0813-GGUF) / no | UD-Q4_K_XL 849, UD-Q8_K_XL 873 (20 shards each) | none / none / none | [card](https://huggingface.co/unsloth/DeepSeek-V4-Pro-0813-GGUF): 1/.95; no Jinja template supplied |
| moonshotai/Kimi-K3 | [Kimi-K3-GGUF](https://huggingface.co/unsloth/Kimi-K3-GGUF) / no | UD-Q1 466, TQ1 508, TQ2 551, IQ1_S 594, IQ1_M 648, IQ2 711, Q2 861, Q4 1508, Q8 1561 (sharded) | none / none / none | [card](https://huggingface.co/unsloth/Kimi-K3-GGUF): fork/Studio required; 1/.95 or agent 1/1.0; mmproj |
| meta-models/Muse-Glimmer-30B | [Muse-Glimmer-30B-GGUF](https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF) / no | IQ2 10–12, IQ3 13–14, Q2 12, Q3 13, Q4 15, Q5 19–21, Q6 26, Q8 29, BF16 55 (2 shards) | Q5_K_XL / Q8_K_XL / BF16 (weight-only) | [guide](https://unsloth.ai/docs/models/muse-glimmer); mmproj and dflash companion |
| google/gemma-4-31B-it | [gemma-4-31B-it-GGUF](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF) / no | IQ2 8–10, Q2 11, Q3 13–15, Q4 16–19, Q5 21, Q6 25–27, Q8 32–35, BF16 61 (2 shards) | Q5_K_XL / Q8_K_XL / BF16 (weight-only) | [guide](https://unsloth.ai/docs/models/gemma-4); temp 1/top-p .95/top-k 64; mmproj + MTP |
| ornith-ai/Ornith-1.5-35B-A3B-GGUF | none found (HF author search exact id, HTTP 200) | already upstream GGUF | unverified / unverified / unverified | no Unsloth publication found |

`gemma-4-31B-it-qat-GGUF` is also published: only UD-Q4_K_XL (17.4 GB) plus 0.2 GB projectors/MTP, and its card supplies `-hf …:UD-Q4_K_XL --spec-type draft-mtp --spec-draft-n-max 4 -ngl 999 -fa on`. [QAT API](https://huggingface.co/api/models/unsloth/gemma-4-31B-it-qat-GGUF?blobs=true), [QAT card](https://huggingface.co/unsloth/gemma-4-31B-it-qat-GGUF)

## Open questions

- “Weight-only” selections need a paid-image smoke test at the desired context; no uniform Unsloth VRAM formula was found.
- All `none found` results used the public HF author-search API, not an exhaustive search of every external mirror.

## Unsloth GGUF catalogue provenance

# Unsloth GGUF overview

Unsloth is the publisher behind the public [Hugging Face organisation](https://huggingface.co/unsloth), the [Unsloth GitHub organisation](https://github.com/unslothai), and documentation at [docs.unsloth.ai](https://docs.unsloth.ai/).  Its GGUF repositories are derivative quantizations, rather than a separate model licence: the MiniMax card explicitly calls them “Model Derivatives” and points to the upstream licence, while the individual cards expose the upstream licence metadata. [MiniMax card](https://huggingface.co/unsloth/MiniMax-H3-GGUF), [HF API example](https://huggingface.co/api/models/unsloth/gemma-4-31B-it-GGUF?blobs=true)

## Dynamic GGUF programme

`UD-` means an Unsloth Dynamic mixed-precision rung: its MiniMax card contrasts dynamic, mixed-precision `UD-` rungs with uniform rungs, and the Dynamic 2.0 documentation describes selecting quantization by layer/tensor rather than applying one uniform bit width. Thus names such as `UD-Q4_K_XL`, `UD-IQ2_M`, and `UD-Q8_K_XL` are repository selection labels, not llama.cpp flags. Unsloth’s Dynamic 2.0 page claims superior accuracy to competing quantizations; treat that as a vendor benchmark claim, not an independently established guarantee. [MiniMax card](https://huggingface.co/unsloth/MiniMax-H3-GGUF), [Dynamic 2.0 GGUFs](https://docs.unsloth.ai/basics/unsloth-dynamic-v2.0-gguf), [Dynamic 3.0 GGUFs](https://docs.unsloth.ai/basics/dynamic-3.0-ggufs)

Pitwall must select the exact repo revision and quant label. Cards can be re-uploaded for tokenizer/chat-template or llama.cpp corrections: the Gemma 4 card says to re-download after Google’s latest chat-template and llama.cpp fixes. Use embedded templates with `--jinja`; do not carry a template from an older conversion. [Gemma card](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF)

## Serving convention

For a supported text GGUF, llama.cpp documents `llama-server -hf owner/repo:quant`, `--host`, `--port`, `--ctx-size`, `--n-gpu-layers`, `--jinja`, and `--alias`; its server exposes OpenAI-compatible `/v1/models` and `/v1/chat/completions`. The documented CUDA image family is `ghcr.io/ggml-org/llama.cpp:server-cuda`. That is the applicable Pitwall path, with `--alias` mandatory for a stable served ID. [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [llama.cpp Docker docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md)

The official cards sometimes recommend `-hf unsloth/<repo>:<quant>` directly (Gemma QAT does so, together with `-ngl 999 -fa on`). Model-specific cards, not a universal default, supply sampling and thinking guidance: Qwen Flash documents thinking `temp=1.0, top_p=.95, top_k=20` and non-thinking `temp=.7, top_p=.8, top_k=20`; Gemma documents `temp=1.0, top_p=.95, top_k=64`. Pass request sampling parameters to the OpenAI API; do not assume llama-server has one global `--min-p`/`--top-p` configuration appropriate for every model. [Gemma QAT card](https://huggingface.co/unsloth/gemma-4-31B-it-qat-GGUF), [Qwen Flash card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF), [Gemma card](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF)

Ollama and LM Studio can consume many GGUFs, but this research found no Unsloth source establishing their OpenAI-compatible endpoint behaviour for every repository. Pitwall should therefore use llama-server, whose API is documented. Image/projector (`mmproj`) and MTP/drafter files are companions, not interchangeable main model weights; a text-only `-hf` pull must be smoke-tested before promising vision or speculative decoding. [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [Gemma QAT card](https://huggingface.co/unsloth/gemma-4-31B-it-qat-GGUF)

## Sources

- https://huggingface.co/unsloth — publisher inventory — accessed 2026-08-27
- https://github.com/unslothai — publisher GitHub — accessed 2026-08-27
- https://docs.unsloth.ai/basics/unsloth-dynamic-v2.0-gguf — Dynamic programme/vendor accuracy claims — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — API and flags — accessed 2026-08-27

## Open questions

- Dynamic 2.0/3.0 does not publish a reusable per-model VRAM-at-context formula; file size is not a VRAM guarantee.
- No source found that makes Ollama/LM Studio a safer Pitwall backend than llama-server.
