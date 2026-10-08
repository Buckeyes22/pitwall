# Pitwall `serve-model` hardware × model matrix

## How to read

`fits@<ctx>` means a documented or headroom-clearing one-GPU configuration at the stated context. `tight@<ctx>` means weights fit but the remaining KV/overhead margin is below the engine's documented headroom; it is not an admission recommendation. `TP<n>@<ctx>` needs *n* GPUs of that class in one pod. `no` is below the documented minimum; `unverified` means the supplied research does not establish that exact placement. The calculation is weights/TP + KV + activations/workspaces/engine overhead, constrained by vLLM VRAM × `gpu_memory_utilization`; vLLM's documented default is 0.92 and SGLang requires its startup memory report for exact capacity [runpod--gpu-catalog.md:70-76].

Tiers used: 16 GB `NVIDIA RTX A4000`; 20 GB `NVIDIA RTX A4500`, `NVIDIA RTX 4000 Ada Generation`; 24 GB `NVIDIA L4`, `NVIDIA RTX 4090`, `NVIDIA GeForce RTX 4090`, `NVIDIA RTX A5000`; 32 GB `NVIDIA RTX 5000 Ada Generation`; 40 GB `NVIDIA A100 40GB`; 48 GB `NVIDIA A6000`, `NVIDIA RTX A6000`, `NVIDIA A40`, `NVIDIA L40`, `NVIDIA L40S`, `NVIDIA RTX 6000 Ada`; 80 GB `NVIDIA H100 80GB HBM3`, `NVIDIA A100 80GB`, `NVIDIA A100 80GB PCIe`; 94 GB `NVIDIA H100 NVL`; 141 GB `NVIDIA H200`; 180 GB `NVIDIA B200` [runpod--gpu-catalog.md:25-46]. (`NVIDIA H200 NVL` is 143 GB and is not used as a named tier.)

Canonical-name drift rows (verbatim):

- `NVIDIA A100 80GB` → **not current exact ID**; `NVIDIA A100-SXM4-80GB` / A100 SXM
- `NVIDIA A100 40GB` → **not current exact ID**; `NVIDIA A100-SXM4-40GB` / A100 SXM 40GB
- `NVIDIA A6000` → **not returned**; closest current type is RTX A6000
- `NVIDIA RTX 6000 Ada` → **not current exact ID**; `NVIDIA RTX 6000 Ada Generation` / RTX 6000 Ada
- `NVIDIA RTX 4090` → **not current exact ID**; `NVIDIA GeForce RTX 4090` / RTX 4090 [runpod--gpu-catalog.md:30-40]

`disk` is container disk. Timeout is the proposed 1,800-second initial cap until download/init P95 telemetry exists; a cache-hit timeout omits the download term [wiring--pod-to-model.md:127-129].

## Matrix

