"""Migration 0026: leases persist their warning thresholds and a trigger-filled capability name."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog

pytestmark = pytest.mark.integration


async def test_warning_marker_columns_and_capability_name_trigger(db_catalog: Catalog) -> None:
    await db_catalog.expect_columns(
        "leases",
        warning_expires_at="timestamptz",
        warning_thresholds="int4[] not null",
        # Nullable on purpose: an orphan lease (no provider row) keeps a NULL name.
        capability_name="text",
    )
    triggers = await db_catalog.triggers("leases")

    assert "trg_leases_capability_name" in triggers
