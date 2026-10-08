---
model_id: moonshotai/Kimi-K3
vendor: Moonshot AI
family: Kimi K3
release_date: '2026-07-27'
license:
  name: Kimi K3 License
  url: https://huggingface.co/moonshotai/Kimi-K3/blob/main/LICENSE
  gated: false
architecture:
  kind: moe
  params_total_b: 2800
  params_active_b: unverified
  context_length_max: 1048576
  modalities:
  - text
  - image
  thinking_mode: always
capabilities:
  tool_calling: supported with the kimi_k3 vLLM parser; production validation required
  structured_outputs: supported by vLLM
  vision: true
  languages: unverified
pitwall:
  capability_name: llm.kimi-k3
  served_model_name: kimi-k3
confidence:
  overall: medium
  notes: The vLLM launch recipe is authoritative, but its documented B200 option uses
    16 GPUs; exact B200 per-GPU VRAM and a single-RunPod-pod topology were not established
    from the consulted sources.
accessed: '2026-08-27'
openai_chat: true
variants:
- id: fp8
  default: true
  engine: vllm
  image: vllm/vllm-openai:kimi-k3
  min_cuda: "13.0"
  repo: moonshotai/Kimi-K3
  file: null
  format: fp8
  min_vram_gb: unverified
  context: unverified
  container_disk_gb: 1800
  startup_min: unverified
  flags:
  - --trust-remote-code
  - --load-format
  - fastsafetensors
  - --enable-prefix-caching
  - --enable-auto-tool-choice
  - --tool-call-parser
  - kimi_k3
  - --reasoning-parser
  - kimi_k3
  env: {}
  recommended_gpu_classes:
  - NVIDIA B200
  tool_call_parser: kimi_k3
  reasoning_parser: kimi_k3
  confidence: medium
  sources: []
---

# moonshotai/Kimi-K3

## Summary

Kimi-K3 is Moonshot AI's 2.8T-parameter native-vision Mixture-of-Experts model with Kimi Delta Attention, 16 of 896 routed experts active per token, and a 1,048,576-token context window. [vLLM launch post](https://vllm.ai/blog/2026-07-27-k3) [HF config](https://huggingface.co/moonshotai/Kimi-K3/raw/main/config.json) The deployable upstream path is the special `vllm/vllm-openai:kimi-k3` CUDA 13 image; this is not a practical one-GPU Pitwall offering, because upstream documents at least 16 B200 GPUs (or 8 B300/GB300 GPUs). [vLLM recipe](https://recipes.vllm.ai/moonshotai/Kimi-K3?hardware=b300) [vLLM launch post](https://vllm.ai/blog/2026-07-27-k3)

## Deployment recipe

