# Pi Workbench — Architecture and Implementation Plan

**Prepared for the maintainer • 19 September 2026 • America/New_York**  
**Version:** 2.0 — research-backed design, not an implemented or benchmarked system  
**Working name:** `pi-workbench` is a proposed name for your integration package, not an existing product.

## 1. Decision

Build on **upstream Pi**, add **one existing subagent backend**, and keep your own code focused on **model profiles, compact delegation, resource admission, context accounting, and task handoff**.

My provisional first backend is **nicobailon/pi-subagents**, with **tintinweb/pi-subagents** as the strongest interactive-UX challenger. Select the winner using measured request overhead and task outcomes, not screenshots, advertised features, or repository popularity.

Do not fork Pi, write another agent loop, or build a universal subscription proxy first. The valuable work is making a familiar coding experience reliable and economical across your models—not reimplementing a terminal, provider SDK, session store, or tool executor.

Your previous document is principally a **local Qwen implementation-worker integration**. This revision expands that into an **interactive daily-driver harness that can also act as an externally delegated worker**. It retains its bounded tasks, compact instructions, isolated changes, and validation loop. [A01]

### Keep the four execution lanes separate

| Lane | Execution harness | Scope of this project |
|---|---|---|
| Claude models used through Claude Code | Claude Code | Leave unchanged. Optional future task/result interoperability only. |
| Codex models used through Codex | Codex | Leave unchanged. Codex will build this project, not become a Pi provider for this design. |
| OpenCode Go subscription | OpenCode / your OpenCode shim | Leave unchanged. Do not migrate its credentials or subscription traffic into Pi. |
| Local Qwen, other local models, MiniMax, GLM, Alibaba Token Plan | Pi Workbench | The interactive and worker environment being designed here. |

A shared task envelope is useful across these lanes. A shared model transport is not required. Existing Sol/Fable orchestration can eventually dispatch the same bounded envelope without changing which harness owns each subscription.

## 2. Evidence and confidence

Three categories are used throughout:

**Existing-plan evidence:** what your uploaded `qwen3.8_pi_harness_research(1).md` actually proposes. It is useful design input, not proof that its configurations have been run successfully. [A01]

**Verified documentation:** current public primary documentation and source inspected on 19 September 2026. Pi's latest release page resolved to **v0.85.1**, released **5 September 2026**. Several extension documents describe their development branches; availability in a specific published package remains to be verified. [S01]

**Proposed design:** the profiles, wrapper schemas, enforcement requirements, budgets, acceptance gates, command names, and repository layout below. These are recommendations for your implementation, not claims that Pi already exposes identically named settings.

No live request was sent to your local model or hosted accounts. No candidate was installed or benchmarked on your hardware. The selected backend is provisional until its pinned build passes the acceptance gates.

## 3. Audit of the original plan

| Original proposal | Decision in this revision | Reason |
|---|---|---|
| Keep Pi's stock prompt and compact repository guidance | Retain | Establish a clean baseline before changing prompts or tools. |
| Prefer fresh bounded Qwen implementation assignments | Retain and generalize | Make fresh leaf contexts the normal path for every provider. |
| Preserve the tuned Qwen server, vision, MTP, GPU arrangement | Retain | Harness work should not silently replace an already tuned inference configuration. |
| Install `pi-llama` first | Make optional | Pi now documents native llama.cpp **router** support. This does not mean your fixed single-model server should be converted to router mode. [S04] |
| Direct Qwen `models.json` after baseline testing | Retain as a candidate | A fixed, explicitly validated provider profile remains appropriate for a dedicated server. |
| Map Qwen effort exclusively inside template kwargs | Treat as unverified | Qwen's official Chat Completions example places `reasoning_effort` at the top level, with thinking switches in `chat_template_kwargs`. Your server's accepted mapping must be tested. [S09] |
| Use 32K output reserve and 20K retained context globally | Replace with per-profile budgets | A smaller local model may not have enough served context for these values. |
| Set one-hour provider/HTTP timeouts everywhere | Split into separate controls | Request inactivity, total generation time, tool time, and task deadlines are different failure modes. |
| Assume one local worker because of MTP | Use one active local generation as an initial policy | Measure actual endpoint capacity. Schedule inference requests rather than holding a lock for a whole agent's lifetime. |
| Launch with `--approve` | Clarify meaning | Project trust approval is not an OS sandbox or a generic permission enforcement layer. [S02] |
| One worktree per writer | Retain with explicit handoff rules | A fresh worktree does not automatically contain the parent's uncommitted changes. |

**Qwen transport gate.** Keep the existing server configuration until requests establish exact model ID, effective context, image handling, tool-call continuity, reasoning control, and reasoning replay. The official card supports `low`, `medium`, and `xhigh`, and warns that lower effort can increase total task latency through retries. Do not make “lowest reasoning” synonymous with “most efficient.” [S09]

**Native llama.cpp qualification.** The documented built-in integration manages a router that can load and unload models. Your dedicated server can remain a direct endpoint. HF's `pi-llama` is an optional compatibility comparison, not an obligatory extra extension. Its URL conventions and Pi's native router URL conventions must not be assumed identical. [S04, S08]

## 4. What to reuse

### 4.1 Upstream Pi is the foundation

Pi already supplies the terminal editor, tool loop, model selection, sessions, branching, compaction, steering/follow-up interactions, and local SDK/RPC surfaces. Its default coding tools are `read`, `bash`, `edit`, and `write`. Subagents and several richer workflows are extension territory. Reuse these foundations rather than recreating them. [S02, S06, S07]

The release notes distinguish supported local SDK/stdio RPC from experimental server/client interfaces. This project should not depend on those experimental interfaces. [S01]

### 4.2 Candidate comparison

