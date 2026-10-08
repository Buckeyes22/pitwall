# Host-neutral dependency workflows

`pitwall agents workflow` executes versioned JSON dependency graphs over the same fourteen transport shims used by direct dispatch. It is a local, foreground scheduler: there is no daemon, database, UI, or background control plane.

Use the runner when Codex or Copilot needs durable dependency ordering, bounded concurrency, retries, verification, cancellation, or resume. In Claude Code, native Workflow remains the default for Claude-hosted graphs. The host-neutral runner is appropriate there only for an explicitly requested external-only graph.

## Commands

```bash
pitwall agents workflow run workflow.json --host copilot
pitwall agents workflow list
pitwall agents workflow list --json
pitwall agents workflow show <workflow-id>
pitwall agents workflow cancel <workflow-id>
pitwall agents workflow resume <workflow-id> --host copilot
```

Run and resume are foreground operations. `cancel` may be called from another terminal; it records the cancellation request and signals the active scheduler, which in turn interrupts every active shim supervisor. On Ctrl+C the runner prints and persists the workflow ID and resume command.

## Minimal document

```json
{
  "schemaVersion": 1,
  "name": "external-review",
  "defaults": {
    "maxConcurrency": 2,
    "providerConcurrency": {"opencode": 1},
    "timeoutSeconds": 1140,
    "workspace": "auto",
    "failurePolicy": "fail-fast"
  },
  "tasks": {
    "analyze": {
      "route": {"provider": "opencode", "model": "replace-with/provider-model"},
      "mode": "read",
      "prompt": {"file": "prompts/analyze.md"}
    },
    "review": {
      "route": {"provider": "grok", "model": "grok-4.7", "effort": "high"},
      "mode": "read",
      "dependsOn": ["analyze"],
      "contextFrom": [{"task": "analyze", "artifact": "stdout", "maxBytes": 50000}],
      "prompt": {"text": "Review the supplied analysis and identify concrete gaps."}
    }
  }
}
```

Prompt files are resolved relative to the workflow JSON. Absolute paths, parent traversal, symlink escapes, missing dependencies, cycles, native-host routes, unknown providers, unsupported effort values, shell-string verification commands, and write tasks using a shared workspace are rejected before any provider starts. Unknown model identifiers remain warnings only for providers whose registry contract permits pass-through.

`route.name` optionally selects an existing named route profile from the `[agents.profiles]` tables of `pitwall.toml`. The workflow must still declare the resolved `provider` and `model`; the scheduler resolves the profile before creating a run and rejects a provider/model mismatch. Named routes then execute through the same `route-shim` path as direct dispatch, carrying the profile's endpoint, credential reference, model, and harness arguments.

## Host-native boundaries

The declared host prevents accidental routing of that host's own family:

- `--host claude` rejects Claude transport tasks. Claude work remains native.
- `--host codex` rejects Codex transport tasks. Codex work remains inline.
- `--host copilot` has no native provider, so the host boundary permits every registered transport.

The host boundary is one thing; the dispatchable set is another. The durable runner launches the `codex`, `claude`, `grok`, `kimi`, `opencode`, and `pi` transports (Codex inline only on the Codex host). Workflow validation checks the adapter's execution capability before a run starts, so a provider can be registered for route discovery without being advertised as workflow-capable. Pi receives the normalized route's exact model and thinking effort through its existing one-shot shim; it does not create a second workflow or RPC manager.

`--host` is advisory metadata, not an authentication or security boundary. A caller can falsely declare Copilot. Pass it again on resume so the CLI verifies that it matches the persisted workflow host. Claude's tripwire hooks inspect observed shell commands and block `workflow run` or `workflow resume` when a Claude session declares another host; they remain the enforcement layer for native Workflow and delegation behavior.

## Scheduling and failure

Independent roots run concurrently up to `maxConcurrency`; `providerConcurrency` can impose a lower provider-specific limit. A dependent task starts only after every dependency is `succeeded` or `verified`.

