# The orchestrator channel

A dispatched subagent can ask the orchestrator a blocking question mid-task and receive an answer, instead of guessing on decisions that are dangerous or irreversible. The orchestrator can also steer a running dispatch. The four primitives:

- **ASK** — the subagent asks one blocking question with explicit options, a mandatory default, and a deadline.
- **ANSWER** — the orchestrator (or the operator) picks one option, or the ask's deadline passes and its default is applied.
- **STEER** — the orchestrator sends a directive (`budget`, `note`, `priority`, `scope`, `stop`); `stop` never bypasses the completion sentinel.
- **REPORT** — the dispatch finishes (or pauses) with its normal sentinel; ask/steer aggregates land in the ledger.

Mailbox files under `runs/<dispatch-id>/mailbox/` are the single source of truth. Every surface — the operator CLI, the loopback broker, the stdio MCP server, the tier-2 hook gate, and the workflow scheduler — is a view over those files.

Delivery tiers:

| Tier | Transport | Availability |
|---|---|---|
| 1 | The child-side `pitwall-channel` stdio MCP server (`ask_orchestrator` blocks in-session) | harnesses with the server registered (`pitwall agents setup mcp`); this alone does not prove parent delivery |
| 4 | Write `mailbox/asks/NNNN.json` and exit 75; the run pauses | every dispatch with ask support; the prompt always carries this contract |

