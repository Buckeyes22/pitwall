# Pitwall + Pi Workbench: Repository-Grounded Integration Plan

**Prepared for the maintainer — 19 September 2026 — America/New_York**  
**Status:** Architecture amendment and focused source review; no repository implementation changes.  
**Input:** `pitwall.zip`, the earlier Pi architecture plan, and its Codex handoff.

## 1. Decision

Keep upstream Pi as the interactive coding harness. Add an independently installable **`packages/pi-workbench/`** package to the existing Pitwall monorepo rather than create a competing standalone routing project.

Pitwall Agent Routing already supplies cross-harness dispatch, named routes, process supervision, private run records, worktree capture/integration, receipts, and an orchestrator mailbox. Reuse those at the cross-harness boundary. Pi should supply the actual interactive conversation, coding tools, session context, and model interaction. Its session-local subagents should have one selected lifecycle backend, not several stacked managers.

The existing `pi-shim` is useful, but it is **not** the interactive Pi harness you described. It launches a one-shot print-mode process with session persistence disabled. Improving that adapter and building the interactive package are related but separate workstreams. [R1–R5]

The native Pi subagent packages considered in the earlier plan remain candidates. Do not automatically import their route registries, schedulers, worktree managers, and run stores alongside equivalent Pitwall facilities. First assign ownership to each execution scope. A package is useful only where it fills a real gap.

### What this changes in the previous plan

| Earlier planning topic | Repository-grounded amendment |
|---|---|
| Add a Pi Workbench integration package | Put the new independently installable TypeScript package inside this existing monorepo. |
| Most dispatch/lifecycle plumbing needs to be sourced from another project | Cross-harness plumbing already exists in `packages/agent-routing`. |
| Choose a subagent package before integration design | Define run/workspace/cancellation ownership first; evaluate native Pi backends within that boundary. |
| Provider setup is principally configuration | First correct Pi-specific configuration preservation and validation in the existing adapter. |
| A managed Pi child can be reached through existing workflows | Direct Pi dispatch exists, but the current workflow production dispatcher does not support Pi. |
| Existing resume can provide the familiar live-session experience | Pitwall's tier-4 resume replays a prompt; it is not recovery of a persisted Pi conversation. |

The earlier plan's compact roles, fresh leaf contexts, accepted-task measurements, exact provider selection, and single-request local admission policy remain valid design goals. This amendment takes precedence on repository placement, existing capabilities, and implementation order.

## 2. Evidence and review limits

The archive contains the broker, `packages/agent-routing`, `packages/gateway`, documentation, tests, and Git metadata. Declared versions are broker `0.1.0a2`, Agent Routing `0.11.1`, and gateway `0.1.2`. The included branch reference points to `66f323109f67fec47317def53bd3a879067dd91a`; that does not establish that every archived working file matches that commit. [R1, R2, R13]

Archive SHA-256:

```text
40473ddd5def54c8113345612047187c39c7d91d8238cd977923cd8d18e0003d
```

This was a focused review of the routing/adapter/workspace/channel/scheduler integration, not a complete audit of every broker, gateway, security, and provider module. Evidence categories below are:

- **Source:** Directly observed in the uploaded repository.
- **Probe:** Reproduced by an isolated local call to the relevant source implementation.
- **Upstream:** Checked against current official Pi documentation; the implementation session must pin and verify the actual installed release.
- **Proposal:** New behavior, package layout, policy, or acceptance criteria recommended here. Proposed field and command names are not claims about existing APIs.

The available Python was 3.13.5. The normal adapter test suite could not collect because this component requires Python 3.14 and an imported runtime file contains syntax the available interpreter cannot parse. An attempt to obtain a 3.14 interpreter failed on DNS access. **No full-suite pass is claimed.** Five narrower probes ran against the unmodified Pi adapter or an extracted unmodified scheduler helper; they did not invoke an actual model or the complete supervisor.

No provider credentials were used. No inference, paid provisioning, deployment, repository push, global configuration edit, or source-code change was performed.

## 3. What Pitwall already provides

### 3.1 Keep the broker optional

The main Python application is a GPU/workload broker. It includes provider operations, cost/routing controls, REST/MCP/CLI/TUI surfaces, and deployment infrastructure. Those are useful when provisioning or operating model endpoints. They are not a necessary dependency for using an existing local model server through Pi. [R1]

