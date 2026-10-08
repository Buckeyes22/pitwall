# Python, uv, and virtual environments

## In one sentence

Pitwall is written in Python; `uv` installs the exact versions it needs into a private folder,
`.venv`, and `uv run` runs commands inside it.

## Why it matters when testing Pitwall

`uv sync --frozen --extra dev` sets everything up, and `uv run` guarantees the project's
Python 3.14 rather than the system one. Never run bare `python`; the system Python may be a
different version, and the imports fail.

## Try it

```bash
uv run python --version
```

Expected: `Python 3.14.7`. If you see any other version, your `uv run` is not pointing at the
project's `.venv`.

## Common confusions

- Never run bare `python`. Use `uv run python` so the project interpreter is on the path.
- After switching branches, run `uv sync --frozen --extra dev` again. New dependencies do not
  appear until you do.
- Agent Routing (`pitwall agents`) uses the same `.venv` as the rest of Pitwall; there is no
  separate environment to switch into.
- `--frozen` reads `uv.lock` exactly. Without it, `uv sync` may update the lock and surprise
  CI.

## Check yourself

1. You checked out a pull request and imports fail. What do you run?

<details><summary>Answer</summary>

`uv sync --frozen --extra dev`. The new branch may have added or changed dependencies.

</details>

## Go deeper

- [Dev environment](../../CONTRIBUTING.md#dev-environment)
