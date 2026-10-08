from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall.api.admin.kill_switch import KillSwitchEngaged
from pitwall.api.exceptions import PreSpendPayloadRejected
from pitwall.api.leases import launch
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderType,
)
from pitwall.core.models import Capability, LeaseReadiness, Provider
from pitwall.cost.budget_gate import BudgetRejected, BudgetSnapshot
from pitwall.providers.interface import CredentialResolutionError
from pitwall.runpod_client import pods, templates
from pitwall.security.pre_spend import PreSpendInspectionService
from tests.conftest import make_asyncpg_pool


class _ProviderLockOnlyConnection:
    """The one query a launch may run on a fake pool: the serve-provider row lock."""

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        if "FROM pitwall.providers" not in sql or "FOR UPDATE" not in sql:
            raise AssertionError("fake repository should not acquire directly")
        return {"config": {}, "health_status": "unknown"}

    def transaction(self) -> _ProviderLockOnlyConnection:
        return self

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class _ProviderLockOnlyAcquire:
    async def __aenter__(self) -> _ProviderLockOnlyConnection:
        return _ProviderLockOnlyConnection()

    async def __aexit__(self, *exc: object) -> None:
        return None


def _capability() -> Capability:
    now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
    return Capability(
        id="cap_llm_qwen3",
        name="llm.qwen3-32b",
        version="1",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        created_at=now,
        updated_at=now,
    )


def _provider(config: dict[str, Any] | None = None) -> Provider:
    return Provider(
        id="prov_qwen3_h100",
        capability_id="cap_llm_qwen3",
        name="qwen3-h100-pod-us-ca",
        provider_type=ProviderType.POD_LEASE,
        region="US-CA-2",
        cloud_type="SECURE",
        config=config
        or {
            "image_ref": "ghcr.io/acme/pitwall-worker:qwen3",
            "template_name": "pitwall-qwen3-h100",
            "gpu_type_priority": ["NVIDIA H100 80GB HBM3", "NVIDIA L4"],
            "container_disk_gb": 80,
            "volume_id": "vol-model-cache",
            "volume_mount": "/workspace",
            "ports": {"http": [8000], "tcp": [22]},
            "env_vars": {"VLLM_MODEL": "Qwen/Qwen3-32B"},
            "cost": {"per_second_active": "0.002"},
        },
        priority=1,
        source=CapabilitySource.API,
        updated_at=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
    )


def test_estimate_lease_launch_cost_reserves_rate_times_ttl() -> None:
    config = dict(_provider().config)
    config["lease_ttl_ms"] = 3_600_000
    # 0.002 USD/s * 3600 s
    assert launch.estimate_lease_launch_cost(_capability(), _provider(config)) == Decimal(
        "7.200000"
    )


@pytest.mark.anyio
async def test_ready_persist_records_ready_at_and_automation_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    ready_at = datetime(2026, 8, 28, 12, 0, 30, tzinfo=UTC)

    class Repo:
        def __init__(self, _pool: object) -> None:
            pass

        async def update_state(self, lease_id: str, state: str) -> None:
            calls.append(("state", state))

        async def update_readiness(
            self,
            lease_id: str,
            readiness: LeaseReadiness,
        ) -> None:
            calls.append(("readiness", readiness))

        async def mark_ready(self, lease_id: str, *, ready_at: datetime) -> object:
            calls.append(("ready_at", ready_at))
            return object()

    class Pool:
        acquire = True

    monkeypatch.setattr(launch, "LeaseRepository", Repo)
    await launch._persist_ready_lease(
        Pool(),
        lease_id="lease-ready",
        ready_pod={
            "id": "pod-ready",
            "readiness": {
                "runtime_seen_at": ready_at.isoformat(),
                "port_mappings_seen_at": ready_at.isoformat(),
                "probe_passed_at": ready_at.isoformat(),
                "probe_method": "runpod_proxy",
            },
        },
    )

    assert ("ready_at", ready_at) in calls


def test_estimate_lease_launch_cost_defaults_to_two_hours() -> None:
    assert launch.estimate_lease_launch_cost(_capability(), _provider()) == Decimal("14.400000")


@pytest.mark.parametrize(
    ("cost", "reserved"),
    [
        pytest.param(
            {"kind": "per_vm_second", "rate_per_second": "0.0005"}, "3.600000", id="per_vm_second"
        ),
        pytest.param({"kind": "zero"}, "1.000000", id="zero-reserves-the-fallback"),
        pytest.param(
            {"kind": "per_request", "per_request": "0.01"}, "1.000000", id="per_request-fallback"
        ),
    ],
)
def test_estimate_lease_launch_cost_prices_other_pricing_over_the_ttl(
    cost: dict[str, str], reserved: str
) -> None:
    """Pricing that is neither per_second nor gpu_hour reserves its lease rate x the 2 h TTL."""
    config = dict(_provider().config)
    config["cost"] = cost
    assert launch.estimate_lease_launch_cost(_capability(), _provider(config)) == Decimal(reserved)


def test_estimate_lease_launch_cost_uses_bid_ceiling() -> None:
    config = dict(_provider().config)
    config["lease_ttl_ms"] = 1_000
    config["cost"] = {"per_second_active": "0.002", "bid_rate_per_second": "0.005"}
    assert launch.estimate_lease_launch_cost(_capability(), _provider(config)) == Decimal(
        "0.005000"
    )


@pytest.mark.anyio
async def test_arm_serve_provider_writes_pod_facts_without_launch_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched: dict[str, Any] = {}
    audits: list[dict[str, Any]] = []
    hf_canary = "hf-test-audit-canary"

    class FakeProviderRepository:
        def __init__(self, pool: object) -> None:
            pass

        async def patch(self, provider_id: str, **kwargs: Any) -> Any:
            patched["provider_id"] = provider_id
            patched.update(kwargs)
            return None

    async def fake_insert_audit(pool: object, **kwargs: Any) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(launch, "ProviderRepository", FakeProviderRepository)
    monkeypatch.setattr(launch, "insert_audit", fake_insert_audit)
    config = dict(_provider().config)
    config["openai_proxy_port"] = 8000
    provider = _provider(config)
    pod_env = launch._env_for_pod(_capability(), provider, extra_env={"HF_TOKEN": hf_canary})

    class _Pool:
        acquire = None

    armed = await launch.arm_serve_provider(
        _Pool(), provider=provider, lease_id="lease-serve", pod_id="pod-serve-1"
    )

    assert armed is True
    assert pod_env["HF_TOKEN"] == hf_canary
    assert patched["provider_id"] == "prov_qwen3_h100"
    assert patched["health_status"] == "healthy"
    assert patched["config"]["active_pod_id"] == "pod-serve-1"
    assert patched["config"]["active_lease_id"] == "lease-serve"
    assert patched["config"]["openai_proxy_port"] == 8000
    assert audits[0]["action"] == "lease_ready"
    assert audits[0]["actor"] == "system:lease"
    assert audits[0]["entity_id"] == "prov_qwen3_h100"
    assert "HF_TOKEN" not in str(patched)
    assert hf_canary not in str(patched)
    assert "HF_TOKEN" not in str(audits)
    assert hf_canary not in str(audits)


@pytest.mark.anyio
async def test_arm_serve_provider_is_noop_without_proxy_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingProviderRepository:
        def __init__(self, pool: object) -> None:
            raise AssertionError("provider must not be patched")

    monkeypatch.setattr(launch, "ProviderRepository", ExplodingProviderRepository)

    class _Pool:
        acquire = None

    armed = await launch.arm_serve_provider(
        _Pool(), provider=_provider(), lease_id="lease-x", pod_id="pod-x"
    )
    assert armed is False


@pytest.mark.anyio
async def test_run_launch_arms_serve_provider_after_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    armed: list[tuple[str, str]] = []

    class FakeBudgetGate:
        async def try_launch(self, **kwargs: Any) -> str:
            return "wkl_serve"

    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-serve"

    def fake_create_pod_with_fallback_sync(**kwargs: Any) -> dict[str, Any]:
        return {"id": "pod-serve-1", "name": kwargs["name"]}

    async def fake_arm(pool: object, *, provider: Any, lease_id: str, pod_id: str) -> bool:
        armed.append((lease_id, pod_id))
        return True

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(pods, "create_pod_with_fallback_sync", fake_create_pod_with_fallback_sync)
    monkeypatch.setattr(launch, "arm_serve_provider", fake_arm)

    result = await launch.run_launch(
        pool=object(), capability=_capability(), provider=_provider(), budget_gate=FakeBudgetGate()
    )
    assert armed == [(result["lease_id"], "pod-serve-1")]


