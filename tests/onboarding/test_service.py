from __future__ import annotations

import asyncio
import datetime as dt
from collections import defaultdict
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest

from pitwall import onboarding
from pitwall.core.models import Capability, Provider
from pitwall.onboarding import (
    EndpointSelection,
    OnboardingAction,
    OnboardingCommand,
    OnboardingError,
    OnboardingEvent,
    OnboardingStatus,
    OnboardingTopology,
    RegistrySelection,
    RunPodOnboardingRequest,
    RunPodOnboardingService,
    TemplateSelection,
    VolumeSelection,
)
from pitwall.runpod_client.discovery import (
    DatacenterCatalogEntry,
    GpuCatalogEntry,
    GpuDiscoverySnapshot,
)
from pitwall.runpod_control_plane import (
    ControlPlaneMutationAudit,
    EndpointCreateRequest,
    EndpointGpuRequest,
    EndpointResource,
    IdentifiedMutationRequest,
    MutationResult,
    PodCreateRequest,
    PostgresRunPodMutationJournal,
    RegistryAuthCreateRequest,
    RegistryAuthResource,
    RunPodControlPlaneError,
    TemplateCreateRequest,
    TemplateResource,
    VolumeCreateRequest,
    VolumeResource,
    _no_preconditions,
    _request_hash,
)
from tests.runpod_control_plane.journal_fakes import FakeJournalPool, recording_insert_audit


class FakeDiscovery:
    def __init__(self) -> None:
        self.fail = False
        self.calls = 0
        self.snapshot = GpuDiscoverySnapshot(
            fetched_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            gpus=(
                GpuCatalogEntry(
                    gpu_type_id="NVIDIA L4",
                    secure_cloud=True,
                    community_cloud=True,
                    secure_price=Decimal("0.50"),
                    community_price=Decimal("0.40"),
                    datacenter_ids=("US-KS-2",),
                    available_gpu_counts=(1,),
                ),
            ),
            datacenters=(
                DatacenterCatalogEntry(
                    datacenter_id="US-KS-2",
                    storage_support=True,
                    listed=True,
                    gpu_types=("NVIDIA L4",),
                    gpu_availability={"NVIDIA L4": True},
                ),
            ),
        )

    async def get_snapshot(self) -> GpuDiscoverySnapshot:
        self.calls += 1
        if self.fail:
            raise RuntimeError("discovery credential should never escape")
        return self.snapshot

    async def aclose(self) -> None:
        return None


class FakeState:
    def __init__(self) -> None:
        self.capabilities: dict[str, Capability] = {}
        self.providers: dict[str, Provider] = {}
        self.events: dict[str, list[OnboardingEvent]] = defaultdict(list)
        self.ledger: list[str] = []
        self.fail_at: str | None = None
        self.fail_event_at: str | None = None
        self._apply_lock = asyncio.Lock()

    @asynccontextmanager
    async def apply_lock(self, plan_id: str):
        del plan_id
        async with self._apply_lock:
            yield

    async def get_capability(self, name: str) -> Capability | None:
        return self.capabilities.get(name)

    async def get_capability_by_id(self, capability_id: str) -> Capability | None:
        return next(
            (value for value in self.capabilities.values() if value.id == capability_id),
            None,
        )

    async def get_provider(self, name: str) -> Provider | None:
        return self.providers.get(name)

    async def get_provider_by_id(self, provider_id: str) -> Provider | None:
        return next((value for value in self.providers.values() if value.id == provider_id), None)

    async def create_capability(self, request: RunPodOnboardingRequest) -> Capability:
        self._fail("capability")
        self.ledger.append("write:capability")
        value = onboarding._proposed_capability(request, now=dt.datetime.now(dt.UTC))
        self.capabilities[request.capability_name] = value
        return value

    async def create_provider(
        self,
        request: RunPodOnboardingRequest,
        *,
        capability: Capability,
        endpoint_id: str | None,
        template_id: str | None,
        volume_id: str | None,
    ) -> Provider:
        self._fail("provider")
        self.ledger.append("write:provider")
        value = onboarding._proposed_provider(
            request,
            capability=capability,
            endpoint_id=endpoint_id,
            template_id=template_id,
            volume_id=volume_id,
            now=dt.datetime.now(dt.UTC),
        )
        self.providers[request.provider_name] = value
        return value

    async def enable_provider(self, provider_id: str) -> Provider | None:
        self.ledger.append("write:provider.enable")
        value = await self.get_provider_by_id(provider_id)
        if value is None:
            return None
        enabled = value.model_copy(update={"enabled": True})
        self.providers[enabled.name] = enabled
        return enabled

    async def enable_capability(self, capability_id: str) -> Capability | None:
        self.ledger.append("write:capability.enable")
        value = next(
            (item for item in self.capabilities.values() if item.id == capability_id),
            None,
        )
        if value is None:
            return None
        enabled = value.model_copy(update={"enabled": True})
        self.capabilities[enabled.name] = enabled
        return enabled

    async def disable_provider(self, provider_id: str) -> None:
        self.ledger.append("write:provider.disable")
        value = await self.get_provider_by_id(provider_id)
        if value is not None:
            self.providers[value.name] = value.model_copy(update={"enabled": False})

    async def disable_capability(self, capability_id: str) -> None:
        self.ledger.append("write:capability.disable")
        for name, value in self.capabilities.items():
            if value.id == capability_id:
                self.capabilities[name] = value.model_copy(update={"enabled": False})
                break

    async def load_events(self, plan_id: str) -> tuple[OnboardingEvent, ...]:
        return tuple(self.events[plan_id])

    async def append_event(self, plan_id: str, event: OnboardingEvent) -> None:
        if self.fail_event_at == event.step:
            raise RuntimeError("audit persistence detail")
        self.ledger.append(f"audit:{event.step}:{event.state.value}")
        self.events[plan_id].append(event)

    def _fail(self, step: str) -> None:
        if self.fail_at == step:
            raise RuntimeError("secret-provider-detail")