| Candidate | What makes it relevant | Fit for this project | Selection condition |
|---|---|---|---|
| **nicobailon/pi-subagents**; package name `pi-subagents` | Existing worker execution, fleet controls, artifacts, workflows, worktrees, and documented extension integration seams | First backend to test for a thin custom front end | Fresh isolated leaves, small active prompt surface, reliable cancellation, and exact provider policy must survive the pinned integration. [S10–S16] |
| **tintinweb/pi-subagents**; package name `@tintinweb/pi-subagents` | Interactive agent handles, fleet management, background execution, and Claude-style agent definitions | Strong contender when the quickest polished interactive experience matters most | Audit its model fallback, scheduling/concurrency boundaries, and automatic worktree preservation commits before adoption. [S17] |
| **Pi's example subagent extension** | Small reference implementation | Excellent API-learning and debugging reference | Use as a fallback only when a narrowly documented gap makes the larger backends unsuitable; do not casually rebuild their lifecycle machinery. [S25] |

**Do not install both competing subagent packages in the same evaluation profile.** Their similarly named concepts are not compatible schemas, and double registration can make debugging and context measurements misleading.

### 4.3 Why the first backend is not an unconditional winner

The nicobailon documentation exposes public integration contracts that make a compact wrapper plausible. However, its stock behavior is not automatically your desired behavior. Worker context can fork; inline output is uncapped unless configured; its usage budget reconciles reported usage rather than reserving concurrent spend. These are configuration and integration concerns, not reasons to discard the backend. [S11]

Background children can inherit ambient extensions. Define explicit child tool and extension sets instead. A role called “reviewer” must also be checked for edit authority; its name is not a guarantee of read-only behavior. [S13]

For TINTIN, the documented worktree path automatically preserves modifications in commits, and its workflow/scheduling mechanisms have their own concurrency behavior. That conflicts with an unqualified “workers never commit” policy unless deliberately adapted. Its model selection also needs an exact-provider audit. None of these findings establishes that it is a poor product; they establish what must be tested for your use case. [S17]

### 4.4 Borrow behavior, not entire prompts

| Source | Useful pattern | Do not import indiscriminately |
|---|---|---|
| Codex | Explicit agent lifecycle, compact results, role specialization, inherited policy | Codex subscription routing or a new copy of its entire runtime. [S19] |
| Claude Code documentation | Familiar agent manifests, independent context, foreground/background interaction, scoped roles | An assumption that its product implementation is an open-source donor. Treat it as a UX reference. [S20] |
| OpenCode | Distinguish primary agents from subagents; restrict launchable roles in the actual tool surface | The whole instruction/tool payload you are trying to avoid. [S21] |
| Aider | Bounded repository-map assistance | A permanent whole-repository dump in every worker request. [S22] |

Every copied component needs its exact source revision, license, notices, modifications, and upgrade tests recorded. Availability on GitHub is not a substitute for a license review. Prefer an upstream dependency or documented API over vendoring when that keeps the integration smaller.

## 5. Proposed architecture

```text
User in Pi TUI                         Existing parent/orchestrator, later
        |                                      |
        +-------------- bounded task ----------+
                               |
                    Pi Workbench package
                 profiles / policy / telemetry
                               |
             +-----------------+------------------+
             |                                    |
       solo session                     coordinator session
       Pi core tools                    core + compact delegation
                                                  |
                                      one subagent backend
                                                  |
                               fresh scoped leaf sessions
                                scout / worker / reviewer
                                                  |
                         validated provider/model profiles
                 local Qwen | MiniMax | Z.AI | Alibaba plan
                                                  |
                        endpoint/account resource admission
```

The thin custom package should own decisions that are specifically yours. The backend should continue to own its sessions, process protocol, worker records, and supported lifecycle operations.

Do not introduce a database server, Kubernetes, Temporal, a generic multi-agent framework, or an extra network API merely to launch a few local workers. Start with backend-owned state plus small append-only receipts. Add a cross-process scheduler only when detached workers or multiple simultaneous Pi sessions make a shared admission boundary necessary.

### Package responsibilities

| Layer | Own responsibility | Explicitly not its responsibility |
|---|---|---|
| Pi core | Conversation loop, provider transport, builtin tools, session/UI primitives | Your deployment's quota policy or preferred role mix |
| Subagent backend | Worker lifecycle, ownership, artifacts, supported control operations | Deciding which account you intend to bill |
| Workbench | Role/profile allowlists, compact tool surface, context accounting, admission policy, normalized reports | Reimplementing Pi's model client or backend internals |
| Model server/provider | Actual inference, transport limits, account-side quota enforcement | Guaranteeing your task was correct |
| Validator/integrator | Independently execute approved checks and integrate reviewed changes | Trusting a model's self-reported success |

## 6. Three operating profiles

The following profiles and names are proposed Workbench constructs, not stock Pi settings.

### `solo`: the clean interactive default

Use stock Pi with only the tools needed for the selected local model. Start with `read`, `bash`, `edit`, and `write`, concise repository instructions, and no subagent roster or delegation schema. This establishes how the model behaves without the additional orchestration layer.

A user-side Workbench command can switch to the coordinator profile between turns. Avoid rebuilding the tool catalogue every few calls merely to save a small number of tokens; measure the impact on cache reuse and transcript compatibility first.

### `coordinator`: the familiar managed-agent experience

Use the same terminal, plus a deliberately compact delegation surface. The goal is to launch a task, see the agent working, inspect it, steer it, stop it, and consume its result without turning every status refresh into another model request.

Initially use a curated native backend tool to establish a functional baseline. Add the two-tool wrapper only after measuring that baseline. Suggested public tools:

```text
agent_task(role, task, acceptance, scope?, inputs?)
agent_control(action, agent_id?, message?)
```

