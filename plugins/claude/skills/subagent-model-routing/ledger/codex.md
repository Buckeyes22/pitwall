# codex GPT-6.2 family (earlier routes retained) — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** the historical seed put GPT-6 Sol in the hardest/critical and deepest-review seat. GPT-6.2 Astra/Sol, GPT-6.1 Terra, and GPT-6 Luna remain unranked until local evidence supports a comparison.
- **Excels at:** hardest implementation, autonomous verify, deepest review
- **Struggles with:** the GPT-5.6 system card's evaluations found a greater tendency than GPT-5.5 to go beyond user intent; prompts must state authorization boundaries and destructive-action constraints explicitly
- **Operational caveats:** GPT-6.2 Astra and Sol are adopted without a local ranking; Terra 6.1 covers visual work and writing. Both 6.2 routes reject none/minimal and accept low through max plus Codex ultra. Preserve the configured default when no selector is supplied. Inspect artifacts and run deterministic checks before accepting completion. Deep multi-file work can approach the 20-minute timeout.
- **Evidence:** the Codex subagents documentation for the GPT-6 roles, the GPT-5.6 System Card (`https://deploymentsafety.openai.com/gpt-5-6`) for the GPT-5.6 evaluations, plus seed defaults — replace routing opinions with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-07-09 (seed)


The generated facts below describe API limits; Codex client efforts, defaults, and windows are described above or in the full reference.

<!-- MODEL-FACTS:codex START (generated from docs/agents/model-facts/families/codex; edit facts.json, not this block) -->
- **Models:** `gpt-6.2-astra`, `gpt-6.2-sol`, `gpt-6.1-terra`, `gpt-6-luna`
- **Context:** 1,050,000 tokens, output 128,000.
- **Effort:** `gpt-6.2-astra`, `gpt-6.2-sol`: low, medium, high, xhigh, max, cannot be disabled; `gpt-6.1-terra`, `gpt-6-luna`: none, low, medium, high, xhigh, max (default medium).
- **Effort on other values:** `gpt-6.2-astra`, `gpt-6.2-sol`: other values are rejected.
- **Stated by vendor (`gpt-6-astra`):** GPT-6 Astra asks a focused question when the answer could change the outcome. Say what a request authorizes and to proceed on reasonable assumptions, or it may stop where you expect it to continue.
- **Stated by vendor (`gpt-6-astra`):** GPT-6 Astra weighs skills and AGENTS.md closely, and conflicting text can make it pause. Audit those files and state that the user's instructions outrank a skill's.
- **Stated by vendor (`gpt-6-astra`):** GPT-6 Astra may delegate to subagents less often than a workflow wants. Say when and how much to split work for parallel subagents.
- **Stated by vendor (`gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`, `gpt-6.1-terra`):** Above none, omit temperature, top_p and log-probability options. Use Responses for tools; only the earlier GPT-6 Sol and Luna accept Chat Completions tools at none.
- **Stated by vendor (`gpt-6.1-sol`):** GPT-6.1 Sol is an earlier lower-cost reasoning option. It has no none or minimal effort (use low in their place), and tool calls need the Responses API.
- **Stated by harness (`gpt-6.2-astra`, `gpt-6.2-sol`):** Choose 6.2 Astra for the hardest reasoning and coding, or 6.2 Sol for everyday agents at lower cost. Move examples to the new IDs; keep the operator's Codex-configured default.
- **Stated by vendor (`gpt-6.2-astra`, `gpt-6.2-sol`):** For a new API integration, start at medium; replace none or minimal with low. Both 6.2 models need Responses for tools; omit sampling and log-probability options.
- **Stated by vendor (`gpt-6.2-astra`, `gpt-6.2-sol`):** Define the finish line and approval boundaries. Let 6.2 state reversible assumptions, keep working through final checks, and ask only for decisions that materially affect the result.
- **Stated by vendor (`gpt-6.2-astra`):** 6.2 Astra is briefer by default: reassess blanket brevity rules. Require final answers to explain the result on their own, without shorthand from tools, reasoning, or progress updates.
- **Stated by vendor (`gpt-6.2-astra`):** State project conventions, preserve caller behavior when reusing logic, and favor readable code. Add tests required by the repository or needed for a specific regression.
- **Stated by vendor (`gpt-6.2-astra`, `gpt-6.2-sol`):** 6.2 can discard prior reasoning after history edits, a model downgrade, or a user or organization change. Append changes and inspect input_transformations when diagnosing lost reasoning.
- **Stated by harness (`gpt-6.2-astra`, `gpt-6.2-sol`, `gpt-6.1-terra`):** Codex 0.169.0 offers low through max plus ultra on both 6.2 models and 6.1 Terra. Ultra enables client delegation; it is not an API effort. runtime-models.json records the reviewed metadata and client defaults.
- **Stated by harness (`gpt-6.1-terra`):** 6.1 Terra handles visual reasoning, computer use, and writing. Codex CLI needs version 0.159.0 or newer; tool calls require Responses.
- **Stated by vendor (`gpt-6-cyber`):** GPT-6 Cyber requires Daybreak Red approval. Record its release and limits without offering an ordinary Codex route; its API listing does not establish account access.
- **Stated by harness:** Codex reads AGENTS.override.md or AGENTS.md from ~/.codex, then one file per directory from the project root down. Later files win, and total size stops at 32 KiB.
- **Facts checked:** 2026-11-17. Sources: `docs/agents/model-facts/families/codex/FACTS.md`.
<!-- MODEL-FACTS:codex END -->