1. Use `vllm/vllm-openai:kimi-k3`, not the ordinary latest image: the official recipe says this image is CUDA 13-only and requires an r580+ NVIDIA driver. [vLLM recipe](https://recipes.vllm.ai/moonshotai/Kimi-K3?hardware=b300)
2. Deploy on a cluster of 16 `NVIDIA B200` GPUs with `--tensor-parallel-size 16`. This is the only documented minimum compatible with Pitwall's validated class list; upstream instead calls 8× B300/GB300 the easiest configuration and says 16× B200 is supported. [vLLM launch post](https://vllm.ai/blog/2026-07-27-k3)
3. Append the `docker_start_cmd` YAML arguments. The model ID is positional because the upstream command is `vllm serve moonshotai/Kimi-K3`; the key upstream launch flags are `--tensor-parallel-size 16`, `--trust-remote-code`, and `--load-format fastsafetensors`. K3-specific tool/reasoning output also requires `--enable-auto-tool-choice --tool-call-parser kimi_k3 --reasoning-parser kimi_k3`. [vLLM launch post](https://vllm.ai/blog/2026-07-27-k3)
4. Enable prefix caching explicitly. vLLM says it is disabled by default for K3 while the hybrid-cache implementation evolves. Set the ordinary model-cache volume large enough for the 1,560.94 GB checkpoint plus image/cache headroom (1.8 TB selected here); the exact ready-time is unverified. [vLLM launch post](https://vllm.ai/blog/2026-07-27-k3) [HF file tree API](https://huggingface.co/api/models/moonshotai/Kimi-K3/tree/main?recursive=true)
5. Require `GET /v1/models` to list `kimi-k3` before routing through Pitwall. vLLM exposes an OpenAI-compatible API for the model. [vLLM recipe](https://recipes.vllm.ai/moonshotai/Kimi-K3?hardware=b300)

## Hardware and quantization

The official checkpoint contains 96 safetensors files totaling 1,560.94 GB (decimal) according to the Hugging Face file-tree API; config declares compressed-tensors `mxfp4-pack-quantized` weights, while the project describes native MXFP4 weights and MXFP8 activations. [HF file tree API](https://huggingface.co/api/models/moonshotai/Kimi-K3/tree/main?recursive=true) [HF config](https://huggingface.co/moonshotai/Kimi-K3/raw/main/config.json) [Moonshot README](https://github.com/MoonshotAI/Kimi-K3/blob/main/README.md)

The documented floor is 16 B200 GPUs; the official vLLM recipe calls for at least 8 GB300, and the vLLM FAQ says at least one 8× B300/GB300 node is required while 16× B200 is supported. [vLLM recipe](https://recipes.vllm.ai/moonshotai/Kimi-K3?hardware=b300) [vLLM launch post](https://vllm.ai/blog/2026-07-27-k3) This dossier does not claim a native or quantized VRAM number at a particular context length because no consulted source provided that calculation. No official GGUF variant or llama.cpp serving recipe was located; this is a vLLM/SGLang deployment, not a llama.cpp deployment.

Community reports show experimental offload/streaming approaches on consumer cards, but they are not an OpenAI/vLLM production path: one LocalLLaMA report describes a 4090 setup with RAM/NVMe expert streaming, and another describes AirLLM streaming. [LocalLLaMA: offload experiment](https://www.reddit.com/r/LocalLLaMA/comments/1v9cwfz/i_got_kimik3_running/) [LocalLLaMA: AirLLM claim](https://www.reddit.com/r/LocalLLaMA/comments/1vtfzjc/airllm_recent_updates_with_qwen38_27b_kimik3_too/) Treat both as low-confidence community experience, not a substitute for the upstream multi-GPU requirement.

## Tool calling, reasoning, and chat template

Use the vLLM `kimi_k3` tool-call parser and `kimi_k3` reasoning parser. K3 serializes tool calls in XTML `tools`/`call`/`argument` channels, while the reasoning parser extracts the XTML `think` channel. [vLLM K3 tool parser](https://docs.vllm.ai/en/latest/api/vllm/tool_parsers/kimi_k3_tool_parser/) [vLLM K3 reasoning parser](https://docs.vllm.ai/en/latest/api/vllm/reasoning/kimi_k3_reasoning_parser/)

Thinking is always enabled and API requests can set `reasoning_effort` to `low`, `high`, or `max` (default `max`). For multi-turn use and tools, the vendor requires returning the full assistant message—particularly `reasoning_content` and `tool_calls`—unchanged in subsequent `messages`. [Moonshot README](https://github.com/MoonshotAI/Kimi-K3/blob/main/README.md) vLLM implements K3 prompt construction as a Python token-sequence renderer rather than a usual Jinja chat template and supports structured output through XGrammar integration. [vLLM launch post](https://vllm.ai/blog/2026-07-27-k3)

## Known issues and community notes

vLLM cautions that K3 sometimes emits a tool-call format its own parser does not recognize, producing an empty `tool_calls`; validate production schemas, retry/fallback on empties, and consider strict/structured tool calling. [vLLM launch post](https://vllm.ai/blog/2026-07-27-k3)

The K3 Docker image has complicated pre-release dependencies, including FlashInfer, so the vLLM launch post says only Docker images are usable at present. [vLLM launch post](https://vllm.ai/blog/2026-07-27-k3) SGLang also publishes a K3 cookbook, but its sizing depends on a live `--mamba-full-memory-ratio` calculation; use vLLM as the primary Pitwall recipe unless an SGLang-specific deployment has been validated. [SGLang K3 cookbook](https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Moonshotai/Kimi-K3.mdx)

## Sources

- https://huggingface.co/moonshotai/Kimi-K3 — repository identity, public model-card access and model metadata — accessed 2026-08-27
- https://huggingface.co/api/models/moonshotai/Kimi-K3/tree/main?recursive=true — 96-shard safetensors file list and sizes; fetched HTTP 200 — accessed 2026-08-27
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/config.json — architecture, 1,048,576 context, 896 experts/16 routed, native vision configuration and MXFP4 compressed-tensors configuration; fetched HTTP 200 — accessed 2026-08-27
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/generation_config.json — 1,048,576 `max_length`; fetched HTTP 200 — accessed 2026-08-27
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/tokenizer_config.json — tokenizer special tokens; fetched HTTP 200 — accessed 2026-08-27
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE — Kimi K3 License text and Model-as-a-Service revenue condition; fetched HTTP 200 — accessed 2026-08-27
- https://github.com/MoonshotAI/Kimi-K3/blob/main/README.md — native MXFP4/MXFP8 description, recommended engines, always-thinking and preserved-history requirements — accessed 2026-08-27
- https://recipes.vllm.ai/moonshotai/Kimi-K3?hardware=b300 — vLLM 0.27.1+, image, CUDA/driver prerequisite, OpenAI API, 8× GB300 hardware prerequisite — accessed 2026-08-27
- https://vllm.ai/blog/2026-07-27-k3 — official command flags, 16× B200 support, tool/reasoning/structured output, deployment limitations and tool-parser warning — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/api/vllm/tool_parsers/kimi_k3_tool_parser/ — exact `kimi_k3` XTML tool parser — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/api/vllm/reasoning/kimi_k3_reasoning_parser/ — exact `kimi_k3` reasoning parser and thinking-disabled behavior — accessed 2026-08-27
- https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/Moonshotai/Kimi-K3.mdx — SGLang K3 support and mamba-ratio sizing flag — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1v9cwfz/i_got_kimik3_running/ — community consumer-GPU offload experiment — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vtfzjc/airllm_recent_updates_with_qwen38_27b_kimik3_too/ — community streaming/offload claim — accessed 2026-08-27

## Open questions

- No consulted primary source states K3's active-parameter count including shared experts; I searched the HF config/model card, Moonshot README, vLLM recipe/blog, and SGLang cookbook.
- No consulted source provides a validated per-GPU VRAM requirement at a stated context length for native MXFP4 or any smaller official quantization; I searched the HF repo files, vLLM recipe/blog, SGLang cookbook, and LocalLLaMA.
- The documented 16× B200 minimum may require a multi-node cluster; this dossier cannot verify that Pitwall's single RunPod pod abstraction can provision that topology. I searched vLLM's K3 recipe/blog and the provided RunPod class list.
- No relevant community repository specifically known as “club 3090” was located in targeted searches for `club 3090 Kimi K3 GitHub`, `Kimi K3 3090 GitHub`, and the named issue trackers. No citation is supplied because relevance/existence could not be verified.
- HF API calls for `generation_config.json` and `tokenizer_config.json` returned an internal error in the browser fetcher, but direct purposeful HTTP fetches returned 200; the values are not used for unsupported deployment flags.

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