@pytest.mark.anyio
async def test_prepare_lease_launch_carries_docker_start_cmd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-serve"

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    config = dict(_provider().config)
    config["docker_start_cmd"] = [
        "--model",
        "org/model",
        "--served-model-name",
        "m",
        "--port",
        "8000",
    ]
    plan = await launch.prepare_lease_launch(object(), _capability(), _provider(config))
    assert plan.docker_start_cmd == [
        "--model",
        "org/model",
        "--served-model-name",
        "m",
        "--port",
        "8000",
    ]


@pytest.mark.anyio
async def test_prepare_lease_launch_without_start_cmd_leaves_it_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-serve"

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    plan = await launch.prepare_lease_launch(object(), _capability(), _provider())
    assert plan.docker_start_cmd is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("configured_path", "expected_path"),
    [(None, "/health"), ("/health_generate", "/health_generate")],
)
async def test_prepare_lease_launch_carries_readiness_path(
    monkeypatch: pytest.MonkeyPatch,
    configured_path: str | None,
    expected_path: str,
) -> None:
    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-serve"

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    config = dict(_provider().config)
    if configured_path is not None:
        config["readiness_path"] = configured_path

    plan = await launch.prepare_lease_launch(object(), _capability(), _provider(config))

    assert plan.readiness_path == expected_path


@pytest.mark.anyio
async def test_run_launch_forwards_readiness_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class FakeBudgetGate:
        async def try_launch(self, **kwargs: Any) -> str:
            return "wkl_serve"

    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-serve"

    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"id": "eptest00000001", "name": str(kwargs["name"])}

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create)
    config = dict(_provider().config)
    config["readiness_path"] = "/health_generate"

    await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=_provider(config),
        budget_gate=FakeBudgetGate(),
    )

    assert seen["readiness_path"] == "/health_generate"


@pytest.mark.anyio
async def test_run_launch_forwards_docker_start_cmd_to_pod_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class FakeBudgetGate:
        async def try_launch(self, **kwargs: Any) -> str:
            return "wkl_serve"

    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-serve"

    def fake_create_pod_with_fallback_sync(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"id": "pod-serve-1", "name": kwargs["name"]}

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(pods, "create_pod_with_fallback_sync", fake_create_pod_with_fallback_sync)
    config = dict(_provider().config)
    config["docker_start_cmd"] = ["--model", "org/model"]

    await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=_provider(config),
        budget_gate=FakeBudgetGate(),
    )
    assert seen["docker_start_cmd"] == ["--model", "org/model"]


@pytest.mark.anyio
async def test_authenticated_registry_command_reaches_v1_create_without_persisting_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class FakeBudgetGate:
        async def try_launch(self, **kwargs: Any) -> str:
            return "wkl_authenticated"

    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-authenticated"

    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"id": "pod-authenticated", "name": kwargs["name"]}

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create)
    provider = _provider(
        {
            **_provider().config,
            "engine": "vllm",
            "endpoint_auth": True,
            "docker_entrypoint": ["sh", "-c"],
            "docker_start_cmd": ["org/model", "--port", "8000"],
        }
    )

    await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=provider,
        extra_env={"PITWALL_ENDPOINT_KEY": "unit-secret"},
        budget_gate=FakeBudgetGate(),
    )

    assert seen["docker_entrypoint"] == ["sh", "-c"]
    assert seen["docker_start_cmd"] == [
        'exec vllm serve org/model --port 8000 --api-key "$PITWALL_ENDPOINT_KEY"'
    ]
    assert seen["env"]["PITWALL_ENDPOINT_KEY"] == "unit-secret"
    assert provider.credential_ref == "RUNPOD_API_KEY"
    assert "unit-secret" not in repr(provider.config)


@pytest.mark.anyio
async def test_authenticated_registry_launch_fails_closed_without_endpoint_key() -> None:
    provider = _provider(
        {
            **_provider().config,
            "endpoint_auth": True,
            "docker_start_cmd": ["org/model"],
        }
    )
    with pytest.raises(launch.InvalidProviderConfig, match="PITWALL_ENDPOINT_KEY"):
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=provider,
            budget_gate=AsyncMock(),
        )


def test_authenticated_sglang_command_keeps_explicit_server_binary() -> None:
    provider = _provider(
        {
            **_provider().config,
            "engine": "sglang",
            "endpoint_auth": True,
            "docker_entrypoint": ["sh", "-c"],
            "docker_start_cmd": [
                "python3",
                "-m",
                "sglang.launch_server",
                "--model-path",
                "org/model",
            ],
        }
    )

    assert launch._docker_start_cmd(provider) == [
        'exec python3 -m sglang.launch_server --model-path org/model --api-key "$PITWALL_ENDPOINT_KEY"'
    ]


def test_authenticated_command_rejects_inline_model_api_key() -> None:
    provider = _provider(
        {
            **_provider().config,
            "endpoint_auth": True,
            "docker_entrypoint": ["sh", "-c"],
            "docker_start_cmd": ["org/model", "--api-key=embedded-secret"],
        }
    )

    with pytest.raises(launch.InvalidProviderConfig, match="literal model API key"):
        launch._docker_start_cmd(provider)


@pytest.mark.anyio
@pytest.mark.parametrize(("configured", "expected"), [(None, 600), (1_800, 1_800)])
async def test_run_launch_forwards_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
    configured: int | None,
    expected: int,
) -> None:
    seen: dict[str, Any] = {}

    class Gate:
        async def try_launch(self, **kwargs: Any) -> str:
            return "wkl-timeout"

    async def template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-timeout"

    def pod(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"id": "pod-timeout-1", "name": kwargs["name"]}

    monkeypatch.setattr(launch, "ensure_template", template)
    monkeypatch.setattr(pods, "create_pod_with_fallback_sync", pod)
    config = dict(_provider().config)
    if configured is not None:
        config["startup_timeout_s"] = configured
    await launch.run_launch(
        pool=object(), capability=_capability(), provider=_provider(config), budget_gate=Gate()
    )
    assert seen["startup_timeout_s"] == expected


def test_template_env_keys_use_pitwall_identity_names() -> None:
    assert "PITWALL_CAPABILITY" in templates.TEMPLATE_ENV_KEYS
    assert "PITWALL_CAPABILITY_ID" in templates.TEMPLATE_ENV_KEYS
    assert "PITWALL_PROVIDER" in templates.TEMPLATE_ENV_KEYS
    assert "PITWALL_PROVIDER_ID" in templates.TEMPLATE_ENV_KEYS
    assert "AWS_SESSION_TOKEN" in templates.TEMPLATE_ENV_KEYS
    assert "R2_CREDENTIAL_EXPIRES_AT" in templates.TEMPLATE_ENV_KEYS
    assert "CLOUD_CAPABILITY" not in templates.TEMPLATE_ENV_KEYS
    assert "CLOUD_PROVIDER" not in templates.TEMPLATE_ENV_KEYS
    assert "R2_ACCESS_KEY" not in templates.TEMPLATE_ENV_KEYS
    assert "R2_SECRET_KEY" not in templates.TEMPLATE_ENV_KEYS


def test_env_for_pod_uses_capability_and_provider_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://pitwall-redis/4")
    monkeypatch.setenv("R2_ENDPOINT", "https://r2.example.test")
    monkeypatch.setenv("R2_ACCESS_KEY", "parent-access-key")
    monkeypatch.setenv("R2_SECRET_KEY", "parent-secret-key")

    env = launch._env_for_pod(
        _capability(),
        _provider(),
        request_id="req_123",
        extra_env={"OPENAI_SERVED_MODEL_NAME_OVERRIDE": "qwen3"},
    )

    assert env["PITWALL_CAPABILITY"] == "llm"
    assert env["PITWALL_CAPABILITY_ID"] == "cap_llm_qwen3"
    assert env["PITWALL_CAPABILITY_NAME"] == "llm.qwen3-32b"
    assert env["PITWALL_PROVIDER"] == "qwen3-h100-pod-us-ca"
    assert env["PITWALL_PROVIDER_ID"] == "prov_qwen3_h100"
    assert env["PITWALL_PROVIDER_TYPE"] == "pod_lease"
    assert env["PITWALL_REQUEST_ID"] == "req_123"
    assert env["REDIS_URL"] == "redis://pitwall-redis/4"
    assert env["R2_ENDPOINT"] == "https://r2.example.test"
    assert "R2_ACCESS_KEY" not in env
    assert "R2_SECRET_KEY" not in env
    assert env["VLLM_MODEL"] == "Qwen/Qwen3-32B"
    assert env["OPENAI_SERVED_MODEL_NAME_OVERRIDE"] == "qwen3"
    assert "CLOUD_CAPABILITY" not in env
    assert "CLOUD_PROVIDER" not in env


