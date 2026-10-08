"""Hermetic MCP adapters for the shared guardrail service."""

from __future__ import annotations

from unittest.mock import patch

from pitwall.mcp.tools.guardrails import (
    pitwall_guardrail_preview,
    pitwall_guardrail_status,
)
from pitwall.security.pre_spend import PreSpendInspectionService


def test_guardrail_tools_share_service_and_preview_does_not_mutate() -> None:
    service = PreSpendInspectionService()
    secret = "sk-abcdefghijklmnop12345678"
    with patch(
        "pitwall.mcp.tools.guardrails.get_pre_spend_inspection_service",
        return_value=service,
    ):
        before = pitwall_guardrail_status()
        preview = pitwall_guardrail_preview({"token": secret})
        after = pitwall_guardrail_status()

    assert before["counters"]["total"] == 0
    assert preview["decision"] == "block"
    assert preview["findings"][0]["path"] == "$.token"
    assert secret not in str(preview)
    assert after["counters"]["total"] == 0
    assert after["last_decision"] is None
