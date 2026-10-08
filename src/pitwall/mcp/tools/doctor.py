"""Installation readiness for MCP clients; the same report as ``pitwall doctor --json``."""

from __future__ import annotations

import os
from typing import Any

from pitwall.doctor import run_doctor


async def pitwall_doctor(canary: str | None = None) -> dict[str, Any]:
    """Return Pitwall's readiness report: install, config, services, and spend controls.

    Args:
        canary: Optional enabled embedding capability to exercise with a dry-run inference.

    Returns:
        ``schema_version``, ``mode``, ``version``, overall ``status`` (ok/warn/fail), a per-status
        ``summary``, and ``checks`` (each with id, phase, status, detail, next_step). Makes no paid
        call and never includes secret values.
    """
    report = await run_doctor(environ=os.environ, canary=canary)
    return report.to_dict()


__all__ = ["pitwall_doctor"]
