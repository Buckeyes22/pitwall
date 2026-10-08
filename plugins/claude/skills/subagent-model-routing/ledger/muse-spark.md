# Muse Spark — capability card (seed)

> Seed card — maintain via `/pitwall:distill` and local ledger evidence.

- **Tier:** Hosted vendor model — unranked pending local evidence
- **Excels at:** (not yet benchmarked)
- **Struggles with:** (not yet benchmarked)
- **Operational caveats:** closed-weights, Meta-hosted Muse Spark (`muse-spark-1.3` by default; `muse-spark-1.2` also available); `--reasoning-effort none…ultra`; `META_API_KEY`; prompt delivered by file; exit `2` is a usage error, `130`/`143` signals. OpenCode Go serves the discounted Contributor tiers (`opencode-go/muse-spark-1.3-contributor`, `opencode-go/muse-spark-1.2-contributor`): prompts and completions may train future Meta models, and availability is limited to regions in Meta's Geographic Use Policy.
- **Evidence:** first-party Meta Muse Code documentation; replace seed observations with local evidence via `/pitwall:distill`
- **Runtime reference:** [../references/model-prompting.md#muse-code](../references/model-prompting.md#muse-code)
- **Last distilled:** 2026-08-26 (seed)

<!-- MODEL-FACTS:muse-spark START (generated from docs/agents/model-facts/families/muse-spark; edit facts.json, not this block) -->
- **Models:** `muse-spark-1.2`, `muse-spark-1.3`
- **Context:** 1,048,576 tokens.
- **Effort:** minimal, low, medium, high, xhigh.
- **Effort on other values:** other values are rejected.
- **Stated by harness (`muse-spark-1.3`):** Muse Code defaults to muse-spark-1.2; pass --model muse-spark-1.3 to reach the current model. Its max effort works on both tiers there.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/muse-spark/FACTS.md`.
<!-- MODEL-FACTS:muse-spark END -->
