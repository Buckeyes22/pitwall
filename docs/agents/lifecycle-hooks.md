# Lifecycle hooks

Portable runtime hooks are configured in:

```text
${XDG_CONFIG_HOME:-~/.config}/pitwall/agents/hooks.json
```

Example:

```json
{
  "dispatch.failed": [
    {
      "command": ["/home/user/bin/notify-routing-failure"],
      "timeoutSeconds": 5,
      "failurePolicy": "ignore"
    }
  ]
}
```

Commands are argument arrays, never shell strings. Each hook receives the lifecycle event JSON on stdin and minimal metadata through:

- `PITWALL_AGENTS_EVENT`
- `PITWALL_AGENTS_DISPATCH_ID`
- `PITWALL_AGENTS_HARNESS`
- `PITWALL_AGENTS_MODEL`
- `PITWALL_AGENTS_WORKFLOW_ID` (workflow attempts only)
- `PITWALL_AGENTS_TASK_ID` (workflow attempts only)
- `PITWALL_AGENTS_HOOK_DEPTH`

Prompt and provider output contents are not included. Hook stdout/stderr and status are captured below the run's `hooks/` directory and never mixed with shim streams.

For each hook that ran, the `hooks/` directory holds `<event>-<id>.stdout.log`, `<event>-<id>.stderr.log`, and `<event>-<id>.json`, the status document (`exitCode`, `timedOut`, `stdoutBytes`, `stderrBytes`, `stdoutTruncated`, `stderrTruncated`).

## Hooks and Ctrl+C

While a supervised run is live, the first Ctrl+C is held for the run: the supervisor turns it into a graceful stop of the harness's process group and the run ends as `cancelled` with exit `130` (see [run records](run-records.md#interrupts-and-exit-codes)). Hook commands run inside that supervisor, for example `steer.unacked` hooks fired from its watch loop. A hook that is already running finishes, so its writes stay whole. Once a Ctrl+C is pending, the remaining hooks of that event are not started. Each skipped hook leaves a status file, `<event>-<id>.json`, containing only `{"skipped": "interrupted"}` beside the status files of the hooks that ran, and the runner prints one stderr line, `pitwall agents: skipped N <event> hook(s) after Ctrl+C`. Only the supervising thread of an interactive run defers Ctrl+C this way: outside a supervised run, or on another thread, hooks run as before.

## Failure policy

v0.3 hooks are fail-open. Invalid JSON, malformed definitions, missing commands, timeouts, nonzero exits, invalid recursion-depth values, and hook-artifact failures do not change the provider exit or suppress the final sentinel. `failurePolicy` is reserved for compatibility; completion events do not support fail-closed behavior in this release.

Recursive routing is bounded by the hook-depth environment value. A malformed external value is treated as depth zero, while a depth of three or greater prevents further hook execution.

These hooks do not replace Claude Code's package-specific Stop hooks. Claude's ledger and DAG tripwires remain the host enforcement/advisory layer; runtime hooks are portable local automation.

## Channel events

The orchestrator channel emits through the same pipeline:

- `dispatch.paused` — the run paused on an unresolved ask (`askIds`, `exitCode`, `reason`: `question` or `orphaned-ask`)
- `dispatch.resumed` — `runs resume` re-entered a paused run (`attempt`)
- `ask.resolved` — an ask received its answer (`askId`, `resolvedBy`: `operator`, `orchestrator`, `default`, or `policy:<model>`)
- `ask.escalated` — an ask needs a human: it waited past half its effective deadline in a tier-1 session, or the workflow scheduler could not auto-answer it (`askId`, `reason`)
- `steer.sent` — the orchestrator sent a steering directive (`steerId`, `kind`)
- `steer.acked` — the subagent acknowledged a steering directive (`steerId`)
- `steer.failed` — a steer was logged as sent but could not be published (`steerId`, `kind`)
- `steer.unacked` — a `requires_ack` steer is unacknowledged (`steerId`, `kind`). It fires once per steer: at the steer's deadline, or at exit (with `atExit: true`) if it was not reported earlier. A `stop` steer reaching its deadline aborts the run. When the run ends, the terminal event (`dispatch.succeeded`, `dispatch.failed`, and the other terminal events) carries `unackedSteerIds` listing every blocking steer still unacknowledged, including steers already reported at their deadline, which get no second event; the field is omitted when the list is empty. A run whose zero exit dispatch rewrote to `77` also carries `softDenialReason` there.
- `dispatch.cancelled` — a graceful abort finished (`exitCode`, `outcome`; the matching ledger row carries `abortReason`)

A hook can pull the operator in the moment an ask escalates — for example, a terminal bell:

```json
{
  "ask.escalated": [
    {"command": ["/usr/bin/printf", "\\a"], "timeoutSeconds": 2, "failurePolicy": "ignore"}
  ]
}
```

See [the orchestrator channel](orchestrator-channel.md) for the full contract.