def test_env_for_pod_rejects_identity_overrides() -> None:
    provider = _provider({"env_vars": {"PITWALL_PROVIDER_ID": "forged"}})

    with pytest.raises(launch.InvalidProviderConfig, match="identity key"):
        launch._env_for_pod(_capability(), provider)


def test_env_for_pod_rejects_hf_token_from_provider_config() -> None:
    provider = _provider({"env_vars": {"HF_TOKEN": "hf-test-persisted-canary"}})
    with pytest.raises(launch.InvalidProviderConfig, match="launch identity key"):
        launch._env_for_pod(_capability(), provider)


def test_env_for_pod_accepts_hf_token_as_launch_extra() -> None:
    env = launch._env_for_pod(
        _capability(),
        _provider(),
        extra_env={"HF_TOKEN": "hf-test-launch-only-canary"},
    )
    assert env["HF_TOKEN"] == "hf-test-launch-only-canary"


def test_env_for_pod_rejects_storage_credential_overrides() -> None:
    provider = _provider({"env_vars": {"AWS_SECRET_ACCESS_KEY": "forged"}})

    with pytest.raises(launch.InvalidProviderConfig, match="storage credential key"):
        launch._env_for_pod(_capability(), provider)


def test_env_for_pod_adds_vended_staging_store_credentials() -> None:
    class FakeStagingStore:
        def vend_pod_credentials(self) -> dict[str, str]:
            return {
                "AWS_ACCESS_KEY_ID": "tmp-access",
                "AWS_SECRET_ACCESS_KEY": "tmp-secret",
                "AWS_SESSION_TOKEN": "tmp-session",
                "R2_CREDENTIAL_EXPIRES_AT": "2026-05-28T13:00:00Z",
            }

        def cleanup_pod_artifacts(self, pods: list[dict[str, Any]]) -> list[Any]:
            raise AssertionError("launch must not clean up staging artifacts")

    env = launch._env_for_pod(
        _capability(),
        _provider(),
        staging_store=FakeStagingStore(),
    )

    assert env["AWS_ACCESS_KEY_ID"] == "tmp-access"
    assert env["AWS_SECRET_ACCESS_KEY"] == "tmp-secret"
    assert env["AWS_SESSION_TOKEN"] == "tmp-session"
    assert env["R2_CREDENTIAL_EXPIRES_AT"] == "2026-05-28T13:00:00Z"
    assert "R2_ACCESS_KEY" not in env
    assert "R2_SECRET_KEY" not in env


def test_max_cost_per_hr_reads_provider_constraints() -> None:
    provider = _provider(
        {
            "image_ref": "ghcr.io/acme/pitwall-worker:qwen3",
            "template_name": "pitwall-qwen3-h100",
            "gpu_type_priority": ["NVIDIA H100 80GB HBM3"],
            "constraints": {"max_cost_per_hr": "1.25"},
        }
    )

    assert launch._max_cost_per_hr(provider) == 1.25


def test_provider_attach_timeout_reads_provider_constraints() -> None:
    provider = _provider(
        {
            "image_ref": "ghcr.io/acme/pitwall-worker:qwen3",
            "template_name": "pitwall-qwen3-h100",
            "gpu_type_priority": ["NVIDIA H100 80GB HBM3"],
            "constraints": {"max_attach_hang_s": "42"},
        }
    )

    assert launch._provider_attach_timeout_s(provider) == 42.0


def test_workload_config_forwards_positive_gpu_count() -> None:
    config = {**_provider().config, "gpu_count": 8}
    assert launch._workload_config_for_provider(_capability(), _provider(config)).gpu_count == 8


class _FakeLeaseAcquire:
    def __init__(self, conn: _FakeLeaseConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeLeaseConnection:
        return self._conn

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _FakeLeaseConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.rows: dict[str, dict[str, Any]] = {}

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any]:
        self.queries.append(query)
        lease_id = str(args[0])
        if lease_id in self.rows and "ON CONFLICT (id) DO UPDATE" not in query:
            raise RuntimeError("duplicate lease id")
        row = {
            "id": lease_id,
            "provider_id": args[1],
            "workload_id": args[2],
            "external_resource_id": args[3],
            "runpod_pod_id": args[4],
            "state": args[5],
            "created_at": args[6],
            "expires_at": args[7],
            "renewal_policy": args[8],
            "auto_teardown_on_expiry": args[9],
            "endpoints": None,
            "readiness": None,
            "cost_accrued_usd": args[12],
            "last_health_at": args[13],
            "terminated_at": None,
            "terminated_reason": None,
        }
        self.rows[lease_id] = row
        return row


class _FakeLeasePool:
    def __init__(self, conn: _FakeLeaseConnection) -> None:
        self._conn = conn

    def acquire(self) -> _FakeLeaseAcquire:
        return _FakeLeaseAcquire(self._conn)


@pytest.mark.anyio
async def test_pre_lease_persist_callback_upserts_retried_lease_id() -> None:
    conn = _FakeLeaseConnection()
    created_at = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
    expiry = datetime(2026, 5, 28, 14, 0, tzinfo=UTC)
    callback = launch._make_pre_lease_persist_callback(
        pool=_FakeLeasePool(conn),
        loop=asyncio.get_running_loop(),
        lease_id="lease_retry",
        provider=_provider(),
        provider_id="prov_qwen3_h100",
        workload_id="wkl-lease-retry",
        created_at=created_at,
        expiry=expiry,
        planned_endpoints=None,
    )

    await asyncio.to_thread(callback, {"id": "pod-first"})
    await asyncio.to_thread(callback, {"id": "pod-retry"})

    assert conn.rows["lease_retry"]["external_resource_id"] == "pod-retry"
    assert conn.rows["lease_retry"]["runpod_pod_id"] == "pod-retry"
    assert conn.rows["lease_retry"]["workload_id"] == "wkl-lease-retry"
    assert len(conn.queries) == 2
    assert all("ON CONFLICT (id) DO UPDATE" in query for query in conn.queries)


@pytest.mark.anyio
async def test_ensure_launch_template_uses_provider_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        calls.append({"pool": pool, "image_ref": image_ref, **kwargs})
        return "template-abc123"

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(
        launch,
        "get_registry_auth_id_from_env",
        lambda image_ref: f"auth-for-{image_ref}",
    )
    pool = object()

    template = await launch.ensure_launch_template(pool, _capability(), _provider())

    assert template.template_id == "template-abc123"
    assert template.template_name == "pitwall-qwen3-h100"
    assert template.image_ref == "ghcr.io/acme/pitwall-worker:qwen3"
    assert template.registry_auth_id == "auth-for-ghcr.io/acme/pitwall-worker:qwen3"
    assert template.container_disk_gb == 80
    assert template.volume_mount_path == "/workspace"
    assert calls == [
        {
            "pool": pool,
            "image_ref": "ghcr.io/acme/pitwall-worker:qwen3",
            "template_name": "pitwall-qwen3-h100",
            "registry_auth_id": "auth-for-ghcr.io/acme/pitwall-worker:qwen3",
            "container_disk_gb": 80,
            "volume_mount_path": "/workspace",
            "docker_start_cmd": (),
            "ports": "8000/http,22/tcp",
            "env": {"VLLM_MODEL": "Qwen/Qwen3-32B"},
        }
    ]


@pytest.mark.anyio
async def test_ensure_launch_template_forwards_config_identity_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_ensure_template(_pool: object, _image_ref: str, **kwargs: Any) -> str:
        calls.append(kwargs)
        return "template-configured"

    config = dict(_provider().config)
    config["docker_start_cmd"] = ["--model", "org/model"]
    config["env_vars"] = {
        "MODEL_ID": "org/model",
        "CACHE_DIR": "/workspace/cache",
    }
    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)

    await launch.ensure_launch_template(
        object(),
        _capability(),
        _provider(config),
        api_key="explicit-key",
        graphql_url="https://graphql.runpod.test/graphql",
        rest_api_url="https://rest.runpod.test/v1",
    )

    assert calls[0]["docker_start_cmd"] == ["--model", "org/model"]
    assert calls[0]["ports"] == "8000/http,22/tcp"
    assert calls[0]["env"] == config["env_vars"]
    assert calls[0]["api_key"] == "explicit-key"
    assert calls[0]["graphql_url"] == "https://graphql.runpod.test/graphql"
    assert calls[0]["rest_api_url"] == "https://rest.runpod.test/v1"


