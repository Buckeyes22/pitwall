"""``pitwall terminate-pod``."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import time
from typing import Any

from pitwall.cli.args import guard_cli_pre_spend
from pitwall.cli.output import Output, add_json_argument
from pitwall.cli.output import json_mode as _json_mode
from pitwall.runpod_client.pods import get_pod_strict_sync
from pitwall.runpod_credentials import MISSING_CREDENTIAL_MESSAGE, resolve_runpod_api_key

_TERMINATE_VERIFY_TIMEOUT_S = 60.0
_TERMINATE_VERIFY_INTERVAL_S = 5.0


def _parse_terminate_pod_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall terminate-pod",
        description="Terminate a single RunPod pod by id with verification.",
    )
    parser.add_argument(
        "--pod-id",
        required=True,
        help="RunPod pod id to terminate",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip post-terminate verification (NOT recommended)",
    )
    parser.add_argument(
        "--verify-timeout-s",
        type=float,
        default=_TERMINATE_VERIFY_TIMEOUT_S,
        help=f"Seconds to wait for pod to reach EXITED/TERMINATED (default {_TERMINATE_VERIFY_TIMEOUT_S})",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


def _is_terminated(pod: dict[str, Any]) -> bool:
    status = pod.get("desiredStatus", "")
    return status in {"EXITED", "TERMINATED"}


async def _terminate_pod_via_control_plane(pod_id: str) -> object:
    """Translate the legacy command into the guarded RP-02 mutation service."""
    from pitwall.db import get_pool
    from pitwall.runpod_control_plane import (
        IdentifiedMutationRequest,
        RunPodControlPlaneService,
    )

    request = IdentifiedMutationRequest(
        intent="apply",
        idempotency_key=("legacy-terminate:" + hashlib.sha256(pod_id.encode()).hexdigest()[:32]),
        resource_id=pod_id,
    )
    service = RunPodControlPlaneService(audit_pool=await get_pool(), actor="system")
    return await service.terminate_pod(request)


def get_pod_sync(pod_id: str) -> dict[str, Any] | None:
    """Read one pod for terminate verification; only a 404 means the pod is gone."""
    return get_pod_strict_sync(pod_id)


def cmd_terminate_pod(argv: list[str]) -> int:
    args = _parse_terminate_pod_args(argv)
    out = Output(_json_mode(args))

    try:
        guard_cli_pre_spend({"pod_id": args.pod_id})
    except Exception:  # reason: rejected identifiers must not be reflected.
        out.set_json({"error": "terminate_failed"})
        if not out.json_mode:
            out.print_error("pod termination failed")
        out.emit()
        return 1

    api_key, _ = resolve_runpod_api_key(os.environ)
    if not api_key:
        out.print_error(MISSING_CREDENTIAL_MESSAGE)
        out.emit()
        return 1

    try:
        mutation = asyncio.run(_terminate_pod_via_control_plane(args.pod_id))
    except Exception:  # reason: CLI boundary must not reflect provider/request error text.
        out.set_json({"error": "terminate_failed"})
        out.print_error("pod termination failed")
        out.print_warning("Manual teardown via RunPod console required.")
        out.emit()
        return 1

    out.print(f"terminate requested for {args.pod_id}")
    out.add_json("pod_id", args.pod_id)
    out.add_json("action", "terminate_requested")
    if hasattr(mutation, "changed"):
        out.add_json("changed", bool(mutation.changed))
    if hasattr(mutation, "already_absent"):
        out.add_json("already_absent", bool(mutation.already_absent))

    if args.no_verify:
        out.emit()
        return 0

    deadline = time.monotonic() + args.verify_timeout_s
    while time.monotonic() < deadline:
        try:
            pod = get_pod_sync(args.pod_id)
        except Exception:  # reason: transient errors must not leak provider response text
            out.print_warning("pod verification temporarily unavailable")
            time.sleep(_TERMINATE_VERIFY_INTERVAL_S)
            continue
        if pod is None:
            msg = f"pod {args.pod_id} no longer returned by RunPod"
            out.print_success(f"OK: {msg}")
            out.add_json("status", "gone")
            out.emit()
            return 0
        if _is_terminated(pod):
            msg = f"pod {args.pod_id} desiredStatus={pod.get('desiredStatus')}"
            out.print_success(f"OK: {msg}")
            out.add_json("status", pod.get("desiredStatus"))
            out.emit()
            return 0
        out.print(f"  ... waiting (desiredStatus={pod.get('desiredStatus')!r})")
        time.sleep(_TERMINATE_VERIFY_INTERVAL_S)

    out.print(
        f"WARN: pod {args.pod_id} did not reach EXITED/TERMINATED within {args.verify_timeout_s}s"
    )
    out.print_warning("Manual verification via RunPod console required.")
    out.add_json("status", "timeout")
    out.emit()
    return 1
