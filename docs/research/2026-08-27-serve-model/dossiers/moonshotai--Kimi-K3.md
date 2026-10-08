---
model_id: "moonshotai/Kimi-K3"
vendor: "Moonshot AI"
family: "Kimi K3"
release_date: "2026-07-27"
license:
  name: "Kimi K3 License"
  url: "https://huggingface.co/moonshotai/Kimi-K3/blob/main/LICENSE"
  gated: false
architecture:
  kind: "moe"
  params_total_b: 2800
  params_active_b: unverified
  context_length_max: 1048576
  modalities: ["text", "image"]
  thinking_mode: "always"
weights:
  format: "safetensors"
  dtype: "mxfp4"
  size_gb: 1560.94
  quantized_variants: []
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-openai:kimi-k3"
  engine_min_version: "0.27.1+"
  docker_start_cmd:
    - "moonshotai/Kimi-K3"
    - "--served-model-name"
    - "kimi-k3"
    - "--port"
    - "8000"
    - "--tensor-parallel-size"
    - "16"
    - "--trust-remote-code"
    - "--load-format"
    - "fastsafetensors"
    - "--enable-prefix-caching"
    - "--enable-auto-tool-choice"
    - "--tool-call-parser"
    - "kimi_k3"
    - "--reasoning-parser"
    - "kimi_k3"
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "kimi_k3"
  reasoning_parser: "kimi_k3"
  chat_template_notes: "K3 uses a Python token-sequence renderer rather than a conventional Jinja template; preserve returned assistant reasoning_content and tool_calls verbatim in subsequent messages."
hardware:
  min_vram_gb_native: unverified
  min_vram_gb_quantized: unverified
  recommended_gpu_classes: ["NVIDIA B200"]
  tensor_parallel: 16
  container_disk_gb: 1800
  startup_time_estimate_min: unverified
capabilities:
  tool_calling: "supported with the kimi_k3 vLLM parser; production validation required"
  structured_outputs: "supported by vLLM"
  vision: true
  languages: "unverified"
pitwall:
  capability_name: "llm.kimi-k3"
  served_model_name: "kimi-k3"
  example_serve_model: >-
    pitwall serve --capability llm.kimi-k3 --model moonshotai/Kimi-K3
    --gpu-class "NVIDIA B200" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --tensor-parallel-size --start-arg 16
confidence:
  overall: "medium"
  notes: "The vLLM launch recipe is authoritative, but its documented B200 option uses 16 GPUs; exact B200 per-GPU VRAM and a single-RunPod-pod topology were not established from the consulted sources."
accessed: "2026-08-27"
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
