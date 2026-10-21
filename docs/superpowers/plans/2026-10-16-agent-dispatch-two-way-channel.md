# Agent-tool dispatch with a two-way orchestrator channel: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In Claude Code, the native `Agent` tool with a `pitwall:<harness>-shim` subagent type becomes the normal way to launch every Pitwall harness. Children keep two-way communication: their questions wake the parent session, and the parent can answer and steer them.

**Architecture:** The ask and steer channel already works for direct shims launched with `--routing-ask-support`. Tier 1 is live, through the `pitwall-channel` MCP `ask_orchestrator` tool. Tier 4 is the file contract, where the child exits 75 and the run pauses. The root problem is that the channel is opt-in: every launch has to remember a flag, and nothing could answer a direct shim anyway. Four things are missing:
- an event announcing a new question,
- a listener that wakes the parent session,
- a shim that turns the channel on by itself whenever that listener is running,
- a launch guard and DAG tripwire that stop treating shim launches as disconnected.

This plan adds:
- an `ask.created` event tagged with the launching Claude session,
- `pitwall agents inbox --watch`, which streams this session's questions and keeps a heartbeat file fresh while it runs,
- a default in `pitwall agents _shim`/`dispatch`: when no flag is given, the channel is on exactly when the launching session's heartbeat is fresh,
- a plugin-declared Monitor that runs the watcher in every interactive session,
- a guard and a tripwire that allow shim launches in any form (Agent, Bash, background), because the shim connects itself.

Nothing is blocked to enforce the channel. With no listener (`-p`, SDK, scripts, CI, Codex or Copilot hosts), behavior is exactly today's.

**Tech Stack:** Python 3.14.7 (stdlib only in hook and watcher paths), Claude Code plugin manifest (`monitors/monitors.json`), pytest/unittest.

**Spec:** This conversation's findings, recorded in "Findings this plan closes" below. No separate spec doc.

## Findings this plan closes

1. The vo-rag session launched `pitwall:codex-shim` as a background Agent without `--routing-ask-support`, so that run had no channel.
2. The events log has `ask.escalated` and `ask.resolved` but no event when a question is first asked. `ask.escalated` fires only at half the deadline (`mcp_tools.py` `SubagentTools._await`).
3. Nothing wakes an idle Claude Code session when a child asks.
4. Instructions conflict:
   - `~/.claude/CLAUDE.md` says to use `codex-shim-bg.sh` with a Monitor.
   - The routing skill and `commands/dag-routing.md` require `dispatch_and_wait` and forbid shim Agents.
   - `codex-shim.md` and `opencode-shim.md` forbid background Agents.