@pytest.mark.anyio
async def test_ensure_launch_template_does_not_require_gpu_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        return "template-image-only"

    provider = _provider(
        {
            "image_ref": "ghcr.io/acme/pitwall-worker:image-only",
            "template_name": "pitwall-image-only",
        }
    )
    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)

    template = await launch.ensure_launch_template(object(), _capability(), provider)

    assert template.template_id == "template-image-only"
    assert template.template_name == "pitwall-image-only"
    assert template.container_disk_gb == 50


@pytest.mark.anyio
async def test_configured_pod_template_short_circuits_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_get(template_id: str, **kwargs: Any) -> templates.Template:
        calls.append((template_id, kwargs))
        return templates.Template(
            id=template_id,
            name="community-vllm",
            imageName="ghcr.io/example/model-server:test",
            containerDiskInGb=40,
            volumeMountPath="/workspace",
            isServerless=False,
        )

    async def fail_generated(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("configured template generated a replacement")

    monkeypatch.setattr(launch, "get_template", fake_get)
    monkeypatch.setattr(launch, "ensure_template", fail_generated)
    config = {**_provider().config, "template_id": "template-serve", "container_disk_gb": 80}
    resolved = await launch.ensure_launch_template(
        object(),
        _capability(),
        _provider(config),
        api_key="explicit-key",
        rest_api_url="https://rest.runpod.test/v1",
    )
    assert calls == [
        (
            "template-serve",
            {
                "api_key": "explicit-key",
                "rest_api_url": "https://rest.runpod.test/v1",
            },
        )
    ]
    assert (resolved.template_id, resolved.image_ref, resolved.container_disk_gb) == (
        "template-serve",
        "ghcr.io/example/model-server:test",
        80,
    )


@pytest.mark.anyio
async def test_serverless_template_is_invalid_provider_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(template_id: str) -> templates.Template:
        return templates.Template(
            id=template_id,
            name="wrong-kind",
            imageName="ghcr.io/example/worker:test",
            isServerless=True,
        )

    monkeypatch.setattr(launch, "get_template", fake_get)
    with pytest.raises(launch.InvalidProviderConfig, match="pod template"):
        await launch.ensure_launch_template(
            object(),
            _capability(),
            _provider({**_provider().config, "template_id": "template-serverless"}),
        )


@pytest.mark.anyio
async def test_configured_template_dry_run_makes_no_template_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry-run touched templates")

    monkeypatch.setattr(launch, "get_template", fail)
    monkeypatch.setattr(launch, "ensure_template", fail)
    config = {**_provider().config, "template_id": "template-dryrun"}
    plan = await launch.prepare_lease_launch(
        object(), _capability(), _provider(config), dry_run=True
    )
    assert plan.template.template_id == "template-dryrun"


@pytest.mark.anyio
async def test_image_template_dry_run_uses_cache_or_literal_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_ensure(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("dry-run must not create a template")

    expected_sha = templates.config_sha(
        "ghcr.io/acme/pitwall-worker:qwen3",
        ports="8000/http,22/tcp",
        env_keys=templates.non_secret_env_keys((*templates.TEMPLATE_ENV_KEYS, "VLLM_MODEL")),
        container_disk_gb=80,
        account_digest=templates.template_account_digest("dry-run-key"),
    )

    async def cached(_pool: object, name: str, sha: str) -> str | None:
        assert name == "pitwall-qwen3-h100"
        assert sha == expected_sha
        return "template-cached"

    monkeypatch.setenv("RUNPOD_API_KEY", "dry-run-key")
    monkeypatch.setattr(launch, "ensure_template", fail_ensure)
    monkeypatch.setattr(launch, "_lookup_cached", cached)
    cached_plan = await launch.prepare_lease_launch(
        object(), _capability(), _provider(), dry_run=True
    )
    assert cached_plan.template.template_id == "template-cached"

    async def uncached(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(launch, "_lookup_cached", uncached)
    result = await launch.run_launch(
        pool=object(), capability=_capability(), provider=_provider(), dry_run=True
    )
    assert result["template_id"] == "dry-run"


@pytest.mark.anyio
async def test_run_launch_dry_run_returns_template_without_creating_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        return "template-dryrun"

    async def fail_create_pod(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("dry_run must not create a pod")

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fail_create_pod)

    async def uncached(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(launch, "_lookup_cached", uncached)

    result = await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=_provider(),
        request_id="req_dry",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["pod_id"] is None
    assert result["template_id"] == "dry-run"
    assert result["template_name"] == "pitwall-qwen3-h100"
    assert result["capability"] == "llm.qwen3-32b"
    assert result["provider"] == "qwen3-h100-pod-us-ca"
    assert result["network_volume_id"] == "vol-model-cache"
    assert result["data_center_id"] == "US-CA-2"


@pytest.mark.anyio
async def test_dry_run_result_never_contains_hf_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-redacted"

    monkeypatch.setattr(launch, "ensure_template", template)

    async def uncached(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(launch, "_lookup_cached", uncached)
    result = await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=_provider(),
        extra_env={"HF_TOKEN": "hf-test-dry-run-canary"},
        dry_run=True,
    )
    assert "hf-test-dry-run-canary" not in str(result)
    assert "HF_TOKEN" not in result


@pytest.mark.anyio
async def test_run_launch_admits_budget_then_template_then_sync_pod_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    budget_kwargs: dict[str, Any] = {}

    class FakeBudgetGate:
        async def try_launch(self, **kwargs: Any) -> str:
            calls.append("budget_gate")
            budget_kwargs.update(kwargs)
            return "wkl_lease_order"

    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        assert calls == ["budget_gate"]
        calls.append("templates.ensure_template")
        assert image_ref == "ghcr.io/acme/pitwall-worker:qwen3"
        assert kwargs["template_name"] == "pitwall-qwen3-h100"
        return "template-order"

    def fake_create_pod_with_fallback_sync(**kwargs: Any) -> dict[str, Any]:
        assert calls == ["budget_gate", "templates.ensure_template"]
        calls.append("create_pod_with_fallback_sync")
        assert kwargs["template_id"] == "template-order"
        assert kwargs["image_name"] == "ghcr.io/acme/pitwall-worker:qwen3"
        return {"id": "pod-order", "name": kwargs["name"]}

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(
        pods,
        "create_pod_with_fallback_sync",
        fake_create_pod_with_fallback_sync,
    )

    result = await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=_provider(),
        request_id="req_order",
        budget_gate=FakeBudgetGate(),
    )

    assert calls == [
        "budget_gate",
        "templates.ensure_template",
        "create_pod_with_fallback_sync",
    ]
    assert budget_kwargs == {
        "capability_id": "cap_llm_qwen3",
        "provider_id": "prov_qwen3_h100",
        "estimate_usd": Decimal("14.400000"),
        "workload_type": "inference",
        "idempotency_key": None,
    }
    assert result["workload_id"] == "wkl_lease_order"
    assert result["template_id"] == "template-order"
    assert result["pod_id"] == "pod-order"


@pytest.mark.anyio
async def test_run_launch_persists_ready_pod_readiness_before_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []

    class FakePool:
        def acquire(self) -> object:
            return _ProviderLockOnlyAcquire()

    class FakeBudgetGate:
        async def try_launch(self, **_kwargs: Any) -> str:
            return "wkl_ready"

    class FakeLeaseRepository:
        def __init__(self, pool: object) -> None:
            assert isinstance(pool, FakePool)

        async def update_state(self, lease_id: str, state: str) -> object:
            events.append(("state", state))
            return object()

        async def update_readiness(self, lease_id: str, readiness: object) -> object:
            events.append(("readiness", readiness))
            return object()

        async def mark_ready(self, lease_id: str, *, ready_at: datetime) -> object:
            events.append(("ready_at", ready_at))
            return object()

    async def fake_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        return "template-ready"

    async def fake_create_pod_with_fallback(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["pre_readiness_callback"] is not None
        return {
            "id": "pod-ready",
            "name": kwargs["name"],
            "readiness": {
                "runtime_seen_at": "2026-05-26T14:00:18Z",
                "port_mappings_seen_at": "2026-05-26T14:00:19Z",
                "probe_passed_at": "2026-05-26T14:00:34Z",
                "probe_method": "ssh_localhost",
            },
        }

    monkeypatch.setattr(launch, "LeaseRepository", FakeLeaseRepository)
    monkeypatch.setattr(launch, "enforce_kill_switch_admission", AsyncMock())
    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create_pod_with_fallback)

    result = await launch.run_launch(
        pool=FakePool(),
        capability=_capability(),
        provider=_provider(),
        request_id="req_ready",
        budget_gate=FakeBudgetGate(),
    )

    readiness_event = events[2]
    assert result["pod_id"] == "pod-ready"
    assert [event[1] for event in events if event[0] == "state"] == [
        "waiting_runtime",
        "waiting_probe",
        "active",
    ]
    assert readiness_event[0] == "readiness"
    assert readiness_event[1].has_active_signals
    assert readiness_event[1].probe_method == "ssh_localhost"
    assert events[3][0] == "ready_at"


@pytest.mark.anyio
async def test_run_launch_returns_provider_fallback_signal_for_pre_wait_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminalized: list[dict[str, Any]] = []

    async def fake_terminalize(pool: Any, **kwargs: Any) -> None:
        terminalized.append({"pool": pool, **kwargs})

    class FakeBudgetGate:
        async def try_launch(self, **_kwargs: Any) -> str:
            return "wkl_pre_wait_guard"

    async def fake_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        return "template-prewait"

    async def fake_create_pod_with_fallback(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["max_cost_per_hr"] == 1.25
        raise pods.ProviderFallbackRequested("pod pod-1 allocated zero GPUs")

    provider = _provider(
        {
            "image_ref": "ghcr.io/acme/pitwall-worker:qwen3",
            "template_name": "pitwall-qwen3-h100",
            "gpu_type_priority": ["NVIDIA H100 80GB HBM3"],
            "constraints": {"max_cost_per_hr": "1.25"},
            "cost": {"per_second_active": "0.002"},
        }
    )
    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create_pod_with_fallback)
    monkeypatch.setattr(launch, "_terminalize_failed_launch", fake_terminalize)

    result = await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=provider,
        request_id="req_prewait",
        budget_gate=FakeBudgetGate(),
    )

    assert result["provider_fallback"] is True
    assert result["provider_fallback_reason"] == "pod pod-1 allocated zero GPUs"
    assert result["pod_id"] is None
    assert result["lease_id"] is None
    assert result["workload_id"] == "wkl_pre_wait_guard"
    assert result["provider_id"] == "prov_qwen3_h100"
    assert terminalized[0]["workload_id"] == "wkl_pre_wait_guard"
    assert terminalized[0]["lease_id"].startswith("lease_prov_qwen3_h100_")


@pytest.mark.anyio
async def test_run_launch_terminalizes_admitted_workload_when_prepare_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminalized: list[dict[str, Any]] = []

    class FakeBudgetGate:
        async def try_launch(self, **_kwargs: Any) -> str:
            return "wkl_prepare_failure"

    async def fail_prepare(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("template preparation failed")

    async def fake_terminalize(pool: Any, **kwargs: Any) -> None:
        terminalized.append({"pool": pool, **kwargs})

    monkeypatch.setattr(launch, "prepare_lease_launch", fail_prepare)
    monkeypatch.setattr(launch, "_terminalize_failed_launch", fake_terminalize)

    with pytest.raises(RuntimeError, match="template preparation failed"):
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=_provider(),
            budget_gate=FakeBudgetGate(),
        )

    assert terminalized[0]["workload_id"] == "wkl_prepare_failure"
    assert terminalized[0]["cost_actual_usd"] == Decimal("0")
    assert terminalized[0]["provenance"] == "lease_launch_precreation_failure"


@pytest.mark.anyio
async def test_mark_workload_failed_records_zero_cost_for_precreation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeWorkloadRepository:
        def __init__(self, pool: object) -> None:
            assert isinstance(pool, FakePool)

        async def guarded_transition(
            self,
            workload_id: str,
            from_states: set[str],
            to_state: str,
            *,
            patch: dict[str, Any],
        ) -> object:
            calls.append(
                {
                    "workload_id": workload_id,
                    "from_states": from_states,
                    "to_state": to_state,
                    "patch": patch,
                }
            )
            return object()

    class FakePool:
        def acquire(self) -> object:
            return object()

    monkeypatch.setattr(launch, "WorkloadRepository", FakeWorkloadRepository)
    await launch._mark_workload_failed(
        FakePool(),
        workload_id="wkl_precreate",
        error=RuntimeError("no pod created"),
        cost_actual_usd=Decimal("0"),
        provenance="lease_launch_precreation_failure",
    )

    assert calls[0]["workload_id"] == "wkl_precreate"
    assert calls[0]["from_states"] == {"queued", "running"}
    assert calls[0]["to_state"] == "failed"
    assert calls[0]["patch"]["cost_actual_usd"] == Decimal("0")
    assert calls[0]["patch"]["cost_actual_provenance"] == ("lease_launch_precreation_failure")


@pytest.mark.anyio
async def test_run_launch_cools_provider_after_volume_attach_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched: list[tuple[str, dict[str, Any]]] = []
    now = datetime(2026, 5, 28, 12, 30, tzinfo=UTC)

    class FakePool:
        def acquire(self) -> object:
            raise AssertionError("fake provider repository should not acquire directly")

    class FakeBudgetGate:
        async def try_launch(self, **_kwargs: Any) -> str:
            return "wkl_attach_hang"

    class FakeProviderRepository:
        def __init__(self, pool: object) -> None:
            assert isinstance(pool, FakePool)

        async def patch(self, provider_id: str, **kwargs: Any) -> object:
            patched.append((provider_id, kwargs))
            return object()

    async def fake_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        return "template-attach"

    async def fake_create_pod_with_fallback(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["volume_attach_timeout_s"] == 42.0
        raise pods.ProviderAttachHangRecoveryRequested(
            "pod pod-hung volume attach hang exceeded 42s (uptimeInSeconds=0)",
            pod_id="pod-hung",
            attach_timeout_s=42.0,
        )

    provider = _provider(
        {
            "image_ref": "ghcr.io/acme/pitwall-worker:qwen3",
            "template_name": "pitwall-qwen3-h100",
            "gpu_type_priority": ["NVIDIA H100 80GB HBM3"],
            "volume_id": "vol-model-cache",
            "constraints": {"max_attach_hang_s": "42"},
            "cost": {"per_second_active": "0.002"},
        }
    )
    monkeypatch.setattr(launch, "ProviderRepository", FakeProviderRepository)
    monkeypatch.setattr(launch, "enforce_kill_switch_admission", AsyncMock())
    monkeypatch.setattr(launch, "_utc_now", lambda: now)
    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create_pod_with_fallback)

    result = await launch.run_launch(
        pool=FakePool(),
        capability=_capability(),
        provider=provider,
        request_id="req_attach_hang",
        budget_gate=FakeBudgetGate(),
    )

    cooldown_until = now + launch.ATTACH_HANG_PROVIDER_COOLDOWN
    assert patched == [
        (
            "prov_qwen3_h100",
            {"cooldown_until": cooldown_until},
        )
    ]
    assert result["provider_fallback"] is True
    assert result["provider_fallback_reason"] == (
        "pod pod-hung volume attach hang exceeded 42s (uptimeInSeconds=0)"
    )
    assert result["provider_cooldown_until"] == cooldown_until.isoformat()
    assert result["pod_id"] is None
    assert result["lease_id"] is None


@pytest.mark.anyio
async def test_run_launch_budget_rejection_skips_template_and_sync_pod_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    snapshot = BudgetSnapshot(
        monthly_budget_usd=Decimal("1.000000"),
        per_request_max_usd=Decimal("1.000000"),
        mtd_spend_usd=Decimal("1.000000"),
        estimate_usd=Decimal("0.120000"),
        budget_remaining_usd=Decimal("0"),
    )

    class RejectingBudgetGate:
        async def try_launch(self, **_kwargs: Any) -> str:
            calls.append("budget_gate")
            raise BudgetRejected("monthly_budget", snapshot)

    async def fail_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        calls.append("templates.ensure_template")
        raise AssertionError("template must not be ensured after budget rejection")

    def fail_create_pod_with_fallback_sync(**_kwargs: Any) -> dict[str, Any]:
        calls.append("create_pod_with_fallback_sync")
        raise AssertionError("pod must not be created after budget rejection")

    monkeypatch.setattr(launch, "ensure_template", fail_ensure_template)
    monkeypatch.setattr(
        pods,
        "create_pod_with_fallback_sync",
        fail_create_pod_with_fallback_sync,
    )

    with pytest.raises(BudgetRejected):
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=_provider(),
            budget_gate=RejectingBudgetGate(),
        )

    assert calls == ["budget_gate"]


@pytest.mark.anyio
async def test_run_launch_guardrail_blocks_before_kill_switch_budget_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    kill_switch = AsyncMock()
    budget_gate = AsyncMock()
    ensure = AsyncMock()
    monkeypatch.setattr(launch, "get_pre_spend_inspection_service", lambda: guardrail)
    monkeypatch.setattr(launch, "enforce_kill_switch_admission", kill_switch)
    monkeypatch.setattr(launch, "ensure_template", ensure)

    with pytest.raises(PreSpendPayloadRejected):
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=_provider(),
            payload={"input": "use sk-abcdefghijklmnopqrstuvwxyz123456"},
            budget_gate=budget_gate,
        )

    kill_switch.assert_not_awaited()
    budget_gate.try_launch.assert_not_awaited()
    ensure.assert_not_awaited()
    assert guardrail.status().counters.block == 1


