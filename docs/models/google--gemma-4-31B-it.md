---
model_id: google/gemma-4-31B-it
vendor: Google
family: Gemma 4
release_date: '2026-04-02'
license:
  name: Apache License 2.0
  url: https://ai.google.dev/gemma/terms
  gated: false
architecture:
  kind: dense
  params_total_b: 30.7
  params_active_b: 30.7
  context_length_max: 262144
  modalities:
  - text
  - image
  - video
  thinking_mode: optional
capabilities:
  tool_calling: supported
  structured_outputs: supported by vLLM guided decoding / json_schema
  vision: true
  languages: over 140
pitwall:
  capability_name: llm.gemma-4-31b-it
  served_model_name: gemma-4-31B-it
confidence:
  overall: medium
  notes: Official HF/vLLM sources verify the model, formats, parser flags, OpenAI
    API, and full-featured TP=2 recipe. The 24-GB floor applies only to community-validated
    INT4 TP=2 deployment, not native BF16. Native minimum VRAM and an official minimum
    vLLM version remain unverified.
accessed: '2026-08-27'
openai_chat: true
variants:
- id: bf16
  default: true
  engine: vllm
  image: vllm/vllm-openai:gemma4
  min_cuda: "12.8"
  repo: google/gemma-4-31B-it
  file: null
  format: bf16
  min_vram_gb: 80
  context: 32768
  container_disk_gb: 75
  startup_min: 30
  flags:
  - --max-model-len
  - '16384'
  - --gpu-memory-utilization
  - '0.90'
  - --max-num-batched-tokens
  - '4096'
  - --enable-auto-tool-choice
  - --reasoning-parser
  - gemma4
  - --tool-call-parser
  - gemma4
  - --chat-template
  - examples/tool_chat_template_gemma4.jinja
  - --limit-mm-per-prompt
  - '{"image": 4}'
  env: {}
  recommended_gpu_classes:
  - NVIDIA H100 80GB HBM3
  - NVIDIA H200
  - NVIDIA A100-SXM4-80GB
  - NVIDIA GeForce RTX 4090
  - NVIDIA A100 80GB PCIe
  tool_call_parser: gemma4
  reasoning_parser: gemma4
  confidence: medium
  sources: []
- id: gguf:UD-Q5_K_XL
  default: false
  engine: llama.cpp
  image: ghcr.io/ggml-org/llama.cpp:server-cuda
  min_cuda: "12.8"
  repo: unsloth/gemma-4-31B-it-GGUF
  file: gemma-4-31B-it-UD-Q5_K_XL.gguf
  format: gguf
  min_vram_gb: 24
  context: unverified
  container_disk_gb: unverified
  startup_min: 30
  flags:
  - --ctx-size
  - '16384'
  - --flash-attn
  - 'on'
  companions:
  - kind: mmproj
    repo: unsloth/gemma-4-31B-it-GGUF
    file: mmproj-F16.gguf
    flags:
    - --mmproj
    - mmproj-F16.gguf
  env: {}
  recommended_gpu_classes:
  - NVIDIA H100 80GB HBM3
  - NVIDIA H200
  - NVIDIA A100-SXM4-80GB
  - NVIDIA GeForce RTX 4090
  - NVIDIA A100 80GB PCIe
  tool_call_parser: gemma4
  reasoning_parser: gemma4
  confidence: medium
  sources:
  - https://huggingface.co/api/models/unsloth/gemma-4-31B-it-GGUF?blobs=true
---

# google/gemma-4-31B-it

## Summary

Gemma 4 31B IT is Google's 30.7B-parameter dense multimodal model: it accepts text and images (and video as frames) and produces text, with a 256K text context. vLLM offers an OpenAI-compatible serving recipe with Gemma 4 tool and reasoning parsers. The native BF16 safetensors download is 62.6 GB; use a two-GPU 80-GB-class deployment for the conservative native recipe, while consumer 24-GB cards are viable only as a two-card INT4 deployment validated by club-3090.

## Deployment recipe

Use `vllm/vllm-openai:v0.27.1` with the YAML `docker_start_cmd` above on two `NVIDIA H100 80GB HBM3` GPUs; this combines vLLM's official full-featured TP=2 / 16,384-token recipe with a conservative GPU class. Make `GET /v1/models` return `gemma-4-31B-it` before proxying traffic. The three launch-critical feature flags are `--enable-auto-tool-choice`, `--tool-call-parser gemma4`, and `--reasoning-parser gemma4`; retain the template and `--limit-mm-per-prompt image=4` for tool/reasoning and bounded vision requests. HF's unauthenticated model API reports `gated:false`, and raw model files returned HTTP 200 without credentials, so this specific public repository needs neither a licence-acceptance flow nor `HF_TOKEN` (unlike older Gemma repositories that may be gated).

