# Claude Code host acceptance evidence

This note records the pre-implementation host baseline for the event-driven
channel redesign. It is evidence about Claude Code 2.1.278 as installed on
2026-09-22; the later implementation and installed acceptance are recorded in
[isolated-consumer-acceptance-evidence.md](isolated-consumer-acceptance-evidence.md).
Probe transcripts and temporary configuration stayed outside the repository;
this note contains no credentials or raw transcripts.

## Hook payload and session identity

A fresh `claude -p` session was run with an isolated settings file containing a
logging `PreToolUse` command. The command was limited to harmless local probes and
used the installed subscription session with a low effort setting. The hook ran
for Bash, native Agent, and a dynamically configured MCP tool.

Every observed `PreToolUse` payload contained these top-level fields:

```json
{
  "cwd": "...",
  "effort": {"level": "low"},
  "hook_event_name": "PreToolUse",
  "permission_mode": "bypassPermissions",
  "prompt_id": "<uuid>",
  "session_id": "<uuid>",
  "tool_input": {},
  "tool_name": "<name>",
  "tool_use_id": "<id>",
  "transcript_path": "..."
}
```

The values were stable for all tool calls in a session and changed between fresh
sessions. This provided the session key later used by the routing marker; its
installed behavior is covered in the consumer evidence.

The observed tool-specific inputs were:

* Bash: `tool_name` was `Bash`; `tool_input` contained `command` and
  `description`.
* Native foreground Agent: the stream named the tool `Task`, while the hook
  payload used `tool_name: "Agent"`; `tool_input` contained `description`,
  `prompt`, `subagent_type`, and `run_in_background: false`.
* Native background Agent: the hook used the same `Agent` shape with
  `run_in_background: true`. Claude emitted an asynchronous launch result,
  later a completion notification, and then the subagent result on the native
  conversation surface.
* MCP: `tool_name` was namespaced as
  `mcp__probe__sleep_return`; `tool_input` contained the arguments and the
  payload also contained `mcp_server: {"name": "probe", "source": "dynamic"}`.

The hook response was visible as a `hook_response` event and included the command's
stdout, stderr, exit code, and outcome. A guard can therefore make a pre-execution
decision from the payload and return a structured deny response. The payload does
not include a Pitwall routing state or workflow marker by itself. A slash command or
Skill invocation is not represented in the later `PreToolUse` payload as a stable
activation field; `/dag-routing` is prompt/command content, so prompt-text inference
would be an unsupported activation mechanism.

The host also gave the MCP server process no observed `session_id` environment
value. A managed MCP server could not create a marker for the right Claude
session from its ordinary tool arguments. The first probe therefore established
that activation had to come from a hook with the observed `session_id`. Merely
checking for a marker file was not activation evidence.