@pytest.mark.anyio
async def test_run_launch_guardrail_blocks_extra_env_before_budget_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    kill_switch = AsyncMock()
    budget_gate = AsyncMock()
    ensure = AsyncMock()
    monkeypatch.setattr(launch, "get_pre_spend_inspection_service", lambda: guardrail)
    monkeypatch.setattr(launch, "enforce_kill_switch_admission", kill_switch)
    monkeypatch.setattr(launch, "ensure_template", ensure)

    with pytest.raises(PreSpendPayloadRejected) as exc_info:
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=_provider(),
            extra_env={"CONTACT": "sk-abcdefghijklmnopqrstuvwxyz123456"},
            budget_gate=budget_gate,
        )

    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in repr(exc_info.value.to_response_body())
    kill_switch.assert_not_awaited()
    budget_gate.try_launch.assert_not_awaited()
    ensure.assert_not_awaited()
    assert guardrail.status().counters.block == 1


@pytest.mark.anyio
async def test_run_launch_guardrail_blocks_provider_egress_config_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    kill_switch = AsyncMock()
    budget_gate = AsyncMock()
    ensure = AsyncMock()
    secret = "opaque-provider-secret-that-has-no-token-prefix"
    monkeypatch.setattr(launch, "get_pre_spend_inspection_service", lambda: guardrail)
    monkeypatch.setattr(launch, "enforce_kill_switch_admission", kill_switch)
    monkeypatch.setattr(launch, "ensure_template", ensure)
    config = {**_provider().config, "nested": {"clientSecret": secret}}

    with pytest.raises(PreSpendPayloadRejected) as exc_info:
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=_provider(config),
            budget_gate=budget_gate,
        )

    assert secret not in repr(exc_info.value.to_response_body())
    kill_switch.assert_not_awaited()
    budget_gate.try_launch.assert_not_awaited()
    ensure.assert_not_awaited()
    assert guardrail.status().counters.block == 1