`role` is a short allowlisted identifier. The model does not select arbitrary endpoints, credentials, raw provider IDs, shell commands for setup, or unrestricted workflow scripts. `agent_control` begins with a narrow action set: `list`, `inspect`, `steer`, and `cancel`. Add `resume` only after ownership and crash recovery tests pass.

Waiting and completion should be event-driven. An explicit status request returns a bounded summary; routine UI repainting consumes no LLM call. Progress text is not automatically appended to the parent conversation.

### `leaf`: a fresh, economical worker

Start a new context with the task envelope, applicable repository instructions, minimal role guidance, and only its required tools. It receives neither the entire parent transcript nor the fleet's management catalogue.

| Initial role | Default purpose | Proposed tools | Delegation |
|---|---|---|---|
| `scout` | Locate implementation, interfaces, and relevant tests | `read`, `find`, `grep`, `ls` | Disabled |
| `worker` | Implement a cohesive, bounded change | `read`, `edit`, `write`, `bash` | Disabled |
| `reviewer` | Review supplied changes against criteria | `read`, `find`, `grep`, `ls` | Disabled |

A deterministic test process is not automatically another model-powered agent. Let the worker use the existing test runner, and independently rerun approved acceptance checks when warranted. A reviewer needing a test can request host-run validation or a separately scoped execution tool.

**Tool names are capability hints, not a security boundary.** A worker with unrestricted `bash` can run Git or write outside its assigned directory. A “read-only” role must not receive unrestricted shell access and then be called read-only by policy alone.

Start with these three roles. Add a separate planner, researcher, UI specialist, or test author only when tasks demonstrate that the extra handoff improves accepted results. A small local model can be an excellent bounded leaf even when it is an unreliable general coordinator.

### Familiar-workflow coverage

This is the proposed product checklist, separating reuse from work still required:

| Experience | Implementation approach |
|---|---|
| Conversational coding, file references, model selection, session continuation | Reuse Pi; validate the selected release's behavior rather than create a second terminal. |
| Plan before changing code | Add a user-selectable read-only planning policy with equally restricted child roles. A prompt saying “do not edit” alone is insufficient. |
| Foreground and background agents | Reuse the selected backend; normalize identity, completion notifications, and controls. |
| Inspect, steer, stop, resume | Prefer existing fleet controls; add only missing ownership/cancellation checks. |
| Review the actual diff | Open the owned workspace's patch; show validation beside it; never imply automatic acceptance. |
| Tests and long-running commands | Retain command identity, complete output, progress, and cancellation. Background **shell processes** need their own lifecycle; background **subagents** do not automatically solve this. |
| Approvals and restrictions | Make trusted versus restricted execution explicit, with tested enforcement for the latter. |
| Skills, MCP tools, language diagnostics | Add selectively and on demand after the basic workflow passes. Do not make every integration visible to every leaf. |
| Editor/other-client integration | Use supported local interfaces later when a concrete client requires them. Do not make another protocol adapter a prerequisite for useful terminal coding. |

### Compact worker instruction to start from

Append a short role to Pi's stock instructions rather than replace the entire system prompt. This is a proposed role, with task-specific details supplied separately:

```text
Complete the assigned task in the provided workspace. Read the relevant
implementation and tests before editing. Make the smallest coherent change
that satisfies the acceptance criteria and preserves unrelated behavior.
Run the approved relevant checks, diagnose failures, and repair your changes.
Do not commit, push, reset, switch branches, or modify unrelated files.
Do not spawn agents. If blocked, report the concrete blocker and evidence.
Stop when the criteria are met. Return a concise summary, changed files,
verification evidence, assumptions, and remaining risks; keep long logs in
artifacts rather than repeating them in the report.
```

This instruction expresses intent. The host's routing, tool, workspace, and execution controls still enforce the guarantees described later.

## 7. Context engineering: make low overhead measurable

A small system prompt is only one component of the request. Account for:

```text
input = system instructions
      + active tool schemas and tool instructions
      + loaded project/global instructions
      + skill catalogue and loaded skill bodies
      + task, conversation, retained reasoning, tool results
      + provider-specific framing
```

Measure actual serialized requests as close to the provider boundary as supported. Use the model's tokenizer where available; otherwise label estimates and keep provider-reported token usage separate. Do not equate JSON bytes with tokens or report missing usage as zero.

### Proposed acceptance targets, not measured results

| Surface | Initial target | Enforcement strategy |
|---|---|---|
| Workbench-added leaf prefix | Preferably no more than roughly 500–800 tokens above equivalent stock Pi | Snapshot the same task/model/tools with and without Workbench. Exclude task-specific project instructions from the delta, but still report their total. |
| Workbench-added coordinator prefix | Initially aim below roughly 1,500–2,000 tokens | Count the complete compact tool schemas and all injected instructions, not only their descriptions. |
| Routine child completion visible to parent | Roughly 300–800 tokens | Result includes decisions, changed files, verification, blockers, and artifact references; full logs remain outside context. |
| Large tool output | Bounded view, durable complete artifact | Preserve failure evidence, relevant head/tail sections, and an explicit truncation marker. |
| Inherited parent conversation | None by default | Deliberate bounded handoff; transcript fork is an explicit exception. |

These are engineering budgets to test, not claims about current Pi, OpenCode, or your models. A slightly larger prompt that prevents repeated failed edits may be better than the smallest prompt. Optimize **tokens and wall time per accepted task**, not the shortest first request.

### Instruction discovery needs its own audit

Pi can load multiple applicable instruction files. Record exactly which ones are loaded in each role and their contribution. Do not silently discard important repository constraints merely to hit a token target. Separate concise universal constraints from optional task-specific guides and load the latter when relevant. [S02]

