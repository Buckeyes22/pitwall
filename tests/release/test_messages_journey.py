"""J33: a Claude-native client completes a tool round trip through ``/v1/messages``.

The route, the Anthropic↔OpenAI translation, the proxy executor, and the SSE relay are real;
the capability/provider repositories and the upstream model server are fakes. A client sends
tools, receives a streamed ``tool_use``, then returns a ``tool_result`` that must reach the
upstream as an OpenAI ``tool`` message.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pitwall.db.quota_repository import ModelIdMapping
from tests.api.test_messages_route import _anthropic_body, _build_app, _FakeQuotaRepo

pytestmark = [pytest.mark.release, pytest.mark.anyio]

_TOOL = {
    "name": "read_file",
    "description": "Read a file",
    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
}

_TOOL_CALL_SSE = [
    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function",'
    b'"function":{"name":"read_file","arguments":""}}]}}]}\n\n',
    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\":"}}]}}]}\n\n',
    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"a.py\\"}"}}]},'
    b'"finish_reason":"tool_calls"}]}\n\n',
    b"data: [DONE]\n\n",
]

_TEXT_SSE = [
    b'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n',
    b"data: [DONE]\n\n",
]


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


async def test_j33_tool_use_round_trip_through_the_messages_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    replies = iter([_TOOL_CALL_SSE, _TEXT_SSE])

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=b"".join(next(replies))
        )

    quota_repo = _FakeQuotaRepo(
        model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")]
    )
    mod = _build_app(quota_repo=quota_repo)
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(upstream), base_url="http://127.0.0.1:20130"
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/v1/messages",
            json=_anthropic_body(model="gw/glm-flash", stream=True, tools=[_TOOL]),
            headers={"Authorization": "Bearer spend-token"},
        )
        assert first.status_code == 200, first.text
        events = _events(first.text)
        starts = [data for name, data in events if name == "content_block_start"]
        assert starts[0]["content_block"]["type"] == "tool_use"
        assert starts[0]["content_block"]["name"] == "read_file"
        tool_use_id = starts[0]["content_block"]["id"]
        partial = "".join(
            data["delta"]["partial_json"]
            for name, data in events
            if name == "content_block_delta" and data["delta"]["type"] == "input_json_delta"
        )
        assert json.loads(partial) == {"path": "a.py"}
        stop = [data for name, data in events if name == "message_delta"][-1]
        assert stop["delta"]["stop_reason"] == "tool_use"

        second = await client.post(
            "/v1/messages",
            json=_anthropic_body(
                model="gw/glm-flash",
                stream=True,
                tools=[_TOOL],
                messages=[
                    {"role": "user", "content": [{"type": "text", "text": "read a.py"}]},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tool_use_id,
                                "name": "read_file",
                                "input": {"path": "a.py"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": "print(1)",
                            }
                        ],
                    },
                ],
            ),
            headers={"Authorization": "Bearer spend-token"},
        )
        assert second.status_code == 200, second.text
        assert '"text":"done"' in second.text

    first_request, second_request = seen
    assert [tool["function"]["name"] for tool in first_request["tools"]] == ["read_file"]
    assert first_request["model"] == "glm-4.7-flash"
    tool_messages = [m for m in second_request["messages"] if m["role"] == "tool"]
    assert tool_messages == [{"role": "tool", "tool_call_id": tool_use_id, "content": "print(1)"}]
    assistant = [m for m in second_request["messages"] if m["role"] == "assistant"][0]
    assert assistant["tool_calls"][0]["id"] == tool_use_id
