# Subscription usage in Agent Routing — design

Status: approved by the maintainer on 2026-09-28, including the host boundary exception in
section 4.7.

## 1. Goal

Three outcomes:

1. The maintainer sees usage for every coding subscription on a machine in one place.
2. The orchestrating model can read that usage before it chooses where to send work, including
   when a second account of the same plan has room.
3. The separate submeter repository is retired. Its capability lives in Pitwall and its desk
   meter keeps working.

Agent Routing has no code that picks a subscription. The orchestrating model picks, guided by the
routing skill and its ledger cards. The skill already tells it to plan around rate-limit headroom
but gives it no way to measure headroom. This design supplies the measurement.

The work is staged so each stage is useful alone:

| Stage | Delivers | Submeter |
| --- | --- | --- |
| 1 | The `usage` command, accounts, and routing-skill rules | Still running |
| 2 | The serve mode, the desk meter contract, and the firmware move | Retired at the end of this stage |
| 3 | The web dashboard | Gone |

## 2. Non-goals

- A broker provider type, migration, routing-engine change, or policy adapter for subscriptions.
- Concurrency caps, terms gates, or tier catalogs for plans other than Model Studio.
- Any change to the Model Studio integration.
- Refusing a dispatch because of usage. The orchestrator decides; dispatch is unchanged.
- Token refresh. Submeter's refresh script and timer are dropped.
- Copying credentials between machines.
- A usage reader for Kimi. Its only signal is a paid request sent under another client's
  user-agent string.
- A second account for key-based plans (`glm`, `minimax`). Route `env` refuses names that look
  like secrets, so a second key cannot be declared there.

## 3. Approaches considered

| Approach | Verdict |
| --- | --- |
| A. Readers, one command, a serve mode, and skill guidance, all in Agent Routing | Chosen. Subscription work is dispatched here, and the chooser is the orchestrating model. |
| B. Subscriptions as broker providers with quota rows and routing-engine changes | Rejected for size. The broker does not dispatch subscription work, so the engine changes would have nothing to route. |
| C. Keep the separate submeter service and have the skill query it | Rejected. It is a Node service outside this repository's gates, and the goal is to retire it. |

## 4. Stage 1 — the usage command

### 4.1 Row

One row per plan and account. Every reader returns this shape.

| Field | Meaning |
| --- | --- |
| `plan` | Stable id: `claude`, `codex`, `glm`, `minimax`, `model-studio`, `kimi`, `grok`, `muse`, `gemini`, `opencode-go` |
| `account` | Short label for the account, empty when the plan has one account |
| `routes` | Names of the routes that send work to this account; empty for the harness default |
| `label` | Display name |
| `tier` | Plan tier when the source reports one, else empty |
| `windows` | List of `{name, used_pct, resets_at}`. `name` is `5h`, `7d`, or `30d`; `used_pct` is an integer or null; `resets_at` is a UTC instant or null |
| `status` | `ok`, `warn`, `limit`, `error`, `stale`, or `unknown` |
| `detail` | One short line: the reason for an `error`, `stale`, or `unknown` status, or an extra figure |
| `observed_at` | UTC instant of the read that produced the row |

Status rules:

- `limit`: any window at or above 100 percent, or the source reports the limit reached.
- `warn`: any window at or above 80 percent.
- `ok`: a successful read below 80 percent.
- `error`: the read failed and no earlier row exists.
- `stale`: the read failed and the row is the last good one.
- `unknown`: the plan is configured but its usage cannot be measured.

`error`, `stale`, and `unknown` mean "no information". They never mean "exhausted".

### 4.2 Accounts

An account is a plan plus a login directory. The CLI that owns a login directory keeps its token
fresh; readers only read it.

- The default account is the CLI's default directory.
- A route declares another account. A `claude` route whose `env` sets `CLAUDE_CONFIG_DIR`, or a
  `codex` route whose `env` sets `CODEX_HOME`, names a second login directory. Route entries
  already carry `env`, and dispatch already applies it to the child process.
- Route entries gain one optional field, `account`: a label of 1 to 16 letters, digits, or
  hyphens. Without it the label is the directory name with leading dots and the plan name
  removed, so `.claude-work` becomes `work`.
- A route for the same harness with an `account` field and no login directory labels the default
  account.
- Routes that share a login directory share one row, and the row lists all of them in `routes`.

The row therefore tells the orchestrator both that another account has room and which route
reaches it.

An account that has been idle has an expired token. Its row is `stale` with the last good numbers,
and `detail` says the login has expired. Any dispatch to that account makes its CLI refresh the
token, after which reads succeed.

