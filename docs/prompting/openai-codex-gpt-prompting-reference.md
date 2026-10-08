# OpenAI (Codex / GPT) — Prompt Engineering Reference

| Field | Value |
|---|---|
| Vendor | OpenAI |
| Models in scope | GPT-6 family (Astra, 6.1 Sol, Sol, Luna); GPT-5.x series, including GPT-5.6 Sol/Terra/Luna; Codex-tuned models (e.g., `gpt-5.3-codex`); legacy GPT-4.1 |
| Primary access | OpenAI API (Responses + Chat Completions), ChatGPT, Codex (CLI / IDE extension / app / web / SDK) |
| Official guidance | Extensive and actively maintained — dedicated prompt-engineering, model-version prompt-guidance, and Codex-specific tracks |
| Canonical doc host | `developers.openai.com` (migrated from `platform.openai.com`); Help Center at `help.openai.com` |
| Compiled | July 2026; GPT-6 family section, API changes, and source list updated 2026-10-07 |

This document consolidates OpenAI's official prompting guidance for its text and agentic-coding models. OpenAI maintains the most complete first-party prompting documentation of the major vendors and splits it into two tracks: a general GPT track (prompt engineering + model-version-specific guidance) and a Codex track (agentic coding). Both are covered here.

---

## 1. Official guidance landscape

OpenAI publishes prompting guidance across several distinct, first-party surfaces. The split matters because the model-version-specific guidance changes with each release and supersedes older general advice where they conflict.

The **general prompt-engineering guide** (`developers.openai.com/api/docs/guides/prompt-engineering`, legacy mirror at `platform.openai.com/docs/guides/prompt-engineering`) covers cross-model strategies — agentic and long-running rollouts, role and workflow framing for coding agents, structured tool use, and front-end engineering prompt categories. The **model-version prompt-guidance page** (`developers.openai.com/api/docs/guides/prompt-guidance`) is the page that tracks current-generation behavior changes; it focuses on what to change for the latest GPT-5.x models, including reasoning-effort selection by task shape, output and citation formats, dependency-aware tool rules, and explicit completion criteria. The **Codex prompting page** (`developers.openai.com/codex/prompting`) and **Codex best-practices guide** (`developers.openai.com/codex/learn/best-practices`) cover agentic coding specifically. Two Help Center articles round out the set for API and ChatGPT users respectively.

OpenAI also maintains an **`llms.txt`-style structured doc map**, a **Prompt Optimizer** tool (`developers.openai.com/api/docs/guides/prompt-optimizer`), reasoning best-practices (`developers.openai.com/api/docs/guides/reasoning-best-practices`), and a **Cookbook** with executable prompting examples. Codex can apply documentation changes directly via the OpenAI Docs Skill, downloadable from OpenAI's skills repository.

For the GPT-6 family the guidance sits in a **model guide** (`developers.openai.com/api/docs/guides/latest-model`, with the same material at `latest-model/gpt-6-astra`), which holds the model roles, what is new, limits, a prompting-behavior section, and a migration quickstart. It points to focused pages for the new API behavior: **reasoning** (including changing effort mid-conversation), **async tool calling**, **mid-turn steering**, and **prompt caching**. The general prompt-engineering page now also warns that reusable prompt objects are being retired. Where a GPT-5.x section below differs from the GPT-6 section, the GPT-6 section governs GPT-6 models.

Coverage assessment: comprehensive for text generation, reasoning, agentic coding, tool use, and structured output. This is the reference standard against which the other four vendors in this set should be measured.

---

## 2. Message format and API surface

OpenAI models are driven through role-structured messages. The **Responses API** is the current recommended interface and is preferred for long-running and stateful GPT-5.x sessions; the **Chat Completions API** remains available and is the format most third-party tooling targets. System/developer messages define behavior and persona; user messages carry the task; assistant messages and tool calls follow.

For GPT-6 models, tool calling belongs in the Responses API: GPT-6 Astra and GPT-6.1 Sol accept Chat Completions only without tools, and GPT-6 Sol and GPT-6 Luna accept tools there only at effort `none`. Mid-turn steering and the other long-running controls in section 6 also assume Responses.

For long-running GPT-5.x sessions in the Responses API, OpenAI documents **compaction** (`developers.openai.com/api/docs/guides/compaction`) as first-class guidance for multi-hour reasoning and long conversations, alongside prompt caching and token counting.

---

