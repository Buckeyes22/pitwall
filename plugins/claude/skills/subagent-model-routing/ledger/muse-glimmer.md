# Muse Glimmer — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Local / self-hosted — unranked pending local evidence *(seed ranking)*
- **Excels at:** (not yet benchmarked against the roster — observations pending)
- **Struggles with:** (not yet benchmarked against the roster — observations pending)
- **Operational caveats:** Apache 2.0; 131,072+ context; `Reasoning strength: low|medium|high|xhigh` (default high); `temperature=1.0, top_p=0.95, top_k=64`; served through an OpenAI-compatible endpoint (`vllm/vllm-openai:muse-glimmer --enable-auto-tool-choice --tool-call-parser muse_glimmer --reasoning-parser muse_glimmer`) and routed with `route-shim.sh <name>` on an endpoint route (`qwen` env delivery by default, or `@opencode` after `profiles sync`)
- **Evidence:** https://huggingface.co/meta-models/Muse-Glimmer-30B and https://dev.meta.ai/docs/muse-glimmer/prompting/ — seed defaults; replace routing opinions with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Runtime reference:** [../references/model-prompting.md#muse-glimmer](../references/model-prompting.md#muse-glimmer)
- **Last distilled:** 2026-08-26 (seed)

<!-- MODEL-FACTS:muse-glimmer START (generated from docs/agents/model-facts/families/muse-glimmer; edit facts.json, not this block) -->
- **Models:** `muse-glimmer-30b`
- **Context:** 131,072 tokens.
- **Sampling:** temperature 1.0, top_p 0.95, top_k 64.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/muse-glimmer/FACTS.md`.
<!-- MODEL-FACTS:muse-glimmer END -->
