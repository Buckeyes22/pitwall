"""Discovery identity, cache hints, honest capabilities, no empty output schemas (F07, F10, F17)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.mcp.raw_stdio import MODERN_META, RawStdio


@pytest.fixture()
def broker() -> Iterator[RawStdio]:
    client = RawStdio.start()
    try:
        yield client
    finally:
        client.close()


def test_discover_has_instructions_and_only_tools(broker: RawStdio) -> None:
    result = broker.request("server/discover", {"_meta": MODERN_META})["result"]
    assert "catalogue" in result["instructions"]
    assert set(result["capabilities"]) - {"experimental", "extensions"} == {"tools"}
    assert result["cacheScope"] == "public" and result["ttlMs"] == 3_600_000


def test_tools_list_is_cacheable_for_an_hour(broker: RawStdio) -> None:
    result = broker.request("tools/list", {"_meta": MODERN_META})["result"]
    assert result["ttlMs"] == 3_600_000 and result["cacheScope"] == "public"


def test_no_tool_advertises_the_generic_object_output_schema(broker: RawStdio) -> None:
    tools = broker.request("tools/list", {"_meta": MODERN_META})["result"]["tools"]
    for tool in tools:
        schema = tool.get("outputSchema")
        assert schema is None or set(schema) - {"type", "additionalProperties", "title"}, tool[
            "name"
        ]


def test_prompts_list_is_not_served(broker: RawStdio) -> None:
    reply = broker.request("prompts/list", {"_meta": MODERN_META})
    assert reply["error"]["code"] == -32601


def test_structured_content_survives_without_output_schema(broker: RawStdio) -> None:
    reply = broker.request(
        "tools/call", {"_meta": MODERN_META, "name": "pitwall_models_list", "arguments": {}}
    )
    assert reply["result"]["isError"] is False
    assert isinstance(reply["result"]["structuredContent"]["models"], list)


def test_legacy_handshake_is_tools_only_without_output_schemas(broker: RawStdio) -> None:
    init = broker.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "legacy", "version": "0"},
        },
    )["result"]
    assert set(init["capabilities"]) - {"experimental", "extensions"} == {"tools"}
    broker.notify("notifications/initialized")
    tools = broker.request("tools/list")["result"]["tools"]
    assert tools and all("outputSchema" not in tool for tool in tools)
    reply = broker.request("tools/call", {"name": "pitwall_models_list", "arguments": {}})
    assert reply["result"]["isError"] is False
    assert isinstance(reply["result"]["structuredContent"]["models"], list)


def test_instructions_state_that_runpod_applies_replay_on_a_repeated_key() -> None:
    from pitwall.mcp import INSTRUCTIONS

    assert "repeating an apply with the same key and request returns the stored result" in (
        INSTRUCTIONS
    )


def test_instructions_name_lease_and_serve_launches_as_retry_safe_with_a_key() -> None:
    from pitwall.mcp import INSTRUCTIONS

    sentence = next(part for part in INSTRUCTIONS.split(". ") if "pitwall_lease_pod" in part)
    for tool in (
        "pitwall_submit_inference",
        "pitwall_submit_job",
        "pitwall_lease_pod",
        "pitwall_serve_model",
    ):
        assert tool in sentence
    assert "idempotency_key" in sentence and "instead of" in sentence


def test_instructions_say_which_launch_codes_keep_the_key_and_which_need_a_new_one() -> None:
    from pitwall.mcp import INSTRUCTIONS

    same_key = INSTRUCTIONS.index("mutation_in_progress")
    assert "same key" in INSTRUCTIONS[same_key : same_key + 80]
    new_key = INSTRUCTIONS.index("lease_state_conflict")
    clause = INSTRUCTIONS[new_key - 120 : new_key + 80]
    for code in ("idempotency_conflict", "idempotency_mismatch", "lease_state_conflict"):
        assert code in clause
    assert "new key" in clause
    assert "first launch's lease" not in INSTRUCTIONS