class FakeResources:
    def __init__(self) -> None:
        self.registries: dict[str, RegistryAuthResource] = {}
        self.volumes: dict[str, VolumeResource] = {}
        self.templates: dict[str, TemplateResource] = {}
        self.endpoints: dict[str, EndpointResource] = {}
        self.ledger: list[str] = []
        self.fail_at: str | None = None
        self.released: list[str] = []
        self._next = 0

    async def list_registry_auths(self) -> list[RegistryAuthResource]:
        self.ledger.append("read:registry")
        return list(self.registries.values())

    async def get_registry_auth(self, resource_id: str) -> RegistryAuthResource:
        self.ledger.append("read:registry")
        return self._existing(self.registries, resource_id, "registry_auth")

    async def create_registry_auth(self, request: RegistryAuthCreateRequest) -> MutationResult:
        self._fail("registry_auth")
        self.ledger.append(f"write:registry:{request.intent}")
        resource_id = self._id("registry")
        resource = RegistryAuthResource(id=resource_id, name=request.name)
        self.registries[resource_id] = resource
        return self._result(request.idempotency_key, "registry_auth.create", resource_id)

    async def delete_registry_auth(self, request: IdentifiedMutationRequest) -> MutationResult:
        self.ledger.append("write:registry.delete")
        self.registries.pop(request.resource_id, None)
        return self._result(request.idempotency_key, "registry_auth.delete", request.resource_id)

    async def list_volumes(self) -> list[VolumeResource]:
        self.ledger.append("read:volume")
        return list(self.volumes.values())

    async def get_volume(self, resource_id: str) -> VolumeResource:
        self.ledger.append("read:volume")
        return self._existing(self.volumes, resource_id, "volume")

    async def create_volume(self, request: VolumeCreateRequest) -> MutationResult:
        self._fail("volume")
        self.ledger.append(f"write:volume:{request.intent}")
        resource_id = self._id("volume")
        resource = VolumeResource(
            id=resource_id,
            name=request.name,
            size_gb=request.size_gb,
            data_center_id=request.data_center_id,
        )
        self.volumes[resource_id] = resource
        return self._result(request.idempotency_key, "volume.create", resource_id)

    async def list_templates(self) -> list[TemplateResource]:
        self.ledger.append("read:template")
        return list(self.templates.values())

    async def get_template(self, resource_id: str) -> TemplateResource:
        self.ledger.append("read:template")
        return self._existing(self.templates, resource_id, "template")

    async def create_template(self, request: TemplateCreateRequest) -> MutationResult:
        self._fail("template")
        self.ledger.append(f"write:template:{request.intent}")
        resource_id = self._id("template")
        resource = TemplateResource(
            id=resource_id,
            name=request.name,
            image=request.image,
            docker_args_fingerprint=onboarding._opaque_fingerprint(
                onboarding.argv_to_v2_args(request.args) if request.args else None
            ),
            env_fingerprint=onboarding._environment_fingerprint(request.env),
            disk_gb=request.disk_gb,
            volume_gb=request.volume_gb,
            ports=tuple(request.ports),
            env_keys=tuple(sorted(request.env)),
            serverless=request.serverless,
            public=False,
            registry_auth_id=request.registry_auth_id,
        )
        self.templates[resource_id] = resource
        return self._result(request.idempotency_key, "template.create", resource_id)

    async def delete_template(self, request: IdentifiedMutationRequest) -> MutationResult:
        self.ledger.append("write:template.delete")
        self.templates.pop(request.resource_id, None)
        return self._result(request.idempotency_key, "template.delete", request.resource_id)

    async def list_endpoints(self) -> list[EndpointResource]:
        self.ledger.append("read:endpoint")
        return list(self.endpoints.values())

    async def get_endpoint(self, resource_id: str) -> EndpointResource:
        self.ledger.append("read:endpoint")
        return self._existing(self.endpoints, resource_id, "endpoint")

    async def resolve_endpoint_gpu_types(
        self, gpu_type_ids: list[str], *, gpu_count: int
    ) -> EndpointGpuRequest:
        self.ledger.append("read:endpoint_gpu_selection")
        assert gpu_type_ids == ["NVIDIA L4"]
        return EndpointGpuRequest(pools=["ADA_24"], count=gpu_count)

    async def create_endpoint(self, request: EndpointCreateRequest) -> MutationResult:
        self._fail("endpoint")
        self.ledger.append(f"write:endpoint:{request.intent}")
        resource_id = self._id("endpoint")
        resource = EndpointResource(
            id=resource_id,
            name=request.name,
            endpoint_type=request.endpoint_type,
            workers=request.workers,
            scaling=request.scaling,
            flashboot=request.flashboot,
            template_id=request.template_id,
            image=request.image,
            gpu_pools=tuple(request.gpu.pools),
            excluded_gpu_type_ids=tuple(request.gpu.excluded_type_ids),
            gpu_count=request.gpu.count,
        )
        self.endpoints[resource_id] = resource
        return self._result(request.idempotency_key, "endpoint.create", resource_id)

    async def delete_endpoint(self, request: IdentifiedMutationRequest) -> MutationResult:
        self.ledger.append("write:endpoint.delete")
        self.endpoints.pop(request.resource_id, None)
        return self._result(request.idempotency_key, "endpoint.delete", request.resource_id)

    async def create_pod(self, request: PodCreateRequest) -> MutationResult:
        self._fail("pod_request_preview")
        self.ledger.append(f"preview:pod:{request.intent}")
        assert request.intent == "preview"
        return MutationResult(
            operation="pod.create",
            resource_type="pod",
            resource_id=None,
            dry_run=True,
            changed=False,
            effect="preview",
            estimated_ceiling=f"{request.max_cost_per_hour} USD/hour",
            idempotency_key=request.idempotency_key,
        )

    async def release_idempotency_key(self, idempotency_key: str, *, reason: str) -> bool:
        self.released.append(idempotency_key)
        return False

    async def idempotency_key_status(self, idempotency_key: str) -> None:
        return None

    @staticmethod
    def _existing(values: dict[str, Any], resource_id: str, resource_type: str) -> Any:
        if resource_id not in values:
            raise RunPodControlPlaneError(
                "resource_not_found",
                f"RunPod {resource_type} was not found",
                operation="get",
                resource_type=resource_type,
                resource_id=resource_id,
            )
        return values[resource_id]

    def _id(self, kind: str) -> str:
        self._next += 1
        return f"{kind}_{self._next}"

    def _fail(self, step: str) -> None:
        if self.fail_at == step:
            raise RuntimeError("credential-value-that-must-not-escape")

    @staticmethod
    def _result(key: str, operation: str, resource_id: str) -> MutationResult:
        return MutationResult(
            operation=operation,
            resource_type=operation.split(".")[0],
            resource_id=resource_id,
            dry_run=False,
            changed=True,
            effect=operation,
            idempotency_key=key,
        )


