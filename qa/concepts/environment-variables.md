# Environment variables

## In one sentence

An environment variable is a named setting, like `DATABASE_URL`, that a terminal passes to every
program it starts.

## Why it matters when testing Pitwall

Pitwall reads its configuration from environment variables and refuses to start when a required
one is missing (journey J05). The README Quick Start `export` lines set the placeholder values for
local testing. A new terminal starts without them, which is the most common reason a command works
in one terminal and fails in another.

## Try it

```bash
export GREETING=hello
echo "$GREETING"
```

Expected: `hello`. Open a new terminal and run `echo "$GREETING"` again. Expected: an empty line.

## Common confusions

- `export` sets a variable for this terminal and the programs it starts, not for other terminals.
- `NAME=value command` sets it for that one command only.
- `env -u NAME command` runs one command with `NAME` removed. Lessons use this to test missing
  settings.
- Never put a real key in a variable you might print or paste.

## Check yourself

1. You set the README variables in terminal 1. Why does `uv run pitwall-api` fail in terminal 2?

<details><summary>Answer</summary>

Terminal 2 has its own environment. Run the `export` lines there too.

</details>

## Go deeper

- [README configuration table](../../README.md#configuration)
- [Core models and config](../../docs/sdlc/16-core-config.md)
