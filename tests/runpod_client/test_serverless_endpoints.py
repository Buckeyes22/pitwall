"""Tests for RunPod serverless endpoint CRUD + scaling config."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from pitwall.runpod_client.pods import RunPodRestError
from pitwall.runpod_client.serverless import (
    Endpoint,
    EndpointScalingConfig,
    create_endpoint,
    delete_endpoint,
    get_endpoint,
    list_endpoints,
    update_endpoint_scaling,
)

pytestmark = pytest.mark.anyio

ENDPOINT_ID = "abc123"
REST_BASE = "https://api.runpod.test/v2"


@pytest.fixture(autouse=True)
def _hermetic_rest_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNPOD_REST_API_URL", REST_BASE)


class TestEndpointScalingConfig:
    def test_defaults(self) -> None:
        config = EndpointScalingConfig()
        assert config.workers_min == 0
        assert config.workers_max == 3
        assert config.idle_timeout == 60
        assert config.gpu_type_id is None
        assert config.flashboot is False

    def test_custom_values(self) -> None:
        config = EndpointScalingConfig(
            workers_min=1,
            workers_max=5,
            idle_timeout=120,
            gpu_type_id="NVIDIA H100",
            flashboot=True,
        )
        assert config.workers_min == 1
        assert config.workers_max == 5
        assert config.idle_timeout == 120
        assert config.gpu_type_id == "NVIDIA H100"
        assert config.flashboot is True

    def test_to_request_json_full(self) -> None:
        config = EndpointScalingConfig(
            workers_min=2,
            workers_max=4,
            idle_timeout=90,
            gpu_type_id="NVIDIA L4",
            flashboot=True,
        )
        payload = config.to_request_json()
        assert payload == {
            "workers": {"min": 2, "max": 4, "idleTimeout": 90},
            "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4.0},
            "flashboot": "FLASHBOOT",
        }

    def test_to_request_json_omits_optional_gpu_when_none(self) -> None:
        config = EndpointScalingConfig(gpu_type_id=None)
        payload = config.to_request_json()
        assert "gpu" not in payload

    def test_to_request_json_omits_flashboot_when_false(self) -> None:
        config = EndpointScalingConfig(flashboot=False)
        payload = config.to_request_json()
        assert payload["flashboot"] == "OFF"

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValueError):
            EndpointScalingConfig(workers_min=0, extra_field=True)  # type: ignore[call-arg]  # reason: intentionally forbidden extra field

    def test_workers_min_negative_rejected(self) -> None:
        with pytest.raises(ValueError):
            EndpointScalingConfig(workers_min=-1)

    def test_workers_max_less_than_one_rejected(self) -> None:
        with pytest.raises(ValueError):
            EndpointScalingConfig(workers_max=0)

    def test_round_trip_from_request_json(self) -> None:
        original = EndpointScalingConfig(
            workers_min=1,
            workers_max=6,
            idle_timeout=45,
            gpu_type_id="NVIDIA H100",
            flashboot=True,
        )
        payload = original.to_request_json()
        parsed = EndpointScalingConfig(
            workers_min=payload["workers"]["min"],
            workers_max=payload["workers"]["max"],
            idle_timeout=payload["workers"]["idleTimeout"],
            gpu_type_id=original.gpu_type_id,
            flashboot=payload["flashboot"] == "FLASHBOOT",
        )
        assert parsed.workers_min == original.workers_min
        assert parsed.workers_max == original.workers_max
        assert parsed.idle_timeout == original.idle_timeout
        assert parsed.gpu_type_id == original.gpu_type_id
        assert parsed.flashboot == original.flashboot

    def test_request_count_omits_inapplicable_idle_timeout(self) -> None:
        payload = EndpointScalingConfig(
            scaler_type="REQUEST_COUNT",
            scaler_value=3,
        ).to_request_json()

        assert payload["workers"] == {"min": 0, "max": 3}
        assert payload["scaling"] == {"type": "REQUEST_COUNT", "requestCount": 3}


@respx.mock
async def test_create_endpoint_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = cast(dict[str, Any], json.loads(request.content))
        response: httpx.Response = runpod_response_factory.endpoint_response(endpoint_id="new-ep-1")
        return response

    respx.post(f"{REST_BASE}/serverless").mock(side_effect=handler)

    scaling = EndpointScalingConfig(workers_min=1, workers_max=4, idle_timeout=90)
    result = await create_endpoint(
        name="my-endpoint",
        template_id="tmpl-abc",
        gpu_pools=["ADA_24"],
        excluded_gpu_types=["NVIDIA RTX 4090"],
        gpu_count=2,
        scaling=scaling,
    )

    assert isinstance(result, Endpoint)
    assert result.id == "new-ep-1"
    assert result.name == "test-endpoint"
    assert result.scaling.workers_min == 0
    assert result.scaling.workers_max == 3
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["name"] == "my-endpoint"
    assert captured["body"]["templateId"] == "tmpl-abc"
    assert captured["body"]["gpu"] == {
        "pools": ["ADA_24"],
        "excludedTypes": ["NVIDIA RTX 4090"],
        "count": 2,
    }
    assert captured["body"]["workers"] == {"min": 1, "max": 4, "idleTimeout": 90}
    assert captured["body"]["scaling"] == {"type": "QUEUE_DELAY", "queueDelay": 4.0}
    assert captured["body"]["flashboot"] == "OFF"
    assert captured["body"]["type"] == "QUEUE"


@respx.mock
async def test_create_endpoint_defaults_scaling_when_not_provided(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = cast(dict[str, Any], json.loads(request.content))
        response: httpx.Response = runpod_response_factory.endpoint_response()
        return response

    respx.post(f"{REST_BASE}/serverless").mock(side_effect=handler)

    result = await create_endpoint(
        name="my-ep",
        template_id=None,
        gpu_pools=["ADA_24"],
        image_name="example.test/model:sha256",
    )

    assert isinstance(result, Endpoint)
    assert captured["body"]["workers"] == {"min": 0, "max": 3, "idleTimeout": 60}
    assert captured["body"]["gpu"]["pools"] == ["ADA_24"]
    assert captured["body"]["image"] == "example.test/model:sha256"


@respx.mock
async def test_create_endpoint_raises_rest_error_on_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    respx.post(f"{REST_BASE}/serverless").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )

    with pytest.raises(RunPodRestError) as exc_info:
        await create_endpoint(name="ep", template_id="tmpl", gpu_pools=["ADA_24"])

    assert exc_info.value.method == "POST"
    assert exc_info.value.path == "serverless"
    assert exc_info.value.status_code == 401


@respx.mock
async def test_create_endpoint_uses_custom_rest_url(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setenv("RUNPOD_REST_API_URL", "https://runpod.example.test/api/")
    route = respx.post("https://runpod.example.test/api/serverless").mock(
        return_value=runpod_response_factory.endpoint_response(endpoint_id="ep-x")
    )

    result = await create_endpoint(name="ep-x", template_id="tmpl", gpu_pools=["ADA_24"])

    assert result.id == "ep-x"
    assert route.call_count == 1


@respx.mock
async def test_create_endpoint_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Point HOME at an empty tmp dir so a real ~/.runpod/config.toml on the machine
    # running the suite can't accidentally supply a fallback credential here.
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    respx.post(f"{REST_BASE}/serverless").mock(
        return_value=httpx.Response(200, json={"error": "unauthorized"})
    )

    with pytest.raises(RuntimeError, match="RUNPOD_API_KEY not set") as exc_info:
        await create_endpoint(name="ep", template_id="tmpl", gpu_pools=["ADA_24"])

    message = str(exc_info.value)
    assert "export RUNPOD_API_KEY" in message
    assert "runpodctl" in message


@respx.mock
async def test_create_endpoint_falls_back_to_runpodctl_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No RUNPOD_API_KEY: serverless.py falls back to runpodctl's saved credential."""

    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    runpod_dir = tmp_path / ".runpod"
    runpod_dir.mkdir()
    (runpod_dir / "config.toml").write_text('apikey = "from-runpodctl"\n')

    captured_auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_auth.append(request.headers.get("authorization"))
        return httpx.Response(
            200,
            json={
                "id": "ep-fallback",
                "name": "ep",
                "workers": {"min": 0, "max": 3},
                "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4.0},
                "flashboot": "OFF",
            },
        )

    route = respx.post(f"{REST_BASE}/serverless").mock(side_effect=handler)

    result = await create_endpoint(name="ep", template_id="tmpl", gpu_pools=["ADA_24"])

    assert result.id == "ep-fallback"
    assert captured_auth == ["Bearer from-runpodctl"]
    assert route.call_count == 1