An installed Pitwall plugin probe invoked `/pitwall:dag-routing` and then ran one
harmless Bash command. Claude recognized the slash command (it appeared in the
fresh session's slash-command inventory), and both the probe hook and the plugin's
PreToolUse hook ran. The Bash hook payload still had only the ordinary fields above;
it contained no slash-command name, Skill name, or routing/workflow attribution.
The session ID was the same as the one on the corresponding Stop-hook events. This
confirms that the host exposes session identity across lifecycle hooks, but the
later `PreToolUse` payload does not provide a routing activation signal by itself.

A subsequent installed-consumer probe used Claude's `UserPromptExpansion` hook
for `/pitwall:dag-routing`. It observed the session marker before the first Bash
action and a `PreToolUse` denial of a direct shim, with no prior Skill or MCP tool
call. See [isolated-consumer-acceptance-evidence.md](isolated-consumer-acceptance-evidence.md)
for the sanitized sequence and the unrelated-session control.

## Native Agent communication

Two bounded sessions used a trivial native task that returned a marker and did no
file or network work.

| Mode | Hook observation | Host result |
| --- | --- | --- |
| Foreground native Agent | `run_in_background: false` | The parent waited for a `task_started`, completion notification, and a native tool result containing the marker. |
| Background native Agent | `run_in_background: true` | Claude returned an asynchronous launch result, then emitted completion and task notification events; the parent received the marker through the native task surface. |

This proves only the native Claude lifecycle for these trivial tasks. It does not
attach Pitwall channel identities to native agents or prove that a native agent can
answer a Pitwall-dispatched child ask. The probes did not produce a native
child-to-parent question and answer, so native foreground and background modes
remain communication-unproven for acceptance item 5; completion notifications
alone are not sufficient to classify either mode as question-safe.

A follow-up foreground probe explicitly instructed the child to use a native
question-to-parent or `AskUserQuestion` mechanism if available and to wait for the
parent's answer. The child reported that neither mechanism was available and used
no tools. This is evidence that the installed native Agent surface does not expose
the required ask/answer path to this child configuration; both native modes must
remain outside the communication-safe allowlist until a different, demonstrated
native mechanism is found.

## MCP tool-return behavior and wait bound

A local stdio MCP server exposed one `sleep_return` tool. It returned a marker
after sleeping for the requested duration. Claude loaded it as
`mcp__probe__sleep_return` in a fresh `claude -p` session.

* A two-second call returned the marker as a normal MCP tool result and Claude
  continued in the same turn.
* A 35-second call also returned normally. Claude emitted one `tool_progress`
  heartbeat at 30 seconds, then returned the marker at roughly 35 seconds. The
  complete host process took roughly 37 seconds.

No short host timeout was observed. This probe did not establish an upper host
timeout. The installed 2.1.278 binary also contains the effective timeout policy: a
per-server `timeout` (when at least 1000 ms), then `MCP_TOOL_TIMEOUT`, then a
default hard call timeout of `100000000` ms (about 27.8 hours), capped at the
32-bit millisecond maximum. Separately, a stdio server has a default idle
timeout of `1800000` ms (30 minutes), overridable by
`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`; HTTP/SSE defaults to five minutes. The idle
watchdog resets on a server progress notification, while the hard call timeout
does not. The 35-second result is therefore consistent with the host defaults;
the design's MCP wait deadline must remain much shorter and independent.

The MCP `PreToolUse` hook ran before the tool process call. Its payload had the
same `session_id` and included the MCP server identity, so the launch guard can
recognize the managed tool by its namespaced tool name and inspect its arguments.

## Fresh-session MCP configuration reload

Using `CLAUDE_CONFIG_DIR` pointed at a disposable config directory, the first
fresh `claude mcp list` process reported one user-scope stdio server. After the
config was edited to add a second server, a second fresh `claude mcp list` process
reported both servers as connected. This confirms that fresh CLI processes read
updated user-scope MCP configuration.

The installed user config currently contains a `pitwall-channel` stdio entry, but
the isolated reload probe deliberately used a local server so it could test config
reload without starting provider or production services. A running Claude session
should be treated as having its already loaded MCP inventory until the host's
reload mechanism is explicitly tested.

## Subagent tool calls reach PreToolUse

On 2026-09-22, a `claude -p --model haiku` session was run with Claude Code
2.1.278 and a disposable settings file whose `PreToolUse` and `PostToolUse`
hooks appended each payload to logs. The parent was asked to call `Agent`
(`subagent_type: general-purpose`) and the child was instructed to run the Bash
command `printf probe`.

The hooks observed, in order:

```text
PRE  Agent  session=eb44b301…  agent_id=(absent)
PRE  Bash   session=eb44b301…  agent_id=af32aa30f45ccd9e9  agent_type=general-purpose
POST Bash   tool_response={"stdout": "probe", ...}
```

The subagent's own `Bash` call therefore passes through `PreToolUse` under the
parent `session_id`, with the `agent_id` identifying the subagent. Consequence
for the launch guard: a native research agent is safe to allow in a
routing-active session, because any shim or provider launch it attempts is
itself a tool call the guard sees and can deny under the same session marker.

A separate observation from the same probe: a `PostToolUse` payload for an MCP
tool (`mcp__pitwall-channel__inbox`) carries `tool_response` as a JSON string
rather than an object. The managed channel handles that shape separately; this
section only records the host behavior.

## What this baseline did not establish

At the time of these probes, the installed component had no `dispatch_and_wait`
or `answer_and_wait` tool. These probes therefore did not establish a live child
ask delivered to the parent, a Pitwall routing marker and pre-execution guard,
or durable reattachment after an MCP restart. The later installed-user and
active-workstation results in
[isolated-consumer-acceptance-evidence.md](isolated-consumer-acceptance-evidence.md)
cover those cases, including a real Claude parent/child exchange and a killed
MCP process followed by reattachment.
