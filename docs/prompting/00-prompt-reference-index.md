# Prompt Engineering Reference Collection — Index

A set of nineteen standalone, first-party-grounded model, transport, and prompt-engineering reference documents. Each is self-contained; this index records what they are, how they were built, and the cross-cutting caveats that apply to all of them.

| Model family | Document |
|---|---|
| OpenAI (Codex / GPT) | `docs/prompting/openai-codex-gpt-prompting-reference.md` |
| Anthropic (Claude Code transport) | `docs/prompting/anthropic-claude-code-prompting-reference.md` |
| Anthropic (Claude Sonnet 5.5; Sonnet 5 system-card evidence) | `docs/prompting/anthropic-claude-sonnet-5-prompting-reference.md` |
| Anthropic (Claude Opus 5.5; Opus 4.8 system-card evidence) | `docs/prompting/anthropic-claude-opus-4.8-prompting-reference.md` |
| Anthropic (Claude Fable 5.1; Fable 5 system-card evidence) | `docs/prompting/anthropic-claude-fable-5-prompting-reference.md` |
| Anthropic (Claude Haiku 5.5; vendor documentation) | `docs/prompting/anthropic-claude-haiku-5-prompting-reference.md` |
| xAI (Grok 4.7 / Grok Build) | `docs/prompting/xai-grok-prompting-reference.md` |
| GLM (Zhipu AI / Z.ai) | `docs/prompting/glm-zhipu-prompting-reference.md` |
| Kimi (Moonshot AI) | `docs/prompting/kimi-moonshot-prompting-reference.md` |
| Qwen (Alibaba) | `docs/prompting/qwen-alibaba-prompting-reference.md` |
| MiniMax | `docs/prompting/minimax-prompting-reference.md` |
| Meta (Muse Glimmer) | `docs/prompting/meta-muse-glimmer-prompting-reference.md` |
| Meta (Muse Code / Muse Spark) | `docs/prompting/meta-muse-code-prompting-reference.md` |
| DeepSeek V4 | `docs/prompting/deepseek-prompting-reference.md` |
| Meituan LongCat | `docs/prompting/meituan-longcat-prompting-reference.md` |
| Xiaomi MiMo | `docs/prompting/xiaomi-mimo-prompting-reference.md` |
| Tencent Hy (Hunyuan) | `docs/prompting/tencent-hy-prompting-reference.md` |
| Google Gemma 4 | `docs/prompting/google-gemma-prompting-reference.md` |
| Google Gemini / Antigravity | `docs/prompting/google-gemini-prompting-reference.md` |

Pi, Hermes Agent, Cline CLI, goose, and DeepSeek Harness (`dsh`) are harness-only, model-agnostic routes: they deliberately have no model-specific prompting reference. Select the canonical reference for the model behind their configured endpoint instead.

---

## Methodology