Freeze the leaf's active tools for a task where possible. Pi supports controlling active tools; its extension system is the seam to inspect rather than injecting a second shadow tool catalogue. [S03]

### Tool results and reporting

Full compiler logs, lengthy search results, and child transcripts should be stored as artifacts with bounded views. Truncation must not make a failed test look successful. Retain the command, exit code, relevant diagnostic, and complete-output path together.

Human-facing transcript inspection should not contaminate the parent's context. A coordinator can ask for a particular excerpt, but the normal completion notification should not replay the child conversation.

### Context admission and compaction

Use an endpoint-specific input ceiling:

```text
allowed input = served context
              - permitted completion reserve
              - tool/continuation safety reserve
```

For illustration only: if the server actually reports **106,496** tokens, and the selected profile reserves **32,768** for completion and **4,096** for continuation safety, the proposed input ceiling is **69,632**. This is arithmetic, not a measured server value or Pi default. Verify how the endpoint counts reasoning against completion and total context.

Do not use a fixed 20K recent-history setting on a 16K or 32K model. Derive profile-specific compaction thresholds and validate them. Preserve task, criteria, changed files, validation state, and the next action through compaction. Keep valid tool-call/result pairings and the reasoning fields required by that provider; do not trim protocol-bearing messages indiscriminately.

## 8. Provider and model profiles

Start with Pi's existing providers. Build custom transport only for a demonstrated compatibility gap.

Pi's v0.85.1 provider documentation includes `minimax`, `minimax-cn`, `zai`, `zai-coding-cn`, and Qwen Token Plan variants including `qwen-token-plan-individual`. The individual international variant narrows the advertised model catalogue; it is not a reason to assume every model is enabled on your account. [S05]

| Your resource | Starting route | Verification before enabling |
|---|---|---|
| Dedicated local Qwen server | Explicit local provider profile; test native compatibility separately | Served model ID/context, tools, reasoning payload/replay, vision, cancellation, usage reporting |
| Other local models | Separate profiles, even on the same endpoint | Tool reliability, exact chat template, context, output limits, edit strategy, practical task size |
| MiniMax subscription | Native MiniMax provider with the appropriate subscription credential | Region, subscription vs PAYG key, exact model entitlement, usage behavior |
| GLM via Z.AI plan | Native Z.AI coding-compatible route | Correct coding-plan endpoint, region, key, exact model entitlement |
| Alibaba personal Token Plan | Prefer the documented individual provider when it matches the actual account | Personal/team distinction, region, plan key, current permitted model IDs, quota semantics |

MiniMax publishes a Pi-specific subscription configuration guide. Z.AI's coding-plan documentation explicitly includes Pi. These make native integration preferable to inventing another compatibility gateway. [S23, S24]

Alibaba's Token Plan and older Coding Plan should not be treated as interchangeable names. Resolve the actual product and account entitlement rather than inferring it from a remembered package description. [S26]

### Proposed profile contract

This is a design sketch for **our configuration**, not a `models.json` file to paste into Pi:

```json
{
  "schemaVersion": 1,
  "profiles": {
    "local-coder": {
      "provider": "local-qwen",
      "modelId": "SET_FROM_SERVER_DISCOVERY",
      "servedContextTokens": null,
      "maxCompletionTokens": 32768,
      "reasoningPreset": "xhigh",
      "resourceGroup": "local-primary-inference",
      "allowedRoles": ["worker", "reviewer"],
      "allowProviderFallback": false,
      "validated": false
    }
  }
}
```

`local-qwen` is a proposed registration name. Launch must fail until required discovery fields are populated and that exact profile passes validation. A profile compiler maps these policy values into the pinned Pi/backend APIs; it must not pass invented fields through to providers.

Resolve every alias to an exact **provider + model ID + endpoint + credential class**. Never use fuzzy model matching across paid accounts. Model-family identity alone is not routing identity: the same family served locally, by Alibaba, and by its own vendor is three different resource/billing paths.

Set provider-native reasoning controls in each profile using the pinned transport documentation. [S29] Preserve protocol-required reasoning within a task; pass task summaries, not opaque provider-specific reasoning objects, between different models.

### Quota accounting

Record input, output, cache, reasoning, total usage, latency, and whether each value is reported or estimated. Track subscription allowances separately from dollar estimates. A zero marginal-dollar estimate does not mean a task consumes no subscription budget.

Disable automatic routing to PAYG credentials. Also check provider-side purchased-credit or spillover settings: prohibiting a harness fallback does not prevent a provider from charging according to the account's existing plan rules. MiniMax's plan documentation is one example where account-side credit behavior matters. [S27]

Do not copy all account credentials into every worker environment. Give an owned child only the credentials its resolved profile needs, subject to the actual process/provider integration.

## 9. Clean subagent management

### User experience target

A worker should be as easy to manage as a familiar foreground/background coding session: launch, visible identity, state, elapsed time, current activity, inspect, steer, stop, and retrieve results. A compact fleet pane should show enough information to distinguish “thinking,” “running tests,” “queued for inference,” and “needs user input.”

Use the backend's fleet UI before building another dashboard. Proposed additional Workbench commands can live under a single `/workbench` namespace: `profile`, `doctor`, `agents`, and `usage`. These are proposed commands; do not assume they are already available or overwrite backend command names.

### Identity and normalized state

Persist stable `taskId`, `runId`, `attemptId`, parent/session identity, effective profile, workspace, and base revision. Separate execution state from acceptance state:

```text
Execution: queued -> running -> succeeded | failed | cancelled | interrupted
                                    
                           waiting/input-needed are observable substates

Acceptance: unchecked -> checks-passed -> reviewed -> accepted
                         |                |
                         +------ rejected +
```

