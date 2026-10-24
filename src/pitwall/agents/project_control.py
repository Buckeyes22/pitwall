"""Opt-in caller-keyed project dispatch with actuator-side epoch fencing.

Uses the existing managed launcher/mailbox. A per-controller file lock covers
both epoch replacement and effect admission; interrupted launch intent is never
silently treated as absence. This is a local trusted-orchestrator interface.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .managed_channel import answer_once, start_dispatch
from .mcp_server import ToolError
from .run_store import atomic_write_json, ensure_private_directory, state_root


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def read(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ToolError("project control record is unreadable") from exc
    if not isinstance(value, dict):
        raise ToolError("project control record is invalid")
    return value


@contextmanager
def locked(root: Path) -> Iterator[None]:
    import fcntl

    ensure_private_directory(root)
    fd = os.open(root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


class ProjectControl:
    def __init__(self, env: Mapping[str, str], orchestrator: Any) -> None:
        self.env = dict(env)
        self.orchestrator = orchestrator

    def call(
        self, args: dict[str, Any], cancel: Any, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        controller = args.get("controller_id")
        binding_hash = args.get("binding_hash")
        if not isinstance(controller, str) or not 1 <= len(controller) <= 128:
            raise ToolError("invalid project controller identity")
        if (
            not isinstance(binding_hash, str)
            or len(binding_hash) != 64
            or any(c not in "0123456789abcdef" for c in binding_hash)
        ):
            raise ToolError("invalid project binding hash")
        root = state_root(self.env) / "controllers" / digest(controller)
        with locked(root):
            state = read(root / "owner.json")
            if state and (
                state["binding_hash"] != binding_hash or state["cwd"] != str(Path.cwd().resolve())
            ):
                raise ToolError("project binding or working directory changed")
            operation = args.get("operation")
            if operation == "status":
                return {"epoch": state["epoch"] if state else 0, "binding_hash": binding_hash}
            epoch, owner = args.get("epoch"), args.get("owner")
            if (
                type(epoch) is not int
                or epoch < 0
                or not isinstance(owner, str)
                or not 1 <= len(owner) <= 128
            ):
                raise ToolError("project epoch and owner are required")
            if operation == "acquire":
                if state and state["owner"] == owner and state["epoch"] == epoch + 1:
                    return state
                if epoch != (state["epoch"] if state else 0):
                    raise ToolError("project epoch changed")
                state = {
                    "epoch": epoch + 1,
                    "owner": owner,
                    "binding_hash": binding_hash,
                    "cwd": str(Path.cwd().resolve()),
                }
                atomic_write_json(root / "owner.json", state)
                return state
            if not state or state["epoch"] != epoch or state["owner"] != owner:
                raise ToolError("stale project controller")
            key = args.get("key")
            if not isinstance(key, str) or not 1 <= len(key) <= 128:
                raise ToolError("project action key is required")
            path = root / (digest(key) + ".json")
            record = read(path)
            if operation == "observe":
                if record is None:
                    return {"event": "absent", "key": key}
                if record["kind"] == "answer":
                    return {"event": "answered", "key": key, **record["result"]}
                dispatch_id = record["dispatch_id"]
                if not (state_root(self.env) / "launches" / dispatch_id).exists():
                    return {"event": "unknown", "key": key, "dispatch_id": dispatch_id}
                return dict(
                    self.orchestrator._wait(dispatch_id, cancel, progress, {"wait_seconds": 0.01})
                )
            if operation == "dispatch":
                request_args = args.get("request")
                if not isinstance(request_args, dict) or set(request_args) - {
                    "route",
                    "prompt",
                    "task_mode",
                    "timeout_seconds",
                }:
                    raise ToolError("invalid project dispatch request")
                request_hash = digest(request_args)
                if record is not None:
                    if record.get("request_hash") != request_hash or record["kind"] != "dispatch":
                        raise ToolError("project action key conflict")
                    return {"event": "admitted", "key": key, "dispatch_id": record["dispatch_id"]}
                prepared = {**request_args, "workspace": "shared", "ask_support": True}
                harness = self.orchestrator._require_child_channel(prepared)
                dispatch_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, controller + ":" + binding_hash + ":" + key)
                )
                request = self.orchestrator._launch_request(prepared, dispatch_id)
                record = {
                    "kind": "dispatch",
                    "dispatch_id": dispatch_id,
                    "request_hash": request_hash,
                }
                # Durable correlation precedes every possible process launch.
                atomic_write_json(path, record)
                launch_env = {**self.env, "PITWALL_AGENTS_MANAGED_EXPECTED_HARNESS": harness}
                start_dispatch(launch_env, request)
                return {"event": "admitted", "key": key, "dispatch_id": dispatch_id}
            if operation == "answer":
                launch_key, ask_id, choice = (
                    args.get("launch_key"),
                    args.get("ask_id"),
                    args.get("choice"),
                )
                if not all(isinstance(x, str) and x for x in (launch_key, ask_id, choice)):
                    raise ToolError("project answer requires launch key, ask and choice")
                assert isinstance(ask_id, str) and isinstance(choice, str)
                request_hash = digest([launch_key, ask_id, choice])
                if record:
                    if record.get("request_hash") != request_hash or record["kind"] != "answer":
                        raise ToolError("project answer key conflict")
                    return {"event": "answered", "key": key, **record["result"]}
                launch = read(root / (digest(launch_key) + ".json"))
                if launch is None or launch["kind"] != "dispatch":
                    raise ToolError("project answer has no admitted dispatch")
                result = answer_once(
                    self.env, launch["dispatch_id"], ask_id, choice, answered_by="orchestrator"
                )
                # Mailbox answer_once reconciles a crash before this receipt.
                payload = {"dispatch_id": launch["dispatch_id"], "answer": result}
                atomic_write_json(
                    path, {"kind": "answer", "request_hash": request_hash, "result": payload}
                )
                return {"event": "answered", "key": key, **payload}
            raise ToolError("unsupported project control operation")
