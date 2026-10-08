"""Template writes must use REST, and must send ports as the API defines them.

Live: deleteTemplate(id:) is rejected ("Unknown argument \"id\""), the `template`
root query does not exist, and _save_template_mutation hard-codes ports: "" so
every template Pitwall created came back with ports: [] while other tooling's
templates carry ["8000/http", "22/tcp"].
"""

from __future__ import annotations

import inspect

import httpx
import pytest
import respx

from pitwall.runpod_client import templates as templates_mod


def test_delete_template_does_not_use_graphql() -> None:
    source = inspect.getsource(templates_mod.delete_template)
    assert "deleteTemplate" not in source, "deleteTemplate(id:) is rejected by the API"
    assert "templates/" in source, "delete must call DELETE /templates/{id}"


def test_get_template_does_not_use_the_nonexistent_root_query() -> None:
    source = inspect.getsource(templates_mod.get_template)
    assert "template(id:" not in source, "the `template` root query does not exist"
    assert "templates/" in source, "get must call GET /templates/{id}"


def test_ports_are_not_hard_coded_empty() -> None:
    source = inspect.getsource(templates_mod)
    assert 'ports: ""' not in source, "ports was accepted and silently discarded"


@respx.mock
@pytest.mark.anyio
async def test_public_account_template_list_uses_strict_rest_v2_envelope() -> None:
    route = respx.get("https://runpod.example.test/v2/templates").mock(
        return_value=httpx.Response(
            200,
            json={
                "templates": [
                    {
                        "id": "tmpl_account",
                        "name": "Account Template",
                        "image": "example/image:1",
                        "disk": 30,
                        "serverless": True,
                        "ports": ["8000/http"],
                        "env": {"SAFE": "value"},
                    }
                ]
            },
        )
    )

    result = await templates_mod.list_account_templates(
        api_key="test-key",
        rest_api_url="https://runpod.example.test/v2",
    )

    assert route.call_count == 1
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-key"
    assert result[0].id == "tmpl_account"
    assert result[0].image_name == "example/image:1"
    assert result[0].ports == "8000/http"
    assert result[0].env is not None
    assert result[0].env[0].key == "SAFE"


@respx.mock
@pytest.mark.anyio
async def test_public_account_template_list_rejects_wrong_envelope() -> None:
    respx.get("https://runpod.example.test/v2/templates").mock(
        return_value=httpx.Response(200, json=[])
    )

    with pytest.raises(RuntimeError, match="templates envelope"):
        await templates_mod.list_account_templates(
            api_key="test-key",
            rest_api_url="https://runpod.example.test/v2",
        )


@respx.mock
@pytest.mark.anyio
async def test_public_account_template_list_rejects_malformed_item() -> None:
    respx.get("https://runpod.example.test/v2/templates").mock(
        return_value=httpx.Response(200, json={"templates": [None]})
    )

    with pytest.raises(RuntimeError, match="template item"):
        await templates_mod.list_account_templates(
            api_key="test-key",
            rest_api_url="https://runpod.example.test/v2",
        )