@pytest.mark.anyio
async def test_already_inspected_serve_path_still_previews_stored_provider_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    kill_switch = AsyncMock()
    budget_gate = AsyncMock()
    ensure = AsyncMock()
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    monkeypatch.setattr(launch, "get_pre_spend_inspection_service", lambda: guardrail)
    monkeypatch.setattr(launch, "enforce_kill_switch_admission", kill_switch)
    monkeypatch.setattr(launch, "ensure_template", ensure)
    config = {**_provider().config, "docker_start_cmd": ["--token", secret]}

    with pytest.raises(PreSpendPayloadRejected) as exc_info:
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=_provider(config),
            budget_gate=budget_gate,
            _pre_spend_inspected=True,
        )

    assert secret not in repr(exc_info.value.to_response_body())
    kill_switch.assert_not_awaited()
    budget_gate.try_launch.assert_not_awaited()
    ensure.assert_not_awaited()
    assert guardrail.status().counters.total == 0


def test_run_launch_guardrail_rejects_redaction_that_would_change_provider_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    monkeypatch.setattr(launch, "get_pre_spend_inspection_service", lambda: guardrail)
    config = {
        **_provider().config,
        "docker_start_cmd": ["--owner", "ada.lovelace@example.com"],
    }

    with pytest.raises(PreSpendPayloadRejected):
        launch._guard_lease_launch_inputs(
            provider=_provider(config),
            payload=None,
        )

    assert guardrail.status().counters.redact == 1


def test_run_launch_dry_run_guardrail_preview_records_no_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    monkeypatch.setattr(launch, "get_pre_spend_inspection_service", lambda: guardrail)

    with pytest.raises(PreSpendPayloadRejected):
        launch._guard_lease_launch_inputs(
            provider=_provider(),
            payload={"input": "sk-abcdefghijklmnopqrstuvwxyz123456"},
            dry_run=True,
        )

    assert guardrail.status().counters.total == 0
    assert guardrail.status().last_decision is None


def test_run_launch_guardrail_preserves_valid_provider_credential_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    monkeypatch.setattr(launch, "get_pre_spend_inspection_service", lambda: guardrail)
    config = {**_provider().config, "apiKeyEnv": "SELF_HOSTED_API_KEY"}
    original = _provider(config)

    guarded_provider, _ = launch._guard_lease_launch_inputs(
        provider=original,
        payload=None,
    )

    assert guarded_provider is original
    assert guarded_provider.config["apiKeyEnv"] == "SELF_HOSTED_API_KEY"
    assert guardrail.status().counters.allow == 1


def test_run_launch_guardrail_redacts_operator_environment_without_mutating_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    monkeypatch.setattr(launch, "get_pre_spend_inspection_service", lambda: guardrail)
    original = _provider(
        {
            **_provider().config,
            "env_vars": {"CONTACT": "ada.lovelace@example.com"},
        }
    )

    guarded_provider, guarded_inputs = launch._guard_lease_launch_inputs(
        provider=original,
        payload=None,
        extra_env={"OWNER": "grace.hopper@example.com"},
    )

    assert guarded_inputs["extra_env"] == {"OWNER": "[REDACTED:email]"}
    assert guarded_provider.config["env_vars"] == {"CONTACT": "[REDACTED:email]"}
    assert original.config["env_vars"] == {"CONTACT": "ada.lovelace@example.com"}
    assert guardrail.status().counters.redact == 1


@pytest.mark.anyio
async def test_run_launch_kill_switch_rejection_precedes_budget_and_pod_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def reject_kill_switch(_pool: Any) -> None:
        calls.append("kill_switch")
        raise KillSwitchEngaged

    class UnexpectedBudgetGate:
        async def try_launch(self, **_kwargs: Any) -> str:
            calls.append("budget_gate")
            raise AssertionError("budget admission must follow the kill-switch check")

    async def fail_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        calls.append("templates.ensure_template")
        raise AssertionError("template must not be ensured while the kill switch is engaged")

    async def fail_create_pod(**_kwargs: Any) -> dict[str, Any]:
        calls.append("create_pod_with_fallback")
        raise AssertionError("pod must not be created while the kill switch is engaged")

    monkeypatch.setattr(launch, "enforce_kill_switch_admission", reject_kill_switch)
    monkeypatch.setattr(launch, "ensure_template", fail_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fail_create_pod)

    with pytest.raises(KillSwitchEngaged):
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=_provider(),
            budget_gate=UnexpectedBudgetGate(),
        )

    assert calls == ["kill_switch"]


@pytest.mark.anyio
@pytest.mark.parametrize("engaged", [False, True])
async def test_kill_switch_admission_reads_account_kill_state(engaged: bool) -> None:
    pool = make_asyncpg_pool(fetchval=engaged)

    if engaged:
        with pytest.raises(KillSwitchEngaged):
            await launch.enforce_kill_switch_admission(pool)
    else:
        await launch.enforce_kill_switch_admission(pool)

    pool.conn.fetchval.assert_awaited_once_with("SELECT EXISTS (SELECT 1 FROM pitwall.kill_log)")


@pytest.mark.anyio
async def test_non_pod_lease_provider_is_rejected() -> None:
    provider = _provider()
    provider.provider_type = ProviderType.SERVERLESS_LB

    with pytest.raises(launch.ProviderNotPodLease):
        await launch.ensure_launch_template(object(), _capability(), provider)


# --- A repeated idempotency key never launches a second pod (Task 7c) -----------------------
# Error classes are read from the module under test: other suites evict and re-import
# pitwall.api modules, so a fresh import could name a different class object.


def _keyed_workload(
    *,
    state: str = "queued",
    digest: str | None = "fp-original",
    workload_id: str = "wkl_keyed",
) -> Any:
    from pitwall.core.models import Workload

    return Workload(
        id=workload_id,
        capability_id="cap_llm_qwen3",
        provider_id="prov_qwen3_h100",
        type="inference",
        state=state,
        idempotency_key="launch-key-1",
        input={launch.LAUNCH_REQUEST_DIGEST_KEY: digest} if digest is not None else None,
        submitted_at=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
    )


