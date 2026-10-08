# Examples

Worked examples for the bundled subagent-model-routing shims.

- [`dependency-workflow/`](dependency-workflow/): a two-task `workflow run` with a review task that depends on an analysis task's output.
- [`failure-and-resume/`](failure-and-resume/): a `workflow run` with a retrying write task, plus `workflow list|show|resume` and `runs apply|discard`.
- `fan-out-review/`: a two-model fan-out code review with real shim output.
- [`isolated-implementation/`](isolated-implementation/): a write task dispatched into an isolated worktree, then reviewed and applied or discarded with `runs`.
- [`lifecycle-hook/`](lifecycle-hook/): wiring a dispatch-failure/timeout hook through `hooks.json`.
- [`profiles/`](profiles/): example `[agents.profiles]` tables for `pitwall.toml` and the commands that exercise them.
