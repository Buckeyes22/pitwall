# Isolated consumer acceptance evidence

This record captures a bounded installed-host probe run on 2026-09-22 with
Claude Code 2.1.278. The final frozen consumer was staged at
`/tmp/pitwall-isolated-consumer-freeze2`; the source checkout and temporary
Claude MCP override were disposable and no credentials or raw transcripts are
stored here.

## Installed entrypoint

The component was copied into
`/tmp/pitwall-isolated-consumer-freeze2/component` and installed with
`packages/agent-routing/scripts/install.sh` using separate disposable script,
bin, state, config, cache, and home directories. The installer doctor reported
seven passes, one expected skip for an optional portable hooks file, and zero
failures. All 17 canonical links resolved to the copied component.

The final installed command was:

```text
/tmp/pitwall-isolated-consumer-freeze2/bin/pitwall-agent-routing
```

A disposable Claude MCP config pointed at that exact installed command. A
direct stdio `initialize`/`tools/list`
handshake through the installed command exposed `dispatch_and_wait`,
`answer_and_wait`, `wait_dispatch`, and `steer_and_wait`, alongside the
recovery tools. The same installed command served the live parent and child
sessions below.

The installed entrypoint resolves into the final copied component at
`/tmp/pitwall-isolated-consumer-freeze2/component`. SHA-256 hashes for the
key frozen files are:

```text
runtime/model_routing/mcp_tools.py       d790ff6a2c9cab6d2852165afbc3c9e095bdcdbefec736041f5d31d27832e7de
runtime/model_routing/managed_channel.py 4eb9e620dc0f6e4495ac3a7aa341d6a184b48ace088b50d51b47e4abd40c8f40
runtime/model_routing/run_store.py      f643b185ddcfc3a925fc74e18559e3945c4791366a221e71a19235ef69ad0dc5
runtime/model_routing/mcp_server.py     89081e29d4f4593f2665f715a208993805d35ca3464370ac12b06f587c487387
runtime/model_routing/routes.py         562df784cd97f2695d20b3c40fba3c91fdf0f938c88fef31b4a9b94acfb5525b
plugins/pitwall/hooks/launch-guard.py   1f2954bbc5d2ae56d3b0b19789ae540e4133797fcb670b531875759741c6681b
plugins/pitwall/hooks/hooks.json         f3390da7d31913bf3e78d2917f6f1b2fa70a2def85ac4f030fab2b59b8b02dc2
plugins/pitwall/commands/dag-routing.md  cd365fbc8f2d4789198def56dbe8780d2f942b9c0edbbeb665604d3a3c85ce6b
```

The final disposable doctor result was `pass` with 7 passes, 1 expected skip
for the optional portable hooks file, and 0 failures across all 17 installed
links.

## Real Claude parent and child exchange

The parent was a fresh `claude -p` session using model `haiku`, the installed
MCP command, and Claude Code 2.1.278. The child was a separate Claude Code
`haiku` process launched by the managed dispatcher through the installed
entrypoint. The child prompt described a repository maintenance decision and
required `ask_orchestrator` before changing anything.

The sanitized event record is:

```text
parent tool: mcp__pitwall-channel__dispatch_and_wait
dispatch_id: a8fd2b19-f864-4a15-a643-1e895a3718dd
child event: ask
ask_id: 0001
blocked_on: choice
severity: blocking
options: include=Include the interactive MCP exchange; omit=Omit the interactive MCP exchange
default: omit
effective deadline_s: 25
parent tool: mcp__pitwall-channel__answer_and_wait
answer: include (answered_by=orchestrator)
child event: terminal
terminal status/outcome: succeeded/ok
child provider/model: claude/haiku
child exit: 0
child wall_ms: 26270
parent final: EVENT_SEQUENCE dispatch_and_wait=ask answer_and_wait=terminal choice=include
```

The parent did not call `inbox` or `answer_ask`. The answer was written once,
and the terminal receipt included the answer record and redacted artifact
references under the disposable state root.

