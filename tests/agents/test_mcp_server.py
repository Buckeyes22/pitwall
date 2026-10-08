"""Protocol behavior of the stdio MCP server (plan Task 13)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from pitwall.agents import (
    mcp_server,
)
from pitwall.agents.mcp_server import (
    ChannelServer,
    ToolCancelled,
    ToolError,
    server_role,
)
from tests.agents.mcp_test_client import (
    McpTestClient,
)

ROOT = Path(__file__).resolve().parents[2]


def _env(root: Path, **extra: str) -> dict[str, str]:
    return {
        "HOME": str(root),
        "XDG_STATE_HOME": str(root / "state"),
        "PATH": os.environ.get("PATH", ""),
        **extra,
    }


class RoleTests(unittest.TestCase):
    def test_role_follows_the_channel_variable(self) -> None:
        self.assertEqual("orchestrator", server_role({}))
        self.assertEqual("orchestrator", server_role({"PITWALL_AGENTS_CHANNEL_DISPATCH_ID": ""}))
        self.assertEqual(
            "subagent",
            server_role(
                {"PITWALL_AGENTS_CHANNEL_DISPATCH_ID": "00000000-0000-4000-8000-000000000001"}
            ),
        )
        self.assertEqual(
            "misconfigured",
            server_role(
                {"PITWALL_AGENTS_CHANNEL_DISPATCH_ID": "${PITWALL_AGENTS_CHANNEL_DISPATCH_ID}"}
            ),
        )


class SubprocessProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.client = McpTestClient(_env(Path(self._temp.name)))

    def tearDown(self) -> None:
        self.client.close()
        self._temp.cleanup()

    def test_initialize_negotiates_and_ping_answers(self) -> None:
        reply = self.client.initialize("2025-06-18")["result"]
        self.assertEqual("2025-06-18", reply["protocolVersion"])
        self.assertEqual("pitwall-channel", reply["serverInfo"]["name"])
        self.assertIn("tools", reply["capabilities"])
        self.assertEqual({}, self.client.request("ping")["result"])

    def test_unknown_version_gets_the_latest_supported(self) -> None:
        self.assertEqual(
            "2025-11-25", self.client.initialize("1999-01-01")["result"]["protocolVersion"]
        )

    def test_errors_are_json_rpc_shaped_and_stdout_is_protocol_only(self) -> None:
        self.client.initialize()
        self.assertEqual(-32601, self.client.request("nope")["error"]["code"])
        self.assertEqual(
            -32602,
            self.client.request("tools/call", {"name": "missing", "arguments": {}})["error"][
                "code"
            ],
        )
        assert self.client.process.stdin is not None
        self.client.process.stdin.write(b"{not json\n")
        self.client.process.stdin.flush()
        self.assertEqual(
            -32700, self.client.wait(None)["error"]["code"]
        )  # parse errors carry id null
        for line in self.client.raw_lines:
            json.loads(line)  # every stdout line is a JSON-RPC message

    def test_eof_exits_cleanly(self) -> None:
        self.client.initialize()
        self.assertEqual(0, self.client.close())


class InProcessMechanicsTests(unittest.TestCase):
    """Worker threads, cancellation, and progress, with a registered stand-in tool."""

    def _serve(self, lines: list[dict]) -> tuple[ChannelServer, list[dict]]:
        read_fd, write_fd = os.pipe()
        stdin, feeder = os.fdopen(read_fd, "rb"), os.fdopen(write_fd, "wb")
        stdout = io.BytesIO()
        server = ChannelServer(
            {"PITWALL_AGENTS_CHANNEL_DISPATCH_ID": "00000000-0000-4000-8000-000000000001"},
            stdin,
            stdout,
        )
        started = threading.Event()

        def slow(arguments: dict, cancel: threading.Event, progress) -> dict:  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
            started.set()
            for _ in range(50):
                progress("still waiting")
                if cancel.wait(0.05):
                    raise ToolCancelled()
            return {"done": True}

        def refuse(arguments: dict, cancel: threading.Event, progress) -> dict:  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
            raise ToolError("refused on purpose")

        server.register({"name": "ask_orchestrator", "inputSchema": {"type": "object"}}, slow)
        server.register({"name": "read_steering", "inputSchema": {"type": "object"}}, refuse)
        thread = threading.Thread(target=server.serve)
        thread.start()
        for message in lines:
            feeder.write((json.dumps(message) + "\n").encode())
            feeder.flush()
            if message.get("id") == 1:
                started.wait(5)
        feeder.close()
        thread.join(10)
        output = stdout.getvalue()
        stdin.close()
        stdout.close()
        return server, [json.loads(line) for line in output.splitlines()]

    def test_cancelled_call_sends_no_response(self) -> None:
        _server, out = self._serve(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "ask_orchestrator",
                        "arguments": {},
                        "_meta": {"progressToken": "p1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
            ]
        )
        self.assertFalse(any(m.get("id") == 1 for m in out))
        self.assertTrue(
            any(
                m.get("method") == "notifications/progress" and m["params"]["progressToken"] == "p1"
                for m in out
            )
        )

    def test_tool_error_is_an_is_error_result(self) -> None:
        _server, out = self._serve(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "read_steering", "arguments": {}},
                },
            ]
        )
        reply = next(m for m in out if m.get("id") == 2)
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("refused on purpose", reply["result"]["content"][0]["text"])

    def test_module_opens_no_network_transport(self) -> None:
        source = Path(mcp_server.__file__).read_text(encoding="utf-8")
        for forbidden in ("import socket", "http.server", "urllib"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
