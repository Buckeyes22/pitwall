"""Build the pod start command: a self-termination timer plus the model server."""

from __future__ import annotations

import shlex
from collections.abc import Sequence

_ACTION_URL = '"https://api.runpod.io/v2/pods/$RUNPOD_POD_ID/action"'


def _lifecycle(action: str) -> str:
    """A v2 pod lifecycle call; the pod-scoped RUNPOD_API_KEY and RUNPOD_POD_ID come from RunPod."""
    return (
        'curl -fsS -X POST -H "Authorization: Bearer $RUNPOD_API_KEY" '
        '-H "Content-Type: application/json" '
        f'-d \'{{"action":"{action}"}}\' {_ACTION_URL}'
    )


_TERMINATE = _lifecycle("terminate")
_STOP = _lifecycle("stop")


def server_binary(engine: str, image: str) -> list[str]:
    if engine == "llama.cpp":
        return ["/app/llama-server"]
    if engine == "vllm":
        return ["vllm", "serve"]
    if engine == "sglang":
        return ["python3", "-m", "sglang.launch_server"]
    raise ValueError(f"unsupported engine: {engine!r}")


def wrap_start_command(
    server: Sequence[str],
    argv: Sequence[str],
    *,
    ttl_seconds: int,
    terminate: bool = True,
) -> list[str]:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    action = _TERMINATE if terminate else _STOP
    exec_line = " ".join(shlex.quote(part) for part in [*server, *argv])
    # The timer's outcome goes to the container's stderr, which the bounded pod-log
    # client reads; the lifecycle response carries no credential.
    script = (
        f"( sleep {ttl_seconds}; echo 'pitwall-deadline: ttl reached' >&2; "
        f"{action} >&2 || echo 'pitwall-deadline: lifecycle call failed' >&2 ) &\n"
        f'exec {exec_line} --api-key "$PITWALL_ENDPOINT_KEY"'
    )
    return [script]


def redact_for_display(command: Sequence[str]) -> list[str]:
    shown: list[str] = []
    hide_next = False
    for part in command:
        if hide_next:
            shown.append("<redacted>")
            hide_next = False
            continue
        shown.append(part)
        if part == "--api-key":
            hide_next = True
    return shown