## UserPromptExpansion and first-action guard

The installed consumer plugin at
`/tmp/pitwall-isolated-consumer-freeze2/component/plugins/pitwall` was loaded
with `--plugin-dir` in a fresh Claude 2.1.278 session. The prompt began with
`/pitwall:dag-routing` and instructed the model to make exactly one first Bash
call to `./definitely-missing/codex-shim.sh --help`. The stream contained one
`Bash` tool use and no `Skill` or MCP tool use. The session-scoped marker was
observed before the first tool result, with `active: true` and
`routing_active: true`, proving the slash-command expansion activated the
marker before the first action. The guard then returned a structured deny:

```text
PreToolUse:Bash hook error: Blocked disconnected external-model launch (recognized shim). Use the managed Pitwall MCP `dispatch_and_wait` tool, which returns answerable child events; do not use a direct shim/provider command or a backgrounded Agent/Bash call.
```

The missing shim process was never started. The stream did not expose a named
`UserPromptExpansion` event, so the activation evidence is the host-observed
marker plus the first-action `PreToolUse` denial in a session with no other
activation tool call.

A fresh session without the routing command ran the same missing shim command;
it returned the shell's ordinary exit 127 and did not produce the Pitwall guard
denial. This confirms the marker is session-scoped and unrelated shell work
remains outside the guard.

## Installed lifecycle matrix

The same installed entrypoint was exercised from a fresh disposable state root
with a disposable `codex` provider harness. The provider harness opened the
installed MCP command as a real child channel, issued real `ask_orchestrator`,
`read_steering`, and `ack_steer` calls, and emitted no credentials or raw
transcripts. The matrix records transport and lifecycle behavior; the real
Claude parent/child exchange above is the external-provider proof.

The concurrent run launched two children together, with the slow child started
first. The fast ask arrived about 0.8 seconds before the slow ask, demonstrating
that the parent can observe asks out of dispatch order and answer both. Both
terminal receipts were `succeeded/ok`:

```text
slow dispatch: c8e92a30-e513-4e60-a1b7-6f9eb35e59e5 -> ask slow suffix? -> terminal succeeded/ok
fast dispatch: 756ce57f-a4f2-4711-928e-9d0052075f28 -> ask fast suffix? -> terminal succeeded/ok
ask arrival: fast before slow
```

The steering cases used separate children. A child that polled steering
returned `steer_ack` with `steer_acknowledged: true`, then reached terminal
`succeeded/ok` (`d44d34ce-cce3-4877-844a-da822d42f669`). A child that did not
read steering returned an ask with `steer_acknowledged: false`; answering its
ask then reached terminal `succeeded/ok`
(`6f3e15e8-dc84-41a2-a37f-3b4a741a043b`).

The default-deadline case (`b925d04f-4baf-4a83-9a0d-e8545a0ae62e` in the disposable
run record) published an ask with `deadline_s: 1`; the durable mailbox recorded
the stated default applied by `answered_by: default`, and the child reached
terminal `succeeded/ok`. The full UUID is retained in the disposable state
only; this document intentionally avoids raw artifact paths.

For restart/reattach, the first parent MCP process was closed while the child
was waiting on its ask. A new parent process, using the same installed command
and state root, called `wait_dispatch`, received the ask, answered it, and
received terminal `succeeded/ok` (`155c6d0b-8974-41fd-b466-cc7e26360647`). For
cancelled wait, the active `dispatch_and_wait` request was cancelled with an
MCP `notifications/cancelled` message; a new parent reattached to the surviving
child, answered its ask, and received terminal `succeeded/ok`
(`afecf98f-573c-4029-a5f0-9bcd49e47ee0`).

## Installed guard denial matrix

Each case used a fresh real Claude Code 2.1.278 session with the installed
plugin, began with `/pitwall:dag-routing`, made exactly one first tool call, and
was stopped after the hook denial. The marker was active in every case and no
child/provider process was started.

