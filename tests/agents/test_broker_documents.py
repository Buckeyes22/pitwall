"""Schema-valid broker documents for tests that stub the Pitwall API.

The broker client validates every response with the API's Pydantic models, so a
stub must answer with a document the real route could return.
"""

from __future__ import annotations

from typing import Any

_STAMP = "2026-09-29T00:00:00Z"


def capability_document(**overrides: Any) -> dict[str, Any]:
    """A GET /v1/capabilities/{name} body; overrides replace top-level keys."""
    document: dict[str, Any] = {
        "id": "cap-1",
        "name": "llm.glimmer",
        "version": "1",
        "class": "llm",
        "cost_mode": "zero",
        "created_at": _STAMP,
        "updated_at": _STAMP,
    }
    document.update(overrides)
    return document


def serve_document(**overrides: Any) -> dict[str, Any]:
    """A POST /v1/serve body; overrides replace top-level keys."""
    document: dict[str, Any] = {
        "capability": "llm.glimmer",
        "lease_id": "lease_2",
        "expires_at": None,
        "model_id": "meta-models/Muse-Glimmer-30B",
        "proxy_base_url": "http://pitwall.test/v1/openai/llm.glimmer/v1",
        "engine": "vllm",
        "variant": None,
        "gpu_count": 1,
        "workload_id": None,
        "template_id": None,
        "provider_id": "provider-1",
        "dry_run": False,
        "created": True,
        "cost_estimate_usd": None,
    }
    document.update(overrides)
    return document


def subscription_document(**overrides: Any) -> dict[str, Any]:
    """A POST /v1/webhook-subscriptions body; overrides replace top-level keys."""
    document: dict[str, Any] = {
        "id": "subscription-1",
        "consumer": "pitwall-agents",
        "webhook_url": "http://127.0.0.1:8765",
        "active": True,
        "event_types": ["lease.ready"],
        "created_at": _STAMP,
        "updated_at": _STAMP,
        "signing_secret": "generated-secret",  # pragma: allowlist secret
    }
    document.update(overrides)
    return document
