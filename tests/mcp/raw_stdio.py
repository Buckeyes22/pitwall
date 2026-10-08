"""Line-level stdio client for MCP era tests: exact JSON-RPC bytes, no SDK client."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
BROKER_ENV = {
    "PITWALL_MCP_TRANSPORT": "stdio",
    "RUNPOD_API_KEY": "test-key",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "REDIS_URL": "redis://localhost:6379/0",
}
MODERN_META: dict[str, Any] = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "pitwall-tests", "version": "0"},
}


class RawStdio:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self._next_id = 0

    @classmethod
    def start(cls, env: dict[str, str] | None = None) -> RawStdio:
        process = subprocess.Popen(
            [sys.executable, "-m", "pitwall.mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**BROKER_ENV, **(env or {}), "PATH": "/usr/bin:/bin"},
            cwd=ROOT,
        )
        return cls(process)

    def _write(self, message: dict[str, Any]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write((json.dumps(message) + "\n").encode())
        self._process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)
        assert self._process.stdout is not None
        while True:
            line = self._process.stdout.readline()
            assert line, "server closed stdout before answering"
            reply: dict[str, Any] = json.loads(line)
            if reply.get("id") == request_id:
                return reply

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=HANG_GUARD_SECS)
        finally:
            if self._process.poll() is None:
                self._process.kill()
                self._process.wait()
            if self._process.stdout is not None:
                self._process.stdout.close()
