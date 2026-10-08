"""Provider configuration is inspected before any persistence boundary."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest

from pitwall.cli import endpoints as cli_endpoints
from pitwall.core.enums import ProviderType
from pitwall.core.models import (
    Provider,
    validate_provider_serialized_config,
    validate_provider_storage_payload,
)
from pitwall.db.repository import ProviderRepository
from pitwall.gitops.schema import GitOpsConfigError, load_desired_state
from pitwall.mcp.tools.admin import pitwall_create_provider
from pitwall.seed import apply_seed_data


class _NoAcquirePool:
    def acquire(self) -> None:
        raise AssertionError("database access occurred before provider inspection")


def _provider(config: dict[str, object]) -> Provider:
    return Provider(
        id="prov-storage-guard",
        capability_id="cap-storage-guard",
        name="storage-guard",
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        config=config,
        priority=1,
        updated_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )


def test_shared_provider_config_validator_rejects_pii_without_reflection() -> None:
    canary = "storage.guard@example.com"
    with pytest.raises(ValueError) as rejected:
        validate_provider_serialized_config({"operator_note": canary})

    assert str(rejected.value) == "provider configuration rejected by pre-spend policy"
    assert canary not in str(rejected.value)


def test_shared_provider_payload_validator_covers_identity_metadata() -> None:
    canary = "provider.identity@example.com"
    with pytest.raises(ValueError) as rejected:
        validate_provider_storage_payload({"name": canary, "config": {}})

    assert str(rejected.value) == "provider configuration rejected by pre-spend policy"
    assert canary not in str(rejected.value)


@pytest.mark.anyio
async def test_provider_create_rejects_before_database_access() -> None:
    canary = "sk-provider-storage-canary-1234567890abcdef"
    repository = ProviderRepository(_NoAcquirePool())  # type: ignore[arg-type]  # reason: I/O canary

    with pytest.raises(ValueError) as rejected:
        await repository.create(_provider({"operator_note": canary}))

    assert str(rejected.value) == "provider configuration rejected by pre-spend policy"
    assert canary not in str(rejected.value)


@pytest.mark.anyio
async def test_provider_patch_rejects_before_database_access() -> None:
    canary = "storage.patch@example.com"
    repository = ProviderRepository(_NoAcquirePool())  # type: ignore[arg-type]  # reason: I/O canary

    with pytest.raises(ValueError) as rejected:
        await repository.patch("prov-storage-guard", config={"operator_note": canary})

    assert str(rejected.value) == "provider configuration rejected by pre-spend policy"
    assert canary not in str(rejected.value)


@pytest.mark.anyio
async def test_mcp_provider_create_rejects_before_pool_access() -> None:
    canary = "mcp.provider@example.com"
    get_pool = AsyncMock(side_effect=AssertionError("pool accessed before inspection"))
    with (
        patch("pitwall.mcp.tools.admin.get_pool", get_pool),
        pytest.raises(ValueError) as rejected,
    ):
        await pitwall_create_provider(
            capability_id="cap-storage-guard",
            name=canary,
            provider_type="public_endpoint",
        )

    get_pool.assert_not_awaited()
    assert canary not in str(rejected.value)


@pytest.mark.anyio
async def test_seed_provider_rejects_before_repository_construction() -> None:
    canary = "seed.provider@example.com"
    with pytest.raises(ValueError) as rejected:
        await apply_seed_data(
            {
                "providers": [
                    {
                        "name": canary,
                        "capability": "cap-storage-guard",
                        "provider_type": "public_endpoint",
                    }
                ]
            },
            pool=_NoAcquirePool(),
        )

    assert canary not in str(rejected.value)


@pytest.mark.anyio
async def test_legacy_register_endpoint_rejects_before_pool_access() -> None:
    canary = "cli.provider@example.com"
    args = cli_endpoints._parse_register_endpoint_args(
        [
            "--endpoint-id",
            "endpoint-fixture",
            "--provider-type",
            "public_endpoint",
            "--capability-id",
            "cap-storage-guard",
            "--name",
            canary,
            "--gpu-class",
            "NVIDIA L4",
        ]
    )
    get_pool = AsyncMock(side_effect=AssertionError("pool accessed before inspection"))
    with patch("pitwall.db.get_pool", get_pool), pytest.raises(ValueError) as rejected:
        await cli_endpoints._register_endpoint_async(args)

    get_pool.assert_not_awaited()
    assert canary not in str(rejected.value)


def test_gitops_provider_rejects_without_reflecting_input(tmp_path) -> None:
    canary = "gitops.provider@example.com"
    desired = tmp_path / "providers.yaml"
    desired.write_text(
        "\n".join(
            (
                "apiVersion: pitwall.dev/v1",
                "providers:",
                f"  - name: {canary}",
                "    capability: cap-storage-guard",
                "    provider_type: public_endpoint",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(GitOpsConfigError) as rejected:
        load_desired_state([desired])

    assert "provider configuration rejected by pre-spend policy" in str(rejected.value)
    assert canary not in str(rejected.value)
