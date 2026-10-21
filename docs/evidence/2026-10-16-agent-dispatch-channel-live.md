# Live evidence: Agent-tool dispatch with the `pitwall-asks` monitor

- **Plan:** `docs/superpowers/plans/2026-10-16-agent-dispatch-two-way-channel.md`, Task 11 Steps 4–12 and Task 12 Step 6.
- **Run:** 2026-10-21, 03:55–04:32 UTC, on the workstation (`ed8`), in the Claude Code session that implemented the plan, after `claude plugin uninstall/install pitwall@pitwall-local` and `/reload-plugins`.
- **Releases:** the checks started on `20261021-agent-dispatch-d4f3286e`. The two defects below were fixed during the run, and later steps ran on `566f85b0` and then `80859575`.
- **Route:** `tpw-deepseek-v4.1-flash-1017` (opencode, `alibaba-token-plan/deepseek-v4.1-flash-1017`). Children ran from a clean scratch repo, `/tmp/adc/scratch`.
- **Prompt:** every ask used the same prompt file: call `ask_orchestrator` once with options `a` ("alpha") and `b` ("bravo"), default `a`, `blocked_on` `choice`, then print the chosen id.

## Results

| Step | Check | Result | Evidence |
|---|---|---|---|
| 4 | Monitor runs and keeps a heartbeat | PASS | `pitwall agents inbox --watch` pid 2172606 from the installed release; `watchers/<session>.json` refreshed every 5 s |
| 5 | Tier 1, background `pitwall:opencode-shim` Agent, no channel flag | PASS | run `2b39d652`: `channel.json` has this session, `harness=opencode`, `tier=1`. The monitor delivered `PITWALL ASK dispatch=2b39d652-… ask=0001 harness=opencode tier=1 deadline_s=169 default=a …` with no user input. `answer_ask` `b` was accepted, and the Agent returned `b` / `SHIM-DONE exit=0`. No launch-guard denial, no Stop-hook block. |
| 6 | Tier 4, pi | BLOCKED | run `d870bfd9`: the channel turned on (`tier=4`, this session). Pi's only working models are on the local endpoint `192.168.22.172` (`z4g4`), which was offline (ping and ssh: `No route to host`). Its default `chat-opencode/test-chat` failed `Connection error.` three times. This exposed defect 2. |
| 7a | `pitwall:route-shim` Agent | PASS | run `00bf2aad`: `PITWALL ASK … options: a=alpha; b=bravo`; answered `b`; `SHIM-DONE exit=0` |
| 7b | `route-shim.sh` as a Bash call with `run_in_background` | PASS | run `c7d345c5`: question delivered by the monitor; answered `b`; `SHIM-DONE exit=0` |
| 8 | Opt-out with `--routing-no-ask-support` | PASS | run `74912e82`: no `channel.json`, no notification; the child returned its default `a`, `SHIM-DONE exit=0` |
| 9 | Steering | PASS | run `e718b388`: `steer_and_wait` kind `note`, `requires_ack` true, returned `steer_ack` ("acknowledged, continuing"); `steer.acked` is in `events.jsonl`; the run finished `done` / `SHIM-DONE exit=0` |
| 10a | Session isolation | PARTIAL | No second interactive Claude session was opened. A second `inbox --watch` ran under another session ID with its own heartbeat. It printed 0 lines while five asks for this session arrived. That proves the watcher's session filter, but not two Claude hosts. |
| 10b | Managed path | PASS | run `6eadb77f` through `dispatch_and_wait`: the ask returned as the tool result with both options; `answer_and_wait` `b` reached `terminal` `ok`. The monitor printed nothing for `6eadb77f`. |
| 11 | Long background run | PASS | run `7382edc7`, background `pitwall:route-shim` Agent: the child was silent for about 12 minutes (seven `sleep 100` calls), past the 600000 ms default of `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS`. The Agent was not stopped as stalled. The ask arrived and was answered `b`, and the run took 754 s, ending `SHIM-DONE exit=0`. No settings change is needed. |
| 12 | Monitor longevity | PASS | At 35 minutes the monitor pid was alive and the heartbeat was 1 s old. A repeat of Step 5 (run `b88240fc`) was delivered, answered `b`, and ended `SHIM-DONE exit=0`. |
| 12b | `Workflow` with shim nodes (Task 12) | PARTIAL | No `Workflow` tool run. The installed `launch-guard.py` was fed real `PreToolUse` payloads under an activated routing marker. A Workflow whose node runs `route-shim.sh` through `pitwall:route-shim`: allowed. `agentType: 'pitwall:codex-shim'`: allowed. `Run verbatim: codex exec hi`: denied ("Blocked direct external-model launch (recognized provider)"). |

## Defects found and fixed during the run

1. **`ask.created` carried no options** (`566f85b0`). The mailbox stores options under `context.options`, but `channel.ask_event_data` read `ask["options"]`, so the Step 5 line showed `options:` with nothing after it. After the fix, Step 7a showed `options: a=alpha; b=bravo`.
2. **A Pi run whose provider requests all failed was recorded as an empty success** (`80859575`). Pi exits 0 after its retry loop gives up, and the failure is visible only in its JSON stream. Dispatch now passes `harness-output.jsonl` to `detect_soft_denial`, and the Pi adapter reports exit 77 with Pi's error.

Both were fixed test-first and pass the full unit suite (`10334 passed, 469 skipped` on `80859575`).

## Environment notes

- **Dirty checkout:** a route whose workspace is isolated refuses a dirty source checkout (`source worktree is dirty`). That's the documented preflight, so the checks ran from a clean scratch repo.
- **Watcher shutdown:** both test watchers exited on SIGTERM and removed their own heartbeat files.
