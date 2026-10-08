# Agent Routing client refresh runbook

Use this runbook when a host CLI keeps loading an older Agent Routing plugin than the one
`pitwall agents install` wrote: a marketplace still pointing at a stale checkout or worktree, or a
plugin cache that survived an upgrade. Normal upgrades need only `uv tool upgrade pitwall` and
`pitwall agents install`. Never point a live marketplace at a candidate, integration, or other
disposable worktree.

The marketplace root is the plugin directory `pitwall agents install` writes,
`${XDG_DATA_HOME:-$HOME/.local/share}/pitwall/plugins`, and the marketplace is named
`pitwall-local`. The refresh below is rehearsed under isolated client homes by
`tests/agents/test_client_rehearsal.py`.

## Before you start

1. Confirm the install is current: `pitwall --version` reports the release you expect, then run
   `pitwall agents install`. It rewrites only the files it owns and records them in the install
   manifest.
2. Run `pitwall agents doctor --installation-only` and require it to pass.
3. Note anything in the host's own settings you want to keep. The refresh removes and re-adds only
   the `pitwall-local` marketplace and the Pitwall plugin, and Claude retains plugin data
   (`--keep-data`).

## Refresh each client

Remove and re-add the marketplace and plugin through the supported client so no cache stays
attached to the stale source:

```bash
claude plugin uninstall --keep-data --scope user \
  pitwall@pitwall-local
claude plugin marketplace remove --scope user pitwall-local
claude plugin marketplace add --scope user \
  "${XDG_DATA_HOME:-$HOME/.local/share}/pitwall/plugins"
claude plugin install --scope user \
  pitwall@pitwall-local

codex plugin remove pitwall-codex@pitwall-local
codex plugin marketplace remove pitwall-local
codex plugin marketplace add \
  "${XDG_DATA_HOME:-$HOME/.local/share}/pitwall/plugins"
codex plugin add pitwall-codex@pitwall-local
```

Use the Copilot commands only when a supported client is installed:

```bash
copilot plugin uninstall pitwall-copilot
copilot plugin marketplace remove pitwall-local
copilot plugin marketplace add "${XDG_DATA_HOME:-$HOME/.local/share}/pitwall/plugins"
copilot plugin install pitwall-copilot@pitwall-local
```

Run each client's list or details command (`claude plugin list`, `codex plugin list`,
`copilot plugin list`) and require the loaded plugin to report the installed Pitwall version. Start a
new session in each client so the refreshed skill loads.

## Recovery

`pitwall agents uninstall` removes exactly what `install` wrote, hook registrations first, and
`pitwall agents install` writes it again. Neither touches profiles in `pitwall.toml`, run records,
workflows, plugin data, or harness configuration.