| variant | engine | image | 24 | 32 | 48 | 80 | 94 | 141 | 180 | min VRAM GB | assumed context | disk GB | startup min | confidence | evidence |
|---|---|---|---|---|---|---|---|---|---|---:|---:|---:|---|---|---|
| MiniMaxAI/MiniMax-Music3 native; not OpenAI-chat (`/v1/audio/speech`) | sglang | unverified | fits@10240 | fits@10240 | fits@10240 | fits@10240 | fits@10240 | fits@10240 | fits@10240 | 24 | 10240 | 60 | unverified | medium | MiniMaxAI--MiniMax-Music3.md:23-46,69-77 |
| MiniMaxAI/MiniMax-H3 native; not OpenAI-chat (`/v1/videos`) | vllm | vllm/vllm-omni:minimax-h3 | TP2@unverified | TP2@unverified | unverified | unverified | unverified | unverified | unverified | 202 | unverified | 135 | unverified | medium | MiniMaxAI--MiniMax-H3.md:27-75,98-106 |
| Qwen/Qwen3.8-27B fp8 | vllm | vllm/vllm-openai:latest | no | no | no | fits@32768 | fits@32768 | fits@32768 | fits@32768 | 80 | 32768 | 70 | unverified | medium | Qwen--Qwen3.8-27B.md:22-24,27-62,81-91 |
| Qwen/Qwen3.8-27B gguf:UD-Q4_K_XL | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda13 | fits@131072 | unverified (largest weight-only is UD-Q8_K_XL) | unverified (largest weight-only is BF16) | unverified | unverified | unverified | unverified | 24 | 131072 | unverified | unverified | medium | unsloth--Qwen3.8-27B-GGUF.md:34-45,47-91,122-140 |
| Qwen/Qwen3.8-Flash-Next fp8 | vllm | vllm/vllm-openai:qwen38-flash-next | no | no | no | no | no | TP8@262144 | TP8@262144 | 141 | 262144 | 220 | unverified | medium | Qwen--Qwen3.8-Flash-Next.md:22-24,27-65,81-95 |
| deepseek-ai/DeepSeek-V4-Flash-0731 fp4+fp8 | vllm | vllm/vllm-openai:v0.25.0 | unverified | unverified | unverified | unverified | unverified | unverified | unverified | unverified | unverified | unverified | unverified | medium | deepseek-ai--DeepSeek-V4-Flash-0731.md:18-29,60-77 |
| deepseek-ai/DeepSeek-V4-Pro-0813 fp8 | vllm | vllm/vllm-openai:v0.25.0 | unverified | unverified | unverified | unverified | unverified | unverified | unverified | unverified | unverified | unverified | unverified | medium | deepseek-ai--DeepSeek-V4-Pro-0813.md:18-28,61-78 |
| google/gemma-4-31B-it native | vllm | vllm/vllm-openai:gemma4 | no | no | unverified | fits@32768 | fits@32768 | fits@32768 | fits@32768 | 80 | 32768 | 75 | unverified | medium | google--gemma-4-31B-it.md:18-62 |
| google/gemma-4-31B-it gguf:UD-Q5_K_XL | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda | unverified | unverified (largest weight-only UD-Q8_K_XL) | unverified (BF16 weight-only) | unverified | unverified | unverified | unverified | 24 | unverified | unverified | unverified | medium | unsloth--gemma-4-31B-it-GGUF.md:23-36,41-56; unsloth--GGUF-catalog.md:17,22-25 |
| meta-models/Muse-Glimmer-30B native | vllm | vllm/vllm-openai:muse-glimmer | no | no | unverified | fits@32768 | fits@32768 | fits@32768 | fits@32768 | 80 | 32768 | 75 | unverified | medium | meta-models--Muse-Glimmer-30B.md:20-61 |
| meta-models/Muse-Glimmer-30B gguf:UD-Q5_K_XL | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda | unverified | unverified (largest weight-only UD-Q8_K_XL) | unverified (BF16 weight-only) | unverified | unverified | unverified | unverified | 24 | unverified | unverified | unverified | medium | unsloth--Muse-Glimmer-30B-GGUF.md:12-36,41-50; unsloth--GGUF-catalog.md:16,22-25 |
| moonshotai/Kimi-K3 fp8 | vllm | vllm/vllm-openai:kimi-k3 | no | no | no | no | no | no | TP16@unverified | unverified | unverified | 1800 | unverified | medium | moonshotai--Kimi-K3.md:20-26,51-70,83-91 |
| ornith-ai/Ornith-1.5-35B-A3B-GGUF gguf:Q4_K_M | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda | tight@32768 | unverified | fits@32768 | fits@32768 | fits@32768 | fits@32768 | fits@32768 | 24 | 32768 | 30 | unverified | medium | ornith-ai--Ornith-1.5-35B-A3B-GGUF.md:20-61,81-108 |
| zai-org/GLM-5.2 fp8 | vllm | vllm/vllm-openai:glm52 | no | no | no | no | no | TP8@131072 | TP8@1048576 | 893 | 131072 | unverified | unverified | high | zai-org--GLM-5.2.md:22-43,65-75 |
| zai-org/GLM-5.2 nvfp4 | vllm | vllm/vllm-openai:glm52 | no | no | no | no | no | no | TP8@unverified | 558 | unverified | unverified | unverified | high | zai-org--GLM-5.2.md:23,72-75 |
| zai-org/GLM-5.3-Flash fp8 | vllm | vllm/vllm-openai:glm53-flash | no | no | no | no | no | TP4@131072 | TP4@131072 | 386 | 131072 | unverified | unverified | high | zai-org--GLM-5.3-Flash.md:20-42,64-74 |

## Not single-GPU on RunPod

