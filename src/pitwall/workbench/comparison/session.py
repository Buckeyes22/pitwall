"""Bounded RPC capture of a Pi child, shared by every acceptance and comparison runner.

The reader threads parse newline-delimited JSON events from the child, stamp each with
``observedAt`` (epoch milliseconds), and keep at most ``max_observed`` of them. Provider and plugin
stderr is only counted, never retained, because it can carry secrets.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from pitwall.workbench.comparison._json import JsonObject, as_object, as_str
from pitwall.workbench.launcher import default_pi_bin

MAX_FRAME_BYTES = 8_000_000
TINTIN_PACKAGE = "@tintinweb/pi-subagents"
TINTIN_ENTRY = "dist/index.js"


class RpcError(RuntimeError):
    """An RPC request failed, timed out, or the Pi child closed."""


@dataclass(frozen=True)
class RecordedEvent:
    seq: int
    event: JsonObject


@dataclass(frozen=True)
class StopOutcome:
    forced: bool


# The hosted runners give Pi this long to exit after stdin EOF before SIGTERM, and this long before
# SIGKILL. A Pi that exits on its own returns at once; a loaded host must not be reported as forced.
PI_STOP_TERM_AFTER_MS = 2_500
PI_STOP_KILL_AFTER_MS = 5_000


def pinned_node_module(package: str, entry: str, pi_bin: Path | str | None = None) -> Path | None:
    """Locate ``entry`` of a package that is installed next to the pinned Pi CLI.

    ``PITWALL_WORKBENCH_TINTIN_EXTENSION`` overrides the Tintin backend path for the comparison runner.
    The lookup walks up from the resolved Pi CLI to each enclosing ``node_modules`` directory.
    """
    if package == TINTIN_PACKAGE and os.environ.get("PITWALL_WORKBENCH_TINTIN_EXTENSION"):
        configured = Path(os.environ["PITWALL_WORKBENCH_TINTIN_EXTENSION"])
        return configured if configured.is_file() else None
    start = Path(pi_bin) if pi_bin is not None else default_pi_bin()
    for directory in Path(os.path.realpath(start)).parents:
        candidate = directory / "node_modules" / package / entry
        if candidate.is_file():
            return candidate
        if directory.name == "node_modules" and (directory / package / entry).is_file():
            return directory / package / entry
    return None


def now_ms() -> int:
    return int(time.time() * 1000)


class RpcSession:
    """A running ``pi --mode rpc`` child with request/response correlation and event capture."""

    def __init__(
        self,
        child: subprocess.Popen[bytes],
        *,
        id_prefix: str,
        max_observed: int = 4000,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        default_timeout_ms: int = 90_000,
    ) -> None:
        stdin, stdout, stderr = child.stdin, child.stdout, child.stderr
        if stdin is None or stdout is None or stderr is None:
            raise RpcError("Pi was not started with piped stdio")
        self.child = child
        self._stdin: IO[bytes] = stdin
        self._stdout: IO[bytes] = stdout
        self._stderr: IO[bytes] = stderr
        self._id_prefix = id_prefix
        self._max_observed = max_observed
        self._max_frame_bytes = max_frame_bytes
        self._default_timeout_ms = default_timeout_ms
        self._condition = threading.Condition()
        self._observed: list[RecordedEvent] = []
        self._sequence = 0
        self._request_number = 0
        self._pending: dict[str, Future[JsonObject]] = {}
        self._closed = False
        self._protocol_error: str | None = None
        self._invalidated: str | None = None
        self._capture_truncated = False
        self.stderr_bytes = 0
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._exit_thread = threading.Thread(target=self._watch_exit, daemon=True)
        for thread in (self._stdout_thread, self._stderr_thread, self._exit_thread):
            thread.start()

    # -- reader threads -------------------------------------------------------------------------

    def _fail_protocol(self, reason: str) -> None:
        with self._condition:
            self._protocol_error = reason
            self._condition.notify_all()

    def _read_stdout(self) -> None:
        stream = self._stdout
        while True:
            raw = stream.readline(self._max_frame_bytes + 1)
            if not raw:
                break
            if len(raw) > self._max_frame_bytes and not raw.endswith(b"\n"):
                self._fail_protocol("RPC frame exceeded bounded capture")
                self.child.terminate()
                break
            text = raw.decode("utf-8", errors="replace")
            if not text.strip():
                continue
            try:
                parsed = json.loads(text)
            except ValueError:
                # A non-JSON stdout line is a protocol failure; the run reports it.
                self._fail_protocol("non-JSON RPC stdout")
                continue
            event = as_object(parsed)
            if event is None:
                self._fail_protocol("non-JSON RPC stdout")
                continue
            event["observedAt"] = now_ms()
            self._record(event)

    def _record(self, event: JsonObject) -> None:
        with self._condition:
            self._sequence += 1
            self._observed.append(RecordedEvent(self._sequence, event))
            if len(self._observed) > self._max_observed:
                self._observed.pop(0)
                self._capture_truncated = True
            event_id = as_str(event.get("id"))
            waiter = (
                self._pending.pop(event_id, None)
                if event.get("type") == "response" and event_id
                else None
            )
            if waiter is not None:
                if event.get("success"):
                    waiter.set_result(event)
                else:
                    detail = (
                        as_str(event.get("error"))
                        or as_str(event.get("message"))
                        or "unknown RPC error"
                    )
                    waiter.set_exception(
                        RpcError(f"RPC {event.get('command')} rejected: {detail[:240]}")
                    )
            self._condition.notify_all()

    def _read_stderr(self) -> None:
        descriptor = self._stderr.fileno()
        while chunk := os.read(descriptor, 65536):
            self.stderr_bytes += len(chunk)

    def _watch_exit(self) -> None:
        self.child.wait()
        self._stdout_thread.join(timeout=5)
        with self._condition:
            self._closed = True
            for waiter in self._pending.values():
                waiter.set_exception(RpcError("Pi exited before RPC response"))
            self._pending.clear()
            self._condition.notify_all()

    # -- state ----------------------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def protocol_error(self) -> str | None:
        with self._condition:
            return self._protocol_error

    def was_capture_truncated(self) -> bool:
        with self._condition:
            return self._capture_truncated

    def last_seq(self) -> int:
        with self._condition:
            return self._sequence

    def snapshot(self) -> list[RecordedEvent]:
        with self._condition:
            return list(self._observed)

    def events_after(self, seq: int) -> list[RecordedEvent]:
        with self._condition:
            return [entry for entry in self._observed if entry.seq > seq]

    def invalidate(self, reason: str) -> None:
        with self._condition:
            if self._invalidated is None:
                self._invalidated = reason

    def unavailable_reason(self) -> str | None:
        with self._condition:
            return self._invalidated

    # -- requests and waiting -------------------------------------------------------------------

    def request(
        self, kind: str, fields: JsonObject | None = None, timeout_ms: int | None = None
    ) -> JsonObject:
        """Send one RPC command and return its successful response event."""
        timeout_ms = self._default_timeout_ms if timeout_ms is None else timeout_ms
        future: Future[JsonObject] = Future()
        with self._condition:
            if self._invalidated is not None:
                raise RpcError(f"RPC session invalidated: {self._invalidated}")
            if self._closed:
                raise RpcError("Pi is closed")
            self._request_number += 1
            request_id = f"{self._id_prefix}-{self._request_number}"
            self._pending[request_id] = future
        payload = json.dumps({**(fields or {}), "type": kind, "id": request_id}) + "\n"
        try:
            self._stdin.write(payload.encode())
            self._stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as error:
            with self._condition:
                self._pending.pop(request_id, None)
            raise RpcError(f"Pi closed its input before {kind}") from error
        try:
            return future.result(timeout=timeout_ms / 1000)
        except FutureTimeoutError:
            with self._condition:
                self._pending.pop(request_id, None)
            raise RpcError(f"RPC {kind} timed out") from None

    def response_data(
        self, kind: str, fields: JsonObject | None = None, timeout_ms: int | None = None
    ) -> JsonObject:
        """The ``data`` object of a successful response (empty when the response has none)."""
        return as_object(self.request(kind, fields, timeout_ms).get("data")) or {}

    def wait_event(
        self,
        event_type: str,
        after: int,
        timeout_ms: int | None = None,
        predicate: Callable[[JsonObject], bool] | None = None,
    ) -> None:
        """Block until an event of ``event_type`` newer than ``after`` is observed."""
        timeout_ms = self._default_timeout_ms if timeout_ms is None else timeout_ms
        deadline = time.monotonic() + timeout_ms / 1000
        with self._condition:
            while True:
                if any(
                    entry.seq > after
                    and entry.event.get("type") == event_type
                    and (predicate is None or predicate(entry.event))
                    for entry in self._observed
                ):
                    return
                if self._closed or self._protocol_error:
                    raise RpcError(self._protocol_error or "Pi exited")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RpcError(f"RPC event {event_type} timed out")
                self._condition.wait(remaining)

    def wait_until(self, check: Callable[[], bool], timeout_ms: int, what: str) -> None:
        """Block until ``check`` holds, failing when Pi exits or the deadline passes."""
        deadline = time.monotonic() + timeout_ms / 1000
        with self._condition:
            while not check():
                if self._closed:
                    raise RpcError(f"Pi exited before {what}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RpcError(f"{what} timed out")
                self._condition.wait(min(remaining, 0.05))

    # -- shutdown -------------------------------------------------------------------------------

    def stop(self, *, term_after_ms: int = 500, kill_after_ms: int = 3_000) -> StopOutcome:
        """Clear queued work, abort, then terminate within a bounded time."""
        if self.closed:
            self._finish()
            return StopOutcome(forced=False)
        try:
            self.request("clear_queue", {}, 3_000)
            self.request("abort", {}, 5_000)
        except RpcError:
            # Termination below is bounded whether or not the abort was acknowledged.
            pass
        forced = False
        with contextlib.suppress(OSError):  # a closed pipe is already the state this wants
            self._stdin.close()
        try:
            self.child.wait(timeout=term_after_ms / 1000)
        except subprocess.TimeoutExpired:
            forced = True
            self.child.terminate()
            try:
                self.child.wait(timeout=max(0.0, (kill_after_ms - term_after_ms) / 1000))
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
        self._finish()
        return StopOutcome(forced=forced)

    def _finish(self) -> None:
        for thread in (self._exit_thread, self._stdout_thread, self._stderr_thread):
            thread.join(timeout=5)
        for stream in (self._stdin, self._stdout, self._stderr):
            if not stream.closed:
                stream.close()


def assistant_messages(events: Sequence[JsonObject]) -> list[JsonObject]:
    """The assistant ``message`` objects of every ``message_end`` event."""
    found: list[JsonObject] = []
    for event in events:
        message = as_object(event.get("message"))
        if (
            event.get("type") == "message_end"
            and message is not None
            and message.get("role") == "assistant"
        ):
            found.append(message)
    return found
