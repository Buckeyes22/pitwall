"""Swapper-faithful OpenAI-compatible loopback server for tests."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

from tests.hang_guard import HANG_GUARD_SECS


class SwapperStub:
    """A mutable fake that models blocking cold loads and single-slot eviction."""

    def __init__(self, models: dict[str, dict[str, Any]]) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.request_bodies: list[bytes] = []
        #: Model ids in the order their cold loads began; a warm request adds nothing.
        self.cold_loads: list[str] = []
        self._models: dict[str, dict[str, Any]] = {}
        self._loaded: set[str] = set()
        self._starting: set[str] = set()
        self._lock = threading.RLock()
        for model_id, config in models.items():
            self.add_model(model_id, config)

        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802  # reason: http.server API
                body = b""
                stub._record(self.path, self.headers.items(), body)
                path = urlsplit(self.path).path
                if path == "/v1/models":
                    if not self._authorized():
                        return
                    with stub._lock:
                        data = [{"id": model_id, "object": "model"} for model_id in stub._models]
                    self._respond(200, {"object": "list", "data": data})
                    return
                if path == "/running":
                    if not self._authorized():
                        return
                    with stub._lock:
                        running = [
                            {
                                "model": model_id,
                                "state": ("starting" if model_id in stub._starting else "ready"),
                            }
                            for model_id in stub._models
                            if model_id in stub._starting or model_id in stub._loaded
                        ]
                    self._respond(200, {"running": running})
                    return
                self._respond(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802  # reason: http.server API
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                stub._record(self.path, self.headers.items(), body)
                path = urlsplit(self.path).path
                if path == "/v1/chat/completions":
                    self._complete(body)
                    return
                unload_prefix = "/api/models/unload/"
                if path.startswith(unload_prefix):
                    model_id = unquote(path[len(unload_prefix) :])
                    if not self._authorized(model_id):
                        return
                    with stub._lock:
                        if model_id not in stub._models:
                            self._respond(404, {"error": "model not found"})
                            return
                        stub._loaded.discard(model_id)
                        stub._starting.discard(model_id)
                    self._respond(200, {"model": model_id, "unloaded": True})
                    return
                self._respond(404, {"error": "not found"})

            def _authorized(self, model_id: str | None = None) -> bool:
                required_key = stub._required_key(model_id)
                if required_key is None:
                    return True
                if self.headers.get("Authorization") == f"Bearer {required_key}":
                    return True
                self._respond(401, {"error": "unauthorized"})
                return False

            def _complete(self, body: bytes) -> None:
                try:
                    payload = json.loads(body.decode("utf-8"))
                    model_id = payload["model"]
                    messages = payload.get("messages", [])
                    if not isinstance(model_id, str) or not isinstance(messages, list):
                        raise TypeError
                except UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError:
                    self._respond(400, {"error": "invalid request"})
                    return

                if not self._authorized(model_id):
                    return
                with stub._lock:
                    config = stub._models.get(model_id)
                    if config is None:
                        self._respond(404, {"error": "model not found"})
                        return
                    cold = model_id not in stub._loaded
                    if cold:
                        if bool(config["single_slot"]):
                            stub._loaded.clear()
                        stub._starting.add(model_id)
                        stub.cold_loads.append(model_id)
                    delay = float(config["first_request_delay"]) if cold else 0.0
                    first_request_gate = config["first_request_gate"] if cold else None
                    fail_start = bool(config["fail_start"]) if cold else False
                    fail_start_gate = config["fail_start_gate"] if fail_start else None

                if delay:
                    time.sleep(delay)
                if first_request_gate is not None:
                    # The cold load lasts until the test releases it, not for a wall-clock
                    # window, so observers never race the load.
                    first_request_gate.wait(timeout=HANG_GUARD_SECS)

                if fail_start:
                    with stub._lock:
                        stub._starting.discard(model_id)
                        stub._loaded.discard(model_id)
                    # Preserve the observable failed-start sequence: /running loses
                    # the model before the blocked completion receives its 500.
                    if fail_start_gate is not None:
                        fail_start_gate.wait(timeout=HANG_GUARD_SECS)
                    else:
                        time.sleep(0.025)
                    self._respond(500, {"error": "upstream command exited prematurely"})
                    return

                with stub._lock:
                    if model_id not in stub._models:
                        stub._starting.discard(model_id)
                        self._respond(404, {"error": "model not found"})
                        return
                    stub._starting.discard(model_id)
                    stub._loaded.add(model_id)

                prompt_chars = sum(
                    len(message.get("content", ""))
                    for message in messages
                    if isinstance(message, dict) and isinstance(message.get("content", ""), str)
                )
                self._respond(
                    200,
                    {
                        "id": "chatcmpl-stub",
                        "object": "chat.completion",
                        "model": model_id,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "pong"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": prompt_chars // 4,
                            "completion_tokens": 1,
                            "total_tokens": prompt_chars // 4 + 1,
                        },
                    },
                )

            def _respond(self, status: int, body: dict[str, Any]) -> None:
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_: object) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        if not isinstance(host, str) or not isinstance(port, int):
            raise RuntimeError("stub did not bind a TCP address")
        return f"http://{host}:{port}"

    def add_model(self, model_id: str, config: dict[str, Any] | None = None) -> None:
        values = config or {}
        first_request_delay = float(values.get("first_request_delay", 0.0))
        normalized = {
            "first_request_delay": first_request_delay,
            "fail_start": bool(values.get("fail_start", False)),
            "fail_start_gate": values.get("fail_start_gate"),
            "first_request_gate": values.get("first_request_gate"),
            "require_key": values.get("require_key"),
            "single_slot": bool(values.get("single_slot", False)),
        }
        if first_request_delay < 0:
            raise ValueError("first_request_delay must be non-negative")
        required_key = normalized["require_key"]
        if required_key is not None and not isinstance(required_key, str):
            raise TypeError("require_key must be a string or None")
        for gate_name in ("fail_start_gate", "first_request_gate"):
            gate = normalized[gate_name]
            if gate is not None and not isinstance(gate, threading.Event):
                raise TypeError(f"{gate_name} must be a threading.Event or None")
        with self._lock:
            self._models[model_id] = normalized
            self._loaded.discard(model_id)
            self._starting.discard(model_id)

    def remove_model(self, model_id: str) -> None:
        with self._lock:
            self._models.pop(model_id, None)
            self._loaded.discard(model_id)
            self._starting.discard(model_id)

    def _record(
        self,
        path: str,
        headers: Any,
        body: bytes,
    ) -> None:
        with self._lock:
            self.requests.append((path, dict(headers)))
            self.request_bodies.append(body)

    def _required_key(self, model_id: str | None) -> str | None:
        with self._lock:
            if model_id is not None:
                config = self._models.get(model_id)
                return None if config is None else config["require_key"]
            keys = {
                config["require_key"]
                for config in self._models.values()
                if config["require_key"] is not None
            }
        if len(keys) > 1:
            raise ValueError("all model catalog keys must match")
        return next(iter(keys), None)

    def __enter__(self) -> SwapperStub:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=HANG_GUARD_SECS)
