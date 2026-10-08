# pitwall-codex

Codex-native companion package for the Claude Code `pitwall` plugin.

This package is intentionally separate from `plugins/claude/` so Codex does not load Claude-only plugin surfaces such as Claude hook files, agent markdown definitions, slash commands, or Workflow instructions.

## What It Contains

- `.codex-plugin/plugin.json` - Codex plugin manifest.
- `skills/subagent-model-routing/SKILL.md` - Codex-native subagent-model-routing workflow.
- `skills/attach-local-endpoint/SKILL.md` - detect and attach a locally hosted model server as a route.

## Install and validate

From this repo checkout:

```bash
codex plugin marketplace add <pitwall>
codex plugin add pitwall-codex@pitwall-local
codex plugin marketplace list
codex plugin list
python3 -m json.tool <pitwall>/plugins/codex/.codex-plugin/plugin.json >/dev/null
```

Codex installs local plugins into its cache under
`~/.codex/plugins/cache/pitwall-local/pitwall-codex/<version>/`.
Restart Codex or start a new thread after reinstalling so the updated skill is
loaded.

## What It Does Not Contain

- No Claude Code `.claude-plugin` manifest.
- No Claude Code `agents/*.md` transport subagents.
- No Claude Code `commands/*.md` slash command.
- No lifecycle Stop-hook package. The Claude Stop hook depends on Claude transcript fields and environment variables, so it stays in the Claude package only. The package does ship one hook: the tier-2 `PreToolUse` steering gate (`hooks/hooks.json` → `hooks/steer-gate.py`), which defers to `pitwall agents _steer-gate` while a `priority`, `scope`, or `stop` steer waits for acknowledgement, exempts the `pitwall-channel` server's own `read_steering`, `ack_steer`, and `ask_orchestrator` tools (matched by exact server name, `pitwall-channel` or Codex's `pitwall_channel`), and fails closed when the gate cannot be evaluated.

## Shared runtime prerequisite

The plugin package does not duplicate the executable runtime. Install the `pitwall` command (`uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl`), then run `pitwall agents install`. That writes the shims for fourteen harnesses (`codex`, `claude`, `agy`, `kimi`, `opencode`, `grok`, `qwen`, `pi`, `hermes`, `cline`, `muse`, `goose`, `dsh`, and `zcode`) plus `route-shim.sh` under `~/.claude/scripts/` (each a two-line wrapper around `pitwall agents _shim`), installs the Claude Code, Codex, and Copilot plugins from the `pitwall-local` marketplace, and registers the channel MCP server as `pitwall mcp serve channel`. `pitwall agents uninstall` removes exactly what install wrote, hook registrations first. The Codex-native package targets only external harnesses; it does not route Codex back through its own CLI. Muse and ZCode are model-bound, Cline holds one custom endpoint at a time, and dsh is experimental. The steering-gate hook fails closed: when `pitwall` cannot be found, times out, or errors, it blocks the tool call and says why. Recovery must not use a tool call: type `! uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl` at the prompt, or disable the plugin with `/plugin`; check the install with `pitwall doctor`. Provider executables are external dependencies, and authentication is never automated. The shared runtime requires Python 3.14.

```bash
test -x ~/.claude/scripts/claude-shim.sh
test -x ~/.claude/scripts/agy-shim.sh
test -x ~/.claude/scripts/kimi-shim.sh
test -x ~/.claude/scripts/opencode-shim.sh
test -x ~/.claude/scripts/grok-shim.sh
pitwall agents runs list
pitwall doctor
```

The Codex skill uses those shims from direct shell commands with prompt files.

Route profiles (`docs/agents/routes.md`) add `route-shim.sh` on top of the provider shims.

External write dispatches can opt into `--routing-workspace isolated --routing-task-mode write`; review them with `pitwall agents runs diff`, apply explicitly, and discard the retained worktree explicitly. Codex work itself stays native and inline.

For a durable external-only dependency graph, use `pitwall agents workflow run workflow.json --host codex`. The validator rejects Codex transport tasks, while tasks for any other registered harness can use concurrency, explicit artifact handoff, retries, verification, cancellation, and resume. `--host` is advisory metadata; the package's native-family rule remains part of the routing contract.

Model discovery is explicit with `pitwall agents doctor --discover-models`; the default doctor and dispatch preflight never run it.

Active routes are Claude models via `claude-shim` (Sonnet 5.5 as the default workhorse, Opus 5.5 for difficult or verification-heavy work, and Fable 5.1 for the hardest generally available Claude work, plus `haiku` and full-name overrides), Gemini via `agy-shim`, Kimi via `kimi-shim`, Grok 4.7 via `grok-shim`, GLM/MiniMax/local models via `opencode-shim`, and Pi, Hermes Agent, Cline CLI, Muse Code, goose, experimental dsh, and ZCode (saved model and account) via their matching shims. Pi, Hermes, Cline, and dsh endpoint routes require sync; Cline supports one custom endpoint, and Muse remains model-bound. Fable's production safeguards may block or fall back in protected domains; this project defines no Mythos-specific route. Codex work stays native in the current thread.

## Prompt References

The Codex-native skill includes compact prompt cards for its Claude Code, Gemini/Antigravity, Muse Code, Grok, OpenCode-provider, and local-model routes, with self-contained detail at `skills/subagent-model-routing/references/model-prompting.md` for isolated plugin installs. In a source checkout, canonical authoring references under `docs/prompting/` include the Gemini and Muse Code references and separate system-card-grounded guides for Claude Sonnet 5.5, Opus 5.5, and Fable 5.1; start with `docs/prompting/00-prompt-reference-index.md` before updating runtime cards or the bundled compilation. Qwen (local llama.cpp endpoints included, configured via `~/.qwen/.env`) routes through the dedicated `qwen-shim`; other local/self-hosted models route through the `opencode-shim` custom-provider path — see the root README for an example, or use the `attach-local-endpoint` skill to detect and attach one automatically.
