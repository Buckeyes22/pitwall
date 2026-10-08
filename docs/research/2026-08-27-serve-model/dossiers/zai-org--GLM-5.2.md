---
model_id: "zai-org/GLM-5.2"
vendor: "zai-org"
family: "GLM-5"
release_date: "2026-06-16"
license:
  name: "MIT"
  url: "https://huggingface.co/zai-org/GLM-5.2/blob/main/LICENSE"
  gated: false
architecture:
  kind: "moe"
  params_total_b: 743
  params_active_b: 39
  context_length_max: 1048576
  modalities: ["text"]
  thinking_mode: "optional"
weights:
  format: "safetensors"
  dtype: "bf16"
  size_gb: 1506.7
  quantized_variants:
    - {repo: "zai-org/GLM-5.2-FP8", method: "FP8", size_gb: 755.6, url: "https://huggingface.co/zai-org/GLM-5.2-FP8"}
    - {repo: "nvidia/GLM-5.2-NVFP4", method: "NVFP4", size_gb: 464.8, url: "https://huggingface.co/nvidia/GLM-5.2-NVFP4"}
    - {repo: "amd/GLM-5.2-MXFP4", method: "MXFP4", size_gb: unverified, url: "https://huggingface.co/amd/GLM-5.2-MXFP4"}
serving:
  recommended_engine: "vllm"
  fits_openai_proxy: true
  image: "vllm/vllm-openai:glm52"
  engine_min_version: "0.23.0"
  docker_start_cmd: ["zai-org/GLM-5.2-FP8", "--served-model-name", "glm-5.2-fp8", "--port", "8000", "--tensor-parallel-size", "8", "--max-model-len", "131072", "--kv-cache-dtype", "fp8", "--tool-call-parser", "glm47", "--reasoning-parser", "glm45", "--enable-auto-tool-choice"]
  env:
    HF_TOKEN: "not needed"
    HF_HUB_ENABLE_HF_TRANSFER: "optional"
  tool_call_parser: "glm47"
  reasoning_parser: "glm45"
  chat_template_notes: "Max thinking by default; enable_thinking=false disables it. XML <tool_call> format."
hardware:
  min_vram_gb_native: 1786
  min_vram_gb_quantized: 893
  recommended_gpu_classes: ["NVIDIA H200", "NVIDIA B200"]
  tensor_parallel: 8
  container_disk_gb: unverified
  startup_time_estimate_min: unverified
capabilities:
  tool_calling: "yes; auto tool choice with glm47"
  structured_outputs: "unverified"
  vision: false
  languages: "English and Chinese (HF tags); broader coverage unverified"
pitwall:
  capability_name: "llm.glm-5-2"
  served_model_name: "glm-5.2-fp8"
  example_serve_model: >-
    pitwall serve --capability llm.glm-5-2 --model zai-org/GLM-5.2-FP8
    --gpu-class "NVIDIA H200" --ttl-minutes 120 --rate-per-second 0.002
    --start-arg --tensor-parallel-size --start-arg 8 --start-arg --max-model-len --start-arg 131072
confidence:
  overall: "high"
  notes: "Official recipe provides exact image/flags and published VRAM minima; a single 80-GB GPU is not viable."
accessed: "2026-08-27"
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
