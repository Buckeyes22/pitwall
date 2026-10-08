# Qwen — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Local / experimental (unranked — pending benchmark) *(seed ranking)*
- **Excels at:** local/experimental, unranked
- **Struggles with:** (not yet benchmarked against the roster — observations pending)
- **Operational caveats:** dispatches through the Qwen Code CLI via `qwen-shim.sh`; point it at any OpenAI-compatible endpoint (local llama.cpp/llama-swap included) with `~/.qwen/.env` (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `QWEN_MODEL` — Qwen Code ≥0.21.14 selects OpenAI-compatible auth when all three are set). The shim never injects `-m`; Qwen Code's own config decides the model unless you pass `--model`. The measured untrimmed agent request carried 64 tool schemas plus a 38,871-character (about 39 KB) system prompt, approximately ~35k tokens before task content. Use the workspace `.qwen/settings.json` `tools.core` allow-list to trim tool schemas and set `QWEN_CODE_MAX_OUTPUT_TOKENS` to leave input headroom; a larger context window alone does not rescue an untrimmed agent
- **Evidence:** seed default — replace with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-08-19 (seed)

<!-- MODEL-FACTS:qwen START (generated from docs/agents/model-facts/families/qwen; edit facts.json, not this block) -->
- **Models:** `qwen3.8-flash`, `qwen3.8-flash-next`, `qwen3.8-27b`, `qwen3.6-27b`, `qwen3.6-35b-a3b`, `qwen3-coder-next`, `qwen3.8-max`, `qwen3.8-2.4t-a95b`
- **Context:** `qwen3.8-flash`, `qwen3.8-max`: 1,000,000 tokens; `qwen3.8-flash-next`, `qwen3.8-27b`, `qwen3.6-27b`, `qwen3.6-35b-a3b`, `qwen3-coder-next`, `qwen3.8-2.4t-a95b`: 262,144 tokens.
- **Effort:** `qwen3.8-flash-next`, `qwen3.8-27b`: xhigh, medium, low (default xhigh); `qwen3.8-2.4t-a95b`: xhigh, medium, low (default xhigh), cannot be disabled.
- **Effort on other values:** `qwen3.8-flash-next`, `qwen3.8-27b`, `qwen3.8-2.4t-a95b`: other values are rejected.
- **Sampling:** `qwen3.8-flash-next`, `qwen3.8-27b`, `qwen3.6-27b`, `qwen3.6-35b-a3b`, `qwen3.8-2.4t-a95b`: temperature 1.0, top_p 0.95, top_k 20; `qwen3-coder-next`: temperature 1.0, top_p 0.95, top_k 40.
- **Shown by artifact:** qwen3.8-max is the hosted release of the open Qwen3.8-2.4T-A95B, adding vision input, non-thinking mode, a 1M window, and built-in tools. The open weights are thinking-only with a 262,144 window.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/qwen/FACTS.md`.
<!-- MODEL-FACTS:qwen END -->
