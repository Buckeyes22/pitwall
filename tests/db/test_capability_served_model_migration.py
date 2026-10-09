"""Migration 0022 adds capabilities.served_model_id."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog

pytestmark = pytest.mark.integration


async def test_served_model_migration_adds_nullable_text_column(db_catalog: Catalog) -> None:
    await db_catalog.expect_columns("capabilities", served_model_id="text")
