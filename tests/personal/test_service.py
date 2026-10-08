"""The personal serve engine owns every paid pod from refusal through cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pitwall.api.exceptions import ServeVerificationFailed
from pitwall.config import PitwallSettings
from pitwall.personal import keys
from pitwall.personal.service import (
    LiveRunPod,
    PersonalServeService,
    RunPodCredentialMissing,
    ServeFailed,
    ServeRefused,
    ServeSpec,
    build_personal_service,
)
from pitwall.personal.state import StateStore
from pitwall.runpod_client.pods import RunPodRestError
from pitwall.runpod_credentials import MISSING_CREDENTIAL_MESSAGE
from pitwall.serve import ServePlanResult
from tests.fakes.personal import ENDPOINT_KEY as _ENDPOINT_KEY
from tests.fakes.personal import GPU as _GPU
from tests.fakes.personal import MODEL as _MODEL
from tests.fakes.personal import FakeCatalogue as _FakeCatalogue
from tests.fakes.personal import FakeClock as _FakeClock
from tests.fakes.personal import FakeRoutes as _FakeRoutes
from tests.fakes.personal import FakeRunPod as _FakeRunPod
from tests.fakes.personal import plan_result as _plan_result


def _spec(**changes: Any) -> ServeSpec:
    values: dict[str, Any] = {
        "model": _MODEL,
        "gpu_class": _GPU,
        "ttl_minutes": 5,
        "max_usd_per_hour": Decimal("1.00"),
        "route": "ornith",
    }
    values.update(changes)
    return ServeSpec(**values)


@pytest.fixture
def runpod() -> _FakeRunPod:
    return _FakeRunPod()


@pytest.fixture
def routes() -> _FakeRoutes:
    return _FakeRoutes()


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "pitwall")


def _fake_cuda(offered: tuple[str, ...] | None) -> Any:
    async def cuda_versions(settings: Any, gpu_class: str) -> tuple[str, ...] | None:
        del settings, gpu_class
        return offered

    return cuda_versions


@pytest.fixture
def service_factory(
    monkeypatch: pytest.MonkeyPatch,
    runpod: _FakeRunPod,
    routes: _FakeRoutes,
    clock: _FakeClock,
    store: StateStore,
) -> Any:
    async def fake_plan(*args: Any, **kwargs: Any) -> ServePlanResult:
        del args, kwargs
        return _plan_result()

    async def prices(**kwargs: Any) -> Any:
        del kwargs
        return SimpleNamespace(gpu_types=())

    def fit(*args: Any, **kwargs: Any) -> list[Any]:
        del args, kwargs
        return [
            SimpleNamespace(
                gpu_class=_GPU,
                gpu_count=1,
                price_per_hour=Decimal("0.22"),
            )
        ]

    async def verify(
        models_url: str, model_id: str, *, headers: Mapping[str, str] | None = None
    ) -> list[str]:
        # The pod's vLLM runs with --api-key: an unauthenticated probe gets 401 forever.
        assert models_url.endswith("/v1/models")
        if headers != {"Authorization": f"Bearer {_ENDPOINT_KEY}"}:
            raise ServeVerificationFailed(model_id, [])
        return [model_id]

    monkeypatch.setattr("pitwall.personal.service.plan_catalogue_model", fake_plan)

    def make(**changes: Any) -> PersonalServeService:
        values: dict[str, Any] = {
            "store": store,
            "settings": PitwallSettings(),
            "catalogue": _FakeCatalogue(),
            "runpod": runpod,
            "routes": routes,
            "clock": clock.now,
            "prices": prices,
            "fit": fit,
            "verify": verify,
            "endpoint_key": _ENDPOINT_KEY,
            "cuda_versions": _fake_cuda(("12.4", "12.8", "13.0")),
            "monthly_budget_usd": Decimal("1000"),
        }
        values.update(changes)
        return PersonalServeService(**values)

    return make


@pytest.fixture
def service(service_factory: Any) -> PersonalServeService:
    return service_factory()


@pytest.fixture
def service_unpriced(service_factory: Any) -> PersonalServeService:
    return service_factory(fit=lambda *args, **kwargs: [])


@pytest.fixture
def service_slow_models(service_factory: Any) -> PersonalServeService:
    async def slow(
        models_url: str, model_id: str, *, headers: Mapping[str, str] | None = None
    ) -> list[str]:
        del models_url, headers
        raise ServeVerificationFailed(model_id, [])

    return service_factory(verify=slow)


@pytest.mark.asyncio
async def test_plan_refuses_over_cap_before_any_write(
    service: PersonalServeService, runpod: _FakeRunPod, routes: _FakeRoutes
) -> None:
    spec = _spec(max_usd_per_hour=Decimal("0.10"))
    with pytest.raises(ServeRefused) as exc:
        await service.plan(spec)
    assert exc.value.code == "price_over_cap"
    assert runpod.created == [] and routes.calls == []


@pytest.mark.asyncio
async def test_plan_refuses_unpriced_without_rate(
    service_unpriced: PersonalServeService,
) -> None:
    with pytest.raises(ServeRefused, match="unpriced"):
        await service_unpriced.plan(_spec())


@pytest.mark.asyncio
async def test_plan_refuses_exact_no_fit_verdict_before_create(
    monkeypatch: pytest.MonkeyPatch,
    service: PersonalServeService,
    runpod: _FakeRunPod,
) -> None:
    async def no_fit(*args: Any, **kwargs: Any) -> ServePlanResult:
        del args, kwargs
        return _plan_result(fit="no")

    monkeypatch.setattr("pitwall.personal.service.plan_catalogue_model", no_fit)
    with pytest.raises(ServeRefused, match="does_not_fit"):
        await service.plan(_spec())
    assert runpod.created == []


@pytest.mark.asyncio
async def test_plan_refuses_existing_route(
    service: PersonalServeService, routes: _FakeRoutes
) -> None:
    routes.existing.add("ornith")
    with pytest.raises(ServeRefused, match="route_exists"):
        await service.plan(_spec())


@pytest.mark.asyncio
async def test_serve_refuses_missing_routing_cli_before_any_pod(
    service: PersonalServeService, runpod: _FakeRunPod, routes: _FakeRoutes
) -> None:
    routes.cli_present = False
    with pytest.raises(ServeRefused) as exc:
        await service.serve(_spec())
    assert exc.value.code == "routing_cli_missing"
    assert runpod.created == [] and routes.calls == []


@pytest.mark.asyncio
async def test_plan_hides_endpoint_key_from_request_preview(
    service: PersonalServeService,
) -> None:
    preview = await service.plan(_spec())
    assert preview.request_preview["env"] == {}
    assert _ENDPOINT_KEY not in repr(preview.request_preview)


@pytest.mark.asyncio
async def test_serve_writes_launching_record_then_ready(
    service: PersonalServeService,
    runpod: _FakeRunPod,
    routes: _FakeRoutes,
    store: StateStore,
) -> None:
    lease = await service.serve(_spec())

    assert runpod.created[0]["docker_entrypoint"] == ["sh", "-c"]
    assert "exec /app/llama-server" in runpod.created[0]["docker_start_cmd"][0]
    assert runpod.created[0]["workload"].ports == "8000/http"
    assert runpod.created[0]["workload"].allowed_cuda_versions == ["12.8", "13.0"]
    assert runpod.created[0]["env"] == {"PITWALL_ENDPOINT_KEY": _ENDPOINT_KEY}
    assert runpod.created[0]["wait_for_readiness"] is False
    assert lease.state == "ready"
    assert lease.endpoint_url == "https://pod123-8000.proxy.runpod.net/v1"
    assert routes.calls[0][:3] == (
        "attach",
        "ornith",
        "https://pod123-8000.proxy.runpod.net/v1",
    )
    assert routes.calls[1] == ("probe", "ornith")
    assert store.get("ornith") == lease


@pytest.mark.asyncio
async def test_readiness_timeout_terminates_and_records_failure(
    service_slow_models: PersonalServeService,
    runpod: _FakeRunPod,
    store: StateStore,
) -> None:
    with pytest.raises(ServeFailed, match="readiness_timeout"):
        await service_slow_models.serve(_spec())
    assert runpod.terminated == ["pod123"]
    lease = store.get("ornith")
    assert lease is not None and lease.state == "failed"


@pytest.mark.asyncio
async def test_progress_failure_after_create_terminates_and_records_failure(
    service: PersonalServeService,
    runpod: _FakeRunPod,
    store: StateStore,
) -> None:
    def progress(message: str) -> None:
        if message == "waiting for the model to answer":
            raise RuntimeError("progress sink failed")

    with pytest.raises(RuntimeError, match="progress sink failed"):
        await service.serve(_spec(), progress=progress)
    assert runpod.terminated == ["pod123"]
    lease = store.get("ornith")
    assert lease is not None and lease.state == "failed"


@pytest.mark.asyncio
async def test_a_ttl_within_the_models_startup_budget_is_refused_before_any_pod(
    service: PersonalServeService,
    runpod: _FakeRunPod,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow = _plan_result().model_copy(update={"startup_timeout_s": 1800})

    async def slow_plan(*_args: Any, **_kwargs: Any) -> ServePlanResult:
        return slow

    monkeypatch.setattr("pitwall.personal.service.plan_catalogue_model", slow_plan)
    with pytest.raises(ServeRefused, match="ttl_below_startup"):
        await service.serve(_spec(ttl_minutes=30))
    assert runpod.created == []
    assert (await service.plan(_spec(ttl_minutes=31))).plan.startup_timeout_s == 1800


@pytest.mark.asyncio
async def test_a_failed_pod_create_is_a_refusal_with_nothing_recorded(
    service: PersonalServeService,
    runpod: _FakeRunPod,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def create_fails(**_kwargs: Any) -> dict[str, Any]:
        raise RunPodRestError("POST", "pods", 500, "create pod: Something went wrong")

    monkeypatch.setattr(runpod, "create_pod", create_fails)
    with pytest.raises(ServeRefused) as refused:
        await service.serve(_spec())
    assert refused.value.code == "create_failed"
    assert "HTTP 500" in refused.value.detail
    assert store.get("ornith") is None


@pytest.mark.asyncio
async def test_route_attach_failure_terminates(
    service: PersonalServeService,
    runpod: _FakeRunPod,
    routes: _FakeRoutes,
    store: StateStore,
) -> None:
    routes.fail_attach = True
    with pytest.raises(ServeFailed, match="route_attach_failed"):
        await service.serve(_spec())
    assert runpod.terminated == ["pod123"]
    lease = store.get("ornith")
    assert lease is not None and lease.state == "failed"


@pytest.mark.asyncio
async def test_cancellation_after_the_pod_exists_terminates_it_and_records_interrupted(
    service: PersonalServeService,
    runpod: _FakeRunPod,
    routes: _FakeRoutes,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def cancelled(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(routes, "attach", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await service.serve(_spec())
    assert runpod.terminated == ["pod123"]
    lease = store.get("ornith")
    assert lease is not None and (lease.state, lease.failure) == ("failed", "interrupted")


@pytest.mark.asyncio
async def test_probe_failure_terminates_and_removes_attached_route(
    service: PersonalServeService,
    runpod: _FakeRunPod,
    routes: _FakeRoutes,
) -> None:
    routes.fail_probe = True
    with pytest.raises(ServeFailed, match="route_attach_failed"):
        await service.serve(_spec())
    assert runpod.terminated == ["pod123"]
    assert routes.calls[-1] == ("remove", "ornith")


@pytest.mark.asyncio
async def test_status_marks_missing_pods_gone_and_terminates_late(
    service: PersonalServeService,
    runpod: _FakeRunPod,
    store: StateStore,
    clock: _FakeClock,
) -> None:
    await service.serve(_spec(ttl_minutes=5))
    runpod.pods.clear()
    assert (await service.status())[0].state == "gone"

    await service.serve(_spec(route="late", ttl_minutes=5))
    clock.advance(minutes=6)
    statuses = await service.status()
    assert runpod.terminated[-1] == "pod-late"
    assert next(item for item in statuses if item.route == "late").state == "stopped"


@pytest.mark.asyncio
async def test_status_retries_late_termination_after_provider_error(
    service: PersonalServeService,
    runpod: _FakeRunPod,
    routes: _FakeRoutes,
    clock: _FakeClock,
) -> None:
    await service.serve(_spec(ttl_minutes=5))
    clock.advance(minutes=6)
    runpod.terminate_error = RuntimeError("transient provider failure")

    first = (await service.status())[0]

    assert first.state == "ready"
    assert runpod.terminated == ["pod123"]
    assert ("remove", "ornith") not in routes.calls

    runpod.terminate_error = None
    second = (await service.status())[0]

    assert second.state == "stopped"
    assert second.failure == "terminated_late"
    assert runpod.terminated == ["pod123", "pod123"]
    assert ("remove", "ornith") in routes.calls


@pytest.mark.asyncio
async def test_stop_terminates_removes_route_and_tolerates_gone(
    service: PersonalServeService, runpod: _FakeRunPod, routes: _FakeRoutes
) -> None:
    await service.serve(_spec())
    lease = await service.stop("ornith")
    assert lease.state == "stopped" and runpod.terminated == ["pod123"]
    assert ("remove", "ornith") in routes.calls
    runpod.terminate_error = RuntimeError("not found")
    assert (await service.stop("ornith")).state == "stopped"


@pytest.mark.asyncio
async def test_logs_are_bounded_by_the_requested_line_count(
    service: PersonalServeService,
) -> None:
    await service.serve(_spec())
    assert await service.logs("ornith", max_lines=7) == "pod123: last 7 lines"


def test_build_personal_service_uses_endpoint_and_provider_keys_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("RUNPOD_API_KEY", "provider-secret")
    monkeypatch.setenv("HF_TOKEN", "hf-secret")
    monkeypatch.delenv(keys.ENDPOINT_KEY_ENV, raising=False)
    monkeypatch.setattr(keys.secrets, "token_urlsafe", lambda _length: "endpoint-secret")

    service = build_personal_service(PitwallSettings(pitwall_routing_cli="routing-bin"))

    assert service._endpoint_key == "endpoint-secret"
    assert service._hf_token == "hf-secret"
    assert service._runpod._api_key == "provider-secret"
    assert service._routes._env[keys.ENDPOINT_KEY_ENV] == "endpoint-secret"
    assert keys.read_endpoint_key(service._store.root) == "endpoint-secret"


def test_build_without_a_key_fails_before_writing_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)

    with pytest.raises(RunPodCredentialMissing) as raised:
        build_personal_service(PitwallSettings(pitwall_routing_cli="routing-bin"))

    assert str(raised.value) == MISSING_CREDENTIAL_MESSAGE
    assert not (tmp_path / "state" / "pitwall" / "endpoint.key").exists()


def test_build_uses_the_runpodctl_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    (home / ".runpod").mkdir(parents=True)
    (home / ".runpod" / "config.toml").write_text('apikey = "runpodctl-secret"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)

    service = build_personal_service(PitwallSettings(pitwall_routing_cli="routing-bin"))

    assert service._runpod._api_key == "runpodctl-secret"


@pytest.mark.parametrize(
    "error", [RuntimeError("fixture-token-canary"), OSError("fixture-token-canary")]
)
async def test_gateway_start_failure_is_safe_and_precedes_pod_creation(
    service_factory, runpod, error
):
    from unittest.mock import Mock

    gateway = Mock()
    gateway.start.side_effect = error
    service = service_factory(gateway=gateway)
    with pytest.raises(ServeRefused) as raised:
        await service.serve(_spec())
    assert raised.value.code == "gateway_start_failed"
    assert "fixture-token-canary" not in raised.value.detail
    gateway.start.assert_called_once()
    assert runpod.created == []


async def test_price_refusal_never_starts_gateway(service_factory, runpod):
    from unittest.mock import Mock

    gateway = Mock()
    service = service_factory(gateway=gateway)
    with pytest.raises(ServeRefused) as raised:
        await service.serve(_spec(max_usd_per_hour=Decimal("0.01")))
    assert raised.value.code == "price_over_cap"
    gateway.start.assert_not_called()
    assert runpod.created == []


@pytest.mark.asyncio
async def test_a_cuda_offer_the_create_api_cannot_request_is_refused_before_launch(
    service_factory: Any,
    runpod: _FakeRunPod,
) -> None:
    """RunPod's pod-create allowedCudaVersions stops at 13.0; a 13.1-only class cannot be targeted."""
    service = service_factory(cuda_versions=_fake_cuda(("13.1",)))
    with pytest.raises(ServeRefused) as refused:
        await service.serve(_spec())
    assert refused.value.code == "cuda_unavailable"
    assert runpod.created == []


