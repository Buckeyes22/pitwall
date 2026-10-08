# Claude Code

`pitwall mcp install claude-code` registers a `pitwall` MCP server that launches over stdio.
It forwards four environment variables by reference, never by value:
`RUNPOD_API_KEY`, `DATABASE_URL`, `REDIS_URL`, and `PITWALL_CONFIG_FILE`.

## User scope

User scope goes through the `claude` CLI, because Claude Code rewrites `~/.claude.json`
while it runs. Install:

```bash
uv run pitwall mcp install claude-code --scope user
```

This prints the exact `claude mcp add-json` command it will run, then runs it. The command
itself is:

<!-- pitwall-mcp-install: claude-code user -->
```bash
claude mcp add-json --scope user pitwall '{"type": "stdio", "command": "/path/to/pitwall", "args": ["mcp", "serve", "broker"], "env": {"RUNPOD_API_KEY": "${RUNPOD_API_KEY:-}", "DATABASE_URL": "${DATABASE_URL:-}", "REDIS_URL": "${REDIS_URL:-}", "PITWALL_CONFIG_FILE": "${PITWALL_CONFIG_FILE:-}"}}'
```

`/path/to/pitwall` is the real `pitwall` console script beside the running
interpreter, or `<python> -m pitwall` when that script is missing; the server it starts is
`pitwall mcp serve broker`. Each `${VAR:-}`
reference resolves at the time Claude Code launches the server, from whatever environment
Claude Code itself runs in.

Verify:

```bash
claude mcp list
```

With the three registry variables exported, `claude mcp list` shows `pitwall` as
`Connected` (verified live 2026-09-11).

Uninstall:

```bash
uv run pitwall mcp uninstall claude-code --scope user
```

This runs `claude mcp remove --scope user pitwall`.

## Project scope

Project scope writes `.mcp.json` in the project root directly. Install:

```bash
uv run pitwall mcp install claude-code --scope project --project-root /path/to/project
```

This adds the following entry to `.mcp.json` under `mcpServers.pitwall`:

<!-- pitwall-mcp-install: claude-code project -->
```json
{
  "mcpServers": {
    "pitwall": {
      "type": "stdio",
      "command": "/path/to/pitwall",
      "args": [
        "mcp",
        "serve",
        "broker"
      ],
      "env": {
        "RUNPOD_API_KEY": "${RUNPOD_API_KEY:-}",
        "DATABASE_URL": "${DATABASE_URL:-}",
        "REDIS_URL": "${REDIS_URL:-}",
        "PITWALL_CONFIG_FILE": "${PITWALL_CONFIG_FILE:-}"
      }
    }
  }
}
```

Any other servers already in `.mcp.json` are kept; the file is rewritten with 2-space
indentation. A backup of the previous file is written next to it as
`.mcp.json.bak.<UTC timestamp>` before any change.

Verify by restarting Claude Code in this project and approving the `pitwall` server when
prompted. Before approval, `claude mcp list` shows it as `Pending approval (run claude to
approve)` (verified live 2026-09-11).

Uninstall:

```bash
uv run pitwall mcp uninstall claude-code --scope project --project-root /path/to/project
```

This removes the `pitwall` entry from `.mcp.json` and leaves every other entry untouched.

## If a foreign `pitwall` entry already exists

If `.mcp.json` or `~/.claude.json` already has a `pitwall` server that this command did not
write, install refuses to touch it and exits with an error naming the file. Rerun with
`--force` to replace it.
