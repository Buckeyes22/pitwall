# Doctor

`pitwall agents doctor` is a bounded, non-destructive diagnostic for the local routing installation. The default validates the configured runtime and plugin contract without model discovery; discovery is a separate explicit mode.

## Commands

```bash
pitwall agents doctor
pitwall agents doctor --json
pitwall agents doctor --harness codex
pitwall agents doctor --installation-only
pitwall agents doctor --harness claude --live-auth
pitwall agents doctor --discover-models
pitwall agents doctor --harness opencode --discover-models
pitwall agents doctor --harness kimi --discover-models
pitwall agents doctor --harness agy --discover-models
pitwall agents doctor --probe-routes
```

The default run starts with local Python and Git availability, state and ledger writability, registry validity, generated assets, installed entrypoints, and hook configuration, then `harness.summary`, which lists installed and missing harnesses and warns when a configured route pins a missing harness. Route checks validate the `[agents.profiles]` tables of `pitwall.toml` and each configured route's harness availability, expiry, endpoint sync status, and API-key environment variable, all offline by default; `--probe-routes` additionally sends one bounded liveness request per endpoint route and can report a cold route as `warming` rather than a failure (see [Self-hosted endpoints](self-hosted.md#discovery-and-liveness)). It then inspects provider executables and local help/version surfaces, plugin boundaries, version alignment, and security/retention warnings. Provider help contracts check only flags the installed CLI documents, with one deliberate exception: the Grok Build CLI accepts `--no-auto-update` while hiding it from `--help`, so doctor validates that flag by invoking `grok --no-auto-update --help` and requires only the visible `--output-format` in the output. A parser that rejects the hidden flag is a `WARN`, not a silent pass. It also runs `kimi doctor config`, a documented read-only local validation command, when Kimi is selected or installed. Help, version, and configuration commands have bounded timeouts and do not request a provider catalog. A provider filter omits the summary because `harness.summary` is not tied to one provider. Doctor itself writes nothing, but harness CLIs may create their own first-run, state, or log files when these help and version commands run.

Doctor never installs a provider. Use the separate, explicitly mutating checkbox setup from a terminal when desired:

```bash
pitwall agents setup harnesses
pitwall agents setup harnesses --dry-run
```

See [optional provider CLI setup](harness-cli-setup.md) for its confirmation, download, and authentication boundaries.

`--installation-only` limits the report to runtime/install checks. The installer uses this mode after creating its symlinks.

### Channel checks

The default run reports registration and protocol health separately from live delivery:

- `channel.mcp_server` — spawns the stdio MCP server once per role over stdio and completes an `initialize`/`tools/list` handshake (and the `server/discover`/`tools/list` path of the 2026-07-28 era). It waits on the server's replies for as long as the child process is alive, up to a 30-second ceiling from launch (`CHANNEL_HANDSHAKE_TIMEOUT`), so a cold start on a loaded host passes. A child that exits before replying fails at once. The four probes (two roles, two eras) run concurrently, so a silent server costs one ceiling (30 seconds) in total. A `WARN` names each failed probe's actual reason in the summary and in `details` (`orchestratorFailure`, `subagentFailure`, `orchestratorFailureModern`, `subagentFailureModern`): `exited with code N before replying`, `closed its output before replying`, `no reply within the 30 s ceiling`, `protocol error: <fixed description>` (never raw server output), `unexpected tool list`, or `the probe failed`. `PASS` requires every probe to list exactly the role's tools: `inbox`, `answer_ask`, `dispatch_and_wait`, `answer_and_wait`, `wait_dispatch`, and `steer_and_wait` for the parent role, and `ask_orchestrator`, `read_steering`, and `ack_steer` for the child role. The `details` also carry `handshakeCeilingSeconds`. This proves only the local protocol, not that a parent model receives child questions. Anything else is a `WARN` (tier 1 unavailable; the tier-4 file contract still works).
- `channel.registration` (one per installed channel harness: Claude Code, Codex, Copilot CLI, OpenCode, Kimi, Cline, Qwen Code, ZCode, Grok, Antigravity, Muse, Hermes, and goose) — `PASS` when the harness's user-scope config carries a `pitwall-channel` entry whose command is an absolute path to an executable file; otherwise `WARN` with remediation `pitwall agents setup mcp --harness <id>`. For Hermes and goose, whose config is YAML, a file that cannot be read is named in the summary (`registration for <harness> is unreadable: <path>: invalid YAML (<error class>) at line N, column M`, or the structural reason) with remediation `fix the config file named above, then run pitwall agents setup mcp --harness <id>`; the message never quotes the file. Harnesses that are not installed get no check. The registration files are listed under [the orchestrator channel](orchestrator-channel.md#tier-1-the-mcp-server).
- `channel.interactive_delivery` — `SKIP` in the default doctor because a real parent-child exchange needs an installed host and a bounded model request. Neither registration nor the local stdio handshake is evidence of live bidirectional delivery.
- `channel.launch_enforcement` — `SKIP` in the default doctor because hook registration alone cannot prove Claude denied a disconnected launch before the process started. A live installed-host guard test is required.

The default run never sends an authentication prompt. `--live-auth` is an explicit request to run a documented local authentication-status command for providers that expose one; doctor does not initiate login or credential changes, and it does not discover models. Antigravity (`agy`) has no documented local status command, so its `--live-auth` check is explicitly different: it sends one bounded `Reply with exactly: pong` inference request. That request can consume provider usage, and the provider CLI may refresh internally as part of it. The result is reported as `verified-request` only when the exact response is returned. Omitting `--live-auth` never sends that prompt. A failed or unavailable check is `unknown`, not proof that an account is logged out.

