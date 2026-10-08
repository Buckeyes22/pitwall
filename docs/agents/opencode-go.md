# OpenCode Go subscription models

[OpenCode Go](https://opencode.ai/docs/go/) is a $10/month subscription inside OpenCode that serves a curated list of open and proprietary coding models through the `opencode-go` provider. This page documents how those models route through Agent Routing, which of them are open-weight (and therefore also covered by Pitwall dossiers), and the per-tier operational facts that matter for fan-out decisions. The plan's model list and usage math are owned by Anomaly and change over time; the authoritative live list is `https://opencode.ai/zen/go/v1/models`.

## Connecting

1. Subscribe at [opencode.ai/auth](https://opencode.ai/auth) and copy the Go API key.
2. Run `/connect` in the OpenCode TUI, select **OpenCode Go**, and paste the key.
3. Models appear under the `opencode-go` provider as `opencode-go/<model-id>` (for example `opencode-go/kimi-k3`).

## Routing

`opencode-shim` is the dispatch path. The registry claims the `opencode-go/*` route families for GLM, MiniMax, Kimi, DeepSeek, Qwen, GPT, Grok, Muse Spark, LongCat, MiMo, and Tencent Hy, so bare specs resolve to the opencode harness and pick up the right reference wiring:

```bash
opencode-shim.sh opencode-go/kimi-k3 prompt.md
route-shim.sh opencode-go/hy3 prompt.md
route-shim.sh go-burst prompt.md   # a profile in pitwall.toml with model = "opencode-go/mimo-v2.6-flash"
```

Model-specific prompting follows the family's canonical reference (`prompting/` collection and the bundled `references/model-prompting.md` sections, including the new LongCat, MiMo, and Tencent Hy cards).

## Model table

The 27 models on the plan's published list (September 2026), with their Go model ids, families, and reference wiring. "Open weights" rows are also covered by a Pitwall dossier under `docs/models/` in the monorepo.

| Model | Go model id | Family / reference | Open weights (dossier) |
|---|---|---|---|
| Grok 4.7 / Grok 4.6 | `opencode-go/grok-4.7`, `opencode-go/grok-4.6` | [Grok](../prompting/xai-grok-prompting-reference.md) | no |
| GPT-6 Luna | `opencode-go/gpt-6-luna` | [GPT/Codex](../prompting/openai-codex-gpt-prompting-reference.md) | no |
| GLM-5.3 / GLM-5.3-Flash | `opencode-go/glm-5.3`, `opencode-go/glm-5.3-flash` | [GLM](../prompting/glm-zhipu-prompting-reference.md) | yes (`zai-org/GLM-5.3`, `zai-org/GLM-5.3-Flash`) |
| GLM-5.2 / GLM-5.1 | `opencode-go/glm-5.2`, `opencode-go/glm-5.1` | [GLM](../prompting/glm-zhipu-prompting-reference.md) | yes (`zai-org/GLM-5.2`, `zai-org/GLM-5.1`) |
| Kimi K3 | `opencode-go/kimi-k3` | [Kimi](../prompting/kimi-moonshot-prompting-reference.md) | yes (`moonshotai/Kimi-K3`) |
| Kimi K2.7 Code / K2.6 | `opencode-go/kimi-k2.7-code`, `opencode-go/kimi-k2.6` | [Kimi](../prompting/kimi-moonshot-prompting-reference.md) | yes (`moonshotai/Kimi-K2.7-Code`, `moonshotai/Kimi-K2.6`) |
| LongCat-2.5 Preview / LongCat-2.0 | `opencode-go/longcat-2.5-preview-free`, `opencode-go/longcat-2.0` | [LongCat](../prompting/meituan-longcat-prompting-reference.md) | yes (`meituan-longcat/LongCat-2.0`; self-hosting is SGLang-only on 2 TB-class nodes) |
| MiMo-V2.6 Pro / Flash, MiMo-V2.5 / MiMo-V2.5-Pro | `opencode-go/mimo-v2.6-pro`, `opencode-go/mimo-v2.6-flash`, `opencode-go/mimo-v2.5`, `opencode-go/mimo-v2.5-pro` | [MiMo](../prompting/xiaomi-mimo-prompting-reference.md) | yes (`XiaomiMiMo/MiMo-V2.5`, `XiaomiMiMo/MiMo-V2.5-Pro`) |
| MiniMax M3 / M2.7 | `opencode-go/minimax-m3`, `opencode-go/minimax-m2.7` | [MiniMax](../prompting/minimax-prompting-reference.md) | yes (`MiniMaxAI/MiniMax-M3`, `MiniMaxAI/MiniMax-M2.7` — M2.7 weights are non-commercial licensed) |
| Muse Spark 1.3 / 1.2 Contributor | `opencode-go/muse-spark-1.3-contributor`, `opencode-go/muse-spark-1.2-contributor` | [Muse Code](../prompting/meta-muse-code-prompting-reference.md) | no — Contributor tiers allow prompts/completions to train future Meta models and are region-limited |
| Qwen3.8 Max / Flash | `opencode-go/qwen3.8-max`, `opencode-go/qwen3.8-flash` | [Qwen](../prompting/qwen-alibaba-prompting-reference.md) | Flash only, as `Qwen/Qwen3.8-Flash-Next` (the Go `qwen3.8-flash` tier and the Flash-Next weights are presumed but not confirmed to be the same checkpoint) |
| Qwen3.7 Max / Plus, Qwen3.6 Plus | `opencode-go/qwen3.7-max`, `opencode-go/qwen3.7-plus`, `opencode-go/qwen3.6-plus` | [Qwen](../prompting/qwen-alibaba-prompting-reference.md) | no (API-only tiers) |
| DeepSeek V4.1 Flash / V4 Pro / Flash / Flash Vision Exp | `opencode-go/deepseek-v4.1-flash`, `opencode-go/deepseek-v4-pro`, `opencode-go/deepseek-v4-flash`, `opencode-go/deepseek-v4-flash-vision-exp` | [DeepSeek](../prompting/deepseek-prompting-reference.md) | yes (`deepseek-ai/DeepSeek-V4.1-Flash`, `deepseek-ai/DeepSeek-V4-Pro-0813`, `DeepSeek-V4-Flash-0731`, `DeepSeek-V4-Flash-Vision-Exp`) |
| Hy4 preview / Hy3 | `opencode-go/hy4-preview`, `opencode-go/hy3` | [Tencent Hy](../prompting/tencent-hy-prompting-reference.md) | yes (`tencent/Hy4-preview`, `tencent/Hy3`) |
| Omen Alpha | `opencode-go/omen-alpha` | — (unidentified vendor; no first-party reference — routes as plain pass-through) | no |

`grok-4.5` and other legacy ids may also appear in the live model list; only the published plan list is tabled here.

## Tier facts that change routing decisions

From the plan's published usage math (September 2026; subject to change):

- **Request budgets differ by orders of magnitude.** Examples per 5 hours: Kimi K3 ≈ 110, Grok 4.6 ≈ 169, Qwen3.8 Max ≈ 160, GLM-5.2 ≈ 880, DeepSeek V4 Flash ≈ 7,600, LongCat-2.0 ≈ 11,400, MiMo-V2.5 ≈ 30,100, Muse Spark Contributor ≈ 45,300. Route fan-out accordingly.
- **Muse Spark Contributor trades data for price.** Prompts and completions may train future Meta models; availability follows Meta's Geographic Use Policy. Do not route confidential work there.
- **DeepSeek V4 pricing is peak/off-peak.** Peak windows are 01:00–04:00 and 06:00–10:00 UTC on weekdays.
- **Usage multipliers vary by model** ($15 to $100 of included usage); some models fall back to Zen balance when exhausted if "Use balance" is enabled.

## Pairing caveats

- The GLM-5.3 served by the `zai-coding-plan` provider and the open-weight `zai-org/GLM-5.3` are documented separately; treat checkpoint identity as unverified unless Z.ai states equivalence.
- `Qwen/Qwen3.8-Flash-Next` is the open-weight Flash release; its pairing with the Go `qwen3.8-flash` id is a documented presumption, not a vendor statement.
- Session affinity matters: OpenCode sends `x-opencode-session` automatically; when proxying other clients at the Go endpoints, preserve a stable session header or accept worse prompt-cache hit rates.

## Where the deployability evidence lives

Pitwall dossiers for the open-weight rows live in the monorepo at `docs/models/` (`zai-org--GLM-5.3.md`, `moonshotai--Kimi-K2.6.md`, `meituan-longcat--LongCat-2.0.md`, `XiaomiMiMo--MiMo-V2.5.md`, `MiniMaxAI--MiniMax-M3.md`, `deepseek-ai--DeepSeek-V4-Flash-Vision-Exp.md`, `tencent--Hy3.md`, and siblings). They own serving engines, images, flags, VRAM floors, and licensing evidence; this page and the registry own routing.
