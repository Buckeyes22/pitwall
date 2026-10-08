# codex GPT-5.6 family — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Sol provisionally holds the strongest-implementer seat (≥ GLM-5.3 Opus peer; reserved for hardest/critical + deepest review); Terra and Luna remain unranked pending local evidence *(seed ranking)*
- **Excels at:** hardest implementation, autonomous verify, deepest review
- **Struggles with:** system-card evaluations found a greater tendency than GPT-5.5 to go beyond user intent; prompts must state authorization boundaries and destructive-action constraints explicitly
- **Operational caveats:** Sol is the flagship route; Terra is the lower-cost route; Luna is the fastest and most cost-efficient route. Treat completion claims as unverified until artifacts and deterministic checks pass, especially after tool failures. Deep multi-file work routinely approaches the 20-min ceiling (split long jobs or raise PITWALL_AGENTS_TIMEOUT_SECS deliberately).
- **Evidence:** GPT-5.6 System Card (`https://deploymentsafety.openai.com/gpt-5-6`) plus seed defaults — replace routing opinions with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-07-09 (seed)

<!-- MODEL-FACTS:codex START (generated from docs/agents/model-facts/families/codex; edit facts.json, not this block) -->
- **Models:** `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`
- **Context:** 1,050,000 tokens, output 128,000.
- **Effort:** `gpt-6-astra`: low, medium, high, xhigh, max, cannot be disabled; `gpt-6.1-sol`: low, medium, high, xhigh, max (default medium), cannot be disabled; `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`: none, low, medium, high, xhigh, max (default medium).
- **Effort on other values:** `gpt-6-astra`: other values are rejected.
- **Stated by vendor (`gpt-6-astra`):** GPT-6 Astra asks a focused question when the answer could change the outcome. Say what a request authorizes and to proceed on reasonable assumptions, or it may stop where you expect it to continue.
- **Stated by vendor (`gpt-6-astra`):** GPT-6 Astra weighs skills and AGENTS.md closely, and conflicting text can make it pause. Audit those files and state that the user's instructions outrank a skill's.
- **Stated by vendor (`gpt-6-astra`):** GPT-6 Astra may delegate to subagents less often than a workflow wants. Say when and how much to split work for parallel subagents.
- **Stated by vendor (`gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`):** With effort above none, omit temperature, top_p and top_logprobs (plus logprobs in Chat Completions, or its include entry in Responses). Tools need Responses; Sol and Luna allow Chat Completions tools only at none.
- **Stated by vendor (`gpt-6.1-sol`):** GPT-6.1 Sol aims for near-Astra results at lower cost; compare it with Astra on your own tasks. It has no none or minimal effort (use low in their place), and tool calls need the Responses API.
- **Stated by harness:** Codex reads AGENTS.override.md or AGENTS.md from ~/.codex, then one file per directory from the project root down. Later files win, and total size stops at 32 KiB.
- **Facts checked:** 2026-10-07. Sources: `docs/agents/model-facts/families/codex/FACTS.md`.
<!-- MODEL-FACTS:codex END -->