The exact mapping to backend states is adapter code. Never infer successful execution solely from process exit code or final prose. Never conflate “model finished” with “tests passed” or “ready to integrate.”

### Steer, cancel, resume

Steering must return whether the message was delivered or queued. A stop must cancel pending Workbench retries/launches, clear owned follow-up queues where necessary, cancel the provider stream, and stop owned tool subprocesses. Record whether termination is confirmed; uncertainty remains visible.

Pi's RPC documentation distinguishes clearing the message queue from aborting active work. Test both, not just a button changing its label. For custom RPC clients, follow its JSONL framing exactly. [S06]

Prefer graceful checkpoint-and-stop at a tool boundary for writing workers. A forced cancellation can interrupt a mutation. Preserve the workspace and mark it for inspection rather than declaring it safely rolled back.

Resume must target the original owned task and workspace, revalidate its revision and process ownership, and distinguish a resumed attempt from a new retry. A child crash must not lead to two competing processes editing the same workspace.

### Small models need bounded orchestration

Start with a single delegation depth and disable child spawning in all leaves. For weak coordinators, begin with a sequential scout → worker → review recipe controlled by the user/host, not unrestricted recursive decomposition. A budget should stop launch storms before they become inference traffic.

Parallelism is valuable for independent exploration and independent hosted workers. It is not a default requirement for every task. One cohesive implementation loop often beats repeatedly splitting a small edit across multiple models.

## 10. Resource scheduling without local-model deadlocks

The initial local policy is **one active generation against the primary local resource group**. This is deliberately conservative and must be revisited using accepted-task throughput, not a presumed rule about all MTP configurations.

The unit being limited is a **model request**, not an entire agent session. A parent must release its generation permit before running tools or waiting for a child using that same endpoint. Otherwise a parent can hold the only slot while waiting forever for its child to acquire it.

```text
parent inference -> release local permit
parent delegates -> child becomes runnable
child inference -> release local permit
child test tool -> CPU work; another permitted inference may proceed
child report -> bounded completion event
parent inference resumes
```

Group aliases that share an inference server/GPU pool under one scheduler key. Separate hosts may still share a constrained GPU or cloud account; groups must reflect actual bottlenecks rather than model names alone.

### Enforceable scope

A scheduler inside one TUI process does not automatically govern detached children or another Pi terminal. For multiple Workbench processes, use a small shared broker or transactional lease store with owner identities, fencing, and recovery. Do not claim a global limit until a two-process test proves it.

The enforcement seam must cover worker turns, parent turns, retries, and automatic compaction calls. During the implementation spike, verify a supported pre-request/provider wrapper boundary. If the pinned APIs cannot govern one path, expose the limitation and reduce concurrency; do not advertise complete control based only on tool-launch counts.

Other software calling the same server outside Workbench remains outside this policy. Server-side capacity limits are the final boundary.

For hosted accounts, combine an account-group concurrency limit with conservative in-flight budget reservations. Reconcile reported usage after completion. Unknown usage remains unknown. Even reservations cannot create a stronger billing guarantee than the provider's accounting and cancellation semantics.

Avoid double retry amplification. Decide whether the provider client, Pi, or the task layer owns each retry class, and test the combined maximum number of requests.

## 11. Workspace, validation, and integration contracts

### Writers

One writable worktree per independently running writer. Record a stable named base ref and its resolved commit. Give the child its worktree as the logical repository root; do not include instructions that direct it back to the main checkout's absolute path.

The nicobailon managed-worktree documentation requires a clean base for its workflow, captures handoff artifacts, and describes restrictions on accepted base refs. Validate these against the pinned version. A raw commit ID should not be passed into an API that only accepts `HEAD` or a named ref. [S14]

Never silently stash, commit, reset, or discard the user's pre-existing work to satisfy a backend requirement. When the source checkout is dirty, either refuse parallel writer launch with a precise explanation or use an explicit, reviewed snapshot-transfer procedure. Preserve staged/unstaged distinctions where the workflow depends on them.

### Reviewers

A clean worktree at the base commit does not contain the worker's patch. Supply the correct patch/snapshot to the reviewer and identify the exact change being reviewed. Reviewing an empty diff and returning “no issues” must fail the test suite.

Parallel readers may share a stable read-only snapshot. A reader observing a live writer's changing checkout should not be treated as a reproducible independent review.

### Integration

After completion, capture changed paths, diff, base commit, validation evidence, and a content hash/manifest for the handoff artifacts. Serialize integration into the target branch. Detect overlapping changes and rerun relevant checks after applying a patch: isolated worktrees do not eliminate conflicts or cross-task regressions.

Do not auto-merge or auto-push. Workers should not commit in the initial workflow. Any backend that automatically commits for preservation needs a deliberate adapter change or a separately approved policy; it is not equivalent to compliance with the no-commit requirement.

Retain failed/cancelled worktrees until artifacts are captured and owned processes are confirmed terminated. A cleanup timeout is not proof that a writer is gone.

## 12. Task and result contracts

These are proposed Workbench contracts. Use a versioned validator and compile them to backend calls; do not teach a small model every backend option.

### Task envelope

```json
{
  "schemaVersion": 1,
  "taskId": "task-0001",
  "role": "worker",
  "profile": "local-coder",
  "task": "Make duplicate webhook delivery idempotent.",
  "scope": ["src/webhooks", "tests/webhooks"],
  "acceptance": [
    "A repeated event ID does not repeat side effects.",
    "Distinct events retain existing behavior.",
    "Targeted regression tests pass."
  ],
  "constraints": [
    "Do not change the public route contract.",
    "Do not add dependencies.",
    "Do not commit, push, reset, or edit unrelated files."
  ],
  "inputs": [],
  "validationProfile": "webhook-tests",
  "workspacePolicy": "isolated-writer",
  "contextPolicy": "fresh"
}
```

