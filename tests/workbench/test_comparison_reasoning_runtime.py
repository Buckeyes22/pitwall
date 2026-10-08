"""Reasoning fixture on the pinned backends, translated from packages/pi-workbench/tests/comparison-reasoning-runtime.test.ts.

The pinned Pi runtime runs against a loopback provider that scripts one foreground Explore child.
Candidate B skips when its private fixture is missing (``PITWALL_WORKBENCH_CANDIDATE_B_EXTENSION``).
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.comparison.reasoning_fixture import (
    write_comparison_child_reasoning_fixture,
)
from pitwall.workbench.launcher import PiLaunchOptions, launch_pi
from pitwall.workbench.profile import compile_profile
from tests.hang_guard import HANG_GUARD_SECS
from tests.workbench.pi_support import (
    FixtureServer,
    RpcClient,
    chat_chunk,
    pinned_pi,
    requires_pi,
    sse_headers,
    sse_write,
    tintin_extension,
)

MARKER = "COMPARISON_REASONING_CHILD_MARKER"
B_EXTENSION = os.environ.get(
    "PITWALL_WORKBENCH_CANDIDATE_B_EXTENSION",
    str(
        Path.home()
        / ".local/state/pitwall-readiness/pi-workbench/backend-review/fixture/node_modules/pi-subagents/index.ts"
    ),
)
C_EXTENSION = tintin_extension()

Json = dict[str, Any]


def tool_names(payload: Json) -> list[str]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return []
    names = [(tool.get("function") or {}).get("name") for tool in tools if isinstance(tool, dict)]
    return [name for name in names if isinstance(name, str)]


def has_tool_result(payload: Json) -> bool:
    messages = payload.get("messages")
    return isinstance(messages, list) and any(m.get("role") == "tool" for m in messages)


def stream_response(handler: BaseHTTPRequestHandler, response: Json) -> None:
    sse_headers(handler)
    tool = response.get("tool")
    if tool:
        call = {
            "index": 0,
            "id": tool["id"],
            "type": "function",
            "function": {"name": tool["name"], "arguments": json.dumps(tool["arguments"])},
        }
        sse_write(handler, chat_chunk({"role": "assistant", "tool_calls": [call]}))
        sse_write(handler, chat_chunk({}, "tool_calls"))
    else:
        sse_write(handler, chat_chunk({"role": "assistant", "content": response["text"]}))
        sse_write(handler, chat_chunk({}, "stop"))
    usage = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
    sse_write(
        handler,
        "data: "
        + json.dumps(
            {
                "id": "comparison-reasoning",
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": usage,
            }
        )
        + "\n\n",
    )
    sse_write(handler, "data: [DONE]\n\n")


def read_jsonl_files(root: Path) -> list[tuple[Path, str]]:
    return [(path, path.read_text()) for path in sorted(root.rglob("*.jsonl")) if path.is_file()]


def is_persisted_child_session(path: Path, content: str) -> bool:
    if re.search(r"[\\/]run-\d+[\\/]session\.jsonl$", str(path)):
        return True
    first = next((line for line in content.split("\n") if line), None)
    try:
        return bool(first and json.loads(first).get("parentSession"))
    except ValueError, AttributeError:
        # A malformed first record is not a child session header.
        return False


def child_session_read_marker(sessions: list[tuple[Path, str]]) -> bool:
    for path, content in sessions:
        if not is_persisted_child_session(path, content):
            continue
        records = [json.loads(line) for line in content.split("\n") if line]
        read_calls: set[str] = set()
        for record in records:
            message = record.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            for part in message.get("content") or []:
                args = part.get("arguments") if isinstance(part, dict) else None
                target = (
                    args.get("path") or args.get("file_path") or args.get("filePath")
                    if isinstance(args, dict)
                    else None
                )
                if (
                    isinstance(part, dict)
                    and part.get("type") == "toolCall"
                    and part.get("name") == "read"
                    and isinstance(part.get("id"), str)
                    and target == "child-probe.txt"
                ):
                    read_calls.add(part["id"])
        for record in records:
            message = record.get("message") or {}
            if (
                message.get("role") == "toolResult"
                and message.get("toolName") == "read"
                and message.get("toolCallId") in read_calls
                and message.get("isError") is not True
                and MARKER in json.dumps(message.get("content"))
            ):
                return True
    return False


def last_two_content(messages: list[Any]) -> Any:
    """``messages.at(-2)?.content ?? messages.at(-1)?.content ?? null`` from the source evidence."""
    for index in (-2, -1):
        if len(messages) >= -index and isinstance(messages[index], dict):
            content = messages[index].get("content")
            if content is not None:
                return content
    return None


def thinking_values(value: object, output: list[str] | None = None) -> list[str]:
    output = [] if output is None else output
    if isinstance(value, list):
        for item in value:
            thinking_values(item, output)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "thinking" and isinstance(item, str):
                output.append(item)
            thinking_values(item, output)
    return output


def run_reasoning_fixture(extension: str, root: Path) -> dict[str, Any]:
    cwd = root / "fixture"
    cwd.mkdir(parents=True)
    (root / "home").mkdir()
    (cwd / "child-probe.txt").write_text(MARKER)
    write_comparison_child_reasoning_fixture(cwd, "low")
    payloads: list[Json] = []

    def respond(handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        try:
            payload = json.loads(body)
        except ValueError:
            # A body that is not JSON is a client error, as the loopback provider would answer.
            handler.send_response(400)
            handler.end_headers()
            return
        payloads.append(payload)
        names = tool_names(payload)
        agent_tool = next((name for name in names if re.search("agent|subagent", name, re.I)), None)
        read_tool = next((name for name in names if name == "read"), None)
        if agent_tool and not has_tool_result(payload):
            arguments: Json = (
                {
                    "agent": "Explore",
                    "task": f"Read child-probe.txt and return exactly {MARKER}.",
                    "async": False,
                }
                if agent_tool == "subagent"
                else {
                    "prompt": f"Read child-probe.txt and return exactly {MARKER}.",
                    "description": "Controlled read-only reasoning fixture",
                    "subagent_type": "Explore",
                    "run_in_background": False,
                }
            )
            stream_response(
                handler,
                {"tool": {"id": "comparison-child", "name": agent_tool, "arguments": arguments}},
            )
        elif read_tool and not agent_tool and not has_tool_result(payload):
            stream_response(
                handler,
                {
                    "tool": {
                        "id": "comparison-read",
                        "name": read_tool,
                        "arguments": {"path": "child-probe.txt"},
                    }
                },
            )
        elif read_tool and not agent_tool and has_tool_result(payload):
            stream_response(handler, {"text": MARKER})
        else:
            stream_response(handler, {"text": "Parent received the controlled child result."})

    with FixtureServer(respond) as server:
        agent_dir = root / "agent"
        profile = compile_profile(
            "fixture",
            {
                "provider": "fixture",
                "modelId": "fixture-model",
                "endpoint": server.url,
                "api": "openai-completions",
                "keyless": "dummy",
                "reasoningLevel": "low",
                "servedContextTokens": 32768,
                "maxCompletionTokens": 4096,
                "resourceGroup": "comparison-reasoning-fixture",
                "allowProviderFallback": False,
            },
            agent_dir,
        )
        client = RpcClient(
            launch_pi(
                PiLaunchOptions(
                    cwd=cwd,
                    profile=profile,
                    pi_bin=pinned_pi(),
                    extension=extension,
                    env={"HOME": str(root / "home")},
                    runtime_dir=root / "runtime",
                )
            )
        )
        try:
            client.command("set_auto_retry", enabled=False)
            client.command(
                "prompt",
                message="Use the foreground Agent tool with subagent_type Explore. Wait for the child to finish, then report its exact marker.",
            )
            client.until(
                lambda: client.settled_count() >= 1,
                timeout=HANG_GUARD_SECS,
                what="the parent agent to settle",
            )
            sessions = read_jsonl_files(agent_dir)
            child_sessions = [s for s in sessions if is_persisted_child_session(*s)]
            levels = [
                match
                for _, content in child_sessions
                for match in re.findall(
                    r'"type":"thinking_level_change"[^\n]*?"thinkingLevel":"([^"]+)"', content
                )
            ]
            events = client.snapshot()
            return {
                "payloads": payloads,
                "childThinkingLevels": list(dict.fromkeys([*levels, *thinking_values(events)])),
                "persistedChildThinkingLevels": list(dict.fromkeys(levels)),
                "persistedChildReadMarker": child_session_read_marker(sessions),
                "events": events,
            }
        finally:
            client.close()


@pytest.mark.live
@pytest.mark.parity
@requires_pi
@pytest.mark.parametrize(
    ("name", "extension"),
    [("B-nicobailon", B_EXTENSION), ("C-tintin", C_EXTENSION)],
    ids=["B-nicobailon", "C-tintin"],
)
def test_pinned_candidate_consumes_the_controlled_low_reasoning_fixture(
    name: str, extension: str | None, tmp_path: Path
) -> None:
    """Source: comparison-reasoning-runtime.test.ts 'pinned <candidate> consumes the controlled low reasoning fixture (skip if unavailable)'."""
    if extension is None or not Path(extension).exists():
        pytest.skip(f"candidate {name} extension is unavailable: {extension}")
    result = run_reasoning_fixture(extension, tmp_path)
    child_payloads = [
        payload
        for payload in result["payloads"]
        if "read" in tool_names(payload)
        and not any(re.search("agent|subagent", n, re.I) for n in tool_names(payload))
    ]
    assert len(child_payloads) > 0
    assert result["persistedChildReadMarker"] is True
    assert "low" in result["persistedChildThinkingLevels"]
    assert "low" in result["childThinkingLevels"]
    assert "high" not in result["childThinkingLevels"]
    assert MARKER in json.dumps(result["events"])
    evidence_dir = os.environ.get("PITWALL_WORKBENCH_REASONING_EVIDENCE_DIR")
    if evidence_dir:
        target = Path(evidence_dir)
        target.mkdir(parents=True, mode=0o700, exist_ok=True)
        (target / f"{name}.json").write_text(
            json.dumps(
                {
                    "candidate": name,
                    "configuredParentReasoning": "low",
                    "childPayloadCount": len(child_payloads),
                    "childPayloadTools": [tool_names(p) for p in child_payloads],
                    "persistedChildThinkingLevels": result["persistedChildThinkingLevels"],
                    "persistedChildReadMarker": result["persistedChildReadMarker"],
                    "childThinkingLevels": result["childThinkingLevels"],
                    "markerObserved": MARKER in json.dumps(result["events"]),
                    "childResultEvidence": [
                        last_two_content(event["messages"])
                        for event in result["events"]
                        if event.get("type") == "agent_end"
                        and isinstance(event.get("messages"), list)
                    ],
                },
                indent=2,
            )
            + "\n"
        )
