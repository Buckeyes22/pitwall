"""Validation scenarios, translated from packages/pi-workbench/tests/validation-scenarios.test.ts.

Each case drives the pinned ``pi`` runtime in RPC mode against a loopback fixture provider that
scripts the tool calls; no real model is ever contacted.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.profile import compile_profile, configure_provider_profile
from tests.workbench.pi_support import (
    FixtureServer,
    RpcClient,
    chat_chunk,
    chat_usage,
    pinned_pi,
    requires_pi,
    sse_headers,
    sse_write,
)

Payload = dict[str, Any]


def send_tool(handler: BaseHTTPRequestHandler, call: dict[str, Any]) -> None:
    sse_headers(handler)
    sse_write(handler, chat_chunk({"role": "assistant", "tool_calls": [call]}))
    sse_write(handler, chat_chunk({}, "tool_calls"))
    sse_write(handler, chat_usage(100, 8))
    sse_write(handler, "data: [DONE]\n\n")


def send_text(handler: BaseHTTPRequestHandler, text: str) -> None:
    sse_headers(handler)
    sse_write(handler, chat_chunk({"role": "assistant", "content": text}))
    sse_write(handler, chat_chunk({}, "stop"))
    sse_write(handler, chat_usage(100, 8))
    sse_write(handler, "data: [DONE]\n\n")


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": 0,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def latest_user_text(payload: Payload) -> str:
    users = [m for m in payload.get("messages", []) if m.get("role") == "user"]
    last = users[-1].get("content") if users else None
    return last if isinstance(last, str) else json.dumps(last if last is not None else "")


def messages_since_latest_user(payload: Payload) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = payload.get("messages", [])
    index = -1
    for position in range(len(messages) - 1, -1, -1):
        if messages[position].get("role") == "user":
            index = position
            break
    return messages if index < 0 else messages[index + 1 :]


def unique_calls(requests: list[Payload]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for payload in requests:
        for message in payload.get("messages", []):
            if message.get("role") == "assistant":
                for call in message.get("tool_calls") or []:
                    seen[call["id"]] = call
    return list(seen.values())


def unique_results(requests: list[Payload]) -> dict[str, dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for payload in requests:
        for message in payload.get("messages", []):
            if message.get("role") == "tool":
                seen[message["tool_call_id"]] = message
    return seen


@dataclass
class Runtime:
    cwd: Path
    client: RpcClient

    def prompt(self, message: str, settled_count: int) -> None:
        self.client.command("prompt", message=message)
        self.client.until(
            lambda: self.client.settled_count() >= settled_count,
            timeout=15.0,
            what=f"settled event {settled_count}",
        )


@contextmanager
def launch_fixture(root: Path, port: int) -> Iterator[Runtime]:
    cwd = root / "repository"
    subprocess.run(["git", "init", "-q", str(cwd)], check=True)
    compiled = compile_profile(
        "fixture",
        {
            "provider": "fixture",
            "modelId": "fixture-model",
            "endpoint": f"http://127.0.0.1:{port}/v1",
            "api": "openai-completions",
            "keyless": "dummy",
            "reasoningLevel": "off",
            "servedContextTokens": 32768,
            "maxCompletionTokens": 4096,
            "resourceGroup": f"validation-{port}",
            "allowProviderFallback": False,
        },
        root / "agent",
    )
    policy = configure_provider_profile(compiled, cwd)
    client = RpcClient(
        launch_pi(
            PiLaunchOptions(
                cwd=cwd,
                profile=compiled,
                pi_bin=pinned_pi(),
                env=policy.env,
                extension=extension_path("extension"),
                runtime_dir=root / "runtime",
            )
        )
    )
    try:
        yield Runtime(cwd, client)
    finally:
        client.close()


def extension_errors(runtime: Runtime) -> list[dict[str, Any]]:
    return [e for e in runtime.client.snapshot() if e.get("type") == "extension_error"]


@pytest.mark.live
@pytest.mark.parity
@requires_pi
def test_t01_pinned_runtime_creates_reads_edits_and_rereads_across_several_turns(
    tmp_path: Path,
) -> None:
    """Source: validation-scenarios.test.ts 'T01 pinned runtime creates, reads, edits, and rereads across several turns'."""
    requests: list[Payload] = []

    def respond(handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        payload = json.loads(body)
        requests.append(payload)
        prompt = latest_user_text(payload)
        phase = next(
            (
                marker
                for marker in ("T01_CREATE", "T01_READ_INITIAL", "T01_EDIT", "T01_READ_FINAL")
                if marker in prompt
            ),
            "unknown",
        )
        tools = [m for m in messages_since_latest_user(payload) if m.get("role") == "tool"]
        if phase == "T01_CREATE" and not tools:
            send_tool(
                handler,
                tool_call(
                    "t01-write",
                    "write",
                    {"path": "fixture.mjs", "content": "export function value() { return 1; }\n"},
                ),
            )
        elif phase == "T01_READ_INITIAL" and not tools:
            send_tool(handler, tool_call("t01-read-initial", "read", {"path": "fixture.mjs"}))
        elif phase == "T01_EDIT" and not tools:
            send_tool(
                handler,
                tool_call(
                    "t01-edit",
                    "edit",
                    {"path": "fixture.mjs", "oldText": "return 1", "newText": "return 2"},
                ),
            )
        elif phase == "T01_READ_FINAL" and not tools:
            send_tool(handler, tool_call("t01-read-final", "read", {"path": "fixture.mjs"}))
        else:
            send_text(handler, f"completed {phase} with {len(tools)} tool result(s)")

    with FixtureServer(respond) as server, launch_fixture(tmp_path, server.port) as runtime:
        runtime.client.command("set_auto_retry", enabled=False)
        runtime.prompt("T01_CREATE: create fixture.mjs with value() returning 1.", 1)
        runtime.prompt("T01_READ_INITIAL: read fixture.mjs and report its exact contents.", 2)
        runtime.prompt("T01_EDIT: change value() to return 2.", 3)
        runtime.prompt("T01_READ_FINAL: read fixture.mjs again and report its exact contents.", 4)
        assert (runtime.cwd / "fixture.mjs").read_text() == (
            "export function value() { return 2; }\n"
        )
        calls = unique_calls(requests)
        results = unique_results(requests)
        assert [call["function"]["name"] for call in calls] == ["write", "read", "edit", "read"]
        for call in calls:
            assert call["id"] in results
        assert "export function value() { return 1; }\\n" in json.dumps(
            results["t01-read-initial"]["content"]
        )
        assert "export function value() { return 2; }\\n" in json.dumps(
            results["t01-read-final"]["content"]
        )
        for result in results.values():
            assert result.get("isError") is not True
        events = runtime.client.snapshot()
        for name in ("write", "read", "edit"):
            assert any(
                e.get("type") == "tool_execution_end"
                and e.get("toolName") == name
                and e.get("isError") is not True
                for e in events
            )
        assert any("return 1" in json.dumps(payload) for payload in requests)
        assert any("return 2" in json.dumps(payload) for payload in requests)
        assert extension_errors(runtime) == []


@pytest.mark.live
@pytest.mark.parity
@requires_pi
def test_t02_pinned_runtime_adds_a_new_function_and_tests_observes_failure_then_repairs_it(
    tmp_path: Path,
) -> None:
    """Source: validation-scenarios.test.ts 'T02 pinned runtime adds a new function and tests, observes failure, then repairs it'."""
    requests: list[Payload] = []
    test_source = (
        "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { multiply } from './multiply.mjs';\n"
        "test('multiplies', () => assert.equal(multiply(2, 3), 6));\n"
    )

    def respond(handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        payload = json.loads(body)
        requests.append(payload)
        prompt = latest_user_text(payload)
        phase = (
            "add" if "T02_ADD" in prompt else "repair" if "T02_FAIL_REPAIR" in prompt else "other"
        )
        tools = [m for m in messages_since_latest_user(payload) if m.get("role") == "tool"]
        if phase == "add" and len(tools) == 0:
            send_tool(
                handler,
                tool_call(
                    "t02-write-function",
                    "write",
                    {
                        "path": "multiply.mjs",
                        "content": "export function multiply(a, b) { return a + b; }\n",
                    },
                ),
            )
        elif phase == "add" and len(tools) == 1:
            send_tool(
                handler,
                tool_call(
                    "t02-write-test",
                    "write",
                    {"path": "multiply.test.mjs", "content": test_source},
                ),
            )
        elif phase == "repair" and len(tools) == 0:
            send_tool(
                handler,
                tool_call("t02-test-failure", "bash", {"command": "node --test multiply.test.mjs"}),
            )
        elif phase == "repair" and len(tools) == 1:
            send_tool(
                handler,
                tool_call(
                    "t02-repair-edit",
                    "edit",
                    {"path": "multiply.mjs", "oldText": "return a + b", "newText": "return a * b"},
                ),
            )
        elif phase == "repair" and len(tools) == 2:
            send_tool(
                handler,
                tool_call("t02-test-success", "bash", {"command": "node --test multiply.test.mjs"}),
            )
        else:
            send_text(
                handler,
                "Added a new multiply function and its test."
                if phase == "add"
                else "Observed the failing test, repaired multiply, and verified the passing test.",
            )

    with FixtureServer(respond) as server, launch_fixture(tmp_path, server.port) as runtime:
        runtime.client.command("set_auto_retry", enabled=False)
        runtime.prompt("T02_ADD: add a NEW multiply function and a test for it.", 1)
        runtime.prompt(
            "T02_FAIL_REPAIR: run the new test, diagnose the failure, repair the implementation, "
            "and rerun it.",
            2,
        )
        assert "return a * b" in (runtime.cwd / "multiply.mjs").read_text()
        assert "multiply(2, 3), 6" in (runtime.cwd / "multiply.test.mjs").read_text()
        direct = subprocess.run(
            ["node", "--test", "multiply.test.mjs"],
            cwd=runtime.cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "pass 1" in direct.stdout
        calls = unique_calls(requests)
        results = unique_results(requests)
        assert [call["function"]["name"] for call in calls] == [
            "write",
            "write",
            "bash",
            "edit",
            "bash",
        ]
        for call in calls:
            assert call["id"] in results
        failure = results["t02-test-failure"]
        success = results["t02-test-success"]
        assert success.get("isError") is not True
        assert re.search(
            r"not ok 1|ERR_ASSERTION|expected.*6.*actual.*5",
            json.dumps(failure["content"]),
            re.IGNORECASE,
        )
        assert re.search(r"# pass 1", json.dumps(success["content"]))
        assert re.search(r"# fail 0", json.dumps(success["content"]))
        assert any(
            e.get("type") == "tool_execution_end"
            and e.get("toolName") == "bash"
            and e.get("isError") is True
            for e in runtime.client.snapshot()
        )
        assert extension_errors(runtime) == []
