# Security Policy

## Supported Versions

Only the latest commit on the `main` branch is supported.

## Reporting a Vulnerability

Report vulnerabilities via **GitHub private vulnerability reporting**:

1. Open the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Include a description, reproduction steps, and impact.

Do not open public issues for undisclosed vulnerabilities. Best-effort acknowledgment within 7 days (solo maintainer).

This component uses the Pitwall repository's single disclosure and advisory process. The root
[`SECURITY.md`](../../SECURITY.md) is authoritative for reporting targets, supported versions, and
cross-component credential scopes; this file supplies the Agent Routing-specific threat model.

## Scope

The shims intentionally execute AI-directed commands. Setting `PITWALL_AGENTS_UNRESTRICTED=1` bypasses child-CLI sandbox prompts; this explicit opt-in is documented, designed behavior — not a vulnerability. Without it, each child CLI keeps its own sandbox and approvals.

In-scope: command/argument injection through shim parameters, ledger-write path traversal, hook parsing flaws that could execute content, installer download/verification weaknesses.

Out of scope: risks from the documented opt-in unrestricted execution model; social engineering.

## Optional provider installer boundary

`pitwall agents setup harnesses` is an explicit mutation surfaces, separate from the read-only doctor and normal shim installation. Missing providers begin unchecked, installed providers are disabled, and selected first-party source domains are shown before a second `[y/N]` confirmation. Automatic bootstrap skips provider setup when `/dev/tty` is unavailable, so CI and redirected installs cannot quietly install provider CLIs.

Installer recipes are argv-only data in `src/pitwall/agents/resources/config/harness-installers.json`. The loader requires exact provider-registry parity, HTTPS URLs, a fixed `bash`/`sh` interpreter allow-list, and provider-specific redirect hosts. Downloads have connection/read/size bounds, must begin with a shebang, and are written to mode-`0600` temporary files only after validation. Execution uses no `eval`, shell command string, or routing run record; temporary scripts are removed in cleanup paths. One provider failure does not broaden authorization to another provider.

These moving first-party installer URLs do not expose one common detached checksum/signature contract. The checked-in manifest therefore pins the exact reviewed installer bytes with SHA-256 when the endpoint can be inspected. A digest mismatch fails before execution. A provider without a reviewed digest is labeled `WARNING: no pinned checksum` during confirmation and remains protected only by HTTPS, exact origins, redirect allow-listing, bounded content, explicit confirmation, and source review. Upstream installers may perform their own artifact verification and may update user-level PATH configuration. This project never runs provider login commands or writes credentials/provider configuration itself. Users requiring vendor-signed or release-pinned artifacts should use each provider's documented verification flow. See [provider CLI setup](harness-cli-setup.md).

`setup inventory` is a separate read-only scan followed by private local snapshot writes. It reads
bounded known JSON, TOML, and YAML config files only for detected harnesses and enumerates bounded
capability directories. Output includes recognized capability names and source paths, never config
values or skill/agent file bodies. The JSON and Markdown snapshots use the routing config root's
mode-`0700` directory and mode-`0600` files. A capability name indicates local availability only;
it does not grant permission, prove authentication, or weaken the receiving harness's policy.

## Runtime data and prompt exposure

The runtime creates private run directories under `${XDG_STATE_HOME:-~/.local/state}/pitwall/agents/runs/`. Directories are mode `0700`; files are mode `0600`. Prompt bodies are not written by default: `request.json` stores the source type/path, byte length, and SHA-256 digest. `--routing-retain-prompt` explicitly writes `prompt.md`.

Provider stdout and stderr are retained for recovery and can contain source code, credentials printed by tools, or other sensitive material. There is no automatic deletion. Use `pitwall agents runs cleanup --older-than <days>` or `--all` according to your retention policy. Cleanup skips active isolated worktrees; discard those explicitly after review.