Likewise, `packages/gateway` is a separate gateway component. Do not make its proxy, catalogue, or fallback behavior mandatory for your local Qwen, native MiniMax/Z.AI, or Alibaba plan connections. Requiring every local coding request to traverse that stack would be a new architectural choice, not a consequence of this monorepo. [R13]

### 3.2 Reuse Agent Routing at the external boundary

Agent Routing is independently packaged, requires Python 3.14, and declares no runtime dependencies. Its relevant existing surfaces include:

| Existing surface | Repository location | Reuse |
|---|---|---|
| Pi shell transport and adapter | `scripts/pi-shim.sh`; `src/pitwall/agents/providers/pi.py` | Preserve the one-shot compatibility lane. |
| Named route resolution | `routes.py`; canonical `provider-registry.json` | Share route identity and explicit harness choice. |
| Endpoint materialization | `route_sync.py`; provider adapters | Correct and reuse, with clear field ownership. |
| Process supervision | `process.py`; `dispatch.py` | Retain for externally dispatched runs. |
| Durable run artifacts | `run_store.py`; `events.py`; `result.py` | Keep external run identity, logs, receipts, and lifecycle history. |
| Worktree management | `workspace.py` | Reuse for Pitwall-owned write dispatches and explicit integration. |
| ASK/ANSWER/STEER mailbox | `channel.py`; `mailbox.py`; `steer_gate.py` | Preserve the cross-harness channel; bridge to Pi selectively. |
| Foreground dependency scheduler | `workflow.py`; `scheduler.py` | Keep as an explicit batch/workflow feature, not a mandatory interactive agent scheduler. |

These are implemented modules and documented contracts, not proof that all guarantees have been exercised successfully on your current host. [R2–R11]

### 3.3 Preserve the existing harness boundaries

Claude Code remains Claude Code, Codex remains Codex, and OpenCode Go remains OpenCode/its shim. The routing documentation already follows the same host-native separation. No subscription transport migration is required to add Pi. [R2, R3]

Do not rewrite the entire shared routing skill for the new package. Keep the existing Claude/Codex/Copilot assets distinct, and do not load their complete catalogues and reference documents into every Pi leaf.

## 4. Findings that affect the implementation

### F1 — Route sync replaces enriched Pi provider configuration

**Classification:** Source + probe. **Priority:** Fix before applying Pi route sync to a tuned configuration.

`PiAdapter._block()` constructs a minimal provider block containing an OpenAI-compatible API, endpoint, model ID, token limits, and optionally an environment-variable key reference. `plan_endpoint_sync()` then assigns `providers[name] = block`. It does not merge the selected model by ID into an existing enriched provider. [R4: lines 76–93 and 109–130]

The probe supplied a provider containing two models, provider-level `compat` and `headers`, and model-level `reasoning`, `input`, `thinkingLevelMap`, and `samplingParams`. Planning sync produced a single-model block without those fields. The planning call did not write the file; applying that plan would perform the replacement.

For your local Qwen configuration, this can erase the information that enables vision, reasoning controls, replay compatibility, and tuned sampling. Defaults are also material: without route limits, the generated block specifies 32,768 context and 4,096 output tokens. These are source defaults, not measurements of your running endpoint.

**Proposed correction:** Define ownership first. Prefer a dedicated Workbench Pi configuration directory, with a documented merge/update policy for any shared configuration. Update only explicitly managed fields; preserve other provider keys, other models, and model-specific metadata. A managed model must be matched by exact ID. An endpoint collision should fail with an actionable conflict, not silently retarget an existing provider.

Do not implement an indiscriminate deep merge: ownership and precedence must be explicit, particularly for API type, auth, headers, compatibility, and conflicting model IDs.

### F2 — Pi sync status ignores context/output drift

**Classification:** Source + probe. **Priority:** Fix alongside F1.

`endpoint_sync_status()` compares endpoint URL and key reference, then checks whether the expected model ID exists. It does not compare token limits or the API type. [R4: lines 95–107]

The probe used an existing model configured for context 106,496/output 32,768 and a route requesting context 65,536/output 4,096. The method returned `synced`. Those values were test fixtures, not observations of your model server.

**Proposed correction:** Compare the normalized set of fields that route sync actually owns. Configuration materialization, credentials being present, and a live model being usable must be reported separately. A structural `synced` result must not be presented as live capability verification.

