# 25 — Pi Workbench

Pi Workbench is `pitwall workbench`, the `pitwall.workbench` Python package. It launches the pinned
upstream Pi runtime for a direct interactive session or a bounded local RPC acceptance run, with one
exact provider and model profile. It is deliberately separate from Agent Routing's one-shot
compatibility adapter. Its native roles use one pinned upstream child backend; the broker and the
durable scheduler remain separate. The user guide is
[Pi Workbench](../operator/pi-workbench.md).

The launcher, profile compiler, admission, accounting, workspace handoff, restricted mode, doctor,
and the acceptance and comparison runners are Python. The Pi extensions that run inside Pi are
TypeScript, committed as compiled JavaScript beside their sources in
`pitwall/workbench/pi_extensions/`; `make pi-extensions-check` recompiles them with the TypeScript
version in `pi_extensions/COMPILER` and fails if the committed `.js` differs.

## Pinned Pi and which version actually runs

The pin comes from `pitwall/agents/resources/config/harness-installers.json`, which also drives
`pitwall agents setup pi`; the workbench Python constants are read from it, and
`tests/workbench/test_pi_pin.py` checks that the CI install steps and these docs state the same
versions. Workbench targets `@earendil-works/pi-coding-agent@0.84.4` and the stock
`pi` executable, with `@tintinweb/pi-subagents@0.19.0` as the native child backend
(`pitwall.workbench.doctor:PINNED_PI_VERSION`, `pitwall.workbench.doctor:PINNED_SUBAGENTS_VERSION`).
`pitwall agents setup pi` installs exactly those two packages (`npm install -g --ignore-scripts`), and
every Pi-launching command checks them first, before it creates a worktree, an agent directory, or any
other state (`pitwall.workbench.doctor:require_pinned_toolchain`). A different Pi version is refused.

The version that runs is the installed 0.84.4, selected deliberately. The architecture plan's mention
of 0.85.1 is not evidence that Workbench runs that release. The supported capability is the tested
0.84.4 stock CLI and its pinned Workbench integrations: exact profile launch, supported `--continue`
session continuation, bounded RPC measurement, and the explicitly loaded native child and steering
interfaces. It does not assert support for Pi 0.85.1, unreleased source, or extension APIs outside
those acceptance results; such a claim requires a new pin and a rerun of the pinned-runtime checks.

Pi itself declares MIT licensing. Pitwall, including Workbench, is Apache-2.0.

## Profile and state ownership

Profiles resolve one exact provider, model ID, endpoint, credential reference, context limit, and
resource group (`config/workbench.example.json` is the template). Credentials remain
environment-variable references, optionally resolved from an existing named OpenCode account. A
deliberate keyless endpoint must state the supported dummy-key policy. Profile compilation
(`pitwall.workbench.profile:compile_profile`) writes a provider entry into a fresh, restrictive
`PI_CODING_AGENT_DIR`; an ownership marker (`.pi-workbench-owned.json`) permits reopening the same
profile and preserves Pi session files. Edited model configuration, unowned files, symlink
directories, and profile drift fail closed
(`pitwall.workbench.profile:configure_provider_profile`).

The provider extension rechecks the private generated profile at load time. For hosted accounts it
requires an explicit account budget and one exact model, endpoint, API, and credential reference; each
request compares Pi's selected model and resolved key before transport. A profile label alone does not
prove the provider's account-side entitlement. Aliases that share an `accountGroup` share one
persisted reservation ledger (`pitwall.workbench.account_budget:AccountBudgetAdmission`).

State defaults to `~/.local/state/pitwall/pi-workbench/`, partitioned by mode (`agent`, `native`, or
`native-planning`), the canonical working-directory hash, and the profile name
(`pitwall.workbench.cli:default_agent_dir`), so using multiple profiles cannot reuse one agent
directory. `PITWALL_WORKBENCH_AGENT_DIR` selects an explicit private directory.

## Runtime boundary