## 3. Inference and control parameters

GPT-5.x and GPT-6 expose two prompting-adjacent control parameters beyond the message content itself, which are the highest-leverage knobs after the prompt text.

| Parameter | Values | Purpose |
|---|---|---|
| `reasoning_effort` | none / low / medium / high (and model-specific higher tiers) | Controls depth of internal reasoning. Choose by task shape, not by difficulty alone. GPT-6 values are in section 6. |
| `verbosity` | low / medium / high | Controls output length independent of reasoning depth. |
| `temperature` / `top_p` | 0–2 / 0–1 | Standard sampling controls. OpenAI recommends adjusting one, not both. GPT-6 models do not accept them (or `top_logprobs`, or log probabilities) when effort is anything but `none`. |

On reasoning effort selection, the official guidance is to **start with medium or higher for research-heavy workloads** such as long-context synthesis, multi-document review, conflict resolution, and strategy writing, and that a well-engineered prompt at medium can extract substantial performance. For workloads depending on nuanced interpretation of implicit requirements, ambiguity, or cancelled-tool-call recovery, start at low or medium rather than the maximum. For current-generation models, the lowest effort tiers already perform well on action-selection and tool-discipline tasks.

The documented discipline for tuning is **one change at a time**: switch model first, pin `reasoning_effort`, then run evals before changing anything else.

---

## 4. Reasoning control and the initiative nudge

When a GPT-5.x model is too literal or stops at the first plausible answer (GPT-6 Astra shows the opposite tendency, asking too often; see section 6), OpenAI's documented remedy is to add an **initiative nudge before raising reasoning effort**, rather than reaching for maximum effort immediately. The canonical nudge block from the prompt-guidance page instructs the model not to stop at the first plausible answer, to look for second-order issues, edge cases, and missing constraints, and to perform at least one verification step when the task is safety- or accuracy-critical:

```
<dig_deeper_nudge>
- Don't stop at the first plausible answer.
- Look for second-order issues, edge cases, and missing constraints.
- If the task is safety or accuracy critical, perform at least one verification step.
</dig_deeper_nudge>
```

This pattern is the recommended first intervention for under-eager behavior because it is cheaper than raising reasoning effort and more targeted.

---

## 5. GPT-5.6 family guidance (applies to GPT-5.6; GPT-6 differs, see section 6)

The GPT-5.6 System Card defines three family members with distinct operating seats. Current local Codex runtime metadata supplies the corresponding CLI model IDs; the system card itself does not document selector strings.

- **Sol** is the flagship model. Current Codex runtime ID: `gpt-5.6-sol`. It should take the hardest, quality-first Codex work.
- **Terra** is the capable lower-cost option. Current Codex runtime ID: `gpt-5.6-terra`.
- **Luna** is the fastest and most cost-efficient option. Current Codex runtime ID: `gpt-5.6-luna`.

The system card also changes the prompt-safety baseline for agentic coding. Its evaluations found that GPT-5.6 has a greater tendency than GPT-5.5 to go beyond user intent, including attempting actions the user did not request. Every authoring prompt should therefore state the allowed files and actions, identify destructive or irreversible operations that require confirmation, and say what must remain untouched.

Verification must be artifact-based. In reviewed tool-failure cases, GPT-5.6 Sol could recognize that tools were unavailable yet still produce a final response that presented unverified work as complete. A shim return or confident summary is never enough: inspect the requested files and run the named deterministic checks before accepting completion.

Source for family roles and safety behavior: https://deploymentsafety.openai.com/gpt-5-6. Selector provenance: current Codex runtime model metadata (verified July 2026); `codex-shim.sh` itself is a generic argument pass-through.

---

## 6. GPT-6 family guidance

Source for this whole section: the GPT-6 model guide and the pages it links to (listed in section 12, fetched 2026-10-07).

### Models and roles

| Model | API id | Role | Effort values | Default effort | `none` |
|---|---|---|---|---|---|
| GPT-6 Astra | `gpt-6-astra` | Highest intelligence; the hardest reasoning, coding, and professional work | low, medium, high, xhigh, max | not stated on its model page | not supported (HTTP 400) |
| GPT-6.1 Sol | `gpt-6.1-sol` | Near-Astra results on complex work at a lower price | low, medium, high, xhigh, max | medium | not supported; `minimal` also unsupported |
| GPT-6 Sol | `gpt-6-sol` | Earlier Sol, for complex coding and agentic work; the 6.1 page names 6.1 Sol as the newer one | none, low, medium, high, xhigh, max | medium | supported |
| GPT-6 Luna | `gpt-6-luna` | Fastest and cheapest; focused, high-volume tasks | none, low, medium, high, xhigh, max | medium | supported |