### F3 — Keyless local endpoints need a Pi-specific auth policy

**Classification:** Source + probe, with an upstream compatibility qualification.

When `apiKeyEnv` is absent, `_block()` omits `apiKey`. Current Pi documentation says custom models without configured auth load but remain unavailable in model selection; keyless servers can use an explicit dummy value or another supported auth source. Thus a generated route without a key is not necessarily invalid, but it needs an explicit keyless policy or an existing Pi auth source. [R4; U1]

**Proposed correction:** Test authenticated endpoints, explicitly keyless local endpoints, and deliberately missing credentials as separate cases. Do not invent a real credential requirement for an intentionally keyless local server; do not silently supply a dummy key to an endpoint that should require authentication. The current `$ENV_VAR` key-reference syntax agrees with current upstream documentation and is not itself a defect. [U1]

### F4 — The current Pi shim is deliberately one-shot

**Classification:** Source + probe. **Priority:** Architectural boundary, not a request to break compatibility.

The adapter constructs:

```text
pi -p --no-session --approve|--no-approve ... <prompt>
```

Its parser reserves `--mode`, `--print`, and related flags. Passing `--mode=rpc` or `--mode=json` through this shim is rejected. The probe confirmed that behavior. [R4: lines 13–67; R5: lines 319–335]

Pitwall still preserves its own logs, results, workspaces, and dispatch identity. What it does not preserve through this command is a Pi-native conversation session. Those are different kinds of durability.

**Proposed correction:** Keep this as the compatibility one-shot path. Interactive Workbench must use Pi's interactive runtime. Native session control should use Pi's supported extension/SDK/RPC interfaces rather than try to smuggle RPC through the old print-mode contract. Current RPC documents steering, follow-ups, queue clearing, abort, and session state; command acceptance is not the same as task completion. [U2, U3]

A new managed Pi transport, should it become necessary, must be explicitly separate from the existing sentinel-based command. It is not part of the first config-fix change.

### F5 — Workflow registration does not imply executable Pi workflows

**Classification:** Source + probe. **Priority:** Required only before putting Pi into the durable workflow runner.

The production scheduler's `_provider_args()` handles `codex`, `claude`, `grok`, `kimi`, and `opencode`. It raises `unsupported workflow provider: pi` for Pi. The helper probe reproduced the error. The workflow documentation already acknowledges this restricted dispatch set. [R8: lines 370–395; R9]

**Proposed correction:** Either reject unsupported providers during workflow validation with a specific explanation, or extend production dispatch through a shared capability-aware adapter interface. Merely adding a model to the registry is insufficient. A generic solution should avoid another growing provider-specific argument switch.

Adding a Pi branch alone also does not prove support for named endpoint routes, provider selection, model configuration, or effort propagation. Each needs an end-to-end fixture.

This limitation must not delay the independent local interactive baseline: that baseline does not need the batch DAG runner.

### F6 — Existing concurrency controls are not request admission

**Classification:** Source and architectural inference.

The workflow scheduler counts active task futures by `route.provider`; here that identifier is the harness (`pi`, `opencode`, etc.). The counter covers the task execution, not individual model requests. It is local to that workflow execution. [R8: lines 844–878]

This cannot, by itself, enforce one inference request against your Qwen endpoint across two Pi sessions, or distinguish two independent hosted accounts behind the same Pi harness.

**Proposed correction:** Keep task concurrency, endpoint inference concurrency, and subscription accounting separate. Define an endpoint/resource-group identity and an account/quota identity. Start with one active request on the local endpoint. Release its permit when the response stream ends or fails; do not hold it while a parent waits on a child. Verify cancellation and final cleanup release it exactly once.

If the pinned runtime cannot expose a reliable request-level integration point without a proxy or upstream change, document that limitation. A worker-lifetime semaphore must not be relabeled as request admission.

### F7 — Existing mailbox steering is not native Pi session steering

**Classification:** Source.

The repository has ASK/ANSWER/STEER semantics and a tier-4 pause/replay path. Pi is not in `CHANNEL_HARNESSES`, the set with built-in MCP channel registration. Tier-4 instructions tell the child to write a mailbox question and pause; resume appends answers to the original prompt and redispatches. [R10: lines 115–140; R11: lines 555–658 and 1263 onward]

That can be useful cross-harness behavior, but it does not provide the same-session interaction you want by itself.

