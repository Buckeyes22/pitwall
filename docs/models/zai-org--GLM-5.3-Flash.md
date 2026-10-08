---
model_id: zai-org/GLM-5.3-Flash
vendor: zai-org
family: GLM-5
release_date: '2026-08-25'
license:
  name: MIT
  url: https://huggingface.co/zai-org/GLM-5.3-Flash
  gated: false
architecture:
  kind: moe
  params_total_b: 321
  params_active_b: 18
  context_length_max: 1048576
  modalities:
  - text
  - image
  - video
  thinking_mode: always
capabilities:
  tool_calling: yes; auto tool choice with glm47
  structured_outputs: unverified
  vision: true
  languages: English and Chinese (HF tags); broader coverage unverified
pitwall:
  capability_name: llm.glm-5-3-flash
  served_model_name: glm-5.3-flash
confidence:
  overall: high
  notes: Official recipe supplies image, parser flags, version floor, and FP8/BF16
    VRAM minima; one 80-GB GPU is not viable.
accessed: '2026-08-27'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:glm53-flash
  min_cuda: "12.8"
  repo: zai-org/GLM-5.3-Flash
  file: null
  format: fp8
  min_vram_gb: 386
  context: 131072
  container_disk_gb: unverified
  startup_min: 60
  flags:
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
  sources: []
---

# zai-org/GLM-5.3-Flash

## Summary
GLM-5.3-Flash is a natively multimodal 321B-total/18B-active MoE with hybrid KDA linear attention and sparse MLA, native FP8 weights, MTP, and a declared one-million-token context. It offers OpenAI-compatible chat via vLLM, but remains multi-GPU: use the dedicated GLM image with TP=4, not one 80-GB GPU. [HF card](https://huggingface.co/zai-org/GLM-5.3-Flash), [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)

## Deployment recipe
Use `vllm/vllm-openai:glm53-flash`; official vLLM documentation says this dedicated image is required until public-image integration lands. The floor is vLLM 0.27.0 and FlashInfer 0.6.17+ is needed for NoPE sparse MLA. Start TP=4 with served name `glm-5.3-flash`, `--tool-call-parser glm47`, `--reasoning-parser glm45`, and `--enable-auto-tool-choice`; do not add `--trust-remote-code`, which the official command does not use. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)

For H200/Hopper, leave KV cache BF16; FP8 KV is Blackwell-only for this model. Start without MTP for the simplest paid launch. After base verification, Blackwell may add `--kv-cache-dtype fp8` and `--speculative-config '{"method":"mtp","num_speculative_tokens":5}'`. Set `VLLM_ENGINE_READY_TIMEOUT_S=3600`; HF reports `gated:false`, so no token is needed. Require `GET /v1/models` to list `glm-5.3-flash`. [recipe source](https://github.com/vllm-project/recipes/blob/main/models/zai-org/GLM-5.3-Flash.yaml), [HF API](https://huggingface.co/api/models/zai-org/GLM-5.3-Flash)

## Hardware and quantization
The default repository is native FP8: 328.3 GB of safetensors by HF metadata, described by vLLM as about 306 GiB before runtime/KV overhead. vLLM publishes a 386-GB FP8 minimum and 772-GB BF16 minimum. Every allowed single GPU tops out at 80 GB, so a one-GPU pod is not viable. Four `NVIDIA H200` GPUs (TP=4, 564 GB total) are the conservative minimum viable listed class; four B200s are also appropriate and enable FP8 KV cache. The recipe verifies H100/B200/GB200/MI355X, but does not publish an exact minimum H100-card count, making H200 safer. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)

An official BF16 sibling exists. HF links community GGUFs, but the searched Unsloth API presently exposes no GGUF file metadata; llama.cpp compatibility, multimodal projector requirements, and a working `llama-server` command are unverified. Do not put a GGUF behind Pitwall until validated. [HF card](https://huggingface.co/zai-org/GLM-5.3-Flash), [Unsloth GGUF](https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF)

[club-3090](https://github.com/noonghunna/club-3090) exists but advertises much smaller-model recipes and none for GLM-5.3-Flash; 24-GB RTX 3090/4090 cards are below the published 386-GB minimum.

## Tool calling, reasoning, and chat template
Use `glm47` for tool calls and `glm45` for reasoning with auto-tool choice. The vLLM recipe says these produce `message.tool_calls` and `message.reasoning_content`. Thinking is always enabled: the template opens `<think>` unconditionally; `reasoning_effort` accepts `low`/`high`, otherwise Max. It accepts OpenAI-style text/image/video parts and emits XML `<tool_call><arg_key>...` calls. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash), [template](https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/chat_template.jinja), [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash)

Structured-output reliability for this exact parser/model is unverified. vLLM warns that auto tools without strict constraints can produce malformed arguments. [vLLM tools](https://docs.vllm.ai/en/stable/features/tool_calling/)

## Known issues and community notes
The image requirement is material; sparse-MLA initialization errors require FlashInfer 0.6.18+. Hopper does not support FP8 KV cache for this model, so do not copy Blackwell's KV flag to H200. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)

SGLang has H100/H200/B200/B300/GB200/GB300 recipes but calls them starting points needing workload validation. Video serving needs `torchcodec` and has a 240,000-visual-token cap. [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash)

## Sources
- https://huggingface.co/zai-org/GLM-5.3-Flash — public/MIT card, multimodal claim and framework links — accessed 2026-08-27
- https://huggingface.co/api/models/zai-org/GLM-5.3-Flash?blobs=true — gating, creation date and safetensor bytes — accessed 2026-08-27
- https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/config.json — architecture and 1,048,576 context — accessed 2026-08-27
- https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/chat_template.jinja — reasoning, multimodal and tool syntax — accessed 2026-08-27
- https://recipes.vllm.ai/zai-org/GLM-5.3-Flash — image/version, flags, memory, KV caveat — accessed 2026-08-27
- https://github.com/vllm-project/recipes/blob/main/models/zai-org/GLM-5.3-Flash.yaml — readiness timeout and variant metadata — accessed 2026-08-27
- https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash — OpenAI-compatible multimodal serving/video constraints — accessed 2026-08-27
- https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF — community GGUF repository existence — accessed 2026-08-27
- https://github.com/noonghunna/club-3090 — consumer-repo scope — accessed 2026-08-27

## Open questions
- Container disk and measured cold-start time are unverified; use the official 3,600-second ready timeout and measure a real Pod.
- A per-rank H200 VRAM table for TP=4/131,072 context was not published; workload/concurrency needs a smoke test.
- No validated llama.cpp command, multimodal projector layout, or exact GGUF files/sizes was found after searching HF, llama.cpp, vLLM/SGLang, Reddit, and club-3090.

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