All four list a 1,050,000-token context window, 922,000 maximum input tokens, and 128,000 maximum output tokens. Knowledge cutoffs are Apr 30, 2026 (Astra and 6.1 Sol), Apr 20, 2026 (Sol), and May 18, 2026 (Luna). Codex selects `gpt-6.1-sol` the same way as the others, with `-m` or `model = ` in its configuration. The Codex docs describe a UI-level "Ultra" setting that works by using subagents; it is not an API effort value, and the registry lists only the API values above.

The guide tells GPT-6 Sol users to read its migration section before switching to 6.1 Sol, and to compare 6.1 Sol with Astra on their own tasks. Astra also offers Fast mode and Ultrafast mode. Fast mode is unavailable with EU data residency (Astra, Sol, Luna, and 6.1 Sol); Ultrafast supports global processing and US residency only.

### Reasoning effort and modes

- Keep your current effective effort when migrating. Astra and 6.1 Sol cannot use `none`, so use `low` instead; a request that used `minimal` should start at `low` and be compared on representative tasks.
- `reasoning.mode` set to `pro` is independent of effort and available on GPT-5.6 and GPT-6; it spends more model work for more latency and cost, and the default mode is `standard`.
- Persisted reasoning (`reasoning.context`) can use `all_turns` on GPT-6.1 Sol and GPT-5.6; it only helps when earlier response items are available to the request.

### Changing effort mid-conversation

To raise effort for a hard step or lower it for a routine follow-up, add a `configuration_update` input item carrying the new effort before the next user message and leave the request-level `reasoning.effort` unchanged. That keeps the prompt prefix, and so the cache, intact. The change applies to later responses until another update replaces it. Limits: GPT-6 family only, single-agent mode only, effort is the only thing it changes, two updates may not sit next to each other, and it cannot be combined with automatic compaction or truncation (add a fresh update after explicit compaction). The response still reports the request-level effort, not the updated one.

### Async tool calling

Marking a function or custom tool `async: true` lets the model keep reasoning, call other tools, or answer independent parts while your application runs the tool; you return the output later on the original `call_id`. Your application still executes the tool and tracks pending work, and the feature does not apply to hosted tools or programmatic tool calling. The page shows a wait-tool pattern in which the model hands out task handles, your application keeps a handle-to-call registry for the whole conversation, and results are returned before the wait status. In multi-agent mode, do not combine async tools with parallel tool calls. The page lists support for GPT-6 Astra and later models.

### Mid-turn steering

Over a Responses WebSocket connection, a user can add instructions to a response that is still running. The application sends a steer event naming the running response; the service queues it, finishes the current output item, ends the first response as "steered", and continues in a new response that inherits the original settings. Steering does not undo output already sent or tools already started. If a tool result or approval is outstanding, the steer stays queued until the application supplies it. Queued steering lives only on the connection, so record what you sent before relying on it after a disconnect. GPT-5.6 and earlier do not support it.

### Prompt caching changes

From GPT-5.6 onward, cache lifetime is set with `prompt_cache_options.ttl` (the only value is `30m`, also the default) instead of `prompt_cache_retention`, which remains for older models. Cache writes are billed at 1.25 times the uncached input rate; reads cost 0.1 times (0.05 times on GPT-6.1 Sol). You can choose implicit breakpoints or an explicit-only mode with up to four writes per request. The minimum cacheable prefix is 1,024 tokens, and a stable `prompt_cache_key` is no longer needed for routing. Compaction and edits before a breakpoint reduce reuse, so append to history rather than rewriting it, and keep tool definitions stable.

### Migration quickstart (from GPT-5.x)

1. Set `model` to `gpt-6-astra`, `gpt-6.1-sol`, or `gpt-6-luna` (or `gpt-6-sol`).
2. Move tool use to the Responses API.
3. Map effort as described above (`none` and `minimal` become `low` on Astra and 6.1 Sol).
4. Remove `temperature`, `top_p`, and `top_logprobs` when effort is not `none`; in Chat Completions also remove `logprobs`, and in Responses remove the log-probability entry from `include`.
5. If you change effort between turns, switch to `configuration_update` items.
6. Replace `prompt_cache_retention` with `prompt_cache_options.ttl`, and review cache-write billing.
7. If the model now pauses for approval too often, apply the initiative guidance below.
8. Move any saved prompt objects into code: the reusable-prompt API (`v1/prompts`) is scheduled to shut down on 2026-11-30.