class JournaledResources:
    """FakeResources whose apply mutations run through the real control-plane journal.

    Each create and delete goes through ``PostgresRunPodMutationJournal`` and the real
    ``_request_hash`` over an in-memory ``config_audit``, so a repeated step key replays
    exactly as it would against the production service.
    """

    _ROUTES = {
        "create_registry_auth": ("registry_auth.create", "registry_auth", "provider", "create"),
        "create_volume": ("volume.create", "volume", "volume", "create"),
        "create_template": ("template.create", "template", "template", "create"),
        "create_endpoint": ("endpoint.create", "endpoint", "provider", "create"),
        "delete_registry_auth": ("registry_auth.delete", "registry_auth", "provider", "delete"),
        "delete_template": ("template.delete", "template", "template", "delete"),
        "delete_endpoint": ("endpoint.delete", "endpoint", "provider", "delete"),
    }

    def __init__(self, inner: FakeResources, pool: FakeJournalPool) -> None:
        self.inner = inner
        self.pool = pool
        self._journal = PostgresRunPodMutationJournal(pool, actor="system")

    def __getattr__(self, name: str) -> Any:
        route = self._ROUTES.get(name)
        call = getattr(self.inner, name)
        if route is None:
            return call
        operation, resource_type, entity_type, action = route

        async def journaled(request: Any) -> MutationResult:
            audit = ControlPlaneMutationAudit(
                operation=operation,
                resource_type=resource_type,
                entity_type=entity_type,
                action=action,
                resource_id=getattr(request, "resource_id", None),
                idempotency_key=request.idempotency_key,
                request_hash=_request_hash(operation, request, b"onboarding-journal-key"),
            )

            async def apply(_: None, conn: Any) -> MutationResult:
                return await call(request)

            return await self._journal.run_idempotent(audit, _no_preconditions, apply)

        return journaled

    async def release_idempotency_key(self, idempotency_key: str, *, reason: str) -> bool:
        self.inner.released.append(idempotency_key)
        return await self._journal.release(idempotency_key, reason=reason)

    async def idempotency_key_status(self, idempotency_key: str) -> Any:
        return await self._journal.latest(idempotency_key)


def _journaled_service(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RunPodOnboardingService, FakeState, FakeResources, FakeJournalPool]:
    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", recording_insert_audit([]))
    pool = FakeJournalPool()
    inner = FakeResources()
    service, state, _, _ = _service(resources=JournaledResources(inner, pool))  # type: ignore[arg-type]  # reason: the journaled wrapper satisfies the resource protocol structurally
    return service, state, inner, pool


def _endpoint_request(**updates: Any) -> RunPodOnboardingRequest:
    values: dict[str, Any] = {
        "name": "demo-onboarding",
        "capability_name": "embedding.demo",
        "capability_class": "embedding",
        "provider_name": "demo-runpod",
        "image": "docker.io/example/worker:sha-abc",
        "public_image": True,
        "gpu_type_ids": ["NVIDIA L4"],
        "rate_per_hour_usd": "0.50",
        "template": TemplateSelection(mode="create", name="demo-template"),
        "endpoint": EndpointSelection(mode="create", name="demo-endpoint"),
        "probe_payload": {"texts": ["hello"]},
    }
    values.update(updates)
    return RunPodOnboardingRequest.model_validate(values)


def _private_volume_pod_request() -> RunPodOnboardingRequest:
    return _endpoint_request(
        topology="pod_lease",
        public_image=False,
        cloud="SECURE",
        data_center_id="US-KS-2",
        registry=RegistrySelection(
            mode="create",
            name="demo-registry",
            username="operator",
            password_env="REGISTRY_PASSWORD",
        ),
        volume=VolumeSelection(
            mode="create",
            name="demo-volume",
            size_gb=20,
            data_center_id="US-KS-2",
        ),
        endpoint=EndpointSelection(mode="none"),
    )


def _service(
    *,
    state: FakeState | None = None,
    resources: FakeResources | None = None,
    discovery: FakeDiscovery | None = None,
) -> tuple[RunPodOnboardingService, FakeState, FakeResources, FakeDiscovery]:
    state = state or FakeState()
    resources = resources or FakeResources()
    discovery = discovery or FakeDiscovery()
    service = RunPodOnboardingService(
        state=state,
        resources=resources,
        discovery=discovery,
        environ={
            "RUNPOD_API_KEY": "never-render-this-runpod-secret",
            "REGISTRY_PASSWORD": "never-render-this-registry-secret",
        },
    )
    return service, state, resources, discovery


@pytest.mark.asyncio
async def test_plan_is_default_complete_topology_and_zero_write() -> None:
    service, state, resources, _ = _service()

    result = await service.execute(OnboardingCommand(request=_endpoint_request()))

    assert result.action == OnboardingAction.PLAN
    assert result.status == OnboardingStatus.PLANNED
    assert result.zero_write is True
    assert result.dry_run_evidence is not None
    assert result.dry_run_evidence.request_kind == "endpoint"
    assert result.dry_run_evidence.guardrail_decision == "allow"
    assert result.dry_run_evidence.request_summary["gpu_type_ids"] == ["NVIDIA L4"]
    assert result.dry_run_evidence.request_summary["gpu_pools"] == ["ADA_24"]
    assert result.estimated_ceiling_usd > 0
    assert result.cost_impact.endpoint_minimum_hourly_usd == 0
    assert result.cost_impact.endpoint_maximum_hourly_usd == Decimal("1.50")
    assert result.cost_impact.paid_compute_floor_created_by_onboarding is False
    assert result.credential_references == ("RUNPOD_API_KEY",)
    assert result.database_mutations == (
        "pitwall.config_audit",
        "pitwall.capabilities",
        "pitwall.providers",
    )
    assert all("proposed" not in resource for resource in result.provider_resources)
    assert set(result.provider_resources) == {
        f"template:{onboarding._planned_resource_id(_endpoint_request(), 'template')}",
        f"endpoint:{onboarding._planned_resource_id(_endpoint_request(), 'endpoint')}",
    }
    assert not [item for item in resources.ledger if item.startswith("write:")]
    assert not state.ledger


