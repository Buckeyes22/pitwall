# Vendor documentation survey, by page type

Surveyed 2026-09-28. Page types: 1 model page, 2 reasoning/effort, 3 harness, 4 prompting and behaviour, 5 release notes and migration.
"Read" means the page's prose was read; "headings" means only its section list was read.

## OpenAI (Codex harness)
Index: https://developers.openai.com/llms.txt (routes to api/docs, codex, and learn.chatgpt.com indexes). Full export: https://developers.openai.com/api/llms-full.txt
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://developers.openai.com/api/docs/models/gpt-6-astra.md | read | context 1,050,000; max output 128,000; effort low..max; cutoff 2026-04-30; price; one page per model |
| 2 | https://developers.openai.com/api/docs/guides/reasoning.md | read (effort section) | per-effort table; Astra rejects `none` with HTTP 400; Chat Completions has no function calling on Astra |
| 3 | https://learn.chatgpt.com/docs/non-interactive-mode.md, /docs/sandboxing.md, /docs/permission-modes.md, /docs/agent-configuration/agents-md.md, /docs/agent-configuration/subagents.md, /docs/config-file/config-reference.md | headings | `codex exec`, sandbox, approvals, AGENTS.md discovery, subagents, config.toml |
| 3 | https://learn.chatgpt.com/docs/models.md | read (start) | Codex model choice, effort, GPT-5.5 retirement 2026-10-14 |
| 4 | https://developers.openai.com/api/docs/guides/latest-model.md | read in full | behaviour notes plus suggested prompt text; one "Using GPT-X" page per family, old ones kept |
| 4 | https://developers.openai.com/api/docs/guides/prompt-guidance-gpt-5p6.md | not fetched | listed as machine-readable prompting guidance for GPT-5.6 Sol |
| 5 | https://developers.openai.com/api/docs/changelog.md, /api/docs/deprecations.md | fetched, not read | 7,171 and 4,462 words |

## Anthropic (Claude Code harness; used when the host is Codex or Copilot)
Index: https://platform.claude.com/llms.txt (637 entries) and https://code.claude.com/docs/llms.txt
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://platform.claude.com/docs/en/models/fable-5-1/overview.md (one per model) | read | model IDs per platform, price, 1M context, 128K output, thinking always on, default effort `high` |
| 2 | https://platform.claude.com/docs/en/build-with-claude/effort.md | read (per-model sections) | one "Recommended effort levels" section per model; Opus 5.5 defaults to `medium`, earlier Opus to `high` |
| 3 | https://code.claude.com/docs/en/headless.md, cli-reference.md, permissions.md, sandboxing.md, memory.md, sub-agents.md, model-config.md | headings | unattended runs, permission prompts, CLAUDE.md, subagents, model aliases and effort |
| 4 | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1.md (one per model) | read (opening, two sections) | organised as symptom then fix; covers scope creep, test over-production, subagent idling, refusals |
| 4 | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices.md | fetched | cross-model techniques, 8,149 words |
| 5 | https://platform.claude.com/docs/en/models/fable-5-1/whats-new-fable-5-1.md, migration-guide.md; /about-claude/model-deprecations.md; /release-notes/overview.md; code.claude.com changelog.md | read (behaviour differences) | "Behavior differences" lists changed and unchanged behaviour against the previous model; 400 errors for temperature, prefill, thinking disabled |

## xAI (Grok Build harness)
Index: https://docs.x.ai/llms.txt (175 entries). Full export: https://docs.x.ai/llms-full.txt (1.6 MB, 205,406 words). Docs search server: https://docs.x.ai/api/mcp
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://docs.x.ai/developers/grok-4-7.md, /developers/models.md | read in full | 500K context, no output limit, cutoff May 2026, effort low..xhigh default high, encrypted reasoning always returned, aliases |
| 2 | https://docs.x.ai/developers/model-capabilities/text/reasoning.md | read in full | per-model table; reasoning cannot be disabled; grok-4.5 treats xhigh as high; stop and penalties return an error |
| 3 | https://docs.x.ai/build/cli/headless-scripting.md, /build/cli/reference.md, /build/features/permissions.md, sandbox.md, project-rules.md, subagents.md, worktrees.md, hooks.md, /build/settings/reference.md | read (first five) | headless flags, output formats, `--no-auto-update`, sandbox off by default, reads CLAUDE.md, no size cap on rules |
| 4 | not published for Grok 4.7 text work | searched every heading in llms-full.txt, read launch post | guidance exists for multi-agent research and for voice; launch post https://x.ai/news/grok-4-7 has capability claims only |
| 5 | https://docs.x.ai/developers/release-notes.md, /developers/migration/may-15-retirement.md | read (top) | monthly notes, retirement dates |

