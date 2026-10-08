# Runtime architecture

This document defines the compatibility and ownership boundaries for the local Agent Routing runtime, the `pitwall.agents` package behind `pitwall agents`. It is an implementation contract, not a model-ranking guide; model evidence and prompting guidance remain under `docs/prompting/` and the package-local skill references.

## Product boundary

The project remains a local plugin and command-line transport layer. It does not run a daemon, web UI, SQLite service, tmux control plane, mobile client, or persistent agent-team service. The shared runtime adds bounded process supervision, local run artifacts, non-destructive diagnostics, opt-in worktrees, and a foreground JSON dependency scheduler to the existing one-shot shims.

`pitwall agents install` writes fifteen public shell entrypoints under `~/.claude/scripts/`: one shim per registered harness plus the route shim. Each is a two-line wrapper, `exec pitwall agents _shim <harness> "$@"`:

- `codex-shim.sh`
- `claude-shim.sh`
- `agy-shim.sh`
- `grok-shim.sh`
- `kimi-shim.sh`
- `opencode-shim.sh`
- `qwen-shim.sh`
- `route-shim.sh`
- `pi-shim.sh`
- `hermes-shim.sh`
- `cline-shim.sh`
- `muse-shim.sh`
- `goose-shim.sh`
- `zcode-shim.sh`
- `dsh-shim.sh`

Their prompt-source syntax, forwarded provider arguments, exit status, ledger behavior, and final `SHIM-DONE exit=<n>` line are public compatibility surfaces. `tests/agents/test_shim_contract.py` pins this behavior.

## Client ownership

Client-native routing boundaries are intentional:

- Claude Code keeps Claude work in native Agent or Workflow calls and exposes Codex, Gemini, Kimi, Grok, Qwen, OpenCode, Pi, Hermes, Cline, Muse, goose, ZCode, and dsh transports.
- Codex keeps GPT/Codex work in the native Codex harness and exposes Claude, Gemini, Kimi, Grok, Qwen, OpenCode, Pi, Hermes, Cline, Muse, goose, ZCode, and dsh transports.
- GitHub Copilot may invoke all fourteen provider transports.

Generated route assets may share a canonical registry, but client-specific prose, commands, agents, hooks, and manifests must remain separate. Claude-only Workflow and Stop-hook instructions must never misroute into the Codex or Copilot packages.

## Layer responsibilities

```text
host plugin or direct shell
          │
          ▼
two-line shim wrapper (`pitwall agents _shim`)
          │
          ▼
Python 3.14 pitwall agents runtime
  ├── provider registry and adapter
  ├── prompt/provider preflight
  ├── child process supervision
  ├── legacy ledger append
  ├── structured run artifacts
  └── fail-open lifecycle events/hooks
```

The wrappers only hand off to `pitwall agents _shim`, which imports no broker, web, or database module. Provider adapters own binary resolution, model parsing, provider argv, prompt delivery, permission flags, and provider-specific telemetry. The shared runtime owns timeout/cancellation, streaming, the sentinel, run state, artifact permissions, ledger writes, and lifecycle hooks.

`route-shim.sh` adds a resolver above the same runtime: it reads the `[agents.profiles]` tables of `pitwall.toml`, picks the harness from the route, validates the combination before any child starts, and calls the provider dispatch path with route metadata. No provider adapter changes behavior for routed dispatches.

Provider commands are always executed as argument arrays without `shell=True`.

## Dispatch lifecycle

Core dispatch states are:

```text
created → preflighting → ready → workspace_preparing → workspace_ready → running → succeeded
```

Every non-terminal state also has failure and channel exits:

```text
preflighting        → preflight_failed | failed
ready               → running | failed
workspace_preparing → workspace_ready | failed
workspace_ready     → running | failed
running             → failed | timed_out | cancelled | paused
paused              → preflighting | running | failed | cancelled
```

Terminal states never transition back to `running`. A `paused` run is not terminal: it holds an unresolved channel ask and re-enters through `runs resume`, which replays the retained prompt with the answers appended. Later worktree application is integration metadata, not a dispatch state.

Each accepted dispatch receives a UUID used by its run directory, lifecycle events, and extended ledger fields. Usage failures that occur before a run begins write no ledger row.

## Local state

Run artifacts live below `${XDG_STATE_HOME:-~/.local/state}/pitwall/agents/runs/<dispatch-id>/`. Directories use mode `0700`; files use mode `0600`. Prompt bodies are not retained unless explicitly requested. Provider stdout and stderr are retained for recovery and inspection and may contain source or secrets. A dispatch that opted into the orchestrator channel also carries `channel.json` and a private `mailbox/` tree beside these artifacts; see [run records](run-records.md) and [the orchestrator channel](orchestrator-channel.md).

The existing observations ledger path and record fields remain supported. Extended fields are additive, and `distill` continues treating unmatched `started` records as interrupted-run visibility rather than completed outcomes.

The Python supervisor owns the timeout and process-group termination. There is no `timeout` or `gtimeout` prerequisite.

## Prompt exposure

Grok Build, Pi, and Muse Code take the prompt as a file path (`file` delivery). Grok Build and Pi always receive a private mode-`0600` copy, `prompt.deliver.md` in the run directory. Muse Code receives that copy when the prompt came from stdin or the run has ask support, and otherwise the caller's own file path. The runtime removes `prompt.deliver.md` after the run unless the prompt is retained (see [run records](run-records.md#the-delivery-prompt)). Claude Code, Codex, OpenCode, and goose receive prompts on stdin. The rest, Antigravity CLI, Kimi Code, Qwen Code, Hermes Agent, Cline CLI, ZCode, and dsh, pass prompt bodies in argv, which can expose them to same-user process inspection while the provider runs. On-disk retention controls do not mitigate argv visibility. Changing those providers to stdin or a file requires separately verified CLI support.

## Hooks and failure policy

Runtime lifecycle hooks receive versioned JSON on stdin, execute without a shell, have independent timeouts, and fail open by default. Hook output is captured to run artifacts and must never be appended after the public sentinel. Claude Stop hooks remain host-specific guardrails and are not replaced by runtime lifecycle hooks.

## Worktrees, discovery, and workflows

The runtime adds opt-in isolated worktrees, the local doctor, explicit discovery, and a foreground, host-neutral dependency scheduler on top of the run store. Workflow state is atomically persisted under the same private state root; every attempt is still an ordinary dispatch with workflow/task/attempt lineage. The runner adds no daemon or database. Claude's native Workflow remains authoritative for Claude-hosted DAGs, and self-declared `--host` validation is advisory while Claude tripwire hooks remain the enforcement layer.

## Clean-room constraint

Devchain informed the capability analysis, but this implementation is independently designed for this repository and must not copy Elastic License 2.0 source code.
