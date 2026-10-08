"""One literal test per gateway surface (port of the Node gateway tests/surfaces.test.ts).

Each compression mode, each inbound shape, and the package entrypoint, driven over HTTP against a
recording in-process upstream.
"""

from __future__ import annotations

import inspect

import pytest

from pitwall.gateway.app import serve
from tests.gateway.test_app_shapes import NOISY, contents, sent_body


@pytest.mark.parity
async def test_compression_off_relays_the_messages_unchanged() -> None:
    # Source: surfaces.test.ts "compression off relays the messages unchanged"
    _, body = await sent_body({"x-pitwall-compression": "off"}, NOISY)
    assert contents(body) == ["rule one\nrule one\nrule two", "hello   \n\n\n\n  world"]


@pytest.mark.parity
async def test_compression_rtk_collapses_repeated_system_lines_and_whitespace() -> None:
    # Source: surfaces.test.ts "compression rtk collapses repeated system lines and whitespace"
    _, body = await sent_body({"x-pitwall-compression": "rtk"}, NOISY)
    assert contents(body) == ["rule one\nrule two", "hello\n\nworld"]


@pytest.mark.parity
async def test_compression_caveman_collapses_repeated_system_lines_and_whitespace() -> None:
    # Source: surfaces.test.ts "compression caveman collapses repeated system lines and whitespace"
    _, body = await sent_body({"x-pitwall-compression": "caveman"}, NOISY)
    assert contents(body) == ["rule one\nrule two", "hello\n\nworld"]


@pytest.mark.parity
async def test_compression_stacked_collapses_repeated_system_lines_and_whitespace() -> None:
    # Source: surfaces.test.ts "compression stacked collapses repeated system lines and whitespace"
    _, body = await sent_body({"x-pitwall-compression": "stacked"}, NOISY)
    assert contents(body) == ["rule one\nrule two", "hello\n\nworld"]


@pytest.mark.parity
async def test_the_openai_inbound_shape_is_relayed_as_sent() -> None:
    # Source: surfaces.test.ts "the openai inbound shape is relayed as sent"
    payload = {"model": "fixture", "messages": [{"role": "user", "content": "hi"}]}
    _, body = await sent_body({"x-pitwall-inbound-shape": "openai"}, payload)
    assert body == payload


@pytest.mark.parity
async def test_the_claude_inbound_shape_projects_system_and_text_blocks() -> None:
    # Source: surfaces.test.ts "the claude inbound shape projects system and text blocks onto OpenAI messages"
    _, body = await sent_body(
        {"x-pitwall-inbound-shape": "claude"},
        {
            "model": "fixture",
            "max_tokens": 8,
            "system": "terse",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        },
    )
    assert body == {
        "messages": [{"role": "system", "content": "terse"}, {"role": "user", "content": "hi"}],
        "model": "fixture",
        "max_tokens": 8,
    }


@pytest.mark.parity
async def test_the_gemini_inbound_shape_projects_system_instruction_and_contents() -> None:
    # Source: surfaces.test.ts "the gemini inbound shape projects systemInstruction and contents onto messages"
    _, body = await sent_body(
        {"x-pitwall-inbound-shape": "gemini"},
        {
            "model": "fixture",
            "systemInstruction": {"parts": [{"text": "be terse"}]},
            "contents": [
                {"role": "user", "parts": [{"text": "hi"}]},
                {"role": "model", "parts": [{"text": "hello"}]},
            ],
        },
    )
    assert body["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


@pytest.mark.parity
async def test_the_responses_inbound_shape_reaches_the_executor_unchanged() -> None:
    # Source: surfaces.test.ts "the responses inbound shape reaches the executor unchanged"
    payload = {"model": "fixture", "input": "fixture"}
    url, body = await sent_body({"x-pitwall-inbound-shape": "responses"}, payload)
    assert url == "http://upstream.invalid/chat/completions"
    assert body == payload


@pytest.mark.parity
def test_the_gateway_entrypoint_is_serve_taking_bind_and_port() -> None:
    # Source: surfaces.test.ts "the pitwall-gateway bin is the executable launcher the build writes"
    # The Node launcher script is replaced by `serve(bind=, port=)`, which `pitwall gateway serve` calls.
    parameters = list(inspect.signature(serve).parameters)
    assert parameters == ["bind", "port"]
    assert inspect.signature(serve).return_annotation in (int, "int")
