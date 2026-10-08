"""One serialiser per response: REST and MCP must share the same functions."""

from __future__ import annotations

import ast
import datetime as dt
import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pitwall.api.capability_schemas import CapabilityResponse
from pitwall.api.provider_schemas import ProviderResponse
from pitwall.api.schemas.leases import LeaseResponse
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderType,
)
from pitwall.core.models import Capability, Lease, Provider

_NOW = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
_SRC = Path(__file__).resolve().parents[2] / "src" / "pitwall"

_CAPABILITY = Capability(
    id="cap_serializer",
    name="llm.serializer",
    version="1.0.0",
    class_=CapabilityClass.LLM,
    cost_mode=CostMode.PER_SECOND,
    source=CapabilitySource.API,
    created_at=_NOW,
    updated_at=_NOW,
)
_FAKE_KEY = "sk-live-not-real"  # pragma: allowlist secret
_PROVIDER = Provider(
    id="prov_serializer",
    capability_id="cap_serializer",
    name="prov-serializer",
    provider_type=ProviderType.SERVERLESS_LB,
    priority=1,
    health_status="unknown",
    config={"api_key": _FAKE_KEY},
    updated_at=_NOW,
)
_LEASE = Lease(
    id="lease_serializer",
    provider_id="prov_serializer",
    runpod_pod_id="pod-1",
    state=LeaseState.CREATING,
    created_at=_NOW,
    expires_at=_NOW + dt.timedelta(hours=1),
    renewal_policy=LeaseRenewalPolicy.MANUAL,
)

# name -> (REST module, MCP module, sample model, response schema)
_CASES: dict[str, tuple[str, str, Any, Any]] = {
    "capability_to_response": (
        "pitwall.api.capability_routes",
        "pitwall.mcp.tools.discovery",
        _CAPABILITY,
        CapabilityResponse,
    ),
    "provider_to_response": (
        "pitwall.api.provider_routes",
        "pitwall.mcp.tools.discovery",
        _PROVIDER,
        ProviderResponse,
    ),
    "lease_to_response": (
        "pitwall.api.routes.leases",
        "pitwall.mcp.tools.leases",
        _LEASE,
        LeaseResponse,
    ),
}


@pytest.mark.parametrize("name", sorted(_CASES))
def test_rest_and_mcp_outputs_identical(name: str) -> None:
    rest_module, mcp_module, sample, schema = _CASES[name]
    canonical: Callable[[Any], dict[str, Any]] = getattr(
        importlib.import_module("pitwall.api.serializers"), name
    )
    rest_fn = getattr(importlib.import_module(rest_module), name)
    mcp_fn = getattr(importlib.import_module(mcp_module), name)

    # Other suites evict and re-import pitwall.api, so compare origin, not object identity.
    for fn in (rest_fn, mcp_fn):
        assert (fn.__module__, fn.__qualname__) == ("pitwall.api.serializers", name)
    assert rest_fn(sample) == mcp_fn(sample)
    schema.model_validate(canonical(sample))


def test_provider_config_is_redacted() -> None:
    assert _FAKE_KEY not in str(
        importlib.import_module("pitwall.api.serializers").provider_to_response(_PROVIDER)
    )


def test_no_private_copies() -> None:
    private = {f"_{name}" for name in _CASES}
    public = set(_CASES)
    found: dict[str, list[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name in private or node.name in public
            ):
                found.setdefault(node.name, []).append(str(path.relative_to(_SRC)))
    assert found == {name: ["api/serializers.py"] for name in public}
