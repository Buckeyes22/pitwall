# Serve-model research corpus — 2026-08-27

This dated corpus preserves research inputs for the serve-model and model-catalogue specifications. Its figures, capacities, prices, and performance statements are research numbers, not runtime measurements.

`docs/models/*.md` were converted from these dossiers by `tools/models/convert_research_dossier.py`; those model documents are the source of truth for runtime. Code never reads `docs/research/`.

## Index

| File | Contents |
| --- | --- |
| `engineering/wiring--pod-to-model.md` | Existing pod, lease, volume, and serving wiring. |
| `engineering/runpod--templates-and-hub.md` | RunPod template and Hub implementation research. |
| `engineering/runpod--gpu-catalog.md` | Dated RunPod hardware and fit research. |
| `engineering/runpod--gputypes-live.json` | 2026-08-27 GPU-type and price snapshot. |
| `engineering/engine--vllm.md` | vLLM serving-engine research. |
| `engineering/engine--sglang.md` | SGLang serving-engine research. |
| `engineering/engine--llama.cpp.md` | llama.cpp serving-engine research. |
| `engineering/engine--others.md` | Other serving-engine options. |
| `engineering/tui--serve-model-integration.md` | Textual console integration research. |
| `engineering/matrix--hardware-x-model.md` | Hardware-by-model research matrix. |
| `engineering/matrix--hardware-x-model.yaml` | Machine-readable hardware-by-model matrix. |
| `engineering/DECISIONS.md` | Research-backed engineering decisions. |
| `engineering/DESIGN-QUESTIONS.md` | Open design questions. |
| `engineering/ENGINEERING-BRIEF.md` | Consolidated engineering brief. |
| `dossiers/MiniMaxAI--MiniMax-H3.md` | MiniMax H3 dossier. |
| `dossiers/MiniMaxAI--MiniMax-Music3.md` | MiniMax Music3 dossier. |
| `dossiers/Qwen--Qwen3.8-27B.md` | Qwen3.8-27B dossier. |
| `dossiers/Qwen--Qwen3.8-Flash-Next.md` | Qwen3.8-Flash-Next dossier. |
| `dossiers/deepseek-ai--DeepSeek-V4-Flash-0731.md` | DeepSeek V4 Flash dossier. |
| `dossiers/deepseek-ai--DeepSeek-V4-Pro-0813.md` | DeepSeek V4 Pro dossier. |
| `dossiers/google--gemma-4-31B-it.md` | Gemma 4 31B instruction dossier. |
| `dossiers/meta-models--Muse-Glimmer-30B.md` | Muse Glimmer 30B dossier. |
| `dossiers/moonshotai--Kimi-K3.md` | Kimi K3 dossier. |
| `dossiers/ornith-ai--Ornith-1.5-35B-A3B-GGUF.md` | Ornith GGUF dossier. |
| `dossiers/unsloth--Qwen3.8-27B-GGUF.md` | Unsloth Qwen3.8-27B GGUF dossier. |
| `dossiers/zai-org--GLM-5.2.md` | GLM-5.2 dossier. |
| `dossiers/zai-org--GLM-5.3-Flash.md` | GLM-5.3 Flash dossier. |
| `dossiers/unsloth--OVERVIEW.md` | Unsloth publisher and GGUF overview. |
| `dossiers/unsloth--GGUF-catalog.md` | Unsloth GGUF catalogue. |
| `dossiers/unsloth--Qwen3.8-Flash-Next-GGUF.md` | Unsloth Qwen Flash GGUF research. |
| `dossiers/unsloth--Muse-Glimmer-30B-GGUF.md` | Unsloth Muse GGUF research. |
| `dossiers/unsloth--gemma-4-31B-it-GGUF.md` | Unsloth Gemma GGUF research. |
| `dossiers/BRIEF.md` | Dossier research brief. |
| `dossiers/TEMPLATE.md` | Dossier authoring template. |
| `README.md` | Corpus scope, provenance, and file index. |
