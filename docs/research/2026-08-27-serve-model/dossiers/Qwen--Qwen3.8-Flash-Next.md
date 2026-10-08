---
model_id: "Qwen/Qwen3.8-Flash-Next"
vendor: "Qwen"
family: "Qwen3.8 / Qwen4-exp preview"
release_date: "2026-08-26"
license:
  name: "Qwen Community License 1.0"
  url: "https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE"
  gated: false
architecture:
  kind: "moe"
  params_total_b: 176
  params_active_b: 6
  context_length_max: 262144
  modalities: ["text", "image", "video"]
  thinking_mode: "always"
weights:
  format: "safetensors"
  dtype: "bf16"
  size_gb: 335.28
  quantized_variants:
    - repo: "Qwen/Qwen3.8-Flash-Next-FP8"
      method: "FP8"
      size_gb: 172.78
      url: "https://huggingface.co/Qwen/Qwen3.8-Flash-Next-FP8"
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-openai:qwen38-flash-next"
  engine_min_version: "vLLM 0.28.0+"
  docker_start_cmd:
    - "Qwen/Qwen3.8-Flash-Next-FP8"
    - "--served-model-name"
    - "qwen3.8-flash-next"
    - "--port"
    - "8000"
    - "--tensor-parallel-size"
    - "8"
    - "--enable-expert-parallel"
    - "--moe-backend"
    - "triton"
    - "--gpu-memory-utilization"
    - "0.85"
    - "--max-num-seqs"
    - "256"
    - "--enable-prefix-caching"
    - "--no-enable-flashinfer-autotune"
    - "--enable-auto-tool-choice"
    - "--tool-call-parser"
    - "qwen3_xml"
    - "--reasoning-parser"
    - "qwen3"
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "qwen3_xml"
  reasoning_parser: "qwen3"
  chat_template_notes: "Thinking cannot be disabled in this template/recipe. The template accepts reasoning_effort xhigh (default), medium, or low and preserve_thinking; tools serialize as <tool_call><function=...>."
hardware:
  min_vram_gb_native: 380
  min_vram_gb_quantized: 141
  recommended_gpu_classes: ["NVIDIA H200", "NVIDIA H200 NVL"]
  tensor_parallel: 8
  container_disk_gb: 220
  startup_time_estimate_min: "unverified"
capabilities:
  tool_calling: "yes; vLLM recipe uses enable-auto-tool-choice plus qwen3_xml"
  structured_outputs: "json, regex (Qwen3 parser family documentation)"
  vision: true
  languages: "unverified"
pitwall:
  capability_name: "llm.qwen3-8-flash-next"
  served_model_name: "qwen3.8-flash-next"
  example_serve_model: >-
    pitwall serve --capability llm.qwen3-8-flash-next --model Qwen/Qwen3.8-Flash-Next-FP8
    --gpu-class "NVIDIA H200" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --tensor-parallel-size --start-arg 8 --start-arg --reasoning-parser --start-arg qwen3
confidence:
  overall: "medium"
  notes: "Official vLLM recipe is explicit but its validated H200 path requires eight GPUs; no single RunPod GPU class in the allowed list is documented as sufficient. Flash-Next’s dedicated container tag must be available to the pod runtime."
accessed: "2026-08-27"
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
