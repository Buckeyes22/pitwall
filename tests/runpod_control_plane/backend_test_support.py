"""Shared in-memory RunPod backend for control-plane, MCP, and fuzz tests."""

from __future__ import annotations

from typing import Any

from pitwall.runpod_client.mounts import NetworkVolume
from pitwall.runpod_client.pods import RunPodRestError
from pitwall.runpod_client.registry import ContainerRegistryAuth, RegistryAuthError
from pitwall.runpod_client.serverless import Endpoint, EndpointScalingConfig
from pitwall.runpod_client.templates import HubTemplate, Template, TemplateNotFoundError
from pitwall.runpod_control_plane import (
    EndpointCreateRequest,
    EndpointGpuRequest,
    EndpointUpdateRequest,
    PodActionRequest,
    PodCreateRequest,
    PodUpdateRequest,
    RegistryAuthCreateRequest,
    RegistryAuthReplaceRequest,
    RunPodControlPlaneService,
    StrictRunPodBackend,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    VolumeCreateRequest,
    VolumeGrowRequest,
)
from pitwall.security.pre_spend import PreSpendInspectionService
from tests.runpod_control_plane.journal_fakes import FakeJournalPool


def idempotency_key_for(suffix: str) -> str:
    return f"resource-test-{suffix}"