## Hardware and quantization

The upstream BF16 safetensors repository reports 62.6 GB total, split into 49.8 GB and 12.8 GB shards. The official vLLM example uses TP=2, `--max-model-len 16384`, and 0.90 GPU-memory utilization, but does not state a GPU type or native memory floor; that field is therefore `unverified`. The official Google QAT GGUF is 18.9 GB total (17.7-GB Q4_0 model plus 1.2-GB multimodal projector) and is served by `llama-server` / `llama serve`, which exposes an OpenAI-compatible endpoint. For Pitwall's standard vLLM path, club-3090 documents a QAT-AWQ INT4 TP=2 profile on two 24-GB RTX 3090s (the 4090 has the same 24-GB capacity); it reports 224K context at 0.95 utilization. Treat this as community evidence for the quantized floor, not a native-BF16 guarantee. A single 24-GB card OOMs in that community recipe; its reported single-card threshold is at least 32 GB.

## Tool calling, reasoning, and chat template

The vLLM parser values are exactly `gemma4` for both `--reasoning-parser` and `--tool-call-parser`; tool choice additionally requires `--enable-auto-tool-choice`. Reasoning is optional: request it with `chat_template_kwargs: {"enable_thinking": true}` (or set `--default-chat-template-kwargs '{"enable_thinking": true}'`), and the parser moves the thought channel into the API `reasoning` field. Use `examples/tool_chat_template_gemma4.jinja`; the official recipe says this is in the vLLM container. vLLM also documents `response_format: {type: json_schema}` guided decoding, but its own recipe warns that schema descriptions are not visible to the model, so state semantic requirements in the system prompt. 31B is vision-capable; cap requests with `--limit-mm-per-prompt image=4`. Do not declare audio support for this 31B model: the HF card limits native audio to E2B/E4B/12B.

## Known issues and community notes

vLLM issue #42687 reports that, on versions including 0.20.0, sub-70-GB devices can auto-select `max_num_batched_tokens=2048`, below Gemma 4's 2,496 tokens per multimodal item; it gives `--max-num-batched-tokens 4096` as the workaround, hence that flag in this recipe. The project also has a report of repetition loops, especially with JSON-schema constrained decoding; bound output tokens and test structured-output workloads before production. For GGUF vision, a llama.cpp CUDA issue reported a SIGABRT while loading the 31B `mmproj` on a 5090 with older builds, with `--no-mmproj` as a text-only workaround; therefore validate vision separately after any llama.cpp upgrade. The relevant operator repo does exist: club-3090 has Gemma 4 31B recipes; its current default is QAT-AWQ INT4 / TP=2, with MTP disabled on vLLM 0.24.0 because tools break when speculative decoding is enabled.

r/LocalLLaMA reports vary substantially by quantization, KV type, runtime, and whether vision is loaded: one Q4 GGUF user reports fitting 131K on a 24-GB GPU, while another reports a 31B Q4_K_M configuration leaves under 1 GB at 14K context. These are anecdotal llama.cpp results and do not supersede the vLLM/club-3090 TP=2 recommendation.

## Sources

