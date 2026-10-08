# Grok 4.5 — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Route:** `pitwall:grok-shim` / `grok-shim.sh <prompt-file>`; available from Claude Code, Codex, and Copilot
- **Tier:** provisional and unranked until local ledger evidence justifies a cross-provider seat
- **Excels at:** agentic coding, tool-using software work, repository inspection, and knowledge tasks through the Grok Build harness
- **Struggles with:** no route-specific local failure pattern is established yet; do not infer reliability or rank from first-party capability claims alone
- **Operational caveats:** `grok-4.7` is the shim default; `--effort low|medium|high|xhigh` controls reasoning depth and xAI documents `high` as the default. The prompt is delivered on argv, unrestricted mode adds `--always-approve`, and Grok Build's sandbox policy remains separate—use explicit boundaries and deterministic checks. Grok 4.6 is available through the OpenCode Go subscription as `opencode-go/grok-4.6` (not through the Grok Build shim); its first-party prompting guidance is the same docs.x.ai family.
- **Evidence:** first-party Grok 4.5, Grok Build CLI, headless-operation, and enterprise-security documentation under `docs.x.ai`, summarized in `../references/model-prompting.md#xai-grok-47-through-grok-build`; replace routing opinions with local observations in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-07-10 (first-party documentation seed)

<!-- MODEL-FACTS:grok START (generated from docs/agents/model-facts/families/grok; edit facts.json, not this block) -->
- **Models:** `grok-4.7`, `grok-4.6`
- **Context:** `grok-4.7`: 500,000 tokens.
- **Effort:** low, medium, high, xhigh (default high), cannot be disabled.
- **Stated by harness:** Grok Build reads CLAUDE.md and AGENTS.md from the repository root down to the working directory, whole and uncapped. Keep those files short; it follows short rules more reliably.
- **Stated by vendor (`grok-4.7`):** Set a prompt_cache_key (x-grok-conv-id on Chat Completions) so a conversation's requests reach one server; without it cache hits are unreliable and input is often billed at full price.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/grok/FACTS.md`.
<!-- MODEL-FACTS:grok END -->