With `fail-fast`, the first failure stops new work, already-running tasks finish or cancel, directly affected dependents become `blocked`, and other unstarted tasks become `skipped`. With `continue`, unrelated branches continue while failed dependencies still block their descendants.

Every state transition is atomically persisted below the private state directory:

```text
${XDG_STATE_HOME:-~/.local/state}/pitwall/agents/workflows/<workflow-id>/
```

Each attempt has a unique dispatch ID and normal run directory. Workflow, task, and attempt lineage is present in run records, lifecycle events, and ledger entries.

A task whose dispatch has open channel asks moves to the non-terminal `waiting_for_answer` state (its `waitingOn` lists the pending ask ids) while the scheduler resolves them; see [Questions and auto-answers](#questions-and-auto-answers). It is not a failure: the task returns to `running` when every ask is answered. A tier-4 dispatch pauses on its asks and resumes afterwards; a tier-1 dispatch keeps running while its child waits inside `ask_orchestrator`.

## Context handoff

Context is opt-in through `contextFrom`; dependency transcripts are never injected automatically. Supported artifacts are `stdout`, `stderr`, `result`, `patch`, and `diffstat`. The composite prompt identifies the dependency task, provider/model, state, artifact, original byte count, and whether the selected content was truncated.

## Questions and auto-answers

Tasks may opt into the orchestrator channel so a dispatched model can ask blocking questions (see [the orchestrator channel](orchestrator-channel.md)):

```json
{
  "defaults": {"askSupport": true, "autoAnswer": {"route": "gw-free", "timeoutSeconds": 30}},
  "tasks": {
    "a": {"askSupport": true, "maxAsks": 3}
  }
}
```

- `askSupport` (boolean, task or defaults) turns on `--routing-ask-support` for the task's dispatch. The normalized task records `askSupport: true` only when the task or the defaults enable it.
- `maxAsks` (integer 1–50, requires effective `askSupport`) caps the task's asks (`--routing-max-asks`).
- `autoAnswer` (defaults only) names an endpoint route that may answer routine asks while the scheduler waits: `{"route": <name>, "timeoutSeconds": <1–120, default 30>}`. It appears in the normalized defaults only when set. Consequential asks (`schema`, `destructive`, `spend`, `blocking`, abort defaults, or fewer than two options) always escalate to the operator. Route existence is checked when the scheduler starts.

While a dispatch has open asks (a tier-4 child pauses on them; a tier-1 child waits inside `ask_orchestrator` and keeps running), the task state becomes `waiting_for_answer`; expired asks take their stated default, policy-eligible asks may be answered by the auto-answer route (provenance `policy:<model>`), and the rest wait for `pitwall agents answer`. When every ask of a paused dispatch is resolved the scheduler resumes the same dispatch id as a new attempt; a tier-1 child simply receives its answer.

## Retry and verification

```json
{
  "retry": {
    "maxAttempts": 2,
    "backoffSeconds": 5,
    "on": ["timeout", "transport-error"]
  },
  "verify": [
    ["python3", "-m", "unittest", "discover", "-s", "tests"],
    ["git", "diff", "--check"]
  ]
}
```

One attempt is the default, so retries are disabled unless configured. Usage/configuration errors are never retried. A write retry receives a fresh dispatch ID and isolated worktree. Verification commands are argument arrays executed directly without a shell inside the task workspace. Provider success with passing checks becomes `verified`; a failed check becomes `verification_failed`.

## Resume guarantees

Resume verifies the workflow digest, canonical registry digest, Git common-directory identity, successful task results, retained write worktrees, and absence of a live scheduler. Completed `succeeded` and `verified` tasks never rerun. Incomplete tasks receive new attempts while their earlier attempt history remains intact. A task persisted as `waiting_for_answer` (a scheduler that died mid-wait) becomes `pending` and re-enters the wait loop for its paused dispatch on resume instead of dispatching fresh; the final cancellation sweep also converts `waiting_for_answer` to `cancelled`.

See [dependency-workflow](../../examples/agents/dependency-workflow/) and [failure-and-resume](../../examples/agents/failure-and-resume/) for editable examples.
