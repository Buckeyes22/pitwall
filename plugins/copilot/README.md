# pitwall-copilot

Copilot-compatible companion package for the Claude Code `pitwall`
plugin.

This package is intentionally separate from `plugins/claude/` so
Copilot does not load Claude-only plugin surfaces such as Claude hook files,
agent markdown definitions, slash commands, or Workflow instructions. It is also
separate from `plugins/codex/` so Copilot UI does not display
Codex-specific package metadata.

## What It Contains

- `plugin.json` - Copilot CLI / VS Code agent-plugin manifest.
- `skills/subagent-model-routing/SKILL.md` - Copilot-compatible subagent-model-routing workflow.
- `skills/attach-local-endpoint/SKILL.md` - detect and attach a locally hosted model server as a route.

## What It Does Not Contain

- No Claude Code `.claude-plugin` manifest.
- No Codex `.codex-plugin` manifest.
- No Claude Code `agents/*.md` transport subagents.
- No Claude Code `commands/*.md` slash command.
- No lifecycle hook package. The Claude Stop hook depends on Claude transcript fields and environment variables, so it stays in the Claude package only.

## Shared runtime prerequisite

The plugin package does not duplicate the executable runtime. Install the `pitwall` command (`uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl`), then run `pitwall agents install`. That writes the shims for fourteen harnesses (`codex`, `claude`, `agy`, `kimi`, `opencode`, `grok`, `qwen`, `pi`, `hermes`, `cline`, `muse`, `goose`, `dsh`, and `zcode`) plus `route-shim.sh` under `~/.claude/scripts/` (each a two-line wrapper around `pitwall agents _shim`), installs the Claude Code, Codex, and Copilot plugins from the `pitwall-local` marketplace, and registers the channel MCP server as `pitwall mcp serve channel`. `pitwall agents uninstall` removes exactly what install wrote, hook registrations first. Muse and ZCode are model-bound, Cline holds one custom endpoint at a time, and dsh is experimental. Provider executables are external dependencies, and authentication is never automated. The shared runtime requires Python 3.14.

```bash
test -x ~/.claude/scripts/codex-shim.sh
test -x ~/.claude/scripts/claude-shim.sh
test -x ~/.claude/scripts/agy-shim.sh
test -x ~/.claude/scripts/kimi-shim.sh
test -x ~/.claude/scripts/opencode-shim.sh
test -x ~/.claude/scripts/grok-shim.sh
pitwall agents runs list
pitwall doctor
```

The Copilot skill uses those shims from direct shell commands with prompt files.

Route profiles (`docs/agents/routes.md`) add `route-shim.sh` on top of the provider shims.

Write dispatches through any shim can opt into `--routing-workspace isolated --routing-task-mode write`; review with `pitwall agents runs diff`, apply explicitly, and discard the retained worktree explicitly.

For a durable dependency graph across any of the fourteen transports, use `pitwall agents workflow run workflow.json --host copilot`. The runner provides concurrency, explicit artifact handoff, retries, verification, cancellation, and resume. `--host` is advisory metadata rather than a security boundary.

Model discovery is explicit with `pitwall agents doctor --discover-models`; the default doctor and dispatch preflight never run it.

Active routes are GPT via `codex-shim`—including the current Codex runtime model IDs `gpt-6-astra`, `gpt-6.1-sol`, and `gpt-6-luna`—Claude models via `claude-shim` (Sonnet 5.5 as the default workhorse, Opus 5.5 for difficult or verification-heavy work, and Fable 5.1 for the hardest generally available Claude work), Gemini via `agy-shim`, Kimi via `kimi-shim`, Grok 4.7 via `grok-shim`, GLM/MiniMax/local models via `opencode-shim`, and Pi, Hermes Agent, Cline CLI, Muse Code, goose, experimental dsh, and ZCode (saved model and account) via their matching shims. Pi, Hermes, Cline, and dsh endpoint routes require sync; Cline supports one custom endpoint, and Muse remains model-bound. Fable's production safeguards may block or fall back in protected domains; this project defines no Mythos-specific route.

## Prompt References

The Copilot-compatible skill includes compact prompt cards for prompt files sent through the Codex, Claude Code, Gemini/Antigravity, Muse Code, Grok, OpenCode-provider, and local-model routes, with self-contained detail at `skills/subagent-model-routing/references/model-prompting.md` for isolated plugin installs. In a source checkout, canonical authoring references under `docs/prompting/` include the Gemini and Muse Code references and separate system-card-grounded guides for Claude Sonnet 5.5, Opus 5.5, and Fable 5.1; start with `docs/prompting/00-prompt-reference-index.md` before updating runtime cards or the bundled compilation. Qwen (local llama.cpp endpoints included, configured via `~/.qwen/.env`) routes through the dedicated `qwen-shim`; other local/self-hosted models route through the `opencode-shim` custom-provider path — see the root README for an example, or use the `attach-local-endpoint` skill to detect and attach one automatically.
