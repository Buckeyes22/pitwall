# Codex

`pitwall mcp install codex` writes a managed TOML block that registers a `pitwall` MCP
server launched over stdio. It forwards four environment variables by name, using Codex's
own `env_vars` reference syntax: `RUNPOD_API_KEY`, `DATABASE_URL`, `REDIS_URL`, and
`PITWALL_CONFIG_FILE`.

## Codex registration is user scope only

Codex registration only supports user scope. Codex-cli (verified with version 0.153.4)
ignores `[mcp_servers]` entries in a project's `.codex/config.toml`; `codex mcp list` omits
them even when the project is trusted. Writing a project-scope block there would produce a
file Codex never reads, so `pitwall mcp install codex --scope project` refuses instead and
exits `1`:

```text
error: codex does not load MCP servers from project-scope config; rerun with --scope user
```

Always use `--scope user` (the default) for Codex.

## User scope

User scope writes `$CODEX_HOME/config.toml`, or `~/.codex/config.toml` when `CODEX_HOME` is
not set. Install:

```bash
uv run pitwall mcp install codex --scope user
```

This adds the following block to `config.toml`:

<!-- pitwall-mcp-install: codex user -->
```toml
# >>> pitwall mcp (managed by `pitwall mcp install`; edits here are overwritten)
[mcp_servers.pitwall]
command = "/path/to/pitwall"
args = ["mcp", "serve", "broker"]
env_vars = ["RUNPOD_API_KEY", "DATABASE_URL", "REDIS_URL", "PITWALL_CONFIG_FILE"]
# <<< pitwall mcp
```

Everything outside the `# >>> pitwall mcp` / `# <<< pitwall mcp` markers is left byte for
byte untouched, so foreign TOML in the same file survives install and uninstall exactly.

Verify:

```bash
codex mcp list
```

`codex mcp list` does no health check; it shows the `pitwall` entry with its forwarded
variable names, whether or not those variables are set in this shell.

Uninstall:

```bash
uv run pitwall mcp uninstall codex --scope user
```

This removes the managed block and restores the surrounding bytes exactly as they were.

## If a foreign `pitwall` entry already exists

If `config.toml` already defines `[mcp_servers.pitwall]` outside the pitwall-managed block,
install refuses to touch the file and exits with an error naming it. Remove that section by
hand, then rerun; there is no `--force` override for Codex, because the file is edited as a
managed block rather than a single value.