@pytest.mark.asyncio
async def test_apply_is_dependency_ordered_and_second_apply_has_no_writes() -> None:
    service, state, resources, _ = _service()
    request = _endpoint_request(describe_inference_probe=True)
    plan = await service.plan(request)

    first = await service.apply(request, confirmed_plan_id=plan.plan_id)
    first_writes = [
        *[item for item in resources.ledger if item.startswith("write:")],
        *[item for item in state.ledger if item.startswith("write:")],
    ]
    second = await service.apply(request, confirmed_plan_id=plan.plan_id)

    assert first.status == OnboardingStatus.COMPLETE
    assert second.status == OnboardingStatus.COMPLETE
    assert second.zero_write is True
    assert first.dry_run_evidence is not None
    assert "LIVE-RP-01" in (first.dry_run_evidence.inference_probe or "")
    provider = state.providers[request.provider_name]
    assert provider.cloud_type is None
    assert provider.config["cloud_type"] == "ALL"
    assert first_writes == [
        "write:template:apply",
        "write:endpoint:apply",
        "write:capability",
        "write:provider",
    ]
    all_writes = [
        *[item for item in resources.ledger if item.startswith("write:")],
        *[item for item in state.ledger if item.startswith("write:")],
    ]
    assert all_writes == first_writes


@pytest.mark.asyncio
async def test_private_volume_pod_topology_never_creates_paid_pod() -> None:
    service, state, resources, _ = _service()
    request = _private_volume_pod_request()
    plan = await service.plan(request)
    planned_registry = onboarding._planned_resource_id(request, "registry_auth")
    assert f"registry_auth:{planned_registry}" in plan.provider_resources
    assert plan.dry_run_evidence is not None
    assert plan.dry_run_evidence.request_summary["registry_auth_id"] == planned_registry

    result = await service.apply(request, confirmed_plan_id=plan.plan_id)

    assert result.status == OnboardingStatus.COMPLETE
    assert result.dry_run_evidence is not None
    assert result.dry_run_evidence.request_kind == "pod"
    assert result.credential_references == ("RUNPOD_API_KEY", "REGISTRY_PASSWORD")
    assert result.cost_impact.network_volume_gb_created == request.volume.size_gb == 20
    assert result.cost_impact.network_volume_pricing == "provider_rate_unavailable"
    assert result.cost_impact.endpoint_maximum_hourly_usd == 0
    assert any(item == "preview:pod:preview" for item in resources.ledger)
    assert not any(item.startswith("write:pod") for item in resources.ledger)
    provider = state.providers[request.provider_name]
    assert provider.provider_type.value == "pod_lease"
    assert provider.config["network_volume_id"]


@pytest.mark.asyncio
async def test_direct_image_load_balancer_endpoint_uses_production_urls() -> None:
    service, state, resources, _ = _service()
    request = _endpoint_request(
        template=TemplateSelection(mode="none"),
        endpoint=EndpointSelection(
            mode="create",
            name="direct-lb",
            endpoint_type="LOAD_BALANCER",
            scaler_type="REQUEST_COUNT",
        ),
    )
    plan = await service.plan(request)

    assert plan.dry_run_evidence is not None
    assert plan.dry_run_evidence.request_summary["image"] == request.image
    result = await service.apply(request, confirmed_plan_id=plan.plan_id)

    assert result.status == OnboardingStatus.COMPLETE
    assert not resources.templates
    provider = state.providers[request.provider_name]
    assert provider.provider_type.value == "serverless_lb"
    assert provider.config["openai_base_url"].endswith("/v1")
    assert provider.config["lb_base_url"].endswith(".api.runpod.ai")


@pytest.mark.asyncio
async def test_explicit_existing_resources_are_reused_without_provider_writes() -> None:
    state = FakeState()
    resources = FakeResources()
    resources.templates["template_existing"] = TemplateResource(
        id="template_existing",
        name="existing-template",
        image="docker.io/example/worker:sha-abc",
        disk_gb=50,
        volume_gb=0,
        serverless=True,
        public=False,
    )
    resources.endpoints["endpoint_existing"] = EndpointResource(
        id="endpoint_existing",
        name="existing-endpoint",
        endpoint_type="QUEUE",
        workers=onboarding.EndpointWorkersRequest(),
        scaling=onboarding.EndpointScalingRequest(),
        flashboot=False,
        template_id="template_existing",
        gpu_pools=("ADA_24",),
        gpu_count=1,
    )
    service, _, _, _ = _service(state=state, resources=resources)
    request = _endpoint_request(
        template=TemplateSelection(mode="existing", resource_id="template_existing"),
        endpoint=EndpointSelection(mode="existing", resource_id="endpoint_existing"),
    )
    plan = await service.plan(request)

    result = await service.apply(request, confirmed_plan_id=plan.plan_id)

    assert result.status == OnboardingStatus.COMPLETE
    assert set(result.existing_resource_reuse) >= {
        "template:template_existing",
        "endpoint:endpoint_existing",
    }
    assert not [item for item in resources.ledger if item.startswith("write:")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_owner", "failure_step"),
    [
        ("resources", "registry_auth"),
        ("resources", "volume"),
        ("resources", "template"),
        ("state", "capability"),
        ("state", "provider"),
        ("resources", "pod_request_preview"),
    ],
)
async def test_failure_status_is_redacted_and_resume_is_complete(
    failure_owner: str,
    failure_step: str,
) -> None:
    service, state, resources, _ = _service()
    request = _private_volume_pod_request()
    plan = await service.plan(request)
    target = state if failure_owner == "state" else resources
    target.fail_at = failure_step

    with pytest.raises(OnboardingError) as caught:
        await service.apply(request, confirmed_plan_id=plan.plan_id)

    serialized = str(caught.value.to_dict())
    assert "never-render" not in serialized
    assert "credential-value" not in serialized
    assert "secret-provider-detail" not in serialized
    assert caught.value.result is not None
    assert caught.value.result.status == OnboardingStatus.FAILED
    assert caught.value.result.can_resume is True

    target.fail_at = None
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert resumed.status == OnboardingStatus.COMPLETE


