# Claude Haiku 5.5 — capability card (vendor-docs seed)

> Vendor-documentation prior — maintain routing confidence via `/pitwall:distill` and your own ledger.

- **Route:** `claude-shim.sh <prompt-file> --model haiku` from the Codex and Copilot packages; Claude-hosted work uses native `Agent` calls
- **Tier:** fastest and cheapest current Claude seat; unranked against other providers until local ledger evidence exists
- **Excels at:** high-volume, latency-sensitive work such as classification, extraction, routing, and subagent tasks, with a 1M-token context window and effort levels
- **Struggles with:** at `low` effort in long agent prompts it can skip a search, stop before the work is done, or report a change done without running a check; with thinking off and a JSON output format it can skip a tool call it needs
- **Operational caveats:** the `haiku` alias reaches Haiku 5.5 only on the Anthropic API from Claude Code v2.1.293, and Haiku 4.5 elsewhere; confirm the resolved model before applying version-specific claims. It has no server-side fallback for refusals. Require deterministic checks and treat any `refusal` stop reason as a failed attempt.
- **Evidence:** vendor [Claude Haiku 5.5 overview](https://platform.claude.com/docs/en/models/haiku-5-5/overview) (October 7, 2026) and [Prompting Claude Haiku 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5), via `../references/model-prompting.md#claude-haiku-55`
- **Last distilled:** 2026-10-07 (vendor-docs seed)

<!-- MODEL-FACTS:claude-haiku-5 START (generated from docs/agents/model-facts/families/claude-haiku-5; edit facts.json, not this block) -->
- **Models:** `claude-haiku-5.5`
- **Context:** 1,000,000 tokens, output 128,000.
- **Effort:** low, medium, high, xhigh, max (default medium).
- **Sampling:** custom values are ignored.
- **Stated by vendor (`claude-haiku-5.5`):** Anthropic positions it for high-volume, latency-sensitive work such as classification, extraction, routing and subagent tasks, with the lowest latency and price in the current lineup.
- **Stated by harness (`claude-haiku-5.5`):** In Claude Code the haiku alias reaches Haiku 5.5 on the Anthropic API from v2.1.293; on Platform on AWS, Bedrock, Agent Platform and Foundry it still reaches Haiku 4.5. Pin claude-haiku-5-5 by name.
- **Stated by vendor (`claude-haiku-5.5`):** Effort defaults to medium; start there, including for agentic coding. Use low for chat and simple volume work. xhigh and max need measured gains over Sonnet 5.5. It is the first Haiku with effort levels.
- **Stated by vendor (`claude-haiku-5.5`):** Thinking is on by default. disabled is accepted at high effort or below and returns 400 at xhigh or max; budget_tokens always returns 400. To think less, lower effort first.
- **Stated by harness (`claude-haiku-5.5`):** Claude Code shows no off switch for thinking on Haiku 5.5, and MAX_THINKING_TOKENS=0 has no effect there. Use a lower effort level to reduce thinking in a Claude Code route.
- **Stated by vendor (`claude-haiku-5.5`):** Thinking counts toward max_tokens, which can reach 128000, so limits sized for Haiku 4.5 can stop after a thinking block. The newer tokenizer also yields about 30 percent more tokens for the same text.
- **Stated by vendor (`claude-haiku-5.5`):** In long agent prompts at low effort it can stop early or report a change done without running a check. Raise effort, or ask for a real test, type-check or build run before it reports done.
- **Stated by vendor (`claude-haiku-5.5`):** Safety classifiers can decline cyber, frontier_llm, bio and general_harms requests with stop reason refusal. There is no server-side fallback, so handle the refusal in the client; resending usually repeats it.
- **Stated by vendor (`claude-haiku-5.5`):** Thinking blocks work only in the account that produced them and only while the system prompt, tools and earlier turns are unchanged. Editing history can fail a replayed block with a 400, so keep it append-only.
- **Stated by harness:** Claude Code loads CLAUDE.md and CLAUDE.local.md from the working directory and every directory above it, root first. AGENTS.md is read only when no CLAUDE.md exists.
- **Facts checked:** 2026-10-07. Sources: `docs/agents/model-facts/families/claude-haiku-5/FACTS.md`.
<!-- MODEL-FACTS:claude-haiku-5 END -->