def _keyed_lease(state: Any = None) -> Any:
    from pitwall.core.enums import LeaseState
    from tests.leases.test_teardown import _lease

    return _lease(state or LeaseState.ACTIVE).model_copy(
        update={
            "id": "lease_keyed",
            "provider_id": "prov_qwen3_h100",
            "workload_id": "wkl_keyed",
            "runpod_pod_id": "pod-keyed",
        }
    )


class _ReplayGate:
    """A budget gate whose key already names an admitted workload."""

    def __init__(self, workload_id: str = "wkl_keyed") -> None:
        self.workload_id = workload_id
        self.calls: list[dict[str, Any]] = []

    async def try_launch_admission(self, **kwargs: Any) -> Any:
        from pitwall.cost.budget_gate import BudgetAdmission

        self.calls.append(kwargs)
        return BudgetAdmission(workload_id=self.workload_id, is_new=False)


def _install_replay_world(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workload: Any,
    lease: Any,
    by_id: Any = None,
) -> None:
    class Workloads:
        def __init__(self, pool: object) -> None:
            pass

        async def get(self, workload_id: str) -> Any:
            assert workload_id == workload.id
            return workload

    class Leases:
        def __init__(self, pool: object) -> None:
            pass

        async def get_by_workload(self, workload_id: str) -> Any:
            assert workload_id == workload.id
            return lease

        async def get(self, lease_id: str) -> Any:
            return by_id if by_id is not None and by_id.id == lease_id else None

    def no_provider_call(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a key replay must not reach RunPod")

    monkeypatch.setattr(launch, "WorkloadRepository", Workloads)
    monkeypatch.setattr(launch, "LeaseRepository", Leases)
    monkeypatch.setattr(launch, "enforce_kill_switch_admission", AsyncMock())
    monkeypatch.setattr(launch, "ensure_template", no_provider_call)
    monkeypatch.setattr(launch, "create_pod_with_fallback", no_provider_call)
    monkeypatch.setattr(launch, "_create_pod_with_fallback", no_provider_call)
    monkeypatch.setattr(pods, "create_pod_with_fallback_sync", no_provider_call)


async def _keyed_launch(gate: Any, *, fingerprint: str = "fp-original") -> dict[str, Any]:
    return await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=_provider(),
        budget_gate=gate,
        idempotency_key="launch-key-1",
        request_fingerprint=fingerprint,
    )


@pytest.mark.anyio
async def test_repeated_key_returns_the_existing_lease_without_a_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_replay_world(monkeypatch, workload=_keyed_workload(), lease=_keyed_lease())
    gate = _ReplayGate()

    result = await _keyed_launch(gate)

    assert len(gate.calls) == 1
    assert result["replayed"] is True
    assert result["lease_id"] == "lease_keyed"
    assert result["pod_id"] == "pod-keyed"
    assert result["external_resource_id"] == "pod-keyed"
    assert result["workload_id"] == "wkl_keyed"
    assert result["dry_run"] is False


@pytest.mark.anyio
async def test_repeated_key_with_a_different_request_is_idempotency_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_replay_world(monkeypatch, workload=_keyed_workload(), lease=_keyed_lease())

    with pytest.raises(launch.IdempotencyMismatch) as raised:
        await _keyed_launch(_ReplayGate(), fingerprint="fp-other")
    assert raised.value.error_code == "idempotency_mismatch"


@pytest.mark.anyio
async def test_a_digestless_workload_of_this_launch_replays_its_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lease workload admitted before digests falls through to the identity replay."""
    _install_replay_world(
        monkeypatch, workload=_keyed_workload(digest=None, state="running"), lease=_keyed_lease()
    )

    result = await _keyed_launch(_ReplayGate())

    assert result["replayed"] is True and result["lease_id"] == "lease_keyed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "update",
    [
        {"provider_id": "prov_other"},
        {"capability_id": "cap_other"},
        {"type": "async_inference"},
        {"input": {"prompt": "an inference payload under the same key"}},
    ],
)
async def test_a_digestless_workload_of_another_request_is_a_mismatch(
    monkeypatch: pytest.MonkeyPatch, update: dict[str, Any]
) -> None:
    """Another surface's workload, or another launch's, is never replayed as this lease."""
    workload = _keyed_workload(digest=None).model_copy(update=update)
    _install_replay_world(monkeypatch, workload=workload, lease=_keyed_lease())

    with pytest.raises(launch.IdempotencyMismatch):
        await _keyed_launch(_ReplayGate())


@pytest.mark.anyio
async def test_a_provisioned_workload_replays_the_lease_its_result_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lambda and Vast leases carry no workload id; the completed result names the lease."""
    workload = _keyed_workload(state="completed").model_copy(
        update={"type": "vm_lease", "result": {"external_id": "i-1", "lease_id": "lease_keyed"}}
    )
    _install_replay_world(monkeypatch, workload=workload, lease=None, by_id=_keyed_lease())

    result = await _keyed_launch(_ReplayGate())

    assert result["replayed"] is True and result["lease_id"] == "lease_keyed"


@pytest.mark.anyio
async def test_a_precreation_failure_that_made_no_pod_releases_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-capacity error before any pod attempt frees the key, as 7b does for raw pods."""
    released: list[tuple[str, str]] = []

    class Workloads:
        def __init__(self, pool: object) -> None:
            pass

        async def guarded_transition(self, *_args: Any, **_kwargs: Any) -> object:
            return object()

        async def fail_and_release_idempotency_key(
            self, workload_id: str, *, cost_actual_provenance: str, cost_reconciled_at: Any
        ) -> object:
            released.append((workload_id, cost_actual_provenance))
            return object()

    class Pool:
        def acquire(self) -> Any:
            raise AssertionError("the lease was never persisted")

    async def no_lease(*_args: Any, **_kwargs: Any) -> str:
        return "missing"

    monkeypatch.setattr(launch, "WorkloadRepository", Workloads)
    monkeypatch.setattr(launch, "_mark_lease_failed", no_lease)

    await launch._run_launch_runpod_create_failed(
        Pool(),
        _provider(),
        pods.NoCapacityError("no capacity", pod_attempts=0),
        lease_id="lease_never",
        workload_id="wkl_precreate",
        response={},
    )
    assert released == [("wkl_precreate", "lease_launch_precreation_failure")]

    released.clear()
    for exc in (
        pods.NoCapacityError("no capacity", pod_attempts=1),
        pods.NoCapacityError("no capacity", ambiguous_create=True),
        TimeoutError("unknown outcome"),
    ):
        await launch._run_launch_runpod_create_failed(
            Pool(),
            _provider(),
            exc,
            lease_id="lease_never",
            workload_id="wkl_maybe_pod",
            response={},
        )
    assert released == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("raised", "code"),
    [
        ("ProvisionReplayInProgress", "mutation_in_progress"),
        ("ProvisionReplayFailed", "idempotency_conflict"),
        ("ProvisionReplayConflict", "idempotency_mismatch"),
    ],
)
async def test_provider_adapter_replay_refusals_are_typed_launch_errors(
    monkeypatch: pytest.MonkeyPatch, raised: str, code: str
) -> None:
    """A Lambda or Vast adapter's replay refusal is a typed code, never an internal error."""
    from pitwall.core.enums import ProviderAdapterId
    from pitwall.providers import provisioning

    error = getattr(provisioning, raised)("refused", workload_id="wkl_adapter")

    class Adapter:
        async def provision(self, _request: Any) -> Any:
            raise error

    class Registry:
        def lookup_compute(self, _adapter_id: str) -> Adapter:
            return Adapter()

    monkeypatch.setattr(launch, "get_default_registry", Registry)
    monkeypatch.setattr(launch, "enforce_kill_switch_admission", AsyncMock())
    provider = _provider().model_copy(update={"adapter_id": ProviderAdapterId.LAMBDA_CLOUD})

    with pytest.raises(launch.PitwallApiError) as caught:
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=provider,
            budget_gate=object(),
            idempotency_key="launch-key-1",
        )
    assert caught.value.error_code == code


