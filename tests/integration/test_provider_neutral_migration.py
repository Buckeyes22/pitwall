from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    LeaseState,
    ProviderAdapterId,
    ProviderType,
    WorkloadState,
)
from pitwall.core.models import Capability, Lease, Provider, Workload
from pitwall.db.repository import (
    CapabilityRepository,
    LeaseRepository,
    ProviderRepository,
    WorkloadRepository,
)
from pitwall.migrations import discover_migrations
from tests.integration.conftest import _MIGRATION_DIR, requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

_MIGRATION_0028 = Path(_MIGRATION_DIR) / "0028_provider_neutral_runtime.sql"


async def test_fresh_schema_has_provider_neutral_columns(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'pitwall'
              AND (table_name, column_name) IN (
                ('providers', 'adapter_id'),
                ('providers', 'credential_ref'),
                ('leases', 'external_resource_id'),
                ('workloads', 'external_job_id')
              )
            """
        )

    assert {(row["table_name"], row["column_name"]) for row in rows} == {
        ("providers", "adapter_id"),
        ("providers", "credential_ref"),
        ("leases", "external_resource_id"),
        ("workloads", "external_job_id"),
    }


async def test_exact_0027_upgrade_backfills_ids_and_preserves_legacy_columns(
    pg_pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = discover_migrations(_MIGRATION_DIR)
    through_0027 = [record for record in records if record.filename < _MIGRATION_0028.name]
    secret = "migration-provider-secret-canary"  # pragma: allowlist secret
    legacy_config = {
        "api_key_env": "CUSTOM_RUNPOD_KEY",  # pragma: allowlist secret
        "clientSecretEnv": "CLIENT_SECRET_REFERENCE",  # pragma: allowlist secret
        "imageRef": "example/model-server:test",
        "apiKey": secret,
        "nested": {"clientSecret": secret, "label": "retained"},
        "items": [
            {"access-key": secret, "region": "US-KS-2"},
            [[{"private.key": secret, "zone": "us-east"}]],
        ],
        "rawSecretRef": secret,
    }
    async with pg_pool.acquire() as conn:
        await conn.execute("DROP SCHEMA IF EXISTS pitwall CASCADE")
        await conn.execute(
            "\n".join(
                (Path(_MIGRATION_DIR) / record.filename).read_text() for record in through_0027
            )
        )
        await conn.execute(
            """
            INSERT INTO pitwall.capabilities
                (id, name, version, class, cost_mode, config, source)
            VALUES ('cap-gpu', 'gpu.lease', '1', 'gpu_lease', 'per_second', '{}'::jsonb, 'api');

            INSERT INTO pitwall.providers
                (id, capability_id, name, provider_type, config, priority, source)
            VALUES
                ('prov-runpod', 'cap-gpu', 'runpod', 'pod_lease', '{}'::jsonb, 0, 'api'),
                ('prov-vast', 'cap-gpu', 'vast', 'pod_lease',
                 '{"adapter_id":"vast"}'::jsonb, 1, 'api'),
                ('prov-together', 'cap-gpu', 'together', 'public_endpoint',
                 '{"backend":"together","api_key_env":"CUSTOM_TOGETHER_KEY"}'::jsonb, -- pragma: allowlist secret
                 2, 'api'),
                ('prov-lambda', 'cap-gpu', 'lambda', 'pod_lease',
                 '{"backend":"lambda_cloud"}'::jsonb, 3, 'api');

            INSERT INTO pitwall.leases
                (id, provider_id, runpod_pod_id, state, created_at, expires_at,
                 renewal_policy)
            VALUES
                ('lease-runpod', 'prov-runpod', 'pod-1', 'creating', now(),
                 now() + interval '1 hour', 'manual'),
                ('lease-vast', 'prov-vast', 'vast-1', 'creating', now(),
                 now() + interval '1 hour', 'manual');

            INSERT INTO pitwall.workloads
                (id, capability_id, provider_id, type, state, runpod_job_id, submitted_at)
            VALUES ('wkl-runpod', 'cap-gpu', 'prov-runpod', 'async_job', 'queued',
                    'job-1', now());
            """
        )
        await conn.execute(
            "UPDATE pitwall.providers SET config = $1::jsonb WHERE id = 'prov-runpod'",
            json.dumps(legacy_config),
        )
        await conn.execute(
            """
            INSERT INTO pitwall.config_audit
                (actor, action, entity_type, entity_id, old_value, new_value, change_reason)
            VALUES
                ('system', 'update', 'provider', 'prov-runpod', $1::jsonb, $2::jsonb,
                 'legacy provider config')
            """,
            json.dumps({"config": legacy_config}),
            json.dumps({"config": legacy_config, "enabled": True}),
        )
        await conn.execute(_MIGRATION_0028.read_text())

        providers = await conn.fetch(
            "SELECT id, adapter_id, credential_ref FROM pitwall.providers ORDER BY id"
        )
        provider_config = await conn.fetchval(
            "SELECT config FROM pitwall.providers WHERE id = 'prov-runpod'"
        )
        audit_row = await conn.fetchrow(
            """
            SELECT old_value, new_value
            FROM pitwall.config_audit
            WHERE entity_type = 'provider' AND entity_id = 'prov-runpod'
            """
        )
        leases = await conn.fetch(
            "SELECT id, external_resource_id, runpod_pod_id FROM pitwall.leases ORDER BY id"
        )
        workload = await conn.fetchrow(
            "SELECT external_job_id, runpod_job_id FROM pitwall.workloads WHERE id = 'wkl-runpod'"
        )

        await conn.execute(
            """
            INSERT INTO pitwall.leases
                (id, provider_id, external_resource_id, runpod_pod_id, state,
                 created_at, expires_at, renewal_policy)
            VALUES
                ('lease-vast-new', 'prov-vast', 'vast-2', NULL, 'creating', now(),
                 now() + interval '1 hour', 'manual'),
                ('lease-runpod-new', 'prov-runpod', 'pod-2', 'pod-2', 'creating', now(),
                 now() + interval '1 hour', 'manual'),
                ('lease-runpod-legacy-new', 'prov-runpod', NULL, 'pod-3', 'creating', now(),
                 now() + interval '1 hour', 'manual');

            INSERT INTO pitwall.workloads
                (id, capability_id, provider_id, type, state, runpod_job_id, submitted_at)
            VALUES ('wkl-runpod-legacy-new', 'cap-gpu', 'prov-runpod', 'async_job',
                    'queued', 'job-2', now());
            """
        )
        new_leases = await conn.fetch(
            """
            SELECT id, external_resource_id, runpod_pod_id
            FROM pitwall.leases
            WHERE id IN ('lease-vast-new', 'lease-runpod-new', 'lease-runpod-legacy-new')
            ORDER BY id
            """
        )
        new_workload = await conn.fetchrow(
            """
            SELECT external_job_id, runpod_job_id
            FROM pitwall.workloads
            WHERE id = 'wkl-runpod-legacy-new'
            """
        )

    assert [tuple(row.values()) for row in providers] == [
        ("prov-lambda", "lambda_cloud", "LAMBDA_CLOUD_API_KEY"),
        ("prov-runpod", "runpod", "CUSTOM_RUNPOD_KEY"),
        ("prov-together", "together", "CUSTOM_TOGETHER_KEY"),
        ("prov-vast", "vast", "VAST_API_KEY"),
    ]
    assert provider_config == {
        "api_key_env": "CUSTOM_RUNPOD_KEY",  # pragma: allowlist secret
        "clientSecretEnv": "CLIENT_SECRET_REFERENCE",  # pragma: allowlist secret
        "imageRef": "example/model-server:test",
        "nested": {"label": "retained"},
        "items": [{"region": "US-KS-2"}, [[{"zone": "us-east"}]]],
    }
    assert audit_row is not None
    assert audit_row["old_value"]["config"]["apiKey"] == "[REDACTED]"
    assert audit_row["new_value"]["config"]["nested"]["clientSecret"] == "[REDACTED]"
    assert secret not in json.dumps(dict(audit_row), default=str, sort_keys=True)
    assert [tuple(row.values()) for row in leases] == [
        ("lease-runpod", "pod-1", "pod-1"),
        ("lease-vast", "vast-1", "vast-1"),
    ]
    assert workload is not None
    assert tuple(workload.values()) == ("job-1", "job-1")
    assert [tuple(row.values()) for row in new_leases] == [
        ("lease-runpod-legacy-new", None, "pod-3"),
        ("lease-runpod-new", "pod-2", "pod-2"),
        ("lease-vast-new", "vast-2", None),
    ]
    assert new_workload is not None
    assert tuple(new_workload.values()) == (None, "job-2")

    provider = await ProviderRepository(pg_pool).get("prov-runpod")
    assert provider is not None
    from pitwall.api.serializers import provider_to_response
    from pitwall.mcp.tools import audit as audit_tools

    async def get_test_pool():
        return pg_pool

    monkeypatch.setattr(audit_tools, "get_pool", get_test_pool)
    serialized_outputs = {
        "rest_provider": provider_to_response(provider),
        "mcp_provider": provider_to_response(provider),
        "mcp_audit": await audit_tools.pitwall_audit_log(
            entity_type="provider",
            entity_id="prov-runpod",
        ),
    }
    assert secret not in json.dumps(serialized_outputs, default=str, sort_keys=True)


async def test_repository_round_trips_generic_and_compatibility_identities(pg_pool) -> None:
    now = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
    capability = Capability(
        id="cap-provider-neutral",
        name="provider.neutral",
        version="1",
        class_=CapabilityClass.CUSTOM,
        cost_mode=CostMode.PER_REQUEST,
        source=CapabilitySource.API,
        created_at=now,
        updated_at=now,
    )
    await CapabilityRepository(pg_pool).create(capability)

    vast = await ProviderRepository(pg_pool).create(
        Provider(
            id="prov-vast-neutral",
            capability_id=capability.id,
            name="vast-neutral",
            adapter_id=ProviderAdapterId.VAST,
            credential_ref="PITWALL_TEST_VAST_REF",
            provider_type=ProviderType.POD_LEASE,
            priority=0,
            updated_at=now,
        )
    )
    together = await ProviderRepository(pg_pool).create(
        Provider(
            id="prov-together-neutral",
            capability_id=capability.id,
            name="together-neutral",
            adapter_id=ProviderAdapterId.TOGETHER,
            credential_ref="PITWALL_TEST_TOGETHER_REF",
            provider_type=ProviderType.PUBLIC_ENDPOINT,
            priority=1,
            updated_at=now,
        )
    )
    assert vast.adapter_id is ProviderAdapterId.VAST
    assert vast.credential_ref == "PITWALL_TEST_VAST_REF"
    assert together.adapter_id is ProviderAdapterId.TOGETHER

    generic_lease = await LeaseRepository(pg_pool).create(
        Lease(
            id="lease-vast-neutral",
            provider_id=vast.id,
            external_resource_id="vast-resource-1",
            state=LeaseState.CREATING,
            created_at=now,
            expires_at=now + dt.timedelta(hours=1),
            renewal_policy="manual",
        )
    )
    assert generic_lease.external_resource_id == "vast-resource-1"
    assert generic_lease.runpod_pod_id is None

    generic_job = await WorkloadRepository(pg_pool).insert(
        Workload(
            id="wkl-together-neutral",
            capability_id=capability.id,
            provider_id=together.id,
            type="inference",
            state=WorkloadState.QUEUED,
            external_job_id="together-job-1",
            submitted_at=now,
        )
    )
    assert generic_job.external_job_id == "together-job-1"
    assert generic_job.runpod_job_id is None

    runpod = await ProviderRepository(pg_pool).create(
        Provider(
            id="prov-runpod-neutral",
            capability_id=capability.id,
            name="runpod-neutral",
            provider_type=ProviderType.POD_LEASE,
            priority=2,
            updated_at=now,
        )
    )
    compatibility_lease = await LeaseRepository(pg_pool).create(
        Lease(
            id="lease-runpod-neutral",
            provider_id=runpod.id,
            runpod_pod_id="pod-compat-1",
            state=LeaseState.CREATING,
            created_at=now,
            expires_at=now + dt.timedelta(hours=1),
            renewal_policy="manual",
        )
    )
    compatibility_job = await WorkloadRepository(pg_pool).insert(
        Workload(
            id="wkl-runpod-neutral",
            capability_id=capability.id,
            provider_id=runpod.id,
            type="async_job",
            state=WorkloadState.QUEUED,
            runpod_job_id="runpod-job-1",
            submitted_at=now,
        )
    )
    assert compatibility_lease.external_resource_id == "pod-compat-1"
    assert compatibility_lease.runpod_pod_id == "pod-compat-1"
    assert compatibility_job.external_job_id == "runpod-job-1"
    assert compatibility_job.runpod_job_id == "runpod-job-1"