@pytest.mark.asyncio
async def test_failure_after_provider_retains_dependencies_and_disables_broker_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, resources, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)
    original = service._production_dry_run

    async def fail_once(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("sensitive failure")

    monkeypatch.setattr(service, "_production_dry_run", fail_once)
    with pytest.raises(OnboardingError) as caught:
        await service.apply(request, confirmed_plan_id=plan.plan_id)

    assert caught.value.result is not None
    assert any(item.startswith("endpoint:") for item in caught.value.result.retained_resources)
    assert resources.endpoints
    assert resources.templates
    assert state.providers[request.provider_name].enabled is False
    assert state.capabilities[request.capability_name].enabled is False

    monkeypatch.setattr(service, "_production_dry_run", original)
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert resumed.status == OnboardingStatus.COMPLETE
    assert resumed.zero_write is False
    assert state.providers[request.provider_name].enabled is True
    assert state.capabilities[request.capability_name].enabled is True


@pytest.mark.asyncio
async def test_endpoint_failure_retains_dependency_then_resumes_idempotently() -> None:
    service, state, resources, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)
    resources.fail_at = "endpoint"

    with pytest.raises(OnboardingError) as caught:
        await service.apply(request, confirmed_plan_id=plan.plan_id)

    assert caught.value.step == "endpoint"
    assert len(resources.templates) == 1
    assert not resources.endpoints
    assert not state.providers
    resources.fail_at = None
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert resumed.status == OnboardingStatus.COMPLETE
    assert len(resources.templates) == len(resources.endpoints) == 1


@pytest.mark.asyncio
async def test_ambiguous_endpoint_write_is_rediscovered_by_owned_name() -> None:
    service, state, resources, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)
    original = resources.create_endpoint

    async def create_then_disconnect(request: EndpointCreateRequest) -> MutationResult:
        await original(request)
        raise RuntimeError("ambiguous provider response")

    resources.create_endpoint = create_then_disconnect  # type: ignore[method-assign]  # reason: fault injection
    with pytest.raises(OnboardingError):
        await service.apply(request, confirmed_plan_id=plan.plan_id)

    assert len(resources.endpoints) == 1
    assert len(resources.templates) == 1
    resources.create_endpoint = original  # type: ignore[method-assign]  # reason: restore fault injection
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert resumed.status == OnboardingStatus.COMPLETE
    assert len(resources.endpoints) == 1
    assert len(state.providers) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("record_type", ["capability", "provider"])
async def test_ambiguous_broker_commit_is_disabled_then_resumed(
    record_type: str,
) -> None:
    service, state, _, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)

    if record_type == "capability":
        original_capability = state.create_capability

        async def create_capability_then_disconnect(
            request: RunPodOnboardingRequest,
        ) -> Capability:
            await original_capability(request)
            raise RuntimeError("ambiguous persistence response")

        state.create_capability = create_capability_then_disconnect  # type: ignore[method-assign]  # reason: fault injection
    else:
        original_provider = state.create_provider

        async def create_provider_then_disconnect(
            request: RunPodOnboardingRequest,
            *,
            capability: Capability,
            endpoint_id: str | None,
            template_id: str | None,
            volume_id: str | None,
        ) -> Provider:
            await original_provider(
                request,
                capability=capability,
                endpoint_id=endpoint_id,
                template_id=template_id,
                volume_id=volume_id,
            )
            raise RuntimeError("ambiguous persistence response")

        state.create_provider = create_provider_then_disconnect  # type: ignore[method-assign]  # reason: fault injection

    with pytest.raises(OnboardingError):
        await service.apply(request, confirmed_plan_id=plan.plan_id)

    if record_type == "capability":
        assert state.capabilities[request.capability_name].enabled is False
        state.create_capability = original_capability  # type: ignore[method-assign]  # reason: restore fault injection
    else:
        assert state.providers[request.provider_name].enabled is False
        state.create_provider = original_provider  # type: ignore[method-assign]  # reason: restore fault injection
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert resumed.status == OnboardingStatus.COMPLETE


@pytest.mark.asyncio
async def test_cancellation_records_failure_and_compensates_known_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, resources, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)
    entered = asyncio.Event()
    blocked = asyncio.Event()

    async def block_endpoint(request: EndpointCreateRequest) -> MutationResult:
        del request
        entered.set()
        await blocked.wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(resources, "create_endpoint", block_endpoint)
    task = asyncio.create_task(service.apply(request, confirmed_plan_id=plan.plan_id))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(resources.templates) == 1
    assert not resources.endpoints
    assert not state.providers
    status = await service.status(request)
    assert status.status == OnboardingStatus.FAILED
    assert status.can_resume is True


@pytest.mark.asyncio
async def test_discovery_failure_is_bounded_and_zero_write() -> None:
    service, state, resources, discovery = _service()
    discovery.fail = True

    with pytest.raises(OnboardingError) as caught:
        await service.plan(_endpoint_request())

    assert caught.value.code == "discovery_unavailable"
    assert "credential" not in caught.value.detail
    assert not [item for item in resources.ledger if item.startswith("write:")]
    assert not state.ledger


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_event", ["validate_config", "discover_capacity", "complete"])
async def test_audit_step_failure_is_safe_and_exact_resume_completes(
    failed_event: str,
) -> None:
    service, state, _, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)
    state.fail_event_at = failed_event

    with pytest.raises(OnboardingError) as caught:
        await service.apply(request, confirmed_plan_id=plan.plan_id)

    assert caught.value.result is not None
    assert caught.value.result.status == OnboardingStatus.FAILED
    assert "persistence detail" not in str(caught.value.to_dict())
    state.fail_event_at = None
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert resumed.status == OnboardingStatus.COMPLETE


@pytest.mark.asyncio
async def test_concurrent_identical_apply_creates_one_topology() -> None:
    service, state, resources, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)

    first, second = await asyncio.gather(
        service.apply(request, confirmed_plan_id=plan.plan_id),
        service.apply(request, confirmed_plan_id=plan.plan_id),
    )

    assert first.status == second.status == OnboardingStatus.COMPLETE
    assert len(resources.templates) == 1
    assert len(resources.endpoints) == 1
    assert len(state.providers) == 1


@pytest.mark.asyncio
async def test_plan_confirmation_and_credential_failures_are_local_and_safe() -> None:
    service, _, resources, _ = _service()
    request = _endpoint_request()
    await service.plan(request)

    with pytest.raises(OnboardingError, match="confirmed plan"):
        await service.apply(request, confirmed_plan_id="runpod_onboard_" + "0" * 24)

    missing = RunPodOnboardingService(
        state=FakeState(),
        resources=resources,
        discovery=FakeDiscovery(),
        environ={},
    )
    with pytest.raises(OnboardingError) as caught:
        await missing.plan(request)
    assert caught.value.code == "credential_reference_unset"
    assert not [item for item in resources.ledger if item.startswith("write:")]


