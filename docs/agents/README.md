# Pitwall Agent Routing and agent install guide

This directory holds the documentation for `pitwall agents` (Agent Routing) and the public guide for
coding agents that install and operate Pitwall. Agent Routing dispatches work to external agent
harnesses; the install guide gets a coding agent from a fresh clone to a working Pitwall with its own
harness registered.

## Install

```bash
uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl
pitwall agents install
```

`pitwall agents install` writes the shims and plugins and registers the channel MCP server. See the
[Agent Routing README](routing-readme.md#install) for what it writes, and
[`install.md`](install.md) for the full agent walkthrough with a verification command after every
stage. A workstation that ran the standalone Agent Routing tool runs `pitwall agents migrate` once;
see the [migration guide](../operator/agents-migration.md).

## Agent Routing

- [`routing-readme.md`](routing-readme.md): what Agent Routing is, install, quickstart, day-to-day
  use, configuration, and the environment variables (`PITWALL_AGENTS_*`).
- [`routes.md`](routes.md): agent profiles (`name@harness`) in the `[agents.profiles]` tables of
  `pitwall.toml`, and the `pitwall agents profiles` commands.
- [`harness-registry.md`](harness-registry.md) and [`harness-cli-setup.md`](harness-cli-setup.md):
  the fourteen harnesses and their optional installer.
- [`architecture.md`](architecture.md), [`run-records.md`](run-records.md),
  [`lifecycle-hooks.md`](lifecycle-hooks.md), [`doctor.md`](doctor.md),
  [`worktree-dispatch.md`](worktree-dispatch.md), [`workflows.md`](workflows.md): the runtime.
- [`orchestrator-channel.md`](orchestrator-channel.md): asks and steering between an orchestrator
  and a child run.
- [`pitwall.md`](pitwall.md), [`self-hosted.md`](self-hosted.md),
  [`attach-local-endpoint.md`](attach-local-endpoint.md), [`usage.md`](usage.md): serving a leased
  model as a profile, local endpoints, and subscription usage.
- [`model-facts/`](model-facts/README.md): the sourced facts about models, harnesses, and hosts.
- The [prompting references](../prompting/00-prompt-reference-index.md) hold the model-specific
  prompting guidance.

## Agent install guide

Point your agent at [`install.md`](install.md). An agent should follow it top to bottom rather
than jumping to a single command.

- [`install.md`](install.md): the walkthrough, prerequisites through MCP registration and
  verification.
- [`claude-code.md`](claude-code.md): exact `pitwall mcp install`/`uninstall` output for Claude
  Code, both scopes.
- [`codex.md`](codex.md): exact `pitwall mcp install`/`uninstall` output for Codex, user scope
  only.
- [`opencode.md`](opencode.md): exact `pitwall mcp install`/`uninstall` output for OpenCode, both
  scopes.

## Records

The `migration-v0.*.md` pages, [`releases/`](releases/), and the acceptance-evidence pages are dated
records of the standalone releases. They keep the names in use when they were written and are not
instructions for the current release.

## Public guide, local checkout

This directory is committed to the repository and applies to every checkout. A checkout may also
hold a local, uncommitted `AGENTS.md` file at the repository root. That file is excluded from
version control on purpose and is never authoritative for what this guide documents. If both exist,
this directory describes the behavior Pitwall actually ships; `AGENTS.md` holds whatever local notes
an operator chose not to commit.
