"""Loopback HTTP fakes for probe and Pitwall client tests (no network)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class LoopbackServer:
    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.request_bodies: list[bytes] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802  # reason: http.server API
                server.requests.append((self.path, dict(self.headers.items())))
                server.request_bodies.append(b"")
                self._respond()

            def do_POST(self) -> None:  # noqa: N802  # reason: http.server API
                server.requests.append((self.path, dict(self.headers.items())))
                length = int(self.headers.get("Content-Length", "0"))
                server.request_bodies.append(self.rfile.read(length))
                self._respond()

            def _respond(self) -> None:
                status, body = server.routes.get(
                    self.path.split("?", 1)[0], (404, {"error": "not found"})
                )
                payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_: object) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> LoopbackServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