### 4.3 Readers

A plan with a reader gets a row when its credential source exists on the machine. A plan without a
reader gets a row when its harness is installed, as the existing harness inventory reports.

| Plan | Source | Credential | Rules carried over from submeter |
| --- | --- | --- | --- |
| `claude` | `GET api.anthropic.com/api/oauth/usage` with header `anthropic-beta: oauth-2025-04-20` | Access token in the login directory's credentials file | At most one fetch per 300 seconds per account; the endpoint answers 429 under frequent polling; if the file's expiry has passed, make no request |
| `codex` | `GET chatgpt.com/backend-api/wham/usage` with the `ChatGPT-Account-Id` header | Access token and account id in the login directory's auth file | Pick windows by `limit_window_seconds`, not by position; `used_percent` is already percent used; if the token's `exp` has passed, make no request |
| `glm` | `GET api.z.ai/api/monitor/usage/quota/limit` | `GLM_API_KEY`, else the `zai-coding-plan` entry in OpenCode's auth store | `TOKENS_LIMIT` windows are identified by unit and number codes (3 and 5 for five hours, 6 and 1 for weekly), with reset order as the fallback; `percentage` is percent used |
| `minimax` | `GET api.minimax.io/v1/token_plan/remains` | `MINIMAX_API_KEY`, else the `minimax-coding-plan` entry in OpenCode's auth store | Quota is a request count; use the M-series coding model's entry; a total of zero gives a null percentage; the key is valid on the `.io` host only |
| `model-studio` | The existing `get_subscription_stats` and local exhaustion lockout | As configured today | Without an AccessKey pair the row is `unknown` and `detail` carries the renewal date |
| `kimi`, `grok`, `muse`, `gemini`, `opencode-go` | None | None read; presence comes from the harness inventory, and for `opencode-go` from its entry in OpenCode's auth store | Row is `unknown` with `detail` "no usage source" |

### 4.4 Cache

Each account has one JSON file under the Agent Routing state directory, in
`usage/<plan>[-<account>].json`. It holds the last row and the time of the last attempt.

- A reader runs at most once per minimum interval: 300 seconds for `claude`, 60 seconds otherwise.
  Inside the interval the cached row is returned.
- The attempt time is written before the request, so a failing source is throttled too.
- The directory is private and writes are atomic, using the helpers the run store already has.
- The file holds the row only. It never holds a credential value.
- The command and the serve mode share these files, so running both does not double the requests.

### 4.5 Command

```text
pitwall-agent-routing usage [--json]
```

Human output, one line per account:

```text
claude        work  Max 20x   5h 92%  resets 14:20   7d 63%  resets Thu 09:00   warn   (default)
claude        home  Max 5x    5h  4%  resets 15:45   7d 11%  resets Sat 18:00   ok     route claude-home
codex               Pro       5h  8%  resets 16:05   7d 40%  resets Mon 11:30   ok     (default)
glm                 Pro       5h  0%  resets 13:10   7d  1%  resets Sun 00:00   ok     (default)
model-studio        Pro       30d  —  renews 2026-10-21                         unknown
```

`--json` prints `{"observed_at": ..., "plans": [row, ...]}`. The exit code is 0 whenever the
command ran; each row carries its own status. A usage error exits 2, matching the other commands.

### 4.6 Routing skill

The "Picking the model" section of each host plugin's routing skill gains these rules:

1. Before choosing a subscription-backed route, run `pitwall-agent-routing usage --json`.
2. Do not choose an account whose status is `limit`.
3. Avoid an account with any window at or above 90 percent when another suitable one is below it.
   This keeps a 10 percent reserve.
4. When a plan has more than one account, use the route of the account with the most room.
5. Treat `error`, `stale`, and `unknown` as no information. They are not a reason to avoid an
   account.
6. When the host's own account is at `warn` or `limit` and no other account of that plan is
   configured, say so to the user and continue.

### 4.7 Host boundary exception

Today the Claude Code host blocks `route-shim.sh` with any Claude model, in
`plugins/pitwall/hooks/dag-tripwire.py`, so Claude work stays native. With that rule a second
Claude account can be seen but never used from a Claude Code host.

The change: the tripwire allows a saved Claude route whose `env` sets `CLAUDE_CONFIG_DIR`. The
tripwire is a standalone script that runs no commands, so it reads `routes.json` itself. A file it
cannot read, or a route without that variable, leaves the boundary in force.

