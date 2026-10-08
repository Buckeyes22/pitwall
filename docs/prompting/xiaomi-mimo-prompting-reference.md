# Xiaomi MiMo — Prompt Engineering Reference

| Field | Value |
|---|---|
| Vendor | Xiaomi (Xiaomi MiMo team) |
| Models in scope | MiMo-V2.5 (omnimodal 311B/15B), MiMo-V2.5-Pro (1.02T/42B text), plus the V2-Flash backbone line |
| Primary access | Open weights (MIT) on HuggingFace `XiaomiMiMo`; hosted access via aggregators such as the OpenCode Go subscription (`opencode-go/mimo-v2.6-pro`, `opencode-go/mimo-v2.6-flash`, `opencode-go/mimo-v2.5`, `opencode-go/mimo-v2.5-pro`) |
| Official guidance | **No dedicated prompt-engineering guide.** The model cards and the team's serving cookbooks are the first-party surface. |
| Canonical doc host | `huggingface.co/XiaomiMiMo` |
| Compiled | September 2026 |

---

## 1. Official guidance landscape

Xiaomi's MiMo team publishes no prose prompt-engineering guide. First-party content is the **HuggingFace model cards** (`huggingface.co/XiaomiMiMo/*`) with sampling recommendations, modality claims, and MTP/speculative-decoding notes, plus vLLM cookbook pages for the V2.5 line (official vLLM documentation). Coverage assessment: model-card-only; assemble guidance from card facts.

---

## 2. Inference parameters (text models)

From the cards: **`temperature=1.0`, `top_p=0.95`** — stated as an explicit recommendation on both MiMo-V2.5 and MiMo-V2.5-Pro. No top_k default is published.

Context: 1,048,576 tokens on both V2.5 models (the `-Base` siblings are 256K).

---

## 3. Reasoning / thinking control

Xiaomi's hosted API switches thinking with the `thinking.type` request parameter (`enabled` or `disabled`); both models think by default (https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/deep-thinking). While thinking is on, custom `temperature` and `top_p` are ignored and the platform forces 1.0 and 0.95 (https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/deep-thinking). For open-weight serving, the chat templates emit think/reasoning segments and every documented serving configuration attaches a reasoning parser (`--reasoning-parser qwen3` in the card's SGLang example for V2.5; `mimo` for Pro and in the vLLM recipe).

On turns with tool calls, pass the model's reasoning content back unchanged. Dropping it lowers instruction following and raises hallucination, and the page names OpenCode and Goose among affected tools (https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/deep-thinking).

Both models retire on 2026-10-21 (Beijing time). The deprecations page lists no automatic replacement, so requests fail after that date; the models page recommends `mimo-v2.6-flash` or `mimo-v2.6-pro` (https://mimo.mi.com/docs/en-US/quick-start/summary/model, https://mimo.mi.com/docs/en-US/updates/deprecate).

---

## 4. Tool calling

Tool calling is first-class: the cards lead with tool-use training at scale (Pro: "thousands of tool calls" trajectories) and every serving recipe attaches `--tool-call-parser mimo` with auto tool choice. No MiMo-specific prompt syntax for tools is documented; use the OpenAI tools schema and rely on the parser.

---

## 5. Documented model behaviors relevant to prompting

From the cards: MiMo-V2.5 is natively omnimodal (text, image, video, audio; a 729M ViT and a 261M audio encoder) — prompts may carry multimodal parts; Pro is text-only. Hybrid SWA:GA attention with a learnable attention sink targets long-context efficiency. V2.5 was trained on ~48T tokens, Pro on ~27T. No default system prompt is published.

---

## 6. Practical recommendation for text prompting (non-official)

Flagged explicitly as **not** Xiaomi-official: apply the shared agentic prompt contract and the collection's common patterns. Keep the documented sampling defaults (temperature 1.0, top_p 0.95). Thinking can be switched off with `thinking.type`; also budget latency by routing reasoning-heavy work to Pro-shaped capacity and extraction-style work to cheaper tiers, then validate locally.

---

## 7. Quick reference

| Situation | Action |
|---|---|
| Default sampling | `temperature=1.0`, `top_p=0.95` (card-recommended) |
| Thinking control | `thinking.type` `enabled`/`disabled`; sampling ignored while thinking |
| Retirement | Both models retire 2026-10-21; no automatic replacement |
| Tool use | OpenAI tools schema; `mimo` tool parser in serving |
| Multimodal | V2.5 accepts text/image/video/audio; Pro is text-only |
| Long context | 1M tokens on both V2.5 models |
| Self-hosting | vLLM recipe pins TP=4 for V2.5 (TP=8 broken); Pro needs 8x B200-class |
| Subscription route | `opencode-go/mimo-v2.6-pro` / `opencode-go/mimo-v2.6-flash` / `opencode-go/mimo-v2.5` / `opencode-go/mimo-v2.5-pro` |

---

## 8. Sources

- Models page: https://mimo.mi.com/docs/en-US/quick-start/summary/model
- Deep thinking guide: https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/deep-thinking
- Deprecations: https://mimo.mi.com/docs/en-US/updates/deprecate
- Model card (V2.5): https://huggingface.co/XiaomiMiMo/MiMo-V2.5
- Model card (Pro): https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro
- vLLM recipe (V2.5, TP=4 caveat): https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.5.yaml
- vLLM recipe (Pro): https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.5-Pro.yaml

## Asking through the orchestrator channel

Through `opencode`, this family asks on tier 1. Tier 1: after `pitwall agents setup mcp --harness opencode`, the dispatcher gives the model the `ask_orchestrator` tool and it waits for the answer. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
