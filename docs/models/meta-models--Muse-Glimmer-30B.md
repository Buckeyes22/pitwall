---
model_id: meta-models/Muse-Glimmer-30B
vendor: meta-models (Meta Inc.)
family: Muse Glimmer
release_date: '2026-08-09'
license:
  name: Apache-2.0
  url: https://huggingface.co/meta-models/Muse-Glimmer-30B/blob/main/LICENSE
  gated: false
architecture:
  kind: dense
  params_total_b: 29.776626688
  params_active_b: 29.776626688
  context_length_max: 131072
  modalities:
  - text
  - image
  - video
  thinking_mode: optional
capabilities:
  tool_calling: 'yes'
  structured_outputs: 'limited: current vLLM issue reports schema enforcement conflicts
    with muse_glimmer reasoning'
  vision: true
  languages: more than 100 languages claimed
pitwall:
  capability_name: llm.muse-glimmer-30b
  served_model_name: muse-glimmer-30b
confidence:
  overall: medium
  notes: Core recipe is sourced from a community cookbook and current vLLM support
    page; the cookbook explicitly says its vLLM path is not end-to-end attested.
accessed: '2026-08-27'
openai_chat: true
variants:
- id: bf16
  default: true
  engine: vllm
  image: vllm/vllm-openai:muse-glimmer
  min_cuda: "12.8"
  repo: meta-models/Muse-Glimmer-30B
  file: null
  format: bf16
  min_vram_gb: 80
  context: 32768
  container_disk_gb: 75
  startup_min: 30
  flags:
  - --enable-auto-tool-choice
  - --tool-call-parser
  - muse_glimmer
  - --reasoning-parser
  - muse_glimmer
  - --generation-config
  - auto
  env: {}
  recommended_gpu_classes:
  - NVIDIA H100 80GB HBM3
  - NVIDIA H200
  - NVIDIA A100-SXM4-80GB
  - NVIDIA GeForce RTX 4090
  - NVIDIA A100 80GB PCIe
  tool_call_parser: muse_glimmer
  reasoning_parser: muse_glimmer
  confidence: medium
  sources: []
- id: gguf:UD-Q5_K_XL
  default: false
  engine: llama.cpp
  image: ghcr.io/ggml-org/llama.cpp:server-cuda
  min_cuda: "12.8"
  repo: unsloth/Muse-Glimmer-30B-GGUF
  file: Muse-Glimmer-30B-UD-Q5_K_XL.gguf
  format: gguf
  min_vram_gb: 24
  context: unverified
  container_disk_gb: unverified
  startup_min: 30
  flags: []
  env: {}
  recommended_gpu_classes:
  - NVIDIA H100 80GB HBM3
  - NVIDIA H200
  - NVIDIA A100-SXM4-80GB
  - NVIDIA GeForce RTX 4090
  - NVIDIA A100 80GB PCIe
  tool_call_parser: muse_glimmer
  reasoning_parser: muse_glimmer
  confidence: medium
  sources:
  - https://huggingface.co/api/models/unsloth/Muse-Glimmer-30B-GGUF?blobs=true
---

# meta-models/Muse-Glimmer-30B

## Summary

Muse Glimmer-30B is Meta's public, Apache-2.0, dense multimodal (text/image/video input, text output) agentic model. The exact HF repository was confirmed public (HF API HTTP 200); use the dedicated `vllm/vllm-openai:muse-glimmer` image for its native model and parser support, with an 80-GB GPU for the BF16 checkpoint. [HF API](https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B) [vLLM supported models](https://docs.vllm.ai/en/latest/models/supported_models/)

## Deployment recipe

1. Launch `vllm/vllm-openai:muse-glimmer` with the listed `docker_start_cmd`. The official-community cookbook says the image entrypoint is `vllm serve`, this model needs no `trust_remote_code`, and ordinary `pip install vllm` was not then sufficient because support was an unmerged PR. [Cookbook](https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md)
2. Set `--enable-auto-tool-choice --tool-call-parser muse_glimmer --reasoning-parser muse_glimmer --generation-config auto`; use the explicit served ID `muse-glimmer-30b`. [Cookbook](https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md)
3. Choose `NVIDIA H100 80GB HBM3` (one GPU / TP=1). The Hugging Face release article calls one 80-GB H100 the practical BF16 inference floor; the cookbook budgets 72 GB and the model card's two BF16 weight shards total 59,553,253,376 bytes. [HF release article](https://huggingface.co/blog/muse-glimmer) [HF tree API](https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B/tree/main?recursive=false&expand=true)
4. No HF token is required for this public, ungated model. Allow at least 75 GB persistent disk for weights and cache. Startup-time evidence was not found; validate readiness with `GET /v1/models`, which must list `muse-glimmer-30b`. [HF API](https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B)