| First tool payload | Guard decision |
| --- | --- |
| native `Agent`, `run_in_background: false` | denied `native-agent` |
| native `Agent`, `run_in_background: true` | denied `native-agent` |
| `Agent`, `subagent_type: pitwall:codex-shim`, `run_in_background: true` | denied `shim-agent` |
| `Bash`, background `./definitely-missing/codex-shim.sh --help &` | denied `shim` |
| `Bash`, foreground `./definitely-missing/codex-shim.sh --help` | denied `shim` |
| `Bash`, foreground `./definitely-missing/codex --help` | denied `provider` |

The Bash paths were deliberately nonexistent, so a missed denial would have
produced an ordinary shell failure rather than provider usage. The matrix
confirms the first-action guard boundary for the listed recognizable forms;
opaque wrappers remain outside the detection guarantee. A separate real Claude
session with the active routing marker attempted
`./opaque-wrapper -- codex --help`: the guard allowed the Bash call and the
missing wrapper produced an ordinary shell error, with no provider started.
This is the documented boundary probe and intentionally does not invoke a real
provider through the wrapper.

## Disposable marketplace refresh probe

A disposable local marketplace declared `pitwall` version `0.11.1`, installed it
with `claude plugin install pitwall@pitwall --scope user --yes --json`, then
added a same-version marker to the marketplace source. Running
`claude plugin marketplace update pitwall` refreshed and validated the catalog.
Running `claude plugin update pitwall@pitwall --scope user --yes --json` returned
`updateOutcome: up_to_date` with old and new version `0.11.1`; the marker did not
appear in the installed cache. Same-version content therefore requires a fresh
install or version change; the tested update commands do not force a reinstall.
In the same disposable config, `claude plugin uninstall pitwall@pitwall
--scope user --yes --json` followed by the same `plugin install` command copied
the marker into the installed cache. An unrelated enabled local plugin remained
installed at version `1.0.0` throughout the pitwall uninstall/reinstall. This is
the safe refresh route for a same-version local source when a restart is
acceptable.

This record covers the installed single-child ask/answer journey, the
lifecycle matrix, the first-action guard matrix, and marketplace behavior.

An installed orphan probe used dispatch
`728f02a5-4c9a-4092-9af3-76a00c314165`, killed the disposable launcher process
group while its child ask was open, and reattached through the same installed
command. The reattached result was `event: orphan`, `status: error`,
`outcome: orphaned`, and the durable orphan record was written.

A real Claude managed-lease probe loaded Claude Code `2.1.278`, the inline
`pitwall` plugin version `0.11.1`, and a connected `pitwall-channel` MCP
server. Its sanitized event sequence was:

```text
ToolSearch -> dispatch_and_wait(provider=codex, ask_support=true) -> ask
ask dispatch_id=60c5f75a-74e7-448b-8b49-9d7c5c7872a6 ask_id=0001
Bash ./definitely-missing/codex-shim.sh --help -> PreToolUse guard denial (marker active)
answer_and_wait(choice=a, answered_by=orchestrator) -> terminal succeeded/ok (provider=codex, exit=0)
Bash ./definitely-missing/codex-shim.sh --help -> PreToolUse guard denial (marker active)
```

The disposable Codex child harness had the installed `pitwall-channel` MCP
command registered in its `CODEX_HOME`; without that registration, the new
managed preflight refused the interactive dispatch before launch. With the
registration present, the child ask and terminal completed through the
installed command.

The stream recorded two permission denials for the recognizable shell launch,
and no shell or provider process started for either denied Bash call. The
post-terminal shell probe occurred in the same Claude turn, before `Stop` or
`SessionEnd`; the session-scoped marker therefore continued to protect that
turn. The managed dispatch lease itself released on terminal; the remaining
denial is turn-scoped guard behavior until the Claude turn/session boundary,
not evidence that the managed child failed. The opaque-wrapper boundary above
intentionally did not invoke a real provider.