class RecordingBackend(StrictRunPodBackend):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_registry_create = False
        self.pod_present = True
        self.endpoint_present = True
        self.template_present = True
        self.volume_present = True
        self.registry_present = True

    async def list_pods(self) -> list[dict[str, Any]]:
        self.calls.append("pods.list")
        return [self._pod("pod_existing", "existing-pod")]

    async def get_pod(self, resource_id: str) -> dict[str, Any] | None:
        self.calls.append("pods.get")
        return self._pod(resource_id, "pod") if self.pod_present else None

    async def create_pod(self, request: PodCreateRequest) -> dict[str, Any]:
        self.calls.append("pods.create")
        return self._pod("pod_new", request.name)

    async def update_pod(self, request: PodUpdateRequest) -> dict[str, Any]:
        self.calls.append("pods.update")
        return self._pod(request.resource_id, "pod")

    async def action_pod(self, request: PodActionRequest) -> dict[str, Any]:
        self.calls.append(f"pods.{request.action}")
        return self._pod(request.resource_id, "pod")

    async def terminate_pod(self, resource_id: str) -> None:
        self.calls.append("pods.terminate")
        self.pod_present = False

    async def list_endpoints(self) -> list[Endpoint]:
        self.calls.append("endpoints.list")
        return [self._endpoint("ep_existing", "existing-endpoint")]

    async def get_endpoint(self, resource_id: str) -> Endpoint:
        self.calls.append("endpoints.get")
        if not self.endpoint_present:
            raise RunPodRestError("GET", f"serverless/{resource_id}", 404, "not found")
        return self._endpoint(resource_id, "endpoint")

    async def resolve_endpoint_gpu_types(
        self, gpu_type_ids: list[str], *, gpu_count: int
    ) -> EndpointGpuRequest:
        self.calls.append("endpoints.resolve_gpu_types")
        assert gpu_type_ids == ["NVIDIA L4"]
        return EndpointGpuRequest(
            pools=["ADA_24"],
            excluded_type_ids=["GPU-X"],
            count=gpu_count,
        )

    async def create_endpoint(self, request: EndpointCreateRequest) -> Endpoint:
        self.calls.append("endpoints.create")
        return self._endpoint(
            "ep_new",
            request.name,
            pools=request.gpu.pools,
            count=request.gpu.count,
        )

    async def update_endpoint(self, request: EndpointUpdateRequest) -> Endpoint:
        self.calls.append("endpoints.update")
        pools = request.gpu.pools if request.gpu else []
        count = request.gpu.count if request.gpu else 1
        return self._endpoint(request.resource_id, "endpoint", pools=pools, count=count)

    async def delete_endpoint(self, resource_id: str) -> None:
        self.calls.append("endpoints.delete")
        self.endpoint_present = False

    async def list_templates(self) -> list[Template]:
        self.calls.append("templates.list")
        return [self._template("tmpl_existing", "existing-template")]

    async def get_template(self, resource_id: str) -> Template:
        self.calls.append("templates.get")
        if not self.template_present:
            raise TemplateNotFoundError(resource_id)
        return self._template(resource_id, "template")

    async def create_template(self, request: TemplateCreateRequest) -> str:
        self.calls.append("templates.create")
        return "tmpl_new"

    async def update_template(self, request: TemplateUpdateRequest) -> Template:
        self.calls.append("templates.update")
        return self._template(request.resource_id, request.name or "template")

    async def delete_template(self, resource_id: str) -> None:
        self.calls.append("templates.delete")
        self.template_present = False

    async def list_volumes(self) -> list[NetworkVolume]:
        self.calls.append("volumes.list")
        return [self._volume("vol_existing", "existing-volume", 20)]

    async def get_volume(self, resource_id: str) -> NetworkVolume:
        self.calls.append("volumes.get")
        if not self.volume_present:
            from pitwall.runpod_client.pods import RunPodRestError

            raise RunPodRestError("GET", resource_id, 404, "not found")
        return self._volume(resource_id, "volume", 20)

    async def create_volume(self, request: VolumeCreateRequest) -> NetworkVolume:
        self.calls.append("volumes.create")
        return self._volume("vol_new", request.name, request.size_gb)

    async def grow_volume(self, request: VolumeGrowRequest) -> NetworkVolume:
        self.calls.append("volumes.grow")
        return self._volume(request.resource_id, "volume", request.size_gb)

    async def delete_volume(self, resource_id: str) -> None:
        self.calls.append("volumes.delete")
        self.volume_present = False

    async def list_registry_auths(self, timeout_s: float) -> list[ContainerRegistryAuth]:
        self.calls.append("registry.list")
        return [ContainerRegistryAuth(id="auth_existing", name="existing-auth")]

    async def get_registry_auth(
        self, resource_id: str, timeout_s: float
    ) -> ContainerRegistryAuth | None:
        self.calls.append("registry.get")
        if not self.registry_present:
            return None
        return ContainerRegistryAuth(id=resource_id, name="auth")

    async def create_registry_auth(
        self,
        request: RegistryAuthCreateRequest | RegistryAuthReplaceRequest,
        password: Any,
        timeout_s: float,
    ) -> ContainerRegistryAuth:
        self.calls.append("registry.create")
        if self.fail_registry_create:
            raise RegistryAuthError("provider rejected password super-secret", status_code=403)
        return ContainerRegistryAuth(id="auth_new", name=request.name)

    async def delete_registry_auth(self, resource_id: str, timeout_s: float) -> None:
        self.calls.append("registry.delete")
        self.registry_present = False

    async def list_hub_templates(self, *, limit: int, offset: int) -> list[HubTemplate]:
        self.calls.append("hub.list")
        return [self._hub("hub_one", "Searchable Worker")]

    async def get_hub_template(self, resource_id: str) -> HubTemplate:
        self.calls.append("hub.get")
        return self._hub(resource_id, "Hub Worker")

    @staticmethod
    def _pod(resource_id: str, name: str) -> dict[str, Any]:
        return {
            "id": resource_id,
            "name": name,
            "desiredStatus": "RUNNING",
            "image": "example/image:1",
            "gpu": {"id": "NVIDIA L4", "count": 1},
            "costPerHr": "0.50",
        }

    @staticmethod
    def _endpoint(
        resource_id: str,
        name: str,
        *,
        pools: list[str] | None = None,
        count: int = 1,
    ) -> Endpoint:
        return Endpoint(
            id=resource_id,
            name=name,
            template_id="tmpl_existing",
            scaling=EndpointScalingConfig(
                workers_min=0,
                workers_max=2,
                idle_timeout=60,
                scaler_type="QUEUE_DELAY",
                scaler_value=4,
            ),
            raw={
                "id": resource_id,
                "name": name,
                "type": "QUEUE",
                "gpu": {
                    "pools": pools or ["AMPERE_24"],
                    "excludedTypes": ["GPU-X"],
                    "count": count,
                },
            },
        )

    @staticmethod
    def _template(resource_id: str, name: str) -> Template:
        return Template(
            id=resource_id,
            name=name,
            imageName="example/image:1",
            dockerArgs="--token arg-secret",
            containerDiskInGb=20,
            env=[
                {"key": "SAFE", "value": "not-returned"},
                {"key": "API_KEY", "value": "also-not-returned"},
            ],
            isServerless=False,
        )

    @staticmethod
    def _volume(resource_id: str, name: str, size: int) -> NetworkVolume:
        return NetworkVolume(
            id=resource_id,
            name=name,
            size=size,
            dataCenter="US-KS-1",
        )

    @staticmethod
    def _hub(resource_id: str, name: str) -> HubTemplate:
        return HubTemplate(
            id=resource_id,
            name=name,
            imageName="public/image:1",
            isServerless=True,
        )


def control_plane_service(
    backend: RecordingBackend,
    *,
    audit: bool = True,
    environ: dict[str, str] | None = None,
    timeout_s: float = 1,
    inspection_service: PreSpendInspectionService | None = None,
) -> RunPodControlPlaneService:
    return RunPodControlPlaneService(
        backend=backend,
        audit_pool=FakeJournalPool() if audit else None,
        environ=environ
        or {"REGISTRY_PASSWORD": "super-secret", "RUNPOD_API_KEY": "service-test-runpod-key"},
        timeout_s=timeout_s,
        inspection_service=inspection_service,
    )