**Proposed correction:** Use native Pi steering for Pi-owned live sessions. Introduce a bounded mailbox-to-Pi bridge only when a Pitwall-owned parent needs it. Do not require every leaf to understand the complete mailbox JSON protocol or poll it through model-generated shell commands.

### F8 — Sync scope is broader than one selected Pi route

**Classification:** Source; integration caution, not automatically a bug.

`plan_sync()` collects all endpoint-bearing routes and passes them to a selected config-sync adapter. Filtering to the Pi harness selects the destination adapter, not just routes explicitly assigned to Pi. That behavior supports harness overrides, but makes accidental metadata replacement broader. [R12: lines 36–73]

**Proposed correction:** The Workbench profile compiler should make its selected routes and destination directory explicit. Do not change existing all-endpoint sync semantics without a migration/compatibility decision.

## 5. Execution ownership: two scopes, one owner per run

The proposed architecture is:

```text
Claude Code / Codex / OpenCode and their current subscriptions
                         |
             existing Pitwall Agent Routing
             named routes / external run records
                         |
               one-shot Pi leaf when requested

User directly in upstream Pi
                         |
               packages/pi-workbench
        curated profiles / UI / context accounting
                         |
             ONE native Pi subagent backend
                         |
          fresh Pi leaves using local or hosted models
```

The first branch is compatibility with your existing cross-harness system. The second is the interactive daily driver. Sharing route/profile identity does not require merging their session managers.

### Concrete default ownership

| Concern | Interactive Pi-owned session | Pitwall-dispatched Pi leaf |
|---|---|---|
| Conversation/session | Pi | One-shot Pi initially; no persisted Pi session is claimed. |
| Child lifecycle | One chosen Pi backend | Nested delegation disabled initially. |
| Parent task/run record | Pi backend/Workbench | Pitwall run store. |
| Workspace ownership | Selected Pi backend for its children | Pitwall worktree manager for the dispatched leaf. |
| Completion evidence | Pi/backend result plus independently run checks | Pitwall receipt plus artifacts and independently run checks. |
| Cancellation | Pi/backend controls, with descendant-termination tests | Pitwall supervisor; Pi leaf must not escape into unmanaged detached children. |
| Route/model identity | Workbench profile referencing Pitwall route identity | Existing route resolver plus validated Pi profile. |

Do not assign both managers the same worker process or worktree. Later observability can link records with parent IDs and runtime-owner metadata, but linked records are not two authorities that independently cancel, retry, apply, or delete the same work.

If native Pi subagent backends fail the required gates, evaluate a **Pitwall-backed managed Pi/RPC backend** as a separate design spike. Reusing Pitwall's supervision and workspace components could then be preferable to adopting a competing manager. Do not build that alternative in parallel with the native backend by default.

## 6. Proposed package and configuration ownership

```text
packages/
  agent-routing/                  existing Python component
    src/pitwall/agents/
      providers/pi.py             targeted corrections
      route_sync.py               change only where ownership contract requires
    tests/
      test_provider_adapters.py   preservation/auth/drift regressions
  gateway/                        unchanged for this first integration
  pi-workbench/                   NEW proposed TypeScript package
    package.json
    src/
      extension.ts
      profiles.ts
      backend.ts
      context-accounting.ts
    agents/
      scout.md
      worker.md
      reviewer.md
    tests/
    examples/
    docs/
      integration-contract.md
      source-lock.md
      measurements.md
```

Do not create every file merely to match the diagram. Start with the smallest runnable package and split modules when implementation justifies it. Keep the new package independently installable; the Python broker's dependency stack must not become a mandatory dependency of local Pi coding.

### Separate the meanings of “provider”

Pitwall Agent Routing's provider registry mostly describes **CLI harnesses**. Pi's provider identifies a **model API/auth integration**. A profile containing only `provider: pi` would not identify the model's API endpoint or subscription account. [R2, R4, R14]

The Workbench boundary should distinguish these concepts explicitly. Proposed semantic fields are:

```text
routeRef                existing named route
harnessId               pi
piProviderId            exact selected Pi provider
modelId                 exact served/provider model
resourceGroupId         inference-capacity identity
quotaAccountRef         subscription/account identity, not a credential
instructionProfile      solo / coordinator / leaf role
```

These are proposed concepts, not additional keys currently accepted by `routes.json`. Its current strict schema must not be silently bypassed.