(Steering delivery spans tiers 1, 2, and 4; see the [Steering](#steering) section.)

## Mailbox layout

```text
${PITWALL_AGENTS_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}/pitwall/agents}/runs/<dispatch-id>/
  channel.json    # what the dispatcher decided: ask cap, timeout, tier
  mailbox/
    asks/0001.json        # written by the subagent (or its MCP tool)
    answers/0001.json     # one answer per ask; exclusive-create, races cannot double-write
    steer/0001.json       # written by the orchestrator
    acks/0001.json        # subagent acknowledgement of steering
    dead-letter/…         # schema-invalid or oversized writes, quarantined
  mailbox.json    # counts, unresolved ids, sequence gaps
```

`channel.json`, `pause.json`, and `resume.json` sit beside the other run artifacts. Directory mode is `0700` and file mode `0600` throughout. Every mailbox document is size-capped at 64 KiB and schema-validated on write and on read; invalid documents are quarantined to `dead-letter/` (newest 20 kept) and never interpreted. Writes publish with `os.link` (exclusive creation), so concurrent writers sequence monotonically instead of overwriting: one answer wins per ask, and the per-run ask cap holds under floods. Sequence numbers never reuse a number consumed by a quarantined file; any residual gap is reported as `sequenceGaps`, not treated as an error.

`mailbox/` is created by the first write (an ask, an answer, or an accepted steer). A read never creates it: `inbox`, the managed waits, the exit report, and the broker's replay check open only a mailbox that already exists, so looking at a run that was dispatched without the channel never turns it into a channel run.

## Enabling asks

```bash
pitwall agents dispatch codex prompt.md --routing-ask-support            # flag
pitwall agents dispatch codex prompt.md --routing-max-asks 3             # cap: 1..50
PITWALL_AGENTS_ASK_SUPPORT=1 ...                                        # environment equivalent
PITWALL_AGENTS_MAX_ASKS=3 ...                                           # environment equivalent
```

The ask cap defaults to 5 per run and survives resume via `channel.json`/`resume.json`. Ask-prone dispatches should use `--routing-workspace isolated` so a paused run resumes against its own worktree, and `runs diff` can show the operator exactly what the subagent changed so far.

## Tier-4 contract

When ask support is on, the dispatcher appends the tier-4 block to the prompt. The subagent writes one `mailbox/asks/NNNN.json` and exits 75 immediately. The supervisor then:

- classifies exit 75 with an ask written during the attempt as a **pause** (`pause.json`, `reason: "question"`; no `result.json` — the resumed run's final result covers the whole dispatch);
- classifies any other failed exit that leaves a pending ask as a pause with `reason: "orphaned-ask"`, so `runs resume` can default the orphaned ask and continue;
- quarantines tier-4 asks numbered above the per-run cap at the pause boundary;
- always emits the `SHIM-DONE exit=<n>` sentinel.

An ask counts whether or not it has been answered yet. Asks are answered while an attempt runs, so an answer can arrive before the supervisor has seen the exit; the run still pauses, `pendingAskIds` is empty, and the resumed attempt delivers the answer. An exit 75 with no ask from that attempt stays a contract-abuse failure, and so does one whose only open ask was past the per-run cap.

## Deadlines and defaults

An ask's deadline is capped by the attempt timeout (D1): `min(3600, 15% of the remaining timeout at ask creation)`. With a 600 s timeout, the cap is 90 s. `channel.json` records the attempt timeout and start so every surface computes the same effective deadline; the broker and MCP server clamp at write time, and a directly-written tier-4 file is clamped when read.

Where the default applies:

- **Tier 1**: the MCP `ask_orchestrator` call applies the default when its deadline passes and returns it to the caller.
- **Tier 4**: `runs resume` applies defaults to expired asks before resuming, printing `ask NNNN expired; applied its default 'x'`.
- **Workflows**: the scheduler's wait loop applies them (see `docs/workflows.md`).
- `inbox` is read-only and marks expired asks. The all-runs inbox lists live and paused runs and skips finished ones; the named-run inbox (the MCP `inbox` tool with `dispatch_id`, or `GET /inbox` for one run) still shows a finished run's asks and steers.

Answer provenance (`answered_by`) is `operator`, `orchestrator`, `default`, or `policy:<model>`. A policy answer may never select `abort`.

## Managed parent-child exchange

A parent with the `pitwall-channel` MCP server starts an interactive external-model run with `dispatch_and_wait`. The tool starts the existing dispatcher independently, then returns an `ask`, terminal, orphan, or bounded `still_running` event. A terminal event, and an orphan whose run directory has a mailbox, carry `unacked_steer_ids`: the blocking steers the child never acknowledged (an empty list when all were acknowledged). The parent handles an ask with `answer_and_wait`, reattaches with `wait_dispatch`, and sends a directive with `steer_and_wait`. The child uses `ask_orchestrator` and remains blocked until an answer or its validated default deadline. A repeated ask after parent reconnection is possible; answer writes are exclusive and conflicting answers are rejected. MCP request cancellation ends the parent wait, not the child run.

`wait_dispatch` and the other waits also work on a run that was not started through `dispatch_and_wait`, for example one a shim started from a terminal. That run has no launcher sidecar to watch, so its wait ends early only when its own supervisor is gone: the wait records the run as failed (see [run records](run-records.md#abandoned-runs)) and returns an `orphan` event carrying the reason. A live supervisor is never orphaned, however long the wait; a `result.json` published while the wait was deciding wins over the orphan; and a wait on a run that was already reconciled, or that reached a terminal state without writing `result.json` because its supervisor died, answers at once with the recorded reason.

Managed interactive launch requires a configured child MCP channel. Registration and a successful local stdio handshake are prerequisites, not proof of a live host exchange. Native Claude `Agent` tasks use Claude's own lifecycle rather than Pitwall dispatch identities; current host probes showed completion messages, not a mid-task ask-to-parent channel. The legacy `inbox` and CLI answer commands below remain for inspection and recovery; they are not the routine parent orchestration path.

When an ask's deadline passes, the child applies its validated default and the parent's next wait returns a `defaulted` event for that ask once. Every event also lists `defaults_applied`, so a parent that reconnects still sees every default.

The Claude plugin's launch guard keeps its per-session state under `${PITWALL_AGENTS_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}/pitwall/agents}/routing-sessions/` (`PITWALL_AGENTS_ROUTING_MARKER_DIR` overrides the directory): a marker, `<session-id>.json`, and a lock file, `.<session-id>.lock`, that serializes updates from sibling tool calls. Session end removes both files. Each marker activation also sweeps lock files whose marker is gone and that were created more than seven days ago; a lock someone holds is skipped, and a caller that was waiting on a swept lock retries on a fresh one. A caller still waiting when its session ended drops its update instead of re-creating the lock, so a lock file outlives its session only when the session crashed, and the sweep then removes it.

Session isolation: run state is private to your OS user (`0700`/`0600`). It is not isolated between two sessions of the same user. Any local session that knows a full dispatch ID can wait on, answer, or steer that run. A dispatch ID is a handle, not a credential.

## Tier 1: the MCP server

`pitwall agents setup mcp` (`pitwall agents install` does this too) registers one stdio server named `pitwall-channel` per harness at user scope. The server is launched as `pitwall mcp serve channel` (`pitwall agents mcp` runs the same server); every entry below carries the absolute path of the `pitwall` command (when `pitwall` is on `PATH`; `setup mcp` stops with exit `2` otherwise, while `pitwall agents install` falls back to the bare command `pitwall`, which doctor then reports as a `channel.registration` `WARN` because the path is not absolute) and the arguments `mcp serve channel`.

| Harness | File | Entry keys this server relies on |
| --- | --- | --- |
| Claude Code | via `claude mcp add-json --scope user pitwall-channel <json>` (the tool never edits `~/.claude.json` directly) | `env` with `${VAR:-}` forwarding |
| Codex | `${CODEX_HOME:-~/.codex}/config.toml` managed block | `env_vars`, `startup_timeout_sec = 30`, `tool_timeout_sec = 3660` |
| Copilot CLI | `~/.copilot/mcp-config.json` → `mcpServers` | `env` with `${VAR}` forwarding, `tools: ["*"]` |
| OpenCode | `opencode.json` → `mcp` (strict JSON) | `command: [CMD, "mcp", "serve", "channel"]`, `environment` with `{env:VAR}` |
| Kimi | `${KIMI_CODE_HOME:-~/.kimi-code}/mcp.json` → `mcpServers` | `toolTimeoutMs: 3660000` (parent env is inherited) |
| Cline | `${CLINE_MCP_SETTINGS_PATH}` else `${CLINE_DATA_DIR:-${CLINE_DIR:-~/.cline}/data}/settings/cline_mcp_settings.json` | `timeout: 3660` |
| Qwen Code | `${QWEN_CODE_HOME:-~/.qwen}/settings.json` → `mcpServers` (other settings keys are preserved) | `timeout: 3660000` (parent env is inherited) |
| ZCode | `~/.zcode/cli/config.json` → `mcp.servers` | `enabled: true`, `timeoutMs: 3660000` (parent env is inherited) |
| Grok Build | `~/.grok/config.toml` managed block | `enabled = true` (parent env is inherited) |
| Antigravity (`agy`) | `~/.gemini/config/mcp_config.json` → `mcpServers` | `disabled: false` (parent env is inherited, so no `env` block) |
| Muse Code | `${XDG_CONFIG_HOME:-~/.config}/muse/settings.json` → `mcpServers` | `env_vars`, `startup_timeout_sec = 30`; `schema_version: 1` is added when the file has none, because Muse rejects settings without it |
| Hermes Agent | `${HERMES_HOME:-~/.hermes}/config.yaml` → `mcp_servers` | `env` with `${VAR}` forwarding, `timeout: 3660` |
| goose | `${XDG_CONFIG_HOME:-~/.config}/goose/config.yaml` → `extensions` | `cmd`, `timeout: 3660`, empty `envs` and `env_keys` (parent env is inherited) |

Live delivery verified 2026-10-07: Grok Build (dispatch db420b68): an `ask_orchestrator` call over the
channel, answered by the orchestrator, then the child's terminal reply. Antigravity, Muse Code, Hermes
Agent, and goose are registered and covered by the hermetic tests, but not verified live: no account
for them is available on the verifying workstation.

dsh and Pi have no MCP client, so they have no tier-1 channel and cannot be managed children; a
dispatch with ask support gives them the tier-4 file contract only. Hermes leaves an unset `${VAR}`
unexpanded, which the server treats as misconfigured, so a Hermes *orchestrator* session sees no
channel tools; Hermes as a dispatched child works because the dispatcher always sets the dispatch id.

```bash
pitwall agents setup mcp [--harness ID]... [--command PATH] [--dry-run] [--yes] [--remove]
```

Without `--harness` it targets every channel harness found on `PATH`; with `--remove` and no `--harness` it targets all thirteen channel harnesses in the table. It prints a diff for each file and asks before writing (`--yes` skips the prompt; a non-interactive run without it exits `2`; `--dry-run` writes nothing). Changing an existing file first copies it to `<file>.bak.<UTC timestamp>`.

`--command PATH` sets the executable written into each entry. The default is the absolute path of `pitwall` found on `PATH`, resolved through symlinks; pass `--command` to register a different install, or when `pitwall` is not on `PATH` (the default lookup exits `2`). Keep the file name `pitwall`: an entry is recognised as ours only when its command's file name is `pitwall` and its arguments are `mcp serve channel`, so an entry written with another file name is refused as foreign on the next run and `--remove` will not remove it. `--remove` deletes the `pitwall-channel` entry that pitwall wrote and registers nothing; it never removes an entry pitwall did not write.

Registration refuses a config it cannot edit safely, names the file, and leaves it unchanged:

- An existing `pitwall-channel` entry that pitwall did not write (it does not run `pitwall mcp serve channel`) is never replaced; rename or remove it.
- The JSON harnesses (everything above except Codex, Grok, Hermes, goose, and Claude Code) need strict JSON: comments, trailing commas, or a top level that is not an object are refused.
- Codex and Grok keep the entry in a block between `# >>> pitwall-channel (managed by pitwall agents install)` and `# <<< pitwall-channel`. A harness that rewrites its config can drop those markers (`grok mcp add` does); the next registration then adopts an unmarked `[mcp_servers.pitwall-channel]` table that is ours and rewrites it with markers. It scans the file text for the table's extent and checks the result against the parsed document, so a layout it cannot follow, such as a quoted table name or a multi-line string, is refused with a request to remove the table by hand.
- Hermes and goose keep the entry as one `  pitwall-channel: {...}` line under a `# managed by pitwall` comment, so the rest of the YAML keeps its formatting and comments. The edit is refused when the file does not end with a newline, has a top level or a section that is not a mapping, has a duplicate top-level section, indents the section's entries by anything but 2 spaces, has a top-level line it cannot place, is not valid YAML, or would not read back as intended after the write. A section header that pitwall adds carries the same comment, so removal drops only a header it added.

An error message names the file and, for a parse failure, the line and column (or YAML error class); it never quotes the file's contents. `pitwall agents install` plans every registration before it writes anything, so one refused config stops the whole install (exit `2`, nothing written) with `cannot register the channel server for <harness>: <reason>`; `setup mcp --remove` skips a harness it cannot read and continues.

The server never opens a socket (stdio only) and picks its role from the environment:

- `PITWALL_AGENTS_CHANNEL_DISPATCH_ID` — the dispatch's UUID: the server exposes the **subagent** tools `ask_orchestrator` (blocking ask with default-on-deadline), `read_steering`, and `ack_steer`.
- unset or empty: the **orchestrator** tools `dispatch_and_wait`, `answer_and_wait`, `wait_dispatch`, `steer_and_wait`, plus recovery tools `inbox` and `answer_ask`.
- any other value: no tools (misconfigured).

### Protocol

It serves MCP `2026-07-28` statelessly: every request carrying `io.modelcontextprotocol/protocolVersion` in `_meta` (with `io.modelcontextprotocol/clientCapabilities`, an object) gets `resultType` and `serverInfo`, and `server/discover` and `subscriptions/listen` are served. A request whose version is not `2026-07-28` gets `-32022` with the supported list; a modern request without client capabilities gets `-32602`. It also serves the legacy `initialize` handshake (2024-11-05 to 2025-11-25), so existing harness registrations keep working; the two eras are told apart per request by the `_meta` version key.

JSON-RPC handling is strict:

- A request `id` must be a string or an integer. A boolean, `null` on a request that carries an `id` member, a float, or a structure gets `-32600` with a `null` id, and an invalid id is never echoed back. A malformed `notifications/cancelled` gets no reply.
- A line over 1 MiB gets `-32600`; a line that is not JSON gets `-32700`; an unknown method gets `-32601`; an unknown tool name gets `-32602`.
- `notifications/cancelled` for an in-flight call ends that call without a response. The server answers `subscriptions/listen` with the honored subset (none, because the tool list never changes) and holds it open until it is cancelled or the client closes stdin.

Tool calls are checked before they run, and a refusal is an `isError` tool result, not a protocol error:

- **Arguments.** `arguments` must be an object and may carry only the properties the tool declares; otherwise the result names the allowed properties (`unknown argument; allowed: ...`). The handlers then validate types and ranges with fixed messages, for example `wait_seconds must be between 0 and 300`, `extra_args must be a list of strings`, and `extra_args cannot override managed routing options`.
- **Rate limit.** One token bucket per server process admits `CALLS_PER_SECOND = 5` calls a second with a burst of `CALL_BURST = 20`; a call over budget returns `rate limited; retry shortly` without running.
- **Internal errors.** An unexpected failure inside a tool returns `internal error; see the server's stderr log` and writes the traceback to stderr only. Messages from validation errors that pitwall authored (route, registry, and profile errors) are returned; anything else is logged, never echoed to the client.
- **Annotations.** `tools/list` declares each tool's `title` and hints. `inbox` is `readOnlyHint: true`; every other tool is `readOnlyHint: false` because it can write mailbox or run state. `dispatch_and_wait` alone is `destructiveHint: true` and `openWorldHint: true` (it launches an external model that can edit the workspace); `read_steering`, `ack_steer`, `answer_and_wait`, and `answer_ask` are `idempotentHint: true`; `ask_orchestrator`, `wait_dispatch`, and `steer_and_wait` are not.

The dispatcher gives every harness `PITWALL_AGENTS_CHANNEL_DISPATCH_ID`, `PITWALL_AGENTS_CHANNEL_STATE_ROOT`, and `PITWALL_AGENTS_CHANNEL_ATTEMPT` (it no longer forwards the dispatcher's own identity variables, so a harness can dispatch again without colliding). Claude Code additionally gets `MCP_TOOL_TIMEOUT=3660000` so a blocking ask can outlast the harness's tool timeout. A harness that drops these variables leaves its subagent with no ask tool; the tier-4 file contract in the prompt is the fallback.

`pitwall agents doctor` reports `channel.mcp_server` (a local stdio handshake per role and protocol era), `channel.registration` per installed channel harness, and `channel.interactive_delivery` as untested by default; see [Doctor](doctor.md#channel-checks). A fresh installed-host exchange is required to prove live delivery. Tier 1 calls emit `ask.escalated` once an ask has waited half its effective deadline, so an operator hook can pull the human in before the default fires.

## Recovery and operator commands

```bash
pitwall agents inbox [--json]           # unresolved asks and unacked steers across live and paused runs (finished runs are skipped)
pitwall agents answer <dispatch-id> <ask-id> <choice> [--note TEXT] [--by operator|orchestrator] [--json]
pitwall agents runs diff <dispatch-id>  # live changeset for a running/paused isolated run
pitwall agents runs resume <dispatch-id>
```

`inbox` lists live and paused runs and skips finished ones; the named-run inbox (the MCP `inbox` tool with `dispatch_id`) still shows a finished run's asks. It shows each ask's options, default, default rationale, remaining time on the effective deadline (`expired` when past), a `context:` pointer to `runs diff`, and `ignored` for steers the subagent never acknowledged in time. Sequence gaps print as warnings on stderr. A shared-workspace run explains that its ask files should be inspected in place.

## Broker endpoints

The loopback receiver (`pitwall agents broker receiver`) serves the channel for programmatic answerers:

| Endpoint | Behavior |
|---|---|
| `POST /asks` | file one ask (202). Deadline clamped by `channel.json`. `ask_cap_exceeded` → 429; a second open ask → 409 `ask_already_open`; schema faults are dead-lettered with 400 |
| `POST /answers/<ask-id>` | file one answer (202). Matching retry after a crash → 202 with `"idempotent": true`; conflicting retry → 409 `ask_already_answered` (stored answer kept, nothing quarantined) |
| `POST /steer` | file one steering directive (202). A malformed steer or body → 400 (`invalid steer schema`); a steer the run can never read (see [Steering](#steering)) → 409 with the reason; an unknown `dispatch_id` → 404 |
| `GET /inbox` | the same payload the CLI prints (finished runs are skipped unless one run is named) |

All POSTs require HMAC signing (`PITWALL_AGENTS_WEBHOOK_SECRET`), are capped at 1 MiB, and a retried `delivery_id` is a 409 `replayed_delivery_id` — the delivery journal survives crashes, so a killed receiver never duplicates or loses a message.

## Steering

The orchestrator can redirect a running (or paused) dispatch without killing it. STEER kinds: `budget`, `note` (advisory), `priority`, `scope`, and `stop` — `stop` asks the model to wrap up and exit normally; it never bypasses the completion sentinel.

```bash
pitwall agents steer <dispatch-id> --kind scope --message "SQLite only" [--no-ack] [--deadline SECONDS]
pitwall agents runs stop <dispatch-id> [--message TEXT] [--grace SECONDS]
```

`steer` returns as soon as the steer is written. It defaults to `--deadline 300` seconds and requires an acknowledgement before that deadline unless `--no-ack` is given (`steer.unacked` fires when the deadline passes without one); only the managed `steer_and_wait` waits. `runs stop` writes a `stop` steer with a grace window (default 60 s) and prints the receipt line. The CLI refuses a steer to a terminal run (`run <id> is <state>; steering applies to running or paused runs`), the MCP tools likewise (`run <id> is <state>; steering applies to a live or paused run`), and a paused run refuses `runs stop` (nothing is running). Before it steers, `runs stop` records a run whose supervisor has vanished as failed (see [run records](run-records.md#abandoned-runs)) and says so instead of sending a steer nobody will read. A terminal run whose recorded harness is still running (its supervisor was killed and the failure was recorded without ending it) is not refused: when the run record names the harness's process group, `runs stop` ends that group, SIGTERM then SIGKILL after the grace window, and reports it. Records written before process groups were recorded name none, so `runs stop` cannot reach those harnesses.

**What a run can be steered with.** Only a run that recorded `channel.json` (it was dispatched with ask support) has a reader for `budget`, `note`, `priority`, and `scope` steers. Every surface applies the same rule (`channel.steer_refusal`) after it validates the steer:

| Run | `stop` | Any other kind |
|---|---|---|
| has a readable `channel.json` | accepted | accepted |
| no `channel.json`, still before launch (`created`, `preflighting`, `ready`, `workspace_preparing`, `workspace_ready`) | accepted | refused: `run <id> is still preparing and has not recorded its orchestrator channel yet; retry the steer once it is running, or send a stop steer` |
| no `channel.json`, running or paused | accepted | refused: `run <id> was dispatched without the orchestrator channel, so it cannot read a <kind> steer; only a stop steer applies, and it aborts the run at its deadline` |
| `channel.json` present but unreadable | accepted | refused: `run <id> has an unreadable channel.json; only a stop steer applies` |

A `stop` is always accepted because the dispatcher's watcher aborts the run at the steer's deadline with or without the channel. For a run with no channel the CLI says so (`the run has no orchestrator channel, so it is aborted in <grace>s`), and the `steer` object in the managed `steer_and_wait` result carries `"delivery": "abort-at-deadline"`. A refused or malformed steer leaves no trace: no `mailbox/` directory, no `mailbox.json`, no dead-letter file, and no `steer.sent` event are created for the run. A steer that fails validation (unknown `kind`, empty `message`, non-boolean `requires_ack`, `deadline_s` outside 1 to 3600) is rejected before the run is touched: the CLI and the MCP tools report the validation message, and the broker answers `400 invalid steer schema` (the document is dead-lettered only when the run already has a channel record). A refusal at the broker is `409` with the message above.

The delivery ladder as built:

1. **Tier 1**: the subagent calls `read_steering` at boundaries and acknowledges with `ack_steer` (advisory notes are delivered exactly once).
2. **Tier 2**: in tier-1 runs of Claude Code and Codex, a `PreToolUse` hook gate denies every other tool call while a `priority`, `scope`, or `stop` steer is unacknowledged, so steering cannot be silently missed. The channel's own tools are exempt so the subagent can read and acknowledge: `read_steering`, `ack_steer`, and `ask_orchestrator` from the server named exactly `pitwall-channel`, spelled `mcp__pitwall-channel__<tool>` by Claude Code and `mcp__pitwall_channel__<tool>` by Codex, which turns the hyphen into an underscore. A server whose name merely contains `pitwall_channel`, or a tool whose name only ends in a channel tool's name, is not exempt. The gate applies only to a dispatched harness (`PITWALL_AGENTS_CHANNEL_DISPATCH_ID` set) whose `channel.json` says tier 1. It fails closed: when the gate cannot be evaluated (`pitwall` is not on `PATH`, `pitwall agents _steer-gate` times out after 10 s, exits nonzero, or prints something that is not JSON) the plugin hook blocks the tool call and says why. The missing-`pitwall` check runs before the dispatch check, so with `pitwall` off `PATH` every tool call in any Claude Code or Codex session with the plugin enabled is blocked, dispatched or not. Recovery must not use a tool call: type `! uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl` at the prompt, or disable the plugin with `/plugin`.
3. **Tier 4**: undelivered steers are appended to the rebuilt prompt on `runs resume` and acked as `delivered on resume`.
4. **Graceful abort**: an ignored `stop` steer past its window, or SIGTERM/SIGHUP to the supervisor, ends the run as `cancelled` with an `abort.json` receipt (`reason`, `signal`, `killed`, `graceSeconds`; tune with `PITWALL_AGENTS_ABORT_GRACE_SECS`). SIGKILL is only used after the grace period, and the ledger outcome becomes `killed`. The `SHIM-DONE` sentinel is always emitted.

The inbox flags a steer as `ignored` when it required an ack and its deadline passed without one. A finished run's inbox entries are not listed in the all-runs inbox; blocking steers still unacknowledged at exit are reported instead through `unackedSteerIds` on the terminal event and the `finished` ledger row, and `unacked_steer_ids` on the managed wait result. A run that exited `0` without doing the work and was recorded as exit `77` carries the reason as `softDenialReason` in `run.json`, the ledger row and the terminal event (never `result.json`), and as `soft_denial_reason` on the managed wait's terminal payload.

## Workflows and auto-answers

A workflow can run a fan-out unattended while still answering its subagents. Set `askSupport` per task or in `defaults`, cap it with `maxAsks`, and name a D3 answerer route in `defaults.autoAnswer`:

```json
{
  "defaults": {"askSupport": true, "autoAnswer": {"route": "gw-free", "timeoutSeconds": 30}},
  "tasks": {"a": {"askSupport": true, "maxAsks": 3}}
}
```

When a task's dispatch has open asks, the scheduler moves it to `waiting_for_answer` and handles each one. A tier-4 child pauses on its asks; a tier-1 child waits inside `ask_orchestrator` while its dispatch keeps running, and the scheduler answers it there:

1. Expired asks take their stated default (`answered_by: "default"`).
2. Routine asks the policy is allowed to answer (D3: `blocked_on` ∈ {choice, naming, file-selection}, `severity: normal`, a non-`abort` default, ≥2 options) are answered by the `autoAnswer` route over its `/chat/completions` endpoint with `policy:<model>` provenance. The request allows the route's declared output limit (1,024 tokens when the route declares none), so a reasoning model can think before it answers. A policy answer never selects `abort`.
3. Everything else escalates: the scheduler emits `ask.escalated` and prints `pitwall agents answer <dispatch-id> <ask-id> <choice>` to stderr, then waits for the operator.

When no ask is pending the scheduler resumes the same dispatch id as a new attempt (`"resumed": true`). If the scheduler dies mid-wait, `workflow resume` re-enters the wait loop for the persisted paused dispatch instead of dispatching fresh. See `docs/workflows.md`.

## Retention

Raw channel Q&A is private and expires with its run: `runs cleanup` removes the run directory (mailbox, asks, answers, steers). Before removal it appends one `source:"shim", event:"channel"` ledger row per run that had channel traffic — counts and timings only (`asks`, `blockedOn`, `resolvedBy`, `latencyToAnswerS`, `steers`, `steerAckLatencyS`), never question text, option text, or file paths (D5: the routing signal survives, the code content does not). If the ledger cannot be appended, the run is kept rather than losing the signal. `/distill` summarizes these rows per model as routing-quality signal.

## Events and ledger

Lifecycle events: `dispatch.paused` (with `reason`), `dispatch.resumed`, `ask.resolved` (`askId`, `resolvedBy`), `ask.escalated`, `steer.sent`, `steer.acked`, `steer.unacked`, `steer.failed` (a steer whose publish failed after its `steer.sent` was logged), and `dispatch.cancelled`. Hooks receive them like any other event; see `docs/lifecycle-hooks.md`.

The `finished` ledger row gains channel aggregates whenever the run has a mailbox (§9.3): `askCount`, `askRatePerHour` (covers every attempt across pauses), `askResolutions` (`"<ask-id>:<answered-by>"`), `steerCount`, `steerAckLatencyS`, and `unackedSteerIds` (blocking steers still unacknowledged at exit). These are routing-quality signal, never question content.