## Google Gemini (Antigravity harness)
Index: https://ai.google.dev/gemini-api/docs/llms.txt (211 entries; does NOT list the latest-model page) and https://antigravity.google/llms.txt (Antigravity pages serve Markdown at `<url>.md`)
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash.md.txt (one per model) | read in full | input 1,048,576, output 65,536, capabilities table, link to DeepMind model card |
| 2 | https://ai.google.dev/gemini-api/docs/thinking.md.txt | headings | thinking levels, thought signatures, token limits |
| 3 | https://antigravity.google/docs/cli/headless.md, /cli/reference.md, /cli/modes.md, /rules.md, /permissions.md, /sandbox.md, /subagents.md, /models.md | headings | headless output formats, rules files (GEMINI.md, AGENTS.md), sandbox, models by plan |
| 4 | https://ai.google.dev/gemini-api/docs/prompting-strategies.md.txt ("Gemini 3" section) | read | family-level: direct and precise, context first then question, verbosity default; suggested system clauses |
| 4 | https://antigravity.google/docs/cli/best-practices.md | read | harness-level: verification loops, explore-plan-execute |
| 5 | https://ai.google.dev/gemini-api/docs/latest-model.md.txt | read | direct equivalent of OpenAI's page: what's new in 3.8 Flash, default thinking `medium`, migration checklist (`minimal` unsupported, sampling parameters removed) |
| 5 | https://ai.google.dev/gemini-api/docs/changelog.md.txt, /deprecations.md.txt | fetched | |

## Google Gemma 4 (open weight)
Index: https://ai.google.dev/gemma/docs/llms.txt
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://ai.google.dev/gemma/docs/core/model_card_4.md.txt | read (best practices) | variants, benchmarks, limitations |
| 2 | https://ai.google.dev/gemma/docs/capabilities/thinking.md.txt | listed | thinking token in system prompt |
| 3 | none (no vendor harness; served through Pitwall or a model-agnostic harness) | | |
| 4 | model card "Best Practices"; https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4.md.txt | read (card section) | sampling 1.0/0.95/64, no thinking text in history, image before text |
| 5 | not found in index | | |

## Z.ai GLM (no vendor harness; reached through opencode and others on the GLM Coding Plan)
Index: https://docs.z.ai/llms.txt (69 entries)
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://docs.z.ai/guides/llm/glm-5.3.md (one per model) | read (feature changes, how to use) | text only, 1M context, 128K output, three base URLs by protocol, Coding Plan limited to the Chat Completions protocol |
| 2 | https://docs.z.ai/guides/capabilities/thinking-mode.md, /thinking.md | read (start) | reasoning forced on; effort low, high, max, default max; thinking blocks must be returned with tool results |
| 3 | https://docs.z.ai/devpack/overview.md, /devpack/latest-model.md, /devpack/tool/others.md, /devpack/usage-policy.md | headings | how to point Claude Code, Cline and other tools at the plan; usage policy |
| 4 | https://docs.z.ai/devpack/resources/best-practice.md | read (first three sections) | generic coding-agent practice, not specific to GLM-5.3; no model behaviour notes found |
| 5 | https://docs.z.ai/guides/overview/migrate-to-glm-new.md, /release-notes/new-released.md | headings | migration checklist: sampling parameters, forced thinking; `thinking.type: disabled` now fails |