A practical ownership split is: Pitwall owns route/harness/endpoint identity; the Pi profile owns reasoning/modality/sampling/compatibility metadata; Workbench owns role/tool/context/admission policy. Cross-validation ensures the referenced route and the selected Pi model agree. Credentials remain references in the correct existing store, not copies in several manifests.

Do not infer account identity from an identical model label. Do not treat a route's `seat` label as proof of a subscription quota boundary. [R14]

### Model/provider rollout

Begin with the existing local Qwen endpoint. Confirm its actual model ID, served context, reasoning format, image capability, and tool continuity; the archive is not evidence of the currently running server.

Then add one hosted Pi profile at a time: MiniMax, GLM/Z.AI, and the correct Alibaba plan endpoint/account. Prefer supported Pi integrations where validated. Do not automatically flatten them all into the generic `openai-completions` block generated for local endpoints, and do not carry over OpenCode provider names as though they were Pi provider IDs.

OpenCode Go routes stay untouched. Existing Claude and Codex routes stay untouched. No implicit PAYG fallback or cross-account substitution is authorized by this plan.

## 7. Keep the model interface smaller than the user interface

Retain the earlier three profiles:

**Solo:** Pi coding tools and concise applicable instructions, with no delegation catalogue.

**Coordinator:** A rich user-facing agent panel and a small model-facing delegation interface. Initial proposed tools remain `agent_task` and `agent_control`; their exact schemas are implementation decisions, not current Pi APIs.

**Leaf:** A fresh, bounded scout/worker/reviewer task with no parent transcript, no routing catalogue, and no nested delegation. A deterministic validator remains a process, not automatically another agent.

Read-only is a capability restriction, not an agent name. A role with unrestricted shell access must not be represented as technically read-only.

Count the serialized request. Include system text, active tool schemas, instructions, skill catalogue, retained history/reasoning, tool output, and returned child results. Report the delta from equivalent stock Pi separately from the total request. A directory full of optional reference files is not automatically prompt overhead; only actual injection/loading establishes that cost.

Keep logs and transcripts accessible to the UI without automatically putting them into the coordinator's conversation. Human inspection and model-context consumption are separate operations. Compare tokens and time per accepted task, not just the first-turn prompt size.

Existing Pitwall tier-4 channel instructions are also context overhead when enabled. Measure them rather than assuming that the thin shell wrapper implies zero instruction overhead. [R11]

## 8. Implementation order and acceptance gates

### Change 1 — Protect the existing Pi configuration path

Scope: the Pi adapter and its direct regression tests; any shared sync edits must be narrowly justified.

Required fixtures cover preservation of provider extras, preservation/upsert of model metadata, preservation of other models, context/output drift, API/endpoint conflicts, idempotence, environment-variable key references, and an explicit keyless-auth strategy. Keep sync plan/diff/backup behavior. Use a temporary home and dedicated Pi directory; do not apply against the real global configuration.

Re-run the existing adapter/route/sync tests and generated-source gates under the component's Python 3.14 environment. Existing tests currently assert the minimal replacement block, so new preservation tests must deliberately extend that contract rather than merely re-run it. [R5, R12, R15]

### Change 2 — Establish an isolated interactive local baseline

Create the minimum independently installable Workbench package and source/version lock. Run stock Pi first, without a subagent extension or a new system prompt. Preserve the tuned model server and its launch configuration.

Validate the exact outgoing model request, sequential tool calls, an edit/test/repair task, reasoning control, compaction, stop behavior, and vision when actually supported. Capture request/accounting measurements and clearly label all unexecuted live gates.

Success means the local model works in the intended interactive configuration. It does not require Pitwall workflows, the gateway, or the broker.

### Change 3 — Add one fresh Pi-native child

Evaluate one native backend in a separate profile. Start from the earlier candidate order, but choose based on this repository's ownership requirements rather than the package's feature count.

Demonstrate a coordinator launching one fresh scout or worker, the user inspecting it, delivering a steering message, cancelling it, and collecting a bounded result. Test that the parent and child can share a one-slot endpoint without deadlock. Establish whether cancellation stops provider activity and owned command descendants, not merely the UI state.

Prove which manager owns the workspace. Review the worker's actual patch. Do not permit automatic commits merely because a backend makes them by default.

### Change 4 — Round-trip through existing Pitwall dispatch

