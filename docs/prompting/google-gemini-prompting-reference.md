# Google Gemini 3.x prompting reference

**Scope:** Gemini 3.x models reached through Google Antigravity CLI (`agy`).  
**Evidence boundary:** Prompt guidance below is derived from first-party Google documentation. CLI-specific behavior is limited to the Antigravity documentation and the verified `agy 1.1.22` observations recorded in the approved design.

## Prompt shape

Gemini 3 responds best to direct, well-structured instructions. State the goal, constraints, success criteria, and requested output shape precisely. Put critical behavior, role, and formatting requirements first. Use one consistent delimiter scheme—Markdown headings or XML-style tags—to separate instructions, context, examples, and source material.

For large inputs, place the context before the task and end with a clear bridge such as “Based on the information above…” followed by the exact question. Add specific, consistently formatted few-shot examples when format or behavior is difficult to describe. Ask explicitly for detail when needed because Gemini 3 defaults to concise answers.

For coding-agent work, include the repository scope, authorized actions, forbidden actions, exact artifacts, validation commands, and completion criteria. Require the agent to report failed checks and uncertainty. Verify resulting files and tests after the harness returns.

## Reasoning and sampling

Antigravity exposes Gemini effort as `low`, `medium`, or `high`; Gemini 3.1 Pro currently exposes `low` and `high`. Select the lowest level appropriate to the task and raise it for harder planning or verification. Do not combine an effort-suffixed Antigravity model slug with `--effort`.

Google recommends retaining default temperature, top-p, and top-k values for Gemini 3.x because changing them—especially lowering temperature below 1.0—can degrade complex reasoning or cause repetition. Prefer prompt clarity and the documented effort control over sampling changes.

## Antigravity operational contract

Use `agy-shim.sh <prompt-file> [agy flags]`. The shim defaults to `gemini-3.8-flash` at medium effort, always adds the dispatch workspace, requests text output, and sets Antigravity's print timeout after the shim supervisor timeout. In unrestricted mode it auto-approves tools. In restricted mode, a command tool can be auto-denied while `agy` exits 0; treat the `jetski: no output produced` stderr diagnostic as failure and verify requested artifacts.

Antigravity authentication is interactive unless a Gemini API-key configuration is supplied. Model availability is tier-dependent and may change; use `agy models` for the live catalog. The registry's base IDs are routing seeds, not an availability guarantee.

## First-party sources

- [Prompt design strategies](https://ai.google.dev/gemini-api/docs/prompting-strategies)
- [Gemini 3 developer guide](https://ai.google.dev/gemini-api/docs/gemini-3)
- [Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking)
- [Antigravity CLI headless mode](https://antigravity.google/docs/cli/headless/)
- [Antigravity models](https://antigravity.google/docs/models/)

Compiled 2026-08-27.

## Asking through the orchestrator channel

Through `agy`, this family asks on tier 4. Tier 4: the model writes `mailbox/asks/NNNN.json` and exits 75; the run pauses until the orchestrator answers and runs `pitwall agents runs resume`. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