@pytest.mark.anyio
async def test_repeated_key_while_the_first_launch_is_in_flight_is_mutation_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_replay_world(monkeypatch, workload=_keyed_workload(state="queued"), lease=None)

    with pytest.raises(launch.LeaseLaunchInProgress) as raised:
        await _keyed_launch(_ReplayGate())
    assert raised.value.error_code == "mutation_in_progress"
    assert raised.value.status_code == 409
    assert raised.value.to_response_body() == {
        "error": "mutation_in_progress",
        "workload_id": "wkl_keyed",
    }


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["failed", "timed_out", "cancelled", "completed"])
async def test_repeated_key_after_a_launch_that_ended_without_a_lease_is_idempotency_conflict(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """An unknown-outcome or failed first attempt is never re-launched under the same key."""
    _install_replay_world(monkeypatch, workload=_keyed_workload(state=state), lease=None)

    with pytest.raises(launch.IdempotencyConflict) as raised:
        await _keyed_launch(_ReplayGate())
    assert raised.value.error_code == "idempotency_conflict"


@pytest.mark.anyio
async def test_repeated_key_returns_a_terminal_lease_as_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.core.enums import LeaseState

    failed = _keyed_lease(LeaseState.FAILED)
    _install_replay_world(monkeypatch, workload=_keyed_workload(state="failed"), lease=failed)

    result = await _keyed_launch(_ReplayGate())

    assert result["replayed"] is True and result["lease_id"] == "lease_keyed"


@pytest.mark.anyio
async def test_new_keyed_admission_records_the_request_digest_in_its_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from pitwall.cost.budget_gate import BudgetAdmission

    executed: list[tuple[Any, ...]] = []

    class Conn:
        async def execute(self, *args: Any) -> None:
            executed.append(args)

    class Gate:
        async def try_launch_admission(self, **kwargs: Any) -> BudgetAdmission:
            await kwargs["after_new_admission"](Conn(), "wkl_new")
            return BudgetAdmission(workload_id="wkl_new", is_new=True)

    async def fake_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        return "template-new"

    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        return {"id": "pod-new", "name": kwargs["name"]}

    monkeypatch.setattr(launch, "enforce_kill_switch_admission", AsyncMock())
    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create)

    result = await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=_provider(),
        budget_gate=Gate(),
        idempotency_key="launch-key-new",
        request_fingerprint="fp-new",
    )

    assert result["pod_id"] == "pod-new" and result.get("replayed") is not True
    assert len(executed) == 1
    sql, workload_id, document = executed[0]
    assert "UPDATE pitwall.workloads" in sql and "input" in sql
    assert workload_id == "wkl_new"
    assert json.loads(document) == {launch.LAUNCH_REQUEST_DIGEST_KEY: "fp-new"}


@pytest.mark.anyio
async def test_unkeyed_launch_records_no_digest_and_keeps_the_legacy_admission_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class LegacyGate:
        async def try_launch(self, **kwargs: Any) -> str:
            seen.update(kwargs)
            return "wkl_legacy"

    async def fake_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        return "template-legacy"

    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        return {"id": "pod-legacy", "name": kwargs["name"]}

    monkeypatch.setattr(launch, "enforce_kill_switch_admission", AsyncMock())
    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create)

    result = await launch.run_launch(
        pool=object(), capability=_capability(), provider=_provider(), budget_gate=LegacyGate()
    )

    assert result["workload_id"] == "wkl_legacy" and result["pod_id"] == "pod-legacy"
    assert "after_new_admission" not in seen


def test_default_launch_fingerprint_covers_what_gets_launched() -> None:
    """Without a caller digest, the provider's launch config and the payload decide the key."""
    base = launch.default_launch_fingerprint(_capability(), _provider(), payload={}, extra_env=None)
    other_model = _provider({**_provider().config, "env_vars": {"VLLM_MODEL": "Qwen/Qwen3-8B"}})

    assert base == launch.default_launch_fingerprint(
        _capability(), _provider(), payload={}, extra_env=None
    )
    assert base != launch.default_launch_fingerprint(
        _capability(), other_model, payload={}, extra_env=None
    )
    assert base != launch.default_launch_fingerprint(
        _capability(), _provider(), payload={"gpu_count": 2}, extra_env=None
    )


def test_default_launch_fingerprint_keys_launch_credentials_without_storing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "rk-test-key")
    one = launch.default_launch_fingerprint(
        _capability(), _provider(), payload={}, extra_env={"HF_TOKEN": "hf_first"}
    )
    two = launch.default_launch_fingerprint(
        _capability(), _provider(), payload={}, extra_env={"HF_TOKEN": "hf_second"}
    )
    assert one != two
    monkeypatch.setenv("RUNPOD_API_KEY", "rk-rotated")
    assert one != launch.default_launch_fingerprint(
        _capability(), _provider(), payload={}, extra_env={"HF_TOKEN": "hf_first"}
    )


def test_routed_lease_fingerprint_is_the_callers_request() -> None:
    one = launch.routed_lease_fingerprint("cap_a", None)
    assert one == launch.routed_lease_fingerprint("cap_a", None)
    assert one != launch.routed_lease_fingerprint("cap_b", None)
    assert one != launch.routed_lease_fingerprint("cap_a", "prov_pinned")


class _AdmitOnlyBudgetGate:
    async def try_launch(self, **kwargs: Any) -> str:
        return "wkl_credential"


def _second_account_provider() -> Provider:
    return _provider().model_copy(update={"credential_ref": "SECOND_RUNPOD_KEY"})


@pytest.mark.anyio
async def test_run_launch_without_a_key_creates_with_the_providers_own_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "ambient-key")
    monkeypatch.setenv("SECOND_RUNPOD_KEY", "second-account-key")
    seen: list[dict[str, Any]] = []
    template_keys: list[Any] = []

    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        template_keys.append(kwargs.get("api_key"))
        return "template-credential"

    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {"id": "pod-credential", "name": kwargs["name"]}

    async def fake_create_ambient(**kwargs: Any) -> dict[str, Any]:
        seen.append({**kwargs, "api_key": "ambient"})
        return {"id": "pod-credential", "name": kwargs["name"]}

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "_create_pod_with_fallback", fake_create)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create_ambient)
    monkeypatch.setattr(launch, "_persist_ready_lease", AsyncMock())
    monkeypatch.setattr(launch, "arm_serve_provider", AsyncMock())

    await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=_second_account_provider(),
        budget_gate=_AdmitOnlyBudgetGate(),
    )

    assert [call["api_key"] for call in seen] == ["second-account-key"]
    assert template_keys == ["second-account-key"]


@pytest.mark.anyio
async def test_run_launch_explicit_key_overrides_the_providers_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECOND_RUNPOD_KEY", "second-account-key")
    seen: list[dict[str, Any]] = []

    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-credential"

    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {"id": "pod-credential", "name": kwargs["name"]}

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "_create_pod_with_fallback", fake_create)
    monkeypatch.setattr(launch, "_persist_ready_lease", AsyncMock())
    monkeypatch.setattr(launch, "arm_serve_provider", AsyncMock())

    await launch.run_launch(
        pool=object(),
        capability=_capability(),
        provider=_second_account_provider(),
        budget_gate=_AdmitOnlyBudgetGate(),
        api_key="explicit-key",
    )

    assert [call["api_key"] for call in seen] == ["explicit-key"]


@pytest.mark.anyio
async def test_launch_failing_after_create_terminates_with_the_creates_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "ambient-key")
    monkeypatch.setenv("SECOND_RUNPOD_KEY", "second-account-key")
    terminated: list[tuple[str, str | None]] = []

    async def fake_ensure_template(pool: object, image_ref: str, **kwargs: Any) -> str:
        return "template-credential"

    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        return {"id": "pod-credential", "name": kwargs["name"]}

    def keyed_terminate(
        pod_id: str, *, api_key: str | None = None, rest_api_url: str | None = None
    ) -> None:
        terminated.append((pod_id, api_key))

    def ambient_terminate(pod_id: str) -> None:
        terminated.append((pod_id, "ambient"))

    monkeypatch.setattr(launch, "ensure_template", fake_ensure_template)
    monkeypatch.setattr(launch, "_create_pod_with_fallback", fake_create)
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create)
    monkeypatch.setattr(launch, "_persist_ready_lease", AsyncMock(side_effect=RuntimeError("db")))
    monkeypatch.setattr(launch, "_mark_lease_failed", AsyncMock())
    monkeypatch.setattr(pods, "_terminate_pod_sync", keyed_terminate)
    monkeypatch.setattr(pods, "terminate_pod_sync", ambient_terminate)
    monkeypatch.setattr(launch, "terminate_pod_sync", ambient_terminate)

    with pytest.raises(RuntimeError, match="db"):
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=_second_account_provider(),
            budget_gate=_AdmitOnlyBudgetGate(),
        )

    assert terminated == [("pod-credential", "second-account-key")]


@pytest.mark.anyio
async def test_run_launch_with_an_unset_provider_credential_fails_typed_before_any_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SECOND_RUNPOD_KEY", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "ambient-key")

    with pytest.raises(CredentialResolutionError, match="SECOND_RUNPOD_KEY"):
        await launch.run_launch(
            pool=object(),
            capability=_capability(),
            provider=_second_account_provider(),
            budget_gate=_AdmitOnlyBudgetGate(),
        )