## Moonshot Kimi (Kimi Code CLI harness)
Index: https://platform.kimi.ai/docs/llms.txt (83 entries) and https://moonshotai.github.io/kimi-code/llms.txt (27 English pages)
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://platform.kimi.ai/docs/models.md, /guide/kimi-k3-quickstart.md, /api/models-overview.md | read (models, headings of K3) | model list with deprecations and dates; K3 page has "Important limits" and FAQ |
| 2 | https://platform.kimi.ai/docs/guide/use-reasoning-effort.md, /guide/use-thinking-models.md | read in full (effort) | K3 always reasons; low, high, max, default max; remove K2.x `thinking` config when migrating |
| 3 | https://moonshotai.github.io/kimi-code/en/reference/kimi-command.md, /customization/agents.md, /configuration/config-files.md, /configuration/providers.md, /guides/sessions.md | read (flags) | `--prompt` cannot combine with `--yolo`, `--auto`, `--plan`; non-interactive mode uses `auto` permission; output formats text and stream-json |
| 4 | https://platform.kimi.ai/docs/guide/kimi-k3-tool-calling-best-practice.md, /guide/tool-call-repeat.md | read (tool calling) | K3-specific but narrow: large tool inventories, repeated tool calls |
| 4 | https://platform.kimi.ai/docs/guide/prompt-best-practice.md | headings | generic prompt advice, not K3-specific; no K3 behaviour notes found |
| 5 | https://platform.kimi.ai/docs/platform-changelog.md; Kimi Code changelog.md | fetched | |

## MiniMax (no vendor coding harness; reached through opencode and others on the Token Plan)
Index: https://platform.minimax.io/docs/llms.txt (157 entries)
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://platform.minimax.io/docs/guides/models-intro.md, /guides/text-generation.md | read (invocation) | M3.1-Flash-Preview, M3 (1M context), M2.7; base URLs per protocol |
| 2 | https://platform.minimax.io/docs/guides/text-generation.md ("Thinking"), /api-reference/text-anthropic-api.md | read | effort low..max, default max on M3.1-Flash-Preview; thinking cannot be disabled, `none` returns 400; effort field name differs per protocol; M2.x ignores the setting |
| 3 | https://platform.minimax.io/docs/token-plan/other-tools.md, /token-plan/pi.md, /token-plan/hermes-agent.md, /token-plan/claude-code.md, /token-plan/faq.md | headings | per-tool setup on the Token Plan; ignored Anthropic parameters (top_k, stop_sequences, mcp_servers) |
| 4 | not published | read index in full | tool-use page says the whole assistant message, thinking included, must be returned each turn; no behaviour notes or prompt guidance for M3 |
| 5 | https://platform.minimax.io/docs/release-notes/apis.md | read (top) | stale: newest entry is 2025-10-28, before M3 |

## Meta Muse Spark (Muse Code harness)
Index: https://dev.meta.ai/llms.txt (148 entries)
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://dev.meta.ai/docs/models.md | read | Muse Spark 1.3 is current (Pitwall's registry names 1.2); 1,048,576 context; audio degraded on 1.3; Standard and Contributor tiers |
| 2 | https://dev.meta.ai/docs/reasoning.md | read (start) | none returns HTTP 400; minimal..xhigh; `max` only on Standard-tier 1.3 |
| 3 | https://dev.meta.ai/docs/muse-code.md, /muse-code/configuration.md, /muse-code/permissions.md, /muse-code/extending.md, /muse-code/workflows.md | headings | headless mode, AGENTS.md, model and effort selection, permission profiles, sandbox, subagents |
| 3 | https://dev.meta.ai/docs/coding-agents.md | headings | how OpenCode, Codex and Claude Code connect to Muse Spark |
| 4 | not published for Muse Spark | searched fetched pages | no behaviour notes or prompt guidance for Spark |
| 5 | https://dev.meta.ai/docs/muse-code/changelog.md | fetched | harness changelog, 7,619 words; no model changelog found |

## Meta Muse Glimmer (open weight)
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://dev.meta.ai/docs/muse-glimmer.md; https://huggingface.co/meta-models/Muse-Glimmer-30B | headings | |
| 2 | https://dev.meta.ai/docs/muse-glimmer/prompting.md ("Reasoning and chain-of-thought") | read | `reasoning_strength` low..xhigh, default high |
| 3 | none (no vendor harness) | | |
| 4 | https://dev.meta.ai/docs/muse-glimmer/prompting.md | read (two sections) | chat template, system prompt advice, common pitfalls |
| 5 | not found | | |

