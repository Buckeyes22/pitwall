# Tencent Hy (Hy3 / Hy4-preview) — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Not yet ranked *(seed)*
- **Excels at:** production-grade tool-call stability across coding scaffolds; an explicit `reasoning_effort` dial; cheap Go tiers (Hy3: 4,300 requests per 5 hours)
- **Struggles with:** no first-party English prompting guide; Hy4-preview may be superseded by a final Hy4
- **Operational caveats:** Tencent Hunyuan MoE line, Apache-2.0. Hy3: 295B/21B, 256K context, self-hosts on 8x H200 (FP8 floor 354 GB). Hy4-preview: 770B/49B, 1M context, needs 8x B300 for the FP8 twin. Subscription route: `opencode-go/hy3` / `opencode-go/hy4-preview`. Card sampling: temperature=0.9, top_p=1.0.
- **Evidence:** seed default — replace with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Last distilled:** 2026-09-07 (seed)

<!-- MODEL-FACTS:hy START (generated from docs/agents/model-facts/families/hy; edit facts.json, not this block) -->
- **Models:** `hy3`, `hy4-preview`
- **Context:** `hy3`: 262,144 tokens, output 131,072; `hy4-preview`: 1,048,576 tokens, output 65,536.
- **Effort:** `hy3`: no_think, low, high (default no_think); `hy4-preview`: no_think, high (default high).
- **Effort on other values:** other values are rejected.
- **Sampling:** temperature 0.9, top_p 1, top_k -1.
- **Shown by artifact (`hy4-preview`):** Hy4 preview can reason longer than a task needs and tends to over-verify its own work. Expect slow, verbose answers; bound scope and effort for routine edits.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/hy/FACTS.md`.
<!-- MODEL-FACTS:hy END -->
