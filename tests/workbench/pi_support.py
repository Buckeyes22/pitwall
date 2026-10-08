"""Helpers shared by the live (real ``pi`` binary) workbench tests.

Live cases never call a real model: a local fixture HTTP server plays the provider.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests.hang_guard import HANG_GUARD_SECS

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PROFILES = REPO_ROOT / "config/workbench.example.json"


def pi_modules() -> Path | None:
    """The ``node_modules`` directory named by ``PITWALL_PI_MODULES`` (Pi and its backend), if any."""
    configured = os.environ.get("PITWALL_PI_MODULES")
    return Path(configured) if configured else None


def pinned_pi() -> str | None:
    """Path of a pinned Pi CLI script, or None so live cases skip with a reason."""
    configured = os.environ.get("PITWALL_WORKBENCH_PI_BIN")
    if configured and Path(configured).is_file():
        return configured
    modules = pi_modules()
    if modules is not None:
        cli = modules / "@earendil-works/pi-coding-agent/dist/bundle/cli.js"
        if cli.is_file():
            return str(cli)
    found = shutil.which("pi")
    return str(Path(found).resolve()) if found else None


def tintin_extension() -> str | None:
    """Path of the pinned ``@tintinweb/pi-subagents`` entry point, when installed."""
    modules = pi_modules()
    if modules is None:
        return None
    entry = modules / "@tintinweb/pi-subagents/dist/index.js"
    return str(entry) if entry.is_file() else None


requires_pi = pytest.mark.skipif(
    pinned_pi() is None or shutil.which("node") is None,
    reason="needs node and a pinned pi binary",
)


class RpcClient:
    """Drive a ``pi --mode rpc`` child: newline-delimited JSON in, events out."""

    def __init__(self, child: subprocess.Popen[bytes]) -> None:
        assert child.stdin is not None
        assert child.stdout is not None
        assert child.stderr is not None
        self.child = child
        self.events: list[dict[str, Any]] = []
        self.errors = ""
        self._sequence = 0
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(target=self._read_events, daemon=True),
            threading.Thread(target=self._read_errors, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _read_events(self) -> None:
        assert self.child.stdout is not None
        for line in self.child.stdout:
            if line.strip():
                event = json.loads(line)
                with self._lock:
                    self.events.append(event)

    def _read_errors(self) -> None:
        assert self.child.stderr is not None
        for line in self.child.stderr:
            self.errors += line.decode(errors="replace")

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events)

    def send(self, message: dict[str, Any]) -> None:
        assert self.child.stdin is not None
        self.child.stdin.write(json.dumps(message).encode() + b"\n")
        self.child.stdin.flush()

    def until(
        self, check: Callable[[], bool], timeout: float = 20.0, what: str = "condition"
    ) -> None:
        deadline = time.monotonic() + timeout
        while not check():
            if time.monotonic() > deadline:
                raise TimeoutError(f"timed out waiting for {what}: {self.errors[-2000:]}")
            if self.child.poll() is not None and not check():
                time.sleep(0.05)
                if check():
                    return
                raise RuntimeError(
                    f"pi exited {self.child.returncode} before {what}: {self.errors[-2000:]}"
                )
            time.sleep(0.02)

    def settled_count(self) -> int:
        return sum(1 for event in self.snapshot() if event.get("type") == "agent_settled")

    def command(self, kind: str, **extra: Any) -> dict[str, Any]:
        self._sequence += 1
        request_id = str(self._sequence)
        self.send({"type": kind, "id": request_id, **extra})

        def reply() -> dict[str, Any] | None:
            for event in self.snapshot():
                if event.get("type") == "response" and event.get("id") == request_id:
                    return event
            return None

        self.until(lambda: reply() is not None, what=f"response to {kind}")
        response = reply()
        assert response is not None
        if not response.get("success"):
            raise RuntimeError(f"pi rejected {kind}: {response}")
        return response

    def close(self) -> None:
        if self.child.poll() is None:
            try:
                if self.child.stdin:
                    self.child.stdin.close()
            except OSError:
                pass
            self.child.terminate()
            try:
                self.child.wait(timeout=HANG_GUARD_SECS)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
        for thread in self._threads:
            thread.join(timeout=HANG_GUARD_SECS)
        for stream in (self.child.stdin, self.child.stdout, self.child.stderr):
            if stream and not stream.closed:
                stream.close()


class FixtureServer:
    """A loopback HTTP server whose ``respond`` callback plays the model provider."""

    def __init__(self, respond: Callable[[BaseHTTPRequestHandler, str, bytes], None]) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length) if length else b""
                respond(self, self.path, body)

            def do_GET(self) -> None:
                respond(self, self.path, b"")

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port: int = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=HANG_GUARD_SECS)

    def __enter__(self) -> FixtureServer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def sse_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("connection", "close")
    handler.end_headers()


def sse_write(handler: BaseHTTPRequestHandler, data: str) -> None:
    handler.wfile.write(data.encode())
    handler.wfile.flush()


def chat_chunk(
    delta: dict[str, Any], finish_reason: str | None = None, model: str = "fixture-model"
) -> str:
    """One OpenAI chat-completions SSE data line."""
    chunk = {
        "id": "fixture",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def chat_usage(prompt: int = 100, completion: int = 8) -> str:
    chunk = {
        "id": "fixture",
        "object": "chat.completion.chunk",
        "choices": [],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }
    return f"data: {json.dumps(chunk)}\n\n"
