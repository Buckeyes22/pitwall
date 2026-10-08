# Claude Sonnet 5 — capability card (seed example)

> System-card prior — maintain routing confidence via `/pitwall:distill` and your own ledger.

- **Route:** `claude-shim.sh <prompt-file>` or `--model sonnet` from the Codex and Copilot packages; Claude-hosted work uses native `Agent` calls
- **Tier:** default Claude workhorse; unranked against other providers until local ledger evidence exists
- **Excels at:** multi-file coding, terminal work, agentic search, multimodal reasoning, and professional tasks; clear gains over Sonnet 4.6
- **Struggles with:** slightly weaker flawed-result handling than Opus 4.8; higher closed-book abstention/error rates than stronger contemporary Claude models; occasional over-refusal or overly discouraging responses
- **Operational caveats:** published benchmarks generally used adaptive thinking at maximum effort. The `sonnet` alias can advance to a newer version; confirm it still maps to Sonnet 5 before applying version-specific claims. Require source/tool inspection and deterministic checks.
- **Evidence:** official [Claude Sonnet 5 System Card](https://www.anthropic.com/claude-sonnet-5-system-card) (June 30, 2026) via `../references/model-prompting.md#claude-sonnet-55`
- **Last distilled:** 2026-07-10 (system-card seed)

<!-- MODEL-FACTS:claude-sonnet-5 START (generated from docs/agents/model-facts/families/claude-sonnet-5; edit facts.json, not this block) -->
- **Models:** `claude-sonnet-5`, `claude-sonnet-5.5`
- **Context:** 1,000,000 tokens, output 128,000.
- **Effort:** `claude-sonnet-5`: low, medium, high, xhigh, max (default high); `claude-sonnet-5.5`: low, medium, high, xhigh, max (default high), cannot be disabled.
- **Sampling:** custom values are ignored.
- **Stated by harness (`claude-sonnet-5.5`):** In Claude Code the sonnet alias reaches Sonnet 5.5 on the Anthropic API; on Platform on AWS it reaches Sonnet 4.6, and on Bedrock, Agent Platform and Foundry Sonnet 4.5. Pin claude-sonnet-5 by name for Sonnet 5.
- **Stated by vendor (`claude-sonnet-5.5`):** Effort defaults to high. Start at medium for well-specified agentic coding, high for harder work, low or medium for chat. Levels are recalibrated from Sonnet 5, so re-run the sweep.
- **Stated by vendor (`claude-sonnet-5.5`):** To skip up-front thinking send thinking type between_tools, at high effort or below. Disabled and budget_tokens return 400, as does between_tools at xhigh or max.
- **Stated by vendor (`claude-sonnet-5.5`):** Forced tool use is rejected: tool_choice any or a named tool returns a 400. Non-default temperature, top_p or top_k also return 400.
- **Stated by vendor (`claude-sonnet-5.5`):** Notes longer than a sentence between tool calls arrive as thinking blocks, empty by default, so a client showing only text looks silent. Use between_tools or display updates.
- **Stated by vendor (`claude-sonnet-5.5`):** At low and medium effort on long agentic work it may stop to check in. Raise effort or add a line to keep working until done and report only when finished.
- **Stated by vendor (`claude-sonnet-5.5`):** At low effort it can report a change done without running a check. Ask for a real test, type-check or build run before it reports done.
- **Stated by vendor (`claude-sonnet-5.5`):** Thinking blocks are bound to the model and conversation. Editing earlier turns or the system prompt can make replayed blocks fail with a 400, so keep history append-only.
- **Stated by vendor (`claude-sonnet-5.5`):** Safety classifiers can decline cyber, bio, frontier_llm, reasoning_extraction and general_harms requests with stop reason refusal in a normal response. Server-side fallback retries only cyber and frontier_llm declines.
- **Stated by harness (`claude-sonnet-5.5`):** Claude Code re-runs a cyber-flagged Sonnet 5.5 request on Sonnet 5. A bio-flagged one ends with a refusal, because Sonnet 5.5 has no biology fallback model.
- **Stated by harness:** Claude Code loads CLAUDE.md and CLAUDE.local.md from the working directory and every directory above it, root first. AGENTS.md is read only when no CLAUDE.md exists.
- **Facts checked:** 2026-10-07. Sources: `docs/agents/model-facts/families/claude-sonnet-5/FACTS.md`.
<!-- MODEL-FACTS:claude-sonnet-5 END -->