- https://huggingface.co/google/gemma-4-31B-it/tree/4797d2888d4e7a1450a92a7d426967eeca6f3d7e — HF metadata, Apache-2.0 label, safetensors file list and 62.6 GB total — accessed 2026-08-27
- https://huggingface.co/api/models/google/gemma-4-31B-it — public model metadata (`gated: false`) and repository file listing (HTTP 200 without authentication) — accessed 2026-08-27
- https://huggingface.co/google/gemma-4-31B-it/raw/main/config.json — architecture (`Gemma4ForConditionalGeneration`), BF16 configs, dense text configuration, and 262,144 text positions (HTTP 200) — accessed 2026-08-27
- https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html — Gemma 4 OpenAI serving, thinking/tool parser names, chat template, and sample context flags — accessed 2026-08-27
- https://build.nvidia.com/google/gemma-4-31b-it/modelcard — 2026-04-02 release date, 30.7B parameters, modalities, 256K context, >140 languages, function-calling capability — accessed 2026-08-27
- https://huggingface.co/google/gemma-4-31B-it-qat-q4_0-gguf/tree/main — official Google QAT GGUF, 17.7-GB Q4_0 model + 1.2-GB mmproj (18.9 GB), and llama.cpp OpenAI-compatible `llama serve` command — accessed 2026-08-27
- https://github.com/noonghunna/club-3090/blob/master/models/gemma-4-31b/README.md — community Gemma 4 31B consumer-GPU recipes, QAT quant paths, TP=2 24-GB floor and single-card caveat — accessed 2026-08-27
- https://raw.githubusercontent.com/noonghunna/club-3090/master/models/gemma-4-31b/vllm/compose/dual/qat-awq-int4/base.yml — community-validated 2x 24-GB QAT-AWQ INT4 deployment, vLLM image/flags, 224K context and MTP/tool caveat — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/42687 — multimodal batch-token startup failure and `--max-num-batched-tokens 4096` workaround — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/40080 — reported repeated-generation failure with JSON-schema constrained decoding — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/21402 — reported Gemma 4 31B/26B CUDA mmproj load crash in older llama.cpp builds and text-only workaround — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1sgsl35/planning_a_local_gemma_4_build_is_a_single_rtx/ — conflicting community 24-GB GGUF/context experiences; treated as anecdotal — accessed 2026-08-27

## Open questions

- The exact native-BF16 VRAM floor at 16K context: the official TP=2 recipe does not state its hardware, so the conservative 2x80-GB recommendation is an operational choice, not an official minimum.
- An official vLLM image-tag/minimum-version guarantee. club-3090 validates v0.24.0/v0.27.1 quantized profiles, but this is not vendor compatibility policy.
- The supported/current llama.cpp Docker image tag and whether the current release has resolved the cited historical CUDA mmproj crash; not used by the primary vLLM recipe.
- No SGLang-specific Gemma 4 parser/tool/reasoning recipe was located in the searched official HF serving guidance and SGLang results; SGLang remains an alternative OpenAI endpoint, not this dossier's recommended engine.

## Folded Unsloth GGUF research

# unsloth/gemma-4-31B-it-GGUF
## Summary
Unsloth’s public GGUF conversion of Gemma 4 31B-it has both conventional and Dynamic rungs plus vision projectors. This is a practical llama.cpp text-serving candidate, but 24 GB only supports a low-context, weight-headroom-sensitive quant.
## Deployment recipe
Use the CUDA llama-server image and YAML arguments. `-hf` selects the exact UD-Q5_K_XL file, `--flash-attn on` and full GPU offload follow llama.cpp convention, and `--alias` permits Pitwall’s `/v1/models` check. llama-server documents `/v1/chat/completions`, `/v1/models`, JSON and JSON-Schema response formats. [server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
## Hardware and quantization
Q4_K_M is 18.4 GB, UD-Q5_K_XL 21.4 GB, UD-Q8_K_XL 34.8 GB and BF16 totals 61.0 GB over two shards. Accordingly Q5 is the highest 24 GB weight-size candidate, Q8 is the largest <48 GB, and BF16 is the largest <80 GB; all three require KV/runtime validation at 32K. `mmproj-{BF16,F16,F32}.gguf` are vision files, and MTP is separate. [HF API](https://huggingface.co/api/models/unsloth/gemma-4-31B-it-GGUF?blobs=true)
## Tool calling, reasoning, and chat template
Use the current embedded template: the card explicitly says an April update requires re-download for Google chat-template and llama.cpp fixes. Upstream recommended sampling is temperature 1.0, top-p .95, top-k 64. Exact native tool parser/reasoning switches are unverified. [card](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF)
## Known issues and community notes
- Do not copy an old template: the card identifies a chat-template/llama.cpp correction. [card](https://huggingface.co/unsloth/gemma-4-31B-it-GGUF)
- A separate QAT repo has a 17.4 GB UD-Q4_K_XL target and documented automatic MTP discovery with recent llama.cpp; it is a distinct model selection. [QAT card](https://huggingface.co/unsloth/gemma-4-31B-it-qat-GGUF)
## Sources
- https://huggingface.co/api/models/unsloth/gemma-4-31B-it-GGUF?blobs=true — files, bytes, public status — accessed 2026-08-27
- https://huggingface.co/unsloth/gemma-4-31B-it-GGUF — licence, template update and sampling — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — API/flags/schema responses — accessed 2026-08-27
## Open questions
- Verify 24 GB maximum context, vision projector command, and tool calls on the selected current image.

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
