# Subscription usage

`pitwall usage` shows how much of each coding subscription on this machine has been
used, and which route reaches each account. The routing skill reads the same rows before it
chooses where to send work.

## The command

```bash
pitwall usage          # a table
pitwall usage --json   # {"observed_at": ..., "plans": [row, ...]}
```

The exit code is 0 whenever the command ran. Each row carries its own status. A bad flag or an
unreadable `pitwall.toml` exits 2.

## A row

| Field | Meaning |
|---|---|
| `plan` | `claude`, `codex`, `glm`, `minimax`, `model-studio`, `kimi`, `grok`, `muse`, `gemini`, or `opencode-go` |
| `account` | Short label for the account; empty when the plan has one account |
| `routes` | Routes that send work to this account; empty means the harness default |
| `label`, `tier` | Display name, and the plan tier when the source reports one |
| `windows` | `{name, used_pct, resets_at}` for `5h`, `7d`, or `30d`; times are UTC |
| `status` | `ok`, `warn` (80 percent or more), `limit` (100 percent or a reported limit), `error`, `stale`, or `unknown` |
| `detail` | The reason for `error`, `stale`, or `unknown`, or an extra figure |
| `observed_at` | When the row was read |

`error`, `stale`, and `unknown` mean there is no information. They never mean the plan is used up.
`stale` keeps the last good numbers.

## Where each plan is read from

| Plan | Read from | Present when |
|---|---|---|
| `claude` | Anthropic's usage endpoint, with the login in the Claude Code login directory | the login directory holds `.credentials.json` |
| `codex` | The ChatGPT usage endpoint, with the login in the Codex login directory | the login directory holds `auth.json` |
| `glm` | The Z.ai quota endpoint | `GLM_API_KEY` is set, or OpenCode's auth store has `zai-coding-plan` |
| `minimax` | The MiniMax plan endpoint | `MINIMAX_API_KEY` is set, or OpenCode's auth store has `minimax-coding-plan` |
| `model-studio` | The Token Plan statistics read and the local exhaustion lockout | a Token Plan endpoint is in `profiles.json` |
| `kimi`, `grok`, `muse`, `gemini` | Nothing; the row is `unknown` | the harness is installed |
| `opencode-go` | Nothing; the row is `unknown` | OpenCode's auth store has `opencode-go` |

Every read is one GET with the credential already on the machine. Nothing refreshes a token,
writes a credential file, or prints a credential. Model Studio shows percentages only when an
Alibaba Cloud AccessKey pair is configured; without one the row is `unknown` and names the renewal
date.

## A second account

An account is a plan plus a login directory. Declare a second one with a route:

```bash
CLAUDE_CONFIG_DIR="$HOME/.claude-home" claude   # sign in once with the second account

pitwall agents profiles add claude-home --model sonnet --harness claude \
  --env CLAUDE_CONFIG_DIR="$HOME/.claude-home" --account home
```

Codex works the same way with `CODEX_HOME`. Use an absolute path. `--account` is a label of 1 to
16 letters, digits, or hyphens; without it the label comes from the directory name. A route for
the same harness with `--account` and no login directory labels the default account.

The row for the second account lists `claude-home` under `routes`, so a reader sees both that the
account has room and how to reach it. An account that has not been used for a while has an expired
login; its row is `stale` and says so. Sending work to it once makes its CLI sign in again.

GLM and MiniMax have one account each: the `[agents.profiles]` tables refuse variable names that look like
secrets, so a second key cannot be declared in a route.

## How often a plan is read

Each account has a file under the state directory, `usage/<plan>[-<account>].json`, holding the
last good row. Claude is read at most once in 300 seconds and every other plan once in 60; inside
that time the stored row is returned. A failed read is throttled the same way.

## Serving usage

```bash
pitwall usage serve [--host 127.0.0.1] [--port 8848] [--interval 45]
```

| Path | Auth | Returns |
|---|---|---|
| `GET /usage` | Bearer | The desk meter payload |
| `GET /plans` | Bearer | The rows, with burn rate, time to full, and history per window |
| `GET /health` | None | `{"ok": true}` |

- The bearer token is read from `PITWALL_AGENTS_USAGE_TOKEN`. Any address other than
  loopback needs it; without it the command exits 78.
- `--interval` is in seconds and must be at least 30.
- Burn rate is percent-points per minute against a reading 5 to 10 minutes old. A window that
  reset, or a row that is not fresh, has no rate.
- History is one point per 5 minutes, the newest 24, held in memory.
- Every response carries `Content-Length` and is never chunked.

`GET /usage` carries at most 7 rows, leaves out `unknown` rows, and cuts account labels to 3
characters, because that is what the desk meter firmware holds. The firmware is in
`examples/desk-meter/`.

A user service keeps it running:

```ini
[Unit]
Description=Pitwall Agent Routing subscription usage
After=network-online.target

[Service]
EnvironmentFile=%h/.config/pitwall/agents/usage.env
ExecStart=%h/.local/bin/pitwall usage serve --host 0.0.0.0 --port 8848
Restart=on-failure

[Install]
WantedBy=default.target
```

`usage.env` holds the `PITWALL_AGENTS_USAGE_TOKEN` line and any plan keys, with mode 600.