The launcher (`pitwall.workbench.launcher:launch_pi`) sets `PI_CODING_AGENT_DIR`, `PI_OFFLINE=1`, and
`PI_TELEMETRY=0`, and starts stock Pi with every discovery surface disabled (`--no-extensions
--no-skills --no-prompt-templates --no-themes`); only the explicit extension paths load. The packaged
extensions import Pi's own packages, so the launcher stages them once into a content-addressed,
read-only runtime directory whose `node_modules` links the pinned Pi packages
(`pitwall.workbench.launcher:runtime_root`). Only the selected credential and operational environment
reach the trusted Pi process. Interactive continuation uses Pi's supported `--continue` option. No
provider fallback is introduced.

The optional native profile (`--native-child`) loads the pinned backend and the Workbench adapter,
restricts the coordinator to stock tools plus `agent_task` and `agent_control`, and provides same-child
steering, queue clearing, and stop through supported session and backend interfaces
(`pitwall.workbench.native_profile:configure_native_profile`). Planning mode (`--planning`, alias `--plan`, which also implies `--native-child`) validates
the managed child role files against the selected model and read-only tool and isolation policy before
native registration (`pitwall.workbench.native_profile:validate_planning_role_profiles`); parent write
tools are removed and fabricated write calls are blocked at execution time.

