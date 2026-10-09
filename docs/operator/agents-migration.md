# Migrating from standalone Agent Routing

Agent Routing used to ship as its own tool (`pitwall-agent-routing`, import package `model_routing`,
state under `subagent-model-routing`). It is now the `pitwall agents` command group of the one
`pitwall` package. This is a clean break: none of the old command, variable, or path names are
aliased. `pitwall agents migrate` moves an existing workstation across in one step.

## Run it

```bash
uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl
pitwall agents migrate
```

`pitwall agents migrate` takes no arguments. It is idempotent: a second run finds nothing to do. It
runs these steps in order:

1. **Profiles.** `routes.json` (and its later `profiles.json` name) in
   `~/.config/subagent-model-routing/` becomes the `[agents.profiles]` tables of `pitwall.toml`. A
   value that already exists in the file and differs is a conflict; every conflict is listed and
   nothing changes.
2. **State and config.** The run store, mailbox, receipts, usage cache, ledger, and the other config
   files move to `~/.local/state/pitwall/agents/` and `~/.config/pitwall/agents/`
   (`PITWALL_AGENTS_STATE_HOME` relocates the state root).
   Moves are printed once per top-level item with its entry count, not once per file.
3. **Shims and plugins.** The standalone shims are replaced by the two-line `pitwall agents _shim`
   wrappers, the plugins are registered under the `pitwall-local` marketplace, and the
   `pitwall-channel` MCP server is registered as `pitwall mcp serve channel`; an entry the old
   install wrote (launcher `pitwall-agent-routing` or `model-routing` with `mcp`, or its marked Codex
   block) is replaced, while any other `pitwall-channel` entry is refused. Registration also refuses a harness config it cannot edit safely, such as JSON that is not strict or a YAML file that would not read back unchanged (the rules are in [the orchestrator channel](../agents/orchestrator-channel.md#tier-1-the-mcp-server)); the install step then fails with `the shim and plugin install failed: <reason>`, the old install stays in place, and the migration exits `1`. Fix the file the message names and run it again. The
   `managed by subagent-model-routing` blocks the dsh, hermes, and opencode adapters wrote into the
   harnesses' own config files are re-marked `managed by pitwall`.
4. **Worktree branches.** Dispatch worktree branches under either earlier prefix
   (`model-routing/<id>` and `pitwall-agent-routing/<id>`) are renamed `pitwall-agents/<id>` in the
   repository that owns them, and each run's `workspace.json` is updated so the ownership check
   accepts the moved worktree. When the branch is gone, or the repository itself is gone, the record
   is not renamed: if the moved worktree directory exists, the record's `path` is rewritten to it
   (`updated <id>: ...`); if it does not, the migration reports the record as stale
   (`stale record <id>: ...`) and leaves it unchanged. A target branch that already exists is a
   conflict, reported with that repository left unchanged.
5. **Environment.** Variables still set under old names are reported with their replacement.
6. **Old install.** The old install is removed only after steps 1 to 3 succeeded: the legacy plugin
   and marketplace registrations, the `pitwall-agent-routing` launcher, its older `model-routing` alias (only a symlink to the launcher or a file the old install wrote), and `parse-shim-result.py`,
   the durable payload under `~/.local/share/subagent-model-routing`, and the emptied legacy
   directories. Each removed path is printed. Files the migration does not own are left in place and
   named.

Exit `2` means the legacy run store holds a dispatch that may still be running; stop or finish it and
run the command again. A non-terminal run counts as running only when there is evidence of it: its
launcher pid (from `launcher.json`) is alive with the same process start identity, or, when no pid was
recorded, a file in its run directory changed within the run's dispatch timeout plus five minutes. The
timeout is the one the run recorded (`timeoutSeconds`), else `PITWALL_AGENTS_TIMEOUT_SECS`, else 1140
seconds. Every other non-terminal run belongs to a dispatch whose runner died before it wrote a final
state. The migration moves those over unchanged and prints
`treated as abandoned: <id> (<state>, last activity <timestamp>)` for each. Exit `1` means a conflict or an unreadable input, with nothing changed, except that a failed install step leaves profiles and state already moved and the old install in place (a worktree-branch
conflict is reported after everything else ran).

If you installed the standalone tool with `uv tool install`, remove that separately:

```bash
uv tool uninstall pitwall-agent-routing
```

The migration rewrites only the `pitwall-channel` registrations. A broker MCP server you registered
yourself, for example `docker exec -i <api container> pitwall mcp serve --transport stdio`, keeps its
old arguments and fails to start; change them to `pitwall mcp serve broker` in each host's config.

## Renames

| Before | Now |
| --- | --- |
| `pitwall-agent-routing <verb>` | `pitwall agents <verb>` |
| `pitwall-agent-routing routes <verb>` | `pitwall agents profiles <verb>` |
| `pitwall-agent-routing pitwall receiver\|subscribe\|watch` | `pitwall agents broker receiver\|subscribe\|watch` |
| `pitwall-agent-routing usage` | `pitwall usage` |
| `pitwall-mcp`, `pitwall mcp serve --transport stdio` | `pitwall mcp serve broker` |
| `routes.json` / `profiles.json` | the `[agents.profiles]` tables of `pitwall.toml` |
| `~/.config/subagent-model-routing/` | `~/.config/pitwall/agents/` |
| `~/.local/state/subagent-model-routing/` | `~/.local/state/pitwall/agents/` |
| `~/.claude/subagent-model-routing/ledger/observations.jsonl` | `~/.local/state/pitwall/agents/ledger/observations.jsonl` |
| marketplace `subagent-model-routing-local` (and Claude's `pitwall`) | `pitwall-local` |
| `bootstrap.sh`, `install.sh`, the `curl \| bash` installer | install the release wheel (see the [install steps](../../README.md#install-pitwall)), then `pitwall agents install` |
| `pitwall-agent-routing/<id>` and `model-routing/<id>` worktree branches | `pitwall-agents/<id>` |

The Claude plugin id (`pitwall`), the Codex plugin id (`pitwall-codex`), the Copilot plugin id
(`pitwall-copilot`), the shim file names, and the `SHIM-DONE exit=<n>` sentinel do not change.

## Environment variables

Every `pitwall agents` entry point refuses to run (exit `2`) while a legacy variable is set, and prints
each one with its replacement, so a stale value is never silently ignored. Rename them:

| Legacy | Replacement |
| --- | --- |
| `SUBAGENT_MODEL_ROUTING_<NAME>` | `PITWALL_AGENTS_<NAME>` |
| `PITWALL_AGENT_ROUTING_<NAME>` | `PITWALL_AGENTS_<NAME>` |
| `SUBAGENT_MODEL_ROUTING_ROUTES` | `PITWALL_AGENTS_PROFILES` |
| `SUBAGENT_MODEL_ROUTING_PROVIDER` | `PITWALL_AGENTS_HARNESS` |
| `SHIM_TIMEOUT_SECS` | `PITWALL_AGENTS_TIMEOUT_SECS` |

`PITWALL_API_TOKEN` stays the broker's own variable; Agent Routing no longer reads it as a fallback.
`PITWALL_WEBHOOK_SECRET` remains only a legacy fallback for receiver verification. Use `PITWALL_AGENTS_API_TOKEN` for normal `read`/`spend` profile
traffic, `PITWALL_AGENTS_SUBSCRIPTION_TOKEN` for `webhook:admin` subscription creation, and
`PITWALL_AGENTS_WEBHOOK_SECRET` for receiver verification.

## What changed besides names

- There is no GNU `timeout` or `gtimeout` prerequisite; the Python supervisor enforces the timeout.
- The `pi` and `grok` shims pass the prompt as a private file, not as an argument.
- There is one package, one version, and one lock. Tags are `v*`; the `agent-routing/v*` tags are
  historical snapshots.
- The packaged schemas keep their filenames and `schemaVersion` contracts. Only `$id` metadata moved,
  from the archived standalone repository to
  `https://raw.githubusercontent.com/Buckeyes22/pitwall/main/src/pitwall/agents/resources/schemas/<name>.schema.json`.
  Persisted schemaVersion-1 records do not embed `$id`, so they need no rewrite.

## Uninstall

`pitwall agents uninstall` removes exactly what `pitwall agents install` wrote, hook registrations
first. Profiles in `pitwall.toml`, run records, workflows, plugin data, and harness configuration are
retained.