## Hardware and quantization

The base repo is BF16 safetensors in two shards, 59.553 GB/29.7766B parameters. Its `config.json` specifies 131,072 positions; its full-context KV-cache allocation is workload dependent, so the safe documented native floor is one 80-GB H100 rather than a derived VRAM claim. [config.json](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/config.json) [safetensors index](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/model.safetensors.index.json) [HF release article](https://huggingface.co/blog/muse-glimmer)

The vendor also publishes `meta-models/Muse-Glimmer-30B-GGUF`, for llama.cpp: `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` is 16.757 GB, `Muse-Glimmer-30B-KQuant-Dynamic-Q4_K_XL.gguf` is 19.654 GB, the image mmproj is 1.400 GB, and the optional DFlash drafter is 1.631 GB. The vendor calls the first an approximately 20-GB (weights plus working context) vision+drafter path for 24-GB VRAM, and the second a 23-GB/32-GB-VRAM path. Its documented OpenAI-compatible alternative is `llama-server -m Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf --mmproj mmproj-Muse-Glimmer-30B-Q4_K_M.gguf -a muse-glimmer-30B -ngl 99 -c 131072 -np 4 --host 0.0.0.0 --port 8000 --jinja --temp 1.0 --top-p 0.95 --top-k 64`, requiring llama.cpp `b10353` or newer; it therefore fits Pitwall’s chat/completions proxy, but needs a CUDA llama.cpp server image rather than the default vLLM image. (The exact published container image was not found.) [GGUF model card](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF) [GGUF tree API](https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B-GGUF/tree/main?recursive=false&expand=true)

A LocalLLaMA user independently reported Q4_K_XL plus mmproj and DFlash at 262,144 context using about 22–23 GB on an RTX 3090. Treat this as community evidence, not a launch guarantee; the 3090 is not a permitted Pitwall class, and the nearest listed consumer class is `NVIDIA RTX 4090`. [LocalLLaMA report](https://www.reddit.com/r/LocalLLaMA/comments/1vkm42m/muse_glimmer_actually_fits_on_a_single_rtx_3090/)

## Tool calling, reasoning, and chat template

The vLLM parser values are exactly `muse_glimmer` for both tool calls and reasoning. The Jinja template implements ATEM markup (`<atem:function_calls>`) and accepts optional reasoning strengths `low`, `medium`, `high`, and `xhigh` via the system prompt. Its official generation config has EOS IDs 200001 (`<|end_of_text|>`) and 200008 (`<|eot|>`); the cookbook warns not to add `<|eom|>` as a stop token because it is an end-of-message rather than an end-of-turn token. [Cookbook](https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md) [chat template](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/chat_template.jinja) [generation config](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/generation_config.json)

## Known issues and community notes

The cookbook labels its vLLM server route “unverified end to end,” despite describing the dedicated image and parsers; do not treat it as production-validated without a paid-pod smoke test. It also notes one tool call per assistant message, so multiple tool calls may arrive as consecutive assistant messages rather than one `tool_calls` array. [Cookbook](https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md)

Current vLLM documentation lists both Muse architectures as supported and says the vision-config checkpoint accepts images and video, with the assistant checkpoint served through DFlash. However, a current vLLM issue reports that structured outputs and `--reasoning-parser muse_glimmer` conflict: enabling schemas can suppress reasoning, while the default can silently fail to enforce the schema. Avoid advertising reliable JSON-schema output until a fixed release is tested. [vLLM supported models](https://docs.vllm.ai/en/latest/models/supported_models/) [vLLM issue #52594](https://github.com/vllm-project/vllm/issues/52594)

For llama.cpp, versions at or below `b10344` did not recognize the `muse-glimmer` architecture; the vendor GGUF card says `b10353` was the first supporting release. [llama.cpp issue #26858](https://github.com/ggml-org/llama.cpp/issues/26858) [GGUF model card](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF)

`noonghunna/club-3090` exists and the linked LocalLLaMA discussion points to its Muse Glimmer discussion; it is relevant consumer-GPU community context but does not validate the vLLM/Pitwall recipe. [club-3090 discussion](https://github.com/noonghunna/club-3090/discussions/976) [LocalLLaMA report](https://www.reddit.com/r/LocalLLaMA/comments/1vkm42m/muse_glimmer_actually_fits_on_a_single_rtx_3090/)

## Sources

- https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B — exact public ID, ungated state, metadata, sibling files, parameters — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B — model card: architecture, modalities, local quantization claims, license — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/config.json — architecture, BF16 dtype, 131,072 context — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/generation_config.json — generation defaults and EOS IDs — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/chat_template.jinja — ATEM/tool/reasoning template behavior — accessed 2026-08-27
- https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B/tree/main?recursive=false&expand=true — file list and exact artifact sizes — accessed 2026-08-27
- https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF — GGUF files, llama.cpp version floor, server recipe, VRAM claims, OpenAI API behavior — accessed 2026-08-27
- https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B-GGUF/tree/main?recursive=false&expand=true — exact GGUF/mmproj/drafter artifact sizes — accessed 2026-08-27
- https://huggingface.co/api/models/meta-models/Muse-Glimmer-30B-assistant — public DFlash assistant checkpoint metadata — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/models/supported_models/ — native vLLM Muse architecture, multimodal and DFlash support — accessed 2026-08-27
- https://github.com/meta-models/meta-oss-cookbook/blob/main/quickstart/README.md — dedicated vLLM image, exact parser/start flags, caveats — accessed 2026-08-27
- https://huggingface.co/blog/muse-glimmer — vLLM transformers-backend and llama.cpp/DFlash examples; 80-GB HF inference floor — accessed 2026-08-27
- https://www.reddit.com/r/LocalLLaMA/comments/1vkm42m/muse_glimmer_actually_fits_on_a_single_rtx_3090/ — reported consumer-GPU GGUF deployment — accessed 2026-08-27
- https://github.com/noonghunna/club-3090/discussions/976 — relevant operator community repository/discussion — accessed 2026-08-27
- https://github.com/vllm-project/vllm/issues/52594 — reported current reasoning/structured-output conflict — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/issues/26858 — legacy llama.cpp architecture-recognition failure — accessed 2026-08-27

## Open questions

- Exact minimum released vLLM version/tag and whether the dedicated `:muse-glimmer` image is still required; searched current vLLM supported-models page, HF release article, cookbook, and the merged-PR timeline, which do not identify a confirmed tagged wheel.
- Exact CUDA `llama-server` container image/tag carrying llama.cpp `b10353` or newer; searched the GGUF card, HF release article, llama.cpp sources, and cookbook.
- A source-backed startup-time estimate and a tested full Pitwall readiness run.
- Whether vLLM structured outputs are explicitly validated for this model; searched model card, HF release article, vLLM supported-model list, and cookbook.

## Folded Unsloth GGUF research

# unsloth/Muse-Glimmer-30B-GGUF
## Summary
Muse Glimmer is Meta’s 29.6B text/image agentic model, published by Unsloth in Dynamic GGUF rungs. Use llama-server for Pitwall’s OpenAI-compatible text API; vision needs a matching projector.
## Deployment recipe
Start the CUDA server with the YAML arguments. `-hf repo:UD-Q5_K_XL` chooses 20.6 GB weights, `--n-gpu-layers all` requests full offload, `--jinja` uses the conversion’s template, and `--alias` makes `/v1/models` stable. [llama server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
## Hardware and quantization
Published main files range from 10.0 GB UD-IQ2_XXS through 31.5 GB UD-Q8_K_XL; BF16 is two 54.8 GB shards. File sizes make Q5 the conservative 24 GB candidate, Q8 the weight-only 48 GB choice, and BF16 the weight-only 80 GB choice. `mmproj-*` and `dflash-kquant.gguf` are companions. [HF API](https://huggingface.co/api/models/unsloth/Muse-Glimmer-30B-GGUF?blobs=true)
## Tool calling, reasoning, and chat template
The upstream card claims reliable tool use and controllable effort; Unsloth links a Muse guide and says it has thinking toggles. Exact llama.cpp tool-call parsing and request fields were not verified, so smoke-test tool calls before exposing them. [card](https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF), [guide](https://unsloth.ai/docs/models/muse-glimmer)
## Known issues and community notes
- No sourced 24 GB context/KV measurement was found; the 24 GB entry is an intentionally conservative weight-size selection, not a guarantee.
## Sources
- https://huggingface.co/api/models/unsloth/Muse-Glimmer-30B-GGUF?blobs=true — files/status — accessed 2026-08-27
- https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF — model, licence and guide link — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — OpenAI endpoints/flags — accessed 2026-08-27
## Open questions
- Verify 24 GB VRAM at 32K and the exact vision/tool request format in a launch test.

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
