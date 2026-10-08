"""Request accounting for Workbench runs (port of ``accounting.ts`` and ``usage.ts``).

Payload shapes keep sizes, roles, and content hashes only, never transcript text. Usage is
provider-reported; it is not an entitlement or an invoice estimate.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_USAGE_FILE_BYTES = 32 * 1024 * 1024
_EFFORTS = frozenset({"none", "off", "minimal", "low", "medium", "high", "xhigh", "max"})
_IMAGE_TYPES = frozenset({"image_url", "image", "input_image"})
_ROLES = ("system", "developer", "assistant", "user", "tool")
_USAGE_FIELDS = ("input", "output", "cacheRead", "cacheWrite", "totalTokens", "reasoning")


@dataclass(frozen=True)
class PayloadShape:
    serialized_bytes: int
    system_bytes: int
    tools_bytes: int
    conversation_bytes: int
    message_count: int
    roles: dict[str, int]
    tool_count: int
    image_count: int
    has_reasoning_fields: bool
    reasoning_effort: str | None
    chat_template_kwargs: dict[str, bool | str]
    content_hashes: list[str]


@dataclass(frozen=True)
class UsageReport:
    requests: int
    settled: int
    unavailable: int
    queue_wait_ms: float
    accounts: dict[str, dict[str, float]]
    usage: dict[str, float] | None
    source: str = "provider-reported"
    accounting_scope: str = "selected profile; excludes other clients and account billing"


def _dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _size(value: object) -> int:
    return len(_dumps(value).encode())


def _hash(value: object) -> str:
    return hashlib.sha256(_dumps(value).encode()).hexdigest()[:16]


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count_images(value: object) -> int:
    if isinstance(value, list):
        return sum(_count_images(item) for item in value)
    if not isinstance(value, dict):
        return 0
    kind = value.get("type")
    own = 1 if isinstance(kind, str) and kind in _IMAGE_TYPES else 0
    return own + _count_images(value.get("content"))


def shape_payload(payload: object) -> PayloadShape:
    """Summarise a serialized provider request without retaining any of its text."""
    value = _object(payload)
    # The hook sees each API's serialized request rather than Pi's provider-neutral context, so
    # normalise the supported request shapes while keeping only sizes, roles, and hashes.
    messages: list[dict[str, Any]] = []
    for key in ("messages", "input"):
        if isinstance(value.get(key), list):
            messages.extend(_object(item) for item in value[key])
    roles: dict[str, int] = {}
    image_count = 0
    has_reasoning = False
    system_bytes = 0
    conversation_bytes = 0
    content_hashes: list[str] = []
    for message in messages:
        role = message.get("role")
        role = role if isinstance(role, str) and role in _ROLES else "unknown"
        roles[role] = roles.get(role, 0) + 1
        if role in ("system", "developer"):
            system_bytes += _size(message)
        else:
            conversation_bytes += _size(message)
        image_count += _count_images(message.get("content"))
        if "reasoning" in message or "reasoning_content" in message:
            has_reasoning = True
        content = message.get("content")
        content_hashes.append(_hash("" if content is None else content))
    # Anthropic keeps system content outside `messages`; Responses may use `instructions`
    # instead of a system or developer input item.
    if "system" in value:
        system_bytes += _size(value["system"])
        image_count += _count_images(value["system"])
    if "instructions" in value:
        system_bytes += _size(value["instructions"])
    # Some compatible Responses endpoints accept scalar input. It has no role but still counts
    # towards conversation size, without retaining the text.
    if "input" in value and not isinstance(value["input"], list):
        conversation_bytes += _size(value["input"])
        content_hashes.append(_hash(value["input"]))
    kwargs = _object(value.get("chat_template_kwargs"))
    safe: dict[str, bool | str] = {}
    for name in ("enable_thinking", "preserve_thinking"):
        if isinstance(kwargs.get(name), bool):
            safe[name] = kwargs[name]
    kwargs_effort = kwargs.get("reasoning_effort")
    if isinstance(kwargs_effort, str) and kwargs_effort in _EFFORTS:
        safe["reasoning_effort"] = kwargs_effort
    effort = value.get("reasoning_effort")
    tools = value.get("tools")
    return PayloadShape(
        serialized_bytes=_size(payload),
        system_bytes=system_bytes,
        tools_bytes=_size([] if tools is None else tools),
        conversation_bytes=conversation_bytes,
        message_count=len(messages),
        roles=roles,
        tool_count=len(tools) if isinstance(tools, list) else 0,
        image_count=image_count,
        has_reasoning_fields=has_reasoning,
        reasoning_effort=effort if isinstance(effort, str) and effort in _EFFORTS else None,
        chat_template_kwargs=safe,
        content_hashes=content_hashes,
    )


def _finite_non_negative(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def safe_usage(value: object) -> dict[str, float] | None:
    """Keep only known numeric usage fields; an all-zero object is unavailable.

    Pi initialises every usage field to zero before a provider sends usage, so an all-zero
    object must not be reported as a measured zero-token request after a transport error.
    """
    usage = _object(value)
    out = {name: usage[name] for name in _USAGE_FIELDS if _finite_non_negative(usage.get(name))}
    return out if out and any(number > 0 for number in out.values()) else None


def append_accounting(path: Path | str, record: object) -> None:
    """Append one JSON line, forcing mode 0600 and refusing symlink destinations."""
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, (_dumps(record) + "\n").encode())
    finally:
        os.close(descriptor)


def usage_report(path: Path | str) -> UsageReport:
    """Total provider-reported usage from an accounting file, per account and overall."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_USAGE_FILE_BYTES:
            raise ValueError("usage file must be a regular file at most 32 MiB")
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as handle:
            text = handle.read()
    finally:
        os.close(descriptor)
    requests = settled = unavailable = 0
    queue_wait_ms = 0.0
    totals: dict[str, float] = {}
    accounts: dict[str, dict[str, float]] = {}
    for line in filter(None, text.split("\n")):
        record = json.loads(line)
        if record.get("type") == "native_request":
            requests += 1
            wait = record.get("queueWaitMs")
            if _finite_non_negative(wait):
                queue_wait_ms += wait
        if record.get("type") != "native_settled":
            continue
        settled += 1
        usage = safe_usage(record.get("usage"))
        if usage is None:
            unavailable += 1
            continue
        ref = record.get("accountRef")
        account = accounts.setdefault(ref if isinstance(ref, str) else "unattributed", {})
        for key, number in usage.items():
            totals[key] = totals.get(key, 0) + number
            account[key] = account.get(key, 0) + number
    return UsageReport(
        requests=requests,
        settled=settled,
        unavailable=unavailable,
        queue_wait_ms=queue_wait_ms,
        accounts=accounts,
        usage=totals or None,
    )
