"""Thin MCP adapters for the shared pre-spend inspection service."""

from __future__ import annotations

from typing import Any

from pitwall.security.pre_spend import get_pre_spend_inspection_service


def pitwall_guardrail_status() -> dict[str, Any]:
    """Return configured guardrail rules and non-sensitive aggregate state."""
    return get_pre_spend_inspection_service().status().to_dict()


def pitwall_guardrail_preview(payload: Any) -> dict[str, Any]:
    """Inspect a payload without provider, database, audit, or counter writes."""
    return get_pre_spend_inspection_service().preview(payload).semantic_dict()


__all__ = ["pitwall_guardrail_preview", "pitwall_guardrail_status"]