The task deliberately gives acceptance criteria, not an entire repository dump. Keep architecture-level decisions with the parent when needed; let the worker own inspect → edit → test → diagnose → repair → report. This preserves the useful delegation boundary from the original plan. [A01, lines 397–438]

Validation profile names resolve to host-approved commands and working directories. A child-supplied arbitrary shell command is evidence to inspect, not automatically an authoritative acceptance gate.

### Normalized result

```json
{
  "schemaVersion": 1,
  "taskId": "task-0001",
  "runId": "run-0001",
  "executionState": "succeeded",
  "acceptanceState": "checks-passed",
  "summary": "Duplicate event processing is covered by an idempotency check.",
  "changedFiles": ["src/webhooks/handler.ts", "tests/webhooks/handler.test.ts"],
  "verification": [
    {"checkId": "webhook-tests", "exitCode": 0, "evidencePath": "artifacts/check-1.log"}
  ],
  "artifacts": {
    "manifest": "artifacts/handoff.json",
    "diff": "artifacts/change.patch"
  },
  "blockers": [],
  "risks": [],
  "usage": {"source": "unavailable", "inputTokens": null, "outputTokens": null}
}
```

This is an illustrative shape, not an actual completed task. A schema-conforming result is not proof its claims are true. Host validation binds results to the correct workspace and revision.

## 13. Backend integration spike

Before implementing policy broadly, prove a narrow vertical path with the chosen pinned backend.

The nicobailon public extension API documents a versioned event-based RPC surface and a separate foreground delegation contract. Its detached `spawn` interface is async-only. The adapter must use the supported path appropriate to its interaction model, not recursively invoke the model-facing subagent tool from a tool-call hook. [S15]

Use a readiness/capability handshake. Check that the installed backend supports the exact lifecycle methods and launch-policy seams required. Fail clearly on version incompatibility. Do not reach into undocumented session internals to make a demo work.

Pi packages installed alongside one another are not automatically importable Node dependencies from your package. Use the documented event contract, or declare and resolve an actual supported dependency for public imports. Keep one canonical runtime copy of the backend and policy state.

### Spike acceptance gates

| Gate | Required demonstration |
|---|---|
| Compact wrapper | Launch a real child while the broad original model-facing delegation tool is inactive. Confirm backend functionality and UI still work. |
| No hidden prompt inflation | Inspect actual request messages to prove inactive tools did not leave their long instructions/catalogues injected. |
| Effective leaf contract | Show resolved provider, model, tools, extensions, context mode, and workspace before launch. |
| Child isolation | Parent-only extensions and roster do not appear in a leaf. Required policy/telemetry components still do. |
| Lifecycle | Launch → steer → complete; launch → cancel; crash → restore ownership; unsupported resume fails clearly. |
| Worktree handoff | Writer changes survive cancellation and are presented to a reviewer at the correct revision. |
| Resource admission | Parent and child on the same one-slot endpoint complete without deadlock; a second TUI cannot bypass the claimed scope. |

Documented capability ceilings and preflight mechanisms are promising integration seams, not an OS sandbox. Test that restrictions reach detached/resumed workers as well as foreground children. [S15, S16]

### Configuration hardening checklist

For the chosen backend, explicitly resolve fresh context, leaf extension allowlists, role tool allowlists, nested delegation, output caps, model matching/fallback, active-run/spawn caps, and schedule/workflow behavior. Disable missions, schedules, automatic memory injection, and other unneeded automation in the initial profile.

Do not copy settings between the two packages. Also distinguish backend configuration files from Pi settings and our proposed Workbench profile file. Build schema validation that rejects misspelled/ignored options rather than silently assuming they worked. Consult the pinned configuration and model docs, including how reasoning level is passed on dispatch. [S12, S28]

## 14. Practical execution controls

A coding harness must protect work and credentials from accidental action as well as obey role intent. The initial implementation should distinguish:

| Control | What it provides | What it does not provide |
|---|---|---|
| Prompt instructions | Clear requested behavior | Enforced filesystem or network restrictions |
| Active tool allowlist | A smaller model-facing action surface | Safety from arbitrary actions through an allowed shell |
| Worktree | Independent working copy for a task | Separate Git metadata, credential isolation, or a sandbox |
| Host tool/path checks | Enforcement for operations they actually intercept | Coverage of hidden writes by ungoverned subprocesses |
| OS/container sandbox, when enabled | Tested filesystem/process/network restrictions | Automatic correctness of generated code |

Support a convenient trusted mode and a restricted mode without pretending they offer the same guarantees. Restricted mode needs an actually tested execution boundary; role/tool settings alone are not sufficient.

Never log secrets in request captures. Prefer local telemetry and explicit retention. Complete tool artifacts can contain repository source and environment details, so keep them under task ownership and exclude them from version control by default.

## 15. Validation and comparison plan

Compare a controlled set rather than installing every extension at once:

**A:** stock Pi; **B:** Pi plus stripped-down nicobailon; **C:** Pi plus stripped-down TINTIN. Use identical repository revisions, model endpoints, native reasoning settings, and acceptance criteria. Record unavoidable differences such as edit tool strategy.

Start with a small repeated fixture suite. Repetitions are a smoke test for instability, not statistical proof of superiority. Expand only where results differ enough to affect the decision.

