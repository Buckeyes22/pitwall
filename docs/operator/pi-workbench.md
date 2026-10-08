# Pi Workbench

`pitwall workbench` launches the stock, pinned Pi coding agent with an exact provider and model profile
and an isolated `PI_CODING_AGENT_DIR`. The opt-in `--native-child` profile adds bounded scout, writer,
and reviewer roles through the pinned child backend, with fresh context, role-specific tools, and
one-slot request admission across cooperating Workbench processes. It makes no endpoint-wide quota
promise. The technical map, including which Pi version is pinned and why, is
[25 — Pi Workbench](../sdlc/25-pi-workbench.md).

## Install

```bash
uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl
pitwall agents setup pi
pitwall workbench doctor
```

`pitwall agents setup pi` installs the pinned Pi and child-backend packages; `--dry-run` prints the
command it would run. Requirements are Linux, Node 22.22.1 or later, and `flock` (util-linux) for shared
request admission. Restricted mode additionally requires Bubblewrap, a `setpriv` that supports
`--seccomp-filter` (util-linux 2.41 or later; Ubuntu 24.04 ships 2.39), permitted user namespaces, and an
x86_64 or arm64 runtime. The launcher checks these before starting Pi and refuses an unrestricted
fallback. `pitwall workbench doctor` reports each prerequisite, and `pitwall doctor` includes the same
checks in its `workbench` section.

## Profiles

Copy `config/workbench.example.json` to a private file and replace the model, endpoint, context and
output limits, and reasoning mapping with verified server values. Enable image input only after
verifying vision support. The example's chat-template mapping is server-specific. A profile names an
environment variable with `apiKeyEnv`; it never holds a key. `accountRef` selects an existing OpenCode
API-key entry in memory.

## Launch

```bash
pitwall workbench launch /absolute/path/to/profile.json local-coder /absolute/path/to/worktree
```

The arguments are `<profile.json> [profile-name] [cwd]`; flags are `--continue`, `--native-child`,
`--planning` (or `--plan`), and `--restricted`.

- `--continue` continues the same Pi session; reuse the same agent directory and profile.
- `--native-child` gives the coordinator `agent_task` and `agent_control`. Read-only leaves receive only
  read, grep, find, and ls. Use `/agents` to inspect a child and its transcript, `/workbench-steer
  <message>` to redirect the same active child, and `/workbench-stop` to clear its queue and stop it.
- `--planning` is a user-selectable read-only native session. It enables the native coordinator
  automatically, exposes only `read`, `grep`, `find`, `ls`, `agent_task`, and `agent_control`, blocks
  destructive tool calls at execution time, and rejects writable child roles.
- `--restricted` starts the complete Pi process tree inside Bubblewrap. It permits writes to the
  selected workspace, the isolated agent directory, and the shared admission directory, and mounts the
  runtime read-only. The home, run, and temporary trees are hidden behind private mounts. The trusted
  Pi process keeps network access for the selected provider; each Bash tool child gets a seccomp filter
  that denies socket syscalls, and an environment with credential variables removed. Use a standalone
  repository for restricted writer tasks.

State defaults to a private directory under `~/.local/state/pitwall/pi-workbench/`, partitioned by mode,
working-directory hash, and profile name. To keep sessions in a specific place, set
`PITWALL_WORKBENCH_AGENT_DIR` to a private absolute directory. Select a new empty directory for a
different profile: the compiler rejects unowned content or profile drift rather than overwriting it.

## Baseline and acceptance

```bash
pitwall workbench fixture /absolute/path/to/new-fixture
pitwall workbench baseline /absolute/path/to/profile.json local-coder \
  /absolute/path/to/new-fixture /absolute/path/to/baseline-report.json
```

Fixture creation requires a new directory; use a new fixture for each run, because a previously repaired
fixture deliberately fails the repair precondition. The baseline needs the selected credential
environment variable and does not retrieve or copy credentials. It reports each gate as passed, failed,
or unexecuted, so a missing endpoint cannot become a simulated success.

