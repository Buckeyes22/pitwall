"""Real-PostgreSQL evidence for the RunPod control-plane mutation journal.

Drives ``RunPodControlPlaneService`` with a recording backend over a migrated schema, so the
journal's lookup SQL, its ``config_audit`` constraint fit, the advisory lock and its timeout,
and the ``0041`` index are exercised against Postgres rather than a fake.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pitwall.runpod_client.pods import RunPodRestError
from pitwall.runpod_control_plane import (
    PodActionRequest,
    PodCreateRequest,
    RunPodControlPlaneError,
    RunPodControlPlaneService,
    VolumeCreateRequest,
    VolumeGrowRequest,
)
from tests.integration.conftest import requires_pg
from tests.runpod_control_plane.test_service import RecordingBackend

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


def _service(
    backend: RecordingBackend, pool: Any, *, timeout_s: float = 1
) -> RunPodControlPlaneService:
    return RunPodControlPlaneService(
        backend=backend,
        audit_pool=pool,
        environ={"RUNPOD_API_KEY": "integration-journal-key"},
        timeout_s=timeout_s,
    )


async def _states(pool: Any, key: str) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT new_value ->> 'state' AS state
            FROM pitwall.config_audit
            WHERE new_value ->> 'kind' = 'runpod_control_plane_mutation'
              AND new_value ->> 'idempotency_key' = $1
            ORDER BY id
            """,
            key,
        )
    return [row["state"] for row in rows]


def _restart(key: str, resource_id: str = "pod_one") -> PodActionRequest:
    return PodActionRequest(
        intent="apply", idempotency_key=key, resource_id=resource_id, action="restart"
    )


async def test_replay_returns_the_stored_result_without_a_provider_call(pg_pool: Any) -> None:
    backend = RecordingBackend()
    service = _service(backend, pg_pool)
    request = _restart("integration-replay-0001")

    first = await service.action_pod(request)
    second = await service.action_pod(request)

    assert backend.calls == ["pods.restart"]
    assert (first.replayed, second.replayed) == (False, True)
    assert second.model_dump(exclude={"replayed"}) == first.model_dump(exclude={"replayed"})
    assert await _states(pg_pool, request.idempotency_key) == ["started", "completed"]
    async with pg_pool.acquire() as conn:
        audit = await conn.fetchval(
            "SELECT count(*) FROM pitwall.config_audit WHERE entity_type = 'lease' "
            "AND change_reason LIKE '%idempotency_key=integration-replay-0001'"
        )
    assert audit == 1


async def test_a_different_request_with_the_same_key_conflicts(pg_pool: Any) -> None:
    backend = RecordingBackend()
    service = _service(backend, pg_pool)
    await service.action_pod(_restart("integration-conflict-01"))

    with pytest.raises(RunPodControlPlaneError) as excinfo:
        await service.action_pod(_restart("integration-conflict-01", resource_id="pod_two"))

    assert excinfo.value.code == "idempotency_conflict"
    assert backend.calls == ["pods.restart"]


async def test_an_unknown_outcome_is_ambiguous_and_never_reapplied(pg_pool: Any) -> None:
    class TimesOut(RecordingBackend):
        async def action_pod(self, request: PodActionRequest) -> dict[str, Any]:
            self.calls.append(f"pods.{request.action}")
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    backend = TimesOut()
    service = _service(backend, pg_pool, timeout_s=0.2)
    request = _restart("integration-ambiguous-1")

    with pytest.raises(RunPodControlPlaneError) as first:
        await service.action_pod(request)
    with pytest.raises(RunPodControlPlaneError) as retry:
        await service.action_pod(request)

    assert first.value.code == "provider_timeout"
    assert retry.value.code == "mutation_outcome_ambiguous"
    assert backend.calls == ["pods.restart"]
    assert await _states(pg_pool, request.idempotency_key) == ["started"]


async def test_a_definite_failure_releases_the_key(pg_pool: Any) -> None:
    class RejectsOnce(RecordingBackend):
        rejected = False

        async def grow_volume(self, request: VolumeGrowRequest) -> Any:
            if not self.rejected:
                self.rejected = True
                raise RunPodRestError("PATCH", "networkvolumes/vol_one", 400, "bad size")
            return await super().grow_volume(request)

    backend = RejectsOnce()
    service = _service(backend, pg_pool)
    request = VolumeGrowRequest(
        intent="apply", idempotency_key="integration-failed-01", resource_id="vol_one", size_gb=30
    )

    with pytest.raises(RunPodControlPlaneError):
        await service.grow_volume(request)
    result = await service.grow_volume(request)

    assert result.changed and not result.replayed
    assert await _states(pg_pool, request.idempotency_key) == [
        "started",
        "failed",
        "started",
        "completed",
    ]


async def test_a_compensated_key_applies_again(pg_pool: Any) -> None:
    backend = RecordingBackend()
    service = _service(backend, pg_pool)
    request = VolumeCreateRequest(
        intent="apply",
        idempotency_key="integration-compensated",
        name="integration-volume",
        size_gb=20,
        data_center_id="US-KS-1",
    )
    await service.create_volume(request)

    assert await service.release_idempotency_key(request.idempotency_key, reason="deleted")
    again = await service.create_volume(request)

    assert not again.replayed
    assert backend.calls.count("volumes.create") == 2
    assert await _states(pg_pool, request.idempotency_key) == [
        "started",
        "completed",
        "compensated",
        "started",
        "completed",
    ]


async def test_a_waiting_caller_times_out_on_the_key_lock(pg_pool: Any) -> None:
    class SlowCreate(RecordingBackend):
        async def create_pod(self, request: PodCreateRequest) -> dict[str, Any]:
            await asyncio.sleep(1.0)
            return await super().create_pod(request)

    service = _service(SlowCreate(), pg_pool, timeout_s=0.2)
    request = PodCreateRequest(
        intent="apply",
        idempotency_key="integration-lock-wait",
        name="slow-pod",
        image="example/image:1",
        gpu_type_ids=["NVIDIA L4"],
        ttl_minutes=60,
    )

    first, second = await asyncio.gather(
        service.create_pod(request), service.create_pod(request), return_exceptions=True
    )

    outcomes = sorted([first, second], key=lambda value: isinstance(value, Exception))
    assert not isinstance(outcomes[0], Exception) and outcomes[0].changed
    assert isinstance(outcomes[1], RunPodControlPlaneError)
    assert outcomes[1].code == "mutation_in_progress" and outcomes[1].retryable


async def test_the_journal_lookup_can_use_the_idempotency_key_index(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL enable_seqscan = off")
        plan = await conn.fetch(
            """
            EXPLAIN SELECT entity_type, entity_id, action, new_value
            FROM pitwall.config_audit
            WHERE new_value ->> 'kind' = 'runpod_control_plane_mutation'
              AND new_value ->> 'idempotency_key' = 'integration-index-key'
              AND entity_type IN ('lease', 'provider', 'template', 'volume')
            ORDER BY id DESC
            LIMIT 1
            """
        )
    assert "idx_config_audit_mutation_idempotency_key" in "\n".join(row[0] for row in plan)


async def test_key_status_reports_state_resource_and_age(pg_pool: Any) -> None:
    service = _service(RecordingBackend(), pg_pool)
    request = _restart("integration-status-001")
    assert await service.idempotency_key_status(request.idempotency_key) is None

    await service.action_pod(request)
    entry = await service.idempotency_key_status(request.idempotency_key)

    assert entry is not None
    assert (entry.state, entry.operation, entry.resource_id) == (
        "completed",
        "pod.restart",
        "pod_one",
    )
    assert 0 <= entry.age_s < 60
