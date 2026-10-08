"""Personal mode launches the Python gateway and refuses a port somebody else holds."""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from pitwall.personal.gateway import (
    DEFAULT_ARGV,
    GatewayPortInUse,
    GatewaySupervisor,
    port_holder,
)
from pitwall.personal.service import ServeRefused
from pitwall.personal.state import StateStore
from tests.hang_guard import HANG_GUARD_SECS
from tests.personal.test_service import (  # noqa: F401  # reason: pytest fixtures reused as-is
    _spec,
    clock,
    routes,
    runpod,
    service_factory,
    store,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_launches_python_gateway(tmp_path: Path) -> None:
    expected = [
        sys.executable,
        "-m",
        "pitwall",
        "gateway",
        "serve",
        "--port",
        "20130",
        "--bind",
        "127.0.0.1",
    ]
    assert expected == DEFAULT_ARGV
    assert "node" not in DEFAULT_ARGV

    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PITWALL_GATEWAY_TOKEN": "launch-token",  # pragma: allowlist secret
        "PITWALL_GATEWAY_UPSTREAM_URL": "http://127.0.0.1:9/v1",
    }
    supervisor = GatewaySupervisor(
        StateStore(tmp_path),
        argv=[*DEFAULT_ARGV[:5], "--port", str(port), "--bind", "127.0.0.1"],
        health_url=f"http://127.0.0.1:{port}/health",
        env=env,
    )
    supervisor.start()
    try:
        deadline = time.monotonic() + HANG_GUARD_SECS
        while not supervisor.health():
            assert time.monotonic() < deadline, "the Python gateway never became healthy"
            time.sleep(0.1)
    finally:
        supervisor.stop()
    assert supervisor.current() is None


def test_port_in_use_reported(tmp_path: Path) -> None:
    spawned: list[Any] = []

    def popen(*args: Any, **kwargs: Any) -> Any:
        spawned.append((args, kwargs))
        raise AssertionError("must not spawn onto a busy port")

    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = int(holder.getsockname()[1])
        supervisor = GatewaySupervisor(
            StateStore(tmp_path),
            argv=[*DEFAULT_ARGV[:5], "--port", str(port), "--bind", "127.0.0.1"],
            env={"PITWALL_GATEWAY_TOKEN": "tok"},  # pragma: allowlist secret
            popen=popen,
        )
        with pytest.raises(GatewayPortInUse) as raised:
            supervisor.start()
    message = str(raised.value)
    assert str(port) in message
    if sys.platform == "linux":
        assert f"pid {os.getpid()}" in message
    assert spawned == []
    assert supervisor.current() is None


def test_port_holder_is_generic_without_proc(tmp_path: Path) -> None:
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = int(holder.getsockname()[1])
        assert port_holder("127.0.0.1", port, proc=tmp_path / "no-proc") == "another process"
    assert port_holder("127.0.0.1", port) is None


async def test_serve_refusal_names_the_busy_port(
    service_factory: Any,  # noqa: F811  # reason: fixture imported above
    runpod: Any,  # noqa: F811  # reason: fixture imported above
) -> None:
    gateway = Mock()
    gateway.start.side_effect = GatewayPortInUse(
        "gateway port 20130 on 127.0.0.1 is already in use by pid 7"
    )
    service = service_factory(gateway=gateway)
    with pytest.raises(ServeRefused) as raised:
        await service.serve(_spec())
    assert raised.value.code == "gateway_start_failed"
    assert "20130" in raised.value.detail and "pid 7" in raised.value.detail
    assert runpod.created == []