The Codex host has no such hook. Its boundary is a sentence in its routing skill, and that
sentence gains the same exception for `CODEX_HOME`.

The shared workflow runner keeps rejecting Claude tasks from a Claude host. Only flat
`route-shim.sh` dispatch gains the exception.

### 4.8 Layout

```text
packages/agent-routing/runtime/model_routing/usage/
  __init__.py      reader registry and collect()
  rows.py          row type, status rules, the JSON fetch, the table
  accounts.py      accounts from routes and default login directories
  cache.py         per-account file, minimum interval, stale fallback
  claude.py
  codex.py
  glm.py
  minimax.py
  model_studio.py  wraps the existing stats read and lockout
  serve.py         stage 2: sampling, payloads, and the HTTP server
```

`cli.py` gains the `usage` subcommand and `routes.py` gains the `account` field. Everything is
standard library only. Each reader takes an injectable opener, as `model_studio_openapi.py` does,
so tests never touch the network.

## 5. Stage 2 — serve mode and the desk meter

### 5.1 Command

```text
pitwall-agent-routing usage serve [--host 127.0.0.1] [--port 8848] [--interval 45]
```

A standard-library HTTP server, following the receiver already in `pitwall_sync.py`. It refreshes
every account on the interval, and the readers' own minimum intervals still apply. An interval
under 30 seconds is refused with a configuration error.

The bearer token comes from `PITWALL_AGENT_ROUTING_USAGE_TOKEN`. A bind address other than
loopback requires the token; without it the command exits with a configuration error. The desk
meter is on the LAN, so its setup sets a token.

### 5.2 Burn rate and history

These need samples over time, which is why they belong to the serve mode and not the command.

- Burn rate is percent-points per minute between the current reading and a baseline 5 to 10
  minutes old. With no baseline in that range there is no rate.
- A negative rate means the window reset. It is reported as no rate.
- Time to full is `(100 - used) / rate`, reported only when the rate is above 0.05.
- History keeps one point per 5 minutes and the newest 24 points, for the five-hour window.
- A `stale` row carries no rate and no time to full, so a frozen source never looks active.

Samples are held in memory. A restart starts the history again.

### 5.3 Endpoints

| Path | Auth | Returns |
| --- | --- | --- |
| `GET /usage` | Bearer | The desk meter contract (5.4) |
| `GET /plans` | Bearer | The same JSON as `usage --json`, plus rate, time to full, and history per window |
| `GET /health` | None | `{"ok": true}` |

Every response sets `Content-Length` and is never chunked. The desk meter stream-parses the body
and cannot read a chunked response.

### 5.4 Desk meter contract

`GET /usage` reproduces submeter's payload so the firmware runs unchanged:
`{"updated": <unix seconds>, "providers": [...]}`.

| Payload field | Source |
| --- | --- |
| `name` | `plan`, or `plan-account` when the plan has more than one account |
| `label`, `tier` | The row's `label` and `tier` |
| `account` | The row's `account`, cut to 3 characters; present only when the plan has more than one account. An empty label becomes `1`, and labels that match once cut become `1`, `2`, and so on |
| `s_pct`, `s_reset_min` | The `5h` window; minutes are computed from `resets_at` |
| `w_pct`, `w_reset_min` | The `7d` window, or the `30d` window for a plan that has no `7d` |
| `status` | The row's status |
| `s_rate`, `s_eta_min`, `w_rate`, `w_eta_min`, `s_hist` | Section 5.2 |
| `extra` | An object holding the row's `detail`, cut to 80 characters, under `error` for a failed row and `note` otherwise; empty when there is no detail |

Limits the firmware imposes:

- It holds 7 rows. The payload carries the first 7 rows that have a reader, in plan order.
- It knows five statuses. Rows whose status is `unknown` are left out of this payload.
- Accounts of one plan are adjacent, which is how the firmware groups them into one split strip.

### 5.5 Firmware

The firmware moves to `examples/desk-meter/` with its PlatformIO configuration, its sources, the
secrets example header, and its bring-up guide as the directory's README.

- It is not built in CI. The release sdist already excludes `examples/`.
- Its comments and guide are cleared of machine names and LAN addresses before the move.
- The server address is compiled in from the secrets header. Pointing the meter at the new
  server is one edit and one reflash.

### 5.6 Retiring submeter

In order:

1. The uncommitted work in the submeter checkout is committed there, so nothing is lost.
2. Stage 2 is released and `usage serve` runs on the machine whose logins it reads.
3. The desk meter is reflashed with the new address and token, and shows live rows.
4. The old aggregator service, the refresh timer, and the refresh script are stopped and removed
   from the old host.
