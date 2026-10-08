# Gemma 4 — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Local / self-hosted — unranked pending local evidence *(seed ranking)*
- **Excels at:** (not yet benchmarked against the roster — observations pending)
- **Struggles with:** (not yet benchmarked against the roster — observations pending)
- **Operational caveats:** Apache 2.0; 256K context (131,072 on E-series); `<|think|>` token at the start of the system prompt enables thinking; `temperature=1.0, top_p=0.95, top_k=64`; served through an OpenAI-compatible endpoint (`vllm or llama.cpp; E-series context is 131072`) and routed with `route-shim.sh <name>` on an endpoint route (`qwen` env delivery by default, or `@opencode` after `profiles sync`)
- **Evidence:** https://ai.google.dev/gemma/docs/core/model_card_4 and https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/ — seed defaults; replace routing opinions with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Runtime reference:** [../references/model-prompting.md#gemma-4](../references/model-prompting.md#gemma-4)
- **Last distilled:** 2026-08-26 (seed)

<!-- MODEL-FACTS:gemma START (generated from docs/agents/model-facts/families/gemma; edit facts.json, not this block) -->
- **Models:** `gemma-4-31b-it`, `gemma-4-31b`, `gemma-4-26b-a4b`, `gemma-4-12b`, `gemma-4-e4b`, `gemma-4-e2b`
- **Context:** `gemma-4-31b-it`, `gemma-4-31b`, `gemma-4-26b-a4b`, `gemma-4-12b`: 262,144 tokens; `gemma-4-e4b`, `gemma-4-e2b`: 131,072 tokens.
- **Sampling:** temperature 1.0, top_p 0.95, top_k 64.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/gemma/FACTS.md`.
<!-- MODEL-FACTS:gemma END -->