Provider readiness is reported in check `details.readiness` using five values:

| Readiness | Evidence the doctor has | What it does not prove |
| --- | --- | --- |
| `absent` | The provider executable is not available to the selected environment | That the provider is uninstalled everywhere or that no credentials exist |
| `configured` | A documented local configuration check succeeded, or a known non-empty credential environment variable is present | Opaque auth-store presence, account identity, credential validity, quota, or a successful request |
| `signed-in` | A documented local authentication-status command succeeded | A fresh provider request, quota, or continued server acceptance |
| `verified-request` | An explicitly requested bounded inference request succeeded | Future availability, quota, or account identity beyond that request |
| `unknown` | No safe evidence was available, or a check failed | Logged-out status; do not treat this as a reason to trigger login automatically |

The repository-supported evidence for each harness is deliberately bounded:

| Harness | Local source or safe command | Readiness the doctor can report | Limitation |
| --- | --- | --- | --- |
| agy | `agy` executable; optional explicit pong request | `verified-request` only with `--live-auth`, otherwise `unknown` | Desktop keyring identity and model discovery do not establish current request validity |
| claude | `claude auth status` with `--live-auth`; `~/.claude` state may identify a local account | `signed-in` or `unknown` | Status is local CLI evidence, not a fresh model request |
| cline | `~/.cline/data/settings/providers.json` metadata | `unknown`; opaque file presence is reported only as metadata | Stored provider keys do not establish identity or server acceptance |
| codex | `codex login status` with `--live-auth`; `~/.codex/auth.json` is local evidence | `signed-in` or `unknown` | Cached identity and status do not prove a fresh request |
| dsh | `~/.dsh/settings.yaml` endpoint and `apiKeyEnv` metadata | `unknown`; opaque file presence is reported only as metadata | Endpoint credentials and session state are not verified |
| goose | `~/.config/goose/config.yaml` local configuration | `unknown`; opaque file presence is reported only as metadata | No repository-supported read-only account-status probe |
| grok | `~/.grok/auth.json` local cache or `XAI_API_KEY` presence | `configured` only for non-empty `XAI_API_KEY`; opaque file presence is `unknown` | Cached or expired access is not a failed refresh and no safe status probe is defined here |
| hermes | `~/.hermes/auth.json` and `~/.hermes/.env` metadata | `unknown`; opaque file presence is reported only as metadata | Local metadata does not establish a usable credential |
| kimi | `kimi doctor config` | `configured` or `unknown` | This validates configuration, not authentication validity or a provider request |
| muse | `~/.config/muse/auth.json` local configuration | `unknown`; opaque file presence is reported only as metadata | No repository-supported read-only account-status probe |
| opencode | `~/.local/share/opencode/auth.json` provider entries | `unknown`; opaque file presence is reported only as metadata | Provider names/nonempty credentials do not establish identity, validity, or quota |
| pi | `~/.pi/agent/auth.json` local profiles | `unknown`; opaque file presence is reported only as metadata | Alternate profiles and provider acceptance are not exhaustively checked |
| qwen | `~/.qwen/settings.json` and `~/.qwen/.env` local endpoint metadata | `unknown`; opaque file presence is reported only as metadata | Key presence does not establish account identity or server acceptance |

Only Claude and Codex currently have repository-defined read-only auth-status commands. Grok and OpenCode declare registry auth-probe capability but have no documented probe in this repository, so doctor skips them rather than inventing a command. Local cached identities, expiry timestamps, desktop keyring entries, and nonempty credential fields remain local evidence; they are never presented as universal account validity.

`--discover-models` is the only mode allowed to run model discovery. It reports its command/source in each check: OpenCode runs `opencode models`, Kimi runs the documented `kimi provider list --json` command, Codex reads its CLI-managed local model cache, Antigravity CLI runs `agy models`, and Claude/Grok/Qwen report `SKIP`. Kimi's JSON may contain provider credentials, so discovery parses it in bounded memory and returns only validated keys from the `models` table; raw stdout, stderr, provider objects, environment values, tokens, and credentials are never retained. Missing binaries, timeouts, changed output, malformed caches, and unsupported providers are warnings rather than hard failures.

## Status and exits

Every check is `PASS`, `WARN`, `FAIL`, or `SKIP` and includes remediation when action is appropriate.

`harness.summary` is `PASS` when no route pins a missing harness and `WARN` otherwise; its message always summarizes the installed and missing inventory.

- Exit `0`: no `FAIL` checks. Warnings and skips remain visible.
- Exit `1`: one or more environmental failures, including an invalid `[agents.profiles]` table in `pitwall.toml`.
- Exit `2`: invalid invocation or an invalid provider registry.

JSON output follows [`src/pitwall/agents/resources/schemas/doctor-result.schema.json`](../../src/pitwall/agents/resources/schemas/doctor-result.schema.json), currently `schemaVersion: 1`. Consumers should use check IDs and statuses rather than parsing human summaries.

## Security boundary

Doctor reads local files and may execute `git --version`, a resolved provider binary's local help/version command, and the documented Kimi configuration validator. Default operation does not write probe files, initiate login or credential changes, update CLIs, contact model catalogs, or rewrite provider configuration. Explicit `--live-auth` and discovery may contact a provider according to the selected CLI command; Kimi's configured-provider listing is local, while OpenCode controls the behavior of `opencode models`. `--probe-routes` contacts each configured endpoint route's own URL for a bounded liveness check. Discovery never runs during install checks or dispatch preflight. Use a restricted environment or provider-specific offline controls if an installed CLI behaves unexpectedly.
