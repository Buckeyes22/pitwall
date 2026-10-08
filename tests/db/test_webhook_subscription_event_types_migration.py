"""Migration 0025: webhook subscriptions may subscribe to lease.expiring; the default is unchanged."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog

pytestmark = pytest.mark.integration


async def test_event_types_default_keeps_completion_and_allow_list_has_expiring(
    db_catalog: Catalog,
) -> None:
    await db_catalog.expect_columns(
        "webhook_subscriptions",
        event_types="text[] not null default ARRAY['workload.completed']::TEXT[]",
    )
    allowed = await db_catalog.constraint(
        "webhook_subscriptions", "webhook_subscription_event_types_allowed"
    )

    assert allowed.kind == "check"
    assert allowed.columns == ("event_types",)
    assert {"workload.completed", "lease.expiring"} <= set(allowed.literals)
