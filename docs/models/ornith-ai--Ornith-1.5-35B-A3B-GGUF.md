---
model_id: ornith-ai/Ornith-1.5-35B-A3B-GGUF
vendor: ornith-ai
family: Ornith-1.5
release_date: '2026-08-18'
license:
  name: MIT
  url: https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 36
  params_active_b: 3
  context_length_max: 262144
  modalities:
  - text
  - image
  - video
  thinking_mode: optional
capabilities:
  tool_calling: yes, llama.cpp --jinja/template-native OpenAI function calling; validate
    this model template
  structured_outputs: yes, llama.cpp documents JSON-object and JSON-schema response_format
  vision: true
  languages: unverified
pitwall:
  capability_name: llm.ornith-1-5-35b-a3b-gguf
  served_model_name: Ornith-1.5-35B-A3B
confidence:
  overall: medium
  notes: GGUF route, files, and upstream vLLM recipe are sourced. Exact 32K VRAM and
    tested llama.cpp version are not.
accessed: '2026-08-27'
openai_chat: true
variants:
- id: gguf:Q4_K_M
  default: true
  engine: llama.cpp
  image: ghcr.io/ggml-org/llama.cpp:server-cuda
  min_cuda: "12.8"
  repo: ornith-ai/Ornith-1.5-35B-A3B-GGUF
  file: Ornith-1.5-35B-Q4_K_M.gguf
  format: gguf
  min_vram_gb: 24
  context: 32768
  container_disk_gb: 30
  startup_min: 30
  flags:
  - --ctx-size
  - '32768'
  - --parallel
  - '1'
  - --flash-attn
  - 'on'
  - --no-mmproj
  env: {}
  recommended_gpu_classes:
  - NVIDIA L40S
  - NVIDIA RTX 6000 Ada Generation
  - NVIDIA A40
  - NVIDIA RTX A6000
  - NVIDIA GeForce RTX 4090
  tool_call_parser: 'none (llama.cpp uses --jinja; vLLM upstream-safetensors parser:
    qwen3_xml)'
  reasoning_parser: 'none (vLLM upstream-safetensors parser: qwen3)'
  confidence: medium
  sources: []
---

# ornith-ai/Ornith-1.5-35B-A3B-GGUF

## Summary

Public MIT-labelled Ornith-1.5 35B-A3B is a 36B-total, roughly 3B-active multimodal MoE with a 262,144-token native setting. This GGUF repository belongs on CUDA llama.cpp, not vLLM. Use text-only Q4_K_M on a 48-GB card as the practical one-GPU Pitwall deployment; 24 GB is only a nominal, low-headroom weight-file floor. An upstream safetensors repository, `ornith-ai/Ornith-1.5-35B-A3B`, exists for vLLM.

## Deployment recipe

Use `ghcr.io/ggml-org/llama.cpp:server-cuda` and the YAML arguments verbatim. `-hf` fetches the chosen GGUF; `--alias` makes `GET /v1/models` return `Ornith-1.5-35B-A3B`, which must be the readiness assertion after `/v1/health` returns 200. llama.cpp documents OpenAI-compatible `/v1/models` and `/v1/chat/completions`, so this fits Pitwall's OpenAI proxy. The key launch flags are `--n-gpu-layers all`, `--ctx-size 32768`, and `--jinja`; `--parallel 1` avoids multiple KV slots. The model is public, so `HF_TOKEN` is not needed. Startup time is unverified; set readiness from an observed first download.

The primary recipe disables the 903-MB projector with `--no-mmproj`, preserving VRAM for text. Remove it only after testing vision/video; llama.cpp says multimodal is experimental. Do not initially add `--spec-type draft-mtp`: a community report alleges the release's MTP head is untrained, an unverified but material performance risk.

Alternative, not the GGUF path: vendor documents vLLM >=0.19.1 for `ornith-ai/Ornith-1.5-35B-A3B`: `--tensor-parallel-size 2 --max-model-len 262144 --gpu-memory-utilization 0.90 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser qwen3_xml --reasoning-parser qwen3 --trust-remote-code`. It recommends two 80-GB GPUs for that 262K configuration: choose two `NVIDIA H100 80GB HBM3`, `NVIDIA H200`, or `NVIDIA A100 80GB` cards.

## Hardware and quantization

