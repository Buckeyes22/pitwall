# Run records

Every accepted dispatch receives a UUID before provider preflight and writes a private run directory:

```text
${PITWALL_AGENTS_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}/pitwall/agents}/runs/<dispatch-id>/
```

Directories use mode `0700` and files use mode `0600`.

## Core artifacts

| Artifact | Purpose |
|---|---|
| `run.json` | Current state plus the validated transition history, the `supervisor` record (`pid`, `pidStartIdentity`) of the process that owns the run, and, once the harness starts, the `harnessProcess` record (`pgid`, `pidStartIdentity`) of the harness's process group |
| `request.json` | Prompt source type/path, delivery kind, SHA-256, byte length, and retention status |
| `events.jsonl` | Versioned lifecycle envelopes for this dispatch |
| `stdout.log` / `stderr.log` | Raw provider streams. A direct shim call also forwards them live to the terminal. A managed launch (`dispatch_and_wait`) keeps them only here: nothing goes live to the terminal or to the launcher's logs |
| `result.json` | Terminal status, timing, exit/signal, sanitized arguments, output hashes, and artifact paths as written (a managed `terminal` or `orphan` event reports the run directory's current paths instead, which matters after a migration moved the run) |
| `prompt.md` | Present only when `--routing-retain-prompt` was explicitly passed |
| `prompt.deliver.md` | The prompt exactly as delivered, including the channel instructions. Written for runs with ask support and for harnesses that take the prompt as a file (Muse Code only when the prompt came from stdin; see [prompt exposure](architecture.md#prompt-exposure)). [The delivery prompt](#the-delivery-prompt) says when it is removed |
| `hooks/` | Captured hook stdout, stderr, and status documents; a hook skipped for a pending Ctrl+C leaves `{"skipped": "interrupted"}` (see [lifecycle hooks](lifecycle-hooks.md#hooks-and-ctrlc)) |
| `workspace.json` | Isolated-worktree ownership, repository identity, base, branch, and retained path |
| `changeset.json` | Captured commit/path/status/diffstat manifest for an isolated dispatch |
| `changes.patch` / `working.patch` | Binary-safe complete patch and post-commit working patch |
| `channel.json` | Orchestrator-channel record: ask cap, attempt timeout, delivery tier |
| `mailbox.json` | Mailbox counts, unresolved ask ids, unacked steer ids, sequence gaps; exists only once the run's `mailbox/` does, which a read never creates |
| `pause.json` | Recorded pause classification (pending ask ids, `reason`, workspace snapshot) |
| `abort.json` | Graceful-abort receipt (`reason`, `signal`, `killed`, `graceSeconds`) |
| `abandoned.json` | Written when a run whose supervisor vanished is recorded as failed: `schemaVersion`, `reason`, `detectedAt`, `harnessTerminated`; see [Abandoned runs](#abandoned-runs) |
| `resume.json` | Replay inputs `runs resume` needs (provider, model, workspace, ask support, ask cap, prompt retention) |

Small stdout/stderr writes are forwarded and retained before child exit, including
partial lines. The 64 KiB pump chunk size is not a minimum delivery size or a total
output limit. Provider-side buffering still requires the provider to flush output.

Prompt contents are absent by default, but provider output can contain source or secrets. There is no automatic deletion.

The `request.json` `promptSource.delivery` field records whether the prompt was delivered inline or through a harness-specific transport such as `file`.

## Inspecting and cleaning runs

```bash
pitwall agents runs list
pitwall agents runs show <dispatch-id-or-unique-prefix>
pitwall agents runs logs <dispatch-id-or-unique-prefix> --channel stdout|stderr|both
pitwall agents runs diff <dispatch-id-or-unique-prefix>
pitwall agents runs apply <dispatch-id-or-unique-prefix> --target <repo>
pitwall agents runs resume <dispatch-id-or-unique-prefix>
pitwall agents runs stop <dispatch-id-or-unique-prefix> [--grace SECONDS]
pitwall agents runs discard <dispatch-id-or-unique-prefix> --yes
pitwall agents runs cleanup --older-than 30
pitwall agents runs cleanup --all
pitwall agents inbox [--json]
pitwall agents answer <dispatch-id> <ask-id> <choice> [--note TEXT]
```

Cleanup is explicit and skips a run while its owned isolated worktree still exists. A missing or ambiguous UUID prefix returns nonzero rather than choosing a run quietly.

## The delivery prompt

`prompt.deliver.md` is mode `0600`. A paused run keeps it, because `runs resume` replays it with the answers appended. Every other terminal path removes it unless `--routing-retain-prompt` was passed: a normal finish, a timeout, a stop-steer or signal abort, a failure while the workspace was being prepared, a run recorded as abandoned, and a failure in a later finalization step (the removal runs in a `finally` once the terminal transition has happened). `resume.json` records `retainPrompt`, so a retained run keeps its prompt across a resume. When `request.json` cannot show whether retention was requested (missing, unreadable, corrupt, or the wrong shape), reconciling an abandoned run keeps the prompt rather than guess. `runs cleanup` also removes the staged copy of a managed dispatch's prompt (`launches/<dispatch-id>/prompt.md`) once the run's own `request.json` exists, unless the dispatch asked to retain the prompt.

## Abandoned runs

A dispatch records its own process in `run.json` as `supervisor` (`pid` and `pidStartIdentity`, the process start time that tells a live process from a reused pid). A resumed attempt records its own. If that process is killed before it writes a terminal state, the run would stay `running` forever, so the run store reconciles it. `runs list`, `runs show`, `runs stop`, `runs cleanup`, and a wait on a standalone run (one with no managed launcher sidecar) each check the run first. A run that is neither terminal nor `paused` is abandoned when:

- its recorded supervisor pid is gone or belongs to a different process: `supervisor pid <pid> exited without recording a terminal state`; or
- it recorded no supervisor pid (an older run) and nothing in its directory has changed for 24 hours: `no supervisor pid was recorded and the run has not changed for 24 hours`.

The harness runs in its own process group, so it outlives a supervisor that is killed outright (SIGKILL, or the out-of-memory killer). Each attempt records that group in `run.json` as `harnessProcess` when the harness starts. Before an abandoned run is recorded, a harness group whose leader still carries the recorded start identity is sent SIGTERM and, if it is still there after 2 seconds, SIGKILL; a pid that now belongs to another process is never signalled. An abandoned run is then recorded as `failed` (a `failed` transition is appended to its history), `abandoned.json` keeps the reason, the detection time, and `harnessTerminated` (whether a surviving harness was ended), and its delivery prompt is removed under the rules above. The run directory keeps its original modification time, so `runs cleanup --older-than` and `runs list` still see the run's real age. Managed runs are judged by their launcher sidecar instead, and a paused run has no supervisor by design, so neither is reconciled this way. Reads that find a live supervisor change nothing, and `runs cleanup` (including `--all`) keeps a nonterminal standalone run whose supervisor is alive, or which has had no recorded pid for less than 24 hours.

## Interrupts and exit codes

A supervised dispatch never leaves its harness running when the operator presses Ctrl+C:

- The first Ctrl+C is held until the harness and its output threads exist, then turned into a stop: SIGTERM to the harness's process group, a 2 s grace, then SIGKILL. The run ends `cancelled` with exit `130` and a `dispatch.cancelled` event. Further Ctrl+C presses during that shutdown are held and cannot cut the sequence short.
- A Ctrl+C that arrives late, in the same tick the harness exits or during the final wait, reap, or output-thread joins, still ends the run as `cancelled` (`130`) unless it had already ended as a timeout or an abort.
- During the grace of a graceful abort (a stop steer past its window, SIGTERM or SIGHUP; `PITWALL_AGENTS_ABORT_GRACE_SECS`, default 10 s) a Ctrl+C ends the grace at once: the group is killed and reaped and the run reports `130`.
- If the supervisor cannot start an output thread, it terminates and reaps the harness's group before the error propagates.
- Hooks stop at a pending Ctrl+C; see [lifecycle hooks](lifecycle-hooks.md#hooks-and-ctrlc).

The shims' other supervisor exit codes are `124` for the timeout, `143` (128 + SIGTERM) for an abort whose harness exited `0` when told to stop (Codex does), `137` when the abort had to SIGKILL (ledger outcome `killed`), `128 + n` for a harness killed by signal `n`, `75` for a pause, and `77` for a harness that exited `0` without doing the work. These apply when the supervisor owns the signal handler: on the main thread of a process that has not installed its own SIGINT handler. The bounded-output runner behind hooks, model discovery, the inventory scan, and OpenCode's model listing likewise terminates and reaps its child's process group on Ctrl+C, then lets the interrupt continue.

## State and ledger relationship

The structured run store is additive. The existing observations ledger remains at `${XDG_STATE_HOME:-~/.local/state}/pitwall/agents/ledger/observations.jsonl` unless `PITWALL_AGENTS_LEDGER` overrides it. Extended ledger rows include `dispatch_id`, schema version, attempt, workspace, and `profile`.

Rows carry `route` — the route profile spec for `route-shim` dispatches, `null` otherwise — at `schema_version` 4; `result.json` may carry a `route` object (spec, name, harness, model, endpointHost, effort, effortSource) and never the key or full URL. `effort` is a string or null and `effortSource` is `route`, `harness`, `caller`, or null; both fields are absent or null for direct shim use.

When a harness exits `0` without doing the work and dispatch rewrites the exit to `77` (a soft denial such as Antigravity's auto-denied tool permission, or OpenCode's empty stdout), the reason string is recorded as `softDenialReason` in `run.json`, on the `finished` ledger row, and in the terminal lifecycle event data; the managed wait's terminal payload carries it, read from `run.json`, as `soft_denial_reason`. `result.json` keeps its fixed field set and never carries it. The field is absent for every other run.

`profile` records the execution policy the child CLI actually ran under: `unrestricted` when the shim suppressed the CLI's own sandbox/approval prompting, `cli-policy` when the CLI kept enforcing it. Requesting a bypass is not the same as getting one — OpenCode only accepts a bypass flag its installed build advertises, so an adapter whose bypass depends on preflight discovery reports the effective profile rather than the requested one.

Setting `SHIM_RESULT=1` also writes the `finished` row to stdout as `SHIM-RESULT <json>` immediately before the sentinel; see the [Agent Routing README](routing-readme.md#quickstart) for the parsing contract.

Workflow attempts additionally carry `workflowId`, `taskId`, effort, and attempt number in run state/results/events, plus `workflow_id`, `task_id`, and `attempt` in ledger rows. The scheduler assigns the dispatch UUID before launch, so these identifiers remain stable through preflight, retries, cancellation, and resume. Workflow state itself lives under `workflows/<workflow-id>/`; see [host-neutral workflows](workflows.md).

Legacy asymmetries remain deliberate:

- usage failures write no ledger row;
- `started` rows omit exit, wall time, and outcome;
- unreadable prompts preserve each shim's existing ordering;
- a clipped process can leave an orphaned `started` row for `distill` to report;
- legacy outcome still treats exit `124` as timeout, while `supervisor_timeout` distinguishes whether the Python supervisor actually fired.

Application is not a lifecycle state. Worktree integration records `applied`, `conflicted`, or `discarded` under `result.json.integration` without transitioning a terminal dispatch.
