# Optional harness CLI setup

`pitwall agents setup harnesses` is an explicit, dependency-free checkbox installer for the optional CLI harnesses used by the transport shims: Codex, Claude Code, Antigravity CLI, Grok Build, Kimi Code, OpenCode, Qwen Code, Pi, Hermes Agent, Cline CLI, Muse Code, goose, DeepSeek Harness (`dsh`), and ZCode (manual installation). The harness executables are not bundled with Pitwall.

## Selector behavior

`pitwall agents install` never installs a harness CLI. Run `pitwall agents setup harnesses` yourself when you want the selector; it needs a controlling terminal because selection input is read directly from `/dev/tty`.

Missing providers begin unchecked. Installed providers are shown as `[x]`, include their resolved path, and cannot be selected for reinstall or upgrade.

Controls:

- Up/Down or `k`/`j`: move between missing providers
- Space: toggle the highlighted provider
- Enter: continue; if nothing is selected, skip
- `q` or Escape: skip without installing anything
- Ctrl-C: restore the terminal and stop with exit 130

The selector shows the exact source domains and whether each installer has a reviewed SHA-256 digest, then asks for a second `[y/N]` confirmation before downloading anything. An unpinned source is labeled with an explicit warning. The default answer is no.

In CI, cron, redirected sessions, or any process without `/dev/tty`, the selector skips itself and prints the manual command. It never waits on stdin and never installs a harness noninteractively. It also skips when `TERM=dumb`; `--no-color` remains available.

## Antigravity CLI (Gemini)

The Antigravity CLI harness is installed by the recipe in `src/pitwall/agents/resources/config/harness-installers.json`, which uses the first-party install script. That script installs `agy` and runs `agy install`; the latter edits shell profiles by default. To opt out of those shell-profile edits after installation, run:

```bash
agy install --skip-aliases --skip-path
```

Authenticate by running `agy` interactively and completing the sign-in flow. For Gemini API-key mode, set `"modelProvider": "gemini"` in `~/.gemini/antigravity-cli/settings.json` and provide `GEMINI_API_KEY`. Inspect the available catalog with `agy models`.

`agy --print-timeout` defaults to five minutes. The shared `agy-shim.sh` overrides it so the shim's own `PITWALL_AGENTS_TIMEOUT_SECS` supervision and ledger outcome fire first.

In restricted mode Antigravity soft-denies un-permitted tools while still exiting `0`; the shim detects the `jetski: no output produced` diagnostic and reports exit `77` (EX_NOPERM) instead, with the remedy on stderr.