Two bounds keep a slow host from failing a correct run. The independent fixture check
(`node --test add.test.mjs`, run by the baseline and by `hosted-acceptance` before the repair, after it,
and after compaction) has a 120 s ceiling. It is a hang guard rather than a latency gate: a check that
is merely slow still passes, and one that exceeds the ceiling fails the gate with
`fixture test <before repair|after repair|after compaction> did not finish within 120 s`. When a run ends, the baseline, `hosted-acceptance`, and
`hosted-native-acceptance` stop Pi by clearing its queue, aborting, and closing its stdin. A Pi that
exits on its own within 2.5 s is not reported as forced; otherwise it receives SIGTERM, and SIGKILL if
it is still running 5 s after the stdin close.

The hosted gates take an OpenCode auth file and one exact profile at a time:

```bash
pitwall workbench hosted-acceptance PROFILE_CONFIG OPENCODE_AUTH_JSON OUTPUT_DIR [PROFILE ...]
pitwall workbench hosted-native-acceptance PROFILE_CONFIG OPENCODE_AUTH_JSON OUTPUT_DIR [PROFILE]
```

These run a fresh fixture, perform a real repair, run an independent acceptance check, force RPC
compaction, continue the same Pi session, and rerun the check. Reports are redacted, and the key is
held in memory and passed only to the selected child environment. This is bounded validation evidence,
not a provider-wide quota or cancellation guarantee. The comparison suite is `pitwall workbench compare`,
with `reevaluate` and `reevaluate-child` to re-score a saved report.

## Environment

The `PITWALL_WORKBENCH_*` variables are listed in `.env.example`: `PITWALL_WORKBENCH_PI_BIN`,
`PITWALL_WORKBENCH_RUNTIME_DIR`, `PITWALL_WORKBENCH_AGENT_DIR`, `PITWALL_WORKBENCH_RESOURCE_DIR`,
`PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR`, `PITWALL_WORKBENCH_TINTIN_EXTENSION`,
`PITWALL_WORKBENCH_CANDIDATE_B_EXTENSION`, and `PITWALL_WORKBENCH_HOSTED_MAX_COMPLETION_TOKENS`. The
launcher sets the rest itself; leave them empty. Only the selected credential and operational
environment are forwarded to Pi.

## Reading usage

`pitwall workbench usage <accounting.jsonl>` reports recorded provider usage and request queue time from
the run's accounting file. Missing usage stays unavailable. These records do not represent the
provider's remaining quota or a billing cap. `pitwall workbench doctor` and `usage` are read-only JSON
commands and tolerate an absent local OpenCode configuration.

## Writer handoff

Native profiles expose `agent_task` and `agent_control`; the latter supports list, inspect, steer,
cancel, and explicit `integrate <handoffId>`, and `/agent-control` offers the same actions.
`/workbench-integrate <handoffId>` is the direct human command for applying a validated handoff with
`PITWALL_WORKBENCH_APPROVED_CHECKS`. Writers require a clean Git base and use a dedicated worktree; a
dirty base fails without stashing or resetting anything. Handoffs persist under the repository's
`.pi-workbench-handoffs` directory and bind task ID, base commit, workspace, and patch hash. No patch is
committed or applied automatically. Set `PITWALL_WORKBENCH_APPROVED_CHECKS` to a JSON array of objects
with `command`, `args`, and an optional `timeoutMs`; they run in the writer worktree after completion,
and without them validation is "not run", not "passed". Integration applies the exact patch to a clean,
unchanged target checkout, reruns the checks there, and leaves the result uncommitted for inspection.
Keep handoff directories until review and explicit integration.

Completed records expose task, run, and attempt lineage, execution and acceptance states, backend
session-file references when the backend provides them, and full result artifacts beside bounded
summaries. Resume of an unfinished native child is not advertised; completed sessions use `--continue`.