@pytest.mark.asyncio
async def test_serve_refuses_before_launch_when_no_cuda_version_meets_the_floor(
    service_factory: Any,
    runpod: _FakeRunPod,
) -> None:
    service = service_factory(cuda_versions=_fake_cuda(("12.4",)))
    with pytest.raises(ServeRefused) as refused:
        await service.serve(_spec())
    assert refused.value.code == "cuda_unavailable"
    assert runpod.created == []


def _verify_after(failures: int) -> Any:
    """A verifier that fails its first ``failures`` attempts, as a loading model does."""
    calls = {"n": 0}

    async def verify(
        models_url: str, model_id: str, *, headers: Mapping[str, str] | None = None
    ) -> list[str]:
        del models_url, headers
        calls["n"] += 1
        if calls["n"] <= failures:
            raise ServeVerificationFailed(model_id, [])
        return [model_id]

    return verify


@pytest.mark.asyncio
async def test_the_readiness_probe_presents_the_endpoint_key(
    service_factory: Any, runpod: _FakeRunPod
) -> None:
    """The pod serves with --api-key, so readiness must authenticate like a client does."""
    seen: list[Mapping[str, str] | None] = []

    async def verify(
        models_url: str, model_id: str, *, headers: Mapping[str, str] | None = None
    ) -> list[str]:
        del models_url
        seen.append(headers)
        return [model_id]

    lease = await service_factory(verify=verify).serve(_spec())
    assert lease.state == "ready"
    assert seen == [{"Authorization": f"Bearer {_ENDPOINT_KEY}"}]
    assert runpod.terminated == []


