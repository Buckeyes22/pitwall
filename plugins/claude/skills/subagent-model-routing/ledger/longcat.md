# LongCat-2.0 — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Not yet ranked *(seed)*
- **Excels at:** long-context work at very low token cost; unlimited-usage Go tier (11,400 requests per 5 hours); thinking on/off control
- **Struggles with:** no first-party English prompting guide; tool arguments must be dicts, not OpenAI-style strings
- **Operational caveats:** Meituan 1.6T-class MoE (~48B active), 256K context, MIT license. Self-hosting needs 2 TB+ nodes (SGLang nightly only); the practical route is the OpenCode Go subscription (`opencode-go/longcat-2.0`). `enable_thinking` toggles reasoning; no documented effort dial.
- **Evidence:** seed default — replace with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-09-07 (seed)

<!-- MODEL-FACTS:longcat START (generated from docs/agents/model-facts/families/longcat; edit facts.json, not this block) -->
- **Models:** `longcat-2.0`, `longcat-2.5-preview`
- **Context:** `longcat-2.0`: 262,144 tokens, output 131,072; `longcat-2.5-preview`: 1,000,000 tokens, output 131,072.
- **Stated by vendor:** LongCat-2.5-Preview accepts image input as well as text; LongCat-2.0 stays text only. It is a preview, and OpenCode Go serves it under the -free id.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/longcat/FACTS.md`.
<!-- MODEL-FACTS:longcat END -->
