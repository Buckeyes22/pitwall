# Gemini — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Unranked — pending benchmark *(seed ranking)*
- **Excels at:** (not yet benchmarked against the roster)
- **Struggles with:** (observations pending)
- **Operational caveats:** dispatches through Google Antigravity CLI via `agy-shim.sh`; access and model availability are tier-gated. The shim defaults to `gemini-3.8-flash` at medium effort and always adds the dispatch workspace. Gemini 4 Argon (`--model gemini-4-argon`) needs a Google AI Ultra or Enterprise plan in Antigravity. Authentication is interactive unless Gemini API-key configuration is supplied. In restricted headless mode, command permission can be auto-denied; the shim detects the `jetski: no output produced` diagnostic and exits `77` (EX_NOPERM) instead of the CLI's 0. Antigravity also exposes Claude and GPT-OSS slugs only through explicit `<slug>@agy`; Claude slugs are blocked on the Claude host
- **Evidence:** seed default — replace with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-08-27 (seed)

<!-- MODEL-FACTS:gemini START (generated from docs/agents/model-facts/families/gemini; edit facts.json, not this block) -->
- **Models:** `gemini-4-argon`, `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.1-pro`
- **Context:** `gemini-4-argon`, `gemini-3.8-flash`, `gemini-3.1-pro`: 1,048,576 tokens, output 65,536.
- **Effort:** `gemini-4-argon`, `gemini-3.8-flash`: low, medium, high (default medium); `gemini-3.6-flash`: minimal, low, medium, high (default medium); `gemini-3.1-pro`: low, medium, high (default high).
- **Effort on other values:** `gemini-3.8-flash`: other values are rejected.
- **Stated by vendor (`gemini-4-argon`):** A synchronous Gemini 4 Argon request is capped at 65,536 output tokens, and a higher max_output_tokens returns a 400. Longer answers need background execution (long decode).
- **Stated by vendor (`gemini-4-argon`):** Gemini 4 Argon does not support explicit context caching. Remove cachedContents use; implicit caching covers prompts of 4,096 tokens or more.
- **Stated by vendor (`gemini-4-argon`):** Hard Gemini 4 Argon prompts can take several minutes. Give the client at least a 15-minute timeout, or run the request in the background.
- **Stated by vendor (`gemini-3.8-flash`):** Gemini 3.8 Flash spends more tokens on long agentic tasks by design; lower thinking_level to low for everyday work to cut cost.
- **Stated by vendor (`gemini-3.8-flash`):** Do not ask Gemini 3.8 Flash for minimal thinking; the API returns an error. Use low, medium, or high.
- **Facts checked:** 2026-10-16. Sources: `docs/agents/model-facts/families/gemini/FACTS.md`.
<!-- MODEL-FACTS:gemini END -->
