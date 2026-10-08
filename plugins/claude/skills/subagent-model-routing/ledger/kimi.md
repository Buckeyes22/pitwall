# Kimi K3 — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Mid-tier (between GLM-5.3 Opus peer and MiniMax-M3 Sonnet peer) *(seed ranking)*
- **Excels at:** mid-tier authoring, burst parallelism
- **Struggles with:** 1-3 sustained concurrency (502s)
- **Operational caveats:** K3 (`kimi-code/k3`): 2.8T MoE/104B active, 1M context; thinking always on, steer with `reasoning_effort` (`low`/`high`/`max`, default `max`); preserved thinking must be passed back in multi-turn flows; subscription-friendly; concurrency-friendly for parallel candidate fan-out; do not sustain >3 concurrent shim calls (502 pressure); see `../references/model-prompting.md#kimi`. OpenCode Go also serves K3 (`opencode-go/kimi-k3`, low 110-requests-per-5-hours tier) plus the open-weight K2.7-Code (thinking-only, 1T/32B, 256K) and K2.6 (thinking or Instant mode) at far higher request tiers; K2.x uses Modified MIT weights.
- **Evidence:** seed default — replace with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-07-07 (seed)

<!-- MODEL-FACTS:kimi START (generated from docs/agents/model-facts/families/kimi; edit facts.json, not this block) -->
- **Models:** `kimi-k3`, `kimi-k2.7-code`, `kimi-k2.6`
- **Context:** `kimi-k3`: 1,048,576 tokens; `kimi-k2.7-code`, `kimi-k2.6`: 262,144 tokens.
- **Effort:** `kimi-k3`: low, high, max (default max), cannot be disabled.
- **Sampling:** `kimi-k2.7-code`: temperature 1.0, top_p 0.95.
- **Reasoning:** pass the whole assistant message, reasoning included, back on every turn (`kimi-k3`, `kimi-k2.7-code`).
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/kimi/FACTS.md`.
<!-- MODEL-FACTS:kimi END -->
