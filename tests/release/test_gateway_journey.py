"""J32: the supervised free-tier gateway routes each provider to its own upstream.

Real pieces: the Python gateway (``pitwall gateway serve``), the personal
``GatewaySupervisor`` that launches it, a generated-shape route table, and the broker's
``openai_gateway`` adapter. Fake piece: one loopback OpenAI-compatible upstream.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.personal.gateway import DEFAULT_ARGV, GatewaySupervisor
from pitwall.personal.state import StateStore
from pitwall.providers.gateway import GatewayProvider, QuotaExhausted
from pitwall.providers.interface import (
    CredentialReference,
    InferenceRequest,
    ProviderOperationContext,
)
from tests.hang_guard import HANG_GUARD_SECS

pytestmark = [pytest.mark.release]

_NOW = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Upstream:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return None

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                upstream.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "model": body["model"],
                    }
                )
                if body["model"] == "m-quota":
                    payload = b'{"error":{"message":"rate limited"}}'
                    self.send_response(429)
                    self.send_header("retry-after", "30")
                else:
                    payload = json.dumps(
                        {
                            "id": "cmpl",
                            "object": "chat.completion",
                            "model": body["model"],
                            "choices": [
                                {"index": 0, "message": {"role": "assistant", "content": "ok"}}
                            ],
                            "usage": {
                                "prompt_tokens": 3,
                                "completion_tokens": 1,
                                "total_tokens": 4,
                            },
                        }
                    ).encode()
                    self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def upstream() -> Iterator[_Upstream]:
    fake = _Upstream()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def gateway(tmp_path: Path, upstream: _Upstream) -> Iterator[tuple[int, GatewaySupervisor]]:
    routes = tmp_path / "gateway-routes.json"
    base = f"http://127.0.0.1:{upstream.port}/v1"
    routes.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routes": {
                    "gw-a": {
                        "base_url": base,
                        "model_id": "m-a",
                        "api_key_env": None,
                        "key_required": False,
                    },
                    "gw-q": {
                        "base_url": base,
                        "model_id": "m-quota",
                        "api_key_env": None,
                        "key_required": False,
                    },
                },
            }
        )
    )
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PITWALL_GATEWAY_TOKEN": "journey-fork-token",
        "PITWALL_GATEWAY_ROUTES": str(routes),
    }
    supervisor = GatewaySupervisor(
        StateStore(tmp_path / "state"),
        argv=[*DEFAULT_ARGV[:5], "--port", str(port), "--bind", "127.0.0.1"],
        health_url=f"http://127.0.0.1:{port}/health",
        env=env,
    )
    supervisor.start()
    deadline = time.monotonic() + HANG_GUARD_SECS
    while not supervisor.health():
        assert time.monotonic() < deadline, "gateway never became healthy"
        time.sleep(0.1)
    yield port, supervisor
    supervisor.stop()


def _request(port: int, name: str) -> tuple[GatewayProvider, InferenceRequest]:
    base = f"http://127.0.0.1:{port}/v1"
    record = ProviderRecord(
        id=f"prov_{name}",
        capability_id="cap_coding",
        name=name,
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=50,
        updated_at=_NOW,
        config={
            "cost": {"mode": "zero"},
            "openai_base_url": base,
            "gateway": {
                "base_url": base,
                "model_id": "seed-model-id",
                "catalog": {"free_type": "keyless"},
            },
        },
    )
    capability = Capability(
        id="cap_coding",
        name="coding.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.ZERO,
        source=CapabilitySource.YAML,
        created_at=_NOW,
        updated_at=_NOW,
    )
    provider = GatewayProvider(environ={"PITWALL_GATEWAY_TOKEN": "journey-fork-token"})
    request = InferenceRequest(
        context=ProviderOperationContext(pool=None, now=_NOW),
        capability=capability,
        provider_record=record,
        credentials=CredentialReference("PITWALL_GATEWAY_API_KEY"),
        payload={"messages": [{"role": "user", "content": "hi"}]},
    )
    return provider, request


@pytest.mark.anyio
async def test_j32_routes_reach_their_upstream_and_quota_signals_are_typed(
    gateway: tuple[int, GatewaySupervisor], upstream: _Upstream
) -> None:
    port, supervisor = gateway
    provider, request = _request(port, "gw-a")
    result = await provider.infer(request)
    assert result.prompt_tokens == 3 and result.completion_tokens == 1
    assert upstream.requests[-1] == {"path": "/v1/chat/completions", "auth": None, "model": "m-a"}

    provider, request = _request(port, "gw-q")
    with pytest.raises(QuotaExhausted) as quota:
        await provider.infer(request)
    assert quota.value.reset_at is not None
    assert dt.timedelta(seconds=25) <= quota.value.reset_at - _NOW <= dt.timedelta(seconds=35)

    record = supervisor.current()
    assert record is not None
    supervisor.stop()
    assert supervisor.current() is None
    deadline = time.monotonic() + HANG_GUARD_SECS
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                pass
        except OSError:
            break
        assert time.monotonic() < deadline, "gateway still listening after stop"
        time.sleep(0.1)
