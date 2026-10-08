# Meta Muse Code Prompting Reference

| Field | Value |
|---|---|
| Vendor | Meta |
| Models in scope | Muse Spark 1.3 (and 1.2) via Muse Code |
| Primary access | Meta Model API / Muse Code CLI |
| Official guidance | dev.meta.ai docs |
| Compiled | 2026-08-26 |

## 1. Official guidance landscape

Meta documents Muse Code through its product, CLI, configuration, permissions, authentication, extension, and changelog pages. Use the CLI documentation as the source of truth for command behavior.

## 2. Muse Code headless contract

Use `muse exec "<prompt>"` for a one-shot prompt, or `--prompt-file` to deliver a prompt from a file. `--json` emits JSONL events. The documented execution controls are `--max-model-steps`, `--no-session-log`, `--session-id`, and `--allow-workspace-switch`. Exit codes are `0` (success), `1` (runtime failure), `2` (usage error), `130` (interrupted), and `143` (terminated).

## 3. Model and reasoning

Select a model with `--model`; the default is `muse-spark-1.3`. `--reasoning-effort` accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, or `ultra`, with default `high`. Use `/models` to inspect available models.

## 4. Permissions

Muse Code supports `--yolo`, `--disable-approval`, `--disable-sandbox`, `--approval-mode on-request|untrusted|never`, `--approval-judge`, `--trust-workspace`, and `--sandbox-network`. Select an approval and sandbox boundary appropriate to the task before dispatch.

## 5. Auth and config

`META_API_KEY` takes precedence as environment configuration, followed by a stored key and browser session. Muse Code configuration is `~/.config/muse/settings.json` with `schema_version: 1`. It reads `AGENTS.md` and `CLAUDE.md` workspace instructions.

## 6. Routing through Pitwall Agent Routing

Evidence-derived: Muse Code is model-bound and uses file prompt delivery. No documented custom endpoint support was found in the official sources listed below.

## 7. Sources

- https://developer.meta.com/ai/resources/blog/build-with-muse-code/
- https://developer.meta.com/ai/products/muse-code/
- https://dev.meta.ai/docs/muse-code/
- https://dev.meta.ai/docs/muse-code/extending.md
- https://dev.meta.ai/docs/muse-code/configuration
- https://dev.meta.ai/docs/muse-code/permissions
- https://dev.meta.ai/docs/muse-code/auth.md
- https://dev.meta.ai/docs/muse-code/changelog.md

## Asking through the orchestrator channel

Through `muse`, this family asks on tier 4. Tier 4: the model writes `mailbox/asks/NNNN.json` and exits 75; the run pauses until the orchestrator answers and runs `pitwall agents runs resume`. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
