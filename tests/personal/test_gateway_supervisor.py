"""GatewaySupervisor: child-process supervision for the loopback gateway."""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pitwall.personal import gateway as gateway_module
from pitwall.personal.gateway import (
    DEFAULT_ARGV,
    DEFAULT_HEALTH_URL,
    GatewayProcess,
    GatewaySupervisor,
)
from pitwall.personal.service import PersonalServeService, ServeSpec
from pitwall.personal.state import StateStore
from pitwall.serve import ServePlanResult

_TOKEN_ENV = {"PITWALL_GATEWAY_TOKEN": "tok"}


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class _FakeRunPod:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def create_pod(self, **kwargs: Any) -> dict[str, Any]:
        self._events.append("pod")
        return {"id": "pod-gw"}

    async def get_pod(self, pod_id: str) -> dict[str, Any] | None:
        return {"id": pod_id}

    async def terminate_pod(self, pod_id: str) -> None:
        return None

    async def read_logs(self, pod_id: str, *, max_lines: int) -> str:
        del pod_id, max_lines
        return ""


class _FakeRoutes:
    def available(self) -> bool:
        return True

    def exists(self, route: str) -> bool:
        del route
        return False

    def attach(self, route: str, *, base_url: str, model_id: str, key_env: str) -> Any:
        del route, base_url, model_id, key_env
        return SimpleNamespace(ok=True)

    def probe(self, route: str) -> Any:
        del route
        return SimpleNamespace(ok=True)

    def remove(self, route: str) -> Any:
        del route
        return SimpleNamespace(ok=True)


class _FakeCatalogue:
    def dossier_variant(self, model_id: str, variant_id: str | None) -> Any:
        del model_id, variant_id
        return SimpleNamespace(gated=False, min_cuda="12.8")


class FakeSupervisor:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.started = 0

    def start(self) -> GatewayProcess:
        self.started += 1
        self._events.append("gateway")
        return GatewayProcess(
            pid=1,
            started_at=dt.datetime(2026, 9, 10, tzinfo=dt.UTC),
            health_url="http://127.0.0.1:20130/health",
            argv=list(DEFAULT_ARGV),
        )


def _supervisor(store: StateStore, **changes: Any) -> GatewaySupervisor:
    values: dict[str, Any] = {"env": dict(_TOKEN_ENV), "port_holder": lambda _bind, _port: None}
    values.update(changes)
    return GatewaySupervisor(store, **values)


def _record(pid: int) -> GatewayProcess:
    return GatewayProcess(
        pid=pid,
        started_at=dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC),
        health_url=DEFAULT_HEALTH_URL,
        argv=list(DEFAULT_ARGV),
    )


def _plan_result() -> ServePlanResult:
    return ServePlanResult(
        model_id="Ornith-1.5-35B-A3B",
        engine="llama.cpp",
        variant="gguf:Q4_K_M",
        gpu_class="NVIDIA GeForce RTX 3090",
        gpu_count=1,
        image="ghcr.io/ggml-org/llama.cpp:server-cuda",
        argv=["--hf-repo", "ornith"],
        volume_cache_env={},
        fit="fits",
        startup_timeout_s=240,
        cost_estimate_usd=None,
        price_source="live",
    )


def test_start_spawns_stock_sidecar_and_persists_owner_only_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    calls: list[dict[str, Any]] = []

    def fake_popen(
        argv: list[str],
        env: Any = None,
        stdin: Any = None,
        start_new_session: bool = False,
    ) -> FakeProcess:
        calls.append(
            {"argv": argv, "env": env, "stdin": stdin, "start_new_session": start_new_session}
        )
        return FakeProcess(pid=4242)

    record = _supervisor(store, popen=fake_popen).start()

    assert record.pid == 4242
    assert record.argv == DEFAULT_ARGV
    assert DEFAULT_ARGV[-4:] == ["--port", "20130", "--bind", "127.0.0.1"]
    assert Path(DEFAULT_ARGV[0]).is_absolute()
    assert record.health_url == DEFAULT_HEALTH_URL
    assert record.started_at.tzinfo is dt.UTC
    [call] = calls
    assert call["argv"] == DEFAULT_ARGV
    assert call["env"]["PITWALL_GATEWAY_TOKEN"] == "tok"
    routes = Path(call["env"]["PITWALL_GATEWAY_ROUTES"])
    assert routes.is_absolute() and routes.is_file()
    assert "tok" not in call["argv"]
    assert call["stdin"] == subprocess.DEVNULL
    assert call["start_new_session"] is True
    state = tmp_path / "gateway.json"
    assert oct(state.stat().st_mode & 0o777) == "0o600"
    assert oct(tmp_path.stat().st_mode & 0o777) == "0o700"
    assert json.loads(state.read_text(encoding="utf-8"))["pid"] == 4242
    assert [p.name for p in tmp_path.iterdir()] == ["gateway.json"]