@respx.mock
async def test_create_endpoint_rejects_missing_compute_before_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    route = respx.post(f"{REST_BASE}/serverless").mock(return_value=httpx.Response(201, json={}))

    with pytest.raises(RuntimeError, match="requires gpu_ids or gpu_pools"):
        await create_endpoint(name="ep", template_id="tmpl")

    assert route.call_count == 0


@respx.mock
async def test_get_endpoint_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        response: httpx.Response = runpod_response_factory.endpoint_response(
            endpoint_id=ENDPOINT_ID,
            name="my-ep",
            workers_min=1,
            workers_max=5,
            idle_timeout=90,
            gpu_type_id="NVIDIA H100",
            flashboot=True,
        )
        return response

    respx.get(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(side_effect=handler)

    result = await get_endpoint(ENDPOINT_ID)

    assert isinstance(result, Endpoint)
    assert result.id == ENDPOINT_ID
    assert result.name == "my-ep"
    assert result.scaling.workers_min == 1
    assert result.scaling.workers_max == 5
    assert result.scaling.idle_timeout == 90
    assert result.scaling.gpu_type_id is None
    assert result.scaling.flashboot is True
    assert captured["auth"] == "Bearer test-key"


@respx.mock
async def test_get_endpoint_normalizes_id(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    route = respx.get(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(
        return_value=runpod_response_factory.endpoint_response(endpoint_id=ENDPOINT_ID)
    )

    result = await get_endpoint(f"  /{ENDPOINT_ID}/  ")

    assert result.id == ENDPOINT_ID
    assert route.call_count == 1


@respx.mock
async def test_get_endpoint_raises_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    respx.get(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )

    with pytest.raises(RunPodRestError) as exc_info:
        await get_endpoint(ENDPOINT_ID)

    assert exc_info.value.status_code == 404


async def test_get_endpoint_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="endpoint_id must be non-empty"):
        await get_endpoint("  ")


async def test_get_endpoint_rejects_path_separator_in_id() -> None:
    with pytest.raises(ValueError, match="endpoint_id must not contain path separators"):
        await get_endpoint("ep-1/status")


@respx.mock
async def test_list_endpoints_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    respx.get(f"{REST_BASE}/serverless").mock(
        return_value=httpx.Response(
            200,
            json={
                "endpoints": [
                    runpod_response_factory.endpoint(endpoint_id="ep-1", name="ep-one"),
                    runpod_response_factory.endpoint(endpoint_id="ep-2", name="ep-two"),
                ]
            },
        )
    )

    result = await list_endpoints()

    assert len(result) == 2
    assert result[0].id == "ep-1"
    assert result[0].name == "ep-one"
    assert result[1].id == "ep-2"
    assert result[1].name == "ep-two"


@respx.mock
async def test_list_endpoints_with_name_prefix(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    captured_params: dict[str, Any] = {}
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        response = httpx.Response(
            200,
            json={"endpoints": [runpod_response_factory.endpoint(name="pitwall-one")]},
        )
        return response

    respx.get(f"{REST_BASE}/serverless").mock(side_effect=handler)

    result = await list_endpoints(name_prefix="pitwall-")

    assert captured_params == {}
    assert [endpoint.name for endpoint in result] == ["pitwall-one"]


@respx.mock
async def test_list_endpoints_raises_on_non_list_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    respx.get(f"{REST_BASE}/serverless").mock(
        return_value=httpx.Response(200, json={"error": "oops"})
    )

    with pytest.raises(RuntimeError, match="invalid REST v2 endpoints envelope"):
        await list_endpoints()


@respx.mock
async def test_list_endpoints_skips_non_dict_items(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    respx.get(f"{REST_BASE}/serverless").mock(
        return_value=httpx.Response(
            200,
            json={
                "endpoints": [
                    runpod_response_factory.endpoint(endpoint_id="ep-1"),
                    "not-a-dict",
                    None,
                    runpod_response_factory.endpoint(endpoint_id="ep-2"),
                ]
            },
        )
    )

    result = await list_endpoints()

    assert len(result) == 2
    assert result[0].id == "ep-1"
    assert result[1].id == "ep-2"


@respx.mock
async def test_update_scaling_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = cast(dict[str, Any], json.loads(request.content))
        response: httpx.Response = runpod_response_factory.endpoint_response(
            endpoint_id=ENDPOINT_ID,
            workers_min=5,
            workers_max=10,
            idle_timeout=300,
            flashboot=True,
        )
        return response

    respx.patch(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(side_effect=handler)

    new_scaling = EndpointScalingConfig(
        workers_min=5,
        workers_max=10,
        idle_timeout=300,
        flashboot=True,
    )
    result = await update_endpoint_scaling(ENDPOINT_ID, scaling=new_scaling)

    assert isinstance(result, Endpoint)
    assert result.id == ENDPOINT_ID
    assert result.scaling.workers_min == 5
    assert result.scaling.workers_max == 10
    assert result.scaling.idle_timeout == 300
    assert result.scaling.flashboot is True
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["workers"] == {"min": 5, "max": 10, "idleTimeout": 300}
    assert captured["body"]["scaling"] == {"type": "QUEUE_DELAY", "queueDelay": 4.0}
    assert captured["body"]["flashboot"] == "FLASHBOOT"


@respx.mock
async def test_update_scaling_preserves_explicit_gpu_pool_controls(
    monkeypatch: pytest.MonkeyPatch,
    runpod_response_factory: Any,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = cast(dict[str, Any], json.loads(request.content))
        return runpod_response_factory.endpoint_response(endpoint_id=ENDPOINT_ID)

    respx.patch(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(side_effect=handler)

    await update_endpoint_scaling(
        ENDPOINT_ID,
        scaling=EndpointScalingConfig(workers_min=1, workers_max=4),
        gpu_pools=["ADA_24", "AMPERE_48"],
        excluded_gpu_types=["NVIDIA RTX 4090"],
        gpu_count=3,
    )

    assert captured["body"]["gpu"] == {
        "pools": ["ADA_24", "AMPERE_48"],
        "excludedTypes": ["NVIDIA RTX 4090"],
        "count": 3,
    }


@respx.mock
async def test_update_scaling_rejects_gpu_type_and_pool_before_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    route = respx.patch(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(
        return_value=httpx.Response(200, json={})
    )

    with pytest.raises(RuntimeError, match="gpu_type_id or gpu_pools"):
        await update_endpoint_scaling(
            ENDPOINT_ID,
            scaling=EndpointScalingConfig(gpu_type_id="NVIDIA L4"),
            gpu_pools=["ADA_24"],
        )

    assert route.call_count == 0


@respx.mock
async def test_update_scaling_rejects_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    respx.patch(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )

    with pytest.raises(RunPodRestError) as exc_info:
        await update_endpoint_scaling(ENDPOINT_ID, scaling=EndpointScalingConfig())

    assert exc_info.value.status_code == 403


@respx.mock
async def test_delete_endpoint_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_auth: list[str] = []
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        captured_auth.append(request.headers.get("authorization", ""))
        return httpx.Response(204)

    respx.delete(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(side_effect=handler)

    result = await delete_endpoint(ENDPOINT_ID)

    assert result == {}
    assert captured_auth == ["Bearer test-key"]


@respx.mock
async def test_delete_endpoint_normalizes_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    route = respx.delete(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(
        return_value=httpx.Response(204)
    )

    await delete_endpoint(f"  /{ENDPOINT_ID}/  ")

    assert route.call_count == 1


@respx.mock
async def test_delete_endpoint_raises_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    respx.delete(f"{REST_BASE}/serverless/{ENDPOINT_ID}").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )

    with pytest.raises(RunPodRestError) as exc_info:
        await delete_endpoint(ENDPOINT_ID)

    assert exc_info.value.status_code == 404


async def test_delete_endpoint_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="endpoint_id must be non-empty"):
        await delete_endpoint("  ")


async def test_delete_endpoint_rejects_path_separator_in_id() -> None:
    with pytest.raises(ValueError, match="endpoint_id must not contain path separators"):
        await delete_endpoint("ep-1/subpath")
