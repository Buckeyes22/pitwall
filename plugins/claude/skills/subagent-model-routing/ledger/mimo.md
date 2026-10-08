# MiMo-V2.5 — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Not yet ranked *(seed)*
- **Excels at:** omnimodal (text/image/video/audio) at 15B active parameters; 1M context; cheap high-volume Go tiers (30,100 requests per 5 hours on MiMo-V2.5)
- **Struggles with:** no first-party prompting guide; no documented thinking on/off toggle
- **Operational caveats:** Xiaomi 311B/15B MoE (Pro: 1.02T/42B, text-only), native FP8, MIT license. Self-host: vLLM recipe pins TP=4 for V2.5 (TP=8 broken) and 8x B200 for Pro; subscription route is `opencode-go/mimo-v2.5` / `opencode-go/mimo-v2.5-pro`. Card sampling: temperature=1.0, top_p=0.95.
- **Evidence:** seed default — replace with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-09-07 (seed)

<!-- MODEL-FACTS:mimo START (generated from docs/agents/model-facts/families/mimo; edit facts.json, not this block) -->
- **Models:** `mimo-v2.5` (retires 2026-10-21), `mimo-v2.5-pro` (retires 2026-10-21), `mimo-v2.6-pro`, `mimo-v2.6-flash`, `mimo-v2.6-pro-ultraspeed`
- **Context:** 1,048,576 tokens, output 131,072.
- **Sampling:** `mimo-v2.5`, `mimo-v2.6-pro`, `mimo-v2.6-flash`, `mimo-v2.6-pro-ultraspeed`: temperature 1.0, top_p 0.95; custom values are ignored; `mimo-v2.5-pro`: custom values are ignored.
- **Reasoning:** pass the whole assistant message, reasoning included, back on every turn (`mimo-v2.5`, `mimo-v2.5-pro`, `mimo-v2.6-pro`, `mimo-v2.6-flash`, `mimo-v2.6-pro-ultraspeed`).
- **Stated by vendor:** On turns with tool calls, send reasoning content back unchanged. Dropping it lowers instruction following and raises hallucination; OpenCode and Goose are named as affected tools.
- **Stated by vendor:** MiMo V2.6 (pro, flash) replaces V2.5, which retires on 2026-10-21. Use V2.6 for new work; ultraspeed is a contact-sales tier with no Pitwall route.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/mimo/FACTS.md`.
<!-- MODEL-FACTS:mimo END -->