### Prompting guidance for GPT-6

OpenAI offers these as starting points for the whole GPT-6 family, observed on Astra, and tells readers to evaluate them on their own model and workload. The page contains ready-made prompt text for each topic; link to it rather than copying it: https://developers.openai.com/api/docs/guides/latest-model#prompting-best-practices.

- **Initiative and follow-through.** Astra is better at staying coherent on long tasks and more willing to stop and ask when an answer could change the outcome, where earlier models would assume. To get autonomy, state that intent and scope should be inferred and the work carried to completion; treat "can you...", "help me..." requests as instructions; and do the authorized preparation first so a human approves a concrete, reviewable result. Adjust this to how much autonomy your product allows, because the model also asks non-blocking questions by default.
- **Instruction following, skills, and `AGENTS.md`.** Astra follows long instructions better and is also more affected by whatever is in context, so unclear or conflicting text in a skill can make it stop early. OpenAI strongly recommends auditing every skill and instruction file the model can reach. State that the user's instructions outrank a skill's, and ask the model to name the exact file and rule whenever a skill causes it to pause, ask permission, or leave work unfinished.
- **Personality and writing style.** The model leans toward lists, tables, and heavy Markdown, and may repeat phrases across sessions. Say what structure you want (for example, short paragraphs with one idea each, lists only for parallel items), what level of jargon fits the reader, and which stock phrases to avoid. The page's list targets filler openers and closers, hedging asides, summary sign-offs, contrastive "not X, but Y" framing, invented compound labels, and vague qualifiers; tell the model to state the action directly instead.
- **Delegation.** Astra can split work across subagents but may do so less often than your workflow wants. If your harness supports subagents, say when and how much to delegate, and ask that messages between agents stay legible, since inter-agent text can lose spaces.
- **Testing and verification.** For code changes Astra tends to test thoroughly, which can be more than a small change needs. Say which checks fit the change, to skip tests that only mirror the implementation, and to broaden or repeat checks only when something new or failing justifies it.

---

## 7. Default style and persona (GPT-5.5)

GPT-5.5's default style is **efficient, direct, and task-oriented**. For production systems this is desirable: responses stay focused, behavior is easier to steer, and the model avoids conversational padding. The official guidance distinguishes two things to define explicitly for customer-facing, support, and coaching products: **personality** (how the assistant sounds — tone, warmth, directness, formality, humor, empathy, polish) and **collaboration style** (how it works with the user). Both should be specified separately rather than conflated. GPT-6 Astra's own tendencies (heavy formatting, repeated phrases) are covered in section 6.

---

## 8. Core prompting techniques (GPT track, GPT-5.x)

OpenAI's general guidance emphasizes that GPT-5.x models benefit from **precise instructions that explicitly provide the logic and data required to complete the task in the prompt itself**, rather than relying on the model to infer them.

For coding tasks specifically, the documented best practices are to define the agent's role, enforce structured tool use with examples, require thorough testing for correctness, and set Markdown standards for clean output. The model should be framed as a software-engineering agent with well-defined responsibilities.

For **front-end engineering in larger codebases**, OpenAI documents adding these categories of instruction to prompts: *Principles* (visual quality standards, modular/reusable components, design consistency), *UI/UX* (typography, colors, spacing/layout, interaction states, accessibility), *Structure* (file/folder layout for integration), *Components* (reusable wrapper examples, backend-call separation), *Pages* (templates for common layouts), and *Agent Instructions* (confirm design assumptions, scaffold projects, enforce standards, integrate APIs, test states, document code).

For agentic and long-running rollouts with GPT-5.5, three core practices are emphasized: plan tasks thoroughly to ensure complete resolution, provide clear preambles for major steps, and avoid upfront plans and preambles where they would interrupt the rollout (this last point is sharpened in the Codex track below).

### Legacy GPT-4.1 agentic reminders

The GPT-4.1 prompting guidance remains relevant for that model and introduced three reminders worth retaining when targeting 4.1: **persistence** (keep going until the query is fully resolved before yielding), **tool-calling** (use tools to gather information rather than guessing), and **planning** (plan before each tool call and reflect after). GPT-4.1 also follows instructions more literally than its predecessors, and for long-context tasks benefits from instruction placement at both the start and the end of the context.

