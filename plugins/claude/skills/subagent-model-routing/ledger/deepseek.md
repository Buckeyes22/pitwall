# DeepSeek V4 — capability card (seed example)

> Seed example — maintain via `/pitwall:distill` and your own ledger.

- **Tier:** Local / self-hosted — unranked pending local evidence *(seed ranking)*
- **Excels at:** (not yet benchmarked against the roster — observations pending)
- **Struggles with:** (not yet benchmarked against the roster — observations pending)
- **Operational caveats:** MIT; 1,048,576 context; `reasoning_effort low|high|max; thinking_mode="thinking"`; `temperature=1.0, top_p=0.95` for agentic runs (`top_p=1.0` otherwise); served through an OpenAI-compatible endpoint (`vllm; DSpark speculative decoding optional`) and routed with `route-shim.sh <name>` on an endpoint route (`qwen` env delivery by default, or `@opencode` after `profiles sync`). Also on OpenCode Go as `opencode-go/deepseek-v4-pro`, `opencode-go/deepseek-v4-flash`, and the experimental vision sibling `opencode-go/deepseek-v4-flash-vision-exp` (image input; peak/off-peak pricing).
- **Evidence:** https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731 and https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813 — seed defaults; replace routing opinions with your own observations via `/pitwall:distill`; observations accumulate in `~/.local/state/pitwall/agents/ledger/observations.jsonl`
- **Runtime reference:** [../references/model-prompting.md#deepseek-v4](../references/model-prompting.md#deepseek-v4)
- **Last distilled:** 2026-08-26 (seed)

<!-- MODEL-FACTS:deepseek START (generated from docs/agents/model-facts/families/deepseek; edit facts.json, not this block) -->
- **Models:** `deepseek-v4.1-flash`, `deepseek-v4-pro`
- **Context:** 1,048,576 tokens, output 384,000.
- **Effort:** `deepseek-v4.1-flash`: low, high, max (default high).
- **Effort on other values:** `deepseek-v4.1-flash`: minimal runs as low, medium runs as high, xhigh runs as high, ultra runs as max; `deepseek-v4-pro`: minimal runs as low, medium runs as high, xhigh runs as high.
- **Sampling:** `deepseek-v4.1-flash`: temperature 1.0, top_p 0.95; `deepseek-v4-pro`: temperature 1.0, top_p 1.0.
- **Stated by vendor (`deepseek-v4.1-flash`):** Call DeepSeek V4.1 Flash on the API as deepseek-flash; it takes image input, unlike V4 Pro, and thinking is on by default at high effort.
- **Shown by artifact (`deepseek-v4.1-flash`):** The API accepts low, high, and max effort; the open weights also take a numeric effort from 1 to 100, which hosted routes do not expose.
- **Shown by artifact (`deepseek-v4.1-flash`):** Give V4.1 Flash at least 256K max_tokens for agentic work; the model card recommends that floor, with temperature 1.0 and top_p 0.95.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/deepseek/FACTS.md`.
<!-- MODEL-FACTS:deepseek END -->