## Alibaba Qwen (Qwen Code harness; also Model Studio plans)
Index: https://www.alibabacloud.com/help/en/model-studio/llms.txt (1,003 entries) and https://qwenlm.github.io/qwen-code-docs/llms.txt (Qwen Code pages are HTML only)
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://www.alibabacloud.com/help/en/model-studio/models.md, /text-generation-model.md | read (selection sections) | recommends qwen3.7-plus for coding tools, qwen3.8-max for strongest reasoning; maps GPT, Claude, Gemini tiers to Qwen tiers |
| 2 | https://www.alibabacloud.com/help/en/model-studio/deep-thinking.md | read (supported models) | per-series list: hybrid or thinking-only, default on; `enable_thinking`, `thinking_budget` |
| 3 | https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/, /approval-mode/, /sandbox/, /sub-agents/, /configuration/settings/, /configuration/model-providers/ | headings | headless output formats, approval modes, sandbox, subagents |
| 3 | https://www.alibabacloud.com/help/en/model-studio/token-plan-overview.md, /coding-plan-faq.md | fetched | plan keys, base URLs, 401/403/404 causes |
| 4 | https://www.alibabacloud.com/help/en/model-studio/prompt-engineering-guide.md | headings | generic (COSTAR framework), not per model; no Qwen3.8 behaviour notes found |
| 5 | https://www.alibabacloud.com/help/en/model-studio/release-notes.md (hub for /newly-released-models, /model-release-notes, /model-depreciation) | read (hub) | three sub-pages not fetched |

## DeepSeek (DeepSeek Harness `dsh`, developer preview; also reached through other harnesses)
Index: none. Sitemap: https://api-docs.deepseek.com/sitemap.xml. Pages are HTML only and need conversion.
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | https://api-docs.deepseek.com/quick_start/pricing | read | `deepseek-flash` is now V4.1-Flash, `deepseek-v4-pro` is V4-Pro-0813 (Pitwall's registry names V4-Flash-0731); 1M context, 384K max output, peak and off-peak prices; vision on Flash only |
| 2 | https://api-docs.deepseek.com/guides/thinking_mode | read | thinking on by default at `high`; effort low, high, max; requested minimal maps to low, medium and xhigh map to high |
| 3 | https://deepseek-harness.github.io/deepseek-harness/ ; https://api-docs.deepseek.com/quick_start/agent_integrations/opencode (also claude_code, codex, hermes) | readme read | harness is in developer preview with breaking changes expected |
| 4 | not published | sitemap has no prompting page | temperature page only |
| 5 | https://api-docs.deepseek.com/updates ; /news/news260910 | headings | dated change log; one news post per release |

## Meituan LongCat, Xiaomi MiMo, Tencent Hy (open weight; Pitwall reaches them through OpenCode Go)
No documentation index found for any of the three. Each vendor has a platform site that is a JavaScript application; fetched without a browser it renders zero words, so it was NOT read. A browser pass is needed before calling anything "not published".
| Vendor | Type 1 | Type 2 | Type 4 | Type 5 | Platform site not yet read |
|---|---|---|---|---|---|
| Meituan LongCat 2.0 | https://huggingface.co/meituan-longcat/LongCat-2.0 (read: usage considerations) | card "Chat Template": thinking on, on with history kept, off | not in card | https://longcat.chat/blog/longcat-2.0 (unreachable from here) | https://longcat.ai/ |
| Xiaomi MiMo V2.5 Pro | https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro (read: deployment) | not in card | card: sampling 1.0 / 0.95 only | not in card | https://platform.xiaomimimo.com/ , https://mimo.xiaomi.com/mimo-v2-5-pro |
| Tencent Hy4 preview | https://huggingface.co/tencent/Hy4-preview (read: specifications) | not read | card "Known Limitations" section exists, not read | card "News" | https://aistudio.tencent.com/ , https://hunyuan.tencent.com/ |
Type 3 for all three is OpenCode: see below.