## Active workstation recheck

After the frozen disposable acceptance and independent race re-review passed,
the 17 Agent Routing links on this Linux workstation were moved to
`~/.local/share/pitwall/releases/20260922-channel-redesign-candidate/packages/agent-routing`.
The installer doctor reported 7 passes, 1 optional skip, and no failures.
The stable `~/.local/bin/pitwall-agent-routing` command resolves to
that component. The existing Claude, Codex, and OpenCode MCP registrations
still point to the stable command; Claude reports `pitwall-channel` connected.

Claude's same-version `pitwall@pitwall` plugin was reinstalled from the new
local marketplace source with persistent plugin data preserved. The installed
cache's `launch-guard.py` hash matches the frozen source, and `claude plugin
details` lists all six hooks: `PreToolUse`, `UserPromptExpansion`,
`PostToolUse`, `PostToolUseFailure`, `SessionEnd`, and `Stop`.

A fresh Claude Code `haiku` parent session used the active user MCP registration
and installed plugin. The host init record confirmed `pitwall@pitwall` loaded
and `pitwall-channel` connected. Its only tools were `ToolSearch`,
`mcp__pitwall-channel__dispatch_and_wait`, and
`mcp__pitwall-channel__answer_and_wait`; it did not call `inbox` or
`answer_ask`. The redacted state record shows:

```text
dispatch_id=6394e4de-e487-4aab-b635-a83f4b630afe
dispatch_and_wait -> ask 0001
answer_and_wait -> terminal succeeded/ok
durable answer: include, answered_by=orchestrator
child exit=0; parent result=success
```

This verifies a newly started host process. MCP processes that were already
running before the link switch still have the previous release loaded in
memory; their owning harness sessions must restart to use these new tools.

The active default doctor reports a passing MCP stdio handshake, all four new
parent tools, and registered child channels for Claude, Codex, and OpenCode.
It correctly leaves interactive delivery and launch enforcement as `SKIP`
because the default doctor does not spend provider quota or inspect a live
Claude hook session. Its 7 unrelated warnings concern existing route sync,
Grok CLI contract, unrestricted-mode configuration, and state size; no check
failed. The active command currently resolves Python 3.14.4, which satisfies
the component's supported `>=3.14,<3.15` runtime range; development and the
frozen candidate environment used Python 3.14.7.

## Final process and capability controls

Two additional disposable probes used the frozen installed command at
`/tmp/pitwall-isolated-consumer-freeze2/component/scripts/pitwall-agent-routing`
and sent `SIGKILL` to the MCP process, rather than closing its stdio stream.
With an ask open on dispatch `e1ec04e0-94de-4788-891b-02698adde96b`, the
launcher PID `906918` survived the parent MCP kill; a new MCP process
reattached by full UUID, recorded one answer, and returned terminal
`succeeded/ok`. With the waiter/MCP killed while dispatch
`e1a66299-1e67-48bf-96b4-65fa0cfa23b5` was running, launcher PID `906967`
survived; a fresh MCP process reattached and observed its durable terminal
`succeeded/ok` receipt. Disposable state was cleaned after the probes.

A separate disposable host configuration had no Claude plugin or `PreToolUse`
hook. Its installed doctor exited successfully: `channel.mcp_server=PASS`,
`channel.interactive_delivery=SKIP` with `registrationIsNotDelivery=true`,
and `channel.launch_enforcement=SKIP` with `enforcementVerified=false`.
Capability inventory reported `mcpChannel=false` for Claude. A recognizable
nonexistent shim path was not denied by the absent guard and failed in the
shell with exit 127, without starting any provider. This control does not
claim interactive delivery or launch enforcement for that host.

Finally, two fresh Claude Code 2.1.278 sessions loaded the installed plugin
without invoking routing. A foreground native `Agent` completed its harmless
task and returned `NATIVE_FOREGROUND_OK`; a background native `Agent` produced
a completed task notification with `NATIVE_BACKGROUND_OK`. Both
`PreToolUse:Agent` hooks exited 0 without a Pitwall denial, both parents
completed, and no active routing marker was created. This confirms the guard
does not suppress ordinary native Agent work outside a routing-active turn.

The configured MacBook, iMac, and Mac Mini were offline, so Darwin execution
used GitHub's real `macos-latest` runner instead. Agent Routing CI
[run 35740584346](https://github.com/Buckeyes22/pitwall/actions/runs/35740584346)
tested commit `4b3ec17` with Python 3.14.7. Its `macos-smoke` job passed the
complete component suite: 768 tests, 4 skips, then the installation doctor
and shell syntax checks. The log explicitly shows passing managed-channel
subprocess tests for ask/answer, concurrent children, cancellation, parent
restart/reattach, steering, default deadlines, orphan detection, prompt
cleanup, and launcher liveness. It also passed the Darwin `ps lstart`/`stat`
fallback and POSIX process-group launch tests. The required CI job and every
other Agent Routing job passed. This verifies the process path on Darwin;
the real Claude parent/child and plugin journeys above were run on Linux.

## Real Codex child exchange (2026-09-22, commit `af5e594`)

A second frozen consumer was staged at `/tmp/pitwall-isolated-consumer-codex`
from commit `af5e594` and installed with `scripts/install.sh --with-mcp` using
disposable home, bin, state, config, and cache directories and a disposable
`CODEX_HOME` holding a copy of the operator's Codex login (deleted after the
run). `setup mcp` registered `pitwall-channel` in that `CODEX_HOME`. The
installed doctor reported `channel.mcp_server=PASS` and
`channel.registration=PASS` for codex. The copied component matched the
source tree exactly (`diff -rq` excluding `.venv` and `__pycache__` was empty).

The parent was a fresh `claude -p --model haiku` session (Claude Code 2.1.278)
whose only MCP server was the installed command (`--strict-mcp-config`). The
child was the real Codex CLI with its default model, launched by the managed
dispatcher. The child prompt required `ask_orchestrator` before anything else.

```text
parent tool: mcp__pitwall-channel__dispatch_and_wait
dispatch_id: b3bd3bb0-df6b-4622-bd7a-3fe8ea179c8d
child event: ask
ask_id: 0001
question: Include the exchange?
blocked_on: choice
severity: blocking
default: omit
parent tool: mcp__pitwall-channel__answer_and_wait
answer: include (answered_by=orchestrator)
child event: terminal
terminal status/outcome: succeeded/ok
child provider/model: codex/codex-default
child exit: 0
child wall_ms: 21525
child stdout: include
parent final: EVENT_SEQUENCE dispatch_and_wait=ask answer_and_wait=terminal
```

The parent did not call `inbox` or `answer_ask`. The run's durable mailbox
holds exactly one ask and one answer. This closes acceptance case 1 for a
non-Claude child.

## Narrowed guard matrix (2026-09-22, commit `af5e594`)

A fresh Claude Code 2.1.278 session loaded the plugin from the frozen
component (`--plugin-dir`), with the workstation's own `pitwall@pitwall`
plugin disabled for that session, and began with `/pitwall:dag-routing`. A
logging `PreToolUse` hook recorded each call and the session's routing marker.

| Tool call | Guard decision |
| --- | --- |
| native `Agent`, `subagent_type: Explore`, listing a directory | allowed; its own `ls` Bash call ran |
| native `Agent`, `general-purpose`, whose prompt did not name a shim | allowed |
| that agent's own `Bash` call `./definitely-missing/codex-shim.sh --help` | denied `shim` (hook `agent_id` set, parent `session_id`) |
| native `Agent` whose prompt names `./definitely-missing/codex-shim.sh` | denied at the `Agent` call |
| `Workflow` inline script containing `codex("x")` | denied `workflow-shim` |
| `mcp__pitwall-channel__dispatch_and_wait` (codex, "pong") | allowed; terminal `succeeded/ok` |

This supersedes the two native `Agent` rows of the earlier guard denial
matrix: native research agents are now allowed, and a shim launch they attempt
is denied at their own tool call.

The same session exercised routing-lease release (plan Task 37). The marker
snapshot taken at the next tool call after the dispatch's `PostToolUse` (whose
`tool_response` was a JSON string) showed `"pending_dispatches": []`, and no
marker file remained after the session. Earlier the same day, before this fix,
four finished dispatches had left their leases in an operator session's marker
and kept provider commands blocked in later turns.

## Doctor launch-enforcement states (2026-09-22, commit `af5e594`)

The installed doctor, run against the disposable home:

```text
no Claude plugin installed      -> channel.launch_enforcement WARN "launch enforcement unavailable: the pitwall Claude plugin is not installed"
plugin installed, not enabled   -> WARN "launch enforcement unavailable: the pitwall@pitwall Claude plugin is installed but not enabled"
plugin installed and enabled    -> PASS "launch guard installed: pitwall@pitwall registers the PreToolUse launch guard; default doctor does not exercise a live denial"
```

`enforcementVerified` stays `false` in every state; the live denial is the
matrix above. A home with no Claude configuration reports SKIP (unit test
`test_server_handshake_and_registration_checks`).

SHA-256 of the key frozen files:

```text
runtime/model_routing/mcp_tools.py        4d8e0a289bf6990c9c415aded4a859f9a683d9356dfdc9a24389e61eb967105d
runtime/model_routing/managed_channel.py  880cb1283fec33c8ab1b2d814a27af6767b2bd9392802fbe66a8493d1c4285be
runtime/model_routing/run_store.py        9e1e81c72a8c659dbd7044d1dae39f4ce5b4c84b837b9bff4673541fae9e16e3
runtime/model_routing/doctor.py           a078ccdcacefbbe14487d83567c6cecd4c367f9acbe4f8f5527ec651102029b0
plugins/pitwall/hooks/launch-guard.py     c1fae0ed70545db250ffb80694c28dc79ab359ce218cb436d4b0f7bee4ee2c55
plugins/pitwall/hooks/hooks.json          f3390da7d31913bf3e78d2917f6f1b2fa70a2def85ac4f030fab2b59b8b02dc2
```

## Real Qwen Code child exchange (2026-09-22, commit `09c4ae4`)

With Qwen Code added to the channel harnesses, a frozen consumer at
`/tmp/pitwall-qwen-consumer` was installed from commit `09c4ae4` with
`install.sh --with-mcp` using a disposable home (holding a copy of the
operator's Qwen Code settings without hooks or MCP servers), config, state, and
cache. The installer registered `pitwall-channel` in the disposable
`~/.qwen/settings.json` as `{"command": <installed>, "args": ["mcp"],
"timeout": 3660000}` alongside the existing settings keys. The installed
doctor reported `channel.mcp_server=PASS` and `channel.registration=PASS` for
qwen.

A fresh `claude -p --model haiku` parent (Claude Code 2.1.278) dispatched the
operator's `q38-qwen` route: Qwen Code 0.23.0 running the local
`qwen3.8-27b-huihui-int8-mtp` endpoint.

```text
parent tool: mcp__pitwall-channel__dispatch_and_wait (route q38-qwen)
dispatch_id: 8eab7b98-6769-4816-b38e-d521f53f5ef4
child event: ask
ask_id: 0001
question: Include the exchange?
blocked_on: choice
severity: blocking
default: omit
parent tool: mcp__pitwall-channel__answer_and_wait
answer: include
child event: terminal
terminal status/outcome: succeeded/ok
child provider/model: qwen/qwen3.8-27b-huihui-int8-mtp
child exit: 0
child wall_ms: 49216
child stdout: include
```

Qwen Code starts its stdio MCP servers with its own environment, so the child's
`pitwall-channel` server received the dispatch identity without an `env` block
in the registration and exposed `ask_orchestrator` for this run.
