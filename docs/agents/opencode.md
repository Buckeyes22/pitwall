# OpenCode

`pitwall mcp install opencode` registers a `pitwall` MCP server launched over stdio. It
forwards four environment variables by reference, using OpenCode's own `{env:VAR}` syntax:
`RUNPOD_API_KEY`, `DATABASE_URL`, `REDIS_URL`, and `PITWALL_CONFIG_FILE`.

## User scope

User scope writes `$XDG_CONFIG_HOME/opencode/opencode.json`, or
`~/.config/opencode/opencode.json` when `XDG_CONFIG_HOME` is not set. Install:

```bash
uv run pitwall mcp install opencode --scope user
```

This adds the following entry to `opencode.json` under `mcp.pitwall`:

<!-- pitwall-mcp-install: opencode user -->
```json
{
  "mcp": {
    "pitwall": {
      "type": "local",
      "command": [
        "/path/to/pitwall",
        "mcp",
        "serve",
        "broker"
      ],
      "enabled": true,
      "environment": {
        "RUNPOD_API_KEY": "{env:RUNPOD_API_KEY}",
        "DATABASE_URL": "{env:DATABASE_URL}",
        "REDIS_URL": "{env:REDIS_URL}",
        "PITWALL_CONFIG_FILE": "{env:PITWALL_CONFIG_FILE}"
      }
    }
  }
}
```

Any other servers already in `opencode.json` are kept; the file is rewritten with 2-space
indentation. A backup of the previous file is written next to it as
`opencode.json.bak.<UTC timestamp>` before any change. On a fresh file, the
`$schema` field is set to `https://opencode.ai/config.json`.

Verify:

```bash
opencode mcp list
```

With the three registry variables exported, `opencode mcp list` shows `pitwall` as
`connected` (verified live 2026-09-11).

Uninstall:

```bash
uv run pitwall mcp uninstall opencode --scope user
```

This removes the `pitwall` entry from `opencode.json` and leaves every other entry
untouched.

## Project scope

Project scope writes `<project-root>/opencode.json`. Install:

```bash
uv run pitwall mcp install opencode --scope project --project-root /path/to/project
```

This adds the same entry shown above to that file:

<!-- pitwall-mcp-install: opencode project -->
```json
{
  "mcp": {
    "pitwall": {
      "type": "local",
      "command": [
        "/path/to/pitwall",
        "mcp",
        "serve",
        "broker"
      ],
      "enabled": true,
      "environment": {
        "RUNPOD_API_KEY": "{env:RUNPOD_API_KEY}",
        "DATABASE_URL": "{env:DATABASE_URL}",
        "REDIS_URL": "{env:REDIS_URL}",
        "PITWALL_CONFIG_FILE": "{env:PITWALL_CONFIG_FILE}"
      }
    }
  }
}
```

Verify, run from this project:

```bash
opencode mcp list
```

Uninstall:

```bash
uv run pitwall mcp uninstall opencode --scope project --project-root /path/to/project
```

## If a foreign `pitwall` entry already exists

If `opencode.json` already has a `pitwall` server that this command did not write, install
refuses to touch it and exits with an error naming the file. Rerun with `--force` to
replace it.
