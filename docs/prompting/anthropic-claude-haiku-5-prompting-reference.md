# Claude Haiku 5.5 model and prompting reference

This is the canonical project reference for routing Claude Haiku 5.5 through `claude-shim.sh`. Its model-specific claims come from Anthropic's vendor documentation, recorded with a source and locator in the [model facts](../agents/model-facts/families/claude-haiku-5/FACTS.md). Routing and prompt recommendations below are derived from those pages.

## Source and route

- Vendor model page: https://platform.claude.com/docs/en/models/haiku-5-5/overview
- Anthropic prompting guide: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5.md
- Release date: October 7, 2026
- Route: `~/.claude/scripts/claude-shim.sh <prompt-file> --model haiku`
- Transport behavior: see [Anthropic Claude Code prompting and transport reference](anthropic-claude-code-prompting-reference.md)

The `haiku` alias reaches Haiku 5.5 only on the Anthropic API, from Claude Code v2.1.293. On Claude Platform on AWS, Amazon Bedrock, Google Cloud's Agent Platform, and Microsoft Foundry it still reaches Haiku 4.5. Use the full name `claude-haiku-5-5` when exact pinning is required.

## Capability profile

Anthropic describes Haiku 5.5 as built for high-volume, latency-sensitive work such as classification, extraction, routing, and subagent tasks. It is the fastest and lowest-priced model in the current lineup, with a 1M-token context window, up to 128K output tokens, text and image input, and a June 2026 reliable knowledge cutoff. It is the first Haiku with effort levels (`low`, `medium`, `high`, `xhigh`, `max`) and adaptive thinking. Default effort is `medium`.

Existing Haiku 4.5 prompts should perform well without changes, but several request settings are breaking: manual `budget_tokens` thinking, non-default sampling parameters, and assistant prefill each return a 400 error, and the newer tokenizer yields about 30 percent more tokens for the same text.

## Behavior that affects prompts

- Effort is the main control. At `low` effort in long agent prompts the model is more likely to skip a search, stop early, or skip a check. Raising effort one level roughly halved early stopping in Anthropic's testing, at more than double the output tokens.
- Thinking is on by default and counts toward `max_tokens`. It can be disabled only at `high` effort or below; at `xhigh` and `max` the request returns a 400 error. Claude Code does not offer an off switch for this model.
- With thinking off and a JSON output format, the model can skip a tool call it needs. Keep adaptive thinking on for those requests, or force the tool call.
- Coding agents may report a change done without running a check at `low` and `medium` effort. Ask for a real test, type-check, or build run before completion.
- With a search tool, give the model today's date; at low effort or with long system prompts also tell it that its training data is old.
- Never put user text inside a `tool_result` block. The model is trained to resist prompt injection through tool results and can ignore such text.
- Safety classifiers can decline a request with `stop_reason: "refusal"`. There is no server-side fallback, and Claude Code's automatic fallback does not name Haiku 5.5 as a source.
- Priority Tier is not supported.

## Routing guidance derived from the documentation

Use Haiku 5.5 for narrow, well-specified, high-volume work where latency and cost dominate, and for bounded subagent tasks. Keep multi-file implementation, ambiguous investigation, and verification-heavy work on Sonnet, Opus, or Fable, and escalate when local evidence supports it. Prefer a prompt contract with:

1. A concrete objective and relevant context.
2. Exact scope and authorization boundaries.
3. Required artifacts and output shape.
4. Deterministic tests, type checks, linters, or other gates.
5. A requirement to inspect source and tool output rather than answer from memory.
6. A concise accounting of failures, uncertainty, and incomplete work.

## Operational caveats

- Start at `medium` effort. Move to `low` only for chat, short tool tasks, and simple volume work, and compare `xhigh` or `max` against Sonnet 5.5 on performance, cost, and speed before paying for them.
- Select response content blocks by `type`; a response can begin with a thinking block.
- Keep conversation history append-only when thinking blocks are sent back.
- Use `--max-budget-usd` for bounded automation.
- Re-run decisive checks from the host after authoring work.
- Leave `PITWALL_AGENTS_UNRESTRICTED` unset (restricted) when the child must retain Claude Code's configured permission policy; set it to `1` only for unattended runs that must not pause for approval.

## Asking through the orchestrator channel

Through `claude`, this family asks on tier 1. Tier 1: after `pitwall agents setup mcp --harness claude`, the dispatcher gives the model the `ask_orchestrator` tool and it waits for the answer. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