| Test | Failure it should expose |
|---|---|
| Create/read/edit/read over several turns | Empty arguments, broken tool IDs, malformed multi-turn tool calling |
| Add a function plus tests; force one failure and repair | A model that narrates validation without actually running it |
| Search a medium repository for the correct implementation | Excessive broad reads, wrong files, inadequate retrieval tools |
| Two agents on disjoint read tasks | Management isolation and correct report attribution |
| One-slot local parent delegating to one-slot child | Inference-lock deadlock |
| Two TUI processes plus a detached child | A concurrency limit that only exists in one process |
| Cancel while thinking, testing, queued, and awaiting retry | Zombie requests/processes and late resumed writes |
| Crash parent or worker, then inspect/resume | Duplicate workers, lost ownership, unsafe cleanup |
| Dirty source checkout and a worker patch | Lost user edits or review of the wrong revision |
| Two writers touch overlapping files | Uncontrolled integration or false conflict-free success |
| Huge test output and huge child final report | Hidden context growth or lost failure evidence |
| Force compaction on each supported model profile | Lost task state or invalid tool/reasoning transcript |
| Quota/rate error and wrong model entitlement | Paid fallback, model substitution, runaway retries |
| Local screenshot task | Model advertises vision but does not receive/process the image |
| Prompt injection in a repository file | Untrusted instructions escaping intended role controls |
| Tool/path violations under restricted mode | Restrictions exist only as prose |

Report first-request input, accumulated input, cached/uncached input where supplied, output/reasoning where supplied, time to first useful action, total wall time, tool errors, retries, validation results, and accepted-task rate. Include queue wait separately from model generation and test execution.

Measure at least a fresh session, a warm continued session, a child launch, a child completion, and post-compaction continuation. A single prompt screenshot cannot establish the ongoing context advantage.

### Edit strategy experiment

Keep Pi's stock edit behavior for the baseline. If the weaker local models repeatedly produce malformed edits, compare an alternative edit strategy on the same tasks. Adopt it only if reduced repair work offsets extra tool/schema tokens. Do not treat an upstream marketing benchmark as proof for your quantization and endpoint.

## 16. Staged implementation backlog

| Stage | Deliverable | Exit criterion |
|---|---|---|
| **0. Inventory and pin** | Inventory Pi/server runtime, inspect provider entitlements locally, pin core/backend candidates, record source/license/API compatibility | No secrets logged; no server/config replacement; source ledger and clean baseline profile exist |
| **1. Local solo baseline** | Direct Qwen profile, compact instructions, request/usage capture, basic fixtures | Tools, reasoning replay, vision where enabled, compaction, and cancellation pass; actual overhead report produced |
| **2. One managed child** | Chosen backend, explicit fresh leaf, one scout and one writer, artifacts, existing fleet UI | No ambient extension leaks, no unintended commits, bounded reports, correct task ownership |
| **3. Resource and workspace correctness** | Endpoint admission, cancellation propagation, worktree handoff, independent validation, recovery | One-slot and two-process tests pass at the claimed scope; dirty checkout remains unchanged |
| **4. Compact coordinator UX** | Measured two-tool wrapper if warranted, profile selection, concise notifications, usage/doctor views | Functionality retained while request delta stays within chosen budget or has justified exceptions |
| **5. Hosted profiles** | Add MiniMax, GLM, Alibaba one at a time | Exact routing and account credentials confirmed; no silent substitution/fallback; quota errors are bounded |
| **6. Targeted capabilities** | Optional diagnostics, repository map, selected skills/MCP, edit strategy experiments | Each addition has a measured task-quality benefit and no leaf-context regression |
| **7. External interoperability** | Optional task-envelope adapter for Sol/Fable or other parent systems | Each subscription remains in its intended harness; normalized results preserve ownership and evidence |

Hosted providers can be probed during inventory, but do not make all-provider orchestration a prerequisite for proving the first local worker. A useful system should exist after stages 1–2, not only after the entire backlog.

## 17. Suggested repository layout

```text
pi-workbench/
  AGENTS.md                     # Short instructions for Codex building this repo
  package.json
  README.md
  docs/
    architecture.md             # This design, maintained with decisions
    source-lock.md              # Release/commit/license/API evidence
    decisions/
    provider-validation/
    benchmark-reports/
  src/
    extension.ts                # Pi extension registration
    profiles/                   # Our schema, validation, profile compiler
    backend/                    # One backend adapter; version/capability handshake
    tasks/                      # Task/result schemas and ownership
    policy/                     # Tool, model, context and extension policy
    resources/                  # Admission, reservations and cancellation
    workspaces/                 # Base revisions, artifacts, handoff, integration
    telemetry/                  # Redacted payload inspection and usage records
    ui/                         # Thin additions to existing Pi/backend UI
  agents/
    scout.md
    worker.md
    reviewer.md
  config/
    workbench.example.json      # Explicitly our schema
    providers.example.json      # No credentials or invented entitlement claims
  tests/
    unit/
    contract/
    integration/
    fixtures/
  scripts/
    doctor.*
    benchmark.*
```

Keep this as one TypeScript package initially unless actual dependency boundaries justify splitting it. Avoid a premature monorepo full of nearly empty services. Respect the pinned Pi/Node requirements found during inventory rather than relying on an older global runtime.

## 18. Definition of done

The first usable release lets you launch Pi against your local model, perform ordinary interactive coding, delegate one bounded task into a fresh context, inspect and stop that worker, preserve its changes, and verify its report. It records actual context overhead and prevents the worker from silently switching provider accounts.

The fuller release adds reliable shared resource admission, crash-safe ownership, precise worktree handoff, compact coordination, and verified hosted profiles. It does not need agent councils, cron, recursive task trees, permanent memory, a browser fleet, or a second dashboard to be successful.

The experience can approach Claude Code/Codex in workflow and management. Equivalent model performance is not guaranteed by equivalent controls. Use stronger hosted models as coordinators when measurements justify it, without forcing local workers to carry the coordinator's entire context.

