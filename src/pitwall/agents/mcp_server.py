"""Stdio MCP server for the orchestrator channel: ``pitwall agents mcp``.

Newline-delimited JSON-RPC 2.0 on stdin/stdout, standard library only. It never
opens a socket (spec §10). stdout carries protocol messages only; diagnostics go
to stderr. Role follows the environment (plan Decision 6): with a UUID in
PITWALL_AGENTS_CHANNEL_DISPATCH_ID it serves the subagent tools,
without one the orchestrator tools, and with anything else no tools.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from typing import Any, BinaryIO, Literal

from .channel import CHANNEL_DISPATCH_ENV

SERVER_NAME = "pitwall-channel"
LEGACY_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)
UNSUPPORTED_PROTOCOL_VERSION = -32022
CACHE_TTL_MS = 3_600_000
INSTRUCTIONS = "Orchestrator channel for Pitwall Agent Routing dispatches."
_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
_SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
_SUBSCRIPTION_KEY = "io.modelcontextprotocol/subscriptionId"
SUBAGENT_TOOLS = ("ask_orchestrator", "read_steering", "ack_steer")
ORCHESTRATOR_TOOLS = (
    "inbox",
    "answer_ask",
    "dispatch_and_wait",
    "answer_and_wait",
    "wait_dispatch",
    "steer_and_wait",
)
MAX_LINE_BYTES = 1024 * 1024
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

Role = Literal["subagent", "orchestrator", "misconfigured"]
ToolHandler = Callable[[dict[str, Any], threading.Event, Callable[[str], None]], dict[str, Any]]


CALLS_PER_SECOND = 5.0
CALL_BURST = 20


class _TokenBucket:
    """A refilling call budget so a runaway client cannot flood the channel's tools."""

    def __init__(self) -> None:
        self._tokens = float(CALL_BURST)
        self._stamp = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                float(CALL_BURST), self._tokens + (now - self._stamp) * CALLS_PER_SECOND
            )
            self._stamp = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


class ToolError(Exception):
    """A tool refused; the client receives an ``isError`` result, not a protocol error."""


class ToolCancelled(Exception):
    """The client cancelled the call; per MCP, no response is sent."""


def server_role(env: Mapping[str, str]) -> Role:
    raw = env.get(CHANNEL_DISPATCH_ENV, "")
    if not raw:
        return "orchestrator"
    try:
        uuid.UUID(raw)
    except ValueError:
        return "misconfigured"
    return "subagent"


