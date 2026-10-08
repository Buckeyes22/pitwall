# GLM-5.3 — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Opus peer (default routed author) *(seed ranking)*
- **Excels at:** everyday code authoring and review at Opus-peer quality; JSON/structured extraction; balanced throughput
- **Struggles with:** (none confirmed at this tier yet — observations pending)
- **Operational caveats:** 5.3 keeps the 744B-A40B base, 1M context/128K output; reasoning mandatory (`reasoning_effort` low/high/max, default max — use max for coding); text-only inputs; coding-plan endpoint via opencode; ~2-5 req/min sustained on medium prompts; code `1302` = ZhipuAI 429
- **Evidence:** seed default — replace with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-07-07 (seed)

<!-- MODEL-FACTS:glm START (generated from docs/agents/model-facts/families/glm; edit facts.json, not this block) -->
- **Models:** `glm-5.3`, `glm-5.3-flash`, `glm-5.2`, `glm-5.1`
- **Context:** `glm-5.3`, `glm-5.3-flash`, `glm-5.2`: 1,048,576 tokens, output 131,072; `glm-5.1`: 202,752 tokens.
- **Effort:** `glm-5.3`, `glm-5.3-flash`: low, high, max (default max), cannot be disabled; `glm-5.2`: high, max (default max).
- **Effort on other values:** `glm-5.3`, `glm-5.3-flash`, `glm-5.2`: any other value runs as max.
- **Sampling:** temperature 1.0, top_p 0.95.
- **Reasoning:** pass the whole assistant message, reasoning included, back on every turn (`glm-5.3`, `glm-5.3-flash`, `glm-5.2`, `glm-5.1`).
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/glm/FACTS.md`.
<!-- MODEL-FACTS:glm END -->
