"""J34: every registered MCP tool, over real stdio, with valid and invalid input.

One ``pitwall.mcp`` stdio server runs against a freshly seeded disposable database. Each
tool gets its valid arguments (spending tools as dry runs, RunPod mutations as previews)
and one invalid argument set. A valid call either succeeds with the top-level keys the
fixture pins, or, when it needs a live provider or an absent record, returns the documented
typed code the fixture pins; it never degrades to a generic failure. Every invalid call is
refused as ``invalid_tool_arguments`` without reflecting the input. On the fresh registry, a
real (non-dry-run) spend under an exhausted monthly budget is refused as ``budget_rejected``
before any workload row exists.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from pitwall.mcp.registry import TOOL_REGISTRY

pytestmark = [pytest.mark.release, pytest.mark.integration, pytest.mark.journey_harness]

FIXTURES: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parent / "mcp_tool_fixtures.json").read_text()
)
NAMES = sorted(spec.name for spec in TOOL_REGISTRY)

# These change the seeded registry (they disable or hibernate the demo provider), so they run
# after every tool that reads it.
_REGISTRY_WRITERS = (
    "pitwall_create_capability",
    "pitwall_create_provider",
    "pitwall_update_capability",
    "pitwall_update_provider",
    "pitwall_disable_provider",
    "pitwall_hibernate_provider",
)


def _payload(result: Any) -> dict[str, Any]:
    if result.structured_content is not None:
        return dict(result.structured_content)
    return dict(json.loads(result.content[0].text))


async def _sweep(env: dict[str, str]) -> dict[str, tuple[Any, Any, frozenset[str]]]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "pitwall.mcp"], env=env)
    order = [n for n in NAMES if n not in _REGISTRY_WRITERS] + list(_REGISTRY_WRITERS)
    results: dict[str, tuple[Any, Any, frozenset[str]]] = {}
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        declared = {
            tool.name: frozenset(tool.input_schema.get("properties", {})) for tool in listed.tools
        }
        for name in order:
            fixture = FIXTURES[name]
            valid = await session.call_tool(name, fixture["valid"])
            invalid = await session.call_tool(name, fixture["invalid"])
            results[name] = (valid, invalid, declared[name])
    return results


@pytest.fixture(scope="module")
def results(
    registry_journey_env: dict[str, str],
) -> dict[str, tuple[Any, Any, frozenset[str]]]:
    return asyncio.run(asyncio.wait_for(_sweep(registry_journey_env), timeout=600))


async def _spend_without_budget(env: dict[str, str]) -> tuple[Any, int, int]:
    import asyncpg

    conn = await asyncpg.connect(env["DATABASE_URL"])
    try:
        before = int(await conn.fetchval("SELECT count(*) FROM pitwall.workloads"))
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "pitwall.mcp"],
            env={**env, "PITWALL_MONTHLY_BUDGET_USD": "0.000001"},
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "pitwall_submit_inference",
                {
                    "capability_id": "cap_embedding_demo",
                    "payload": {"texts": ["hello"]},
                    "dry_run": False,
                },
            )
        after = int(await conn.fetchval("SELECT count(*) FROM pitwall.workloads"))
    finally:
        await conn.close()
    return result, before, after


def test_a_real_spend_without_budget_is_refused_over_stdio(
    registry_journey_env: dict[str, str],
) -> None:
    """Before the sweep (which disables the demo provider), on the freshly seeded registry:
    an unfunded, non-dry-run spend is refused before any workload exists."""
    result, before, after = asyncio.run(
        asyncio.wait_for(_spend_without_budget(registry_journey_env), timeout=120)
    )
    payload = _payload(result)
    assert result.is_error, payload
    assert payload.get("error") == "budget_rejected", payload
    assert after == before


def test_every_tool_has_fixtures() -> None:
    assert sorted(FIXTURES) == NAMES
    for name, fixture in FIXTURES.items():
        assert ("expect_keys" in fixture) != ("expect_error" in fixture), name


@pytest.mark.parametrize("name", NAMES)
def test_tool_over_stdio(name: str, results: dict[str, tuple[Any, Any, frozenset[str]]]) -> None:
    fixture = FIXTURES[name]
    valid, invalid, declared = results[name]
    payload = _payload(valid)
    if "expect_error" in fixture:
        assert valid.is_error, (name, payload)
        assert payload == {"error": fixture["expect_error"]}, name
    else:
        assert not valid.is_error, (name, payload)
        missing = [key for key in fixture["expect_keys"] if key not in payload]
        assert not missing, (name, missing)

    assert invalid.is_error, name
    rejected = _payload(invalid)
    assert rejected["error"] == "invalid_tool_arguments", name
    # Feedback names Pitwall's own declared parameters, never the caller's values.
    assert set(rejected) <= {"error", "allowed", "fields"}, name
    assert set(rejected.get("allowed", [])) <= declared, name
    assert json.dumps(fixture["invalid"]) not in str(invalid.content)