class ChannelServer:
    def __init__(self, env: Mapping[str, str], stdin: BinaryIO, stdout: BinaryIO) -> None:
        self.env = dict(env)
        self.role = server_role(self.env)
        self._stdin = stdin
        self._stdout = stdout
        self._write_lock = threading.Lock()
        self._inflight: dict[Any, threading.Event] = {}
        self._inflight_lock = threading.Lock()
        self._listens: set[Any] = set()
        self._tools: dict[str, tuple[dict[str, Any], ToolHandler]] = {}
        self._workers: list[threading.Thread] = []
        self._bucket = _TokenBucket()

    def register(self, definition: dict[str, Any], handler: ToolHandler) -> None:
        self._tools[str(definition["name"])] = (definition, handler)

    def _visible(self) -> dict[str, tuple[dict[str, Any], ToolHandler]]:
        allowed = {"subagent": SUBAGENT_TOOLS, "orchestrator": ORCHESTRATOR_TOOLS}.get(
            self.role, ()
        )
        return {name: entry for name, entry in self._tools.items() if name in allowed}

    def _send(self, message: dict[str, Any]) -> None:
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self._write_lock:
            self._stdout.write(payload)
            self._stdout.flush()

    def _result(self, request_id: Any, result: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: Any, code: int, message: str) -> None:
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    def serve(self) -> int:
        for raw in self._stdin:
            if len(raw) > MAX_LINE_BYTES:
                self._error(None, INVALID_REQUEST, "message exceeds 1 MiB")
                continue
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError, UnicodeDecodeError:
                self._error(None, PARSE_ERROR, "parse error")
                continue
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                echo = message.get("id") if isinstance(message, dict) else None
                self._error(echo if _valid_id(echo) else None, INVALID_REQUEST, "invalid request")
                continue
            self._dispatch(message)
        with self._inflight_lock:
            for event in self._inflight.values():
                event.set()
            listens = list(self._listens)
            self._listens.clear()
        for listen_id in listens:
            try:
                self._result(listen_id, self._complete({"_meta": {_SUBSCRIPTION_KEY: listen_id}}))
            except OSError, ValueError:  # reason: the client may close stdout before shutdown
                _discard_stdout(self._stdout)
                break
        for worker in self._workers:
            worker.join(timeout=5)
        return 0

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        raw_params = message.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        if method == "notifications/cancelled":
            target = params.get("requestId")
            if not _valid_id(target):
                return  # a malformed notification gets no reply
            with self._inflight_lock:
                event = self._inflight.get(target)
                self._listens.discard(target)
            if event is not None:
                event.set()
            return
        if "id" in message and not _valid_id(request_id):
            self._error(None, INVALID_REQUEST, "request id must be a string or an integer")
            return
        if request_id is None:
            return  # other notifications, including notifications/initialized
        modern = self._modern_envelope(request_id, params)
        if modern is False:
            return
        if method == "server/discover":
            self._discover(request_id)
            return
        if method == "subscriptions/listen" and modern:
            self._listen(request_id)
            return
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = (
                requested if requested in LEGACY_PROTOCOL_VERSIONS else LEGACY_PROTOCOL_VERSIONS[0]
            )
            self._result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": _version(self.env)},
                    "instructions": "Orchestrator channel for Pitwall Agent Routing dispatches.",
                },
            )
            return
        if method == "ping":
            self._result(request_id, self._complete({}) if modern else {})
            return
        if method == "tools/list":
            listing: dict[str, Any] = {
                "tools": [definition for definition, _ in self._visible().values()]
            }
            if modern:
                listing = self._complete({**listing, "ttlMs": CACHE_TTL_MS, "cacheScope": "public"})
            self._result(request_id, listing)
            return
        if method == "tools/call":
            self._start_call(request_id, params, modern=bool(modern))
            return
        self._error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")

    def _server_info(self) -> dict[str, Any]:
        return {"name": SERVER_NAME, "version": _version(self.env)}

    def _modern_envelope(self, request_id: Any, params: dict[str, Any]) -> bool | None:
        """True: a valid modern request. False: rejected (error sent). None: no modern _meta."""
        meta = params.get("_meta")
        if not isinstance(meta, dict) or _VERSION_KEY not in meta:
            return None
        requested = meta.get(_VERSION_KEY)
        if requested not in MODERN_PROTOCOL_VERSIONS:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": UNSUPPORTED_PROTOCOL_VERSION,
                        "message": "Unsupported protocol version",
                        "data": {
                            "supported": list(MODERN_PROTOCOL_VERSIONS),
                            "requested": requested,
                        },
                    },
                }
            )
            return False
        if not isinstance(meta.get(_CAPABILITIES_KEY), dict):
            self._error(
                request_id, INVALID_PARAMS, "missing io.modelcontextprotocol/clientCapabilities"
            )
            return False
        return True

    def _complete(self, result: dict[str, Any]) -> dict[str, Any]:
        meta = dict(result.get("_meta") or {})
        meta[_SERVER_INFO_KEY] = self._server_info()
        return {**result, "resultType": "complete", "_meta": meta}

    def _shape(self, result: dict[str, Any], modern: bool) -> dict[str, Any]:
        return self._complete(result) if modern else result

    def _discover(self, request_id: Any) -> None:
        self._result(
            request_id,
            self._complete(
                {
                    "supportedVersions": list(MODERN_PROTOCOL_VERSIONS),
                    "capabilities": {"tools": {"listChanged": False}},
                    "instructions": INSTRUCTIONS,
                    "ttlMs": CACHE_TTL_MS,
                    "cacheScope": "public",
                }
            ),
        )

    def _listen(self, request_id: Any) -> None:
        """Acknowledge with the honored subset (none: the tool list never changes) and hold open."""
        with self._inflight_lock:
            self._listens.add(request_id)
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/subscriptions/acknowledged",
                "params": {"_meta": {_SUBSCRIPTION_KEY: request_id}, "notifications": {}},
            }
        )

    def _start_call(self, request_id: Any, params: dict[str, Any], *, modern: bool = False) -> None:
        name = params.get("name")
        entry = self._visible().get(str(name))
        if entry is None:
            self._error(request_id, INVALID_PARAMS, f"unknown tool: {name}")
            return
        raw_arguments = params.get("arguments")
        arguments: dict[str, Any] = raw_arguments if isinstance(raw_arguments, dict) else {}
        raw_meta = params.get("_meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        token = meta.get("progressToken")
        declared = entry[0].get("inputSchema", {}).get("properties", {})
        misshapen = raw_arguments is not None and not isinstance(raw_arguments, dict)
        if misshapen or set(arguments) - set(declared):
            allowed = ", ".join(sorted(declared)) or "no arguments"
            reason = "arguments must be an object" if misshapen else "unknown argument"
            self._result(
                request_id,
                self._shape(
                    {
                        "content": [{"type": "text", "text": f"{reason}; allowed: {allowed}"}],
                        "isError": True,
                    },
                    modern,
                ),
            )
            return
        if not self._bucket.take():
            self._result(
                request_id,
                self._shape(
                    {
                        "content": [{"type": "text", "text": "rate limited; retry shortly"}],
                        "isError": True,
                    },
                    modern,
                ),
            )
            return
        cancel = threading.Event()
        with self._inflight_lock:
            self._inflight[request_id] = cancel
        counter = [0]

        def progress(text: str) -> None:
            if token is None:
                return
            counter[0] += 1
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progressToken": token, "progress": counter[0], "message": text},
                }
            )

        handler = entry[1]
        tool_name = str(entry[0].get("name"))

        def run() -> None:
            try:
                payload = handler(arguments, cancel, progress)
                self._result(
                    request_id,
                    self._shape(
                        {
                            "content": [
                                {"type": "text", "text": json.dumps(payload, sort_keys=True)}
                            ],
                            "structuredContent": payload,
                            "isError": False,
                        },
                        modern,
                    ),
                )
            except ToolCancelled:
                pass
            except ToolError as exc:
                self._result(
                    request_id,
                    self._shape(
                        {"content": [{"type": "text", "text": str(exc)}], "isError": True}, modern
                    ),
                )
            except Exception:  # reason: a tool bug must not kill the server
                print(f"tool {tool_name} request {request_id} failed", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                self._result(
                    request_id,
                    self._shape(
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "internal error; see the server's stderr log",
                                }
                            ],
                            "isError": True,
                        },
                        modern,
                    ),
                )
            finally:
                with self._inflight_lock:
                    self._inflight.pop(request_id, None)

        worker = threading.Thread(target=run, name=f"mcp-call-{request_id}", daemon=True)
        self._workers.append(worker)
        worker.start()


def _valid_id(value: object) -> bool:
    """A JSON-RPC id this server accepts: a string or an integer, never a boolean."""
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _discard_stdout(stream: BinaryIO) -> None:
    """Point a broken stdout at devnull so the interpreter's exit flush cannot fail.

    A failed flush leaves the unsent bytes buffered; the Python documentation's SIGPIPE
    note recommends this redirect so shutdown does not exit 120 with a traceback.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, stream.fileno())
        finally:
            os.close(devnull)
    except OSError, ValueError:  # reason: an in-memory stream has no descriptor to redirect
        pass


def _version(env: Mapping[str, str]) -> str:
    try:
        from .execution import distribution_version

        return distribution_version(env)
    except Exception:  # reason: the version is informational; report unknown on any failure
        return "unknown"


def main(env: Mapping[str, str]) -> int:
    from .mcp_tools import build_server  # mcp_tools imports this module; import at call time

    return build_server(env, sys.stdin.buffer, sys.stdout.buffer).serve()