def test_request_scenarios_validate_before_io() -> None:
    with pytest.raises(ValueError, match="private images require"):
        _endpoint_request(public_image=False)
    with pytest.raises(ValueError, match="network volumes require cloud=SECURE"):
        _endpoint_request(
            volume=VolumeSelection(
                mode="create",
                name="volume",
                size_gb=10,
                data_center_id="US-KS-2",
            )
        )
    with pytest.raises(ValueError, match="endpoint mode none"):
        _endpoint_request(topology=OnboardingTopology.POD_LEASE)
    with pytest.raises(ValueError, match="private endpoint images require a template"):
        _endpoint_request(
            public_image=False,
            registry=RegistrySelection(mode="existing", resource_id="registry_private"),
            template=TemplateSelection(mode="none"),
        )


@pytest.mark.asyncio
async def test_status_and_rollback_guidance_are_read_only() -> None:
    service, state, resources, _ = _service()
    request = _endpoint_request()

    status = await service.execute(OnboardingCommand(action="status", request=request))
    rollback = await service.execute(OnboardingCommand(action="rollback", request=request))

    assert status.action == OnboardingAction.STATUS
    assert rollback.action == OnboardingAction.ROLLBACK
    assert any("audit evidence" in item for item in rollback.rollback_guidance)
    assert not [item for item in resources.ledger if item.startswith("write:")]
    assert not state.ledger


@pytest.mark.asyncio
async def test_completed_plan_repairs_deleted_and_disabled_broker_state() -> None:
    service, state, _, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)
    await service.apply(request, confirmed_plan_id=plan.plan_id)

    state.providers.clear()
    missing = await service.status(request)
    assert missing.status == OnboardingStatus.IN_PROGRESS
    assert next(step for step in missing.steps if step.key == "provider").state.value == "pending"

    recreated = await service.apply(request, confirmed_plan_id=plan.plan_id)
    assert recreated.status == OnboardingStatus.COMPLETE
    assert recreated.zero_write is False
    assert state.providers[request.provider_name].enabled is True

    provider = state.providers[request.provider_name]
    capability = state.capabilities[request.capability_name]
    state.providers[request.provider_name] = provider.model_copy(update={"enabled": False})
    state.capabilities[request.capability_name] = capability.model_copy(update={"enabled": False})
    disabled = await service.status(request)
    assert disabled.status == OnboardingStatus.IN_PROGRESS

    restored = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert restored.status == OnboardingStatus.COMPLETE
    assert restored.zero_write is False
    assert state.providers[request.provider_name].enabled is True
    assert state.capabilities[request.capability_name].enabled is True


@pytest.mark.asyncio
async def test_distinct_service_instances_share_durable_apply_serialization() -> None:
    state = FakeState()
    resources = FakeResources()
    discovery = FakeDiscovery()
    first, _, _, _ = _service(state=state, resources=resources, discovery=discovery)
    second, _, _, _ = _service(state=state, resources=resources, discovery=discovery)
    request = _endpoint_request()
    plan = await first.plan(request)

    first_result, second_result = await asyncio.gather(
        first.apply(request, confirmed_plan_id=plan.plan_id),
        second.apply(request, confirmed_plan_id=plan.plan_id),
    )

    assert first_result.status == second_result.status == OnboardingStatus.COMPLETE
    assert len(resources.templates) == 1
    assert len(resources.endpoints) == 1
    assert len(state.providers) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_template",
    [
        TemplateSelection(
            mode="create",
            name="demo-template",
            env={"apiKey": "sensitive-canary-" + "x" * 32},
        ),
        TemplateSelection(
            mode="create",
            name="demo-template",
            args=["--notify", "person@example.com"],
        ),
    ],
)
async def test_whole_request_guard_rejects_before_any_io(
    unsafe_template: TemplateSelection,
) -> None:
    service, state, resources, discovery = _service()
    request = _endpoint_request(template=unsafe_template)

    with pytest.raises(OnboardingError) as caught:
        await service.plan(request)

    assert caught.value.code == "guardrail_rejected"
    assert "sensitive-canary" not in str(caught.value.to_dict())
    assert "person@example.com" not in str(caught.value.to_dict())
    assert discovery.calls == 0
    assert not resources.ledger
    assert not state.ledger


@pytest.mark.asyncio
async def test_confirmed_rate_cannot_understate_current_discovery_price() -> None:
    service, state, resources, _ = _service()
    request = _endpoint_request(rate_per_hour_usd="0.000001")

    with pytest.raises(OnboardingError) as caught:
        await service.plan(request)

    assert caught.value.code == "rate_below_discovered_price"
    assert not resources.ledger
    assert not state.ledger


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resource_kind", "field", "drifted_value"),
    [
        ("template", "image", "docker.io/other/image:latest"),
        ("template", "docker_args_fingerprint", "0" * 64),
        ("template", "env_fingerprint", "1" * 64),
        ("template", "disk_gb", 60),
        ("template", "volume_gb", 20),
        ("template", "volume_mount_path", "/workspace"),
        ("template", "ports", ("9000/http",)),
        ("template", "env_keys", ("OTHER",)),
        ("template", "serverless", False),
        ("template", "public", True),
        ("template", "registry_auth_id", "auth_wrong"),
        ("endpoint", "endpoint_type", "LOAD_BALANCER"),
        (
            "endpoint",
            "workers",
            onboarding.EndpointWorkersRequest(maximum=4),
        ),
        (
            "endpoint",
            "scaling",
            onboarding.EndpointScalingRequest(value=5),
        ),
        ("endpoint", "flashboot", True),
        ("endpoint", "template_id", "template_wrong"),
        ("endpoint", "gpu_pools", ("AMPERE_80",)),
        ("endpoint", "excluded_gpu_type_ids", ("NVIDIA GeForce RTX 4090",)),
        ("endpoint", "gpu_count", 2),
    ],
)
async def test_existing_resources_must_match_every_observable_field(
    resource_kind: str,
    field: str,
    drifted_value: object,
) -> None:
    state = FakeState()
    resources = FakeResources()
    template_env = {"MODE": "production"}
    template_args = ["python", "worker.py"]
    template = TemplateResource(
        id="template_existing",
        name="existing-template",
        image="docker.io/example/worker:sha-abc",
        docker_args_fingerprint=onboarding._opaque_fingerprint(
            onboarding.argv_to_v2_args(template_args)
        ),
        env_fingerprint=onboarding._environment_fingerprint(template_env),
        disk_gb=50,
        volume_gb=0,
        ports=("8000/http",),
        env_keys=("MODE",),
        serverless=True,
        public=False,
    )
    endpoint = EndpointResource(
        id="endpoint_existing",
        name="existing-endpoint",
        endpoint_type="QUEUE",
        workers=onboarding.EndpointWorkersRequest(),
        scaling=onboarding.EndpointScalingRequest(),
        flashboot=False,
        template_id="template_existing",
        gpu_pools=("ADA_24",),
        gpu_count=1,
    )
    if resource_kind == "template":
        template = template.model_copy(update={field: drifted_value})
    else:
        endpoint = endpoint.model_copy(update={field: drifted_value})
    resources.templates[template.id] = template
    resources.endpoints[endpoint.id] = endpoint
    service, _, _, _ = _service(state=state, resources=resources)
    request = _endpoint_request(
        template=TemplateSelection(
            mode="existing",
            resource_id="template_existing",
            args=template_args,
            env=template_env,
            ports=["8000/http"],
        ),
        endpoint=EndpointSelection(mode="existing", resource_id="endpoint_existing"),
    )

    with pytest.raises(OnboardingError) as caught:
        await service.plan(request)

    assert caught.value.code == "resource_drift"
    assert caught.value.step == resource_kind