Runtime settings are enforced, not advised: no provider or agent retries, derived compaction, and the
HTTP idle deadline (`pitwall.workbench.runtime_settings:enforce_runtime_settings`). Timeouts are
separate controls: `requestInactivityTimeoutMs` (Pi's header and body idle deadline),
`generationTimeoutMs` (total active provider generation), `toolTimeoutMs` (solo shell commands), and
`taskTimeoutMs` (the native task deadline); `requestTimeoutMs` remains a combined fallback
(`pitwall.workbench.timeouts`).

## Admission

Admission is one active request per resource group. `pitwall.workbench.admission:RequestAdmission` is
an in-process asyncio permit with strict FIFO hand-off.
`pitwall.workbench.admission:SharedRequestAdmission` is a host-wide permit backed by `flock` on a
private lock file, so cooperating Workbench processes, including parent and child requests and
compaction through the wrapped provider, take turns. The kernel owns the lock: a crashed holder
releases it when its file descriptor closes, with no stale-lock deletion and no lease stealing. It does
not constrain unrelated clients or other machines. Aborting a granted request retains its permit until
transport settlement; tools and waiting coordinators do not retain inference permits. The default
directory is `~/.local/state/pitwall/pi-workbench/admission` (`PITWALL_WORKBENCH_RESOURCE_DIR`).

## Measurement and acceptance

The explicitly loaded measurement extension records bounded provider payload shape, safe reasoning
fields, provider usage when reported, and compaction events
(`pitwall.workbench.accounting:shape_payload`, `pitwall.workbench.accounting:append_accounting`). It
does not retain prompt text, tool output, credentials, or complete transcripts. `pitwall workbench
usage <accounting.jsonl>` reports recorded usage and request queue time
(`pitwall.workbench.accounting:usage_report`); missing usage stays unavailable.

The RPC baseline (`pitwall workbench baseline`) requires a named disposable fixture
(`pitwall workbench fixture <new-directory>`) and reports identity, repair, compaction continuation,
vision when advertised, reasoning control, cancellation, and accounting as passed, failed, or
unexecuted gates. Fixture validation runs independently of the model's report. Compaction summary
usage comes from the RPC compact response; the provider-request hook does not claim to observe
compaction payloads. The hosted gates (`hosted-acceptance`, `hosted-native-acceptance`) run one exact
hosted profile at a time against a fresh fixture, with the credential held in memory and reports
redacted (`pitwall.workbench.hosted.acceptance:run_hosted_acceptance`). A hosted profile's
`apiKeyEnv` is always `PITWALL_PI_HOSTED_KEY`: the runner reads the account's key from the auth
file and places it under that name only in the Pi child's environment; it is not an operator-set variable. `compare`, `reevaluate`, and
`reevaluate-child` drive and re-score the comparison suite
(`pitwall.workbench.comparison.runner:run_comparison`).

The baseline is evidence about the selected local runtime and fixture only. It makes no claim about
admission across unrelated clients, hosted-account quota guarantees, benchmark superiority, or
server-side GPU cancellation. The native backend owns child lifecycle; Workbench owns explicit writer
handoffs.

Durable native task records accept result and summary artifacts only when they are existing regular
files owned by the task under the task-record directory. Traversal, arbitrary filenames, missing paths,
and final or intermediate symlink escapes fail record validation
(`pitwall.workbench.task_record:assert_task_artifact_path`,
`pitwall.workbench.task_record:TaskRecordStore`).

## Writer handoff

Writers require a clean Git base and use a dedicated worktree
(`pitwall.workbench.workspace:create_handoff_workspace`). Dirty bases fail without stashing or
resetting changes. Handoffs persist under the repository's `.pi-workbench-handoffs` directory; their
manifests bind task ID, base commit, workspace, and patch hash
(`pitwall.workbench.workspace:capture_patch`, `pitwall.workbench.workspace:load_handoff`). No generated
patch is automatically committed or applied. Crash recovery preserves files for inspection
(`pitwall.workbench.workspace:recover_handoff`). The host may supply `PITWALL_WORKBENCH_APPROVED_CHECKS`
as a JSON array of `{command, args, timeoutMs?}`; those commands run in the writer worktree after
completion (`pitwall.workbench.workspace:run_approved_checks`), and without them validation is not run,
not passed. Integration applies the exact patch to a clean, unchanged target checkout, reruns the
approved checks there, and leaves the result uncommitted
(`pitwall.workbench.workspace:integrate_handoff`).

## Restricted mode

`--restricted` starts the complete Pi process tree inside Bubblewrap and launches each Bash tool child
through `setpriv` with an unlinked per-launch seccomp filter that denies classic socket syscalls and
`io_uring` setup, enter, and register
(`pitwall.workbench.restricted:build_restricted_command`, `pitwall.workbench.seccomp:seccomp_program`).
The trusted Pi process keeps network access for the selected provider; each Bash tool child receives a
derived environment with the selected and conventional provider credential variables removed
(`pitwall.workbench.restricted:restricted_tool_environment`). Startup fails closed, with no
unrestricted fallback, unless Linux, `/usr/bin/bwrap`, a `/usr/bin/setpriv` with `--seccomp-filter`
(util-linux 2.41 or later), permitted user namespaces, and an x86_64 or arm64 runtime are present
(`pitwall.workbench.restricted:assert_restricted_prerequisites`). Use a standalone repository for
restricted writer tasks: an external Git common directory is intentionally not made writable.

## Doctor and checks

`pitwall workbench doctor` inspects installed hosted metadata and credential presence and reports the
toolchain and restricted-mode prerequisites; it makes no provider request
(`pitwall.workbench.doctor:doctor`). The `workbench` section of `pitwall doctor` reports
`workbench.pi`, `workbench.pi_subagents`, `workbench.node`, `workbench.flock`, and
`workbench.restricted`; the workbench is optional, so gaps warn or skip
(`pitwall.workbench.doctor:workbench_section`).

Tests are hermetic and live in `tests/workbench/`; run them with `make test-fast`. Live checks stay
behind the `live` marker.

## Source map

- Command line: `pitwall.workbench.cli` (`pitwall.workbench.cli:parse_launch_args`).
- Profile validation and owned config: `pitwall.workbench.profile`.
- Interactive and RPC launch: `pitwall.workbench.launcher`.
- Native planning role validation: `pitwall.workbench.native_profile`.
- Host-local request lease: `pitwall.workbench.admission`.
- Hosted account budget: `pitwall.workbench.account_budget`.
- Writer ownership, capture, recovery, and integration: `pitwall.workbench.workspace`.
- Restricted process boundary: `pitwall.workbench.restricted` and `pitwall.workbench.seccomp`.
- Task-artifact containment: `pitwall.workbench.task_record`.
- Provider-reported usage view: `pitwall.workbench.accounting`.
- Hosted acceptance and baseline: `pitwall.workbench.hosted`.
- Comparison suite: `pitwall.workbench.comparison`.
- Pi extensions: `pitwall/workbench/pi_extensions/`.
