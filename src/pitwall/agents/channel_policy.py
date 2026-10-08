"""D3 auto-answer policy: routine asks answered by an endpoint route (spec §9.2, §14 D3)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from .broker import resolve_pitwall_api_token
from .http_urls import open_http_url
from .mailbox import POLICY_PREFIX
from .profiles import resolved_endpoint

POLICY_BLOCKED_ON = frozenset({"choice", "naming", "file-selection"})
DEFAULT_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 64 * 1024
# Reasoning models spend completion tokens before they write the JSON answer; a route's
# declared output limit replaces this default.
POLICY_MAX_TOKENS = 1024
_SYSTEM = (
    "You answer routine clarification questions from a coding agent. Choose exactly one of the "
    'provided option ids. Reply with only a JSON object: {"choice": "<option id>"}.'
)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    choice: str | None
    answered_by: str | None
    reason: str


def escalation_reason(ask: Mapping[str, Any]) -> str | None:
    """None when D3 allows a policy answer; otherwise why the ask goes to the operator."""
    blocked_on = ask.get("blocked_on")
    if blocked_on not in POLICY_BLOCKED_ON:
        return f"blocked_on {blocked_on!r} always escalates to the operator"
    if ask.get("severity", "normal") != "normal":
        return "blocking severity always escalates to the operator"
    if ask.get("default") == "abort":
        return "an abort default always escalates to the operator"
    if len(ask.get("context", {}).get("options", [])) < 2:
        return "the ask offers fewer than two options"
    return None


def build_messages(ask: Mapping[str, Any]) -> list[dict[str, str]]:
    context = ask.get("context", {})
    lines = [f"Question: {ask['question']}", "Options:"]
    lines += [f"- {option['id']}: {option['text']}" for option in context.get("options", [])]
    lines.append(f"The agent's default: {ask['default']}")
    if ask.get("default_rationale"):
        lines.append(f"Default rationale: {ask['default_rationale']}")
    if context.get("files_touched"):
        lines.append("Files involved (names only): " + ", ".join(context["files_touched"]))
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


def _parse_choice(content: str, option_ids: set[str]) -> str | None:
    candidates = [content]
    if (match := re.search(r"\{.*?\}", content, re.S)) is not None:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, dict)
            and isinstance(value.get("choice"), str)
            and value["choice"] in option_ids
        ):
            return str(value["choice"])
    return None


def request_choice(
    ask: Mapping[str, Any],
    *,
    route_name: str,
    entry: Mapping[str, Any],
    env: Mapping[str, str],
    timeout: float = DEFAULT_TIMEOUT_S,
) -> PolicyDecision:
    if (reason := escalation_reason(ask)) is not None:
        return PolicyDecision(None, None, reason)
    endpoint = resolved_endpoint(entry)
    if endpoint is None:
        return PolicyDecision(
            None, None, f"route {route_name} has no endpoint; only endpoint routes answer asks"
        )
    model = str(entry["model"])
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key_env := endpoint.get("apiKeyEnv"):
        key, _resolved = resolve_pitwall_api_token(env, str(key_env))
        if key:
            headers["Authorization"] = f"Bearer {key}"
    limits = entry.get("limits")
    output = limits.get("output") if isinstance(limits, Mapping) else None
    max_tokens = output if isinstance(output, int) and output > 0 else POLICY_MAX_TOKENS
    body = json.dumps(
        {
            "model": model,
            "messages": build_messages(ask),
            "temperature": 0,
            "max_tokens": max_tokens,
        }
    ).encode()
    url = str(endpoint["baseUrl"]).rstrip("/") + "/chat/completions"
    try:
        with open_http_url(
            request.Request(url, data=body, headers=headers, method="POST"), timeout=timeout
        ) as reply:
            payload = json.loads(reply.read(MAX_RESPONSE_BYTES).decode("utf-8"))
    except error.HTTPError as exc:
        exc.close()
        return PolicyDecision(None, None, f"policy route {route_name} returned HTTP {exc.code}")
    except (TimeoutError, error.URLError, OSError, ValueError) as exc:
        return PolicyDecision(None, None, f"policy route {route_name} failed: {exc}")
    try:
        first = payload["choices"][0]
        raw_content = first["message"]["content"]
    except KeyError, IndexError, TypeError:
        return PolicyDecision(None, None, "the policy reply had no message content")
    content = "" if raw_content is None else str(raw_content)
    option_ids = {str(option["id"]) for option in ask["context"]["options"]}
    if (choice := _parse_choice(content, option_ids)) is None:
        if isinstance(first, Mapping) and first.get("finish_reason") == "length":
            return PolicyDecision(
                None,
                None,
                f"the policy reply hit its {max_tokens}-token limit before selecting an option",
            )
        return PolicyDecision(
            None, None, "the policy reply did not select one of the ask's options"
        )
    return PolicyDecision(
        choice, f"{POLICY_PREFIX}{model}", f"selected by policy route {route_name}"
    )
