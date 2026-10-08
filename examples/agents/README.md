# Examples

Worked examples for the Agent Routing shims. Two further examples sit one level up, in `examples/`.

- [`dependency-workflow/`](dependency-workflow/): a two-task `workflow run` with a review task that depends on an analysis task's output.
- [`failure-and-resume/`](failure-and-resume/): a `workflow run` with a retrying write task, plus `workflow list|show|resume` and `runs apply|discard`.
- [`fan-out-review/`](fan-out-review/): a two-model fan-out code review with real shim output.
- [`isolated-implementation/`](isolated-implementation/): a write task dispatched into an isolated worktree, then reviewed and applied or discarded with `runs`.
- [`lifecycle-hook/`](lifecycle-hook/): wiring a dispatch-failure/timeout hook through `hooks.json`.
- [`profiles/`](profiles/): example `[agents.profiles]` tables for `pitwall.toml` and the commands that exercise them.
- [`../desk-meter/`](../desk-meter/): ESP32 touch-display firmware that shows subscription usage from `pitwall usage serve`.
- [`../verify_webhook.py`](../verify_webhook.py): verifies the signature of an outbound Pitwall webhook.
