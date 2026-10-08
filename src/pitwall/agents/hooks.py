"""Portable, fail-open lifecycle hooks."""

from __future__ import annotations

import contextlib
import json
import sys
import uuid
from collections.abc import Mapping
from typing import Any

from .paths import config_root
from .process import interrupt_pending, run_bounded_capture
from .run_store import RunStore, atomic_write_bytes, ensure_private_directory

MAX_HOOK_OUTPUT_BYTES = 1024 * 1024


class HookRunner:
    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = dict(env)
        self.config_path = config_root(env) / "hooks.json"
        self._config: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._config is not None:
            return self._config
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
            self._config = value if isinstance(value, dict) else {}
        except OSError, json.JSONDecodeError:
            self._config = {}
        return self._config

    def __call__(self, event: dict[str, Any], store: RunStore) -> None:
        hooks = self._load().get(event["event"], [])
        if not isinstance(hooks, list):
            return
        try:
            depth = int(self.env.get("PITWALL_AGENTS_HOOK_DEPTH", "0") or "0")
        except TypeError, ValueError:
            depth = 0
        if depth >= 3:
            return
        for index, definition in enumerate(hooks):
            # Each hook runs to completion so its writes stay whole, but once a Ctrl+C is
            # pending in the supervising run the rest of this event's hooks are skipped,
            # so the supervisor can act on it as soon as the current hook ends.
            if interrupt_pending():
                self._record_skipped(event, store, len(hooks) - index)
                return
            try:
                self._run_one(definition, event, store, depth)
            except OSError, TypeError, ValueError:
                # Hooks are fail-open in v0.3: malformed configuration and
                # artifact-write failures must never suppress the sentinel.
                continue

    def _record_skipped(self, event: dict[str, Any], store: RunStore, count: int) -> None:
        """Leave a status file per hook skipped for a pending Ctrl+C, and say so on stderr."""
        with contextlib.suppress(OSError):
            hook_dir = store.artifact("hooks")
            ensure_private_directory(hook_dir)
            for _ in range(count):
                atomic_write_bytes(
                    hook_dir / f"{event['event']}-{uuid.uuid4()}.json",
                    (json.dumps({"skipped": "interrupted"}, sort_keys=True) + "\n").encode("utf-8"),
                )
        print(
            f"pitwall agents: skipped {count} {event['event']} hook(s) after Ctrl+C",
            file=sys.stderr,
        )

    def _run_one(self, definition: Any, event: dict[str, Any], store: RunStore, depth: int) -> None:
        if not isinstance(definition, dict):
            return
        command = definition.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            return
        timeout = definition.get("timeoutSeconds", 5)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            timeout = 5
        hook_id = str(uuid.uuid4())
        hook_dir = store.artifact("hooks")
        ensure_private_directory(hook_dir)
        child_env = dict(self.env)
        child_env.update(
            {
                "PITWALL_AGENTS_HOOK_DEPTH": str(depth + 1),
                "PITWALL_AGENTS_EVENT": event["event"],
                "PITWALL_AGENTS_DISPATCH_ID": event["dispatchId"],
                "PITWALL_AGENTS_HARNESS": event["provider"],
                "PITWALL_AGENTS_MODEL": event["model"],
            }
        )
        if event.get("workflowId") is not None:
            child_env["PITWALL_AGENTS_WORKFLOW_ID"] = str(event["workflowId"])
        if event.get("taskId") is not None:
            child_env["PITWALL_AGENTS_TASK_ID"] = str(event["taskId"])
        try:
            result = run_bounded_capture(
                command,
                env=child_env,
                timeout_seconds=float(timeout),
                max_bytes=MAX_HOOK_OUTPUT_BYTES,
                stdin=(json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8"),
            )
            stdout, stderr = result.stdout, result.stderr
            status = {
                "exitCode": None if result.timed_out else result.returncode,
                "timedOut": result.timed_out,
                "stdoutBytes": result.stdout_bytes,
                "stderrBytes": result.stderr_bytes,
                "stdoutTruncated": result.stdout_truncated,
                "stderrTruncated": result.stderr_truncated,
            }
        except OSError as exc:
            stdout = b""
            stderr = str(exc).encode("utf-8", errors="replace")
            status = {
                "exitCode": None,
                "timedOut": False,
                "stdoutBytes": 0,
                "stderrBytes": len(stderr),
                "stdoutTruncated": False,
                "stderrTruncated": False,
            }
        prefix = f"{event['event']}-{hook_id}"
        atomic_write_bytes(hook_dir / f"{prefix}.stdout.log", stdout)
        atomic_write_bytes(hook_dir / f"{prefix}.stderr.log", stderr)
        atomic_write_bytes(
            hook_dir / f"{prefix}.json",
            (json.dumps(status, sort_keys=True) + "\n").encode("utf-8"),
        )
