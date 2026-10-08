---
model_id: Qwen/Qwen3.8-Flash-Next
vendor: Qwen
family: Qwen3.8 / Qwen4-exp preview
release_date: '2026-08-26'
license:
  name: Qwen Community License 1.0
  url: https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 176
  params_active_b: 6
  context_length_max: 262144
  modalities:
  - text
  - image
  - video
  thinking_mode: always
capabilities:
  tool_calling: yes; vLLM recipe uses enable-auto-tool-choice plus qwen3_xml
  structured_outputs: json, regex (Qwen3 parser family documentation)
  vision: true
  languages: unverified
pitwall:
  capability_name: llm.qwen3-8-flash-next
  served_model_name: qwen3.8-flash-next
confidence:
  overall: medium
  notes: Official vLLM recipe is explicit but its validated H200 path requires eight
    GPUs; no single RunPod GPU class in the allowed list is documented as sufficient.
    Flash-Next’s dedicated container tag must be available to the pod runtime.
accessed: '2026-08-27'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:qwen38-flash-next
  min_cuda: "12.8"
  repo: Qwen/Qwen3.8-Flash-Next-FP8
  file: null
  format: fp8
  min_vram_gb: 141
  context: 262144
  container_disk_gb: 220
  startup_min: 30
  flags:
  - --enable-expert-parallel
  - --moe-backend
  - triton
  - --gpu-memory-utilization
  - '0.85'
  - --max-num-seqs
  - '256'
  - --enable-prefix-caching
  - --no-enable-flashinfer-autotune
  - --enable-auto-tool-choice
  - --tool-call-parser
  - qwen3_xml
  - --reasoning-parser
  - qwen3
  env: {}
  recommended_gpu_classes:
  - NVIDIA H200
  - NVIDIA H200 NVL
  tool_call_parser: qwen3_xml
  reasoning_parser: qwen3
  confidence: medium
  sources: []
---

# Qwen/Qwen3.8-Flash-Next

## Summary

Qwen3.8-Flash-Next is a multimodal ultra-sparse MoE Qwen4-architecture preview: 125B main-model parameters plus 51B N-gram embedding parameters (176B total), with 6B activated per token, native 262,144-token context, and image/video inputs. The official vLLM recipe requires the dedicated `vllm/vllm-openai:qwen38-flash-next` image, so this is an eight-H200-class deployment rather than a normal one-GPU Pitwall launch. [model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/README.md) [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next)

## Deployment recipe

