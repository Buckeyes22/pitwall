"""Prove MCP stdio transport: initialize + list-tools works via the Python mcp SDK."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.anyio

_REPO_ROOT_ARG = "--repo-root"


def _repo_root() -> str:
    for i, arg in enumerate(sys.argv):
        if arg.startswith(f"{_REPO_ROOT_ARG}="):
            return arg.split("=", 1)[1]
        if arg == _REPO_ROOT_ARG and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return str(Path(__file__).resolve().parent.parent.parent)


@pytest.fixture()
def server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "pitwall.mcp"],
        env={
            "PITWALL_MCP_TRANSPORT": "stdio",
            "RUNPOD_API_KEY": "test-key",
            "DATABASE_URL": "postgresql://test:test@localhost/test",
            "REDIS_URL": "redis://localhost:6379/0",
        },
        cwd=_repo_root(),
    )


async def test_initialize_returns_server_info(server_params: StdioServerParameters) -> None:
    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        result = await session.initialize()
        assert result.server_info.name == "pitwall"
        assert result.protocol_version


async def test_list_tools_returns_pitwall_health(server_params: StdioServerParameters) -> None:
    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.list_tools()
        tool_names = [t.name for t in result.tools]
        assert "pitwall_health" in tool_names


async def test_call_health_returns_server_status(
    server_params: StdioServerParameters,
) -> None:
    """A client can call the read-only health tool through stdio JSON-RPC."""
    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool("pitwall_health", {})

    assert result.is_error is False
    checks = result.structured_content
    assert checks is not None
    assert set(checks) == {"ok", "database", "redis", "provider_registry"}
    assert all(isinstance(value, bool) for value in checks.values())
    assert checks["provider_registry"] is True


async def test_call_models_list_matches_packaged_catalogue(
    server_params: StdioServerParameters,
) -> None:
    """The stdio catalogue call exposes every packaged model dossier exactly once."""
    catalogue_root = Path(_repo_root()) / "docs" / "models"
    expected_model_ids = {
        match.group(1)
        for path in catalogue_root.glob("*.md")
        if path.name != "README.md"
        for match in [re.search(r"^model_id:\s*(\S+)\s*$", path.read_text(), re.MULTILINE)]
        if match is not None
    }
    assert expected_model_ids

    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool("pitwall_models_list", {})

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    models = result.structured_content["models"]
    assert {model["model_id"] for model in models} == expected_model_ids
    assert all(model["variant_ids"] for model in models)


async def test_initialize_capabilities_declare_tools(
    server_params: StdioServerParameters,
) -> None:
    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        result = await session.initialize()
        assert result.capabilities.tools is not None


async def test_call_tool_validation_error_never_reflects_input(
    server_params: StdioServerParameters,
) -> None:
    canary = "sk-stdio-validation-canary-1234567890abcdef"
    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "pitwall_models_fit",
            {"model": {"unexpected": canary}},
        )

    assert result.is_error is True
    assert result.structured_content == {"error": "invalid_tool_arguments", "fields": ["model"]}
    assert canary not in str(result)


async def test_serve_failures_keep_stable_codes_over_stdio_without_detail_leaks(
    server_params: StdioServerParameters, registry_config_file: Path
) -> None:
    """Real MCP wire with an injected service failure; no provider or registry I/O."""
    server_params = server_params.model_copy(
        update={
            "env": {**(server_params.env or {}), "PITWALL_CONFIG_FILE": str(registry_config_file)}
        }
    )
    script = """
from pitwall.mcp.tools import serve
from pitwall.api.exceptions import (
    ServeRateRequired, ServeUnknownVariant, ServeInvalidGpuClass,
    ServeTemplateInvalid, ServeVerificationFailed, ServeLaunchFailed,
)
canary = "synthetic-wire-serve-detail"
failures = {
    "rate_required": ServeRateRequired(canary),
    "unknown_variant": ServeUnknownVariant(canary, "bad-variant"),
    "invalid_gpu_class": ServeInvalidGpuClass(canary, ()),
    "invalid_template": ServeTemplateInvalid(canary),
    "served_model_mismatch": ServeVerificationFailed(canary, ["wrong-model"]),
    "launch_failed": ServeLaunchFailed(canary),
}
async def pool():
    return object()
async def fail(_pool, request, **kwargs):
    assert request.dry_run is True
    raise failures[request.capability_name]
serve.get_pool = pool
serve.load_catalogue = lambda: object()
serve.serve_model = fail
from pitwall.mcp.__main__ import main
main()
"""
    params = server_params.model_copy(update={"args": ["-c", script]})
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        for code in (
            "rate_required",
            "unknown_variant",
            "invalid_gpu_class",
            "invalid_template",
            "served_model_mismatch",
            "launch_failed",
        ):
            result = await session.call_tool(
                "pitwall_serve_model", {"capability": code, "dry_run": True}
            )
            assert result.is_error is True, code
            assert result.structured_content == {"error": code}
            assert "synthetic-wire-serve-detail" not in str(result)


async def test_unknown_arguments_are_refused_before_the_handler_runs(
    server_params: StdioServerParameters,
) -> None:
    """A misspelled ``dry_run`` must never fall through to a real, paid call."""
    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        misspelled = await session.call_tool(
            "pitwall_serve_model",
            {"capability": "coding.chat", "model": "org/model", "dryrun": True},
        )
        zero_arg = await session.call_tool("pitwall_health", {"verbose": True})

    assert all(tool.input_schema.get("additionalProperties") is False for tool in tools.values())
    assert misspelled.is_error is True
    assert misspelled.structured_content == {
        "error": "invalid_tool_arguments",
        "allowed": sorted(tools["pitwall_serve_model"].input_schema["properties"]),
    }
    assert "dryrun" not in str(misspelled)
    assert zero_arg.is_error is True
    assert zero_arg.structured_content == {"error": "invalid_tool_arguments", "allowed": []}