Run one Pitwall-owned Pi leaf with the validated profile, no nested delegation, and the existing sentinel/receipt contract. Show that its run records and explicit worktree integration remain intact.

Preserve the reference parser's contract for `SHIM-RESULT`/`SHIM-DONE`. A completion receipt proves transport completion, not correctness. Re-run approved checks against the actual changed revision.

Clarify lineage and cancellation scope before allowing an externally dispatched Pi coordinator to spawn its own fleet. That is not needed for the first useful implementation.

### Change 5 — Add hosted profiles and resource accounting

Add provider/account-specific fixtures and one authorized live smoke test per configured account. Keep provider/model identity exact. Add request-level admission for each shared inference endpoint and separately account for hosted quota.

Do not enable automatic fallback. Do not describe a reconciled local usage estimate as a guaranteed provider-side spending cap.

### Change 6 — Optional workflow/RPC integration

Only after the daily-driver path works, decide whether Pi should run in Pitwall's durable workflow scheduler and whether that should remain one-shot or use a new managed RPC transport.

Resolve the unsupported-provider path and named-route propagation before advertising workflow support. Introduce capability validation so registration and execution support cannot be confused.

A managed RPC transport needs its own framing, request IDs, event ordering, lifecycle and cancellation tests. Preserve the old shell interface instead of changing its stdout into an incompatible JSON event stream.

## 9. Specific tests for clean subagent management

| Test | Expected evidence |
|---|---|
| Fresh leaf context | Captured request contains no inherited parent transcript or coordinator-only tools. |
| Same-endpoint parent/child | Both progress with one inference slot; parent waiting does not retain the slot. |
| Live steer | Directive reaches the existing Pi session at its documented boundary, not a silently restarted worker. |
| Stop with queued work | Queues/retries are addressed as well as current generation; no unexpected follow-on turn. |
| Stop during a tool | Owned descendants terminate or a clear unresolved state is reported; changed workspace retained. |
| Restart/resume | Native-session resume and Pitwall replay are labeled separately. |
| Writer-to-reviewer handoff | Reviewer receives the actual worker patch and base identity, not an empty clean base. |
| Config regeneration | Unowned provider/model metadata survives; owned drift is detected. |
| Provider affinity | Same-named models on different providers/accounts cannot be substituted. |
| Cross-harness regression | Claude/Codex/OpenCode Go commands, native boundaries, receipts, and route semantics remain unchanged. |
| Context reduction | Measured request delta and accepted-task outcome, not a claim based on README size. |

Current Pi RPC distinguishes `clear_queue` from `abort`; stopping the current operation without handling remaining queued work is not equivalent to stopping all future work. Use the pinned runtime's documented behavior and test it. [U2]

## 10. Copy-ready next Codex assignment

> Work in the existing Pitwall monorepo. Read this integration plan and the earlier Pi architecture plan before editing. This document establishes the existing Pitwall monorepo as the implementation target.
>
> First inspect the actual checkout, local Python/Node/Pi versions, active configuration directories, and existing model endpoint. Preserve all pre-existing changes and global configuration. Use Python 3.14 for `packages/agent-routing` and the repository's own lock/CI guidance. Do not rewrite source syntax to accommodate an older interpreter.
>
> Complete the narrow Pi adapter correctness change first. Examine `src/pitwall/agents/providers/pi.py`, `route_sync.py`, `routes.py`, and `tests/test_provider_adapters.py`. Add failing regressions for enriched provider/model preservation, other-model preservation, limits drift, endpoint/API conflicts, idempotence, auth references, and intentionally keyless endpoints. Define field ownership before implementing merge behavior. Retain existing route-sync compatibility unless a change is explicitly documented and tested.
>
> Do not change the legacy `pi-shim` into an RPC stream. Do not rewrite the broker, gateway, workflow scheduler, or all provider adapters as part of this fix. Do not silently migrate Claude Code, Codex, or OpenCode Go routes.
>
> After those tests pass, create the minimum isolated `packages/pi-workbench` TypeScript package and establish a stock-Pi/local-model baseline. Pin the exact Pi and eventual backend versions and record their source/licensing. Verify the actual server model, context, reasoning format, vision capability, and tool use without changing its tuned launcher. Missing endpoint access means the live gates stay explicitly unexecuted, not simulated as successes.
>
> Only then add one native Pi child under one backend. The interactive backend owns that child's lifecycle and workspace; Pitwall remains authoritative for separately dispatched external runs. No nested delegation in Pitwall-launched Pi leaves initially. Do not put the new interactive path behind the durable workflow runner, which currently cannot dispatch Pi.
>
> Report changed files, executed commands, actual results, mocked/unexecuted gates, request overhead measurements, ownership decisions, and the next smallest useful change. Do not claim complete Claude Code/Codex equivalence, production readiness, or measured savings from a single smoke test.

