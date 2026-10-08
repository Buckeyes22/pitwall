# Codex Handoff — Pi Workbench

**Date:** 19 September 2026  
**Read first:** `2026-09-19-pi-workbench-architecture.md` in full.  
**This file is an implementation prompt, not permission to replace existing machine configuration or spend arbitrary subscription quota.**

## Objective

Build a lightweight, familiar interactive coding harness around upstream Pi for my local Qwen and other local models, MiniMax, GLM, and Alibaba Token Plan models. Clean subagent management is the main feature. Preserve low model-facing overhead.

Claude Code stays in Claude Code. Codex stays in Codex. OpenCode Go stays in OpenCode/my OpenCode shim. Do not migrate those credentials or route their subscriptions through Pi. This project may later support bounded task/result interoperability, not a universal model proxy.

The architecture document contains research findings, proposed design, and untested assumptions. Do not treat its proposed configuration keys as existing Pi APIs. Resolve all integration decisions against the exact pinned versions you use.

## Initial implementation scope

Complete **Stage 0: inventory and pin**, then **Stage 1: local solo baseline**. If those pass and the necessary endpoint is available, implement the smallest **Stage 2: one fresh managed child** vertical slice. Do not begin with a full orchestration framework, several backends, a standalone dashboard, or all cloud accounts at once.

Use TypeScript and Pi's supported extension/SDK/RPC surfaces. Build in one repository/package initially. Prefer upstream functionality to new infrastructure.

## Inventory before modifying anything

Inspect the actual machine and repository. Record installed Pi/Node/package-manager versions, active Pi config directories and context files, enabled extensions, local model endpoint, server build, exact exposed model ID, served context, slot/resource behavior, and existing launch configuration. Redact all credentials.

The earlier model description was Qwen3.8-27B Q8 XL with approximately 104K served context, vision, MTP, and a tuned GPU configuration. Treat those as prior information to verify—not permission to overwrite the launcher, change quantization, repartition GPU memory, or switch a fixed server into router mode.

Use a disposable fixture repository and isolated Pi configuration/profile for tests. Preserve existing global configuration. Inspect environment and config filenames without printing secret values.

Confirm the selected upstream release and pin it. The research saw Pi v0.85.1 as latest on 19 September 2026. Do not assume extension development-branch documentation matches a published npm package. Record actual source commits, package artifacts, licenses, and peer/API compatibility in `docs/source-lock.md`.

## Local baseline requirements

Start with stock Pi tools and a compact role instruction. Do not immediately add a large system prompt or a subagent catalogue.

Verify sequential multi-turn tools, read/edit stability, a test-and-repair loop, exact reasoning request fields and reasoning replay, vision if advertised, compaction, cancellation, and request/stream timeout behavior. Qwen's official API example uses top-level `reasoning_effort`; confirm the actual llama.cpp build's supported mapping rather than assuming the older draft's template-only mapping works.

Measure the actual model-facing request. Report system, tools, instructions, task/history, and total overhead where measurement supports that separation. Distinguish tokenizer estimates, provider-reported counts, and unavailable usage. Do not invent a token comparison with OpenCode.

Do not turn a missing live endpoint or missing credentials into fake validation. Implement local fixtures/mocks and clearly identify which checks remain unexecuted. Ask only for genuinely unavailable input after inspecting the accessible environment.

## Backend selection

First investigate **nicobailon/pi-subagents** (`pi-subagents`) because its documented integration seams may support a thin front end. Evaluate **tintinweb/pi-subagents** (`@tintinweb/pi-subagents`) in a separate isolated profile as the interactive-UX challenger if the first backend fails a required gate.

Do not install both subagent packages in the same runtime. Do not fork Pi or copy backend internals before producing a concrete failed acceptance gate and documenting why the supported API cannot meet it.

Use explicit fresh contexts, explicit child tools/extensions, bounded returned reports, no nested delegation, no schedules/missions, no automatic provider fallback, and no automatic commits. Use the backend's existing fleet UI before writing another one.

For nicobailon, inspect its current public API: detached RPC spawn is async-only; a separate structured foreground delegation API exists. Use a capability handshake. Never recursively invoke its model-facing tool inside another tool's tool-call hook. Do not assume separately installed Pi packages are directly importable Node dependencies.

## Non-negotiable correctness tests

1. **Context:** A leaf must not inherit the parent's transcript, parent-only extensions, or management catalogue. Its actual payload must prove this.
2. **Routing:** A profile resolves to an exact provider/model/endpoint/credential class. No fuzzy match may substitute a paid account or a different plan.
3. **Local scheduling:** Limit active inference requests, not agent lifetimes. A parent and child sharing one slot must not deadlock. State precisely whether limits cover one process, detached children, or multiple TUI sessions.
4. **Cancellation:** Stop queued follow-ups/retries, provider generation, and owned tool descendants. Distinguish a requested stop from confirmed termination.
5. **Workspace:** Preserve all pre-existing user changes. Do not auto-stash/commit/reset to satisfy worktree requirements. Review the worker's actual patch, not an empty clean base.
6. **Evidence:** Agent completion, successful tests, review, and acceptance are separate states. Rerun approved validation against the correct revision.
7. **Budget:** Reported-usage thresholds are not reservations or guaranteed account-side spending caps. Never claim they are.
8. **Capabilities:** A worktree or tool allowlist is not an OS sandbox. Do not label unrestricted shell roles read-only.

Use graceful checkpointing for writing workers before hard stops where possible. Preserve uncertain or failed workspaces for inspection.

## Proposed compact interface

After measuring the native backend baseline, evaluate a small wrapper exposing:

```text
agent_task(role, task, acceptance, scope?, inputs?)
agent_control(action, agent_id?, message?)
```

These are our proposed tools, not stock Pi APIs. Do not expose the original broad tool and the wrapper simultaneously merely to make both appear available. Confirm that disabling a tool also removes its injected instructions from the request where intended.

Use three initial roles: scout, worker, reviewer. Leaves do not spawn agents. Let deterministic test runners run tests without creating an unnecessary model agent.

## Deliverables from the first implementation session

Produce the runnable isolated local baseline, a concise root `AGENTS.md`, a source/version lock, validated example configuration without secrets, fixture tests, an actual measurement report, and a decision record describing the selected backend and any unresolved seams.

Report exactly what was changed, what was executed, what passed/failed, what remains mocked or untested, and the next smallest useful implementation slice. Preserve existing launchers and global settings. Do not claim full Claude Code/Codex equivalence or production-ready lifecycle guarantees from a successful smoke test.