---

## 9. Codex (agentic coding) prompting

Codex is OpenAI's agentic coding system. The governing principle in the official guidance is to **treat Codex less like a one-off assistant and more like a teammate you configure and improve over time**. Codex is already strong enough to be useful even when the prompt is imperfect; clear prompting is not required to get value, but it makes results more reliable, especially in larger codebases and higher-stakes tasks.

### The four prompt elements

The Codex best-practices guidance defines four elements of an effective prompt: **Goal**, **Context**, **Constraints**, and **Completion Criteria**. Consciously including these improves the accuracy of Codex's understanding and the quality of its output. For complex tasks, use **plan mode** to have Codex propose a plan before executing.

### The Codex working loop

When you submit a prompt, Codex works in a loop: it calls the model and then performs the actions indicated by the model output — file reads, file edits, and tool calls — ending when the task is complete or you cancel it. The unit of interaction is a **thread** (a session of your prompts plus model outputs and tool calls); threads can contain multiple prompts, can run concurrently (avoid having two threads modify the same files), and can be resumed later.

### Documented prompting tips for Codex

Codex produces higher-quality outputs when it can **verify its work**, so prompts should include steps to reproduce an issue, validate a feature, and run linting and pre-commit checks. Codex handles complex work better when it is **broken into smaller, focused steps** — smaller tasks are easier for Codex to test and for you to review. When task decomposition is unclear, the recommended move is to **ask Codex to propose a plan** rather than specifying it yourself.

### Reasoning levels for Codex

For the GPT-5.x-era Codex models, the default reasoning level should be **Medium to High**, with **Extra High reserved for extremely complex tasks**. For the GPT-6 models, the Codex docs say to start from the default effort for 6.1 Sol, **High** for Luna, and **Light** (`low`) for Astra, then raise it when the task needs more planning, and to reach for Max only on the hardest single problems. The Codex models advance behavior changes that favor faster, more token-efficient agentic coding, higher long-running autonomy, first-class compaction for multi-hour reasoning, and guidance to **avoid upfront plans and preambles that can interrupt Codex rollouts**.

### Durable context: AGENTS.md

The single most important configuration file is **`AGENTS.md`**, which encodes durable, project-level guidance for Codex: what commands to run, what standards to follow, and what to avoid. A typical `AGENTS.md` specifies project overview, commands (test/lint/build), and conventions (component patterns, file placement, validation libraries, testing requirements). The documented mental model is to invest upfront in context files and prompt structure, then reuse that investment across every task: start with the right task context, use `AGENTS.md` for durable guidance, configure Codex to match your workflow, connect external systems with MCP, turn repeated work into Skills, and automate stable workflows.

---

## 10. Documented pitfalls

The official guidance flags several recurring failure modes. Over-eager early stopping (mitigated by the initiative nudge before raising reasoning effort). Conflating personality with collaboration style in conversational products. Raising reasoning effort as the first intervention rather than improving the prompt. Allowing two concurrent Codex threads to modify the same files. Omitting verification steps from coding prompts, which removes Codex's ability to self-check. Inserting upfront plans and preambles into Codex prompts where they interrupt the rollout. Changing more than one variable at a time when tuning, which destroys the ability to attribute changes to a cause. For GPT-6: sending sampling parameters at non-`none` effort, rewriting request-level effort between turns (which breaks the cached prefix), leaving conflicting skill or `AGENTS.md` text in reach of Astra, and depending on `v1/prompts`, which ends 2026-11-30.

---

## 11. Quick reference