- Qwen/Qwen3.8-Flash-Next: documented FP8 launch is TP8 on `NVIDIA H200`/`NVIDIA H200 NVL`; its 141-GB quantized floor is not a single-GPU recipe. Out of scope for `serve-model` v1 [Qwen--Qwen3.8-Flash-Next.md:60-65,81-95].
- GLM-5.2: FP8 is TP8 H200 at 131,072 context (or 8×B200 for 1M); NVFP4's 558-GB minimum is also multi-GPU. Out of scope for `serve-model` v1 [zai-org--GLM-5.2.md:65-77].
- GLM-5.3-Flash: TP4 H200/B200; 386-GB published FP8 minimum. Out of scope for `serve-model` v1 [zai-org--GLM-5.3-Flash.md:64-76].
- Kimi-K3: documented TP16 B200, and its disk requirement is 1,800 GB. Out of scope for `serve-model` v1 [moonshotai--Kimi-K3.md:51-56,83-91].

## Newer GPUs (not yet canonical)

All cells are **derived**, not verified: 5090 is 32 GB, RTX PRO 6000 Blackwell Server Edition 96 GB, and B300 SXM6 AC 288 GB [runpod--gpu-catalog.md:54-59]. Derived arithmetic applies the 0.92 vLLM usable-memory limit and the documented weights+KV+overhead formula [runpod--gpu-catalog.md:70-76].

| documented variant | RTX 5090 32 | RTX PRO 6000 96 | B300 288 |
|---|---|---|---|
| Qwen3.8-27B BF16 (51.75 GB) | no (derived) | fits@32768 (derived) | fits@32768 (derived) |
| Gemma-4-31B BF16 | no (derived) | fits@32768 (derived) | fits@32768 (derived) |
| Muse-Glimmer-30B BF16 (59.55 GB) | no (derived) | fits@32768 (derived) | fits@32768 (derived) |
| Qwen3.8-Flash-Next FP8 (172.78 GB) | no (derived) | no (derived) | tight@262144 (derived) |

## Cheapest fit

Snapshot 2026-08-27, refresh at launch. Secure-cloud price is per GPU-hour.

| model best single-GPU variant | cheapest canonical `fits` class | snapshot $/hr |
|---|---|---:|
| MiniMax-Music3 native | NVIDIA RTX 4090 | 0.74 |
| Qwen3.8-27B gguf:UD-Q4_K_XL | NVIDIA RTX 4090 | 0.74 |
| Ornith GGUF Q4_K_M | NVIDIA RTX A6000 (48 GB) | 0.53 |

No defensible cheap single-GPU result exists for H3, both DeepSeeks, Kimi-K3, either GLM, Flash-Next, Gemma GGUF, or Muse GGUF because their larger-tier GGUF assertions are weight-only [runpod--gpu-catalog.md:34,40,45; unsloth--GGUF-catalog.md:16-17,22-25; matrix evidence above].

## Sources

- Supplied model dossiers under `../dossiers/` — model image, VRAM, TP, disk, context, confidence, and documented variants; accessed 2026-08-27.
- [runpod--gpu-catalog.md](runpod--gpu-catalog.md) — canonical names, VRAM, price snapshot, newer GPU inventory, drift, and headroom inputs; accessed 2026-08-27.
- [engine--vllm.md](engine--vllm.md), [engine--sglang.md](engine--sglang.md), [engine--llama.cpp.md](engine--llama.cpp.md), [engine--others.md](engine--others.md) — engine image/headroom/readiness rules; accessed 2026-08-27.
- [wiring--pod-to-model.md](wiring--pod-to-model.md) — disk and readiness heuristic; accessed 2026-08-27.

## Open questions

- The Qwen 27B dossier gives 80 GB for FP8 while the Unsloth GGUF catalog lists a 31.5-GB UD-Q8_K_XL file; these are different formats and the latter does not prove a fully-GPU service. The directly sourced 24-GB configuration is Q4_K_M at 131,072; larger-weight selections remain unverified [Qwen--Qwen3.8-27B.md:57-62; unsloth--Qwen3.8-27B-GGUF.md:34,44-45,122-140].
- `NVIDIA RTX 5000 Ada Generation` has no secure-cloud lane in the snapshot, so its numeric price cannot be used for launch selection [runpod--gpu-catalog.md:45].
- Exact cold-start time, cache-hit time, first-download bandwidth, and per-context KV/concurrency are not measured for most rows; use 1,800 s initially and replace it with the proposed P95 formula after telemetry [wiring--pod-to-model.md:127-129].
- The two DeepSeek dossiers identify engine/images but leave VRAM, TP, disk, and context unverified; they intentionally remain unverified here.
