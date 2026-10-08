"""Migration 0037: a submitted_at index serves the budget gate's month-to-date query."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog

pytestmark = pytest.mark.integration


async def test_0037_has_the_plain_index_and_no_partial_one(db_catalog: Catalog) -> None:
    indexes = await db_catalog.indexes("workloads")

    plain = indexes["idx_workloads_submitted_at"]
    assert plain.columns == ("submitted_at",)
    assert plain.predicate is None
    assert not plain.unique
    assert "idx_workloads_month_spend" not in indexes
