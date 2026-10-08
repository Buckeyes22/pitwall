# ZCode through a Z.AI account

The `zcode` harness runs the installed official ZCode Agent CLI with the operator's
saved model and account. It does not read or copy credentials, initiate login, or
rewrite ZCode configuration. Existing OpenCode GLM routes remain independent.

## Setup and dispatch

Install/configure ZCode first, then add its saved selection as a profile:

```sh
pitwall agents profiles add zcode --model zcode-default --harness zcode --account zai
pitwall agents dispatch route zcode /absolute/path/to/prompt.md
```

`zcode-default` labels ZCode's saved selection; it does not pin GLM or claim the
served model ID. Change the model in ZCode. CLI 0.16.9 has no `--model` or effort
flag, so these overrides are refused. The adapter sends `--prompt` as one argument
(with the normal 120 KiB prompt limit), `--cwd` for the dispatch workspace, and
text output. It uses `--mode yolo` only when `PITWALL_AGENTS_UNRESTRICTED=1` is set and
explicit `--mode build` otherwise, because ZCode's own
headless default is yolo. Build mode can require interactive permission approval.
Dynamic workflows are not enabled. ZCode's own enabled plugins, hooks and MCP
configuration still apply. `ZCODE_BIN` overrides binary discovery.

ZCode supports other providers internally, but this adapter exposes only its
saved selection and does not materialize custom endpoint profiles. Doctor checks
the binary/help contract; it does not infer authenticated readiness from auth files
or issue a model request. Verify with an explicitly requested small coding prompt.

## Installation

This harness is detected and usable when installed. Pitwall's harness installer
reports manual installation for a missing ZCode CLI: the upstream repository
documents building a distribution and hosting its installer, but does not supply
a canonical pinned public CLI install URL. Do not substitute an unrelated npm CLI.

## Sources

Checked 2026-10-04:

- [Official model and account setup](https://zcode.z.ai/en/docs/configuration):
  an account-bound Coding Plan routes automatically and uses the account's quota.
  Coding API keys use `/api/coding/paas/v4`; the general prepaid endpoint differs.
- [Official CLI README](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/README.md):
  CLI configuration, plugins, MCP and hooks.
- [Official CLI argument parser](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/packages/cli/src/arguments.ts):
  prompt, cwd, modes, text output; no per-run model selector.
- [Official distribution instructions](https://github.com/zai-org/ZCode#zcode-命令行版).
- Live installed `zcode --help`: CLI 0.16.9; runtime distribution 3.14.3.
