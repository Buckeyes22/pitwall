# Engineering research brief: wiring Pitwall `serve-model` from pod to model

**Context.** Pitwall (the repository root, treated as read-only for this research) is a self-hosted RunPod GPU broker. A new
`serve-model` command creates or reuses a `pod_lease` provider, launches a RunPod **pod** from a template (image +
container start arguments), waits for readiness (`/health`, then `GET /v1/models` must list the served model id),
and fronts the pod with Pitwall's OpenAI-compatible proxy (`/v1/openai/<capability>/v1`). Model dossiers for 13
open-weight models already exist in `../` (same directory tree; read a few to see the shape and the assumptions
they make about images, `docker_start_cmd`, `HF_TOKEN`, `container_disk_gb`, `startup_time_estimate_min`).

**Goal of this research wave:** make the pod→model path concrete and evidence-based so a follow-up design can
decide (a) how weights get onto the pod, (b) which RunPod-provided templates/Hub listings to build on, (c) which
GPU classes satisfy which models, and (d) how each serving engine must be launched and probed.

**Date:** 2026-08-27. Several products and versions may be newer than your training data — research everything
from live sources and cite the exact URL for every claim; write `unverified` where you cannot source a fact.

## Sources (priority order)
1. Vendor docs: RunPod docs (`docs.runpod.io`: pods, templates, Hub, network volumes, GPU types/pricing, REST and
   GraphQL API references, `runpodctl`), vLLM docs (`docs.vllm.ai`, `recipes.vllm.ai`, Docker/Dockerfile), SGLang
   docs (`docs.sglang.io`, cookbooks), llama.cpp (`github.com/ggml-org/llama.cpp` — `tools/server/README.md`,
   `docs/docker.md`, `docs/build.md`), Hugging Face Hub docs (cache layout, `HF_HOME`, `HF_TOKEN`,
   `HF_HUB_ENABLE_HF_TRANSFER`, `huggingface-cli`/`hf download`, gated repos).
2. Official images: `vllm/vllm-openai` tags and entrypoint (`ENTRYPOINT ["vllm","serve"]` — the model is the
   first positional argument), `lmsysorg/sglang`, `ghcr.io/ggml-org/llama.cpp:server-cuda*`, RunPod's own
   images (`runpod/*`), and what each image ships (CUDA version floor, driver requirements, ports, cache dirs).
3. Community operations experience: r/LocalLLaMA, r/RunPod, GitHub issues, and `https://github.com/noonghunna/club-3090`.

## Hard rules
- No invention: flags, env vars, paths, ports, VRAM numbers, prices, and version floors must come from a cited page.
- RunPod GPU names must be exactly these (they are validated by Pitwall):
  `NVIDIA H100 80GB HBM3`, `NVIDIA H100 NVL`, `NVIDIA H200`, `NVIDIA H200 NVL`, `NVIDIA B200`,
  `NVIDIA A100 80GB`, `NVIDIA A100 80GB PCIe`, `NVIDIA A100 40GB`, `NVIDIA A6000`, `NVIDIA RTX A6000`,
  `NVIDIA A40`, `NVIDIA L40`, `NVIDIA L40S`, `NVIDIA L4`, `NVIDIA RTX 6000 Ada`, `NVIDIA RTX 4090`,
  `NVIDIA GeForce RTX 4090`, `NVIDIA RTX A5000`, `NVIDIA RTX A4500`, `NVIDIA RTX A4000`,
  `NVIDIA RTX 5000 Ada Generation`, `NVIDIA RTX 4000 Ada Generation`. If RunPod now offers others (e.g. RTX
  5090, B300/GB300, RTX PRO 6000), list them separately as "not yet canonical in Pitwall".
- Do not run pods, do not spend money, do not download weights, do not edit the repository, do not run git.
  You may read the repository to ground Pitwall-specific claims (cite `path:line`).
- Write your document EARLY with the core findings, then deepen it; end with `## Sources` (URL — what it
  supported — accessed 2026-08-27) and `## Open questions`.
- Final chat reply: a short summary of the decisive findings and recommendations, the file(s) written, and the
  last line `UNIT <name> done` or `UNIT <name> blocked: <reason>`.