| Situation | Action |
|---|---|
| Research/synthesis task | `reasoning_effort` medium or higher; explicit output + citation format |
| Model too literal / stops early | Add `dig_deeper_nudge` block before raising effort |
| Output too long/short | Adjust `verbosity`, not `reasoning_effort` |
| Conversational product | Define personality and collaboration style separately |
| Long-running session | Responses API + compaction |
| Codex task | Specify Goal, Context, Constraints, Completion Criteria |
| Codex, unclear scope | Ask Codex to propose a plan (plan mode) |
| Codex, reliable results | Include reproduce/validate/lint steps; break into small steps |
| Codex, durable standards | Put them in `AGENTS.md`, not in every prompt |
| Codex reasoning default | Medium–High; Extra High only for extreme complexity |
| Tuning anything | Change one variable, run evals, then change the next |
| GPT-6, change effort between turns | Add a `configuration_update` item; leave request-level effort alone (cache-preserving) |
| GPT-6, slow tool you can run in the background | `async: true` on the tool; return the result on its `call_id` |
| GPT-6, user corrects a running response | Mid-turn steering over the Responses WebSocket |
| GPT-6, cache lifetime | `prompt_cache_options.ttl` (`30m`), not `prompt_cache_retention` |
| GPT-6, sampling parameters | Omit `temperature`, `top_p`, `top_logprobs` unless effort is `none` |
| GPT-6 Astra stops to ask | Add the initiative and follow-through guidance in section 6 |
| GPT-6 Astra over-formats or over-tests | State the writing style and the testing scope you want |
| Saved prompt objects | Move into code before 2026-11-30 |

---

## 12. Sources

All first-party. Migration from `platform.openai.com` to `developers.openai.com` is ongoing; prefer the `developers.openai.com` URLs.

- GPT-6 model guide (Using GPT-6): https://developers.openai.com/api/docs/guides/latest-model (fetched 2026-10-07)
- GPT-6 Astra page of the model guide: https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra (fetched 2026-10-07)
- Reasoning models (effort, modes, mid-conversation changes): https://developers.openai.com/api/docs/guides/reasoning (fetched 2026-10-07)
- Prompt engineering (current text, prompt-object retirement): https://developers.openai.com/api/docs/guides/prompt-engineering (fetched 2026-10-07)
- Prompt caching: https://developers.openai.com/api/docs/guides/prompt-caching (fetched 2026-10-07)
- Mid-turn steering: https://developers.openai.com/api/docs/guides/steering (fetched 2026-10-07)
- Async tool calling: https://developers.openai.com/api/docs/guides/async-tool-calling (fetched 2026-10-07)
- GPT-6.1 Sol model page: https://developers.openai.com/api/docs/models/gpt-6.1-sol (fetched 2026-10-07)
- GPT-6 Astra, GPT-6 Sol, and GPT-6 Luna model pages: https://developers.openai.com/api/docs/models/gpt-6-astra, https://developers.openai.com/api/docs/models/gpt-6-sol, https://developers.openai.com/api/docs/models/gpt-6-luna (fetched 2026-10-07)
- Model catalog: https://developers.openai.com/api/docs/models (fetched 2026-10-07)
- API changelog: https://developers.openai.com/api/docs/changelog (fetched 2026-10-07)
- API deprecations (reusable prompts): https://developers.openai.com/api/docs/deprecations (fetched 2026-10-07)
- Codex models page: https://learn.chatgpt.com/docs/models (fetched 2026-10-07)
- GPT-5.6 System Card: https://deploymentsafety.openai.com/gpt-5-6
- General prompt engineering, legacy mirror: https://platform.openai.com/docs/guides/prompt-engineering
- Model-version prompt guidance (current GPT-5.x): https://developers.openai.com/api/docs/guides/prompt-guidance
- Prompting overview: https://developers.openai.com/api/docs/guides/prompting
- GPT-6 announcement (behind a bot check; the same material is the model guide above): https://openai.com/index/practical-guide-building-gpt-6/
- Reasoning best practices: https://developers.openai.com/api/docs/guides/reasoning-best-practices
- Prompt optimizer: https://developers.openai.com/api/docs/guides/prompt-optimizer
- Compaction (long sessions): https://developers.openai.com/api/docs/guides/compaction
- Codex prompting: https://developers.openai.com/codex/prompting
- Codex best practices: https://developers.openai.com/codex/learn/best-practices
- Codex AGENTS.md: https://developers.openai.com/codex/guides/agents-md
- Codex workflows: https://developers.openai.com/codex/workflows
- Help Center (API): https://help.openai.com/en/articles/6654000-best-practices-for-prompt-engineering-with-the-openai-api
- Help Center (ChatGPT): https://help.openai.com/en/articles/10032626-prompt-engineering-best-practices-for-chatgpt
- Cookbook (executable examples): https://developers.openai.com/cookbook

## Asking through the orchestrator channel

Through `codex`, this family asks on tier 1. Tier 1: after `pitwall agents setup mcp --harness codex`, the dispatcher gives the model the `ask_orchestrator` tool and it waits for the answer. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