## 11. Validation performed during this review

The five isolated observations were:

```json
{
  "sync_drift": "synced despite context/output mismatch",
  "planned_sync": {
    "removed_provider_fields": ["compat", "headers"],
    "removed_model_fields": ["input", "reasoning", "samplingParams", "thinkingLevelMap"],
    "models_before": 2,
    "models_after": 1
  },
  "keyless_block": {
    "apiKey_present": false,
    "default_contextWindow": 32768,
    "default_maxTokens": 4096
  },
  "pi_command": "print mode, no-session; RPC/JSON mode overrides rejected",
  "workflow_pi": "unsupported workflow provider: pi"
}
```

The adapter modules were loaded unchanged into an isolated namespace to avoid importing unrelated Python-3.14-only code. The scheduler observation executed its exact `_provider_args` function in isolation. This establishes those narrow behaviors, not full package integration or performance. The normal unittest collection failed in the unavailable required-interpreter environment and was not represented as a passing test run.

## 12. Source map

All R-sources below are from the uploaded archive, not a successful live GitHub checkout. Paths are relative to `pitwall/`. Source lines refer to the archived files.

- **R1:** `README.md`, especially the broker architecture and independent Agent Routing component; `pyproject.toml` for broker version/runtime requirements.
- **R2:** `docs/agents/routing-readme.md`; `pyproject.toml` for component version, independent packaging, dependency list, and runtime requirements.
- **R3:** `docs/agents/architecture.md`, especially product boundary, client ownership, dispatch lifecycle, and prompt exposure.
- **R4:** `src/pitwall/agents/providers/pi.py`, lines 13–71 (print-mode adapter), 76–93 (provider block), 95–107 (sync status), 109–130 (sync plan).
- **R5:** `tests/agents/test_provider_adapters.py`, lines 319–357 (Pi adapter tests).
- **R6:** `docs/agents/run-records.md`; `src/pitwall/agents/run_store.py`.
- **R7:** `docs/agents/worktree-dispatch.md`; `src/pitwall/agents/workspace.py`.
- **R8:** `src/pitwall/agents/scheduler.py`, lines 210–304 (production execution), 370–395 (supported dispatch cases), 844–878 (task/provider concurrency).
- **R9:** `docs/agents/workflows.md`, especially supported production transports, explicit context handoff, verification, and resume semantics.
- **R10:** `src/pitwall/agents/capability_inventory.py`, lines 95–140; `docs/orchestrator-channel.md`.
- **R11:** `src/pitwall/agents/dispatch.py`, lines 555–658 and 1263 onward; `src/pitwall/agents/process.py` for process-group handling.
- **R12:** `src/pitwall/agents/route_sync.py`, lines 36–73 (scope), 92–146 (apply/backup mechanism).
- **R13:** `packages/gateway/README.md`; `packages/gateway/package.json`.
- **R14:** `docs/agents/routes.md`; `src/pitwall/agents/routes.py`, especially `ResolvedRoute`, provider selection, effort propagation, and endpoint materialization; canonical `resources/config/provider-registry.json`.
- **R15:** `.github/workflows/agent-routing-ci.yml` for the declared Python environment and component test/static/generated-source gates.

Official upstream Pi references checked on 19 September 2026; these are development-branch documents, not proof of the installed version:

**U1 — Custom model configuration, auth, and metadata**

```text
https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/models.md
```

**U2 — RPC commands and session control**

```text
https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/rpc.md
```

**U3 — Extension and UI integration**

```text
https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/extensions.md
```

## Final direction

Build **the Pi interactive experience that Pitwall does not yet provide**, rather than another copy of the cross-harness infrastructure it already has. Correct the existing Pi configuration path first. Keep external dispatch and native interactive subagents separately owned, connected by explicit route/profile and result contracts. Add more orchestration only after a measured, useful local baseline exists.