5. `launch_guard_shell.py` `SHIM_BASENAMES`, `HARNESS_BASENAMES` and `PROMPT_SHIM_RE` omit `zcode` and `mcode`. The guard cannot see those launches at all, while `dag_tripwire_shims.py` already lists both.
6. The guard denies `pitwall:*-shim` Agents, and any direct `*-shim.sh` Bash call (including a transport subagent's own call), in routing-active sessions. The `dag-tripwire.py` Stop hook blocks a turn whose shim Agents ran without `dispatch_and_wait` or `Workflow`. Together they block the preferred path.
7. Root cause of 1 and 6: ask support is off unless the launch passes `--routing-ask-support` or `PITWALL_AGENTS_ASK_SUPPORT=1` (`dispatch.py` `_strip_routing_args`, and `Dispatch.load_prompt` near line 1038). The guard exists to force launches onto the one path that always sets it. It was opt-in because nothing could answer a direct shim (`docs/agents/orchestrator-channel.md` "## Enabling asks").

## Verified facts the design relies on

- **Shared code path.** Every `~/.claude/scripts/*-shim.sh` is a one-line `exec pitwall agents _shim <harness>`.
- **Tiers on this workstation.**
  - Tier 1, with `pitwall-channel` registered: agy, cline, codex, goose, grok, hermes, kimi, muse, opencode, qwen, zcode, plus mcode (in `CHANNEL_HARNESSES`).
  - Tier 4: dsh and pi.
  - The route shim resolves to one of these.
- **Session ID.** `CLAUDE_CODE_SESSION_ID` holds the **parent** session's ID in the main session's Bash, in a subagent's Bash and in a Monitor process. The live probe on 2026-10-16 showed `7a876bee-…` in all three. The value changes on `/clear`.
  - The docs say stdio MCP servers receive it too ([env-vars](https://code.claude.com/docs/en/env-vars)), so a managed `dispatch_and_wait` launch would inherit it. Task 1 excludes managed launches.
- **Child MCP servers run the installed release, not `PATH`.** The child configs name an absolute binary: `~/.config/opencode/opencode.json` and `~/.codex/config.toml` both run `/home/chris/.local/share/pitwall/releases/20261015-v0.3.0a1-public-44281034/.venv/bin/pitwall mcp serve channel`.
  - Codex passes only whitelisted env vars to it, so the child-side MCP server must read the session from the channel record, never from its env.
  - Live checks need the branch installed as a release.
- **Plugin monitors**:
  - **Declaration:** in `monitors/monitors.json` as an array of strict `{name, command, description, when?}` objects; unknown keys fail the plugin load.
  - **Lifetime:** with `when: "always"` they start at session start and on plugin reload, and keep running "until it ends" (the session). They run in the session working directory, in interactive sessions only.
  - **After an update:** a plugin update needs a session restart before monitors use it.
  - **Not covered by the docs:** what happens if the command exits, so the watcher must never exit on its own.
  - Sources: [components#monitors](https://code.claude.com/docs/en/plugins/components#monitors), [manifest-reference#monitors](https://code.claude.com/docs/en/plugins/manifest-reference#monitors), [loading](https://code.claude.com/docs/en/plugins/loading#when-auto-update-runs).
- **`claude plugin validate <dir>`** is the authoritative manifest check. It validates monitor paths on Claude Code v2.1.283 or later; this workstation runs 2.1.305 (re-checked 2026-10-20).
- **Background subagents** are subject to `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS`, default 600000. The docs call it a "stall timeout … for subagents" and don't define a stall. Task 11 checks whether a transport waiting silently in one long Bash call trips it.
- **`answer_ask` works on paused tier-4 runs.** `channel.answer_ask` writes the mailbox answer with no state check, and `pitwall agents runs resume` continues the run.
- **Plugin install copies everything.** `pitwall agents install` copies the whole `plugins/claude` tree (`installation.py`, `rglob("*")`), so a new `monitors/` directory ships without manifest changes.
- **One dispatch path.** `pitwall agents dispatch` and `pitwall agents _shim` both go to `dispatch_legacy`/`dispatch_profile` (`cli.py` near line 1956), so a default set in `Dispatch.load_prompt` covers every harness, the route shim, `*-shim-bg.sh` wrappers, and background launches.
- **Who passes the flag today.** Managed launches always pass `--routing-ask-support` (`managed_channel.py` near line 525). The workflow scheduler passes it only for `askSupport` tasks (`scheduler.py` near line 319). Its children inherit the session's env, so an unflagged task would pick up the new default unless the scheduler opts out explicitly.
- **Resume docs exist only for ask-support runs.** `dispatch.py` writes `askSupport: True` near line 1146, inside the ask-support branch, and the resume argv re-adds the flag (near line 1691). Resumes are unaffected by the default.
- **Release-acceptance bindings.** `tests/agents/test_mcp_subagent_tools.py` carries 6 reviewed bindings. New `add_argument` calls become `cli:argument:*` surfaces auto-bound to journey J36. Both need `make regen-bindings`.

## Global Constraints

- Python 3.14 only. `uv run` or `.venv/bin/python`, never bare `python`.
- Worktree setup before any task: `cd /home/chris/git/pitwall-agent-dispatch && uv sync --frozen --extra dev --python 3.14.7`.
- Hook files under `plugins/claude/hooks/` stay stdlib-only. The watcher module must not import fastapi, uvicorn, asyncpg, redis, arq, textual, runpod, mcp or prometheus_client (`tests/test_startup_imports.py`).
- `pitwall.agents` never imports broker services, repositories or `pitwall.db` (`tests/test_agents_import_direction.py`).
- The `## Asking and steering through the orchestrator channel` section keeps every needle `tests/agents/test_parity.py::test_channel_discipline_is_taught_everywhere` checks, in all three host skills.
- `dispatch_and_wait`, `answer_and_wait`, `wait_dispatch` and `steer_and_wait` keep working unchanged. They become the alternative path, not the removed one.
- No launch is blocked to enforce the channel. The guard's existing marker-scoped rules for raw provider CLIs, `pitwall agents workflow run|resume` and uninspectable `Workflow` scripts are unchanged.
- An explicit choice always wins over the default: `--routing-ask-support` or `PITWALL_AGENTS_ASK_SUPPORT=1` forces the channel on; `--routing-no-ask-support` or `PITWALL_AGENTS_ASK_SUPPORT=0` forces it off.
- Heartbeat constants: `WATCHER_HEARTBEAT_S = 5.0` (refresh interval) and `WATCHER_STALE_S = 30.0` (age after which a listener counts as gone).
- Tests stay hermetic: no live credentials or paid providers outside Task 11. Chris authorized (2026-10-16) Task 11's real-model runs on opencode and pi and the release install, gated on Steps 1–3 passing.
- Any task that changes a test bound in `release_acceptance/reviewed-bindings.json`, or adds a CLI argument, ends with `make regen-bindings` and commits `release_acceptance/reviewed-bindings*.json`.
- A task that adds an argparse argument also raises `release_acceptance/denominator.json` (`counts`, `total`, and a dated `reason` that keeps the previous note), or `tests/release_acceptance/test_denominator.py` fails. Task 3 adds two (`cli_arguments` 490 -> 492).
- Commits carry DCO `-s`.

## Review Focus

1. **Two Claude sessions in the same repo.** Session A's child asks, and only session A is woken. Pinned by `test_watch_ignores_other_sessions` (Task 3).
2. **The same question asked twice concurrently.** `reenter_open` returns the existing ask. The watcher prints it once. Pinned by `test_watch_dedupes_repeated_ask_events` (Task 3) and `test_reentered_ask_emits_no_second_created_event` (Task 2).
3. **A `dispatch_and_wait` run in a session that also has the monitor.** The ask reaches Claude once, through the tool result, not also through the monitor. Pinned by `test_managed_launch_records_no_orchestrator_session` (Task 1).
4. **A shim launched with no listener**: a `-p` session, a script, CI, or the test suite itself running inside Claude Code (it inherits `CLAUDE_CODE_SESSION_ID` but uses a sandboxed state root). The channel stays off and the run behaves exactly as today; no tier-4 run pauses with nobody to answer. Pinned by `test_default_is_off_without_a_fresh_listener` and `test_stale_heartbeat_means_no_listener` (Task 4).
5. **The watcher hitting a bad line, a missing or truncated log, or a restart while a question is open.** It keeps running, prints nothing for the bad line, and re-announces still-open questions after a restart. Pinned by `test_watch_survives_bad_missing_and_truncated_log` and `test_watch_announces_open_asks_at_start` (Task 3).

---

### Task 1: Record the launching Claude session and harness on the channel record

**Files:**
- Modify: `src/pitwall/agents/channel.py` (`ChannelConfig`, `ChannelConfig.to_json`, `_config_from_json`)
- Modify: `src/pitwall/agents/dispatch.py` (`_write_channel_record`, near line 1084)
- Test: `tests/agents/test_channel_config.py`

**Interfaces:**
- Produces:
  - `ChannelConfig.orchestrator_session: str | None = None` and `ChannelConfig.harness: str | None = None`.
  - JSON keys `orchestratorSession` and `harness`, both optional on read so older records still load.
  - `ORCHESTRATOR_SESSION_ENV = "CLAUDE_CODE_SESSION_ID"` in `channel.py`.

- [ ] **Step 1: Write the failing tests**
  - `test_channel_config_round_trips_orchestrator_session_and_harness`: build `ChannelConfig("d1", orchestrator_session="sess-a", harness="opencode")` and round-trip it through `to_json` and `_config_from_json`. Assert both fields are equal.
  - `test_channel_config_without_session_keys_still_loads`: a schemaVersion-1 doc without the two keys loads, with both fields `None`.
  - `test_shim_records_claude_session_on_channel_record`: run the fake-harness direct dispatch used in `tests/agents/test_channel_tier1_integration.py` with `--routing-ask-support` and env `CLAUDE_CODE_SESSION_ID=sess-a`. Assert that `load_channel_config(run_dir)` returns `orchestrator_session == "sess-a"` and `harness` equal to the dispatched harness id.
  - `test_managed_launch_records_no_orchestrator_session`: the same run with `PITWALL_AGENTS_MANAGED_LAUNCH=1` (`run_store.MANAGED_LAUNCH_ENV`) records `orchestrator_session is None`.
- [ ] **Step 2: Run them and confirm they fail.** Run `uv run pytest tests/agents/test_channel_config.py -q`. Expected: 4 failed.
- [ ] **Step 3: Implement.**
  - Add the two fields to `ChannelConfig`, `to_json` and `_config_from_json` (read with `doc.get`).
  - In `_write_channel_record`, pass `harness=self.harness_id` and `orchestrator_session=None if self.env.get(MANAGED_LAUNCH_ENV) == "1" else (self.env.get(ORCHESTRATOR_SESSION_ENV) or None)`.
  - On resume (`self.previous_channel`), keep the previous record's session when the env has none.
- [ ] **Step 4: Run them and confirm they pass.** Run `uv run pytest tests/agents/test_channel_config.py tests/agents/test_channel_tier1_integration.py -q`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -s -m "feat(agents): channel record carries the launching Claude session and harness"`

### Task 2: Emit `ask.created` for every new question, on both tiers

**Files:**
- Modify: `src/pitwall/agents/channel.py` (new `ask_event_data`)
- Modify: `src/pitwall/agents/mcp_tools.py` (`SubagentTools.ask`, and `SubagentTools._await` for the `ask.escalated` payload)
- Modify: `src/pitwall/agents/dispatch.py` (`_finish_paused`)
- Modify: `src/pitwall/agents/scheduler.py` (the workflow `ask.escalated` emit near line 750)
- Modify: `docs/agents/orchestrator-channel.md`, `docs/agents/lifecycle-hooks.md` (event tables)
- Test: `tests/agents/test_mcp_subagent_tools.py`, `tests/agents/test_pause_contract.py`

**Interfaces:**
- Consumes: `ChannelConfig.orchestrator_session` and `ChannelConfig.harness` (Task 1), both read from the run's channel record. The MCP server never reads `CLAUDE_CODE_SESSION_ID` from its own env.
- Produces:
  - `ask_event_data(ask: Mapping[str, Any], config: ChannelConfig | None, tier: str) -> dict[str, Any]`, returning keys `askId`, `question`, `options`, `default`, `deadlineS`, `blockedOn`, `severity`, `tier`, `orchestratorSession`, `harness`.
  - The event name `ask.created` carries that dict. `ask.escalated` data gains `orchestratorSession` and `harness`.

- [ ] **Step 1: Write the failing tests**
  - `test_ask_emits_created_event_with_session` (test_mcp_subagent_tools.py): with a channel config whose `orchestrator_session="sess-a"`, start `SubagentTools.ask` in a thread, wait for the ask file, answer it, and join. Read the run's `events.jsonl`. Assert exactly one `ask.created` whose data has `askId`, `question`, `tier == "1"` and `orchestratorSession == "sess-a"`.
  - `test_reentered_ask_emits_no_second_created_event`: extend the setup of `test_concurrent_identical_asks_reenter_the_same_ask`. Assert the count of `ask.created` events for that ask id is at most 1 per `ask` call that wrote the file. The watcher dedupes the race in Task 3.
  - `test_escalated_event_carries_session`: from the `test_deadline_returns_the_default_and_records_it` setup, assert the `ask.escalated` data has `orchestratorSession`.
  - `test_workflow_escalation_carries_session` (test_scheduler.py): a workflow task whose run has a channel record with `orchestrator_session="sess-a"` and an ask the policy cannot answer emits `ask.escalated` whose data has `orchestratorSession == "sess-a"` and `harness`. Without it, Task 3's session filter would drop the scheduler's escalations.
  - `test_tier4_pause_emits_created_for_each_pending_ask` (test_pause_contract.py): run a fake child that writes one tier-4 ask and exits 75. Assert an `ask.created` event with `tier == "4"` and the ask's id, emitted before `dispatch.paused`.
- [ ] **Step 2: Run them and confirm they fail.** Run `uv run pytest tests/agents/test_mcp_subagent_tools.py tests/agents/test_pause_contract.py tests/agents/test_scheduler.py -q`. Expected: the 5 new tests fail.
- [ ] **Step 3: Implement.**
  - In `SubagentTools.ask`, snapshot `{a["ask_id"] for a in box.pending_asks()}` before `write_ask`. Emit `ask.created` only when the returned `ask_id` is not in the snapshot.
  - In `_finish_paused`, emit `ask.created` for each pending ask before `emitter.emit_dispatch_paused`.
  - In `scheduler.py`, add `orchestratorSession` and `harness` from `load_channel_config(store.path)` to the workflow `ask.escalated` data.
  - Add `ask.created` to both docs' event tables.
- [ ] **Step 4: Run them and confirm they pass.** Run `uv run pytest tests/agents/test_mcp_subagent_tools.py tests/agents/test_pause_contract.py tests/agents/test_scheduler.py tests/agents/test_events.py -q && make regen-bindings && uv run pytest tests/release_acceptance/test_surface_bindings.py -q`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -s -m "feat(agents): announce every new ask with an ask.created event"`. Include `release_acceptance/reviewed-bindings*.json`.

### Task 3: `pitwall agents inbox --watch` streams this session's questions

**Files:**
- Create: `src/pitwall/agents/ask_watch.py`
- Modify: `src/pitwall/agents/channel.py` (heartbeat constants, `watcher_heartbeat_path`, `listener_active`)
- Modify: `src/pitwall/agents/cli.py` (the `inbox_parser` near line 1735, the `_inbox` dispatch near line 1971)
- Modify: `tests/test_startup_imports.py` (new test `test_inbox_watch_imports_no_heavy_modules`; the existing loop keeps its `pitwall.agents.dispatch` assertion for `_shim` and `_steer-gate` only)
- Test: `tests/agents/test_ask_watch.py`

**Interfaces:**
- Consumes:
  - `ask.created` and `ask.escalated` event data (Task 2).
  - `channel.read_channel_inbox(env, None)` for the startup backlog. It already lists unresolved asks of live and paused runs only, with `dispatchId`, `askId`, `question`, `options`, `default`, `deadlineRemainingS` and `blockedOn`.
  - `load_channel_config` and `ChannelConfig.orchestrator_session` (Task 1) to filter that backlog by session.
- Produces:
  - `format_ask_line(event: Mapping[str, Any]) -> str`
  - `matches_session(event: Mapping[str, Any], session_id: str | None) -> bool`. A `None` session matches everything.
  - `open_ask_events(env: Mapping[str, str], session_id: str | None) -> list[dict[str, Any]]`
  - `watch(events_path: Path, env: Mapping[str, str], session_id: str | None, *, out: TextIO, stop: threading.Event, poll_s: float = 0.5) -> int`
  - CLI: `pitwall agents inbox --watch [--all-sessions]`. The session defaults to `CLAUDE_CODE_SESSION_ID`.
  - In `channel.py` (already imported by `dispatch.py`, stdlib only): `WATCHER_HEARTBEAT_S = 5.0`, `WATCHER_STALE_S = 30.0`, `watcher_heartbeat_path(env: Mapping[str, str], session_id: str) -> Path | None`, which returns `state_root(env) / "watchers" / f"{session_id}.json"` and `None` when the ID doesn't match `^[A-Za-z0-9-]{1,128}$`, and `listener_active(env: Mapping[str, str], session_id: str | None, *, now: float | None = None) -> bool`, which is true when that file's mtime is within `WATCHER_STALE_S` of `now`.
  - The watcher writes the heartbeat as `{"schemaVersion": 1, "session": …, "pid": …, "updatedEpoch": …}` through a temp file and `os.replace`, at start and every `WATCHER_HEARTBEAT_S`, and deletes it on `stop`. `--all-sessions` writes no heartbeat.

- [ ] **Step 1: Write the failing tests** in `tests/agents/test_ask_watch.py`. Each test writes envelopes shaped like `EventEmitter.emit` output into a temp `events.jsonl` and runs `watch` in a thread with a `stop` event.
  - `test_format_ask_line_names_reply_tool`: tier-1 line output equals:
    `PITWALL ASK dispatch=d1 ask=0001 harness=opencode tier=1 deadline_s=170 default=b | Which file? | options: a=x.py; b=y.py | reply: answer_ask(dispatch_id="d1", ask_id="0001", choice=<option id>)`
    For tier 4, the line ends with `| paused: answer, then pitwall agents runs resume d1`. The question is cut at 400 characters and each option text at 60.
  - `test_watch_streams_only_new_asks_for_this_session`: lines appended after start for `sess-a` are printed. `dispatch.output` lines are not.
  - `test_watch_ignores_other_sessions`: an `ask.created` for `sess-b` prints nothing when watching `sess-a`.
  - `test_watch_dedupes_repeated_ask_events`: two identical `ask.created` envelopes for one `(dispatchId, askId)` print one line. A later `ask.escalated` for it prints one line prefixed `PITWALL ASK ESCALATED`.
  - `test_watch_announces_open_asks_at_start`: given a run dir with a pending ask and a channel config for `sess-a`, the first output line is that ask before any tailing.
  - `test_watch_survives_bad_missing_and_truncated_log`: start with no file, create it, append a non-JSON line and a valid ask, truncate to zero, then append another ask. Each valid ask is printed once, nothing is printed for the bad line, the watcher is still running, and the exit code is 0 on `stop`.
  - `test_watch_keeps_heartbeat_fresh_and_removes_it_on_stop`: with `poll_s=0.05` and the heartbeat interval patched to 0.1 s, `listener_active(env, "sess-a")` is true while the watcher runs, the file's `updatedEpoch` advances, and the file is gone after `stop`. With `session_id=None`, no file is written under `watchers/`.
  - `test_heartbeat_path_rejects_unsafe_session_ids`: `watcher_heartbeat_path(env, "../x")` and `watcher_heartbeat_path(env, "")` return `None`, and `listener_active` returns false for them and for `None`.
  - `test_cli_inbox_watch_flag_parses`: `build_parser().parse_args(["inbox", "--watch", "--all-sessions"])` parses both flags.
- [ ] **Step 2: Run them and confirm they fail.** Run `uv run pytest tests/agents/test_ask_watch.py -q`. Expected: collection error, because `ask_watch` doesn't exist yet.
- [ ] **Step 3: Implement `ask_watch.py`.**
  - Use stdlib only, with a byte-offset tail loop: seek to EOF at start, poll the size every `poll_s`, and reset the offset to 0 when the size shrinks.
  - Keep a seen-set of `(dispatchId, askId, kind)`.
  - Call `out.flush()` after every line.
  - Catch every per-line and per-poll exception, write it to stderr and continue. The loop ends only on `stop`.
  - In `cli.py`, `--watch` lazily imports `ask_watch` and runs until SIGTERM or SIGINT, then exits 0.
- [ ] **Step 4: Run them and confirm they pass.** Run `uv run pytest tests/agents/test_ask_watch.py tests/test_startup_imports.py -q && make regen-bindings && uv run pytest tests/release_acceptance -q -k "surface_bindings or cli_arguments"`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -s -m "feat(agents): inbox --watch streams this Claude session's asks and keeps a listener heartbeat"`. Include `release_acceptance/reviewed-bindings*.json`.


### Task 4: The shim turns the channel on by itself when its session has a live listener

**Files:**
- Modify: `src/pitwall/agents/dispatch.py` (`RoutingOptions.ask_support`, `_strip_routing_args`, `Dispatch.load_prompt` near line 1038)
- Modify: `src/pitwall/agents/scheduler.py` (the `_shim` argv builder near line 319)
- Modify: `docs/agents/orchestrator-channel.md` (rewrite `## Enabling asks`)
- Test: `tests/agents/test_channel_default.py` (create), `tests/agents/test_scheduler.py`

**Interfaces:**
- Consumes: `listener_active` (Task 3) and `ORCHESTRATOR_SESSION_ENV` (Task 1).
- Produces:
  - `RoutingOptions.ask_support: bool | None = None`. `--routing-ask-support` sets `True`; the new `--routing-no-ask-support` sets `False`.
  - `resolve_ask_support(routing: RoutingOptions, env: Mapping[str, str]) -> bool` in `dispatch.py`, in this order: the flag when not `None`; then `PITWALL_AGENTS_ASK_SUPPORT` `"1"` → on, `"0"` → off; otherwise `listener_active(env, env.get(ORCHESTRATOR_SESSION_ENV))`.

- [ ] **Step 1: Write the failing tests** in `tests/agents/test_channel_default.py`. Use the fake-harness direct dispatch from `tests/agents/test_channel_tier1_integration.py`, with `PITWALL_AGENTS_STATE_HOME` in a temp dir and `CLAUDE_CODE_SESSION_ID=sess-a`.
  - `test_fresh_listener_turns_the_channel_on_without_a_flag`: write a fresh heartbeat for `sess-a`, then dispatch with no routing flag. `load_channel_config(run_dir)` is not `None`, and its `orchestrator_session == "sess-a"`.
  - `test_default_is_off_without_a_fresh_listener`: no heartbeat file. No channel record exists, and the delivered prompt has no tier block.
  - `test_stale_heartbeat_means_no_listener`: a heartbeat whose mtime is `WATCHER_STALE_S + 1` seconds old leaves the channel off.
  - `test_other_sessions_listener_does_not_count`: a fresh heartbeat for `sess-b` leaves a `sess-a` launch off.
  - `test_explicit_choices_override_the_listener`: `--routing-no-ask-support` and env `PITWALL_AGENTS_ASK_SUPPORT=0` with a fresh heartbeat are off; `--routing-ask-support` with no heartbeat is on.
  - `test_tier4_default_pauses_only_with_a_listener` (pi-style tier-4 fake child): with a fresh heartbeat, the child's ask makes the shim exit 75; with no heartbeat, the same child is never given the contract and exits 0.
  - In `tests/agents/test_scheduler.py`, `test_task_without_ask_support_opts_out_explicitly`: a task without `askSupport` produces an argv containing `--routing-no-ask-support`. The existing `test_ask_support_task_dispatches_with_channel_flags` still passes.
- [ ] **Step 2: Run them and confirm they fail.** Run `uv run pytest tests/agents/test_channel_default.py tests/agents/test_scheduler.py -q`. Expected: the new tests fail; the explicit-on case already passes.
- [ ] **Step 3: Implement.**
  - Change the `RoutingOptions` field, parse `--routing-no-ask-support` next to `--routing-ask-support`, and replace the expression at `load_prompt` near line 1038 with `self.ask_support = resolve_ask_support(self.routing, self.env)`.
  - In `scheduler.py`, append `--routing-no-ask-support` when `task.get("askSupport")` is falsy.
  - Rewrite `## Enabling asks`. The channel turns on automatically when the launching Claude Code session runs the `pitwall-asks` monitor. The flag and env var force it on, `--routing-no-ask-support` and `PITWALL_AGENTS_ASK_SUPPORT=0` force it off. With no listener, nothing changes.
- [ ] **Step 4: Run them and confirm they pass.** Run `uv run pytest tests/agents/test_channel_default.py tests/agents/test_scheduler.py tests/agents/test_pause_contract.py tests/agents/test_channel_tier1_integration.py tests/agents/test_dispatch_import_weight.py -q`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -s -m "feat(agents): shims connect to the orchestrator whenever its session is listening"`

### Task 5: Plugin monitor that runs the watcher in every interactive session

**Files:**
- Create: `plugins/claude/monitors/monitors.json`
- Modify: `plugins/claude/README.md`, with one section covering:
  - what the monitor does, and that its heartbeat is what turns shim channels on,
  - that it runs only in interactive sessions,
  - that a plugin update needs a session restart before the monitor picks it up,
  - how to disable it.
- Test: `tests/agents/test_plugin_monitors.py`

**Interfaces:**
- Consumes: `pitwall agents inbox --watch` (Task 3).
- Produces: a monitor named `pitwall-asks`.

- [ ] **Step 1: Write the failing test** `test_monitor_manifest_is_strict_and_runs_the_watcher`:
  - Load the JSON.
  - Assert it is a list with one object whose keys are a subset of `{"name","command","description","when"}`.
  - Assert `name == "pitwall-asks"`, `when == "always"`, and `command == "pitwall agents inbox --watch"`.
  - Assert `"description"` contains `"questions"`.
- [ ] **Step 2: Run it and confirm it fails.** Run `uv run pytest tests/agents/test_plugin_monitors.py -q`. Expected: FileNotFoundError.
- [ ] **Step 3: Create the manifest** with exactly those four keys and the description `Pitwall subagent questions waiting on this session`.
- [ ] **Step 4: Run it and confirm it passes.** Run `uv run pytest tests/agents/test_plugin_monitors.py tests/agents/test_plugin_identity.py -q && claude plugin validate plugins/claude --strict`. Expected: all tests pass, and the validator prints `Validation passed`.
- [ ] **Step 5: Commit.** `git commit -s -m "feat(plugin): pitwall-asks monitor wakes the session on subagent questions"`

### Task 6: Launch guard stops treating shim launches as disconnected, and knows zcode and mcode

**Files:**
- Modify: `plugins/claude/hooks/launch_guard_shell.py` (`SHIM_BASENAMES`, `HARNESS_BASENAMES`, `PROMPT_SHIM_RE`, `_segment_launch`)
- Modify: `plugins/claude/hooks/launch-guard.py` (`main`, the deny message)
- Test: `tests/agents/test_claude_tripwires.py` (`LaunchGuardTests`)

**Interfaces:**
- Consumes: the Task 4 default. A shim or `pitwall agents dispatch` launch now carries its own channel, so neither is a "disconnected launch".
- Produces:
  - `command_launch` no longer returns `"shim"` for `*-shim.sh` basenames or for `pitwall agents dispatch`. It still returns `"provider"` for raw harness CLIs and `"dispatcher"` for `pitwall agents workflow run|resume`.
  - The marker-scoped `Agent` branch drops `SHIM_AGENT_TYPES` and `PROMPT_SHIM_RE` handling. A general-purpose Agent whose prompt names a raw provider CLI is still denied through `command_launch`.
  - `SHIM_BASENAMES` gains `mcode-shim.sh` and `zcode-shim.sh`, `HARNESS_BASENAMES` gains `mcode` and `zcode`, and `PROMPT_SHIM_RE` gains `mcode|zcode`. These sets are still used by `dag_tripwire_shims.py` and the Workflow script inspection.
  - Deny message: `Blocked direct external-model launch (recognized <kind>). Launch it through a pitwall:<harness>-shim Agent, a ~/.claude/scripts/<harness>-shim.sh command, or dispatch_and_wait, so Pitwall records the run and connects its channel.`

**Existing tests whose expectations change** (intended behavior change):
- `test_blocks_foreground_and_background_shims_before_execution` becomes `test_allows_foreground_and_background_shims`: all three commands are allowed.
- `test_blocks_namespaced_and_prompted_shim_agents` becomes `test_allows_namespaced_and_prompted_shim_agents`: both payloads are allowed.
- `test_native_agent_shim_bash_call_is_denied` becomes `test_native_agent_shim_bash_call_is_allowed`.
- `test_dag_command_expansion_activates_before_first_child_launch` and `test_session_end_clears_the_routing_marker` switch their probe command to `codex --help`, which is still denied while the marker is active.
- `test_blocks_recognizable_direct_harness_and_dispatcher_launches` drops `pitwall agents dispatch codex prompt.md` from its denied list and keeps the other three.

- [ ] **Step 1: Write the failing tests and edit the five above.**
  - `test_every_plugin_shim_launch_is_allowed`: for every `<h>` in `plugins/claude/agents/*-shim.md`, the Bash command `~/.claude/scripts/<h>-shim.sh p.md`, the same with a trailing `&`, and the `Agent` call with `subagent_type: pitwall:<h>-shim` (with `run_in_background` True and False) are allowed while the marker is active, including with `agent_id` set.
  - `test_raw_provider_clis_stay_denied`: `zcode -p hi` and `mcode run x` are denied while the marker is active, and the reason names `pitwall:<harness>-shim`.
  - `test_guard_knows_every_plugin_shim_agent`: with `agents = {p.stem for p in plugins/claude/agents/*-shim.md}` and `known = {n.removesuffix(".sh") for n in SHIM_BASENAMES}`, assert `agents <= known` and `known - agents == {"claude-shim"}`. The Claude shim has no plugin agent.
- [ ] **Step 2: Run them and confirm they fail.** Run `uv run pytest tests/agents/test_claude_tripwires.py -q -k "LaunchGuard or launch_guard"`. Expected: the new and edited tests fail.
- [ ] **Step 3: Implement** the Produces list.
- [ ] **Step 4: Run them and confirm they pass.** Run `uv run pytest tests/agents/test_claude_tripwires.py tests/agents/test_launch_guard_locks.py -q`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -s -m "feat(plugin): guard allows self-connecting shim launches and recognizes zcode and mcode"`

### Task 7: DAG tripwire accepts shim Agents as a correct transport

**Files:**
- Modify: `plugins/claude/hooks/dag-tripwire.py` (the turn scan near line 118 that tallies `Workflow`/managed calls against shim launches, and the block message near line 194)
- Modify: `plugins/claude/commands/dag-routing.md` (lines 8–10)
- Test: `tests/agents/test_claude_tripwires.py`

**Interfaces:**
- Consumes: the Task 4 default. A `pitwall:<h>-shim` Agent or a `*-shim.sh` Bash launch is a connected transport.

**Existing test whose expectation changes:** `test_dag_tripwire_blocks_direct_qwen_dispatch_without_workflow` becomes `test_dag_tripwire_accepts_direct_qwen_shim_agents`, with no block.

- [ ] **Step 1: Write the failing test** `test_dag_tripwire_accepts_shim_agents_and_shim_bash`: a turn with the routing skill active, two `pitwall:qwen-shim` Agent calls and one `qwen-shim.sh` Bash call, and no `dispatch_and_wait` or `Workflow` call, produces no `decision: block`. The tripwire's other checks (Claude models through `route-shim`, Gemini-only `agy-shim`) still block, as their existing tests show.
- [ ] **Step 2: Run it and confirm it fails.** Run `uv run pytest tests/agents/test_claude_tripwires.py -q -k dag_tripwire`. Expected: the new and edited tests fail.
- [ ] **Step 3: Implement.**
  - Remove the "shim dispatch without managed transport" rule. Keep the shim tally only where the other checks use it.
  - Rewrite `dag-routing.md` lines 8–10. For each node:
    - Launch a `pitwall:<harness>-shim` Agent (background allowed) or call `dispatch_and_wait`.
    - The `pitwall-asks` monitor surfaces the node's questions. Answer with `answer_ask`, and steer with `steer_and_wait`.
    - Record each node's dispatch ID and outcome before releasing dependants.
- [ ] **Step 4: Run it and confirm it passes.** Run `uv run pytest tests/agents/test_claude_tripwires.py -q`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -s -m "feat(plugin): DAG tripwire and dag-routing accept shim agents"`

### Task 8: All 15 shim agent definitions describe the self-connecting transport

**Files:**
- Modify: `plugins/claude/agents/{agy,cline,codex,dsh,goose,grok,hermes,kimi,mcode,muse,opencode,pi,qwen,route,zcode}-shim.md`. They range from 40 to 90 lines and are not uniform. Only `codex`, `grok` and `opencode` carry the long `NEVER set run_in_background` block, and only `codex` and `opencode` carry the comment `Backgrounded Agent or Bash calls are forbidden`.
- Test: `tests/agents/test_shim_agent_contract.py` (create)

**Interfaces:**
- Consumes: the Task 4 default, and the tier-4 `SHIM-DONE exit=75` pause from `_finish_paused`.
- Produces: one shared paragraph, inserted verbatim before each file's completion-check section:

  > `Channel: the shim connects itself to this Claude session when the pitwall-asks monitor is running, so the command needs no channel flag. This Agent may itself run in the background; your one Bash call stays in the foreground. If the final line is SHIM-DONE exit=75, return the full stdout followed by the line: PAUSED — the child asked a question; answer it with answer_ask, then resume with pitwall agents runs resume <dispatch-id>`

- [ ] **Step 1: Write the failing test** `test_every_shim_agent_states_the_channel_contract`. For each of the 15 files, assert:
  - the body contains the shared paragraph verbatim
  - it does not contain `Backgrounded Agent or Bash calls are forbidden`
  - it still contains one of the existing foreground rules, `Never background the work` or `NEVER background the work`
- [ ] **Step 2: Run it and confirm it fails.** Run `uv run pytest tests/agents/test_shim_agent_contract.py -q`. Expected: 15 failures.
- [ ] **Step 3: Edit all 15 files.**
  - Insert the paragraph.
  - In `codex-shim.md` and `opencode-shim.md`, replace the HTML-comment sentence `The transport is synchronous … Use the managed dispatch_and_wait MCP tool for routed external work.` with: the subagent blocks on the shim command, the Agent may run in the background, and the shim connects its own channel.
- [ ] **Step 4: Run it and confirm it passes.** Run `uv run pytest tests/agents/test_shim_agent_contract.py tests/agents/test_client_rehearsal.py tests/agents/test_parity.py -q`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -s -m "docs(plugin): every shim agent documents the self-connecting background transport"`

### Task 9: The routing skill and docs make Agent dispatch the primary Claude Code path

**Files:**
- Modify: `plugins/claude/skills/subagent-model-routing/SKILL.md`, these sections:
  - the intro bullets (lines 10–11)
  - `### Long dispatches and outage checks` (288)
  - `## Section 0 -- Mechanism is mandatory` (321)
  - `## Asking and steering through the orchestrator channel` (895), including the line near 902 that says the child hears nothing unless you pass `--routing-ask-support`, and the line near 924 about shim runs without the flag
  - `## Dispatch mechanics (managed event channel)` (954)
  - `## Dispatch pattern -- single call` and `-- parallel N`
  - the `Transport misroute` failure-mode row and the `batch direct shim calls` red-flag row
- Modify: `plugins/claude/skills/subagent-model-routing/ARCHITECTURE.md`, `plugins/claude/README.md` and `docs/agents/lifecycle-hooks.md`, wherever they describe the launch guard blocking shims
- Modify: `docs/agents/orchestrator-channel.md` (new section `## Agent-tool dispatch from Claude Code`)
- Modify: `CHANGELOG.md` `## [Unreleased]`: added `ask.created`, `inbox --watch` with its heartbeat, the `pitwall-asks` monitor and `--routing-no-ask-support`; changed the ask-support default, the guard and the DAG-tripwire policy
- Test: `tests/agents/test_direct_shim_ask_docs.py` (add a test), `tests/agents/test_parity.py` (unchanged, must stay green)

**Interfaces:**
- Consumes: the names from Tasks 2–8, exactly: `ask.created`, `pitwall agents inbox --watch`, `pitwall-asks`, `--routing-ask-support`, `--routing-no-ask-support`, `answer_ask`, `steer_and_wait`, `pitwall agents runs resume`.

- [ ] **Step 1: Write the failing test** `test_claude_skill_teaches_agent_dispatch`. In the Claude SKILL.md, the `## Dispatch mechanics` section must contain:
  - `pitwall:<harness>-shim`
  - `pitwall-asks`
  - `--routing-no-ask-support`
  - `answer_ask`
  - `steer_and_wait`
  - `pitwall agents runs resume`

  It must not contain `foreground-only, non-interactive fallback`.
- [ ] **Step 2: Run it and confirm it fails.** Run `uv run pytest tests/agents/test_direct_shim_ask_docs.py -q`. Expected: 1 failed.
- [ ] **Step 3: Rewrite the Claude skill sections.**
  - The primary flat and DAG launch is an `Agent` call with `subagent_type: pitwall:<harness>-shim`, background allowed. Its prompt carries the shim command, plus `--routing-workspace isolated --routing-task-mode write` for write tasks. No channel flag is needed while the `pitwall-asks` monitor runs.
  - The monitor delivers `PITWALL ASK` lines. Answer with `answer_ask` (tier 1). For tier 4, answer and then run `pitwall agents runs resume <id>` through the same shim agent type.
  - Steer with `steer_and_wait`. Use `--routing-no-ask-support` for a launch that must never ask.
  - `dispatch_and_wait` remains the documented alternative, for `-p` and SDK sessions where plugin monitors do not run.
  - Keep every parity needle, including `--routing-ask-support`, which still forces the channel on. The Codex and Copilot skills are unchanged.
  - Update the guard descriptions in the other three docs, then add the channel doc section and the changelog entry.
- [ ] **Step 4: Run it and confirm it passes.** Run `uv run pytest tests/agents/test_direct_shim_ask_docs.py tests/agents/test_parity.py -q && make docs-check 2>&1 | tail -5`. Expected: all pass, and the docs check is clean (`make docs-check` runs `tools/ci/check_markdown_links.py`).
- [ ] **Step 5: Commit.** `git commit -s -m "docs(agents): Agent-tool dispatch with the ask monitor is the Claude Code default"`

### Task 10: Remove the conflicting workstation instruction

**Files:**
- Modify: `/home/chris/.claude/CLAUDE.md` (outside the repo; no commit). In the `## Multi-agent lanes` section, replace the bullet `Long shim dispatches (over 15 minutes expected) go through codex-shim-bg.sh, never inside an Agent's Bash call. Wait with one Monitor on the exit_code file, not sleep loops.`

- [ ] **Step 1: Replace it with:** `Pitwall subagents launch as a pitwall:<harness>-shim Agent (background allowed). The shim connects its own channel while the pitwall-asks monitor runs, and questions arrive as PITWALL ASK notifications.`
- [ ] **Step 2: Verify.** Run `grep -n 'shim-bg\|pitwall-asks' ~/.claude/CLAUDE.md`. Expected: only the new line, naming `pitwall-asks`.

### Task 11: Full gates, install, and live checks across both tiers

**Files:** none until Step 13, which writes the evidence doc.

- [ ] **Step 1: Unit suite.** Run `make test-fast 2>&1 | tail -15`. Expected: 0 failed.
- [ ] **Step 2: CI parity.** Run `uv lock --check && uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/ && uv run python tools/ci/check_markdown_links.py && uv run python tools/ci/check_dco.py`, plus the semgrep and pip-audit steps exactly as `.github/workflows/ci.yml` writes them. Expected: every command exits 0.
- [ ] **Step 3: Journey runner.** Bring the test database up with `make up`, and run nothing else against it at the same time. Then run `bash scripts/release/run-user-journeys.sh`, with `DATABASE_URL` and `REDIS_URL` set for `pitwall_test` on `127.0.0.1:5444` and Redis on `127.0.0.1:6380`. Expected: the matrix gate passes.
- [ ] **Step 4: Install the branch as the workstation release.** Do this only after Steps 1–3 all pass. If any of them fails, fix it and rerun it; never install a build that has not passed all three.
  - Follow the existing release-install flow (memory `pitwall-agent-routing-install`). First record the current release dir with `readlink -f ~/.local/bin/pitwall`, and back up `~/.config/opencode/opencode.json` and `~/.codex/config.toml`.
  - Install from the worktree so that `~/.local/bin/pitwall`, the child MCP registrations in `~/.config/opencode/opencode.json` and `~/.codex/config.toml`, and `~/.local/share/pitwall/plugins/claude` all point at the new build.
  - Rollback, if needed: point `~/.local/bin/pitwall` back at the recorded release dir, restore the two config backups, and reinstall that release's plugin.
  - Run Steps 5–12 only in a new `claude` session started in a scratch repo, such as `mktemp -d` with `git init`. Leave other sessions alone; they keep their old plugin until they restart.
  - Expected: the scratch session's task panel lists `pitwall-asks`, `ls ~/.local/state/pitwall/agents/watchers/` shows that session's `<session-id>.json`, and `grep -c releases/ ~/.codex/config.toml` names the new release dir.
- [ ] **Step 5: Tier-1 live check (opencode, cheapest configured route).**
  - Launch a background `pitwall:opencode-shim` Agent with **no channel flag**. Its prompt tells the child to call `ask_orchestrator` once with options `a` and `b` and then print the chosen id.
  - Leave the session idle.
  - Expected:
    - a `PITWALL ASK … harness=opencode tier=1` notification arrives without user input
    - `answer_ask` with `b` is accepted
    - the Agent returns stdout containing `b` and `SHIM-DONE exit=0`
    - no guard denial or Stop-hook block fires
- [ ] **Step 6: Tier-4 live check (pi).**
  - Same prompt shape and no flag, using the file contract.
  - Expected:
    - a `PITWALL ASK … tier=4` notification
    - the Agent returns `SHIM-DONE exit=75` and the `PAUSED` line
    - after `answer_ask` and `pitwall agents runs resume <id>` through a `pitwall:pi-shim` Agent, the result contains the answer and `SHIM-DONE exit=0`
- [ ] **Step 7: Route-shim and background-Bash live check.** Run Step 5 through `pitwall:route-shim` on an existing tier-1 profile, then once more as a plain Bash `~/.claude/scripts/opencode-shim.sh p.md` with `run_in_background`. Expected: the same result as Step 5 both times.
- [ ] **Step 8: Opt-out check.** Run Step 5 with `--routing-no-ask-support`. Expected: no channel record in the run dir, no notification, and the child reports that `ask_orchestrator` is unavailable or never sees the tier block.
- [ ] **Step 9: Steering check.** During a tier-1 run told to work for 60 seconds, call `steer_and_wait` with kind `note`. Expected: a `steer.acked` event in the run's `events.jsonl`.
- [ ] **Step 10: Session isolation and managed path.**
  - Open a second session in the same repo and repeat Step 5 from the first. Expected: only the first session receives the notification.
  - Then run one `dispatch_and_wait` ask in the first session. Expected: the ask arrives as the tool result and the monitor prints nothing for it.
- [ ] **Step 11: Long background run.**
  - Launch a background `pitwall:opencode-shim` Agent whose child works silently for 12 minutes, past the 600000 ms default of `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS`, then asks once and finishes.
  - Expected: the Agent is not stopped as stalled, the question arrives, and the run ends with `SHIM-DONE exit=0`.
  - If the Agent is stopped as stalled:
    - set `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` to `1500000` in `~/.claude/settings.json` `env`,
    - add that setting to Task 9's skill text and `plugins/claude/README.md` as a documented requirement,
    - repeat this step until it passes.
- [ ] **Step 12: Monitor longevity.** Leave the session idle for 35 minutes, then confirm the heartbeat file's mtime is under 30 seconds old and repeat Step 5. Expected: the notification still arrives. The docs say plugin monitors run until the session ends.
- [ ] **Step 13: Record evidence.** Write `docs/evidence/2026-10-16-agent-dispatch-channel-live.md` with each step's command, the dispatch IDs and the observed lines. Commit with `git commit -s -m "docs(evidence): live two-way Agent dispatch across tiers"`.

### Task 12 (continuation, 2026-10-21): The launch guard allows Workflow scripts with shim nodes

Chris asked to relax the rule Task 6 kept. A native `Workflow` node is a subagent of the launching session, so its Bash call carries that session's `CLAUDE_CODE_SESSION_ID` (see "Verified facts") and the Task 4 default connects its shim exactly as it does for a shim `Agent`. Denying a Workflow script for containing shim nodes no longer protects anything.

**Files:**
- Modify: `plugins/claude/hooks/launch-guard.py` (`_workflow_launch`, its docstring, the `workflow` comment in `main`, the imports)
- Modify: `plugins/claude/hooks/launch_guard_shell.py` only to delete constants that no code or test uses any more
- Modify: `tests/agents/test_claude_tripwires.py` (`test_workflow_denied_only_when_it_launches_external_models`)
- Modify the docs that say Workflow shim nodes are denied or non-interactive: `plugins/claude/skills/subagent-model-routing/SKILL.md` (intro bullet, Part A bullets and paragraph, Section 0, `## Legacy Workflow pre-flight`, the routing-active paragraph near line 935, `## Dispatch mechanics`, the Part B paragraph near line 829), `ARCHITECTURE.md`, `plugins/claude/README.md`, `plugins/claude/commands/dag-routing.md`, `docs/agents/orchestrator-channel.md`, `docs/agents/lifecycle-hooks.md`, and the `CHANGELOG.md` `### Changed` entry Task 9 wrote

**Interfaces:**
- `_workflow_launch` returns `"workflow-uninspectable"` when the script cannot be read (unchanged), the `command_launch(script)` kind when the script launches a raw provider CLI, and `None` otherwise. Shim agent types, shim helpers and `*-shim.sh` paths are no longer reasons to deny.

**Existing test whose expectation changes:** `test_workflow_denied_only_when_it_launches_external_models` keeps its uninspectable cases (`/nonexistent/workflow.js`, `{"name": "saved-workflow"}`) denied and gains a raw-provider case (`await agent("Run verbatim: codex exec hi")`, denied); its two shim cases (`agentType: 'pitwall:codex-shim'`, `codex('review the diff')`) move to allowed.

- [ ] **Step 1: Write the failing test** `test_workflow_with_shim_nodes_is_allowed`: with the routing marker active, Workflow scripts using each of `agentType: 'pitwall:<h>-shim'` for every `plugins/claude/agents/*-shim.md`, the skill's `codex(...)` helper, and `Run verbatim: ~/.claude/scripts/opencode-shim.sh /tmp/x.md` are allowed, both inline (`script`) and from a `scriptPath` file. Edit the existing test as described above.
- [ ] **Step 2: Run them and confirm they fail.** `uv run pytest tests/agents/test_claude_tripwires.py -q -k "workflow"`.
- [ ] **Step 3: Implement** the Interfaces change, and rewrite the listed doc passages: a Workflow DAG whose nodes are shim agents is allowed in a routing-active session, each node's shim connects to this session through the `pitwall-asks` monitor exactly like a shim `Agent`, and an unreadable Workflow script or a raw provider launch is still denied. Keep the legacy Workflow examples and helper patterns; drop only the claims that they cannot ask the parent or must not run in a routing-active session.
- [ ] **Step 4: Run them and confirm they pass.** `uv run pytest tests/agents/test_claude_tripwires.py tests/agents/test_launch_guard_locks.py tests/agents/test_direct_shim_ask_docs.py tests/agents/test_parity.py tests/agents/test_shim_agent_contract.py -q && make docs-check`, then `make regen-bindings` and `uv run pytest tests/release_acceptance -q`.
- [ ] **Step 5: Commit.** `git commit -s -m "feat(plugin): launch guard allows Workflow scripts whose nodes are shim agents"`
- [ ] **Step 6: Live check.** Add to Task 11: a native `Workflow` with one `pitwall:opencode-shim` node running the ask prompt, launched in the scratch session, delivers a `PITWALL ASK` and finishes with `SHIM-DONE exit=0`.

### Task 13 (continuation, 2026-10-21): Defects the Task 11 live checks found

Both fixed test-first and committed during the live run.

- [x] **`ask.created` carried no options** (`566f85b0`). The mailbox stores an ask's options under `context.options`, but `channel.ask_event_data` read `ask["options"]`, so every `PITWALL ASK` line showed `options:` with nothing after it. `test_ask_emits_created_event_with_session` now asserts the options.
- [x] **A Pi run whose provider requests all failed was recorded as an empty success** (`80859575`). Pi exits 0 after its retry loop gives up, and the failure appears only in its JSON stream. `apply_soft_denial` now also scans `harness-output.jsonl`, and `PiAdapter.detect_soft_denial` reports exit 77 with Pi's error. Tests: `test_pi_provider_failure_after_exit_zero_is_a_soft_denial`, `test_failed_provider_turn_in_the_raw_stream_becomes_exit_77`.
