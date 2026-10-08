"""Tests for the serve-model service."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from pydantic import ValidationError

from pitwall import serve
from pitwall.api.exceptions import (
    PreSpendPayloadRejected,
    ServeBudgetExhausted,
    ServeCapExceeded,
    ServeConflict,
    ServeInvalidGpuClass,
    ServeLaunchFailed,
    ServeNoServeHistory,
    ServePriceUnknown,
    ServeRateRequired,
    ServeTemplateInvalid,
    ServeUnknownVariant,
    ServeVerificationFailed,
)
from pitwall.config import PitwallSettings
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderType,
)
from pitwall.core.models import Capability, Lease, LeaseEndpoints, LeaseReadiness, Provider
from pitwall.cost.budget_gate import BudgetRejected, BudgetSnapshot
from pitwall.models.catalogue import load_catalogue
from pitwall.models.errors import UnknownVariant as CatalogueUnknownVariant
from pitwall.models.lookup import CompanionInfo, CompanionKind, Engine, VariantInfo
from pitwall.models.prices import GpuPriceFreshness, GpuPriceSnapshot
from pitwall.runpod_client.graphql import RunpodGpuType
from pitwall.runpod_client.pods import RunPodError
from pitwall.security.pre_spend import PreSpendInspectionService
from tests.conftest import make_asyncpg_pool

_NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def test_serve_request_defaults_activity_only_when_idle_timeout_is_set() -> None:
    base = {
        "capability_name": "llm.cap-test",
        "model": "org/model",
        "gpu_class": "NVIDIA L4",
        "image": "example/model:test",
    }

    assert serve.ServeRequest(**base).renewal_policy is LeaseRenewalPolicy.MANUAL
    automatic = serve.ServeRequest(**base, idle_timeout_min=20)
    assert automatic.renewal_policy is LeaseRenewalPolicy.ACTIVITY
    manual = serve.ServeRequest(
        **base,
        idle_timeout_min=20,
        renewal_policy=LeaseRenewalPolicy.MANUAL,
    )
    assert manual.renewal_policy is LeaseRenewalPolicy.MANUAL
    with pytest.raises(ValidationError):
        serve.ServeRequest(**base, idle_timeout_min=4)
    with pytest.raises(ValidationError):
        serve.ServeRequest(**base, max_usd_per_hour=Decimal("0"))


@pytest.mark.asyncio
async def test_pre_spend_guard_blocks_before_repository_budget_or_provider_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    capability_repo = AsyncMock()
    provider_repo = AsyncMock()
    lease_repo = AsyncMock()
    monkeypatch.setattr(serve, "get_pre_spend_inspection_service", lambda: guardrail)
    monkeypatch.setattr(serve, "CapabilityRepository", lambda _pool: capability_repo)
    monkeypatch.setattr(serve, "ProviderRepository", lambda _pool: provider_repo)
    monkeypatch.setattr(serve, "LeaseRepository", lambda _pool: lease_repo)
    launch = AsyncMock()
    monkeypatch.setattr(serve, "run_launch", launch)

    with pytest.raises(PreSpendPayloadRejected) as rejected:
        await serve.serve_model(
            object(),
            serve.ServeRequest(
                capability_name="llm.guardrail-test",
                model="org/model",
                gpu_class="NVIDIA L4",
                image="example/model:test",
                env={"CALLER_VALUE": "sk-abcdefghijklmnopqrstuvwxyz123456"},
                rate_per_second=Decimal("0.001"),
            ),
            base_url="http://test",
            settings=PitwallSettings(),
        )

    assert rejected.value.to_response_body()["decision"] == "block"
    assert guardrail.status().counters.to_dict() == {
        "total": 1,
        "allow": 0,
        "redact": 0,
        "block": 1,
    }
    capability_repo.get_by_name.assert_not_awaited()
    provider_repo.get_by_name.assert_not_awaited()
    lease_repo.get_active_for_provider.assert_not_awaited()
    launch.assert_not_awaited()


def test_enforce_price_cap_accepts_live_price_at_or_below_cap() -> None:
    snapshot = GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType(
                id="NVIDIA L4",
                memoryInGb=24,
                securePrice=Decimal("0.8000"),
            ),
        ),
        checked_at=_NOW,
        source="live",
    )
    freshness = GpuPriceFreshness(age_seconds=0, source="live", stale=False)

    serve.enforce_price_cap(
        gpu_class="NVIDIA L4",
        max_usd_per_hour=Decimal("0.8000"),
        snapshot=snapshot,
        freshness=freshness,
    )


def test_enforce_price_cap_rejects_excess_and_unknown_prices() -> None:
    live = GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType(
                id="NVIDIA L4",
                memoryInGb=24,
                securePrice=Decimal("0.9000"),
            ),
        ),
        checked_at=_NOW,
        source="live",
    )
    fresh = GpuPriceFreshness(age_seconds=0, source="live", stale=False)
    with pytest.raises(ServeCapExceeded) as exceeded:
        serve.enforce_price_cap(
            gpu_class="NVIDIA L4",
            max_usd_per_hour=Decimal("0.8000"),
            snapshot=live,
            freshness=fresh,
        )
    assert exceeded.value.to_response_body() == {
        "error": "cap_exceeded",
        "gpu_class": "NVIDIA L4",
        "price_usd_per_hour": "0.9000",
        "max_usd_per_hour": "0.8000",
    }

    fallback = live.model_copy(update={"source": "fallback"})
    with pytest.raises(ServePriceUnknown) as unknown:
        serve.enforce_price_cap(
            gpu_class="NVIDIA L4",
            max_usd_per_hour=Decimal("0.8000"),
            snapshot=fallback,
            freshness=None,
        )
    assert unknown.value.to_response_body() == {
        "error": "price_unknown",
        "gpu_class": "NVIDIA L4",
        "max_usd_per_hour": "0.8000",
    }


@pytest.mark.asyncio
async def test_cap_is_checked_before_registry_or_runpod_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType(
                id="NVIDIA L4",
                memoryInGb=24,
                securePrice=Decimal("1.2500"),
            ),
        ),
        checked_at=_NOW,
        source="live",
    )
    capability_repo = AsyncMock()
    capability_repo.get_by_name.return_value = None
    provider_repo = AsyncMock()
    provider_repo.get_by_name.return_value = None
    lease_repo = AsyncMock()
    monkeypatch.setattr(serve, "CapabilityRepository", lambda _pool: capability_repo)
    monkeypatch.setattr(serve, "ProviderRepository", lambda _pool: provider_repo)
    monkeypatch.setattr(serve, "LeaseRepository", lambda _pool: lease_repo)
    monkeypatch.setattr(serve, "load_gpu_price_snapshot", AsyncMock(return_value=snapshot))
    launch = AsyncMock()
    monkeypatch.setattr(serve, "run_launch", launch)

    with pytest.raises(ServeCapExceeded):
        await serve.serve_model(
            object(),
            serve.ServeRequest(
                capability_name="llm.cap-test",
                model="org/model",
                gpu_class="NVIDIA L4",
                image="example/model:test",
                rate_per_second=Decimal("0.001"),
                max_usd_per_hour=Decimal("1.0000"),
            ),
            base_url="http://test",
            settings=PitwallSettings(),
        )

    capability_repo.create.assert_not_awaited()
    provider_repo.create.assert_not_awaited()
    provider_repo.patch.assert_not_awaited()
    launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalogue_plan_renders_launch_shape_fit_and_fallback_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = GpuPriceSnapshot(
        gpu_types=(RunpodGpuType(id="NVIDIA GeForce RTX 3090", memoryInGb=24),),
        checked_at=_NOW,
        source="fallback",
    )
    monkeypatch.setattr(serve, "load_gpu_price_snapshot", AsyncMock(return_value=snapshot))

    result = await serve.plan_catalogue_model(
        serve.ServePlanRequest(
            model="Qwen/Qwen3.8-27B",
            variant="gguf:UD-Q4_K_XL",
            gpu_class="RTX_3090",
            network_volume_id="volume-cache",
        ),
        settings=PitwallSettings(),
        catalogue=load_catalogue(),
    )

    assert result.model_id == "qwen3.8-27b"
    assert result.engine == "llama.cpp"
    assert result.image
    assert result.argv[:4] == [
        "--hf-repo",
        "unsloth/Qwen3.8-27B-GGUF",
        "--hf-file",
        "Qwen3.8-27B-UD-Q4_K_XL.gguf",
    ]
    assert result.volume_cache_env == {"LLAMA_CACHE": "/workspace/llama-cache"}
    assert result.fit == "fits"
    assert result.startup_timeout_s >= 900
    assert result.cost_estimate_usd is None
    assert result.price_source == "fallback"
    assert result.gpu_class == "NVIDIA GeForce RTX 3090"


@pytest.mark.asyncio
async def test_catalogue_plan_estimates_from_read_only_live_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType(
                id="NVIDIA GeForce RTX 3090",
                memoryInGb=24,
                securePrice=Decimal("0.30"),
            ),
        ),
        checked_at=_NOW,
        source="live",
    )
    monkeypatch.setattr(serve, "load_gpu_price_snapshot", AsyncMock(return_value=snapshot))

    result = await serve.plan_catalogue_model(
        serve.ServePlanRequest(
            model="Qwen/Qwen3.8-27B",
            variant="gguf:UD-Q4_K_XL",
            gpu_class="NVIDIA GeForce RTX 3090",
            ttl_minutes=120,
        ),
        settings=PitwallSettings(),
        catalogue=load_catalogue(),
    )

    assert result.cost_estimate_usd == "0.60"
    assert result.price_source == "live"


def test_launch_shape_vllm_is_positional_and_adds_tp_only_above_one() -> None:
    assert serve.launch_shape(
        "vllm",
        model="org/model",
        served="served-model",
        gpu_count=1,
        repo=None,
        file=None,
        flags=["--max-model-len", "8192"],
        companion_flags=(),
        start_args=["--trust-remote-code"],
    ) == [
        "org/model",
        "--served-model-name",
        "served-model",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--max-model-len",
        "8192",
        "--trust-remote-code",
    ]
    multi = serve.launch_shape(
        "vllm",
        model="org/model",
        served="served-model",
        gpu_count=4,
        repo=None,
        file=None,
        flags=[],
        companion_flags=(),
        start_args=[],
    )
    assert multi[7:9] == ["--tensor-parallel-size", "4"]


def test_launch_shape_vllm_omni_flag_selects_the_vllm_omni_entrypoint() -> None:
    argv = serve.launch_shape(
        "vllm",
        model="MiniMaxAI/MiniMax-H3",
        served="minimax-h3",
        gpu_count=2,
        repo=None,
        file=None,
        flags=["--omni", "--trust-remote-code"],
        companion_flags=(),
        start_args=[],
    )
    # The pinned vllm-omni image ships no entrypoint, so the argv must name it.
    assert argv[:3] == ["vllm-omni", "serve", "MiniMaxAI/MiniMax-H3"]
    assert argv[3] == "--served-model-name"
    assert argv.count("--omni") == 1


def test_launch_shape_llama_cpp_matches_server_entrypoint() -> None:
    assert serve.launch_shape(
        "llama.cpp",
        model="org/model",
        served="served-model",
        gpu_count=2,
        repo="org/model-gguf",
        file="model-Q4.gguf",
        flags=["--ctx-size", "4096"],
        companion_flags=(),
        start_args=["--tensor-split", "1,1"],
    ) == [
        "--hf-repo",
        "org/model-gguf",
        "--hf-file",
        "model-Q4.gguf",
        "--alias",
        "served-model",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--n-gpu-layers",
        "all",
        "--jinja",
        "--ctx-size",
        "4096",
        "--tensor-split",
        "1,1",
    ]


@pytest.mark.parametrize(
    ("gpu_count", "tp_tail"),
    [(1, []), (4, ["--tp", "4"])],
)
def test_sglang_launch_shape_is_exact(gpu_count: int, tp_tail: list[str]) -> None:
    assert serve.launch_shape(
        "sglang",
        model="acme/Model",
        served="acme-model",
        gpu_count=gpu_count,
        repo="acme/Model",
        file=None,
        flags=("--trust-remote-code",),
        companion_flags=(),
        start_args=("--context-length", "32768"),
    ) == [
        "python3",
        "-m",
        "sglang.launch_server",
        "--model-path",
        "acme/Model",
        "--served-model-name",
        "acme-model",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        *tp_tail,
        "--trust-remote-code",
        "--context-length",
        "32768",
    ]


def test_launch_shape_orders_variant_companion_and_request_flags() -> None:
    result = serve.launch_shape(
        "llama.cpp",
        model="acme/Model",
        served="acme-model",
        gpu_count=1,
        repo="acme/Model-GGUF",
        file="model.gguf",
        flags=("--parallel", "1"),
        companion_flags=("--mmproj", "/workspace/mmproj.gguf"),
        start_args=("--ctx-size", "32768"),
    )

    assert result[-6:] == [
        "--parallel",
        "1",
        "--mmproj",
        "/workspace/mmproj.gguf",
        "--ctx-size",
        "32768",
    ]


def test_no_catalogue_returns_no_dossier() -> None:
    assert serve.NoCatalogue().variant("org/model", None) is None


def _request(**overrides: Any) -> serve.ServeRequest:
    values: dict[str, Any] = {
        "capability_name": "llm.serve-test",
        "model": "org/model",
        "gpu_class": "NVIDIA H100 80GB HBM3",
        "image": "example/model-server:test",
        "rate_per_second": Decimal("0.002"),
    }
    values.update(overrides)
    return serve.ServeRequest(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability_name", "bad/name"),
        ("model", "bad model"),
        ("served_model_name", "bad\tname"),
        ("variant", "bad variant"),
        ("image", "bad\nimage"),
        ("image", "user:token@ghcr.io/org/img:1"),
        ("image", "https://ghcr.io/org/img"),
        ("image", "ghcr.io/org/img:1\nsecret"),
        ("datacenter", "bad datacenter"),
        ("idempotency_key", "bad key"),
        ("start_args", ["bad arg"]),
        ("start_args", ["--token=hf_test_start_arg_token"]),
        ("start_args", ["--api-key=short-inline-value"]),
        ("env", {"": "value"}),
        ("env", {"KEY": "bad\rvalue"}),
    ],
)
def test_serve_request_rejects_unsafe_name_and_argument_shapes(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _request(**{field: value})


def test_serve_request_rejects_endpoint_key_in_persisted_environment() -> None:
    with pytest.raises(ServeTemplateInvalid, match="PITWALL_ENDPOINT_KEY"):
        serve._reject_launch_only_env(
            {"PITWALL_ENDPOINT_KEY": "endpoint-secret"},
            source="request env",
        )


def _capability(served: str | None = None) -> Capability:
    return Capability(
        id="cap_llm_serve",
        name="llm.serve-test",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        served_model_id=served,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(config: dict[str, Any] | None = None) -> Provider:
    return Provider(
        id="prov_serve",
        capability_id="cap_llm_serve",
        name="serve-llm.serve-test",
        provider_type=ProviderType.POD_LEASE,
        config=config or {},
        priority=0,
        source=CapabilitySource.API,
        updated_at=_NOW,
    )


def _serve_history(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "model": "org/history-model",
        "engine": "vllm",
        "image_ref": "example/history-server:test",
        "gpu_types": ["NVIDIA L4"],
        "gpu_count": 2,
        "variant": "fp8",
        "env_vars": {"HISTORY_ENV": "1"},
        "docker_start_cmd": [
            "--model",
            "org/history-model",
            "--served-model-name",
            "history-served",
        ],
        "network_volume_id": "volume-history",
        "container_disk_gb": 60,
        "lease_ttl_ms": 5_400_000,
        "idle_timeout_min": 30,
        "max_usd_per_hour": "2.0000",
        "renewal_policy": "activity",
        "cost": {"per_second_active": "0.002"},
    }
    config.update(overrides)
    return config


def test_explicit_gpu_change_reprices_provider_instead_of_reusing_history_rate() -> None:
    config = serve._provider_config(
        _request(gpu_class="NVIDIA RTX A6000", rate_per_second=None),
        info=None,
        engine="vllm",
        image="example/model-server:test",
        docker_start_cmd=("org/model",),
        gpu_types=("NVIDIA RTX A6000",),
        restoring_history=False,
        served_model_id="served-model",
        existing={
            "gpu_types": ["NVIDIA GeForce RTX 4090"],
            "cost": {"per_second_active": "0.000137"},
        },
        settings=PitwallSettings(),
        cloud_type=None,
        price=Decimal("0.5300"),
        refresh_rate=True,
    )

    assert config["gpu_types"] == ["NVIDIA RTX A6000"]
    assert config["cost"]["per_second_active"] == "0.000148"


def _lease() -> Lease:
    ready = _NOW + timedelta(seconds=30)
    return Lease(
        id="lease-serve-1",
        provider_id="prov_serve",
        runpod_pod_id="pod-serve-1",
        state=LeaseState.ACTIVE,
        created_at=_NOW,
        expires_at=_NOW + timedelta(hours=2),
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        endpoints=LeaseEndpoints(http={"8000": "https://pod-serve-1-8000.proxy.runpod.net"}),
        readiness=LeaseReadiness(
            runtime_seen_at=ready,
            port_mappings_seen_at=ready,
            probe_passed_at=ready,
            probe_method="runpod_proxy",
        ),
    )


class Repos:
    def __init__(
        self,
        capability: Capability | None,
        provider: Provider | None,
        lease: Lease | None,
    ) -> None:
        self.capability = capability
        self.provider = provider
        self.lease = lease
        self.capability_creates: list[Capability] = []
        self.capability_patches: list[dict[str, Any]] = []
        self.provider_creates: list[Provider] = []
        self.provider_patches: list[dict[str, Any]] = []
        self.keyed_workload: Any = None
        self.keyed_lease: Lease | None = None
        self.key_lookups: list[str] = []

    async def capability_get(self, name: str) -> Capability | None:
        return self.capability

    async def capability_create(self, value: Capability) -> Capability:
        self.capability_creates.append(value)
        self.capability = value
        return value

    async def capability_patch(self, capability_id: str, **kwargs: Any) -> Capability | None:
        self.capability_patches.append(kwargs)
        assert self.capability is not None
        self.capability = self.capability.model_copy(update=kwargs)
        return self.capability

    async def provider_get(self, name: str) -> Provider | None:
        return self.provider

    async def provider_create(self, value: Provider) -> Provider:
        self.provider_creates.append(value)
        self.provider = value
        return value

    async def provider_patch(self, provider_id: str, **kwargs: Any) -> Provider | None:
        self.provider_patches.append(kwargs)
        assert self.provider is not None
        self.provider = self.provider.model_copy(update=kwargs)
        return self.provider

    async def active_for_provider(self, provider_id: str) -> Lease | None:
        return self.lease

    async def lease_get(self, lease_id: str) -> Lease | None:
        return self.lease

    async def workload_by_key(self, key: str) -> Any:
        self.key_lookups.append(key)
        return self.keyed_workload

    async def lease_by_workload(self, workload_id: str) -> Lease | None:
        return self.keyed_lease


def _install_repos(monkeypatch: pytest.MonkeyPatch, repos: Repos) -> None:
    monkeypatch.setattr(
        serve,
        "CapabilityRepository",
        lambda pool: type(
            "Caps",
            (),
            {
                "get_by_name": lambda self, name: repos.capability_get(name),
                "create": lambda self, value: repos.capability_create(value),
                "patch": lambda self, capability_id, **kw: repos.capability_patch(
                    capability_id, **kw
                ),
            },
        )(),
    )
    monkeypatch.setattr(
        serve,
        "ProviderRepository",
        lambda pool: type(
            "Providers",
            (),
            {
                "get_by_name": lambda self, name: repos.provider_get(name),
                "create": lambda self, value: repos.provider_create(value),
                "patch": lambda self, provider_id, **kw: repos.provider_patch(provider_id, **kw),
            },
        )(),
    )
    monkeypatch.setattr(
        serve,
        "LeaseRepository",
        lambda pool: type(
            "Leases",
            (),
            {
                "latest_active_for_provider": (
                    lambda self, provider_id: repos.active_for_provider(provider_id)
                ),
                "get": lambda self, lease_id: repos.lease_get(lease_id),
                "get_by_workload": lambda self, workload_id: repos.lease_by_workload(workload_id),
            },
        )(),
    )
    monkeypatch.setattr(
        serve,
        "WorkloadRepository",
        lambda pool: type(
            "Workloads",
            (),
            {"get_by_idempotency_key": lambda self, key: repos.workload_by_key(key)},
        )(),
        raising=False,
    )


@pytest.mark.anyio
async def test_capability_only_serve_restores_history_and_request_caps_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability("org/history-model"), _provider(_serve_history()), None)
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"dry_run": True}))
    snapshot = GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType(
                id="NVIDIA L4",
                memoryInGb=24,
                securePrice=Decimal("0.80"),
            ),
        ),
        checked_at=_NOW,
        source="live",
    )
    monkeypatch.setattr(serve, "load_gpu_price_snapshot", AsyncMock(return_value=snapshot))

    class RemovedCatalogue:
        def variant(self, model_id: str, variant_id: str | None) -> VariantInfo | None:
            del model_id, variant_id
            return None

    result = await serve.serve_model(
        object(),
        serve.ServeRequest(
            capability_name="llm.serve-test",
            idle_timeout_min=20,
            max_usd_per_hour=Decimal("1.00"),
            dry_run=True,
        ),
        base_url="http://test",
        settings=PitwallSettings(),
        catalogue=RemovedCatalogue(),
    )

    config = repos.provider_patches[-1]["config"]
    assert result.model_id == "org/history-model"
    assert result.gpu_count == 2
    assert config["model"] == "org/history-model"
    assert config["gpu_types"] == ["NVIDIA L4"]
    assert config["lease_ttl_ms"] == 5_400_000
    assert config["idle_timeout_min"] == 20
    assert config["max_usd_per_hour"] == "1.00"
    assert config["engine"] == "vllm"
    assert config["image_ref"] == "example/history-server:test"
    assert config["env_vars"] == {"HISTORY_ENV": "1"}
    assert config["docker_start_cmd"] == [
        "--model",
        "org/history-model",
        "--served-model-name",
        "history-served",
    ]
    await serve.serve_model(
        object(),
        serve.ServeRequest(
            capability_name="llm.serve-test",
            gpu_count=3,
            ttl_minutes=45,
            dry_run=True,
        ),
        base_url="http://test",
        settings=PitwallSettings(),
        catalogue=RemovedCatalogue(),
    )

    explicit_config = repos.provider_patches[-1]["config"]
    assert explicit_config["gpu_count"] == 3
    assert explicit_config["lease_ttl_ms"] == 2_700_000


@pytest.mark.anyio
async def test_live_cuda_and_price_selection_follow_explicit_gpu_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = replace(_gguf(), min_cuda="12.8", gated=False)
    repos = Repos(
        _capability(),
        _provider(
            {"gpu_types": ["NVIDIA GeForce RTX 4090"], "cost": {"per_second_active": "0.000137"}}
        ),
        None,
    )
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"dry_run": True}))
    monkeypatch.setattr(
        serve,
        "load_gpu_price_snapshot",
        AsyncMock(
            return_value=GpuPriceSnapshot(
                gpu_types=(
                    RunpodGpuType(
                        id="NVIDIA RTX A6000",
                        memoryInGb=48,
                        securePrice=Decimal("0.5300"),
                    ),
                ),
                checked_at=_NOW,
                source="live",
            )
        ),
    )

    class Market:
        gpus = (
            type(
                "Gpu",
                (),
                {
                    "gpu_type_id": "NVIDIA RTX A6000",
                    "rest_v2": type(
                        "Rest",
                        (),
                        {"cuda_versions": (("12.8", False), ("13.0", True))},
                    )(),
                },
            )(),
        )

    class MarketService:
        async def read(self) -> Market:
            return Market()

    result = await serve.serve_model(
        object(),
        _request(
            model="org/model-gguf",
            gpu_class="NVIDIA RTX A6000",
            variant="gguf:q4",
            image=None,
            rate_per_second=None,
            dry_run=True,
        ),
        base_url="http://test",
        settings=PitwallSettings(),
        catalogue=Catalogue(info),
        market_service=MarketService(),
    )

    config = repos.provider_patches[-1]["config"]
    assert result.dry_run is True
    assert config["allowed_cuda_versions"] == ["13.0"]
    assert config["cost"]["per_second_active"] == "0.000148"


def test_serve_request_price_cap_accepts_lease_precision_only() -> None:
    base = {"capability_name": "llm.serve-test"}
    assert serve.ServeRequest(**base, max_usd_per_hour=Decimal("1.2345"))
    assert serve.ServeRequest(**base, max_usd_per_hour="1.25")
    with pytest.raises(ValidationError):
        serve.ServeRequest(**base, max_usd_per_hour=Decimal("0.12345"))


@pytest.mark.anyio
async def test_capability_only_explicit_gpu_class_overrides_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability("org/history-model"), _provider(_serve_history()), None)
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"dry_run": True}))
    snapshot = GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType(
                id="NVIDIA A40",
                memoryInGb=48,
                securePrice=Decimal("1.00"),
            ),
        ),
        checked_at=_NOW,
        source="live",
    )
    monkeypatch.setattr(serve, "load_gpu_price_snapshot", AsyncMock(return_value=snapshot))

    await serve.serve_model(
        object(),
        serve.ServeRequest(
            capability_name="llm.serve-test",
            gpu_class="NVIDIA A40",
            dry_run=True,
        ),
        base_url="http://test",
        settings=PitwallSettings(),
    )

    assert repos.provider_patches[-1]["config"]["gpu_types"] == ["NVIDIA A40"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider",
    [None, _provider({"gpu_types": ["NVIDIA L4"]})],
)
async def test_capability_only_without_model_history_is_a_stable_422(
    monkeypatch: pytest.MonkeyPatch,
    provider: Provider | None,
) -> None:
    repos = Repos(_capability(), provider, None)
    _install_repos(monkeypatch, repos)

    with pytest.raises(ServeNoServeHistory) as raised:
        await serve.serve_model(
            object(),
            serve.ServeRequest(capability_name="llm.serve-test"),
            base_url="http://test",
            settings=PitwallSettings(),
        )

    assert raised.value.status_code == 422
    assert raised.value.to_response_body()["error"] == "no_serve_history"


class Catalogue:
    def __init__(self, info: VariantInfo | None) -> None:
        self.info = info

    def variant(self, model_id: str, variant_id: str | None) -> VariantInfo | None:
        return self.info


class CountingCatalogue(Catalogue):
    def __init__(self, info: VariantInfo, *, default_variant_id: str = "fp8") -> None:
        super().__init__(info)
        self.default_variant_id = default_variant_id
        self.default_resolution_count = 0

    def variant(self, model_id: str, variant_id: str | None) -> VariantInfo | None:
        if variant_id is None:
            self.default_resolution_count += 1
        return super().variant(model_id, variant_id)

    def dossier_variant(self, model_id: str, variant_id: str | None) -> object:
        if variant_id is None:
            self.default_resolution_count += 1
        return type("SelectedVariant", (), {"id": variant_id or self.default_variant_id})()


def _gguf() -> VariantInfo:
    return VariantInfo(
        variant_id="gguf:q4",
        engine="llama.cpp",
        image="example/llama-server:test",
        repo="org/model-gguf",
        file="model-Q4.gguf",
        flags=("--ctx-size", "4096"),
        companions=(),
        evidence=None,
        openai_chat=True,
        env={"ROW_ENV": "1"},
        container_disk_gb=None,
        startup_min=None,
        gated=True,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("replacement", [None, "changed"])
async def test_capability_only_replays_first_serve_when_dossier_removed_or_changed(
    monkeypatch: pytest.MonkeyPatch,
    replacement: str | None,
) -> None:
    first = Repos(_capability(), None, None)
    _install_repos(monkeypatch, first)
    monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"dry_run": True}))
    first_result = await serve.serve_model(
        object(),
        _request(variant="gguf:q4", image=None, dry_run=True),
        base_url="http://test",
        settings=PitwallSettings(pitwall_hf_token="hf-test-history-canary"),
        catalogue=Catalogue(_gguf()),
    )
    assert first.provider is not None
    persisted = first.provider.config

    changed = replace(
        _gguf(),
        engine="vllm",
        image="example/changed-server:test",
        repo="org/changed-model",
        flags=("--changed-flag",),
        env={"CHANGED_ENV": "1"},
        container_disk_gb=120,
    )
    revived = Repos(_capability(first_result.model_id), first.provider, None)
    _install_repos(monkeypatch, revived)
    await serve.serve_model(
        object(),
        serve.ServeRequest(capability_name="llm.serve-test", dry_run=True),
        base_url="http://test",
        settings=PitwallSettings(),
        catalogue=Catalogue(changed if replacement == "changed" else None),
    )

    replayed = revived.provider_patches[-1]["config"]
    for key in (
        "docker_start_cmd",
        "image_ref",
        "env_vars",
        "gpu_types",
        "gpu_count",
        "engine",
        "model",
        "variant",
        "network_volume_id",
        "container_disk_gb",
    ):
        assert replayed.get(key) == persisted.get(key)


@pytest.mark.anyio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_omitted_variant_default_is_resolved_once_in_dry_run_and_live_paths(
    monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)
    catalogue = CountingCatalogue(_gguf(), default_variant_id="gguf:q4")
    if dry_run:
        monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"dry_run": True}))
    else:

        async def launch(**kwargs: Any) -> dict[str, Any]:
            repos.lease = _lease()
            return {"lease_id": "lease-serve-1"}

        monkeypatch.setattr(serve, "run_launch", launch)
        monkeypatch.setattr(serve, "verify_served_model", AsyncMock(return_value=["org/model"]))

    result = await serve.serve_model(
        object(),
        _request(image=None, dry_run=dry_run),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(pitwall_hf_token="hf-test-counting"),
        catalogue=catalogue,
    )

    assert result.variant == "gguf:q4"
    assert catalogue.default_resolution_count == 1


def test_vllm_speculative_json_is_preserved_byte_for_byte() -> None:
    raw = '{"method":"mtp", "num_speculative_tokens":5}'
    companion = CompanionInfo(
        kind="mtp",
        repo="zai-org/GLM-5.3-Flash",
        file="mtp.safetensors",
        flags=("--speculative-config", raw),
    )

    assert serve._companion_flags("vllm", (companion,)) == (
        "--speculative-config",
        raw,
    )


def test_llama_mmproj_uses_declared_file() -> None:
    companion = CompanionInfo(
        kind="mmproj",
        repo="acme/Vision-GGUF",
        file="/workspace/mmproj-model-f16.gguf",
        flags=("--mmproj", "/workspace/mmproj-model-f16.gguf"),
    )
    assert serve._companion_flags("llama.cpp", (companion,)) == companion.flags


@pytest.mark.parametrize(
    ("engine", "companion"),
    [
        (
            "sglang",
            CompanionInfo("mtp", "acme/MTP", "mtp.bin", ()),
        ),
        (
            "vllm",
            CompanionInfo("mmproj", "acme/Vision", "mmproj.bin", ("--mmproj", "mmproj.bin")),
        ),
        (
            "vllm",
            CompanionInfo("mtp", "acme/MTP", "mtp.bin", ("--speculative-config", "[]")),
        ),
        (
            "llama.cpp",
            CompanionInfo("draft", "acme/Draft", "draft.gguf", ("--draft-model", "draft.gguf")),
        ),
    ],
)
def test_unsupported_companion_shapes_are_rejected(
    engine: Engine, companion: CompanionInfo
) -> None:
    with pytest.raises(ValueError, match="unsupported companion"):
        serve._companion_flags(engine, (companion,))


@pytest.mark.parametrize("kind", ["mtp", "draft"])
def test_llama_speculative_companion_uses_sourced_tokens(kind: CompanionKind) -> None:
    companion = CompanionInfo(
        kind=kind,
        repo="unsloth/gemma-4-31B-it-qat-GGUF",
        file="gemma-4-31B-it-qat-MTP.gguf",
        flags=("--spec-type", "draft-mtp", "--spec-draft-n-max", "4"),
    )

    assert serve._companion_flags("llama.cpp", (companion,)) == companion.flags


@pytest.mark.anyio
@pytest.mark.parametrize(
    "info",
    [
        VariantInfo(
            variant_id="fp8",
            engine="vllm",
            image="example/vllm:test",
            repo="acme/NonChat",
            file=None,
            flags=(),
            companions=(),
            evidence=None,
            openai_chat=False,
            env={},
            container_disk_gb=80,
            startup_min=10,
            gated=False,
        ),
        VariantInfo(
            variant_id="fp8",
            engine="sglang",
            image="lmsysorg/sglang:v0.5.18-runtime",
            repo="acme/SGLang-With-MTP",
            file=None,
            flags=(),
            companions=(
                CompanionInfo(
                    kind="mtp",
                    repo="acme/MTP",
                    file="mtp.safetensors",
                    flags=(),
                ),
            ),
            evidence=None,
            openai_chat=True,
            env={},
            container_disk_gb=80,
            startup_min=10,
            gated=False,
        ),
    ],
)
async def test_ineligible_catalogue_rows_are_unknown_before_repository_writes(
    monkeypatch: pytest.MonkeyPatch, info: VariantInfo
) -> None:
    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)

    with pytest.raises(ServeUnknownVariant) as exc_info:
        await serve.serve_model(
            object(),
            _request(model=info.repo, image=None, dry_run=True),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
            catalogue=Catalogue(info),
        )

    assert exc_info.value.error_code == "unknown_variant"
    assert repos.capability_creates == []
    assert repos.capability_patches == []
    assert repos.provider_creates == []
    assert repos.provider_patches == []


@pytest.mark.anyio
async def test_sglang_dry_run_persists_exact_launch_cache_and_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = VariantInfo(
        variant_id="fp8",
        engine="sglang",
        image="lmsysorg/sglang:v0.5.18-runtime",
        repo="acme/Model",
        file=None,
        flags=("--trust-remote-code",),
        companions=(),
        evidence=None,
        openai_chat=True,
        env={},
        container_disk_gb=120,
        startup_min=8,
        gated=False,
    )
    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(
        serve,
        "run_launch",
        AsyncMock(return_value={"dry_run": True, "template_id": "template-plan"}),
    )

    await serve.serve_model(
        object(),
        _request(
            model="acme/Model",
            image=None,
            gpu_count=4,
            start_args=["--context-length", "32768"],
            dry_run=True,
        ),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(runpod_network_volume_id="volume-model-cache"),
        catalogue=Catalogue(info),
    )

    config = repos.provider_creates[0].config
    assert config["image_ref"] == "lmsysorg/sglang:v0.5.18-runtime"
    assert config["docker_start_cmd"] == [
        "python3",
        "-m",
        "sglang.launch_server",
        "--model-path",
        "acme/Model",
        "--served-model-name",
        "acme/Model",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--tp",
        "4",
        "--trust-remote-code",
        "--context-length",
        "32768",
    ]
    assert config["env_vars"] == {
        "HF_HOME": "/workspace/hf",
        "HF_HUB_CACHE": "/workspace/hf/hub",
    }
    assert config["readiness_path"] == "/health_generate"


@pytest.mark.anyio
async def test_unknown_variant_and_variant_without_dossier_are_422() -> None:
    with pytest.raises(ServeUnknownVariant):
        await serve.serve_model(
            object(),
            _request(variant="missing"),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
            catalogue=Catalogue(None),
        )

    class KnownModelUnknownVariant:
        def variant(self, model_id: str, variant_id: str | None) -> VariantInfo | None:
            raise CatalogueUnknownVariant(model_id, variant_id)

    with pytest.raises(ServeUnknownVariant):
        await serve.serve_model(
            object(),
            _request(variant="missing"),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
            catalogue=KnownModelUnknownVariant(),
        )


@pytest.mark.anyio
async def test_replay_returns_active_lease_and_conflict_does_not_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    same = Repos(
        _capability("org/model"),
        _provider({"engine": "vllm", "gpu_count": 1, "openai_proxy_port": 8000}),
        _lease(),
    )
    _install_repos(monkeypatch, same)
    monkeypatch.setattr(serve, "run_launch", pytest.fail)
    with respx.mock:
        models = _mock_models(["org/model"])
        result = await serve.serve_model(
            object(),
            _request(),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
        )
    assert models.called
    assert result.created is False and result.lease_id == "lease-serve-1"

    different = Repos(_capability("org/other"), same.provider, _lease())
    _install_repos(monkeypatch, different)
    with pytest.raises(ServeConflict):
        await serve.serve_model(
            object(),
            _request(),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
        )


@pytest.mark.anyio
async def test_dossier_builds_llama_provider_cache_timeout_and_launch_only_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)
    seen: dict[str, Any] = {}

    async def launch(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {
            "dry_run": True,
            "template_id": "template-plan",
            "workload_id": None,
        }

    monkeypatch.setattr(serve, "run_launch", launch)
    result = await serve.serve_model(
        object(),
        _request(variant="gguf:q4", gated=True, dry_run=True),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(
            pitwall_hf_token="hf-test-launch-canary",
            runpod_network_volume_id="volume-model-cache",
        ),
        catalogue=Catalogue(_gguf()),
    )
    config = repos.provider_creates[0].config
    assert config["startup_timeout_s"] == 1_800
    assert config["container_disk_gb"] == 80
    assert config["gpu_count"] == 1 and config["engine"] == "llama.cpp"
    assert config["readiness_path"] == "/health"
    assert config["docker_start_cmd"][-2:] == ["--ctx-size", "4096"]
    assert config["env_vars"]["LLAMA_CACHE"] == "/workspace/llama-cache"
    assert config["env_vars"]["ROW_ENV"] == "1"
    assert "HF_TOKEN" not in config and "HF_TOKEN" not in config["env_vars"]
    assert seen["extra_env"] == {"HF_TOKEN": "hf-test-launch-canary"}
    assert "hf-test-launch-canary" not in str(result.to_dict())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("info", "expected_flag"),
    [
        (
            VariantInfo(
                variant_id="fp8",
                engine="vllm",
                image="example/vllm:test",
                repo="org/model",
                file=None,
                flags=(),
                companions=(),
                evidence=None,
                openai_chat=True,
                env={},
                container_disk_gb=None,
                startup_min=None,
                gated=False,
                served_model_name="catalogue-model",
            ),
            "--served-model-name",
        ),
        (
            VariantInfo(
                variant_id="fp8",
                engine="llama.cpp",
                image="example/llama:test",
                repo="org/model-gguf",
                file="model.gguf",
                flags=(),
                companions=(),
                evidence=None,
                openai_chat=True,
                env={},
                container_disk_gb=None,
                startup_min=None,
                gated=False,
                served_model_name="catalogue-model",
            ),
            "--alias",
        ),
    ],
)
async def test_catalogue_served_model_name_controls_launch_result_and_replay(
    monkeypatch: pytest.MonkeyPatch, info: VariantInfo, expected_flag: str
) -> None:
    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"dry_run": True}))
    result = await serve.serve_model(
        object(),
        _request(image=None, dry_run=True),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(),
        catalogue=Catalogue(info),
    )
    command = repos.provider_creates[0].config["docker_start_cmd"]
    assert command[command.index(expected_flag) + 1] == "catalogue-model"
    assert result.model_id == "catalogue-model"
    assert repos.capability_patches == [{"served_model_id": "catalogue-model"}]

    replay = Repos(_capability("catalogue-model"), repos.provider, _lease())
    _install_repos(monkeypatch, replay)
    with respx.mock:
        models = _mock_models(["catalogue-model"])
        replay_result = await serve.serve_model(
            object(),
            _request(image=None),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
            catalogue=Catalogue(info),
        )
    assert models.called
    assert replay_result.model_id == "catalogue-model"

    override = Repos(_capability(), None, None)
    _install_repos(monkeypatch, override)
    override_result = await serve.serve_model(
        object(),
        _request(image=None, dry_run=True, served_model_name="request-model"),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(),
        catalogue=Catalogue(info),
    )
    assert override_result.model_id == "request-model"


@pytest.mark.anyio
async def test_verification_mismatch_retries_then_tears_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability(), _provider(), None)
    _install_repos(monkeypatch, repos)
    torn_down: list[tuple[str, str]] = []

    async def launch(**kwargs: Any) -> dict[str, Any]:
        repos.lease = _lease()
        return {
            "lease_id": "lease-serve-1",
            "workload_id": "wkl-serve",
            "template_id": "template-serve",
        }

    async def teardown(lease_id: str, **kwargs: Any) -> None:
        torn_down.append((lease_id, kwargs["reason"]))

    monkeypatch.setattr(serve, "run_launch", launch)
    monkeypatch.setattr(serve, "run_teardown", teardown)
    monkeypatch.setattr(serve, "VERIFY_TOTAL_TIMEOUT_S", 0.01)
    monkeypatch.setattr(serve, "VERIFY_RETRY_INTERVAL_S", 0.0)
    with respx.mock:
        respx.get("https://pod-serve-1-8000.proxy.runpod.net/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "other-model"}]})
        )
        with pytest.raises(ServeVerificationFailed):
            await serve.serve_model(
                object(),
                _request(),
                base_url="http://127.0.0.1:8080",
                settings=PitwallSettings(),
            )
    assert torn_down == [("lease-serve-1", "served_model_mismatch")]


@pytest.mark.anyio
async def test_verification_probe_sends_endpoint_key_without_exposing_it_in_url() -> None:
    with respx.mock:
        route = respx.get("https://pod-auth-8000.proxy.runpod.net/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "served-model"}]})
        )
        observed = await serve.verify_served_model(
            "https://pod-auth-8000.proxy.runpod.net/v1/models",
            "served-model",
            headers={"Authorization": "Bearer endpoint-unit-secret"},
        )

    assert observed == ["served-model"]
    assert route.calls[0].request.headers["authorization"] == "Bearer endpoint-unit-secret"
    assert "endpoint-unit-secret" not in str(route.calls[0].request.url)


@pytest.mark.anyio
async def test_no_dossier_requires_image_and_rate() -> None:
    with pytest.raises(ServeTemplateInvalid):
        await serve.serve_model(
            object(),
            _request(image=None),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
        )

    repos = Repos(_capability(), None, None)
    with pytest.MonkeyPatch.context() as monkeypatch:
        _install_repos(monkeypatch, repos)
        with pytest.raises(ServeRateRequired):
            await serve.serve_model(
                object(),
                _request(rate_per_second=None),
                base_url="http://127.0.0.1:8080",
                settings=PitwallSettings(),
            )
    assert repos.capability_creates == []
    assert repos.capability_patches == []
    assert repos.provider_creates == []
    assert repos.provider_patches == []


@pytest.mark.anyio
async def test_pre_persistence_rejections_never_issue_writes() -> None:
    def reject_writes(query: str, *args: object) -> None:
        del args
        if query.lstrip().upper().startswith(("INSERT", "UPDATE")):
            raise AssertionError("persisted before validation")
        return None

    invalid_gpu_pool = make_asyncpg_pool(fetchrow_side_effect=reject_writes)
    with pytest.raises(ServeInvalidGpuClass) as exc_info:
        await serve.serve_model(
            invalid_gpu_pool,
            _request(gpu_class="L4"),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
        )
    assert exc_info.value.suggestions == ("NVIDIA L4",)

    rate_pool = make_asyncpg_pool(fetchrow_side_effect=reject_writes)
    with pytest.raises(ServeRateRequired):
        await serve.serve_model(
            rate_pool,
            _request(rate_per_second=None),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
        )


@pytest.mark.anyio
async def test_legacy_gpu_alias_is_normalized_in_provider_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)

    async def launch(**kwargs: Any) -> dict[str, Any]:
        return {"dry_run": True, "template_id": "template-plan", "workload_id": None}

    monkeypatch.setattr(serve, "run_launch", launch)
    await serve.serve_model(
        object(),
        _request(gpu_class="NVIDIA RTX 4090", dry_run=True),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(),
    )

    assert repos.provider_creates[0].config["gpu_types"] == ["NVIDIA GeForce RTX 4090"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "launch_result",
    [
        {"provider_fallback": True, "provider_fallback_reason": "no capacity"},
        {"lease_id": "lease-missing", "template_id": "template-serve"},
    ],
)
async def test_provider_fallback_and_missing_lease_raise_launch_failed(
    monkeypatch: pytest.MonkeyPatch,
    launch_result: dict[str, Any],
) -> None:
    repos = Repos(_capability(), _provider(), None)
    _install_repos(monkeypatch, repos)

    async def launch(**kwargs: Any) -> dict[str, Any]:
        return launch_result

    monkeypatch.setattr(serve, "run_launch", launch)
    with pytest.raises(ServeLaunchFailed):
        await serve.serve_model(
            object(),
            _request(),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
        )


@pytest.mark.anyio
async def test_runpod_launch_error_becomes_serve_launch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability(), _provider(), None)
    _install_repos(monkeypatch, repos)

    async def fail_launch(**_kwargs: Any) -> dict[str, Any]:
        raise RunPodError("Unauthorized")

    monkeypatch.setattr(serve, "run_launch", fail_launch)
    with pytest.raises(ServeLaunchFailed, match="Unauthorized"):
        await serve.serve_model(
            object(), _request(), base_url="http://127.0.0.1:8080", settings=PitwallSettings()
        )


@pytest.mark.anyio
async def test_models_success_returns_engine_variant_and_gpu_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability(), _provider(), None)
    _install_repos(monkeypatch, repos)

    async def launch(**kwargs: Any) -> dict[str, Any]:
        repos.lease = _lease()
        return {
            "lease_id": "lease-serve-1",
            "workload_id": "wkl-serve",
            "template_id": "template-serve",
        }

    monkeypatch.setattr(serve, "run_launch", launch)
    with respx.mock:
        respx.get("https://pod-serve-1-8000.proxy.runpod.net/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "served-model"}]})
        )
        result = await serve.serve_model(
            object(),
            _request(served_model_name="served-model", gpu_count=2),
            base_url="http://127.0.0.1:8080/",
            settings=PitwallSettings(),
        )
    assert result.engine == "vllm"
    assert result.variant is None
    assert result.gpu_count == 2
    assert result.proxy_base_url == ("http://127.0.0.1:8080/v1/openai/llm.serve-test/v1")


@pytest.mark.anyio
async def test_cache_env_volume_rules_and_startup_clamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = VariantInfo(
        variant_id="fp8",
        engine="vllm",
        image="example/vllm:test",
        repo="org/model",
        file=None,
        flags=(),
        companions=(),
        evidence=None,
        openai_chat=True,
        env={},
        container_disk_gb=60,
        startup_min=4,
        gated=False,
    )
    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)

    async def launch(**kwargs: Any) -> dict[str, Any]:
        return {"dry_run": True, "template_id": "template-plan"}

    monkeypatch.setattr(serve, "run_launch", launch)
    await serve.serve_model(
        object(),
        _request(image=None, dry_run=True),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(runpod_network_volume_id="volume-model-cache"),
        catalogue=Catalogue(info),
    )
    config = repos.provider_creates[0].config
    assert config["startup_timeout_s"] == 900
    assert config["readiness_path"] == "/health"
    assert config["env_vars"]["HF_HOME"] == "/workspace/hf"
    assert config["env_vars"]["HF_HUB_CACHE"] == "/workspace/hf/hub"

    repos_without_volume = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos_without_volume)
    await serve.serve_model(
        object(),
        _request(image=None, dry_run=True),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(),
        catalogue=Catalogue(info),
    )
    assert "HF_HOME" not in repos_without_volume.provider_creates[0].config["env_vars"]
    assert "HF_HUB_CACHE" not in repos_without_volume.provider_creates[0].config["env_vars"]


@pytest.mark.anyio
async def test_request_overrides_dossier_fields_and_non_gated_has_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = VariantInfo(
        variant_id="fp8",
        engine="vllm",
        image="example/row:test",
        repo="org/model",
        file=None,
        flags=("--row-flag", "yes"),
        companions=(),
        evidence=None,
        openai_chat=True,
        env={"ROW_ENV": "row", "KEEP_ENV": "1"},
        container_disk_gb=60,
        startup_min=20,
        gated=False,
    )
    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)
    seen: dict[str, Any] = {}

    async def launch(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"dry_run": True, "template_id": "template-plan"}

    monkeypatch.setattr(serve, "run_launch", launch)
    await serve.serve_model(
        object(),
        _request(
            image="example/request:test",
            container_disk_gb=42,
            env={"ROW_ENV": "request", "REQUEST_ENV": "1"},
            start_args=["--request-flag", "yes"],
            dry_run=True,
        ),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(pitwall_hf_token="hf-test-unused-canary"),
        catalogue=Catalogue(info),
    )
    config = repos.provider_creates[0].config
    assert config["image_ref"] == "example/request:test"
    assert config["container_disk_gb"] == 42
    assert config["env_vars"] == {
        "ROW_ENV": "request",
        "KEEP_ENV": "1",
        "REQUEST_ENV": "1",
    }
    assert config["docker_start_cmd"][-4:] == [
        "--row-flag",
        "yes",
        "--request-flag",
        "yes",
    ]
    assert seen["extra_env"] == {}


@pytest.mark.anyio
async def test_budget_rejection_propagates_before_any_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability(), _provider(), None)
    _install_repos(monkeypatch, repos)

    async def rejecting_run_launch(**kwargs: Any) -> dict[str, Any]:
        snapshot = BudgetSnapshot(
            monthly_budget_usd=Decimal("50"),
            per_request_max_usd=Decimal("10"),
            mtd_spend_usd=Decimal("49"),
            estimate_usd=Decimal("14.4"),
            budget_remaining_usd=Decimal("1"),
        )
        raise BudgetRejected("monthly_budget", snapshot)

    monkeypatch.setattr(serve, "run_launch", rejecting_run_launch)
    with pytest.raises(ServeBudgetExhausted) as raised:
        await serve.serve_model(
            object(),
            _request(),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
        )
    assert raised.value.to_response_body()["error"] == "budget_exhausted"


@pytest.mark.anyio
async def test_warm_only_verifies_then_tears_down_and_persists_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    pool = object()
    repos = Repos(_capability(), None, _lease())
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(
        serve,
        "run_launch",
        AsyncMock(return_value={"lease_id": "lease-serve-1", "workload_id": "work-serve"}),
    )
    monkeypatch.setattr(serve, "verify_served_model", AsyncMock(return_value=["org/model"]))
    teardown = AsyncMock(return_value=type("Closed", (), {"lease": _lease()})())
    monkeypatch.setattr(serve, "run_teardown", teardown)

    result = await serve.serve_model(
        pool,
        _request(network_volume_id="volume-cache", datacenter="US-EXAMPLE-1"),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(),
        catalogue=Catalogue(
            VariantInfo(
                variant_id="fp8",
                engine="vllm",
                image="example/model-server:test",
                repo="org/model",
                file=None,
                flags=(),
                companions=(),
                evidence=None,
                openai_chat=True,
                env={},
                container_disk_gb=40,
                startup_min=15,
                gated=False,
            )
        ),
        warm_only=True,
    )

    assert isinstance(result, serve.WarmResult)
    assert result.torn_down is True and result.volume_id == "volume-cache"
    assert result.gpu_class == "NVIDIA H100 80GB HBM3"
    assert result.gpu_count == 1
    assert result.price_source is None
    assert repos.provider_creates[0].config["network_volume_id"] == "volume-cache"
    assert repos.provider_creates[0].config["data_center_id"] == "US-EXAMPLE-1"
    warm_cache = repos.provider_patches[-1]["config"]["warm_cache"]
    assert warm_cache["variant"] == "fp8"
    assert warm_cache["volume_id"] == "volume-cache"
    assert warm_cache["verified_at"].endswith("+00:00")
    assert result.cache_state == "warm"
    assert result.variant == "fp8"
    assert serve.warm_cache_matches(repos.provider.config, variant="fp8") is True
    assert serve.warm_cache_matches(repos.provider.config, variant="awq") is False
    assert (
        serve.warm_cache_matches(
            {**repos.provider.config, "network_volume_id": "volume-other"}, variant="fp8"
        )
        is False
    )
    teardown.assert_awaited_once_with(
        "lease-serve-1", pool=pool, redis_client=None, reason="warm_complete"
    )


@pytest.mark.anyio
async def test_warm_only_dry_run_does_not_record_warm_cache_or_claim_warm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"template_id": "tpl-serve"}))

    result = await serve.serve_model(
        object(),
        _request(network_volume_id="volume-cache", dry_run=True),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(),
        warm_only=True,
    )

    assert result.cache_state == "cold"
    assert all("warm_cache" not in patch.get("config", {}) for patch in repos.provider_patches)


@pytest.mark.anyio
async def test_warm_only_without_volume_never_records_or_claims_warm_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(_capability(), None, _lease())
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"lease_id": "lease-serve-1"}))
    monkeypatch.setattr(serve, "verify_served_model", AsyncMock(return_value=["org/model"]))
    monkeypatch.setattr(
        serve, "run_teardown", AsyncMock(return_value=type("Closed", (), {"lease": _lease()})())
    )

    result = await serve.serve_model(
        object(),
        _request(),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(),
        warm_only=True,
    )

    assert result.cache_state == "cold"
    assert all("warm_cache" not in patch.get("config", {}) for patch in repos.provider_patches)


@pytest.mark.anyio
async def test_warm_only_verification_failure_tears_down_with_mismatch_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    pool = object()
    repos = Repos(_capability(), None, _lease())
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"lease_id": "lease-serve-1"}))
    monkeypatch.setattr(
        serve,
        "verify_served_model",
        AsyncMock(side_effect=ServeVerificationFailed("org/model", [])),
    )
    teardown = AsyncMock()
    monkeypatch.setattr(serve, "run_teardown", teardown)

    with pytest.raises(ServeVerificationFailed):
        await serve.serve_model(
            pool,
            _request(network_volume_id="volume-cache"),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(),
            warm_only=True,
        )

    teardown.assert_awaited_once_with(
        "lease-serve-1", pool=pool, redis_client=None, reason="served_model_mismatch"
    )


@pytest.mark.anyio
async def test_configured_stale_price_refuses_live_launch_before_runpod_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.models.prices import GpuPriceSnapshot

    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)
    snapshot = GpuPriceSnapshot(
        gpu_types=(),
        checked_at=_NOW - timedelta(seconds=61),
        source="live",
    )
    monkeypatch.setattr(serve, "load_gpu_price_snapshot", AsyncMock(return_value=snapshot))
    launch = AsyncMock()
    monkeypatch.setattr(serve, "run_launch", launch)

    with pytest.raises(serve.ServeStalePrice):
        await serve.serve_model(
            object(),
            _request(),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(pitwall_price_max_age_s=60),
        )

    launch.assert_not_awaited()


@pytest.mark.anyio
async def test_configured_stale_price_allows_active_lease_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.models.prices import GpuPriceSnapshot

    repos = Repos(_capability("org/model"), _provider({"openai_proxy_port": 8000}), _lease())
    _install_repos(monkeypatch, repos)
    snapshot = GpuPriceSnapshot(
        gpu_types=(),
        checked_at=_NOW - timedelta(seconds=61),
        source="fallback",
    )
    monkeypatch.setattr(serve, "load_gpu_price_snapshot", AsyncMock(return_value=snapshot))
    launch = AsyncMock()
    monkeypatch.setattr(serve, "run_launch", launch)

    with respx.mock:
        _mock_models(["org/model"])
        result = await serve.serve_model(
            object(),
            _request(),
            base_url="http://127.0.0.1:8080",
            settings=PitwallSettings(pitwall_price_max_age_s=60),
        )

    assert result.created is False
    assert result.lease_id == "lease-serve-1"
    assert result.price_stale is None
    assert result.price_source is None
    serve.load_gpu_price_snapshot.assert_not_awaited()
    launch.assert_not_awaited()


@pytest.mark.anyio
async def test_configured_stale_price_is_reported_by_dry_run_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.models.prices import GpuPriceSnapshot

    repos = Repos(_capability(), None, None)
    _install_repos(monkeypatch, repos)
    snapshot = GpuPriceSnapshot(
        gpu_types=(),
        checked_at=_NOW - timedelta(seconds=61),
        source="live",
    )
    monkeypatch.setattr(serve, "load_gpu_price_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(
        serve, "run_launch", AsyncMock(return_value={"dry_run": True, "template_id": "plan"})
    )

    result = await serve.serve_model(
        object(),
        _request(dry_run=True),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(pitwall_price_max_age_s=60),
    )

    assert result.price_source == "live"
    assert result.price_age_seconds is not None and result.price_age_seconds >= 61
    assert result.price_stale is True


def test_a_ttl_within_the_startup_budget_is_refused_before_launch() -> None:
    from types import SimpleNamespace

    thirty = SimpleNamespace(startup_min=30)
    with pytest.raises(serve.ServeTtlBelowStartup) as refused:
        serve.enforce_ttl_above_startup(30, thirty)  # type: ignore[arg-type]  # reason: test passes a SimpleNamespace stand-in where VariantInfo is expected
    assert refused.value.to_response_body()["error"] == "ttl_below_startup"
    serve.enforce_ttl_above_startup(31, thirty)  # type: ignore[arg-type]  # reason: test passes a SimpleNamespace stand-in where VariantInfo is expected
    with pytest.raises(serve.ServeTtlBelowStartup):
        serve.enforce_ttl_above_startup(15, None)  # no dossier: the 30 min default budget
    serve.enforce_ttl_above_startup(serve.DEFAULT_TTL_MINUTES, None)


# --- A repeated idempotency key never launches a second serving pod (Task 7c) ---------------


def _record_publishes(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    published: list[Any] = []

    async def publish(_pool: Any, _redis: Any, event: Any) -> None:
        published.append(event)

    monkeypatch.setattr(serve, "publish_lease_event", publish)
    return published


def _mock_models(model_ids: list[str]) -> Any:
    return respx.get("https://pod-serve-1-8000.proxy.runpod.net/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": item} for item in model_ids]})
    )


def _launch_error(name: str) -> Any:
    """The error class the launch module serve calls raises (suites re-import pitwall.api)."""
    return serve.replay_idempotent_launch.__globals__[name]


def _keyed_serve_workload(request: serve.ServeRequest, *, state: str = "running") -> Any:
    from pitwall.api.leases.launch import LAUNCH_REQUEST_DIGEST_KEY
    from pitwall.core.models import Workload

    return Workload(
        id="wkl-serve",
        capability_id="cap_llm_serve",
        provider_id="prov_serve",
        type="inference",
        state=state,
        idempotency_key="serve-key-1",
        input={LAUNCH_REQUEST_DIGEST_KEY: serve.serve_request_fingerprint(request)},
        submitted_at=_NOW,
    )


def _keyed_repos(monkeypatch: pytest.MonkeyPatch) -> Repos:
    repos = Repos(
        _capability("org/model"),
        _provider({"engine": "vllm", "gpu_count": 1, "openai_proxy_port": 8000}),
        None,
    )
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", pytest.fail)
    return repos


async def _serve(request: serve.ServeRequest) -> Any:
    return await serve.serve_model(
        object(), request, base_url="http://127.0.0.1:8080", settings=PitwallSettings()
    )


def test_serve_request_fingerprint_covers_the_request_but_not_the_key_or_dry_run() -> None:
    base = serve.serve_request_fingerprint(_request(idempotency_key="serve-key-1"))

    assert base == serve.serve_request_fingerprint(_request(idempotency_key="serve-key-2"))
    assert base == serve.serve_request_fingerprint(
        _request(idempotency_key="serve-key-1", dry_run=True)
    )
    for change in (
        {"model": "org/other"},
        {"gpu_class": "NVIDIA L4"},
        {"gpu_count": 2},
        {"variant": "fp8"},
        {"ttl_minutes": 30},
        {"image": "example/other-server:test"},
        {"env": {"EXTRA": "1"}},
        {"start_args": ["--max-model-len", "4096"]},
        {"max_usd_per_hour": Decimal("1.5")},
    ):
        assert base != serve.serve_request_fingerprint(
            _request(idempotency_key="serve-key-1", **change)
        ), change


@pytest.mark.anyio
async def test_repeated_key_returns_the_keyed_lease_without_registry_writes_or_a_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(idempotency_key="serve-key-1")
    repos = _keyed_repos(monkeypatch)
    repos.keyed_workload = _keyed_serve_workload(request)
    repos.keyed_lease = _lease()
    published = _record_publishes(monkeypatch)

    with respx.mock:
        models = _mock_models(["org/model"])
        result = await _serve(request)

    assert models.called and len(published) == 1
    assert repos.key_lookups == ["serve-key-1"]
    assert result.created is False and result.lease_id == "lease-serve-1"
    assert repos.provider_patches == [] and repos.capability_patches == []
    assert repos.provider_creates == [] and repos.capability_creates == []


@pytest.mark.anyio
async def test_repeated_key_with_a_different_request_is_idempotency_mismatch_before_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = _keyed_repos(monkeypatch)
    repos.keyed_workload = _keyed_serve_workload(_request(idempotency_key="serve-key-1"))
    repos.keyed_lease = _lease()

    with pytest.raises(_launch_error("IdempotencyMismatch")):
        await _serve(_request(idempotency_key="serve-key-1", gpu_count=2))
    assert repos.provider_patches == [] and repos.capability_patches == []


@pytest.mark.anyio
async def test_repeated_key_while_the_first_serve_has_no_lease_is_mutation_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(idempotency_key="serve-key-1")
    repos = _keyed_repos(monkeypatch)
    repos.keyed_workload = _keyed_serve_workload(request, state="queued")

    with pytest.raises(_launch_error("LeaseLaunchInProgress")):
        await _serve(request)
    assert repos.provider_patches == []


@pytest.mark.anyio
async def test_repeated_key_whose_lease_ended_is_lease_state_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(idempotency_key="serve-key-1")
    repos = _keyed_repos(monkeypatch)
    repos.keyed_workload = _keyed_serve_workload(request, state="completed")
    repos.keyed_lease = _lease().model_copy(update={"state": LeaseState.STOPPED})

    with pytest.raises(serve.LeaseStateConflict) as raised:
        await _serve(request)
    assert raised.value.to_response_body() == {
        "error": "lease_state_conflict",
        "id": "lease-serve-1",
        "state": "stopped",
        "operation": "serve",
    }


@pytest.mark.anyio
async def test_first_keyed_serve_passes_its_request_digest_to_the_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(idempotency_key="serve-key-1", served_model_name="served-model")
    repos = Repos(_capability(), _provider(), None)
    _install_repos(monkeypatch, repos)
    seen: dict[str, Any] = {}

    async def launch(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        repos.lease = _lease()
        return {"lease_id": "lease-serve-1", "workload_id": "wkl-serve"}

    monkeypatch.setattr(serve, "run_launch", launch)
    with respx.mock:
        respx.get("https://pod-serve-1-8000.proxy.runpod.net/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "served-model"}]})
        )
        result = await _serve(request)

    assert result.created is True
    assert seen["idempotency_key"] == "serve-key-1"
    assert seen["request_fingerprint"] == serve.serve_request_fingerprint(request)


@pytest.mark.anyio
async def test_a_lost_admission_race_while_the_winner_is_not_ready_is_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loser never verifies, publishes, or tears down a lease the winner is still readying."""
    request = _request(idempotency_key="serve-key-1")
    repos = Repos(
        _capability("org/model"),
        _provider({"engine": "vllm", "gpu_count": 1, "openai_proxy_port": 8000}),
        None,
    )
    _install_repos(monkeypatch, repos)
    published = _record_publishes(monkeypatch)

    async def launch(**_kwargs: Any) -> dict[str, Any]:
        repos.lease = _lease().model_copy(update={"state": LeaseState.CREATING})
        return {"lease_id": "lease-serve-1", "workload_id": "wkl-serve", "replayed": True}

    async def no_teardown(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a replay never tears down the original lease")

    monkeypatch.setattr(serve, "run_launch", launch)
    monkeypatch.setattr(serve, "run_teardown", no_teardown)
    monkeypatch.setattr(serve, "verify_served_model", pytest.fail)

    with pytest.raises(_launch_error("LeaseLaunchInProgress")) as raised:
        await _serve(request)
    assert raised.value.error_code == "mutation_in_progress"
    assert published == []


@pytest.mark.anyio
async def test_a_lost_admission_race_on_an_active_lease_verifies_before_publishing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(idempotency_key="serve-key-1")
    repos = Repos(
        _capability("org/model"),
        _provider({"engine": "vllm", "gpu_count": 1, "openai_proxy_port": 8000}),
        None,
    )
    _install_repos(monkeypatch, repos)
    published = _record_publishes(monkeypatch)

    async def launch(**_kwargs: Any) -> dict[str, Any]:
        repos.lease = _lease()
        return {"lease_id": "lease-serve-1", "workload_id": "wkl-serve", "replayed": True}

    async def no_teardown(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a replay never tears down the original lease")

    monkeypatch.setattr(serve, "run_launch", launch)
    monkeypatch.setattr(serve, "run_teardown", no_teardown)

    with respx.mock:
        models = _mock_models(["org/model"])
        result = await _serve(request)

    assert result.created is False and result.lease_id == "lease-serve-1"
    assert models.called and len(published) == 1


@pytest.mark.anyio
async def test_a_keyed_replay_of_a_creating_lease_is_in_progress_and_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(idempotency_key="serve-key-1")
    repos = _keyed_repos(monkeypatch)
    repos.keyed_workload = _keyed_serve_workload(request, state="queued")
    repos.keyed_lease = _lease().model_copy(update={"state": LeaseState.CREATING})
    published = _record_publishes(monkeypatch)
    monkeypatch.setattr(serve, "verify_served_model", pytest.fail)

    with pytest.raises(_launch_error("LeaseLaunchInProgress")) as raised:
        await _serve(request)
    assert raised.value.error_code == "mutation_in_progress"
    assert published == []


@pytest.mark.anyio
async def test_an_unkeyed_replay_of_a_waiting_probe_lease_is_in_progress_and_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(
        _capability("org/model"),
        _provider({"engine": "vllm", "gpu_count": 1, "openai_proxy_port": 8000}),
        _lease().model_copy(update={"state": LeaseState.WAITING_PROBE}),
    )
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", pytest.fail)
    monkeypatch.setattr(serve, "verify_served_model", pytest.fail)
    published = _record_publishes(monkeypatch)

    with pytest.raises(_launch_error("LeaseLaunchInProgress")) as raised:
        await _serve(_request())
    assert raised.value.error_code == "mutation_in_progress"
    assert published == []


@pytest.mark.anyio
async def test_an_active_replay_whose_model_does_not_verify_is_in_progress_without_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = Repos(
        _capability("org/model"),
        _provider({"engine": "vllm", "gpu_count": 1, "openai_proxy_port": 8000}),
        _lease(),
    )
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", pytest.fail)
    monkeypatch.setattr(serve, "VERIFY_REQUEST_TIMEOUT_S", 0.01)
    monkeypatch.setattr(serve, "VERIFY_RETRY_INTERVAL_S", 0.0)

    async def no_teardown(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a replay never tears down the lease")

    monkeypatch.setattr(serve, "run_teardown", no_teardown)
    published = _record_publishes(monkeypatch)

    with respx.mock:
        _mock_models(["still-loading"])
        with pytest.raises(serve.LeaseNotServing) as raised:
            await _serve(_request())
    assert published == []
    body = raised.value.to_response_body()
    assert body["error"] == "mutation_in_progress" and body["reason"] == "lease_not_serving"
    assert body["id"] == "lease-serve-1" and "pitwall_stop_lease" in body["remedy"]


@pytest.mark.anyio
async def test_a_keyed_dry_run_never_consults_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = Repos(_capability(), _provider(), None)
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(serve, "run_launch", AsyncMock(return_value={"dry_run": True}))

    await _serve(_request(idempotency_key="serve-key-1", dry_run=True))

    assert repos.key_lookups == []
