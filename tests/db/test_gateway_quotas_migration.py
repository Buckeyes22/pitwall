"""Migration 0033: gateway providers are allowed and the quota tables exist."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog

pytestmark = pytest.mark.integration


async def test_0033_widens_the_adapter_check_and_adds_the_quota_tables(db_catalog: Catalog) -> None:
    adapter = await db_catalog.constraint("providers", "providers_adapter_id_check")
    provider_type = await db_catalog.constraint("providers", "providers_provider_type_check")

    assert "openai_gateway" in adapter.literals
    assert "openai_gateway" in provider_type.literals
    assert {
        "provider_quotas",
        "provider_quota_samples",
        "model_id_map",
    } <= await db_catalog.tables()
