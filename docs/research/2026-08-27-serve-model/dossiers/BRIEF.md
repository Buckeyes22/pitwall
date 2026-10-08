# Model dossier research brief (Pitwall `serve-model`)

**Purpose.** Pitwall (`pitwall`) is a self-hosted RunPod GPU broker. A new `serve-model` command turns
"serve open-weight model M on GPU class G for T minutes" into a RunPod **pod** running an OpenAI-compatible server
(default image: upstream `vllm/vllm-openai`), fronted by Pitwall's `/v1/openai/<capability>/v1` proxy. Each dossier
tells the tool and its operator exactly how to configure and deploy one model: engine, image, container start
arguments, hardware floor, quantization options, parsers, gating, and known problems.

**Date of research:** 2026-08-27. Several of these models are newer than any prior knowledge you may hold —
**research everything from live sources; never fill a field from memory.** If a fact cannot be found, write
`unverified` and say what you looked for.

## Sources (in priority order)
1. The Hugging Face model card and repo files: `https://huggingface.co/<org>/<model>` — README, `config.json`
   (architecture, `max_position_embeddings`, `torch_dtype`, quantization config), `generation_config.json`,
   `tokenizer_config.json` / `chat_template.jinja` (tool-call and thinking markup), file listing (total weight size,
   safetensors vs GGUF, shards), the license file, and whether the repo is **gated**.
2. The vendor's own docs/GitHub (e.g. the model's GitHub README, vLLM/SGLang "recipes" pages, `docs.vllm.ai`
   supported-models + tool-calling + reasoning-outputs pages, llama.cpp docs for GGUF).
3. Community deployment experience: r/LocalLLaMA and related subreddits, GitHub issues/discussions on the vLLM,
   SGLang, llama.cpp, and model repos, and community repos about running models on consumer GPUs (the operator
   mentioned a GitHub repo referred to as "club 3090" — search for it and cite it if it exists and is relevant).

Cite the **exact URL** for every factual claim and note the HTTP status you got if a page was gated/404. Prefer
primary sources; when community sources disagree with the model card, record both and mark confidence.

## Hard rules
- No invention: flags, env vars, VRAM numbers, context lengths, and parser names must come from a cited page.
  Wrong vLLM flags will break a paid GPU launch.
- Use only these RunPod GPU names in `recommended_gpu_classes` (they are validated by the tool):
  `NVIDIA H100 80GB HBM3`, `NVIDIA H100 NVL`, `NVIDIA H200`, `NVIDIA H200 NVL`, `NVIDIA B200`,
  `NVIDIA A100 80GB`, `NVIDIA A100 80GB PCIe`, `NVIDIA A100 40GB`, `NVIDIA A6000`, `NVIDIA RTX A6000`,
  `NVIDIA A40`, `NVIDIA L40`, `NVIDIA L40S`, `NVIDIA L4`, `NVIDIA RTX 6000 Ada`, `NVIDIA RTX 4090`,
  `NVIDIA GeForce RTX 4090`, `NVIDIA RTX A5000`, `NVIDIA RTX A4500`, `NVIDIA RTX A4000`,
  `NVIDIA RTX 5000 Ada Generation`, `NVIDIA RTX 4000 Ada Generation`.
  If a model needs more VRAM than one of these provides, say so and give the tensor-parallel count.
- GGUF repositories are served by **llama.cpp** (`llama-server`, OpenAI-compatible), not vLLM; say which image/
  command serves them and whether they fit Pitwall's OpenAI proxy. Non-text models (music/audio/vision) must state
  whether they expose an OpenAI-compatible API at all and what the alternative serving path is.
- Do not run models, do not download weights, do not edit any repository; write only under the output directory
  below. Do not run git. Keep web fetches purposeful (roughly ≤ 25 pages per model).
- Every dossier ends with a `## Sources` list (URL + what it supported + access date) and a
  `## Open questions` list (what could not be verified).

## Output
One Markdown file per model at `<this directory>/<org>--<model>.md` (replace `/` with `--`), following
`TEMPLATE.md` exactly (same headings, same YAML keys; leave a key as `unverified` rather than deleting it).
Also produce a one-paragraph summary per model in your final reply: engine, minimum viable GPU/VRAM, the three
most important start flags, tool/reasoning parser names, gating/license, overall confidence, and the biggest open
question.
