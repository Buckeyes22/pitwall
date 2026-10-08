# MiniMax-M3 — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Sonnet peer (Sonnet-grade throughput) *(seed ranking)*
- **Excels at:** sonnet-grade throughput
- **Struggles with:** intermittent stalls — retry ≤3, no reroute
- **Operational caveats:** stall detection/retry machinery retained as cheap insurance; `--thinking` is a binary visibility toggle for M3, not an effort dial; no official text prompt-engineering guide — use Anthropic-style structured prompting. M3 (1M context, tri-state `thinking`) and M2.7 (230B/10B, 196K context) are both on OpenCode Go (`opencode-go/minimax-m3`, `opencode-go/minimax-m2.7`); M2.7 weights are non-commercial licensed.
- **Evidence:** seed default — replace with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-07-07 (seed)

<!-- MODEL-FACTS:minimax START (generated from docs/agents/model-facts/families/minimax; edit facts.json, not this block) -->
- **Models:** `minimax-m3`, `minimax-m2.7`, `minimax-m3.1-flash-preview`
- **Context:** `minimax-m3`: 1,048,576 tokens; `minimax-m2.7`: 204,800 tokens; `minimax-m3.1-flash-preview`: 1,000,000 tokens.
- **Effort:** `minimax-m3.1-flash-preview`: low, medium, high, xhigh, max (default max), cannot be disabled.
- **Effort on other values:** `minimax-m3.1-flash-preview`: other values are rejected.
- **Sampling:** `minimax-m3`: temperature 1.0, top_p 0.95; `minimax-m2.7`: temperature 1.0, top_p 0.95, top_k 40; `minimax-m3.1-flash-preview`: top_p 0.95.
- **Reasoning:** pass the whole assistant message, reasoning included, back on every turn (`minimax-m3`, `minimax-m2.7`, `minimax-m3.1-flash-preview`).
- **Stated by vendor:** In multi-turn and tool-use work, send the model's full previous reply back, thinking blocks included, or its reasoning chain breaks.
- **Stated by vendor:** MiniMax-M3.1-Flash-Preview is offered only through Token Plan and MiniMax Code for now, so no Pitwall route reaches it yet. The public API and OpenCode Go serve M3.
- **Stated by vendor (`minimax-m3.1-flash-preview`):** M3.1-Flash-Preview always thinks. Lower effort to cut latency; disabling thinking or sending effort none returns HTTP 400. Omitting effort means max.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/minimax/FACTS.md`.
<!-- MODEL-FACTS:minimax END -->
