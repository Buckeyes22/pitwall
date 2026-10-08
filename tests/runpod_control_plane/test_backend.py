"""Call-equivalence tests for the adapter over existing strict clients."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from pitwall.runpod_client.mounts import NetworkVolume
from pitwall.runpod_client.registry import ContainerRegistryAuth
from pitwall.runpod_client.serverless import (
    Endpoint,
    EndpointScalingConfig,
    V2EndpointGpuRequest,
)
from pitwall.runpod_client.templates import HubTemplate, Template
from pitwall.runpod_control_plane import (
    EndpointCreateRequest,
    EndpointUpdateRequest,
    PodActionRequest,
    PodCreateRequest,
    PodUpdateRequest,
    RegistryAuthCreateRequest,
    StrictRunPodBackend,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    VolumeCreateRequest,
    VolumeGrowRequest,
)

pytestmark = pytest.mark.anyio


def _endpoint(resource_id: str = "ep_one") -> Endpoint:
    return Endpoint(
        id=resource_id,
        name="endpoint",
        scaling=EndpointScalingConfig(),
    )


def _template(resource_id: str = "tmpl_one") -> Template:
    return Template(
        id=resource_id,
        name="template",
        imageName="example/image:1",
    )


async def test_pod_methods_preserve_the_existing_strict_client_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = StrictRunPodBackend()
    listing = AsyncMock(return_value=[{"id": "pod_one"}])
    getter = AsyncMock(return_value={"id": "pod_one"})
    creator = AsyncMock(return_value={"id": "pod_new"})
    updater = AsyncMock(return_value={"id": "pod_one"})
    starter = AsyncMock(return_value={"id": "pod_one"})
    terminator = AsyncMock(return_value=None)
    monkeypatch.setattr("pitwall.runpod_control_plane.get_pods", listing)
    monkeypatch.setattr("pitwall.runpod_control_plane.get_pod_strict", getter)
    monkeypatch.setattr("pitwall.runpod_control_plane.create_pod_with_fallback", creator)
    monkeypatch.setattr("pitwall.runpod_control_plane.update_pod", updater)
    monkeypatch.setattr("pitwall.runpod_control_plane.start_pod", starter)
    monkeypatch.setattr("pitwall.runpod_control_plane.terminate_pod", terminator)

    create_request = PodCreateRequest(
        intent="apply",
        idempotency_key="backend-pod-create",
        name="raw-pod",
        image="example/image:1",
        gpu_type_ids=["NVIDIA L4"],
        ttl_minutes=60,
        gpu_count=2,
        data_center_id="US-KS-1",
        network_volume_id="vol_one",
        ports=["8000/http"],
        env={"SAFE": "value"},
        args=["python", "worker.py"],
        registry_auth_id="auth_one",
        max_cost_per_hour="1.25",
    )
    update_request = PodUpdateRequest(
        intent="apply",
        idempotency_key="backend-pod-update",
        resource_id="pod_one",
        ports=["9000/http"],
    )
    action_request = PodActionRequest(
        intent="apply",
        idempotency_key="backend-pod-action",
        resource_id="pod_one",
        action="start",
    )

    assert await backend.list_pods() == [{"id": "pod_one"}]
    assert await backend.get_pod("pod_one") == {"id": "pod_one"}
    await backend.create_pod(create_request)
    await backend.update_pod(update_request)
    await backend.action_pod(action_request)
    await backend.terminate_pod("pod_one")

    listing.assert_awaited_once_with()
    getter.assert_awaited_once_with("pod_one")  # get_pod_strict: an outage raises
    create_kwargs = creator.await_args.kwargs
    assert create_kwargs["name"] == "raw-pod"
    assert create_kwargs["image_name"] == "example/image:1"
    assert create_kwargs["workload"].gpu_types == ["NVIDIA L4"]
    assert create_kwargs["workload"].gpu_count == 2
    assert create_kwargs["network_volume_id"] == "vol_one"
    assert create_kwargs["data_center_id"] == "US-KS-1"
    assert create_kwargs["docker_start_cmd"] == ["python", "worker.py"]
    assert create_kwargs["container_registry_auth_id"] == "auth_one"
    assert create_kwargs["max_cost_per_hr"] == 1.25
    assert create_kwargs["wait_for_readiness"] is False
    updater.assert_awaited_once_with(
        "pod_one",
        env=None,
        ports=["9000/http"],
        container_registry_auth_id=None,
    )
    starter.assert_awaited_once_with("pod_one")
    terminator.assert_awaited_once_with("pod_one")


async def test_endpoint_methods_preserve_nested_workers_scaling_and_gpu_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = StrictRunPodBackend()
    creator = AsyncMock(return_value=_endpoint("ep_new"))
    updater = AsyncMock(return_value=_endpoint())
    monkeypatch.setattr("pitwall.runpod_control_plane.create_endpoint", creator)
    monkeypatch.setattr("pitwall.runpod_control_plane.update_endpoint_scaling", updater)

    create = EndpointCreateRequest.model_validate(
        {
            "intent": "apply",
            "idempotency_key": "backend-endpoint-create",
            "name": "endpoint",
            "endpoint_type": "LOAD_BALANCER",
            "template_id": "tmpl_one",
            "workers": {
                "minimum": 2,
                "maximum": 7,
                "idle_timeout_seconds": 90,
            },
            "scaling": {"type": "REQUEST_COUNT", "value": 3},
            "gpu": {
                "pools": ["ADA_24", "AMPERE_48"],
                "excluded_type_ids": ["GPU-X"],
                "count": 2,
            },
            "flashboot": True,
        }
    )
    update = EndpointUpdateRequest.model_validate(
        {
            "intent": "apply",
            "idempotency_key": "backend-endpoint-update",
            "resource_id": "ep_one",
            "workers": {
                "minimum": 1,
                "maximum": 5,
                "idle_timeout_seconds": 120,
            },
            "scaling": {"type": "QUEUE_DELAY", "value": 4},
            "gpu": {
                "pools": ["AMPERE_24"],
                "excluded_type_ids": ["GPU-Y"],
                "count": 3,
            },
        }
    )

    await backend.create_endpoint(create)
    await backend.update_endpoint(update)

    create_kwargs = creator.await_args.kwargs
    assert create_kwargs["gpu_pools"] == ["ADA_24", "AMPERE_48"]
    assert create_kwargs["excluded_gpu_types"] == ["GPU-X"]
    assert create_kwargs["gpu_count"] == 2
    assert create_kwargs["endpoint_type"] == "LOAD_BALANCER"
    create_scaling = create_kwargs["scaling"]
    assert (create_scaling.workers_min, create_scaling.workers_max) == (2, 7)
    assert create_scaling.scaler_type == "REQUEST_COUNT"
    assert create_scaling.scaler_value == 3
    assert create_scaling.flashboot is True

    update_args = updater.await_args.args
    update_kwargs = updater.await_args.kwargs
    assert update_args[0] == "ep_one"
    assert (update_args[1].workers_min, update_args[1].workers_max) == (1, 5)
    assert update_args[1].idle_timeout == 120
    assert update_args[1].scaler_type == "QUEUE_DELAY"
    assert update_kwargs["gpu_pools"] == ["AMPERE_24"]
    assert update_kwargs["excluded_gpu_types"] == ["GPU-Y"]
    assert update_kwargs["gpu_count"] == 3


async def test_endpoint_gpu_types_resolve_through_strict_v2_catalogue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = StrictRunPodBackend()
    resolver = AsyncMock(
        return_value=V2EndpointGpuRequest(
            pools=["ADA_24"],
            excludedTypes=["NVIDIA GeForce RTX 4090"],
        )
    )
    monkeypatch.setattr(
        "pitwall.runpod_control_plane.resolve_gpu_selection_for_types",
        resolver,
    )

    selection = await backend.resolve_endpoint_gpu_types(["NVIDIA L4"], gpu_count=2)

    resolver.assert_awaited_once_with(["NVIDIA L4"])
    assert selection.pools == ["ADA_24"]
    assert selection.excluded_type_ids == ["NVIDIA GeForce RTX 4090"]
    assert selection.count == 2


async def test_template_and_hub_calls_stay_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = StrictRunPodBackend()
    account_listing = AsyncMock(return_value=[_template()])
    account_get = AsyncMock(return_value=_template())
    account_create = AsyncMock(return_value="tmpl_new")
    account_update = AsyncMock(return_value=_template())
    account_delete = AsyncMock(return_value=True)
    hub_listing = AsyncMock(
        return_value=[HubTemplate(id="hub_one", name="public", imageName="runpod/public:1")]
    )
    hub_get = AsyncMock(
        return_value=HubTemplate(id="hub_one", name="public", imageName="runpod/public:1")
    )
    monkeypatch.setattr("pitwall.runpod_control_plane.list_account_templates", account_listing)
    monkeypatch.setattr("pitwall.runpod_control_plane.get_template", account_get)
    monkeypatch.setattr("pitwall.runpod_control_plane.create_template_rest", account_create)
    monkeypatch.setattr("pitwall.runpod_control_plane.update_template", account_update)
    monkeypatch.setattr("pitwall.runpod_control_plane.delete_template", account_delete)
    monkeypatch.setattr("pitwall.runpod_control_plane.list_hub_templates", hub_listing)
    monkeypatch.setattr("pitwall.runpod_control_plane.get_hub_template", hub_get)

    create = TemplateCreateRequest(
        intent="apply",
        idempotency_key="backend-template-create",
        name="template",
        image="example/image:1",
        env={"SAFE": "value"},
        ports=["8000/http"],
    )
    update = TemplateUpdateRequest(
        intent="apply",
        idempotency_key="backend-template-update",
        resource_id="tmpl_one",
        name="updated",
        ports=["9000/http"],
    )

    await backend.list_templates()
    await backend.get_template("tmpl_one")
    await backend.create_template(create)
    await backend.update_template(update)
    await backend.delete_template("tmpl_one")
    await backend.list_hub_templates(limit=25, offset=50)
    await backend.get_hub_template("hub_one")

    account_listing.assert_awaited_once_with()
    account_get.assert_awaited_once_with("tmpl_one")
    assert account_create.await_args.kwargs["env"] == {"SAFE": "value"}
    assert account_create.await_args.kwargs["ports"] == ["8000/http"]
    assert account_update.await_args.kwargs["ports"] == "9000/http"
    account_delete.assert_awaited_once_with("tmpl_one")
    hub_listing.assert_awaited_once_with(limit=25, offset=50)
    hub_get.assert_awaited_once_with("hub_one")


async def test_volume_adapter_closes_each_strict_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume = NetworkVolume(id="vol_one", name="volume", size=20, dataCenter="US-KS-1")

    class FakeVolumeClient:
        def __init__(self) -> None:
            self.list = AsyncMock(return_value=[volume])
            self.get = AsyncMock(return_value=volume)
            self.create = AsyncMock(return_value=volume)
            self.update = AsyncMock(return_value=volume)
            self.delete = AsyncMock(return_value=None)
            self.aclose = AsyncMock(return_value=None)

    clients: list[FakeVolumeClient] = []

    def factory() -> FakeVolumeClient:
        client = FakeVolumeClient()
        clients.append(client)
        return client

    monkeypatch.setattr("pitwall.runpod_control_plane.NetworkVolumeClient", factory)
    backend = StrictRunPodBackend()
    create = VolumeCreateRequest(
        intent="apply",
        idempotency_key="backend-volume-create",
        name="volume",
        size_gb=20,
        data_center_id="US-KS-1",
    )
    grow = VolumeGrowRequest(
        intent="apply",
        idempotency_key="backend-volume-grow",
        resource_id="vol_one",
        size_gb=40,
    )

    await backend.list_volumes()
    await backend.get_volume("vol_one")
    await backend.create_volume(create)
    await backend.grow_volume(grow)
    await backend.delete_volume("vol_one")

    assert len(clients) == 5
    clients[0].list.assert_awaited_once_with()
    clients[1].get.assert_awaited_once_with("vol_one")
    clients[2].create.assert_awaited_once_with("volume", 20, "US-KS-1")
    clients[3].update.assert_awaited_once_with("vol_one", 40)
    clients[4].delete.assert_awaited_once_with("vol_one")
    assert all(client.aclose.await_count == 1 for client in clients)


async def test_registry_adapter_resolves_secret_only_at_client_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = AsyncMock(return_value=[ContainerRegistryAuth(id="auth_one", name="auth")])
    getter = AsyncMock(return_value=ContainerRegistryAuth(id="auth_one", name="auth"))
    creator = AsyncMock(return_value=ContainerRegistryAuth(id="auth_new", name="auth"))
    deleter = AsyncMock(return_value=None)
    monkeypatch.setattr("pitwall.runpod_control_plane.list_container_registry_auths", listing)
    monkeypatch.setattr("pitwall.runpod_control_plane.get_container_registry_auth", getter)
    monkeypatch.setattr("pitwall.runpod_control_plane.create_container_registry_auth", creator)
    monkeypatch.setattr("pitwall.runpod_control_plane.delete_container_registry_auth", deleter)
    backend = StrictRunPodBackend()
    request = RegistryAuthCreateRequest(
        intent="apply",
        idempotency_key="backend-registry-create",
        name="registry",
        username="robot",
        password_env="REGISTRY_PASSWORD",
    )

    await backend.list_registry_auths(12.5)
    await backend.get_registry_auth("auth_one", 12.5)
    await backend.create_registry_auth(request, SecretStr("secret-value"), 12.5)
    await backend.delete_registry_auth("auth_one", 12.5)

    listing.assert_awaited_once_with(timeout_s=12.5)
    getter.assert_awaited_once_with("auth_one", timeout_s=12.5)
    creator.assert_awaited_once_with(
        "registry",
        "robot",
        "secret-value",
        timeout_s=12.5,
    )
    deleter.assert_awaited_once_with("auth_one", timeout_s=12.5)
