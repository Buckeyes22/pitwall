# Meituan LongCat — Prompt Engineering Reference

| Field | Value |
|---|---|
| Vendor | Meituan (LongCat team) |
| Models in scope | LongCat-2.0 (plus the earlier LongCat-Flash line) |
| Primary access | Open weights (MIT) on HuggingFace `meituan-longcat`; hosted access via aggregators such as the OpenCode Go subscription (`opencode-go/longcat-2.5-preview-free`, `opencode-go/longcat-2.0`) |
| Official guidance | **No dedicated prompt-engineering guide.** The model card plus the team's deployment cookbooks are the first-party surface. |
| Canonical doc host | `huggingface.co/meituan-longcat` |
| Compiled | September 2026 |

---

## 1. Official guidance landscape

Meituan publishes no prose prompt-engineering guide for LongCat. The first-party sources are the **HuggingFace model cards** (`huggingface.co/meituan-longcat/*`), which carry the thinking controls, tool-call conventions, and context claims used below. Deployment guidance lives in the SGLang cookbook (official SGLang documentation, not Meituan-authored) and the card's linked superpod recipes.

Coverage assessment: model-card-only, comparable to MiniMax's text situation. Assemble prompting guidance from the card facts; there is no vendor prompt taxonomy to follow.

---

## 2. Inference parameters (text models)

The LongCat-2.0 card states no sampling recommendations — this is a gap, not a default. Until Meituan publishes values, treat any sampling choice as non-official and validate locally.

Context: 262,144 tokens served (the card reports training on 1M-context data; served lengths beyond 256K are unverified).

---

## 3. Reasoning / thinking control

Thinking is controlled through the chat template, not a request parameter:

- `enable_thinking=True/False` — chat-template kwarg toggling the reasoning mode.
- `save_reasoning_content` — option to persist assistant reasoning turns; assistant messages carry `reasoning_content`.

This is the same shape as Qwen's template kwargs; if a harness owns the template invocation, the prompt file itself cannot flip the switch — configure it in the harness's provider settings.

---

## 4. Tool calling

LongCat's chat template accepts OpenAI-style `tools` and emits `tool_calls`, with one documented divergence (from the card): **`arguments` must be a dict, not the string-encoded form the standard OpenAI schema uses.** Adapters that stringify tool arguments will produce malformed calls. The card documents interleaved tool use in agentic evaluations.

---

## 5. Documented model behaviors relevant to prompting

From the LongCat-2.0 card: sparse-attention long-context focus (38-layer, 768-expert MoE with identity "zero experts"), agentic and long-context benchmark positioning, and an N-gram embedding table for retrieval-style grounding. No system-prompt default is published; there is no canonical recommended prompt of the kind Kimi or Qwen document.

---

## 6. Practical recommendation for text prompting (non-official)

Flagged explicitly as **not** Meituan-official: with no vendor guide, apply the shared agentic prompt contract (objective, scope, artifacts, validation, completion criteria) and the collection's common patterns — role assignment, delimiters, explicit task decomposition, and output-format demands with post-return validation. For thinking control, prefer leaving `enable_thinking` on for coding work and off for extraction-style tasks where the reasoning latency is not worth it, then validate both directions locally.

---

## 7. Quick reference

| Situation | Action |
|---|---|
| Sampling defaults | None published — validate locally; do not copy other vendors' values |
| Thinking on/off | `enable_thinking` chat-template kwarg (configure in the harness, not the prompt) |
| Preserve reasoning | `save_reasoning_content` option; assistant turns carry `reasoning_content` |
| Tool arguments | Dicts, not strings — check adapter serialization |
| Long context | 256K served window |
| Self-hosting | SGLang main/nightly only; FP8 weights ~2 TB — B300-class nodes |
| Subscription route | `opencode-go/longcat-2.5-preview-free` / `opencode-go/longcat-2.0` |

---

## 8. Sources

- Model card: https://huggingface.co/meituan-longcat/LongCat-2.0
- Config (context, expert layout): https://huggingface.co/meituan-longcat/LongCat-2.0/raw/main/config.json
- SGLang deployment cookbook (official SGLang docs, not Meituan): https://docs.sglang.io/cookbook/autoregressive/Meituan/LongCat-2.0

## Asking through the orchestrator channel

Through `opencode`, this family asks on tier 1. Tier 1: after `pitwall agents setup mcp --harness opencode`, the dispatcher gives the model the `ask_orchestrator` tool and it waits for the answer. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