def test_start_forwards_a_custom_argv(tmp_path: Path) -> None:
    spawned: list[list[str]] = []

    def fake_popen(
        argv: list[str],
        env: Any = None,
        stdin: Any = None,
        start_new_session: bool = False,
    ) -> FakeProcess:
        del env, stdin, start_new_session
        spawned.append(list(argv))
        return FakeProcess(pid=7)

    record = _supervisor(
        StateStore(tmp_path), argv=["node", "shim.js", "--port", "20130"], popen=fake_popen
    ).start()

    assert record.argv == ["node", "shim.js", "--port", "20130"]
    assert spawned == [record.argv]


def test_current_round_trips_the_persisted_record(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    assert store.read_document("gateway.json") is None
    supervisor = _supervisor(store)
    assert supervisor.current() is None

    store.write_document("gateway.json", _record(4242).model_dump(mode="json"))

    assert supervisor.current() == _record(4242)


def test_start_is_idempotent_while_recorded_pid_is_alive(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    record = _record(os.getpid())
    record.identity = gateway_module._process_identity(os.getpid())
    store.write_document("gateway.json", record.model_dump(mode="json"))
    popen = MagicMock(side_effect=AssertionError("supervisor must not spawn a second gateway"))

    assert _supervisor(store, popen=popen).start() == record
    popen.assert_not_called()


def test_start_refuses_to_boot_without_a_token(tmp_path: Path) -> None:
    supervisor = GatewaySupervisor(StateStore(tmp_path), env={}, popen=MagicMock())

    with pytest.raises(RuntimeError, match="PITWALL_GATEWAY_TOKEN"):
        supervisor.start()


def test_stop_kills_the_recorded_process_group_and_clears_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    record = _record(4242).model_dump(mode="json")
    record["identity"] = "boot:100:cmd:abc"
    store.write_document("gateway.json", record)
    monkeypatch.setattr(gateway_module, "_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(gateway_module, "_process_identity", lambda pid: "boot:100:cmd:abc")
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(gateway_module.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))

    _supervisor(store).stop()

    assert signals == [(4242, signal.SIGTERM)]
    assert store.read_document("gateway.json") is None


def test_stop_tolerates_an_already_exited_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    store.write_document("gateway.json", _record(999_999).model_dump(mode="json"))
    monkeypatch.setattr(gateway_module, "_alive", lambda pid: False)
    monkeypatch.setattr(
        gateway_module.os,
        "killpg",
        MagicMock(side_effect=AssertionError("no kill for an already-exited pid")),
    )

    _supervisor(store).stop()

    assert store.read_document("gateway.json") is None


def test_stop_without_any_record_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway_module, "_alive", lambda pid: False)

    _supervisor(StateStore(tmp_path)).stop()


def _health_supervisor(store: StateStore, status: int, seen: dict[str, Any]) -> GatewaySupervisor:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        if status <= 0:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(status)

    return _supervisor(store, transport=httpx.MockTransport(handler))


def test_health_sends_the_bearer_token_to_the_health_url(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    assert _health_supervisor(StateStore(tmp_path), 200, seen).health() is True
    assert seen["url"] == DEFAULT_HEALTH_URL
    assert seen["authorization"] == "Bearer tok"


def test_health_is_false_on_non_200_and_transport_errors(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    assert _health_supervisor(StateStore(tmp_path), 503, seen).health() is False
    assert _health_supervisor(StateStore(tmp_path), 0, seen).health() is False


async def test_personal_serve_starts_the_gateway_before_launching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    async def fake_plan(*args: Any, **kwargs: Any) -> ServePlanResult:
        del args, kwargs
        return _plan_result()

    monkeypatch.setattr("pitwall.personal.service.plan_catalogue_model", fake_plan)

    async def prices(**kwargs: Any) -> Any:
        del kwargs
        return SimpleNamespace(gpu_types=())

    def fit(*args: Any, **kwargs: Any) -> list[Any]:
        del args, kwargs
        return [
            SimpleNamespace(
                gpu_class="NVIDIA GeForce RTX 3090", gpu_count=1, price_per_hour=Decimal("0.22")
            )
        ]

    async def verify(models_url: str, model_id: str, *, headers: Any = None) -> list[str]:
        del models_url, headers
        return [model_id]

    service = PersonalServeService(
        store=StateStore(tmp_path / "pitwall"),
        settings=SimpleNamespace(),
        catalogue=_FakeCatalogue(),
        runpod=_FakeRunPod(events),
        routes=_FakeRoutes(),
        clock=lambda: dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC),
        prices=prices,
        fit=fit,
        verify=verify,
        endpoint_key="k",
        gateway=FakeSupervisor(events),
        cuda_versions=AsyncMock(return_value=("12.8", "13.0")),
        monthly_budget_usd=Decimal("1000"),
    )

    spec = ServeSpec(
        model="ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        gpu_class="NVIDIA GeForce RTX 3090",
        ttl_minutes=5,
        max_usd_per_hour=Decimal("1.00"),
        route="ornith",
    )

    lease = await service.serve(spec)

    assert lease.state == "ready"
    assert events == ["gateway", "pod"]


# R14: stop() only signals the process it started.


def test_stop_refuses_a_reused_pid_whose_identity_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    record = _record(4242).model_dump(mode="json")
    record["identity"] = "boot:100:cmd:abc"
    store.write_document("gateway.json", record)
    monkeypatch.setattr(gateway_module, "_alive", lambda pid: True)
    monkeypatch.setattr(gateway_module, "_process_identity", lambda pid: "boot:999:cmd:zzz")
    monkeypatch.setattr(
        gateway_module.os,
        "killpg",
        MagicMock(side_effect=AssertionError("must not signal a pid we did not start")),
    )

    _supervisor(store).stop()

    assert store.read_document("gateway.json") is None


def test_stop_refuses_when_the_record_carries_no_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    store.write_document("gateway.json", _record(4242).model_dump(mode="json"))
    monkeypatch.setattr(gateway_module, "_alive", lambda pid: True)
    monkeypatch.setattr(
        gateway_module.os,
        "killpg",
        MagicMock(side_effect=AssertionError("an unverifiable pid is never signalled")),
    )

    _supervisor(store).stop()

    assert store.read_document("gateway.json") is None


def test_start_records_the_child_identity_and_signals_only_that_pgid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    monkeypatch.setattr(gateway_module, "_process_identity", lambda pid: f"boot:7:cmd:{pid}")
    record = _supervisor(store, popen=lambda argv, **kw: FakeProcess(pid=4242)).start()
    assert record.identity == "boot:7:cmd:4242"

    monkeypatch.setattr(gateway_module, "_alive", lambda pid: pid == 4242)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(gateway_module.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    monkeypatch.setattr(
        gateway_module.os, "getpgid", MagicMock(side_effect=AssertionError("pgid is the pid"))
    )

    _supervisor(store).stop()

    assert signals == [(4242, signal.SIGTERM)]


def test_start_ignores_a_live_pid_that_is_not_our_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    stale = _record(4242).model_dump(mode="json")
    stale["identity"] = "boot:1:cmd:old"
    store.write_document("gateway.json", stale)
    monkeypatch.setattr(gateway_module, "_alive", lambda pid: True)
    monkeypatch.setattr(gateway_module, "_process_identity", lambda pid: f"boot:2:cmd:{pid}")
    spawned: list[int] = []

    def fake_popen(argv: list[str], **kw: Any) -> FakeProcess:
        spawned.append(1)
        return FakeProcess(pid=5151)

    record = _supervisor(store, popen=fake_popen).start()

    assert spawned == [1]
    assert record.pid == 5151


def test_process_identity_reads_the_live_process_start_and_cmdline() -> None:
    identity = gateway_module._process_identity(os.getpid())
    assert identity is not None and identity.startswith("boot:")
    assert gateway_module._process_identity(os.getpid()) == identity


def test_stop_waits_for_the_process_it_started(tmp_path: Path) -> None:
    """A stopped gateway must be gone when stop() returns, not left running or unreaped."""
    store = StateStore(tmp_path)
    child = subprocess.Popen(  # a real child in its own session, like the gateway
        ["sleep", "30"], stdin=subprocess.DEVNULL, start_new_session=True
    )

    def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        del args, kwargs
        return child

    supervisor = _supervisor(store, popen=popen)
    try:
        supervisor.start()
        supervisor.stop()
        assert child.returncode is not None, "stop() returned before the gateway exited"
    finally:
        if child.returncode is None:  # never leak the child into later tests
            child.kill()
            child.wait()


def test_process_identity_survives_the_child_exec() -> None:
    """The record is written right after spawn, possibly mid-exec; identity must not move."""
    child = subprocess.Popen(
        ["sh", "-c", "sleep 0.3; exec sleep 30"],
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        before = gateway_module._process_identity(child.pid)
        time.sleep(0.8)  # the shell has exec'd into sleep: new command line, same process
        assert before is not None
        assert gateway_module._process_identity(child.pid) == before
    finally:
        child.kill()
        child.wait()
