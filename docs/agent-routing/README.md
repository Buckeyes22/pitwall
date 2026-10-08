# Pitwall Agent Routing

Pitwall Agent Routing delegates bounded coding work to locally installed agent harnesses (`codex`,
`claude`, `kimi`, `opencode`, and the rest) and returns a stable completion sentinel, a private run
record, and an optional workflow result. It is the `pitwall agents` command group of the one
`pitwall` package. The broker does not import it, and its shim and hook entry points do not load the
broker's web, database, or queue dependencies.

## Install

```bash
uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl
pitwall agents install
```

`pitwall agents install` writes the thirteen harness shims and `route-shim.sh` under
`~/.claude/scripts/`, copies the Claude Code, Codex, and GitHub Copilot CLI plugins with one
`pitwall-local` marketplace per host, registers them with each host CLI on `PATH`, and registers the
`pitwall-channel` MCP server. `pitwall agents uninstall` removes exactly that.

Detailed command, profile, workflow, harness, worktree, and doctor documentation is in
[`docs/agents/`](../agents/routing-readme.md). A workstation that ran the standalone tool runs
`pitwall agents migrate` once; see the [migration guide](../operator/agents-migration.md). To refresh
a host CLI that still loads an older plugin, follow the [client refresh runbook](live-cutover.md).

## Terms

- A **harness** is an agent CLI (`codex`, `claude`, `kimi`, `opencode`, and so on).
- An **agent profile** is a `name@harness` binding kept in the `[agents.profiles]` tables of
  `pitwall.toml`.
- A **dispatch** is one harness run.
- A **workflow** is a dependency-ordered set of dispatches.
- A **provider** is a compute or inference backend, which is a different thing from a harness.

## Trust boundary

Routing starts local harness CLIs with the operator's user authority. The opt-in unrestricted
profile (`PITWALL_AGENTS_UNRESTRICTED=1`) is designed for unattended work and bypasses a child CLI's interactive approvals; without it, each CLI keeps its own policy.
Generated code, child output, profile and workflow documents, lifecycle hooks, and harness
installers must be reviewed as local command-execution inputs. Run output is private on disk but can
contain source or secrets printed by a harness, and no automatic retention period is imposed.

Self-hosted endpoint keys remain in named environment variables. The optional Pitwall receiver is
loopback-only and is not installed or enabled automatically. See the unified
[Pitwall security policy](../../SECURITY.md) and the detailed
[component threat model](../agents/SECURITY.md).