**Final design principle:** retain a rich human interface, a small model-facing interface, and explicit enforceable execution boundaries. These are separate concerns; improving the UI need not inflate every worker request.

## 19. Source register

Sources were inspected on **19 September 2026**. URLs are given literally for Codex to retrieve. Development-branch documentation must be reconciled with the selected release/commit before implementation. Citation IDs in this file are local to this document.

**[A01] Uploaded starting plan.** `qwen3.8_pi_harness_research(1).md`, 552 lines. Key ranges: 3–40 model/harness premise; 181–289 proposed provider configuration; 293–349 context/timeouts; 353–438 instructions/delegation; 442–468 worktrees/RPC; 472–534 smoke tests. It is prior design input, not independently established runtime evidence.

**[S01] Pi release v0.85.1.** Latest release page resolved here; includes the supported SDK/RPC versus experimental interface distinction.  
`https://github.com/earendil-works/pi/releases/tag/v0.85.1`

**[S02] Pi coding-agent README.** Core tools, UI/session behavior, context files, project trust, and extension philosophy. Development branch inspected.  
`https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/README.md`

**[S03] Pi extension documentation.** Tools, active-tool control, lifecycle hooks, UI integration.  
`https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/extensions.md`

**[S04] Pi v0.85.1 llama.cpp documentation.** Native integration is explicitly a router integration.  
`https://raw.githubusercontent.com/earendil-works/pi/v0.85.1/packages/coding-agent/docs/llama-cpp.md`

**[S05] Pi v0.85.1 provider documentation.** MiniMax, Z.AI, Qwen Token Plan variants and credential conventions.  
`https://raw.githubusercontent.com/earendil-works/pi/v0.85.1/packages/coding-agent/docs/providers.md`

**[S06] Pi RPC documentation.** Supported local process interface, commands, framing, queue/abort behavior.  
`https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/rpc.md`

**[S07] Pi v0.85.1 SDK documentation.** Supported embedding interface.  
`https://raw.githubusercontent.com/earendil-works/pi/v0.85.1/packages/coding-agent/docs/sdk.md`

**[S08] Hugging Face pi-llama.** Optional llama.cpp connector/discovery extension.  
`https://github.com/huggingface/pi-llama`

**[S09] Official Qwen3.8-27B model card.** Reasoning controls, request examples, sampling, and multi-turn guidance.  
`https://huggingface.co/Qwen/Qwen3.8-27B`

**[S10] nicobailon/pi-subagents overview.** Candidate backend; documentation inspected at development branch.  
`https://github.com/nicobailon/pi-subagents`

**[S11] nicobailon tool reference.** Context behavior, output caps, reported-usage budgets, writer checkpoint guidance.  
`https://github.com/nicobailon/pi-subagents/blob/main/docs/tool-reference.md`

**[S12] nicobailon configuration documentation.** Explicit controls for concurrency, context, schedules, missions, and timeouts.  
`https://raw.githubusercontent.com/nicobailon/pi-subagents/main/docs/configuration.md`

**[S13] nicobailon agent documentation.** Effective roles, tools, extension inheritance, nested delegation.  
`https://github.com/nicobailon/pi-subagents/blob/main/docs/agents.md`

**[S14] nicobailon workflows documentation.** Managed worktrees, clean bases, handoff, and integration behavior.  
`https://github.com/nicobailon/pi-subagents/blob/main/docs/workflows.md`

**[S15] nicobailon extension API.** Versioned RPC, foreground delegation, capability contracts and restrictions.  
`https://raw.githubusercontent.com/nicobailon/pi-subagents/main/docs/extension-api.md`

**[S16] nicobailon package manifest.** Inspect packaging/export availability against the selected artifact; a development manifest is not proof of npm publication.  
`https://raw.githubusercontent.com/nicobailon/pi-subagents/main/package.json`

**[S17] TINTIN subagents README.** Interactive fleet, routing, scheduling/concurrency, worktrees, and automatic preservation commits.  
`https://raw.githubusercontent.com/tintinweb/pi-subagents/master/README.md`

**[S19] OpenAI Codex subagent documentation.** Workflow reference, not a proposal to reroute Codex through Pi.  
`https://learn.chatgpt.com/docs/agent-configuration/subagents`

**[S20] Claude Code subagent documentation.** UX and workflow reference.  
`https://code.claude.com/docs/en/sub-agents`

**[S21] OpenCode agent documentation.** Primary/subagent model, task permissions, model-facing role restrictions.  
`https://opencode.ai/docs/agents/`

**[S22] Aider repository-map documentation.** Optional bounded repository context pattern.  
`https://aider.chat/docs/repomap.html`

**[S23] MiniMax Pi setup documentation.** Explicit subscription-key integration.  
`https://platform.minimax.io/docs/token-plan/pi`

**[S24] Z.AI coding-plan supported tools.** Includes Pi and endpoint guidance.  
`https://docs.z.ai/devpack/tool/others`

**[S25] Upstream Pi subagent example.** Reference implementation, not a claim of full production lifecycle parity.  
`https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/examples/extensions/subagent/index.ts`

**[S26] Alibaba Cloud Token Plan overview.** Distinguish current plan products and validate entitlements.  
`https://www.alibabacloud.com/help/en/model-studio/token-plan-overview`

**[S27] MiniMax Token Plan introduction.** Credential and account-side plan/credit behavior.  
`https://platform.minimax.io/docs/token-plan/intro`

**[S28] nicobailon model configuration.** Resolve exact model and reasoning dispatch semantics against pinned code.  
`https://github.com/nicobailon/pi-subagents/blob/main/docs/models.md`

**[S29] Pi custom model documentation.** Compatibility flags, thinking mappings, and transport-specific configuration.  
`https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/models.md`
