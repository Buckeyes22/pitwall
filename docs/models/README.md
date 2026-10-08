# Model dossiers

Each `org--model.md` is a model dossier. Its YAML front matter is the runtime source of truth;
the Markdown body retains the research and operator context. The filename replaces the first `/`
in `model_id` with `--`.

## Ownership relative to Agent Routing

Pitwall dossiers own deployability evidence: variants, serving engines and images, hardware fit,
serving flags, observed/estimated cost evidence, and licensing. Agent Routing's lightweight model
catalogue owns harness selection, authentication method, prompt delivery, effort controls, aliases,
and harness-native or subscription-only records. Neither catalogue generates or replaces the
other.

Exact overlap is defined only by byte-for-byte `modelId` equality. As of 2026-09-07 the exact
overlaps are:

- `MiniMaxAI/MiniMax-M2.7`
- `MiniMaxAI/MiniMax-M3`
- `Qwen/Qwen3.8-Flash-Next`
- `XiaomiMiMo/MiMo-V2.5`
- `XiaomiMiMo/MiMo-V2.5-Pro`
- `deepseek-ai/DeepSeek-V4-Flash-0731`
- `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp`
- `deepseek-ai/DeepSeek-V4-Pro-0813`
- `meituan-longcat/LongCat-2.0`
- `meta-models/Muse-Glimmer-30B`
- `moonshotai/Kimi-K2.6`
- `moonshotai/Kimi-K2.7-Code`
- `moonshotai/Kimi-K3`
- `tencent/Hy3`
- `tencent/Hy4-preview`
- `zai-org/GLM-5.1`
- `zai-org/GLM-5.2`
- `zai-org/GLM-5.3`
- `zai-org/GLM-5.3-Flash`

Human-reviewed near matches are an explicit, non-failing review list. It currently begins with
Agent Routing's `google/gemma-4-31B` and Pitwall's `google/gemma-4-31B-it`. Validation never strips
`-it` or another suffix and never treats approximate similarity as a CI failure. The focused
catalogue convergence tests recompute the sorted exact intersection and assert the curated pair so
an ownership review is prompted only when an intentional catalogue edit changes either set.

CLI model inputs accept both spellings: use `org/model` or the dossier/REST-style `org--model`
with `models show`, `models fit`, and `serve --model`. When the input contains no `/`, the
CLI normalizes one `--` separator to `/`.

```yaml
model_id: org/model
vendor: org
family: Model family
release_date: "2026-08-27" # or unverified
license: {name: Apache-2.0, url: https://example.test/license, gated: false}
architecture:
  kind: dense # dense | moe
  params_total_b: 7 # positive number or unverified
  params_active_b: 7 # positive number or unverified
  context_length_max: 32768 # positive integer or unverified
  modalities: [text]
  thinking_mode: optional
capabilities: {tool_calling: "yes", structured_outputs: "yes", vision: false, languages: English}
openai_chat: true
pitwall: {capability_name: llm.model, served_model_name: model}
variants:
  - id: bf16
    default: true
    engine: vllm # vllm | llama.cpp | sglang
    image: vllm/vllm-openai:v0.12.1
    repo: org/model
    file: null # required for llama.cpp and forbidden for vllm and sglang
    format: bf16 # bf16 | fp8 | int4 | awq | nvfp4 | gptq | gguf
    min_vram_gb: 24 # positive integer or unverified
    context: 32768 # positive integer or unverified
    container_disk_gb: 40 # positive integer or unverified
    startup_min: 15 # positive integer or unverified
    flags: []
    companions:
      - kind: mtp # mmproj | mtp | draft
        repo: acme/MTP
        file: mtp.safetensors
        flags: [--speculative-config, '{"method":"mtp","num_speculative_tokens":5}']
    evidence:
      kind: measured # measured | research
      gpu_class: NVIDIA L4
      observed_vram_gb: 21.5 # positive number or unverified
      observed_startup_s: 87.0 # positive number or unverified
      date: "2026-08-27"
    env: {}
    recommended_gpu_classes: [NVIDIA GeForce RTX 4090]
    tool_call_parser: null
    reasoning_parser: null
    confidence: medium # low | medium | high
    sources: [https://example.test/model]
confidence: {overall: medium, notes: research notes}
accessed: "2026-08-27"
```

`served_model_name` is the OpenAI model identifier exposed by catalogue launches. The returned
serve result's `model_id` uses it (falling back to `model_id`); clients must use that returned ID.

Every dossier has at least one variant, exactly one default variant, and unique variant IDs.
GPU hints must use canonical RunPod names. `unverified` is accepted only for the documented
positive-number fields; an unverified VRAM floor is shown as unfit. Confidence is informational:
low means limited evidence, medium means corroborated planning evidence, and high means direct
source support.

- `companions` defaults to an empty list. Each item has only `kind`, `repo`, `file`, and `flags`; unknown keys fail dossier validation.
- Copy companion `flags` token-for-token from an engine/model source listed in the variant’s `sources`. The loader validates shape only and never guesses flags or contacts Hugging Face.
- Variant `flags` must be native to their declared engine; the Docker-free flag-hygiene test enforces this boundary.
- `container_disk_gb` is the total allocation for base weights, every companion file, and operating headroom. Companion effects on VRAM belong in `min_vram_gb`; fit arithmetic never adds them a second time.
- `evidence` is optional. Use `kind: measured` only after a live observation; use `kind: research` for sourced planning evidence. `gpu_class` must be canonical, observations are positive numbers or `unverified`, and `date` is the observation/research date in `YYYY-MM-DD` form.
- `openai_chat: false` dossiers remain visible to list/detail/fit surfaces but `serve` refuses them as `422 unknown_variant`; console detail and fit screens show `not servable via the OpenAI proxy`.
- SGLang rows use `file: null` and declare no companions in this revision. Do not add MiniMax-Music3 until a pinned SGLang-Omni image exists and the catalogue row is explicitly non-chat.

Keep the Markdown research body when editing front matter. GGUF re-publications are folded into
their upstream dossier as `llama.cpp` variants; no standalone Unsloth dossiers ship. Unsupported
engines are excluded rather than coerced. Set `PITWALL_MODELS_DIR` to load an alternate dossier
directory during development.

Record a real observation with `models evidence MODEL --variant ID --gpu-class CANONICAL_NAME
--observed-vram-gb N --observed-startup-s N`. This overwrites only that variant's `evidence` YAML
mapping's values as `kind: measured`, dates it in UTC, validates the complete dossier before
writing, and leaves its Markdown body byte-for-byte unchanged. The command re-dumps normalized
YAML, so comments and formatting in front matter are not preserved; parsed non-evidence values
are unchanged. It writes to `PITWALL_MODELS_DIR` when set;
otherwise it writes to the packaged/source dossier directory. Use canonical RunPod GPU names only.

Validate the shipped catalogue with:

```bash
uv run --frozen pytest -q tests/models/test_catalogue.py -p no:randomly -k shipped
```
