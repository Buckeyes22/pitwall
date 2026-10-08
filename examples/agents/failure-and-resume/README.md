# Failure and resume example

Replace both model identifiers and `replace-with-route-name` before running. The named profile's
resolved provider and model must match the task declaration; remove the optional `name` field when
using direct provider dispatch. `implement` is a write task and therefore receives an isolated
worktree. It retries only a timeout or transport failure, then runs the verification argv without a shell.

```bash
pitwall agents workflow run workflow.json --host codex
pitwall agents workflow list
pitwall agents workflow show <workflow-id>
pitwall agents workflow resume <workflow-id>
```

The Codex host example intentionally contains no Codex transport task; Codex work stays native in the calling thread. Applying or discarding a successful isolated result remains explicit through `pitwall agents runs apply|discard <dispatch-id>`.