Kimi Code, Antigravity CLI, Qwen Code, Hermes Agent, Cline CLI, ZCode, and dsh receive the prompt body as a command-line argument to preserve their verified CLI contracts. While those children run, the prompt may be visible to same-user process inspection tools such as `ps`. Claude Code, Codex, OpenCode, and goose receive prompts over stdin; Grok Build, Pi, and Muse Code receive a prompt-file path (a private mode-`0600` `prompt.deliver.md` for Grok Build and Pi). Disabling on-disk prompt retention does not prevent argv visibility. Kimi prompt mode applies its unattended `auto` permission policy while retaining configured static deny rules; an unset or `0` `PITWALL_AGENTS_UNRESTRICTED` cannot make this CLI mode interactive or manual (the shim refuses to dispatch Kimi without `=1`). The shim rejects `-y`/`--yolo`/`--auto` because Kimi forbids combining them with prompt mode.

Lifecycle hooks are trusted local code configured in `${XDG_CONFIG_HOME:-~/.config}/pitwall/agents/hooks.json`. Commands execute directly as argument arrays, receive metadata-only event JSON on stdin, and do not receive prompt or output content by default. Hook stdout/stderr is captured privately. Hooks fail open, so malformed configuration must not suppress a shim sentinel.

## Isolated-worktree limitations

`--routing-workspace isolated` protects the caller's checkout from direct edits, but Git worktrees share the repository object database and are not containers. The child retains the same user identity, filesystem permissions, credentials, processes, and network access allowed by its provider sandbox policy. An isolated child can still access paths outside its worktree unless the provider sandbox prevents it.

Dirty source changes are never copied, stashed, or synthesized automatically. Without `--routing-base <commit>`, dirty source repositories are rejected. Ignored files are not included in captured patches.

`runs apply` accepts only an owned run with a safe path manifest, patch paths contained by that manifest, a matching Git common directory, and a clean target worktree. A three-way application can intentionally leave conflicts for manual resolution. Explicit commit replay can leave a cherry-pick in progress; resolve and continue it, or use `git cherry-pick --abort`, as reported by the integration message. Model-generated patches remain untrusted code: inspect `runs diff`, verify the target diff, and rerun project checks before committing. `runs discard` verifies the recorded owner, branch, and worktree path before deletion and requires confirmation unless `--yes` is explicit.

The default doctor is read-only and performs no live model discovery or authentication prompt. It may run Kimi's documented `doctor config` validator, which does not modify configuration or validate credential liveness. `--live-auth` explicitly opts into documented local authentication-status commands; Antigravity has no such status command and instead sends one bounded pong inference request, which may consume provider usage. Doctor does not initiate login or credential changes, and provider CLI refresh behavior remains provider-defined. Kimi reports `SKIP` because it exposes no authentication-status command. `--discover-models` separately opts into bounded provider discovery. OpenCode discovery may contact its configured provider; Codex reads its local CLI cache; Kimi parses only validated model-alias keys from its local provider JSON. Raw output, provider objects, environment values, and credentials are not copied into the doctor report.

## Workflow and nested-orchestration risks

Workflow documents can cause several unattended agents to read, modify, and verify a repository. The scheduler snapshots task prompts and retains selected dependency context, wrapper output, verification output, state, and normal dispatch artifacts below the private workflow/run roots. Treat those files as sensitive and remove them according to the same retention policy as direct runs.

`contextFrom` is deliberately allow-listed and byte-bounded, but selected artifacts may still contain source, secrets printed by tools, or malicious instructions generated by another model. Review workflow files before running them. Verification commands are direct argv arrays without shell expansion, but they are trusted local commands supplied by the workflow author.

The `--host` value is self-declared misuse prevention, not a security boundary. A caller can claim `--host copilot` to pass runner-side native-family validation. Claude's observed-tool tripwire hooks remain the enforcement layer and native Claude Workflow remains the default for Claude-hosted graphs.

Cancellation signals the scheduler and active shim wrappers so provider process groups are terminated. Isolated worktrees are intentionally retained for inspection, including after failure or cancellation. Resume verifies workflow/registry digests, Git repository identity, completed artifacts, and retained write worktrees before scheduling incomplete tasks again.
