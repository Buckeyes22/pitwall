"""Isolated REST contract tests; global app registration is serialized separately."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.exceptions import install_api_error_handler
from pitwall.api.routes.runpod_resources import router
from pitwall.runpod_control_plane import (
    PodCreateRequest,
    RunPodControlPlaneService,
    StrictRunPodBackend,
)
from tests.runpod_control_plane.journal_fakes import FakeJournalPool, recording_insert_audit

pytestmark = pytest.mark.anyio


class ApiBackend(StrictRunPodBackend):
    def __init__(self) -> None:
        self.create_calls = 0
        self.fail_create = False

    async def list_pods(self) -> list[dict[str, Any]]:
        return [{"id": "pod_one", "name": "one", "desiredStatus": "RUNNING"}]

    async def create_pod(self, request: PodCreateRequest) -> dict[str, Any]:
        self.create_calls += 1
        if self.fail_create:
            from pitwall.runpod_client.pods import RunPodRestError

            raise RunPodRestError("POST", "pods", 403, "permission denied")
        return {"id": "pod_new", "name": request.name, "desiredStatus": "CREATED"}


def _app(
    backend: ApiBackend,
    monkeypatch: pytest.MonkeyPatch,
    pool: FakeJournalPool | None = None,
) -> FastAPI:
    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", recording_insert_audit([]))
    app = FastAPI()
    app.include_router(router)
    install_api_error_handler(app)
    app.state.runpod_control_plane_service = RunPodControlPlaneService(
        backend=backend,
        audit_pool=pool if pool is not None else FakeJournalPool(),
        actor="rest:admin",
    )
    return app


def test_isolated_router_exposes_the_complete_resource_operation_inventory() -> None:
    operations = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }

    assert len(operations) == 29
    assert ("POST", "/v1/admin/runpod/endpoints") in operations
    assert ("PATCH", "/v1/admin/runpod/endpoints/{resource_id}") in operations
    assert ("POST", "/v1/admin/runpod/registry-auths/{resource_id}/replace") in operations
    assert ("GET", "/v1/admin/runpod/hub/templates/search") in operations
    assert not any(method != "GET" and "/hub/" in path for method, path in operations)


async def test_read_and_zero_write_preview_use_the_shared_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ApiBackend()
    transport = httpx.ASGITransport(app=_app(backend, monkeypatch))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listing = await client.get("/v1/admin/runpod/pods")
        preview = await client.post(
            "/v1/admin/runpod/pods",
            json={
                "intent": "preview",
                "idempotency_key": "rest-preview-0001",
                "name": "preview-pod",
                "image": "example/image:1",
                "gpu_type_ids": ["NVIDIA L4"],
                "ttl_minutes": 60,
            },
        )

    assert listing.status_code == 200
    assert listing.json()[0]["id"] == "pod_one"
    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert backend.create_calls == 0


async def test_mutation_requires_explicit_intent_and_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ApiBackend()
    transport = httpx.ASGITransport(app=_app(backend, monkeypatch))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/admin/runpod/pods",
            json={
                "name": "pod",
                "image": "example/image:1",
                "gpu_type_ids": ["NVIDIA L4"],
                "ttl_minutes": 60,
            },
        )

    assert response.status_code == 422
    assert backend.create_calls == 0


async def test_deprecated_v1_fields_fail_before_provider_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ApiBackend()
    secret = "api-secret-that-must-not-leak"
    transport = httpx.ASGITransport(app=_app(backend, monkeypatch))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/admin/runpod/pods",
            json={
                "intent": "apply",
                "idempotency_key": "rest-v1-field-001",
                "name": "pod",
                "image": "example/image:1",
                "gpuTypeIds": ["NVIDIA L4"],
                "ttl_minutes": 60,
                "env": {"API_KEY": secret},
            },
        )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert secret not in response.text
    assert backend.create_calls == 0


async def test_pre_spend_denial_is_stable_non_disclosing_and_pre_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ApiBackend()
    secret = "sk-1234567890abcdef1234567890abcdef"
    transport = httpx.ASGITransport(app=_app(backend, monkeypatch))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/admin/runpod/pods",
            json={
                "intent": "apply",
                "idempotency_key": "rest-pre-spend-001",
                "name": "pod",
                "image": "example/image:1",
                "gpu_type_ids": ["NVIDIA L4"],
                "ttl_minutes": 60,
                "env": {"NORMAL": secret},
            },
        )

    assert response.status_code == 422
    assert response.json()["error"] == "pre_spend_payload_rejected"
    assert secret not in response.text
    assert backend.create_calls == 0


async def test_provider_permission_failure_maps_to_stable_safe_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ApiBackend()
    backend.fail_create = True
    transport = httpx.ASGITransport(app=_app(backend, monkeypatch))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/admin/runpod/pods",
            json={
                "intent": "apply",
                "idempotency_key": "rest-permission-01",
                "name": "new-pod",
                "image": "example/image:1",
                "gpu_type_ids": ["NVIDIA L4"],
                "ttl_minutes": 60,
            },
        )

    assert response.status_code == 502
    detail = response.json()
    assert detail["error"] == "provider_error"
    assert detail["provider_status"] == 403
    assert "Authorization" not in repr(detail)


async def test_path_and_body_resource_ids_must_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ApiBackend()
    transport = httpx.ASGITransport(app=_app(backend, monkeypatch))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(
            "DELETE",
            "/v1/admin/runpod/pods/pod_one",
            json={
                "intent": "preview",
                "idempotency_key": "rest-mismatch-01",
                "resource_id": "pod_two",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"] == "resource_id_mismatch"


async def test_unsafe_read_identifier_has_a_stable_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ApiBackend()
    transport = httpx.ASGITransport(app=_app(backend, monkeypatch))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/admin/runpod/pods/bad$id")

    assert response.status_code == 422
    detail = response.json()
    assert detail["error"] == "invalid_resource_id"
    assert "bad$id" not in repr(detail)


def _pod_body(key: str, name: str = "new-pod") -> dict[str, Any]:
    return {
        "intent": "apply",
        "idempotency_key": key,
        "name": name,
        "image": "example/image:1",
        "gpu_type_ids": ["NVIDIA L4"],
        "ttl_minutes": 60,
    }


async def test_repeated_apply_replays_and_a_changed_request_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ApiBackend()
    transport = httpx.ASGITransport(app=_app(backend, monkeypatch))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/v1/admin/runpod/pods", json=_pod_body("rest-replay-0001"))
        second = await client.post("/v1/admin/runpod/pods", json=_pod_body("rest-replay-0001"))
        conflict = await client.post(
            "/v1/admin/runpod/pods", json=_pod_body("rest-replay-0001", name="other-pod")
        )

    assert first.status_code == second.status_code == 200
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert second.json()["resource_id"] == first.json()["resource_id"] == "pod_new"
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "idempotency_conflict"
    assert backend.create_calls == 1


async def test_an_unknown_prior_outcome_is_a_409_and_never_reapplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ApiBackend()
    pool = FakeJournalPool()
    pool.fail_states = {"completed"}
    transport = httpx.ASGITransport(app=_app(backend, monkeypatch, pool))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/v1/admin/runpod/pods", json=_pod_body("rest-unknown-001"))
        pool.fail_states = set()
        retry = await client.post("/v1/admin/runpod/pods", json=_pod_body("rest-unknown-001"))

    assert first.status_code == 503
    assert first.json()["error"] == "audit_write_failed"
    assert retry.status_code == 409
    assert retry.json()["error"] == "mutation_outcome_ambiguous"
    assert backend.create_calls == 1


@pytest.mark.parametrize(
    "code", ["idempotency_conflict", "mutation_outcome_ambiguous", "mutation_in_progress"]
)
def test_journal_refusals_are_409(code: str) -> None:
    from pitwall.api.routes.runpod_resources import _status_for_error
    from pitwall.runpod_control_plane import RunPodControlPlaneError

    error = RunPodControlPlaneError(code, "refused", operation="pod.create", resource_type="pod")
    assert _status_for_error(error) == 409