@pytest.mark.asyncio
async def test_existing_direct_image_endpoint_requires_exact_image() -> None:
    resources = FakeResources()
    resources.endpoints["endpoint_existing"] = EndpointResource(
        id="endpoint_existing",
        name="existing-endpoint",
        endpoint_type="QUEUE",
        workers=onboarding.EndpointWorkersRequest(),
        scaling=onboarding.EndpointScalingRequest(),
        flashboot=False,
        image="docker.io/other/image:latest",
        gpu_pools=("ADA_24",),
        gpu_count=1,
    )
    service, _, _, _ = _service(resources=resources)
    request = _endpoint_request(
        template=TemplateSelection(mode="none"),
        endpoint=EndpointSelection(mode="existing", resource_id="endpoint_existing"),
    )

    with pytest.raises(OnboardingError) as caught:
        await service.plan(request)

    assert caught.value.code == "resource_drift"
    assert caught.value.step == "endpoint"


@pytest.mark.asyncio
async def test_failed_resume_restores_reused_broker_records_to_disabled() -> None:
    service, state, _, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)

    async def fail_dry_run(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("bounded dry-run failure")

    service._production_dry_run = fail_dry_run  # type: ignore[method-assign]  # reason: fault injection
    with pytest.raises(OnboardingError):
        await service.apply(request, confirmed_plan_id=plan.plan_id)
    assert state.providers[request.provider_name].enabled is False
    assert state.capabilities[request.capability_name].enabled is False

    with pytest.raises(OnboardingError) as resumed:
        await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)

    assert state.providers[request.provider_name].enabled is False
    assert state.capabilities[request.capability_name].enabled is False
    assert resumed.value.result is not None
    assert any("provider:" in item for item in resumed.value.result.retained_resources)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gpu", "datacenter", "expected_code"),
    [
        (
            GpuCatalogEntry(
                gpu_type_id="NVIDIA L4",
                secure_cloud=False,
                community_cloud=True,
                community_price=Decimal("0.40"),
                datacenter_ids=("US-KS-2",),
                available_gpu_counts=(1,),
            ),
            DatacenterCatalogEntry(
                datacenter_id="US-KS-2",
                storage_support=True,
                listed=True,
                gpu_types=("NVIDIA L4",),
                gpu_availability={"NVIDIA L4": True},
            ),
            "gpu_cloud_unavailable",
        ),
        (
            GpuCatalogEntry(
                gpu_type_id="NVIDIA L4",
                secure_cloud=True,
                secure_price=Decimal("0.50"),
                datacenter_ids=("US-KS-2",),
                available_gpu_counts=(8,),
            ),
            DatacenterCatalogEntry(
                datacenter_id="US-KS-2",
                storage_support=True,
                listed=True,
                gpu_types=("NVIDIA L4",),
                gpu_availability={"NVIDIA L4": True},
            ),
            "gpu_count_unavailable",
        ),
        (
            GpuCatalogEntry(
                gpu_type_id="NVIDIA L4",
                secure_cloud=True,
                secure_price=Decimal("0.50"),
                datacenter_ids=("US-KS-2",),
                available_gpu_counts=(1,),
            ),
            DatacenterCatalogEntry(
                datacenter_id="US-KS-2",
                storage_support=False,
                listed=True,
                gpu_types=("NVIDIA L4",),
                gpu_availability={"NVIDIA L4": True},
            ),
            "volume_unsupported_in_data_center",
        ),
        (
            GpuCatalogEntry(
                gpu_type_id="NVIDIA L4",
                secure_cloud=True,
                secure_price=Decimal("0.50"),
                datacenter_ids=("US-KS-2",),
                available_gpu_counts=(1,),
            ),
            DatacenterCatalogEntry(
                datacenter_id="US-KS-2",
                storage_support=True,
                listed=True,
                gpu_types=("NVIDIA L4",),
                gpu_availability={"NVIDIA L4": False},
            ),
            "gpu_unavailable_in_data_center",
        ),
    ],
)
async def test_discovery_requires_compatible_cloud_count_and_datacenter(
    gpu: GpuCatalogEntry,
    datacenter: DatacenterCatalogEntry,
    expected_code: str,
) -> None:
    discovery = FakeDiscovery()
    discovery.snapshot = GpuDiscoverySnapshot(
        fetched_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        gpus=(gpu,),
        datacenters=(datacenter,),
    )
    service, state, resources, _ = _service(discovery=discovery)

    with pytest.raises(OnboardingError) as caught:
        await service.plan(_private_volume_pod_request())

    assert caught.value.code == expected_code
    assert not resources.ledger
    assert not state.ledger


@pytest.mark.asyncio
async def test_resume_after_compensation_recreates_through_the_real_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, resources, pool = _journaled_service(monkeypatch)
    request = _private_volume_pod_request()
    plan = await service.plan(request)
    state.fail_at = "capability"

    with pytest.raises(OnboardingError):
        await service.apply(request, confirmed_plan_id=plan.plan_id)
    assert resources.registries == {} and resources.templates == {}

    state.fail_at = None
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)

    assert resumed.status == OnboardingStatus.COMPLETE
    assert resources.ledger.count("write:registry:apply") == 2
    assert resources.ledger.count("write:template:apply") == 2
    assert len(resources.registries) == 1 and len(resources.templates) == 1
    registry_key = onboarding._step_idempotency_key(plan.plan_id, "registry_auth")
    assert pool.states(registry_key) == [
        "started",
        "completed",
        "compensated",
        "started",
        "completed",
    ]


