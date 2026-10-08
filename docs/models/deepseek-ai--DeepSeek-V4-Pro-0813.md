---
model_id: deepseek-ai/DeepSeek-V4-Pro-0813
vendor: deepseek-ai
family: DeepSeek V4
release_date: unverified
license:
  name: MIT
  url: https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 1600
  params_active_b: 49
  context_length_max: 1048576
  modalities:
  - text
  thinking_mode: optional
capabilities:
  tool_calling: native DSML tool calls via vLLM parser deepseek_v4
  structured_outputs: unverified
  vision: false
  languages: unverified
pitwall:
  capability_name: llm.deepseek-v4-pro-0813
  served_model_name: DeepSeek-V4-Pro-0813
confidence:
  overall: medium
  notes: No single listed RunPod GPU is source-validated; source recipe requires 8×H200
    DP+EP.
accessed: '2026-08-27'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:v0.25.0
  min_cuda: "12.8"
  repo: deepseek-ai/DeepSeek-V4-Pro-0813
  file: null
  format: fp8
  min_vram_gb: unverified
  context: unverified
  container_disk_gb: unverified
  startup_min: unverified
  flags:
  - --trust-remote-code
  - --kv-cache-dtype
  - fp8
  - --block-size
  - '256'
  - --data-parallel-size
  - '8'
  - --enable-expert-parallel
  - --max-model-len
  - '800000'
  - --tokenizer-mode
  - deepseek_v4
  - --tool-call-parser
  - deepseek_v4
  - --enable-auto-tool-choice
  - --reasoning-parser
  - deepseek_v4
  - --speculative-config
  - '{"method":"dspark","num_speculative_tokens":7,"draft_sample_method":"probabilistic"}'
  env: {}
  recommended_gpu_classes:
  - NVIDIA H200
  tool_call_parser: deepseek_v4
  reasoning_parser: deepseek_v4
  confidence: medium
  sources: []
---

# deepseek-ai/DeepSeek-V4-Pro-0813

## Summary

Pro-0813 is the official V4 Pro release with DSpark: a text MoE with 1.6T total/49B active parameters and 1M context. [DeepSeek model card](https://fe-static.deepseek.com/chat/transparency/deepseek-V4-model-card-EN.pdf) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813) It supports OpenAI-compatible vLLM/SGLang serving but is not a single-GPU RunPod deployment; the official recipe calls for eight H200 GPUs, DP+EP, and 800K context. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)

## Deployment recipe

Use `vllm/vllm-openai:v0.25.0` or later—the fused 0813 checkpoint requires vLLM 0.25.0. The critical command elements are eight-way DP+EP, FP8 KV cache and the 800K H200 context cap, and `--tokenizer-mode deepseek_v4` with both `deepseek_v4` parsers. The vendor base example also specifies `--trust-remote-code`, block 256, DeepGEMM MoE, FP4 indexer cache, and DSpark; hardware-specific kernel options must match the image/GPU. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)

This model's documented vLLM and SGLang routes call OpenAI-compatible `/v1/chat/completions`, so it fits Pitwall. Before traffic, `GET /v1/models` must include `DeepSeek-V4-Pro-0813`; startup duration is unverified. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)

## Hardware and quantization

The fused FP4+FP8 checkpoint is about 893 GB on disk. The recipe specifically recommends 8×H200 DP+EP and caps it to 800K because dense parameters replicate across ranks. That is the smallest source-backed RunPod configuration; none of the listed single GPU classes has a source-validated recipe, so native/quantized VRAM floors remain `unverified` instead of being guessed. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)

NVIDIA's NVFP4 re-quantization exists for Blackwell and uses the default rather than DeepGEMM MoE backend. A community GGUF upload exists, but size, quality, exact llama.cpp support, and hardware floor were not verified, so it is not recommended for Pitwall. [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro) [GGUF repo](https://huggingface.co/unsloth/DeepSeek-V4-Pro-0813-GGUF)

No source-backed 24-GB consumer deployment was found. The searched `club-3090` material applies to Flash only, not Pro.

## Tool calling, reasoning, and chat template

The release has no Jinja template and instead ships encoding helpers. `reasoning_effort` is low/high/max; Think Max needs at least 393,216 context and high/max may need up to 384K output. Use `--tokenizer-mode deepseek_v4` for this OpenAI endpoint. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813) [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)

The exact tool and reasoning parser names are `deepseek_v4`, with `--enable-auto-tool-choice`; the parser handles `<think>` and DSML calls together. Structured-output support is unverified. [vLLM parser](https://docs.vllm.ai/en/latest/api/vllm/parser/deepseek_v4/) [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)

## Known issues and community notes

- Parameter metadata conflict: DeepSeek's technical card says 1.6T total/49B active, while the Hugging Face page labels the fused release 1.7T. This dossier uses the vendor's architectural figures; the accounting difference is unverified. [DeepSeek model card](https://fe-static.deepseek.com/chat/transparency/deepseek-V4-model-card-EN.pdf) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)
- The HF card's 4×GB300 example is not transferable to Pitwall because GB300 is not an allowed class; use its H200 eight-GPU vLLM recipe. [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813) [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro)
- Community reports say the HF repo briefly returned 404 near release. Current primary page was accessible; pin revision after qualification. [LocalLLaMA report](https://www.reddit.com/r/LocalLLaMA/comments/1vndovb/unslothdeepseekv4pro0813gguf_hugging_face/) [HF card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)
- Hosted-API reports of variable/broken reasoning are not proof that the open checkpoint behaves that way and are not part of this deployment recipe. [r/DeepSeek report](https://www.reddit.com/r/DeepSeek/comments/1vn5y49/deepseeks_v4_pro_0813_official_release_last_night/)

## Sources

- https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813 — official release, public/MIT status, OpenAI snippets, template/reasoning, flags — accessed 2026-08-27
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813/raw/main/config.json — DeepSeek-V4 config and 1,048,576 positions — accessed 2026-08-27
- https://fe-static.deepseek.com/chat/transparency/deepseek-V4-model-card-EN.pdf — MoE, 1.6T/49B, text, context, MIT — accessed 2026-08-27
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Pro — vLLM 0.25.0, ~893GB checkpoint, 8×H200, parsers, NVFP4 — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/api/vllm/parser/deepseek_v4/ — DeepSeek-V4 DSML/reasoning parsing — accessed 2026-08-27
- https://huggingface.co/unsloth/DeepSeek-V4-Pro-0813-GGUF — unqualified GGUF alternative — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vndovb/unslothdeepseekv4pro0813gguf_hugging_face/ — repository availability report — accessed 2026-08-27
- https://www.reddit.com/r/DeepSeek/comments/1vn5y49/deepseeks_v4_pro_0813_official_release_last_night/ — community API-only report — accessed 2026-08-27

## Open questions

- Exact eight-H200 VRAM/KV calculation, disk headroom, and startup duration were not published; searched HF files/card, vendor card, vLLM docs/recipe, SGLang snippets, community reports.
- No primary source validated a native single-B200 or a smaller-than-eight-H200 Pro configuration.
- The vendor 1.6T figure conflicts with HF's 1.7T display for 0813; no source explains the accounting difference.
- Structured outputs and a production-qualified Pro GGUF llama.cpp command remain unverified.

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