Listed files: Q4_K_M 21.7 GB, Q5_K_M 25.3 GB, Q6_K 29.2 GB, Q8_0 37.8 GB, BF16 71.1 GB, plus the 903-MB BF16 projector. Thus Q4 is the only listed quant that nominally fits a 24-GB GPU; Q5+ do not from file size alone. This is not a measured VRAM claim: CUDA allocations, KV cache, and projector make 24 GB fragile, so this dossier recommends 48 GB and 32K context. The vendor calls BF16 about 70 GB and recommends 2x80 GB for 256K context; one-GPU BF16 and exact 32K VRAM remain unverified.

A community 12-GB RTX 4070 Ti report used Q4 at 32K, but its command kept 28 MoE layers on CPU (`--n-cpu-moe 28`): it proves a CPU/RAM-offload route, not a fully GPU-resident 12-GB configuration. The operator-mentioned `club-3090` repository is relevant general guidance only: its FAQ says its tooling does not serve GGUF and directs operators to manual llama.cpp.

## Tool calling, reasoning, and chat template

By default the model emits `<think>...</think>`. Vendor's vLLM safetensors recipe uses `qwen3_xml` to parse tool calls and `qwen3` to parse reasoning into `reasoning_content`. These are not llama.cpp flags.

For this GGUF server use `--jinja`; llama.cpp documents OpenAI-style function calling through it and warns a compatible chat template may be needed. Ornith's supplied template injects tools and requires XML `<tool_call><function=...><parameter=...>` output, wraps tool results in `<tool_response>`, and emits an empty think block when `enable_thinking=false`. llama.cpp documents `response_format` support for both JSON object and JSON schema. Its experimental multimodal OAI endpoint can accept `image_url`, but this primary deployment intentionally disables vision.

## Known issues and community notes

- The exact-GGUF LocalLLaMA run on a 12-GB RTX 4070 Ti used CUDA llama.cpp, 32K context, CPU MoE offload, and MTP speculation; reported performance is anecdotal rather than a RunPod sizing target.
- A LocalLLaMA post alleges the MTP head is randomly initialized. Vendor confirmation was not found, so treat it as an unverified reason to avoid speculative decoding until benchmarked.
- A CUDA llama.cpp issue for Qwen3.5-35B-A3B reports silent corruption in multi-turn tool loops with prompt caching, including stock Q4_K_M at 61,440 context. Ornith's `qwen3_5_moe` hybrid architecture makes this relevant by inference, not proof it affects Ornith. Test multi-turn tool loops and disable prompt-cache reuse if reproduced.
- Current llama.cpp docs label multimodal support experimental; start Pitwall text-only.

## Sources

- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF — public model card: MIT label, GGUF, model-size display, quant sizes, and llama.cpp command — accessed 2026-08-27
- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF/tree/main — file list: all GGUF sizes and 903-MB mmproj — accessed 2026-08-27
- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B/raw/main/config.json — architecture, 262,144 context, expert count, BF16 and image/video config — accessed 2026-08-27
- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B/blob/main/README.md — vendor vLLM/SGLang recipes, runtime minima, 2x80-GB 256K guidance, parsers and reasoning behavior — accessed 2026-08-27
- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B/raw/main/chat_template.jinja — tool, think, and multimodal Jinja behavior — accessed 2026-08-27
- https://huggingface.co/api/models/ornith-ai/Ornith-1.5-35B-A3B-GGUF — public metadata and 2026-08-18 creation timestamp — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md — CUDA server image — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — HF loading, alias/models/health/API, `--jinja` function calls, JSON schema, experimental multimodal — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vt6hwc/ornith1535ba3b_q4_running_60tks_on_4070ti/ — 12-GB GPU CPU-offload deployment report — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vtu555/if_you_are_wondering_why_ornith_15_35b_a3b_with/ — unverified MTP allegation — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/21681 — Qwen3.5 hybrid CUDA prompt-cache corruption report — accessed 2026-08-27
- https://github.com/noonghunna/club-3090/blob/master/docs/FAQ.md — club-3090 says its tooling does not serve GGUF — accessed 2026-08-27

## Open questions

- The linked Hugging Face `LICENSE` URL returned HTTP 404 on 2026-08-27 although metadata says MIT; obtain actual license text/vendor confirmation for legal review.
- Exact llama.cpp build support for this Ornith GGUF, its Jinja tool template, and `reasoning_content` extraction is unverified; model card, current server docs, and Qwen3.5 issues were searched.
- Exact fully-GPU VRAM at 32K/64K/262K and a guaranteed 24-GB Q4 configuration remain unverified; sources covered file sizes, vendor 2x80 guidance, and a 12-GB CPU-offload run.
- BF16 mmproj interoperability with Q4, stability of vision/video through Pitwall, image tag/digest, and first-download startup time are unverified.

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
