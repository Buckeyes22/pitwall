# Isolated implementation example

This example runs a write task away from the caller's checkout. Substitute a provider/model and prompt appropriate to your installation.

```bash
mkdir -p /tmp/pitwall-agents-example
printf '%s\n' \
  'Add one focused implementation, run the project tests, and summarize the files changed.' \
  > /tmp/pitwall-agents-example/implement.md

~/.claude/scripts/opencode-shim.sh <provider/model> \
  /tmp/pitwall-agents-example/implement.md \
  --routing-workspace isolated \
  --routing-task-mode write
```

The command can modify code inside its isolated worktree. It does not modify this checkout until an explicit application:

```bash
pitwall agents runs list
pitwall agents runs diff <dispatch-id>
pitwall agents runs apply <dispatch-id> --target "$PWD"
```

Review the patch and rerun decisive project checks before applying. When the retained branch is no longer needed:

```bash
pitwall agents runs discard <dispatch-id> --yes
```
