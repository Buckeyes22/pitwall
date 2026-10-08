"""Minimal stdio MCP client for tests: one reader thread, requests matched by id."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]


class McpTestClient:
    def __init__(self, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            [
                str(PITWALL),
                "agents",
                "mcp",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self._next_id = 0
        self._responses: dict[Any, dict[str, Any]] = {}
        self.notifications: list[dict[str, Any]] = []
        self.raw_lines: list[bytes] = []
        self._cond = threading.Condition()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            with self._cond:
                self.raw_lines.append(line)
                message = json.loads(line)
                if "id" in message and ("result" in message or "error" in message):
                    self._responses[message["id"]] = message
                else:
                    self.notifications.append(message)
                self._cond.notify_all()

    def send(
        self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False
    ) -> int | None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        request_id = None
        if not notify:
            self._next_id += 1
            request_id = self._next_id
            message["id"] = request_id
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()
        return request_id

    def wait(self, request_id: Any, timeout: float = HANG_GUARD_SECS) -> dict[str, Any]:
        with self._cond:
            if not self._cond.wait_for(lambda: request_id in self._responses, timeout=timeout):
                raise TimeoutError(f"no response to request {request_id}")
            return self._responses.pop(request_id)

    def wait_for_notification(self, method: str, timeout: float = HANG_GUARD_SECS) -> None:
        with self._cond:
            if not self._cond.wait_for(
                lambda: any(n.get("method") == method for n in self.notifications), timeout=timeout
            ):
                raise TimeoutError(f"no {method} notification")

    def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = HANG_GUARD_SECS
    ) -> dict[str, Any]:
        return self.wait(self.send(method, params), timeout)

    def initialize(self, version: str = "2025-06-18") -> dict[str, Any]:
        reply = self.request(
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        )
        self.send("notifications/initialized", notify=True)
        return reply

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout: float = HANG_GUARD_SECS,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name, "arguments": arguments}
        if meta is not None:
            params["_meta"] = meta
        return self.request("tools/call", params, timeout)

    def finish_input(self, timeout: float = HANG_GUARD_SECS) -> None:
        """Close stdin, then wait until the server exits and every output line has been read."""
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=timeout)
        self._reader.join(timeout=timeout)
        if self._reader.is_alive():
            raise TimeoutError("server output did not reach end of file")

    def close(self) -> int:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            returncode = self.process.wait(timeout=HANG_GUARD_SECS)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            returncode = self.process.wait(timeout=HANG_GUARD_SECS)
        finally:
            # Popen owns all three pipe objects. Explicitly closing them keeps
            # the helper warning-free and wakes the daemon reader after the
            # server has finished its cancellation cleanup.
            for stream in (self.process.stdout, self.process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
            self._reader.join(timeout=HANG_GUARD_SECS)
        return returncode