"Official" was scoped to **first-party content only**: vendor developer docs and API platforms, the vendor's own GitHub org, and HuggingFace model cards published by the vendor's org account. Third-party blogs, aggregators, and community guides were excluded except where they pointed to a first-party source, in which case the first-party source was used. Where a document includes a non-first-party claim (e.g., a widely-reported model behavior not in the vendor's docs, or an external reference recommendation), it is **explicitly flagged as non-official** in that document.

For the two vendors whose English/international docs were ambiguous (Z.ai and MiniMax), the documentation **indexes were pulled directly** rather than inferring coverage from search results, to confirm whether a dedicated prompting page actually exists. Content was extracted from live official pages where the docs were machine-readable; the xAI reference uses only `docs.x.ai` pages for Grok 4.5 and Grok Build. The three version-specific Claude references cite Anthropic's official hosted system cards; those cards are evaluation reports, so each reference labels its operational prompting advice as evidence-derived guidance. The vendor publications are linked, not copied into this repository.

Compiled June–July 2026. Every document carries its own source list; prefer those links over this summary.

---

## Coverage at a glance

The single most useful cross-vendor finding: **a dedicated text-prompting guide is not a given.** Some vendors publish broad prompting guides, while xAI's relevant first-party guidance is organized around the Grok 4.5 model and Grok Build harness rather than a standalone general prompting page.

| Vendor | Dedicated text-prompt guide? | Where | Language | Notes |
|---|---|---|---|---|
| OpenAI | Yes, extensive (multiple tracks) | `developers.openai.com` | English | Separate GPT and Codex tracks; version-specific guidance |
| Anthropic | CLI operational guidance plus model system cards | `code.claude.com` and `anthropic.com/system-cards` | English | The transport reference covers print mode, model/effort, permissions, limits, and project discovery; separate Sonnet 5, Opus 4.8, and Fable 5 references derive routing guidance from their officially hosted cards, and the Haiku 5.5 reference derives it from the vendor model and prompting pages |
| xAI | No standalone general guide | `docs.x.ai` | English | Grok 4.7 model, Grok Build CLI, and headless-operation docs provide the route-specific facts |
| Google Gemini | Yes, API and model guidance | `ai.google.dev` | English | Antigravity CLI adds the active agentic harness route |
| Qwen | Yes, example-heavy | Alibaba Cloud Model Studio | English (+ Chinese) | Built around a named 6-element framework |
| Kimi | Yes | `platform.moonshot.ai` / `platform.kimi.ai` | English | Mirrors OpenAI's taxonomy; strong agent/tool-specific notes |
| GLM | Yes, but **Chinese platform only** | `docs.bigmodel.cn` | Chinese | International `docs.z.ai` has **no** standalone prompt page |
| MiniMax | **No** (text); guides exist for speech + image/video | model cards + API docs | — | Text prompting must be assembled from cards; Anthropic-API-compatible |

---

## Cross-cutting caveats

**Domain churn from rebrands is a live maintenance hazard.** Zhipu now presents internationally as **Z.ai** (`z.ai` / `docs.z.ai`) while retaining `bigmodel.cn` domestically; Moonshot's platform answers at both `moonshot.ai` and `kimi.ai` with `kimi.com` for China; MiniMax moved its English platform from `minimaxi.com` to `minimax.io` (the old domain is now the China site). Several `minimaxi.com` English paths now 404. If these references are loaded into a knowledge base or RAG pipeline, store the **`llms.txt` index URL** for each vendor that publishes one (OpenAI, Z.ai, and MiniMax all do) rather than deep page links — the indexes survive page reshuffling better.

**For the three Chinese labs, model cards beat prose guides for model-specific tuning.** The richest model-specific knobs — thinking-mode activation and syntax, preserved-thinking replay requirements, sampling defaults — live in the **capability/API pages and HuggingFace model cards**, not the prose prompt guides, which tend to restate standard technique. GLM's thinking-parameter behavior, Kimi's per-family temperatures, MiniMax's `temperature=1.0 / top_p=0.95 / top_k=40`, and Qwen3's `/think` and `/no_think` switches are all card/capability-doc facts, not prompt-guide facts.

**Official vs community must stay separated.** Each document keeps vendor-documented guidance distinct from field heuristics. The clearest example is GLM's widely-reported positional bias toward the start of the prompt — a useful heuristic, but **not** in Zhipu's official docs, and labeled accordingly.

**Versioning moves fast.** These model families ship frequent releases (e.g., MiniMax M2 → M2.1 → M2.5 → M2.7 → M3; GLM 4.x → 5.x; Kimi K2 → K2.7). Parameter recommendations and thinking-mode behavior are version-specific; re-check the model card on each upgrade.

---

## Common patterns represented across the collection

Despite different framings, the official guides converge on the same core taxonomy, which is worth internalizing once and applying everywhere:

- **Clear, specific instructions** as the highest-leverage move (stated as "the most important step" by both Qwen and GLM).
- **Role/persona assignment** via the system message.
- **Delimiters** to separate instruction from content — triple quotes (GLM, Kimi), XML tags (Kimi), or high-recognizability separators like `###` / `===` / `>>>` (Qwen).
- **Few-shot / output examples** for hard-to-describe styles and output consistency.
- **Explicit task-step decomposition** for complex or multi-step tasks.
- **Reference text / grounding** with an explicit "say so if the answer isn't present" instruction to suppress hallucination.
- **Recursive chunk-and-summarize** for documents exceeding the context window.
- **The output-length caveat** — every guide that addresses length notes the model hits structural targets (paragraphs, bullets) more reliably than exact word counts.

Where the families genuinely diverge is in **reasoning/thinking control** (each has its own parameter and activation semantics), **tool-use prompting philosophy** (notably Kimi's instruction *not* to describe tools in the system prompt, versus OpenAI's structured tool examples), and **agentic-coding scaffolding** (OpenAI's `AGENTS.md` and four prompt elements; MiniMax's architect-style spec-writing tendency). Those divergences are where the per-family documents earn their keep.

---

## Synchronization Matrix

These reference files are the canonical source for model-specific prompting guidance. Every installed package also carries the same self-contained runtime compilation at `skills/subagent-model-routing/references/model-prompting.md`, because an isolated plugin cache cannot read this repository-level `docs/prompting/` directory. CI requires the Claude Code, Codex, and Copilot copies to be byte-identical. The runtime skills intentionally duplicate compact operational cards, so future guidance changes must update every surface listed here in the same change. Capability tiers are maintained in the plugin ledger (`plugins/claude/skills/subagent-model-routing/ledger/`); prompting guidance here is tier-independent.

| Model family | Canonical reference | Route status | Runtime cards | Human/audit surfaces |
|---|---|---|---|---|
| Google Gemini / Antigravity | `docs/prompting/google-gemini-prompting-reference.md` | Active via `agy-shim` and the Antigravity CLI | `plugins/claude/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| OpenAI / Codex / GPT | `docs/prompting/openai-codex-gpt-prompting-reference.md` | Active via `codex-shim` from Claude Code and Copilot; native/inline in Codex | `plugins/claude/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| Anthropic / Claude Code transport | `docs/prompting/anthropic-claude-code-prompting-reference.md` | Active via `claude-shim` from Codex and Copilot; native `Agent` in Claude Code | `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| Anthropic / Claude Sonnet 5.5 | `docs/prompting/anthropic-claude-sonnet-5-prompting-reference.md` | Default `claude-shim` target from Codex and Copilot | `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| Anthropic / Claude Opus 5.5 | `docs/prompting/anthropic-claude-opus-4.8-prompting-reference.md` | Active via `claude-shim --model opus` from Codex and Copilot | `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| Anthropic / Claude Fable 5.1 | `docs/prompting/anthropic-claude-fable-5-prompting-reference.md` | Active via `claude-shim --model fable` from Codex and Copilot; no Mythos-specific route/reference | `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| Anthropic / Claude Haiku 5.5 | `docs/prompting/anthropic-claude-haiku-5-prompting-reference.md` | Active via `claude-shim --model haiku` from Codex and Copilot | `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| xAI Grok 4.7 / Grok Build | `docs/prompting/xai-grok-prompting-reference.md` | Active via `grok-shim` | `plugins/claude/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| Kimi / Moonshot | `docs/prompting/kimi-moonshot-prompting-reference.md` | Active via `kimi-shim` and the Kimi Code CLI | `plugins/claude/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| GLM / Z.ai | `docs/prompting/glm-zhipu-prompting-reference.md` | Active via `opencode-shim zai-coding-plan/glm-5.3` | `plugins/claude/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| MiniMax | `docs/prompting/minimax-prompting-reference.md` | Active via `opencode-shim minimax/MiniMax-M3.1-Flash-Preview` | `plugins/claude/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| Qwen / Alibaba | `docs/prompting/qwen-alibaba-prompting-reference.md` | Active via `qwen-shim` and the Qwen Code CLI (any OpenAI-compatible endpoint configured in `~/.qwen/.env`, local llama.cpp included) | `plugins/claude/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/codex/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards`; `plugins/copilot/skills/subagent-model-routing/SKILL.md` -> `Prompt Reference Cards` | `README.md`; `plugins/claude/README.md`; `plugins/codex/README.md`; `plugins/copilot/README.md` |
| Meta (Muse Code / Muse Spark) | `docs/prompting/meta-muse-code-prompting-reference.md` | Active via `muse-shim` and the Muse Code CLI | all three packages' `references/model-prompting.md#muse-code` | `README.md`; the three package READMEs |
| Harness-only adapters (Pi, Hermes Agent, Cline CLI, goose, dsh) | — | Model-agnostic routes; use the reference for the configured endpoint model | No model reference by design | `README.md`; `docs/routes.md`; package route tables |
| Meta / Muse Glimmer | `docs/prompting/meta-muse-glimmer-prompting-reference.md` | Self-hosted OpenAI-compatible endpoint via `route-shim` (qwen env delivery or opencode after sync) | all three packages' `references/model-prompting.md#muse-glimmer` | `README.md`; the three package READMEs |
| DeepSeek V4 | `docs/prompting/deepseek-prompting-reference.md` | Self-hosted OpenAI-compatible endpoint via `route-shim` (qwen env delivery or opencode after sync); also on OpenCode Go as `opencode-go/deepseek-v4-*` | all three packages' `references/model-prompting.md#deepseek-v4` | `README.md`; the three package READMEs |
| Meituan LongCat | `docs/prompting/meituan-longcat-prompting-reference.md` | OpenCode Go as `opencode-go/longcat-2.5-preview-free` and `opencode-go/longcat-2.0` (self-hosting is SGLang-only on 2TB+ nodes) | all three packages' `references/model-prompting.md#longcat` | `README.md`; the three package READMEs |
| Xiaomi MiMo | `docs/prompting/xiaomi-mimo-prompting-reference.md` | OpenCode Go as `opencode-go/mimo-v2.6-pro`, `opencode-go/mimo-v2.6-flash`, `opencode-go/mimo-v2.5`, and `opencode-go/mimo-v2.5-pro` (self-hosting via the vLLM recipe) | all three packages' `references/model-prompting.md#mimo` | `README.md`; the three package READMEs |
| Tencent Hy (Hunyuan) | `docs/prompting/tencent-hy-prompting-reference.md` | OpenCode Go as `opencode-go/hy3` and `opencode-go/hy4-preview` (self-hosting via the vLLM recipe) | all three packages' `references/model-prompting.md#hy-tencent` | `README.md`; the three package READMEs |
| Google Gemma 4 | `docs/prompting/google-gemma-prompting-reference.md` | Self-hosted OpenAI-compatible endpoint via `route-shim` (qwen env delivery or opencode after sync) | all three packages' `references/model-prompting.md#gemma-4` | `README.md`; the three package READMEs |

## Update Checklist

When any canonical prompt reference changes:

1. Update every runtime card location listed in the corresponding synchronization-matrix row and all three package-local `references/model-prompting.md` copies. Client-native model families intentionally do not appear as shim routes in their own package.
2. If route status changed, update this matrix and the allowed-routes text in the affected runtime skills.
3. If the change affects user-facing orientation, update `README.md` and the affected package READMEs.
4. Run the structural prompt-reference validation checks, including package-local reference resolution and byte-for-byte bundle synchronization.
5. When a harness gains or loses MCP registration support, update the "Asking through the orchestrator channel" tier in every reference that routes through it, and the tier list in model-prompting.md.

Qwen is active through the dedicated `qwen-shim` over the Qwen Code CLI, pointed at any OpenAI-compatible endpoint via `~/.qwen/.env`; other local/self-hosted models still route through opencode custom providers. If that route changes, update this matrix, the runtime cards, allowed-routes text, and README surfaces in the same change.