@pytest.mark.asyncio
async def test_a_second_compensation_deletes_with_its_own_rollback_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, resources, _pool = _journaled_service(monkeypatch)
    request = _private_volume_pod_request()
    plan = await service.plan(request)
    state.fail_at = "capability"

    with pytest.raises(OnboardingError):
        await service.apply(request, confirmed_plan_id=plan.plan_id)
    with pytest.raises(OnboardingError) as second:
        await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)

    assert second.value.result is not None
    assert not [item for item in second.value.result.retained_resources if "failed" in item]
    assert resources.registries == {} and resources.templates == {}
    state.fail_at = None
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert resumed.status == OnboardingStatus.COMPLETE


@pytest.mark.asyncio
async def test_an_ambiguous_step_names_the_remedy_and_resume_recreates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _state, resources, pool = _journaled_service(monkeypatch)
    request = _private_volume_pod_request()
    plan = await service.plan(request)
    create_template = resources.create_template
    timed_out: list[str] = []

    async def times_out_once(request: TemplateCreateRequest) -> MutationResult:
        if not timed_out:
            timed_out.append(request.idempotency_key)
            raise RunPodControlPlaneError(
                "provider_timeout",
                "RunPod control-plane request timed out",
                operation="create",
                resource_type="template",
                retryable=True,
            )
        return await create_template(request)

    monkeypatch.setattr(resources, "create_template", times_out_once)
    with pytest.raises(OnboardingError) as first:
        await service.apply(request, confirmed_plan_id=plan.plan_id)
    assert first.value.code == "runpod_provider_timeout"
    assert pool.states(timed_out[0]) == ["started"]

    with pytest.raises(OnboardingError) as ambiguous:
        await service.apply(request, confirmed_plan_id=plan.plan_id)
    assert ambiguous.value.code == "runpod_mutation_outcome_ambiguous"
    assert "demo-template" in ambiguous.value.detail
    assert "resume" in ambiguous.value.detail
    assert resources.ledger.count("write:template:apply") == 0

    with pytest.raises(OnboardingError) as early:
        await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert early.value.code == "runpod_mutation_outcome_ambiguous"
    assert "demo-template" in early.value.detail
    assert resources.ledger.count("write:template:apply") == 0

    pool.advance(onboarding._UNKNOWN_OUTCOME_GRACE_S + 1)
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)
    assert resumed.status == OnboardingStatus.COMPLETE
    assert resources.ledger.count("write:template:apply") == 1
    assert pool.states(timed_out[0]) == ["started", "compensated", "started", "completed"]


@pytest.mark.asyncio
async def test_resume_with_a_lagging_list_reuses_the_live_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, resources, pool = _journaled_service(monkeypatch)
    request = _private_volume_pod_request()
    plan = await service.plan(request)
    state.fail_at = "provider"  # dependencies are retained for a provider-step failure
    with pytest.raises(OnboardingError):
        await service.apply(request, confirmed_plan_id=plan.plan_id)
    assert len(resources.templates) == 1
    template_id = next(iter(resources.templates))

    # The list omits the live template until the template step has run, then catches up.
    lag = {"on": True}
    list_templates = resources.list_templates
    journaled = service._resources
    create_template = journaled.create_template
    replays: list[bool] = []

    async def lagging_list() -> list[TemplateResource]:
        if lag["on"]:
            resources.ledger.append("read:template")
            return []
        return await list_templates()

    async def tracked_create(request: TemplateCreateRequest) -> MutationResult:
        result = await create_template(request)
        replays.append(result.replayed)
        lag["on"] = False
        return result

    monkeypatch.setattr(resources, "list_templates", lagging_list)
    monkeypatch.setattr(journaled, "create_template", tracked_create, raising=False)
    state.fail_at = None
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)

    assert resumed.status == OnboardingStatus.COMPLETE
    assert replays == [True]
    assert resources.ledger.count("write:template:apply") == 1
    assert list(resources.templates) == [template_id]
    template_key = onboarding._step_idempotency_key(plan.plan_id, "template")
    assert pool.states(template_key) == ["started", "completed"]


@pytest.mark.asyncio
async def test_resume_releases_a_completed_step_only_when_its_resource_is_gone_by_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, resources, pool = _journaled_service(monkeypatch)
    request = _private_volume_pod_request()
    plan = await service.plan(request)
    state.fail_at = "provider"
    with pytest.raises(OnboardingError):
        await service.apply(request, confirmed_plan_id=plan.plan_id)
    resources.templates.clear()  # deleted out of band; get-by-id answers not found

    state.fail_at = None
    resumed = await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)

    assert resumed.status == OnboardingStatus.COMPLETE
    assert resources.ledger.count("write:template:apply") == 2
    template_key = onboarding._step_idempotency_key(plan.plan_id, "template")
    assert pool.states(template_key) == [
        "started",
        "completed",
        "compensated",
        "started",
        "completed",
    ]


@pytest.mark.asyncio
async def test_resume_refuses_when_a_get_by_id_answers_an_unusable_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, resources, pool = _journaled_service(monkeypatch)
    request = _private_volume_pod_request()
    plan = await service.plan(request)
    state.fail_at = "provider"
    with pytest.raises(OnboardingError):
        await service.apply(request, confirmed_plan_id=plan.plan_id)

    async def glitching_get(resource_id: str) -> TemplateResource:
        # What the control plane now raises for an empty or non-object 200 template body.
        raise RunPodControlPlaneError(
            "provider_error",
            "RunPod control-plane request failed",
            operation="get",
            resource_type="template",
            resource_id=resource_id,
        )

    async def lagging_list() -> list[TemplateResource]:
        return []  # the list has not caught up, so the step reaches its get-by-id check

    monkeypatch.setattr(resources, "list_templates", lagging_list)
    monkeypatch.setattr(resources, "get_template", glitching_get)
    state.fail_at = None
    with pytest.raises(OnboardingError) as refused:
        await service.apply(request, confirmed_plan_id=plan.plan_id, resume=True)

    assert refused.value.code == "runpod_provider_error"
    assert resources.ledger.count("write:template:apply") == 1
    template_key = onboarding._step_idempotency_key(plan.plan_id, "template")
    assert pool.states(template_key) == ["started", "completed"]
