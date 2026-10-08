# Orchestrator channel coverage and run-store audit: findings

Date: 2026-10-06. Branch: `feat/zcode-harness`. Plan: [`plans/2026-10-06-mcp-alignment-channel-and-run-store.md`](../plans/2026-10-06-mcp-alignment-channel-and-run-store.md).

Part 1 answers why some routed subagents never talk to their parent. Part 2
([Run-store audit](#part-2-run-store-audit)) covers fourteen further defects found by auditing every run
record on the workstation.

# Part 1: channel coverage

## Question

Some routed subagents ask their parent questions over the `pitwall-channel` MCP server; others never
try. Is the channel server missing from some harness configurations, or is something else going on?

## Short answer

Both, and the larger cause is not installation:

1. **Codex and Copilot parents are never taught the managed path.** The Claude routing skill sends
   interactive work through `dispatch_and_wait`, and a Claude PreToolUse launch guard enforces it.
   The Codex and Copilot routing skills never mention `dispatch_and_wait`; they tell the parent to
   launch shims and "opt in per dispatch with `--routing-ask-support`". A shim launched without that
   flag gives the child no channel instructions at all, so the child never tries.
2. **Five installed harnesses cannot be registered at all.** `CHANNEL_HARNESSES`
   (`src/pitwall/agents/capability_inventory.py`) lists eight harnesses. Antigravity (`agy`), Grok,
   Hermes, goose, and Muse all support MCP servers but have no registration code, so
   `dispatch_and_wait` refuses them and they only ever run as channel-less shims.
3. **dsh and Pi have no MCP client.** They can only use the tier-4 file contract (write an ask file,
   exit 75, resume).

## How a child learns about the channel

`src/pitwall/agents/dispatch.py` (`load_prompt`) appends channel instructions only when ask support
is on:

| Condition | What the child's prompt contains |
| --- | --- |
| ask support off (the default for `pitwall agents dispatch` and every `*-shim.sh`) | nothing about the channel |
| ask support on, harness registered (`mcp_channel_registered`) | tier 1 (`ask_orchestrator` over MCP) plus the tier-4 file fallback |
| ask support on, harness not registered | tier-4 file fallback only |

Ask support is on when the dispatch carries `--routing-ask-support` or `PITWALL_AGENTS_ASK_SUPPORT=1`.
Managed dispatches (`dispatch_and_wait`, `src/pitwall/agents/managed_channel.py:start_dispatch`)
always set it and refuse a harness whose channel is not registered and handshake-verified
(`src/pitwall/agents/mcp_tools.py:_require_child_channel`).

## Evidence from this workstation

Source: every run record under `~/.local/state/pitwall/agents/runs/` created since 2026-09-01
(6,839 runs). "Tier" comes from each run's `channel.json`; "none" means ask support was off.
A managed run is one with a matching `launches/` record.

| Harness | Launch | Tier | Runs | Runs with an ask | Steered runs that acknowledged |
| --- | --- | --- | --- | --- | --- |
| opencode | shim | none | 6,064 | 0 | 0 of 42 |
| codex | managed | 1 | 465 | 69 | 8 of 18 |
| claude | shim | none | 153 | 0 | none steered |
| opencode | managed | 1 | 129 | 8 | 12 of 26 |
| zcode | managed | 1 | 6 | 0 | 0 of 2 |
| zcode, grok, codex, opencode, kimi | shim (one opencode managed) | none | 23 | 0 | 0 of 1 |

Every managed run got tier 1, and children on it do ask. Every shim run had ask support off: 6,239
children were never told the channel exists. The 6,064 OpenCode shim runs are a downstream batch
classifier that calls `opencode-shim.sh` directly. A second downstream evaluation script also calls
the shims directly. Both parse shim output, which is why ask support should not simply become the
default for every shim dispatch (see "Recommendation").

`pitwall agents doctor --json` on this workstation: `channel.mcp_server` PASS; `channel.registration`
PASS for claude, codex, copilot, opencode, kimi, cline, qwen, and zcode.

## Per-harness MCP configuration (verified 2026-10-06)

Each format below was captured by running the harness's own `mcp add` against a scratch `HOME`, or by
loading a scratch config with the harness. Environment passthrough was checked with a stub MCP server
that records the environment it was started with.

| Harness | Config file | Server map | Dispatch identity reaches the server | How verified |
| --- | --- | --- | --- | --- |
| Antigravity | `~/.gemini/config/mcp_config.json` | `mcpServers` (`command`, `args`, `env`, `disabled`) | not verified offline: `agy` starts servers only during a model turn | `agy mcp add` in a scratch `HOME` |
| Grok | `~/.grok/config.toml` | `[mcp_servers.<name>]` (`command`, `args`, `enabled`, optional `env` table) | yes, inherited from the parent environment | `grok mcp add` plus `grok mcp doctor` with the stub server |
| Hermes | `${HERMES_HOME:-~/.hermes}/config.yaml` | `mcp_servers:` (`command`, `args`, `env`, `timeout`) | **only through `env`**: Hermes passes an allowlist (`PATH`, `HOME`, `XDG_*`, ...) plus the entry's `env`, whose `${VAR}` values it expands | `hermes mcp test` with the stub server |
| goose | `${XDG_CONFIG_HOME:-~/.config}/goose/config.yaml` | `extensions:` (`type: stdio`, `cmd`, `args`, `envs`, `env_keys`, `timeout`) | yes, inherited | `goose mcp-probe` with the stub server; `goose info -v` parses the entry |
| Muse | `${XDG_CONFIG_HOME:-~/.config}/muse/settings.json` | `mcpServers` under `"schema_version": 1` (`command`, `args`, `env`, `env_vars`, `startup_timeout_sec`) | not verified offline: the echo provider does not start servers | field names from the Muse 1.4.3 binary |

Two YAML facts shape the design. The agents package does not use PyYAML: it reads YAML with a line
scanner and writes managed blocks marked `# managed by pitwall` (`HermesAdapter._merged`). A one-line
JSON mapping is valid YAML: Hermes and goose both load
`pitwall-channel: {"command": ..., "args": [...]}` correctly, and Pitwall can read it back with
`json.loads`.

## Things that looked like causes and are not

- **ZCode.** Its three failed managed runs were provider HTTP 429s ("Turn execution failed"); the
  three that succeeded had no reason to ask.
- **A literal `${VAR}` placeholder gives the server no tools.** This is deliberate.
  `server_role` (`src/pitwall/agents/mcp_server.py`) classes an unexpanded dispatch id as
  `misconfigured`, pinned by `tests/agents/test_mcp_server.py::RoleTests`. A child whose harness
  failed to expand the variable must not receive the orchestrator tools. One consequence: Hermes
  keeps unset `${VAR}` references literally, so a Hermes *orchestrator* session sees no channel
  tools. Hermes as a *child* works, because the dispatcher always sets the dispatch id.
- **Steering acknowledgement below 100%.** Codex (8 of 18) and OpenCode (12 of 26) children were told
  to call `read_steering`. Some runs ended before their next boundary. Only Claude Code and Codex have
  the tier-2 PreToolUse gate that blocks other tool calls until a steer is acknowledged. That is
  model behavior on a working channel, not a coverage gap.

## Documentation gap found on the way

The registration table in `docs/agents/orchestrator-channel.md` ("Tier 1: the MCP server") has no
ZCode row, although ZCode registration shipped in the ZCode harness commit.

## Recommendation

1. Register the channel for Grok, Antigravity, Muse, Hermes, and goose, with the formats above, so
   each can be a managed child.
2. Teach the Codex and Copilot routing skills the managed path (`dispatch_and_wait`, answer with
   `answer_and_wait`), the same as the Claude skill. Pin it in the skill parity test.
3. Keep ask support opt-in for direct shim callers. Making it the default would change the prompts of
   the downstream batch callers above, with no parent there to answer.
4. Document dsh and Pi as tier-4 only, with the Hermes orchestrator limitation, and add the missing
   ZCode row.
5. Prove each new harness with one live managed dispatch whose child asks and receives an answer.
   Registration and a stdio handshake do not prove delivery.

# Part 2: run-store audit

## Method

Five read-only analysis passes covered every record under `~/.local/state/pitwall/agents/` on
2026-10-06: 6,842 runs created 2026-09-23 to 2026-10-06 (succeeded 6,580, failed 120, cancelled 43,
timed out 33, still `running` 66), 606 managed launch records, the global `events.jsonl` (79,198
events), and the worktree, ledger, lock, and routing-session directories. Each pass covered one theme
across the whole store: failures, lifecycle integrity, false successes, asks and steering, and storage.
Every finding below was re-checked against the files and the source before it was written down.
Dispatch ids are given in full where a reader may want to open the run.

## Summary

| # | Finding | Runs or size affected | Severity |
| --- | --- | --- | --- |
| 1 | The tier-2 steering gate blocks Codex's own channel tools | 8 runs, 7 recorded `succeeded` with the directive unapplied | High: wrong result, deadlock |
| 2 | A standalone run whose supervisor dies stays `running` forever | 62 runs, about 8 days old | High: phantom state, no recovery |
| 3 | Empty output with exit 0 is recorded as success | 70 OpenCode runs | High: wrong result |
| 4 | `wait`/`inbox` declares a live standalone run orphaned | 2 runs that later succeeded | Medium: wrong verdict persisted |
| 5 | A cancelled run reports exit 0 | 10 Codex runs | Medium: callers read success |
| 6 | A steer to a run without the channel is accepted as sent | 65 steers in 43 runs | Medium: silent no-op |
| 7 | Unacknowledged steers vanish at exit and stay in `inbox` forever | 112 steers in 67 runs | Medium: silent drop |
| 8 | Claude prompts over 120 KiB cannot be dispatched | 9 runs | Medium: hard failure |
| 9 | A flag is taken as the prompt path | 10 runs | Low: phantom run and ledger rows |
| 10 | Managed launches store every byte of child stderr twice | 400 MB, about a third of the state directory | Medium: disk |
| 11 | Delivery prompts are kept on every ask-support run | 604 runs | Medium: privacy, contradicts docs |
| 12 | The earlier migration left stale worktree records and artifact paths | 16 records, 1 orphaned 23 MB worktree, 6,120 result files | Low |
| 13 | Launch-guard lock files are never removed | 250 files | Low |
| 14 | `grok-4.5` is offered but rejected; terminal states are defined twice | 1 run; 0 runs | Low |

## 1. The tier-2 steering gate blocks Codex's own channel tools

**What happens.** When a Codex child has an unacknowledged `scope`, `priority`, or `stop` steer, the
PreToolUse gate denies every tool call, including the `read_steering` and `ack_steer` calls that would
clear the steer. The child cannot make progress, cannot acknowledge, and ends its turn. The dispatch is
recorded as `succeeded` with exit 0.

**Evidence.** 8 Codex runs on 2026-10-05 and 2026-10-06 (gpt-6.1-sol, gpt-6-astra, gpt-6-luna) with 42
gate denials between them. None of the 12 Codex blocking steers was acknowledged, against 9 of 10 other
Codex steers. In all 8 runs no channel tool call starts after the first denial. 7 ended `succeeded`;
the children's final text says the hook "rejects `read_steering`, `ack_steer`, `ask_orchestrator`".
Examples: `91c85723-792a-4559-8a3f-a7c3d2401499`, `801927ce-09d5-4961-a9ba-2b171bf1aac0`,
`2d6ac9d4-5bb6-4c5c-a4b5-246e72a6e1f8`.

**Root cause.** `src/pitwall/agents/steer_gate.py:is_channel_tool` exempts a tool only when its
lower-cased name contains `pitwall-channel`. Claude Code keeps the server name as registered
(`mcp__pitwall-channel__read_steering`). Codex replaces the hyphen with an underscore
(`mcp__pitwall_channel__read_steering`); the Codex session logs on this workstation show more than 700
calls in that form. `tests/agents/test_steer_gate.py` pins only the hyphen form.

**Impact.** Every blocking steer to a Codex child deadlocks it, and the orchestrator is told the run
succeeded.

**Fix.** Normalize hyphens to underscores before matching, and pin both spellings in the test.

## 2. A standalone run whose supervisor dies stays `running` forever

**What happens.** A dispatch started by a shim (not by `dispatch_and_wait`) records no process id. If
its supervisor is killed with SIGKILL, nothing ever writes a terminal state. `runs list` and `runs
show` report `running` indefinitely, `runs stop` writes a steer to the dead run and prints "stop
requested", and `runs cleanup` eventually deletes the record without ever recording the failure.

**Evidence.** 62 OpenCode runs created on 2026-09-28 (21) and 2026-09-29 (41), last written 182 to 194
hours before the audit. None has a launch record, `result.json`, or recorded pid; 61 stopped writing
within 0.5 s of `dispatch.started`. They come from a downstream batch classifier that SIGKILLs its whole
process group when a job overruns; 5,722 sibling runs in the same window succeeded. The global event log
holds 52 more dispatches, already cleaned up, that end without a terminal event. Examples:
`7cd4f4c3-12b4-4713-a4d6-747b7d00bd75`, `21cc6caa-a73f-4ad3-8099-c88d6410f3ce`,
`b79f13a9-a29f-4a9c-84f0-6c2012baa975`.

**Root cause.** `dispatch.py:Lifecycle._write` is the only writer of `run.json` and runs inside the
supervisor, which traps SIGTERM and SIGHUP but cannot trap SIGKILL. No pid is recorded, so no other
process can tell the supervisor is gone. `run_store._managed_run_is_live` and
`managed_channel._launcher_alive` cover managed launches only. The one liveness check for standalone
runs, `migrate._classify_runs`, runs only during migration.

**Impact.** Phantom running dispatches, a `runs stop` that reports success against a dead process, and
lost evidence of the failure.

**Fix.** Record the supervisor pid and its start identity in `run.json`. Add a reconciliation in
`run_store` that records a non-terminal standalone run as `failed` (with an `abandoned.json` reason)
when its recorded supervisor is gone, or, for runs that predate the pid, when nothing in the run has
changed for 24 hours. Call it from `runs list`, `runs show`, `runs stop`, `runs cleanup`, and the
managed wait.

## 3. Empty output with exit 0 is recorded as success

**What happens.** An OpenCode child that prints nothing and exits 0 is recorded as `succeeded`, and the
shim prints `SHIM-DONE exit=0`.

**Evidence.** 70 runs on 2026-09-28 and 2026-09-29 (space-bunny-free 66, longcat-2.5-preview-free 4).
68 have a stderr that is only the `> build · <model>` banner; 27 ran 300 to 569 seconds before exiting
empty. No other provider produced a zero-byte success. Examples:
`17fa08eb-f824-4d02-add0-e2aa51e1db77`, `57eeb7e8-60e9-45db-bb75-c4f6e5107bef`,
`1bd057f2-6583-4493-87dc-a8f5ad2528c7`.

**Root cause.** `dispatch.py:_LegacyDispatch.apply_soft_denial` converts a zero exit to exit 77 only when
an adapter's `detect_soft_denial` recognizes a marker in one output tail. The OpenCode adapter has no
override, and the hook sees each stream separately, so it cannot express "nothing on stdout".

**Impact.** Callers that trust exit 0 consume an empty answer as a result.

**Fix.** Add an adapter flag, `empty_stdout_is_failure`, which `apply_soft_denial` checks against the
child's stdout byte count, and set it for OpenCode.

## 4. `wait`/`inbox` declares a live standalone run orphaned

**What happens.** Waiting on a dispatch that was started by a shim, not by `dispatch_and_wait`, treats
the missing launcher record as a dead launcher. Two seconds later it returns an `orphan` event and writes
`orphan.json` into the live run directory.

**Evidence.** 2 runs. `1b01266f-9ec5-4e29-a42b-461898e735a5` was declared orphaned on 2026-10-01 and
succeeded 1 minute 42 seconds later; `69191471-7fa9-4f95-af09-19d9edb97c02` succeeded 7 seconds after
its verdict. Neither has a launch directory; both keep the false `orphan.json`.

**Root cause.** `managed_channel.py:_next_event` calls `_launcher_alive`, which returns False when there
is no launcher record at all. After `ORPHAN_GRACE_SECONDS` the wait calls `_write_orphan`, which writes
into both the launch directory and the run directory.

**Impact.** The orchestrator abandons a run that is still working, and the run carries a false verdict.

**Fix.** When a run exists and has no launch directory, never apply the launcher check. End the wait
early only when finding 2's reconciliation shows the supervisor is gone.

## 5. A cancelled run reports exit 0

**What happens.** When a run is aborted (a `stop` steer not honored in time, or SIGTERM to the
supervisor), Codex exits 0 on SIGTERM. That 0 is recorded as the exit code and printed as `SHIM-DONE
exit=0`, although the run state is `cancelled`.

**Evidence.** 10 Codex runs from 2026-09-26 to 2026-10-06 with state `cancelled` and exit 0; 4 also
have launcher `exit.json` return code 0. Examples: `2bc5b7a3-2b35-47c4-ad58-6e77e11b42c7`,
`7cbb0426-71ce-4ea4-b129-0ac238d007c6`.

**Root cause.** `process.py:run_process` keeps the child's own return code when `aborted` is set. The
pre-spawn abort path in `dispatch.py:launch_child` already uses 143.

**Impact.** Shim callers, transport agents, and managed launchers read a cancelled run as a success.

**Fix.** When the run was aborted and the child exited 0, record 143 (128 + SIGTERM).

## 6. A steer to a run without the channel is accepted as sent

**What happens.** `pitwall agents steer` and `steer_and_wait` accept a `note`, `scope`, `priority`, or
`budget` steer for a run whose child was never told about the channel. The steer can never be read. A
`stop` steer only ever takes effect as a forced abort when its deadline passes.

**Evidence.** 65 steers in 43 runs that have no `channel.json` (OpenCode 62 steers in 42 runs, ZCode 3
in 1); 45 of them are non-stop kinds; none was acknowledged. The CLI printed "steer NNNN sent" for
each. Examples: `24d7aeb6-bb30-4743-9895-ce53969561eb`, `2a5e78e5-1e47-47ab-a782-bb48a3df5336`,
`cb9fb8d2-dd3d-4dfe-a5b5-9c6653a17622`.

**Root cause.** `channel.py:send_steer` and `managed_channel.py:steer_once` check only the run state,
never whether the run has a channel (`load_channel_config`).

**Impact.** The orchestrator believes it redirected work that never heard the redirect.

**Fix.** Refuse non-stop steers for a run without `channel.json`, with a message that says why. Accept
`stop`, and say that it aborts the run at its deadline.

## 7. Unacknowledged steers vanish at exit and stay in `inbox` forever

**What happens.** A blocking steer still unacknowledged when the run ends produces no event if its
deadline had not passed yet. The terminal event and the managed terminal payload do not mention it.
`pitwall agents inbox` lists it as `ignored` indefinitely, because the inbox scans finished runs too.

**Evidence.** 112 unacknowledged blocking steers, all in finished runs (67 runs: Codex 12, OpenCode 97,
ZCode 3). 41 never produced a `steer.unacked` event; 39 of those are `scope`, `priority`, or `stop`.
Examples: `0fef0e3c-a63e-41dd-b325-352683063271` (scope steers sent 524 s and 84 s before the end),
`14f27fd4-af10-4686-95a9-99afbdd7afab`, `1b629926-2d92-4623-acee-8ff2b770ad6a`.

**Root cause.** `channel.py:SteerWatcher` emits `steer.unacked` only while the supervisor polls and only
after the steer's deadline. `dispatch.py:_write_terminal` does not report steers still pending at exit.
`channel.py:read_channel_inbox` applies no state filter when listing every run.

**Impact.** The orchestrator cannot tell that its direction was dropped, and the inbox fills with noise.

**Fix.** At exit, emit `steer.unacked` (marked `atExit`) for every steer still pending, add their ids
to the terminal event, the ledger row, and the managed terminal payload, and skip finished runs in the
all-runs inbox.

## 8. Claude prompts over 120 KiB cannot be dispatched

**What happens.** The Claude shim refuses any prompt over 120 KiB with exit 64, "prompt too large for
argv delivery".

**Evidence.** 9 Claude Opus runs created within 21 seconds on 2026-10-05, all exit 64. Examples:
`a9221646`, `b0b1c11e`, `f8883513` (prefixes; `runs show` accepts them).

**Root cause.** `harnesses/claude.py:ClaudeAdapter` is the only adapter of a stdin-capable CLI that
passes the prompt as one command-line argument (`prompt_delivery = "argv"`). Linux caps one argument
at 128 KiB, so `harnesses/base.py:check_prompt_size` refuses above 120 KiB. `claude -p` reads its
prompt from standard input, the way Codex, OpenCode, and goose prompts are already delivered.

**Impact.** Large review and planning prompts cannot go to Claude through Pitwall.

**Fix.** Deliver the Claude prompt on stdin and update the registry's `promptDelivery` for Claude.

## 9. A flag is taken as the prompt path

**What happens.** `codex-shim.sh --help` or `codex-shim.sh -m gpt-6-sol prompt.md` treats `--help` or
`-m` as the prompt file. Pitwall creates a run, fails it with exit 66 "unreadable prompt source", and
writes a ledger `finished` row for a model that was never invoked; with `-m` first, the prompt path is
recorded as the model.

**Evidence.** 10 runs (`--help` 7, `-m` 3) across Codex 3, ZCode 2, OpenCode 2, Grok 2, and Claude 1.
Examples: `1fe4a19e-e68c-4c6f-89b1-360d0887c1eb`, `437ab744-6b2f-43c0-8202-9878bdf28993` (model field
holds a file path), `8efb0a8c`, `44aa0191`.

**Root cause.** Each adapter's `parse` takes the first positional argument as the prompt source, and
`dispatch.py:_LegacyDispatch.parse_request` accepts it unchecked; `open_run` then creates the run before
`load_prompt` fails.

**Impact.** Phantom failed runs and ledger rows that distort usage and model statistics.

**Fix.** In `parse_request`, print usage and exit 0 for `-h` or `--help`, and reject any other prompt
source or model that starts with `-` (except `-` for stdin) as a usage error before a run exists.

## 10. Managed launches store every byte of child stderr twice

**What happens.** `dispatch_and_wait` starts the supervisor with its stdout and stderr redirected to
`launches/<id>/launcher.stdout.log` and `launcher.stderr.log`. The supervisor copies every byte of the
child's output both into the run's own logs and onto its own stdout and stderr, so each managed run's
output is stored twice.

**Evidence.** 608 `launcher.stderr.log` files hold 400 MB; the run's own `stderr.log` for the same ids
holds the same 400 MB, byte-identical for the largest pairs (for example
`670bdbed-c1ea-4e3f-b2fe-8f35bb383c48`). The whole state directory is about 1.2 GB. No documentation
mentions these files, and nothing reads them back.

**Root cause.** `process.py:_pump` always writes each chunk to the run log and to `terminal_fd`, and
`dispatch.py:launch_child` passes fds 1 and 2. For a managed launch those are the launcher log files
opened in `managed_channel.py:start_dispatch`.

**Impact.** A third of the state directory is duplicate data, and secret-shaped strings that providers
echo into stderr are stored in a second, undocumented place.

**Fix.** Mark managed launches in the supervisor's environment and, for them, stop copying child output
onto the supervisor's own streams. The supervisor's own messages and the `SHIM-DONE` sentinel still
reach the launcher logs.

## 11. Delivery prompts are kept on every ask-support run

**What happens.** Every run with ask support keeps `prompt.deliver.md`, the full prompt plus the channel
instructions, after it finishes. `docs/agents/architecture.md` says prompt bodies are not retained unless
explicitly requested, and `docs/agents/run-records.md` does not list the file.

**Evidence.** 604 runs, every one with `retained: false` in `request.json`; 600 are finished. 2.1 MB in
total, with more than 2,300 absolute home-directory paths inside. Examples:
`4d7eac93-663d-4704-83a5-4498749f590c`, `da203220-af7e-42e4-84fb-6a5554778d50`.

**Root cause.** `dispatch.py:_LegacyDispatch.deliver_prompt` writes the file for every ask-support run
so a paused run can resume from exactly what the child saw. `_write_terminal` removes it only for
file-delivery harnesses with a stdin or private prompt.

**Impact.** Prompt bodies persist against the documented privacy behavior.

**Fix.** Remove the delivery prompt at every terminal state unless `--routing-retain-prompt` was passed.
Paused runs keep it, because pausing goes through `_finish_paused`, not `_write_terminal`. Document the
file in `run-records.md`.

## 12. The earlier migration left stale worktree records and artifact paths

**What happens.** Two leftovers from the move to the current state directory:

- 16 isolated-workspace records still name branch prefix `pitwall-agent-routing/` and a path under
  the old state directory, which no longer exists. One of them, `75694a97-a293-4a23-a1a4-7a75e7c2fcba`
  (23 MB, unapplied changes), has its directory under the new `worktrees/` but no branch or `.git`
  link in its repository. `runs discard` cannot verify it, and `runs cleanup` would remove the record
  and leave the directory.
- 6,120 of 6,778 `result.json` files list `artifacts` paths under the old state directory, all missing.
  The managed terminal payload returns them to the orchestrator.

**Root cause.** `migrate.py:_migrate_worktree_branches` renames only `LEGACY_BRANCH_PREFIX`
(`model-routing/`), and leaves the record untouched when the branch is already gone.
`managed_channel.py:_terminal_payload` returns the `artifacts` stored at write time, not the run's
current location.

**Impact.** A retained worktree that cannot be discarded, and artifact paths that point nowhere.

**Fix.** Make the migration accept every earlier branch prefix and rewrite a record's path to the moved
worktree even when the branch is gone. Build the terminal payload's `artifacts` from the run's current
directory.

## 13. Launch-guard lock files are never removed

**What happens.** The Claude launch guard creates `routing-sessions/.<session-id>.lock` for every
session and never deletes it.

**Evidence.** 250 zero-byte lock files (131 created on 2026-10-05) against 3 live session markers.

**Root cause.** `plugins/claude/hooks/launch_guard_markers.py:marker_lock` creates the lock file;
`launch_guard_leases.py` removes only the marker (`deactivate_marker`, `clear_if_idle`, and the lease
release).

**Impact.** Unbounded growth of the routing-session directory. Small today.

**Fix.** Remove a session's lock file when Claude ends the session, and sweep lock files whose marker is
gone and that are older than seven days.

## 14. `grok-4.5` is offered but rejected; terminal states are defined twice

**What happens.** The registry and routing skills offer `grok-4.5`, which the Grok CLI rejects
("Couldn't set model 'grok-4.5': unknown model id"; `grok models` lists only `grok-4.7`). Separately,
`dispatch.py` and `managed_channel.py` each define `TERMINAL_STATES`, and the sets differ: the
dispatch copy includes `blocked`, which no transition can reach.

**Evidence.** Run `76d77e92` on 2026-09-30. `TERMINAL_STATES` at `dispatch.py:54` and
`managed_channel.py:58`.

**Root cause.** `docs/agents/model-facts/families/grok/facts.json` still marks `grok-4.5` `current`, and
the registry is generated from it. The two state sets were written separately.

**Impact.** A routing choice that always fails; a latent disagreement about which states are final.

**Fix.** Retire `grok-4.5` through the model-facts pipeline, with a cited `retires` fact, and
regenerate. Define `TERMINAL_STATES` once in `run_store.py` and import it everywhere.

## Problems outside Pitwall's code

- **Disk space.** The root filesystem is 97% full. Codex logged "No space left on device" in 9 runs,
  and 4 runs failed on inotify limits. The largest reclaimable items are about 1.2 GB of migration
  backups from 2026-08-31 and about 116 MB of worktrees whose source repository is gone.
- **The downstream batch classifier** SIGKILLs its shims (the cause of finding 2's runs), sends retry
  prompts that contain only chunk ids (45 runs "succeeded" with "please resend the data"), disables
  every tool, and hit OpenCode's free-tier gate 44 times.
- **Provider-side failures.** About 90 runs failed on quotas, rate limits, outages, an invalid API key,
  a capacity error, and one policy refusal. They are recorded correctly.

## Checked and not a Pitwall defect

- **Two Codex runs that sat silent for their full timeout** (`b328603b-1978-4a94-8ba4-6a584f8143e4`,
  three hours). Codex read the prompt ("Reading prompt from stdin..." is its normal banner), logged
  "failed to refresh available models: request timed out" nine minutes later, and hung inside its own
  network call. Pitwall delivered stdin and closed it.
- **Model substitution:** none in 6,575 runs whose harness reports the model.
- **Output truncation:** none; every `stdout.log` matches the size and hash in its `result.json`.
- **Ask handling:** 119 asks, all resolved (87 by the orchestrator, 32 by default at their deadline),
  none malformed, past the cap, or dead-lettered; every escalation fired at half its deadline.
- **Permissions:** every run file is 0600 and every run directory 0700.
