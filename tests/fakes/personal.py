"""Shared fakes for the personal serve engine: RunPod, routes, clock, and catalogue.

Used by ``tests/personal`` unit tests and the ``tests/release`` personal journeys, so both
exercise the engine against the same provider behaviour.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pitwall.api.exceptions import ServeVerificationFailed
from pitwall.config import PitwallSettings
from pitwall.personal.service import PersonalServeService
from pitwall.personal.state import StateStore
from pitwall.runpod_client.pods import RunPodError
from pitwall.serve import ServePlanResult

NOW = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.UTC)
MODEL = "ornith-ai/Ornith-1.5-35B-A3B-GGUF"
GPU = "NVIDIA GeForce RTX 3090"
SMALL_GPU = "NVIDIA GeForce RTX 3060"
ENDPOINT_KEY = "test-endpoint-key"
PRICE_PER_HOUR = Decimal("0.22")


class FakeRunPod:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.pods: dict[str, dict[str, Any]] = {}
        self.terminated: list[str] = []
        self.terminate_error: Exception | None = None
        self.get_errors = 0

    async def create_pod(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        route = str(kwargs["name"]).removeprefix("pitwall-")
        pod_id = "pod123" if route == "ornith" else f"pod-{route}"
        pod = {"id": pod_id}
        self.pods[pod_id] = pod
        return pod

    async def get_pod(self, pod_id: str) -> dict[str, Any] | None:
        if self.get_errors:
            self.get_errors -= 1
            raise RunPodError(f"get_pod({pod_id}) failed: connection refused")
        return self.pods.get(pod_id)

    async def terminate_pod(self, pod_id: str) -> None:
        self.terminated.append(pod_id)
        if self.terminate_error is not None:
            raise self.terminate_error
        self.pods.pop(pod_id, None)

    async def read_logs(self, pod_id: str, *, max_lines: int) -> str:
        return f"{pod_id}: last {max_lines} lines"

    def vanish(self, pod_id: str) -> None:
        """The provider lost the pod without Pitwall asking (preemption, manual delete)."""
        self.pods.pop(pod_id, None)


class FakeRoutes:
    def __init__(self) -> None:
        self.existing: set[str] = set()
        self.calls: list[tuple[str, ...]] = []
        self.fail_attach = False
        self.fail_probe = False
        self.cli_present = True

    def available(self) -> bool:
        return self.cli_present

    def exists(self, route: str) -> bool:
        return route in self.existing

    def attach(self, route: str, *, base_url: str, model_id: str, key_env: str) -> Any:
        self.calls.append(("attach", route, base_url, model_id, key_env))
        return SimpleNamespace(ok=not self.fail_attach)

    def probe(self, route: str) -> Any:
        self.calls.append(("probe", route))
        return SimpleNamespace(ok=not self.fail_probe)

    def remove(self, route: str) -> Any:
        self.calls.append(("remove", route))
        return SimpleNamespace(ok=True)

    def names(self, action: str) -> list[str]:
        return [call[1] for call in self.calls if call[0] == action]


class FakeClock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> dt.datetime:
        return self.value

    def advance(self, *, minutes: int) -> None:
        self.value += dt.timedelta(minutes=minutes)


class FakeCatalogue:
    def dossier_variant(self, model_id: str, variant_id: str | None) -> Any:
        del model_id, variant_id
        return SimpleNamespace(gated=False, min_cuda="12.8")


def plan_result(*, fit: str = "fits") -> ServePlanResult:
    return ServePlanResult(
        model_id="Ornith-1.5-35B-A3B",
        engine="llama.cpp",
        variant="gguf:Q4_K_M",
        gpu_class=GPU,
        gpu_count=1,
        image="ghcr.io/ggml-org/llama.cpp:server-cuda",
        argv=["--hf-repo", MODEL, "--hf-file", "Ornith-1.5-35B-Q4_K_M.gguf"],
        volume_cache_env={},
        fit=fit,
        startup_timeout_s=240,
        cost_estimate_usd=None,
        price_source="live",
    )


def fake_cuda(offered: tuple[str, ...] | None) -> Callable[..., Any]:
    async def cuda_versions(settings: Any, gpu_class: str) -> tuple[str, ...] | None:
        del settings, gpu_class
        return offered

    return cuda_versions


async def fake_prices(**kwargs: Any) -> Any:
    del kwargs
    return SimpleNamespace(gpu_types=())


def fake_fit(*args: Any, **kwargs: Any) -> list[Any]:
    """RTX 3090 fits at PRICE_PER_HOUR; every other class does not fit."""
    del args, kwargs
    return [SimpleNamespace(gpu_class=GPU, gpu_count=1, price_per_hour=PRICE_PER_HOUR)]


async def fake_verify(
    models_url: str, model_id: str, *, headers: Mapping[str, str] | None = None
) -> list[str]:
    """A key-protected vLLM: /v1/models answers only the pod's endpoint key."""
    assert models_url.endswith("/v1/models")
    if headers != {"Authorization": f"Bearer {ENDPOINT_KEY}"}:
        raise ServeVerificationFailed(model_id, [])
    return [model_id]


def build_service(
    state_dir: Path,
    runpod: FakeRunPod,
    routes: FakeRoutes,
    *,
    clock: FakeClock | None = None,
    **changes: Any,
) -> PersonalServeService:
    """The personal engine over fakes, as the CLI's ``_service_or_exit`` builds it live."""
    values: dict[str, Any] = {
        "store": StateStore(state_dir),
        "settings": PitwallSettings(),
        "catalogue": FakeCatalogue(),
        "runpod": runpod,
        "routes": routes,
        "clock": (clock or FakeClock()).now,
        "prices": fake_prices,
        "fit": fake_fit,
        "verify": fake_verify,
        "endpoint_key": ENDPOINT_KEY,
        "cuda_versions": fake_cuda(("12.4", "12.8", "13.0")),
        "monthly_budget_usd": Decimal("1000"),
    }
    values.update(changes)
    return PersonalServeService(**values)


async def fake_plan(request: Any, **kwargs: Any) -> ServePlanResult:
    """The catalogue planner: RTX 3060 does not fit; everything else fits."""
    del kwargs
    return plan_result(fit="no" if request.gpu_class == SMALL_GPU else "fits")