@pytest.mark.asyncio
async def test_a_model_that_loads_longer_than_one_verify_window_becomes_ready(
    service_factory: Any,
    runpod: _FakeRunPod,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live runs took ~4.5 min to answer; one 60 s verify window must not end the wait."""
    slow = _plan_result().model_copy(update={"startup_timeout_s": 1800})

    async def slow_plan(*_args: Any, **_kwargs: Any) -> ServePlanResult:
        return slow

    monkeypatch.setattr("pitwall.personal.service.plan_catalogue_model", slow_plan)
    service = service_factory(verify=_verify_after(4))
    lease = await service.serve(_spec(ttl_minutes=60))
    assert lease.state == "ready"
    assert runpod.terminated == []


@pytest.mark.asyncio
async def test_a_crash_looping_container_is_terminated_before_the_startup_budget(
    service_factory: Any,
    runpod: _FakeRunPod,
    store: StateStore,
) -> None:
    uptimes = iter([40, 5, 35, 4, 30])

    async def get_pod(pod_id: str) -> dict[str, Any] | None:
        pod = runpod.pods.get(pod_id)
        if pod is None:
            return None
        return {**pod, "runtime": {"uptime": next(uptimes)}}

    read_while_alive: list[bool] = []

    async def read_logs(pod_id: str, *, max_lines: int) -> str:
        read_while_alive.append(pod_id in runpod.pods)
        return "loading model\nggml_cuda_init: failed to initialize CUDA: out of memory\n"

    runpod.get_pod = get_pod  # type: ignore[method-assign]  # reason: test replaces the client method with a stub
    runpod.read_logs = read_logs  # type: ignore[method-assign]  # reason: test replaces the client method with a stub
    service = service_factory(verify=_verify_after(10_000))
    with pytest.raises(ServeFailed, match="container_restarting") as failed:
        await service.serve(_spec())
    assert runpod.terminated == ["pod123"]
    lease = store.get("ornith")
    assert lease is not None and lease.state == "failed"
    # The container's own account of the crash, read before the pod is terminated.
    assert read_while_alive == [True]
    assert failed.value.log_tail is not None
    assert "failed to initialize CUDA: out of memory" in failed.value.log_tail


@pytest.mark.asyncio
async def test_status_keeps_a_lease_when_runpod_is_unreachable(
    service: PersonalServeService, runpod: _FakeRunPod
) -> None:
    await service.serve(_spec(ttl_minutes=5))
    runpod.get_errors = 1

    [lease] = await service.status()

    assert lease.state == "ready", "an unreachable RunPod is not a gone pod"
    assert (await service.status())[0].state == "ready"


@pytest.mark.asyncio
async def test_an_unreachable_pod_read_during_startup_keeps_waiting(
    service_factory: Any, runpod: _FakeRunPod
) -> None:
    runpod.get_errors = 2
    service = service_factory(verify=_verify_after(3))

    lease = await service.serve(_spec())

    assert lease.state == "ready"
    assert runpod.terminated == []


@pytest.mark.asyncio
async def test_live_runpod_reads_pods_strictly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []

    async def strict_get(pod_id: str, *, strict_errors: bool = False) -> dict[str, Any]:
        calls.append((pod_id, strict_errors))
        return {"id": pod_id}

    monkeypatch.setattr("pitwall.runpod_client.pods._get_pod", strict_get)

    assert await LiveRunPod(PitwallSettings(), "placeholder").get_pod("pod1") == {"id": "pod1"}
    assert calls == [("pod1", True)]