Use the official FP8 checkpoint and the exact H200 recipe represented in the YAML: `--tensor-parallel-size 8 --enable-expert-parallel --moe-backend triton --gpu-memory-utilization 0.85 --max-num-seqs 256 --enable-prefix-caching --no-enable-flashinfer-autotune --enable-auto-tool-choice --tool-call-parser qwen3_xml --reasoning-parser qwen3`. It is the official Hopper configuration; the recipe says ordinary TP8 is incompatible with this FP8 checkpoint, so TEP8 is required. The public Hub API returns `gated: false` (HTTP 200), hence no `HF_TOKEN` is needed. After download/load, verify `GET /v1/models` lists `qwen3.8-flash-next`; startup time is unverified because no source supplies a download-bandwidth-independent estimate. [recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next) [Hub API](https://huggingface.co/api/models/Qwen/Qwen3.8-Flash-Next?blobs=true)

## Hardware and quantization

The official recipe lists BF16 checkpoint memory as 335.28 GiB and its validated TP2 BF16 configuration as about 190 GiB per GB300 GPU (therefore the YAML native floor is 380 GB aggregate, not a claim that an allowed 180-GB B200 works). It lists FP8 as 172.78 GiB and mandates TEP8 on eight H200s; 141 GB is the physical per-H200 VRAM, with the eight-way setting carrying model and cache. The Hub file list confirms 131 safetensors shards and 335.28 GiB for BF16; the official FP8 sibling is 172.78 GiB. Reserve 220 GB local disk for FP8 weights plus image/cache headroom; this is an operational estimate, not an upstream measured requirement. 24-GB-class deployment is not supported by any source found. [recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next) [BF16 file list](https://huggingface.co/api/models/Qwen/Qwen3.8-Flash-Next?blobs=true) [FP8 file list](https://huggingface.co/api/models/Qwen/Qwen3.8-Flash-Next-FP8?blobs=true)

## Tool calling, reasoning, and chat template

Use `--tool-call-parser qwen3_xml` and `--reasoning-parser qwen3`; vLLM documents `qwen3_xml` and the `qwen3` reasoning parser family, including JSON/regex structured outputs and default-on Qwen thinking. The shipped template emits XML tool calls and has `enable_thinking` / `preserve_thinking` / `reasoning_effort`; however, the SGLang model cookbook explicitly says Flash-Next always reasons and thinking cannot be switched off, so treat `enable_thinking: false` as unsupported until vLLM-specific validation says otherwise. `reasoning_effort` accepts `xhigh`, `medium`, and `low`; `preserve_thinking` defaults true. [vLLM tool parsers](https://docs.vllm.ai/en/latest/features/tool_calling/) [vLLM reasoning outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) [template](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/chat_template.jinja) [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-Flash-Next)

## Known issues and community notes

The official recipe warns that FP8 plain TP8 is incompatible, TP1 compilation OOMs on GB300, `--max-num-seqs 256` avoids a Mamba-cache capacity error, and CPU offload/increased TP/reduced context are remedies for load OOM. The 51B N-gram embedding offload is NVIDIA-only and needs at least 51 GB host RAM plus runtime headroom; `VLLM_PLE_CPU_OFFLOAD=1` is mandatory for DEP but optional for the TP/TEP configuration above. A newly opened upstream vLLM feature request confirms that auxiliary-GPU PLE placement is not a current supported flag; do not use the proposed `--ple-offload-device` from that issue. A community report describes an RTX Pro 6000 experiment using a third-party NVFP4 checkpoint and host PLE offload; it is useful evidence that offload can work, but it does not validate the official FP8 recipe or an allowed RunPod class, so it is not the deployment recommendation. Qwen’s GitHub README currently shows `qwen3_coder` while the dedicated vLLM recipe uses `qwen3_xml`; pin the recipe value and smoke-test tools. [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next) [upstream issue](https://github.com/vllm-project/vllm/issues/53908) [community report](https://www.reddit.com/r/LocalLLM/comments/1vz20ap/qwen38flashnextnvfp4_on_single_rtx_pro_6000_120ts/)

## Sources

- https://huggingface.co/Qwen/Qwen3.8-Flash-Next — model card: release, architecture, capabilities, context, supported engines, thinking controls, license metadata — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/config.json — architecture, BF16 dtype, 262,144 native position limit, vision/video configuration — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/chat_template.jinja — XML tool wire format and thinking-template switches — accessed 2026-08-27
- https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE — Qwen Community License 1.0 terms — accessed 2026-08-27
- https://huggingface.co/api/models/Qwen/Qwen3.8-Flash-Next?blobs=true — HTTP 200; public/gated status and file list/sizes — accessed 2026-08-27
- https://huggingface.co/api/models/Qwen/Qwen3.8-Flash-Next-FP8?blobs=true — HTTP 200; official FP8 sibling file list/size — accessed 2026-08-27
- https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next — required image, vLLM 0.28.0+, validated commands, memory, hardware, parser flags, issues — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/features/tool_calling/ — `qwen3_xml` parser flag — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/features/reasoning_outputs/ — `qwen3` parser, structured-output and thinking-mode behavior — accessed 2026-08-27
- https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-Flash-Next — thinking/tool behavior cross-check — accessed 2026-08-27
- https://github.com/QwenLM/Qwen3.8-Flash-Next — vendor vLLM/SGLang commands; documented `qwen3_coder` discrepancy — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/53908 — open request shows auxiliary-GPU PLE placement is not an available supported interface — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLM/comments/1vz20ap/qwen38flashnextnvfp4_on_single_rtx_pro_6000_120ts/ — community NVFP4/host-offload report, treated as non-authoritative — accessed 2026-08-27

## Open questions

- Whether Pitwall/RunPod can provision the required eight-GPU H200 single-node group for one serve-model request; this cannot be inferred from the validated GPU-class list.
- The dedicated container tag’s availability in the RunPod registry and a measured cold-start/readiness budget.
- A vLLM-specific proof that `enable_thinking: false` works; SGLang says it does not.
- The recipe/vendor tool-parser disagreement (`qwen3_xml` versus `qwen3_coder`) needs a vLLM-image smoke test before paid tool-calling launches.

## Folded Unsloth GGUF research

# unsloth/Qwen3.8-Flash-Next-GGUF
## Summary
This is Unsloth’s Dynamic 3.0 GGUF conversion of the Qwen vision/video model. The card requires llama.cpp PR #27742 (or Unsloth Desktop), so a stock CUDA-server image is not verified despite llama-server’s OpenAI API.
## Deployment recipe
Use only after a current image contains the cited PR. The YAML command selects 78.9 GB UD-Q2_K_XL, full GPU offload, embedded Jinja, 32K context, and an alias. Poll `/v1/health`, then require `/v1/models` to list the alias. [card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF), [server API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
## Hardware and quantization
UD-IQ1_S is 72.2 GB and UD-Q2_K_XL is 78.9 GB across shards, so neither establishes a 24/48 GB launch; UD-Q2 is merely under 80 GB in download size. `mmproj-BF16.gguf` and `mmproj-F16.gguf` are vision companions. [HF API](https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF?blobs=true)
## Tool calling, reasoning, and chat template
Thinking requests use temperature 1.0, top-p .95, top-k 20; instruct/no-thinking uses .7/.8/20. The card documents `chat_template_kwargs.enable_thinking=false` and `preserve_thinking=false`; it does not establish exact llama.cpp tool/reasoning parser flags. [card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF)
## Known issues and community notes
- PR-required support is the principal launch blocker; pinning `server-cuda` without proving the PR is unsafe. [card](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF)
## Sources
- https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF?blobs=true — public status and exact file bytes — accessed 2026-08-27
- https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF — support, context, sampling and template guidance — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — server API/flags — accessed 2026-08-27
## Open questions
- Whether the current `server-cuda` tag includes PR #27742; recheck before launch.
- KV-cache headroom at 32K/80 GB and vision-projector invocation are unverified.

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
