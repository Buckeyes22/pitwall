# Claude Fable 5 — capability card (seed example)

> System-card prior — maintain routing confidence via `/pitwall:distill` and your own ledger.

- **Route:** `claude-shim.sh <prompt-file> --model fable` from the Codex and Copilot packages; Claude-hosted work uses native `Agent` calls
- **Tier:** hardest generally available Claude seat; unranked against other providers until local ledger evidence exists
- **Excels at:** frontier-level general coding, long-context agentic work, multimodal and professional tasks; published safeguarded results exceed Opus 4.8 on several coding benchmarks
- **Struggles with:** high-risk biology and cybersecurity requests can be blocked or fall back to Opus 4.8 without notice; external testing found that it sometimes over-justified borderline multi-agent orchestration choices
- **Operational caveats:** production safeguards are part of the route, so benchmark and local behavior may combine the underlying Fable path with fallback. Use narrow authorization boundaries, confirmation gates for destructive/external actions, and deterministic verification. This repository intentionally has no Mythos route/card.
- **Evidence:** official [Claude Fable 5 & Claude Mythos 5 System Card](https://www.anthropic.com/claude-fable-5-mythos-5-system-card) (June 9, 2026) via the Fable-only runtime guidance in `../references/model-prompting.md#claude-fable-51`
- **Last distilled:** 2026-07-10 (system-card seed)

<!-- MODEL-FACTS:claude-fable-5 START (generated from docs/agents/model-facts/families/claude-fable-5; edit facts.json, not this block) -->
- **Models:** `claude-fable-5`, `claude-fable-5.1`, `claude-mythos-5.1`
- **Context:** 1,000,000 tokens, output 128,000.
- **Effort:** low, medium, high, xhigh, max (default high), cannot be disabled.
- **Sampling:** custom values are ignored.
- **Stated by vendor (`claude-fable-5`):** On long runs, tell it to check each progress claim against a tool result from the session and to say plainly what is unverified; this nearly eliminated fabricated status reports.
- **Stated by harness (`claude-fable-5.1`):** In Claude Code the fable alias reaches Fable 5.1, except in Claude apps gateway sessions where it stays on Fable 5. Select claude-fable-5 by name to get Fable 5.
- **Stated by vendor (`claude-fable-5.1`):** Start at high, the default. At medium it roughly matches Fable 5 at lower cost, and at low it often rivals Opus and Sonnet models on cost per task. Re-run sweeps, as levels are recalibrated.
- **Stated by vendor (`claude-fable-5.1`):** Thinking is always on. Disabled and budget_tokens return 400, as do forced tool use and non-default sampling parameters. Steer cost with effort.
- **Stated by vendor (`claude-fable-5.1`):** It writes fewer updates during long tool runs. Set thinking display to updates to receive them, remove lines that hold findings for the end, and ask for a standalone recap.
- **Stated by vendor (`claude-fable-5.1`):** On autonomous runs it may announce a next step instead of doing it, or ask permission for work already requested. Tell it nobody can answer and to proceed on reversible steps.
- **Stated by vendor (`claude-fable-5.1`):** At xhigh and max it may draft a long deliverable in thinking and again in the reply. Prefer high for such requests and set max_tokens for thinking plus reply.
- **Stated by vendor (`claude-fable-5.1`):** At low effort it searches less and answers from memory. Raise effort for turns needing fresh facts, or add a nudge to verify with a search tool.
- **Stated by vendor (`claude-fable-5.1`):** It tends to rewrite a whole file for a small change, costing output tokens. Ask for targeted edits and to keep changes and tests to what the task asks.
- **Stated by vendor (`claude-fable-5.1`):** Thinking blocks are bound to the model and conversation, and earlier models cannot read them. Editing earlier turns can fail with a 400, so keep history append-only.
- **Stated by vendor (`claude-mythos-5.1`):** Mythos 5.1 is the same model, specs and price as Fable 5.1 under the id claude-mythos-5-1. It is open only to organizations verified through an Anthropic program, such as the Cyber Verification Program; apply to the program that fits.
- **Stated by harness:** Claude Code loads CLAUDE.md and CLAUDE.local.md from the working directory and every directory above it, root first. AGENTS.md is read only when no CLAUDE.md exists.
- **Facts checked:** 2026-10-07. Sources: `docs/agents/model-facts/families/claude-fable-5/FACTS.md`.
<!-- MODEL-FACTS:claude-fable-5 END -->