5. The submeter repository is archived read-only.

Left behind in the archive: the TypeScript aggregator, the refresh script, the credential-copy
arrangement, and the machine-specific handoff and operations notes.

## 6. Stage 3 — the dashboard

A single page served by `usage serve` at `GET /`, with no build step. It shows three things:

| Panel | Source | Rule |
| --- | --- | --- |
| Subscriptions | `GET /plans` | One card per account: windows, reset time, rate, status, and the route that reaches it |
| Running pods | The broker's `GET /v1/admin/runpod/pods` | Read through the serve process with the broker's admin secret; GET only |
| Agents and messages | The Agent Routing run store and its `events.jsonl` | Read only; ask and steer text is cut short until the user opens it; agent output logs are never read |

The page never calls a vendor or the broker directly, and it never issues a write.

Stage 3 is described here at the level decided so far. The run store's mailbox format and the
broker's admin header were not examined for this document. Stage 3 gets its own design pass, added
to this document, before it is planned.

## 7. Error handling

| Condition | Result |
| --- | --- |
| Credential source missing, or harness not installed for a plan without a reader | No row for that plan |
| Login directory named by a route does not exist | `error`, detail names the route |
| Credential file unreadable or malformed | `error`, detail names the file, never its contents |
| Token expired | `stale` when a row is cached, else `error`; no request is made |
| HTTP 401 or 403 | `error` or `stale`, detail gives the status code |
| HTTP 429, timeout, or network failure | `stale` when a row is cached, else `error` |
| Response is not the expected shape | `error` or `stale`, detail "unexpected response" |
| Plan has no reader | `unknown` |
| `usage serve` bound beyond loopback with no token, or given an interval under 30 seconds | Exits with a configuration error (78) |
| `usage serve` cannot bind its port | Exits 1 |

Detail text is a status code plus fixed wording. It never includes a response body.

## 8. Security

- Readers never refresh a token and never write to a credential file. A refresh rotates the token
  and breaks the CLI that owns it.
- Token and key values are never printed, logged, cached, or served.
- The cache directory is private to the user.
- The serve mode binds to loopback by default and requires a token on any other address.

## 9. Testing

- Hermetic by default: each reader runs against recorded response shapes through a fake opener.
- Table tests for the window rules: Codex by duration, GLM by unit and number codes, MiniMax with
  a zero total.
- Account tests: a route's login directory, the label rules, shared directories, and the default
  account's label.
- Cache tests: the minimum interval, the stale fallback, and the attempt time written first.
- A test that no reader requests a token-refresh address.
- Command tests for both output forms and the exit codes.
- Tripwire tests: a Claude route with a login directory passes; one without is blocked.
- Serve tests: the token rule, `Content-Length` on every response, the 7-row limit, the 3-character
  account label, the omitted `unknown` rows, and the rate and history rules.
- A contract test that compares `GET /usage` with a payload recorded from submeter.
- One opt-in live check per reader, skipped unless explicitly enabled.

## 10. Documentation and pinned lists

- New `packages/agent-routing/docs/usage.md`; the commands added to the Agent Routing README.
- `packages/agent-routing/docs/routes.md`: the `account` field and the host boundary exception.
- `docs/sdlc/23-agent-routing.md` and the Agent Routing changelog.
- `release_acceptance/reviewed-bindings.json` and `release_acceptance/denominator.json`, because
  the commands are new surfaces.
- `tests/release/cli_fixtures.json` and the surface count in the CLI journey test. The count is
  already behind on the base branch: it says 444 where discovery finds 455, and
  `routes add-model-studio-endpoint` has no fixture. Both are put right here.
- A `qa/` concept card or mission for the command.

## 11. Risks

- The Claude, Codex, and GLM sources are undocumented. Any of them can change without notice. A
  reader that stops parsing reports `error` and the other rows are unaffected.
- A vendor may object to automated reads of its usage endpoint. Each reader is a single read-only
  request on a long interval, made with the user's own credential.
- An idle account's numbers are old until it is used again. The row says so.
- The host boundary exception lets a Claude Code host run Claude work outside the native path.
  It is limited to routes that name a second login directory.

## 12. Branching

The work branches from the tip of `feat/model-studio`, in its own worktree. Every other active
branch forks from that commit, the `model-studio` reader wraps code that exists only there, and
`routes.py` and `cli.py` differ from `main` in the places this work edits.
