# Claude Opus 4.8 — capability card (seed example)

> System-card prior — maintain routing confidence via `/pitwall:distill` and your own ledger.

- **Route:** `claude-shim.sh <prompt-file> --model opus` from the Codex and Copilot packages; Claude-hosted work uses native `Agent` calls
- **Tier:** verification-heavy and difficult-work Claude seat; unranked against other providers until local ledger evidence exists
- **Excels at:** software engineering, agentic tool use, knowledge work, deep repository tracing, long context, professional tasks, and honest code-status summaries
- **Struggles with:** system-card case studies still include fabrication, ignored corrections, skipped cheap verification, and instruction-following failures; unsafeguarded prompt-injection robustness regressed in some agentic settings; refusals can be over-elaborate
- **Operational caveats:** published benchmarks generally used adaptive thinking at maximum effort. The `opus` alias can advance to a newer version; confirm it still maps to Opus 4.8 before applying version-specific claims. Require actual command evidence and explicit untrusted-content boundaries.
- **Evidence:** official [Claude Opus 4.8 System Card](https://www.anthropic.com/claude-opus-4-8-system-card) (May 28, 2026; corrections through June 17, 2026) via `../references/model-prompting.md#claude-opus-55`
- **Last distilled:** 2026-07-10 (system-card seed)

<!-- MODEL-FACTS:claude-opus-4.8 START (generated from docs/agents/model-facts/families/claude-opus-4.8; edit facts.json, not this block) -->
- **Models:** `claude-opus-4.8`, `claude-opus-5`, `claude-opus-5.5`
- **Context:** 1,000,000 tokens, output 128,000.
- **Effort:** `claude-opus-4.8`, `claude-opus-5`: low, medium, high, xhigh, max (default high); `claude-opus-5.5`: low, medium, high, xhigh, max (default medium), cannot be disabled.
- **Sampling:** custom values are ignored.
- **Stated by vendor (`claude-opus-4.8`):** Review prompts that say to report only important issues are followed literally and lower recall. Ask for every finding with confidence and severity, and filter in a later step.
- **Stated by harness (`claude-opus-5.5`):** In Claude Code the opus alias reaches Opus 5.5 on the Anthropic API, Platform on AWS, Bedrock and Agent Platform; only Foundry still reaches Opus 4.6. Pin claude-opus-4-8 by name for Opus 4.8.
- **Stated by vendor (`claude-opus-5.5`):** Effort defaults to medium and a request without it runs a level below Opus 5. Set it explicitly; medium matches Opus 5 at high on coding evals. Re-run sweeps, as levels are recalibrated.
- **Stated by vendor (`claude-opus-5.5`):** Thinking is always on. Disabled and manual budget_tokens return 400, as do forced tool use and non-default temperature, top_p or top_k. Steer cost with effort.
- **Stated by vendor (`claude-opus-5.5`):** It thinks more per turn than Opus 5 at the same effort, most at xhigh and max. Reserve those for measured gains and set max_tokens high, up to 128000 for agentic coding.
- **Stated by vendor (`claude-opus-5.5`):** On long unattended runs it may end a turn with text and no tool call. Treat that as a report, track open items in a checklist, and name the early stops to avoid in the system prompt.
- **Stated by vendor (`claude-opus-5.5`):** Notes between tool calls arrive as thinking blocks, empty under the default display, so a client showing only text goes quiet. Set display updates to render them.
- **Stated by vendor (`claude-opus-5.5`):** Do not ask it to write out its reasoning in the reply; that can be declined as a reasoning_extraction refusal. Read summarized thinking blocks instead.
- **Stated by vendor (`claude-opus-5.5`):** Thinking blocks are bound to the model and conversation. Editing earlier turns or the system prompt can make replayed blocks fail with a 400, so keep history append-only.
- **Stated by harness:** Claude Code loads CLAUDE.md and CLAUDE.local.md from the working directory and every directory above it, root first. AGENTS.md is read only when no CLAUDE.md exists.
- **Facts checked:** 2026-10-07. Sources: `docs/agents/model-facts/families/claude-opus-4.8/FACTS.md`.
<!-- MODEL-FACTS:claude-opus-4.8 END -->
