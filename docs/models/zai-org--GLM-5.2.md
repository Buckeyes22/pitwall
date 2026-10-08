---
model_id: zai-org/GLM-5.2
vendor: zai-org
family: GLM-5
release_date: '2026-06-16'
license:
  name: MIT
  url: https://huggingface.co/zai-org/GLM-5.2/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 743
  params_active_b: 39
  context_length_max: 1048576
  modalities:
  - text
  thinking_mode: optional
capabilities:
  tool_calling: yes; auto tool choice with glm47
  structured_outputs: unverified
  vision: false
  languages: English and Chinese (HF tags); broader coverage unverified
pitwall:
  capability_name: llm.glm-5-2
  served_model_name: glm-5.2-fp8
confidence:
  overall: high
  notes: Official recipe provides exact image/flags and published VRAM minima; a single
    80-GB GPU is not viable.
accessed: '2026-08-27'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:glm52
  min_cuda: "12.8"
  repo: zai-org/GLM-5.2-FP8
  file: null
  format: fp8
  min_vram_gb: 893
  context: 131072
  container_disk_gb: unverified
  startup_min: unverified
  flags:
  - --max-model-len
  - '131072'
  - --kv-cache-dtype
  - fp8
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
- id: nvfp4
  default: false
  engine: vllm
  image: vllm/vllm-openai:glm52
  min_cuda: "12.8"
  repo: nvidia/GLM-5.2-NVFP4
  file: null
  format: nvfp4
  min_vram_gb: 558
  context: unverified
  container_disk_gb: unverified
  startup_min: unverified
  flags:
  - --max-model-len
  - '131072'
  - --kv-cache-dtype
  - fp8
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

# zai-org/GLM-5.2

## Summary
GLM-5.2 is Z.ai's text-only 743B-total/39B-active MoE for reasoning, coding, and long-horizon agentic work, with a declared 1,048,576-token window and five-token MTP. Its BF16 repository is public/MIT but needs multi-node serving; Pitwall should serve the official native-FP8 sibling, `zai-org/GLM-5.2-FP8`, on eight GPUs. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.2), [HF card](https://huggingface.co/zai-org/GLM-5.2)

## Deployment recipe
Use `vllm/vllm-openai:glm52` (or `:glm52-cu129` on CUDA 12.x) and serve `zai-org/GLM-5.2-FP8`. The official recipe pins vLLM 0.23.0 and says current `main` is needed if combining tools with MTP. Start TP=8 with `--max-model-len 131072`, Hopper `--kv-cache-dtype fp8`, `--tool-call-parser glm47 --reasoning-parser glm45 --enable-auto-tool-choice`, and served name `glm-5.2-fp8`; it does not require `--trust-remote-code`. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.2)

Expose 8000 and wait for `GET /v1/models` to list `glm-5.2-fp8`. `HF_TOKEN` is not required: the HF API returns `gated:false`; transfer acceleration is optional. The HF card demonstrates OpenAI-compatible `/v1/chat/completions`. [HF API](https://huggingface.co/api/models/zai-org/GLM-5.2), [HF card](https://huggingface.co/zai-org/GLM-5.2/tree/main)

## Hardware and quantization
BF16 files total 1.51 TB (1,506.7 GB of safetensors by HF metadata), while vLLM publishes a 1,786-GB BF16 VRAM minimum and says BF16 needs multi-node deployment. The FP8 sibling is 755.6 GB and has a published 893-GB minimum; its standard recipe is 8x H200/H20 (141 GB each). Use eight `NVIDIA H200` GPUs; 8x B200 is the cited full-1M-context topology with FP8 E4M3 KV cache. 131,072 context is the safer H200 launch default. [HF files](https://huggingface.co/zai-org/GLM-5.2/tree/main), [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.2)

`nvidia/GLM-5.2-NVFP4` is a public 464.8-GB Blackwell ModelOpt checkpoint. Its official command uses TP=8, expert parallel, `--trust-remote-code`, `glm45`, `glm47`, auto tools, and FP8 E4M3 KV; the recipe gives it a 558-GB minimum. AMD MXFP4 is ROCm/MI355X-only. No source established a supported GGUF/llama.cpp deployment for GLM-5.2. [NVIDIA card](https://huggingface.co/nvidia/GLM-5.2-NVFP4), [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.2)

24-GB consumer cards are below even the NVFP4 published minimum. [club-3090](https://github.com/noonghunna/club-3090) exists but currently advertises smaller Qwen/Gemma/Tess recipes, not GLM-5.2; it is not deployment evidence for this model.

## Tool calling, reasoning, and chat template
Use exact parsers `glm47` (tools) and `glm45` (reasoning), plus `--enable-auto-tool-choice`. The repository template serializes tool definitions/calls as XML-style `<tool_call>`, `<arg_key>`, and `<arg_value>`. Thinking defaults to Max; `reasoning_effort=high` is the alternate documented effort, while `chat_template_kwargs: {"enable_thinking": false}` is non-thinking. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.2), [template](https://huggingface.co/zai-org/GLM-5.2/raw/main/chat_template.jinja)

Structured-output reliability is unverified. vLLM notes that without strict tool conditions it extracts tool calls from raw text, so arguments can be malformed. [vLLM tools](https://docs.vllm.ai/en/stable/features/tool_calling/)

## Known issues and community notes
FP8 performance requires DeepGEMM; use the dedicated GLM image. The official recipe says tool calling with MTP needs vLLM `main` and cites an MTP acceptance-rate fix. [vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.2)

An open ROCm issue reports an MTP deadlock on 8x MI300X and a separate TP>1 expert-parallel MTP bug. This does not directly apply to the NVIDIA H200 recipe, but it is a reason not to add MTP/expert parallel without testing. [vLLM issue #48568](https://github.com/vllm-project/vllm/issues/48568)

## Sources
- https://huggingface.co/zai-org/GLM-5.2/tree/main — public status, MIT label, file list and OpenAI example — accessed 2026-08-27
- https://huggingface.co/api/models/zai-org/GLM-5.2?blobs=true — gating, creation date, exact safetensor bytes — accessed 2026-08-27
- https://huggingface.co/zai-org/GLM-5.2/raw/main/config.json — MoE architecture and context — accessed 2026-08-27
- https://huggingface.co/zai-org/GLM-5.2/raw/main/chat_template.jinja — thinking/tool syntax — accessed 2026-08-27
- https://recipes.vllm.ai/zai-org/GLM-5.2 — vLLM image/version/flags, hardware, quantizations and caveats — accessed 2026-08-27
- https://huggingface.co/nvidia/GLM-5.2-NVFP4 — NVFP4 runtime command and Blackwell scope — accessed 2026-08-27
- https://docs.vllm.ai/en/stable/features/tool_calling/ — auto-tool requirements — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/48568 — ROCm MTP issue — accessed 2026-08-27
- https://github.com/noonghunna/club-3090 — consumer-repo scope — accessed 2026-08-27

## Open questions
- Container disk requirement and measured cold startup time are unverified; measure a Pod launch before setting a narrow timeout.
- A NVIDIA-specific per-rank VRAM calculation for TP=8/H200 at 131,072 context was not found beyond the 893-GB FP8 minimum.
- No validated structured-output or GGUF/llama.cpp recipe was found after searching HF, vLLM, llama.cpp, Reddit, and club-3090.

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
