# Orchestrator channel live evidence (2026-09-21 to 2026-09-25)

Summary of every live orchestrator-channel run on record (plan
[`2026-09-23-review-remediation.md`](../superpowers/plans/2026-09-23-review-remediation.md),
Task 22, Step 1). Private receipts stay under `$PITWALL_EVIDENCE_ROOT`; the in-repository records
are linked. Each run used a disposable fixture and exited zero unless noted.

| Date | Harness and route | Observed | Evidence |
| --- | --- | --- | --- |
| 2026-09-21 | OpenCode, Alibaba DeepSeek V4.1 Flash (dispatch `b0ddd3e9`) | ASK reached the parent inbox; the parent answered the non-default `beta`; the child returned `CHANNEL_OK:beta` | `release-acceptance/installed-channel-20260921/ask-answer` |
| 2026-09-21 | OpenCode, Alibaba DeepSeek V4.1 Flash (dispatch `6606ee00`) | A scope steer was queued; the child called `read_steering` and `ack_steer 0001` and returned the steered marker `STEER_APPLIED:violet` | `release-acceptance/installed-channel-20260921/steer-ack` |
| 2026-09-21 | Claude Code, Sonnet (dispatch `eac5be9a`) | The child asked, took the non-default repair answer, acknowledged steer 0001, repaired `add.mjs`, and returned `CLAUDE_TOOL_CHANNEL_OK`; the parent reran the unchanged two-case test independently | `release-acceptance/installed-channel-20260921/claude-tool-journey` |
| 2026-09-22 | Claude Code parent and child | Event-driven `dispatch_and_wait` ask and `answer_and_wait` terminal through an installed consumer | [Real Claude parent and child exchange](../agents/isolated-consumer-acceptance-evidence.md#real-claude-parent-and-child-exchange) |
| 2026-09-22 | Codex child under a Claude parent | Event-driven ask, answer, and terminal receipt naming provider `codex` | [Real Codex child exchange](../agents/isolated-consumer-acceptance-evidence.md#real-codex-child-exchange-2026-09-22-commit-af5e594) |
| 2026-09-22 | Qwen Code child under a Claude parent | Event-driven ask, answer, and terminal receipt | [Real Qwen Code child exchange](../agents/isolated-consumer-acceptance-evidence.md#real-qwen-code-child-exchange-2026-09-22-commit-09c4ae4) |

## Release candidate runs (2026-09-25)

These runs used Agent Routing 0.12.0 from the release branch, installed into an isolated `HOME`
(the workstation's own installation was not changed). Each child got a failing `add.mjs` fixture:
it asked whether to repair, the operator answered `repair` with `pitwall-agent-routing answer`, a
queued scope steer asked for a named final marker, and the child acknowledged it, repaired the
file, and returned that marker. The parent then checked the test file's hash was unchanged and
reran the test itself. Every run recorded `ask.resolved`, `steer.acked`, and `dispatch.succeeded`.
Receipts: `release-acceptance/channel-live-20260925/`.

| Harness and route | Dispatch | Result |
| --- | --- | --- |
| Codex (subscription default) | `c21166da` | Asked, answered, steer acknowledged, `CODEX_LIVE_STEERED_OK`, test green |
| Kimi Code, `kimi-code/k3` | `b9f3c820` | Asked, answered, steer acknowledged, `KIMI_LIVE_STEERED_OK`, test green |
| Qwen Code, local `qwen3.8-27b` (`q38-qwen`) | `124e4314` | Asked, answered, steer acknowledged, `QWEN_LIVE_STEERED_OK`, test green |
| Cline, local `qwen3.8-27b` | `2488551c` | Asked, answered, steer acknowledged, `CLINE_LIVE_STEERED_OK`, test green |
| GitHub Copilot CLI 1.0.88 as the parent, Kimi Code child | `2eb5ef59` | Copilot called `dispatch_and_wait`, `steer_and_wait`, and `answer_and_wait`; the child's ask resolved `repair` by the orchestrator, the steer was acknowledged, and the child returned `COPILOT_PARENT_STEERED_OK`; test green |

Phase D (unattended fan-out, two Kimi children, `q38-qwen` as the policy answerer): the routine
naming ask was answered `a` with provenance `policy:qwen3.8-27b-huihui-int8-mtp` and the child
wrote `Notes`; the destructive ask escalated ("blocked_on 'destructive' always escalates to the
operator"), the operator answered `b`, and `tmp-scratch` was kept. Both tasks finished in one
attempt and the workflow succeeded in 35 seconds. The first two Phase D runs found two defects,
fixed before this run: the scheduler never serviced asks from tier-1 children, which block
instead of pausing, and the policy request's 64-token cap left a reasoning model no room to
answer. The plan's written Phase D procedure also creates its files after the seed commit, which
the isolated-workspace preflight rejects as a dirty tree.

Retained non-passes, recorded as observations: the first OpenCode steer fixture (`bb1a2f76`)
defaulted before the parent answered and returned `STEER_MISSING`, and the first OpenCode ask
fixture sent an invalid `blocked_on` value that the channel rejected.

## Coverage against the channel harnesses

| Harness | Ask and answer | Steer acknowledged |
| --- | --- | --- |
| Claude Code | Live | Live |
| OpenCode | Live | Live |
| Codex | Live | Live |
| Qwen Code | Live | Live |
| Cline | Live | Live |
| Kimi Code | Live | Live |
| GitHub Copilot CLI (parent) | Live | Live |

Phase D ran live on 2026-09-25 (above).
