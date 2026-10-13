# Muse Wonder — capability card (seed)

> Seed card — maintain via `/pitwall:distill` and local ledger evidence.

- **Tier:** Hosted vendor model (preview) — unranked pending local evidence
- **Excels at:** (not yet benchmarked against the roster; Meta's card reports leading DeepSWE 1.1 and SWE-Marathon results)
- **Struggles with:** (observations pending; Meta's card lists visual and spatial reasoning, especially 3D, as a known limitation)
- **Operational caveats:** Meta-hosted, closed weights, reached through Muse Code with `--model muse-wonder-1.0`; the shim default stays `muse-spark-1.3`. Muse Wonder Full is Muse Code only, on the Power Usage and Maximum Usage plans, with no published model id. `META_API_KEY`; prompt delivered by file; exit `2` is a usage error, `130`/`143` signals.
- **Evidence:** Meta's Muse Wonder model card and Model API documentation; replace seed observations with local evidence via `/pitwall:distill`
- **Runtime reference:** [../references/model-prompting.md#muse-wonder](../references/model-prompting.md#muse-wonder)
- **Last distilled:** 2026-10-13 (seed)

<!-- MODEL-FACTS:muse-wonder START (generated from docs/agents/model-facts/families/muse-wonder; edit facts.json, not this block) -->
- **Models:** `muse-wonder-1.0`, `muse-wonder-full`
- **Context:** `muse-wonder-1.0`: 1,048,576 tokens.
- **Effort:** `muse-wonder-1.0`: minimal, low, medium, high, xhigh, max.
- **Effort on other values:** `muse-wonder-1.0`: other values are rejected.
- **Stated by vendor (`muse-wonder-1.0`):** Keep max effort on Muse Wonder for problems where a right answer beats speed, such as a concurrency bug or a large migration plan; it reasons longer and bills more tokens.
- **Stated by vendor:** Reasoning tokens count against max_tokens or max_output_tokens and bill as output, so leave room for reasoning when sizing an output limit.
- **Stated by vendor (`muse-wonder-1.0`):** Meta's coding-agent setup runs Muse Wonder at high effort as a sound default and keeps xhigh and max for deeper reasoning.
- **Facts checked:** 2026-10-13. Sources: `docs/agents/model-facts/families/muse-wonder/FACTS.md`.
<!-- MODEL-FACTS:muse-wonder END -->