## Harness documentation (type 3 only; these harnesses are not tied to one model)
| Harness | Index | Format | Notes |
|---|---|---|---|
| OpenCode | none (https://opencode.ai/docs/llms.txt is 404) | HTML, converts cleanly | /docs/cli/, /docs/agents/, /docs/permissions/, /docs/rules/ (has a "Claude Code Compatibility" section), /docs/models/ (variants), /docs/go/ (usage limits, validated and known-problematic clients) |
| Cline | https://docs.cline.bot/llms.txt (113 entries) | Markdown | |
| Hermes Agent | https://hermes-agent.nousresearch.com/docs/llms.txt (238 entries) | Markdown | |
| goose | https://goose-docs.ai/llms.txt (3 KB, no page entries) | not checked | |
| Pi | none (https://pi.dev/llms.txt is 404) | HTML at https://pi.dev/docs/latest | |
| DeepSeek Harness | https://deepseek-harness.github.io/deepseek-harness/llms.txt (186 entries) | index text is in Chinese; page language not checked | developer preview |

## Addendum: hosts as a second source (checked 2026-09-28)
A host is a service that serves another vendor's model. Pitwall uses two: Alibaba Model Studio and OpenCode Go.

### Alibaba Model Studio
Searched the English index (1,003 entries) and eleven fetched pages for Xiaomi/MiMo, Tencent/Hunyuan/Hy, Meituan/LongCat.
- Tencent and Meituan: no mention anywhere.
- Xiaomi: one mention, in the index description of the third-party hub ("MiMo (Xiaomi)"). The hub page itself links only DeepSeek, Kimi, GLM, GLM-ZHIPU, MiniMax. No MiMo page exists in the index.
- The Chinese-language site (help.aliyun.com) timed out from this machine and was not read.
Model Studio does publish one page per hosted model for other vendors: /kimi-k3.md, /glm-5-3.md, /deepseek-v4-1-flash.md, /deepseek-v4-pro.md, /minimax-api.md, and a harness page /deepseek-harness.md. Each has Model Capabilities, Context Limits, Pricing, Rate Limits as served by Alibaba. Example: Kimi K3 on Model Studio has web search unsupported and batch unsupported.

### OpenCode Go (https://opencode.ai/docs/go/)
Lists all three vendors with price and monthly limit per model, and names newer models than Pitwall's cards: MiMo-V2.6-Flash, MiMo-V2.6-Pro, LongCat 2.5 Preview Free. Has a "Known Problematic Clients" table that names MiMo Code, Xiaomi's own harness (https://github.com/XiaomiMiMo/MiMo-Code).

## Addendum: Hugging Face as a source (checked 2026-09-28)
29 of the 30 model identifiers Pitwall names resolve on Hugging Face. `minimax/MiniMax-M3` returns 401; the real identifier is `MiniMaxAI/MiniMax-M3`. No account or token was needed for any of the 29.
Fetched per model into a scratch folder, one directory per model: api.json, files.txt, README.md, config.json, generation_config.json, tokenizer_config.json, chat_template.jinja, LICENSE.

| Data | Where | Machine-readable | What it gives |
|---|---|---|---|
| Identity and freshness | `GET /api/models/<id>`: sha, lastModified, createdAt, gated | yes | a commit hash per revision, so a change is detectable without reading anything |
| Licence | card front matter: license, license_name, license_link | yes | 8 models use a vendor licence, not a standard one (Kimi K3, GLM-5.3, MiniMax M3, Qwen3.8-Flash-Next and others) |
| Size | `safetensors.total` | yes | parameter count |
| Context length | config.json or tokenizer_config.json | yes | found for 26 of 29 |
| Sampling defaults | generation_config.json | yes | found for 20 of 29; Hy uses 0.9 / 1.0, most others 1.0 / 0.95, DeepSeek 1.0 / 1.0 |
| Effort values actually accepted | chat_template.jinja | yes, by reading the template | the template is the ground truth; see below |
| Successor | card front matter `new_version`; base_model | yes | only GLM-5.2 sets it (points to GLM-5.3) |
| Newer models from the vendor | `GET /api/models?author=<org>&sort=createdAt` | yes | found DeepSeek-V4.1-Flash (2026-09-10) and five MiMo-V2.6 models (2026-09-21 to 09-27) |
| Who hosts it | `?expand[]=inferenceProviderMapping` | yes | Kimi K3: five hosts; LongCat 2.0: none |
| Usage guidance | README sections | no, prose | varies: Gemma has nine usage sections, MiMo-V2.5-Pro and GLM-5.1 have none |

Effort values read from chat templates:
| Model | Accepted | Default | On any other value |
|---|---|---|---|
| tencent/Hy3 | no_think, low, high | no_think | raises an error |
| tencent/Hy4-preview | no_think, high | high | raises an error; `low` is NOT accepted |
| zai-org/GLM-5.3 | low, high, max | max | silently becomes max |
| Qwen/Qwen3.8-27B | low, medium, xhigh | xhigh | raises an error |
| meta-models/Muse-Glimmer-30B | reasoning_strength | high | |
| meituan-longcat/LongCat-2.0 | enable_thinking, thinking_budget (minimum 1024) | thinking on | |

Behaviour notes found in cards:
- Hy4-preview "Known Limitations": spends longer than necessary reasoning through complex tasks; tends to over-verify its own work.
- Kimi K3 "Model Usage": trained with preserved thinking history; the whole assistant message, reasoning included, must be passed back.
- GLM-5.3 "Note": `clear_thinking` defaults to false; pass `clear_thinking=true` for chat.

## Addendum: Xiaomi, Tencent, Meituan vendor sites (read 2026-09-28 with a browser)
This replaces the "not yet read" row above. All three have documentation sites. None has an index file.

### Xiaomi MiMo — https://mimo.mi.com/docs/en-US/ (renders only in a browser)
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | /quick-start/summary/model | read in full | current models are mimo-v2.6-pro, mimo-v2.6-flash, mimo-v2.6-pro-ultraspeed; 1M context, 128K output, 100 RPM, 10M TPM. **mimo-v2.5 and mimo-v2.5-pro are deprecated at 10:00 Beijing time on 2026-10-21** (Pitwall names both) |
| 2 | /quick-start/usage-guide/text-generation/deep-thinking | read | on or off only, `thinking.type`; no effort levels; on by default; temperature and top_p are ignored while thinking |
| 3 | /integration/claude-code, /tokenplan/integration/codex-configuration; MiMo Code at https://mimo.xiaomi.com/mimocode | not read | Xiaomi has its own harness, MiMo Code |
| 4 | deep-thinking page "Important Notes" | read | reasoning_content must be passed back on every turn that has tool calls, or instruction following drops and hallucination rises; the page names OpenCode and Goose as affected |
| 5 | /updates/model, /updates/deprecate, /news/latest/v2-6 | read | dated release log; V2.6 released 2026-09-22 |

### Tencent Hy — new platform TokenHub, https://cloud.tencent.com/document/product/1823 (Chinese only; renders in a browser)
The older Hunyuan docs at /document/product/1729 list only `hunyuan-*` models and state that new models go to TokenHub. https://aistudio.tencent.com/ does not respond from this network.
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | /document/product/1823/130051 (model list) | read (Hy rows) | hy4-preview: 1M context, 960k input, 64k output. hy3: 256k context, 192k input, 128k output. Both: preserved thinking, structured output, function calling, cache |
| 2 | /document/product/1823/130079 (text generation) | read (matching lines) | thinking support varies by model; interleaved and preserved thinking have their own pages; fields may be ignored or downgraded |
| 3 | /document/product/1823/130069 (OpenCode), /130070 (Claude Code), /130092 (Coding Plan) | read (OpenCode) | base URLs and provider setup |
| 4 | not found on TokenHub | | the Hugging Face card's "Known Limitations" remains the only behaviour note |
| 5 | not found | | |
TokenHub is also a host for other vendors: kimi-k3, kimi-k2.8-preview, glm-5.3, deepseek-v4.1-flash, minimax-m3, mimo-v2.6-pro.

### Meituan LongCat — https://longcat.ai/platform/docs/ (plain HTML, no browser needed)
| Type | Page | Depth | Notes |
|---|---|---|---|
| 1 | /platform/docs/ (quick start) | read | LongCat-2.5-Preview and LongCat-2.0; 1M context, 128K output; endpoints https://api.longcat.ai/openai and /anthropic |
| 2 | /platform/docs/api/chat | read (parameters) | thinking on or off only; temperature range 0 to 1 |
| 3 | /platform/docs/open-code, /hermes-agent, /cline, /claude-code, /codex | fetched | one setup page per tool |
| 4 | not found | | /platform/docs/faq (2,855 words) not read |
| 5 | /platform/docs/change-log | read (top) | LongCat-2.5-Preview released 2026-09-25 |
