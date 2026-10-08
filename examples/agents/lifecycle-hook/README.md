# Lifecycle hook example

Copy [`../lifecycle-hooks.json`](../lifecycle-hooks.json) to `${XDG_CONFIG_HOME:-~/.config}/pitwall/agents/hooks.json` and replace its command with a trusted local executable.

Each entry is an argument array, never a shell string. `timeoutSeconds` bounds the hook (default 5; a missing, non-numeric, or non-positive value falls back to 5), and `failurePolicy` is reserved: hooks are always fail-open.

Hook commands receive metadata-only event JSON on stdin. They do not receive the prompt or provider output by default, and a failure, timeout, or nonzero exit never changes the provider exit or the final sentinel.

Each hook that ran leaves three files below the run's `hooks/` directory, never mixed with the shim streams: `<event>-<id>.stdout.log`, `<event>-<id>.stderr.log`, and `<event>-<id>.json`, whose status holds `exitCode` (`null` after a timeout), `timedOut`, the stdout and stderr byte counts, and their truncation flags.

After a Ctrl+C the supervisor stops running the remaining hooks for that event. Each hook it skipped leaves `<event>-<id>.json` containing `{"skipped": "interrupted"}`, and `pitwall agents` prints how many it skipped on stderr.
