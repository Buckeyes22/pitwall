# Tencent Hy (Hunyuan) — Prompt Engineering Reference

| Field | Value |
|---|---|
| Vendor | Tencent (Tencent Hy team / Hunyuan organization) |
| Models in scope | Hy3, Hy4-preview (plus the earlier Hy3-preview and the Hy-MT translation line) |
| Primary access | Open weights (Apache-2.0) on HuggingFace `tencent` under the Tencent-Hunyuan org; hosted access via aggregators such as the OpenCode Go subscription (`opencode-go/hy3`, `opencode-go/hy4-preview`) |
| Official guidance | **No dedicated prompt-engineering guide.** The model cards (with quickstart code) are the first-party surface. |
| Canonical doc hosts | `huggingface.co/tencent`, `github.com/Tencent-Hunyuan`, ModelScope `Tencent-Hunyuan` |
| Compiled | September 2026 |

---

## 1. Official guidance landscape

The Hy team publishes no prose prompt-engineering guide. First-party content is the **HuggingFace model cards**, which carry the reasoning-effort control, sampling recommendations, tool-call stability claims, and quickstart snippets. Coverage assessment: model-card-only; the cards are unusually operational (they include complete vLLM launch commands), but there is no prompt taxonomy.

---

## 2. Inference parameters (text models)

From both cards: **`temperature=0.9`, `top_p=1.0`** — stated as "Recommended parameters". This is an unusual combination in the collection (most vendors recommend 1.0/0.95); prefer the card values when in doubt and validate locally.

Context: Hy3 serves 262,144 tokens; Hy4-preview serves 1,048,576.

---

## 3. Reasoning / thinking control

Both models steer reasoning depth with `reasoning_effort`, but they accept different values. Each chat template raises an error on any other value.

- Hy3 accepts `no_think`, `low`, and `high`, and defaults to `no_think` (Hy3 `chat_template.jinja` lines 40-46; the card quickstart agrees, lines 135 and 143).
- Hy4-preview accepts only `no_think` and `high`, and defaults to `high` (Hy4-preview `chat_template.jinja` lines 32-38). `low` is an error there.

On Hy4-preview the switch is passed as `extra_body={"chat_template_kwargs": {"reasoning_effort": ...}}` per the card. Prefer `no_think` for extraction and routine edits; use `high` for multi-step coding and debugging.

---

## 4. Tool calling

Tool calling is a headline capability: the Hy3 card documents production-grade tool-call stability (tool-call error recovery and consistency improvements across CodeBuddy, Cline, and KiloCode scaffoldings), and both cards' serving commands attach dedicated parsers (`hy_v3` / `hy_v4`) with auto tool choice. No Hy-specific prompt syntax for tools is documented; use the OpenAI tools schema.

---

## 5. Documented model behaviors relevant to prompting

From the cards: Hy4-preview adds gated DeepSeek-style sparse attention with cross-layer index reuse and identity Hyper-Connections, targeting long-context agent work at 1M tokens; both models ship a native MTP layer for speculative decoding (serving-side, not prompt-side). No default system prompt is published.

---

## 6. Practical recommendation for text prompting (non-official)

Flagged explicitly as **not** Tencent-official: apply the shared agentic prompt contract and the collection's common patterns, with the card sampling defaults (0.9/1.0). Use `reasoning_effort` as the primary cost/quality dial (Hy3 has three levels, Hy4-preview two) and validate tool-heavy prompts against the documented scaffold-consistency claims.

---

## 7. Quick reference

| Situation | Action |
|---|---|
| Default sampling | `temperature=0.9`, `top_p=1.0` (card-recommended) |
| Thinking dial | `reasoning_effort`: Hy3 `no_think` / `low` / `high` (default `no_think`); Hy4-preview `no_think` / `high` (default `high`); other values error |
| Tool use | OpenAI tools schema; `hy_v3`/`hy_v4` parsers in serving |
| Long context | Hy3 256K; Hy4-preview 1M |
| Self-hosting | Hy3 fits 8x H200 (FP8 floor 354 GB); Hy4-preview needs 8x B300-class |
| Subscription route | `opencode-go/hy3` / `opencode-go/hy4-preview` |

---

## 8. Sources

- Model card (Hy3): https://huggingface.co/tencent/Hy3
- Model card (Hy4-preview): https://huggingface.co/tencent/Hy4-preview
- vLLM recipe (Hy3): https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy3.yaml
- vLLM recipe (Hy4-preview): https://github.com/vllm-project/recipes/blob/main/models/tencent/Hy4-preview.yaml
- GitHub organization: https://github.com/Tencent-Hunyuan

## Asking through the orchestrator channel

Through `opencode`, this family asks on tier 1. Tier 1: after `pitwall agents setup mcp --harness opencode`, the dispatcher gives the model the `ask_orchestrator` tool and it waits for the answer. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
