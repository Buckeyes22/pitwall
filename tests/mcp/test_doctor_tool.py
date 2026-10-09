"""pitwall_doctor MCP tool (plan Task 3)."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.doctor import DoctorCheck, DoctorReport


async def test_tool_returns_the_cli_report_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.mcp.tools.doctor import pitwall_doctor

    seen: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> DoctorReport:
        seen.update(kwargs)
        return DoctorReport(
            "registry", "0.0.0", (DoctorCheck("db.connect", "services", "ok", "connected"),)
        )

    monkeypatch.setattr("pitwall.mcp.tools.doctor.run_doctor", fake)
    result = await pitwall_doctor(canary="embedding.demo")
    assert result["status"] == "ok"
    assert result["checks"][0]["id"] == "db.connect"
    assert seen["canary"] == "embedding.demo"


def test_tool_is_registered() -> None:
    from pitwall.mcp.registry import TOOL_NAMES, TOOL_REGISTRY
    from tests.mcp.test_registry_health import EXPECTED_TOOL_COUNT

    assert "pitwall_doctor" in TOOL_NAMES
    assert len(TOOL_NAMES) == len(TOOL_REGISTRY) == EXPECTED_TOOL_COUNT
