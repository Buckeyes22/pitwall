# Logs, tracebacks, and exit codes

## In one sentence

Programs report problems in three ways: log lines, tracebacks (Python's crash report), and exit
codes (a number where `0` means success).

## Why it matters when testing Pitwall

A traceback during normal use is a bug, and exit codes are part of Pitwall's documented contract
([CLI exit codes](../../docs/sdlc/18-cli.md#exit-codes)). When a command prints a traceback, the
report needs the traceback verbatim, the exact command, and the environment.

## Try it

```bash
cat does-not-exist.txt; echo "exit=$?"
```

Expected: `No such file or directory`, then `exit=1`. Then:

```bash
env -u DATABASE_URL -u REDIS_URL uv run pitwall config check; echo "exit=$?"
```

Expected: a `missing-runtime-config` message and `exit=78`.

## Common confusions

- Read a traceback from the bottom. The last line is the error; the lines above are the path
  it took to get there.
- `$?` holds only the last command's code. After a pipe, use `${PIPESTATUS[0]}` for the first
  command in the pipe.
- Exit `0` is success, exit non-zero is failure. A command can print an error and still exit
  `0`; both go in the report.
- `os.EX_CONFIG` is `78` on Linux. Pitwall returns it when the runtime configuration is
  missing.

## Check yourself

1. A command printed an error but `exit=0`. Is that worth noting?

<details><summary>Answer</summary>

Yes. The exit code is one signal and the printed error is another. Compare both against the
  documented contract before deciding.

</details>

## Go deeper

- [CLI exit codes](../../docs/sdlc/18-cli.md#exit-codes)