OpenCode always prints its answer, so a run that exits `0` with nothing, or only whitespace, on stdout (a stalled model) is recorded as a failure with exit `77`, and the shim says so on stderr. Exit `77` (EX_NOPERM) means a harness reported success without doing the work, either because a tool permission was auto-denied or because it produced no answer; the run records which in `softDenialReason` (`run.json`, the ledger row and the terminal event) (see [run records](run-records.md#state-and-ledger-relationship)).

## Run or rerun it manually

After installing the release wheel (see the [Quick Start](../../README.md#quick-start)):

```bash
pitwall agents setup harnesses
pitwall agents setup harnesses --dry-run
pitwall agents setup harnesses --no-color
```

`--dry-run` opens the same selector and confirmation, then prints the manifest-defined URL and interpreter without downloading or executing anything. Re-running normal setup is the recovery path after a partial failure: successfully installed CLIs are detected and disabled, leaving only missing providers selectable.

## Inventory connected harness capabilities

Bootstrap follows client-plugin registration with:

```bash
pitwall agents setup inventory
```

The command detects installed routing harnesses plus GitHub Copilot CLI, then checks their known
user-level JSON, TOML, YAML, and capability-directory surfaces. It retains names only for MCP
servers, plugins, skills, agents, commands, extensions, and tools; config values and skill/agent
file bodies are not copied. Input files and directory enumeration are bounded. The private outputs
are `${XDG_CONFIG_HOME:-~/.config}/pitwall/agents/harness-capabilities.json` and a compact
`harness-capabilities.md` model-context view. All three routing skills read the Markdown snapshot
before selecting or briefing an external harness. This is availability context, not authorization
or an authentication check.

Run the command again after installing a harness or changing its configuration. `--json` prints
the same redacted snapshot for inspection while still updating both files. A malformed or
unreadable config produces a path-only warning and does not prevent other harnesses from being
inventoried.

## Supported platforms

The checkbox selector supports:

- macOS
- Linux
- Windows through WSL (treated as Linux)

Native PowerShell and Command Prompt installer orchestration are not included. Install provider CLIs from their first-party Windows documentation instead, then use this project from its supported shell environment.

Harness setup does not install Git, Python, Node.js, Homebrew, WSL, or a system package manager. Python 3.14 and Git remain separately diagnosed runtime prerequisites.

## Installer sources

`src/pitwall/agents/resources/config/harness-installers.json` is the declarative source of truth. Display names, executable resolution, and override environment variables continue to come from `src/pitwall/agents/resources/config/harness-registry.json`; the two registries must have exactly the same provider IDs.

Script recipes use `installerUrl`, `interpreter`, and `sha256`, plus an optional `env` object whose upper-case string entries are passed only to the installer. They may explicitly set `"kind": "script"`; omitting `kind` preserves the original recipe format.

Package-manager recipes use a separate shape:

| Kind | Required fields | Redirect allow-list | Execution and integrity boundary |
|---|---|---|---|
| `npm` | `"kind": "npm"`, `"package": "<name>"`, `"version": "<exact semver>"` | exactly `registry.npmjs.org` | `npm install -g --ignore-scripts <package>@<version>`; no reviewed checksum |

The installer sources were re-verified against first-party documentation on 2026-08-27:

| Provider | Installer URL | Checksum status | First-party documentation |
|---|---|---|---|
| Codex | `https://chatgpt.com/codex/install.sh` | reviewed SHA-256 pinned | [OpenAI Codex](https://github.com/openai/codex) |
| Claude Code | `https://claude.ai/install.sh` | reviewed SHA-256 pinned | [Claude Code setup](https://code.claude.com/docs/en/getting-started) |
| Antigravity CLI | `https://antigravity.google/cli/install.sh` | unavailable from the verification network; explicit warning | [Antigravity CLI install](https://antigravity.google/docs/cli/install/) |
| Grok Build | `https://x.ai/cli/install.sh` | reviewed SHA-256 pinned | [Grok Build overview](https://docs.x.ai/build/overview) |
| Kimi Code | `https://code.kimi.com/kimi-code/install.sh` | unavailable from the verification network; explicit warning | [Kimi Code setup](https://moonshotai.github.io/kimi-code/en/guides/getting-started.html) |
| OpenCode | `https://opencode.ai/install` | reviewed SHA-256 pinned | [OpenCode setup](https://opencode.ai/docs/) |
| Qwen Code | `https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/install-qwen-standalone.sh` | unavailable from the verification network; explicit warning | [Qwen Code](https://github.com/QwenLM/qwen-code) |
| Pi | npm `@earendil-works/pi-coding-agent@0.84.4` and `@tintinweb/pi-subagents@0.19.0` | no reviewed checksum; `--ignore-scripts` | [Pi coding agent](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md) |
| Hermes Agent | `https://hermes-agent.nousresearch.com/install.sh` | reviewed SHA-256 pinned | [Hermes Agent](https://github.com/NousResearch/hermes-agent) |
| Cline CLI | npm `cline@3.0.60` | no reviewed checksum; `--ignore-scripts` | [Cline CLI reference](https://docs.cline.bot/cli/cli-reference) |
| Muse Code | `https://dev.meta.ai/install.sh` | reviewed SHA-256 pinned | [Meta Muse Code](https://developer.meta.com/ai/products/muse-code/) |
| goose | `https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh` | not pinned: `stable` is a moving release tag | [goose installation](https://goose-docs.ai/docs/getting-started/installation/) |
| ZCode | manual build/install; existing CLI supported | no canonical pinned public CLI installer | [Official source](https://github.com/zai-org/ZCode/blob/main/README.md) |
| DeepSeek Harness (`dsh`) | npm `@deepseek-ai/dsh@0.1.1-rc.2` | no reviewed checksum; `--ignore-scripts` | [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) |

The runtime does not use npm as a universal fallback. It runs npm only for an explicit, pinned npm recipe. If a first-party standalone installer is unavailable or changes its redirect target, setup fails with the documentation link rather than guessing another package-manager command.

Static source review on the verification date observed these delivery details without executing the scripts on the maintainer account:

| Provider | Observed delivery behavior |
|---|---|
| Codex | POSIX `sh`; about 25 KiB; redirects through `github.com` to `release-assets.githubusercontent.com`; installs a user-level binary under `~/.local/bin` and verifies the downloaded release artifact |
| Claude Code | Bash; about 6 KiB; redirects to `downloads.claude.ai`; keeps native-install data under `~/.claude` |
| Antigravity CLI | Bash; downloads a sha512-verified tarball, writes `~/.local/bin/agy`, then runs `agy install`; the recipe allows its manifest and tarball redirect hosts. |
| Grok Build | Bash; about 17 KiB; remains on `x.ai`; uses the user-level `~/.grok/bin` location |
| Kimi Code | The exact Bash installer URL is present in MoonshotAI's current repository and guide. The endpoint was not reachable from the release-review network, so its response and any redirect could not be independently inspected there; setup permits only `code.kimi.com` and fails closed if delivery differs. |
| OpenCode | Bash; about 14 KiB; redirects to `raw.githubusercontent.com`; uses the user-level `~/.opencode/bin` location |
| Qwen Code | Bash; the upstream `scripts/installation/install-qwen-standalone.sh` source installs a standalone archive with an npm fallback and writes its `qwen` wrapper under the user-level `~/.local/bin` location. The Aliyun OSS endpoint was not reachable from the review network, so the served bytes could not be digest-pinned; setup permits only `qwen-code-assets.oss-cn-hangzhou.aliyuncs.com` and fails closed if delivery differs. |
| Pi | Pinned npm recipe: `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.84.4 @tintinweb/pi-subagents@0.19.0` (the Workbench backend rides along); `pitwall agents setup pi` runs just this recipe, and `--dry-run` prints it. The npm packages have no reviewed checksum. |
| Hermes Agent | Bash installer at `hermes-agent.nousresearch.com`, with the reviewed manifest digest and no redirect host allowed. |
| Cline CLI | Pinned npm recipe: `npm install -g --ignore-scripts cline@3.0.60`; the npm package has no reviewed checksum. |
| Muse Code | POSIX `sh` installer at `dev.meta.ai`, with the reviewed manifest digest and no redirect host allowed. |
| goose | Bash; the stable release URL redirects from `github.com` to `release-assets.githubusercontent.com`. Its moving `stable` tag cannot be checksum-pinned, so setup emits the explicit unpinned warning and fails closed on any other host. |
| DeepSeek Harness (`dsh`) | Pinned npm recipe: `npm install -g --ignore-scripts @deepseek-ai/dsh@0.1.1-rc.2`; the npm package has no reviewed checksum. |

The Linux flow was additionally exercised with Codex in a disposable clean HOME: the selector and confirmation installed only Codex, the binary became discoverable on the refreshed user PATH, its local version check succeeded, and doctor reported `provider.codex.binary_resolved` as passing. No live recipe was run over an existing maintainer installation. Portable provider-setup and bootstrap tests also run in the macOS CI smoke job; WSL follows the tested Linux manifest and runtime path.

When updating a recipe:

1. Re-check the current first-party documentation.
2. Download the installer without executing it and inspect its redirect chain, interpreter, size, and user-level install path.
3. Record the reviewed response's SHA-256 digest, or use `null` only when the endpoint cannot be inspected and document why.
4. Update the URL/redirect allow-list and `sourceVerifiedOn` date.
5. Run the schema, semantic, offline setup, and PTY tests.
6. Validate the real installer only in a disposable VM, container, or dedicated clean user—not over an active maintainer installation.

## Security boundary

Selecting a provider authorizes this tool to download and execute that provider's manifest-defined installer. It does not authorize any other provider, an upgrade of an already resolved binary, authentication, model discovery, API-key entry, or provider configuration by this project.

Before execution, setup:

- accepts only fixed HTTPS URLs from the checked-in manifest;
- rejects every redirect hop whose HTTPS hostname is absent from the provider-specific allow-list, then revalidates the final URL;
- bounds connection/read time and installer size;
- requires a non-empty script beginning with a shebang;
- compares the response with the manifest's reviewed SHA-256 digest when present and aborts on mismatch;
- downloads into memory, then writes a mode-`0600` temporary file;
- executes a fixed `bash` or `sh` argv array with no shell interpolation or `eval`;
- pins npm recipes to an exact version and passes `--ignore-scripts`; npm recipes carry no digest;
- runs npm only for an explicitly selected, manifest-pinned recipe; the missing reviewed checksum remains an explicit npm security boundary;
- streams installer output directly to the user's terminal without adding it to routing run records; and
- removes the temporary script after success, failure, timeout, or interruption.

The manifest digest proves that a download matches the exact bytes reviewed by this project's maintainer; it is not a vendor signature and does not establish who authored those bytes. Some upstream installers verify the artifacts they subsequently download. An unavailable digest is shown as a warning and relies on fixed HTTPS origins, redirect allow-listing, bounded downloads, explicit confirmation, and first-party recipe review. Users who require vendor-authenticated artifacts should follow the provider's documented release/signature process directly.

Official installers may create user-level directories and update shell PATH configuration according to their own documented behavior. This project does not edit shell profiles itself. After setup, the current bootstrap process temporarily adds existing common user binary directories to PATH so a newly installed Claude or Codex host can be registered in the same run.

No login command is executed. Authenticate afterward as applicable:

```bash
codex login
claude auth login
agy                         # interactive sign-in, or configure modelProvider gemini + GEMINI_API_KEY
grok login                  # or configure XAI_API_KEY for headless use
kimi login
opencode auth login
# OpenCode Go subscription: run /connect in the OpenCode TUI, select "OpenCode Go",
# and paste the key from https://opencode.ai/auth — see docs/opencode-go.md
# Qwen Code: configure ~/.qwen/.env (OPENAI_API_KEY, OPENAI_BASE_URL, QWEN_MODEL) for an OpenAI-compatible endpoint
pi --list-models              # verify the configured Pi provider/API key
hermes setup
cline auth
# Muse API-key authentication (Muse 1.3+):
printf '%s\n' "$META_API_KEY" | muse auth set --provider meta --api-key-stdin
goose configure
dsh --help                    # dsh endpoint credentials use apiKeyEnv in ~/.dsh/settings.yaml
```

## Routing to a self-hosted endpoint

Install and authenticate a model-agnostic harness here, then represent the
server as a named route rather than embedding endpoint details in dispatch
commands. See [Attach a locally hosted model](attach-local-endpoint.md) to
find and attach one from scratch, and [Self-hosted endpoints](self-hosted.md)
for the route recipe, cold-start timeout settings, discovery, and liveness
checks.

### DeepSeek Harness endpoint sync

`pitwall agents profiles sync` writes dsh's hot-reloaded `$DSH_HOME/settings.yaml`
(default `~/.dsh/settings.yaml`). It adds one managed
`llm-pi-ai.providers.<route>` mapping with `api`, `baseURL`, optional
`apiKeyEnv`, and `models`, and selects it through dsh's
`agent-default-model` settings section. Dispatch then uses dsh's stock
`headless` profile; no generated profile or `package.json` is required.

The settings document is shared with dsh and the user. Sync preserves unrelated
sections, comments, and unmanaged providers, and removes only provider mappings
marked as managed by pitwall. Because this project has no YAML
dependency, its merger intentionally supports ordinary indented block mappings
for the two sections it edits. It refuses duplicate sections, inline/flow
mappings for those sections, malformed managed markers, and files without a
final newline; normalize such YAML before retrying rather than allowing sync to
clobber it. Only one dsh endpoint route can be selected as the agent default at
a time.

### Hermes Agent endpoint caveat

Hermes resolves endpoint credentials from its own provider configuration, not
from the environment: an `OPENAI_API_KEY` set for the process is never
attached to a non-loopback endpoint. `pitwall agents profiles sync --harness
hermes` merges a managed `providers:` block into
`${HERMES_HOME:-~/.hermes}/config.yaml`, recording `key_env` — the variable's
name, never its value — and dispatch selects the route automatically with
`--provider <route-name>`. Unlike Cline and dsh, Hermes holds several managed
providers at once. If Hermes still prints an `HTTP NNN:` failure and exits 0,
the shim reports exit 77 instead.

Hermes also classifies endpoint spellings differently: a CGNAT IP in
the RFC 6598 CGNAT range is "local", while a
dotted DNS name for the same endpoint is remote and retains the 90 s first-byte stale kill.
Set `providers.<id>.stale_timeout_seconds` in Hermes' configuration to escape that
remote-endpoint default.

## Results and recovery

Selected providers run independently in stable registry order. A failed provider does not prevent later selections from running. The final summary distinguishes download errors, installer nonzero exits/timeouts, unresolved binaries, and failed local version checks.

Exit codes:

- `0`: all selected installs verified, all providers were already installed, nothing was selected, confirmation was declined, or setup was skipped
- `1`: at least one selected provider failed to download, install, resolve, or verify
- `2`: invalid manifest/invocation, unsupported native platform, or required TTY unavailable
- `130`: Ctrl-C

Bootstrap continues to print registration and recovery guidance after optional setup exit 1, then returns nonzero so the failure is visible. An explicitly forced menu propagates invocation errors immediately.

After resolving a failure:

```bash
pitwall agents setup harnesses
pitwall agents doctor
```

The default doctor remains read-only and does not install, authenticate, upgrade, or discover models.

## ZCode

The ZCode adapter uses an already installed CLI and its saved account/model.
The `manual` recipe has no download or execution fields: when ZCode is missing,
setup returns a failed result with the official build/install documentation. An
installed binary is kept. See [ZCode account route](zcode.md).
