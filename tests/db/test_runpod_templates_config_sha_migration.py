"""Migration 0023 upgrades generated-template cache identity."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog, MigrationSandbox

pytestmark = pytest.mark.integration


async def test_config_sha_is_required_and_replaces_the_image_only_cache_key(
    db_catalog: Catalog,
) -> None:
    await db_catalog.expect_columns("runpod_templates", config_sha="text not null")
    constraints = await db_catalog.constraints("runpod_templates")
    unique = constraints["runpod_templates_name_config_sha_key"]

    assert unique.kind == "unique"
    assert unique.columns == ("name", "config_sha")
    assert "runpod_templates_name_image_sha_key" not in constraints
    assert (
        await db_catalog.index("runpod_templates", "idx_runpod_templates_config_sha")
    ).columns == ("config_sha",)


async def test_existing_rows_are_backfilled_before_the_column_becomes_required(
    db_sandbox: MigrationSandbox,
) -> None:
    await db_sandbox.apply_through("0022_capability_served_model")
    connection = db_sandbox.connection
    await connection.execute(
        "INSERT INTO pitwall.runpod_templates (id, runpod_template_id, name, image_sha, image_ref) "
        "VALUES ('tpl_1', 'rp_1', 'worker', 'sha256:abc', 'registry/worker@sha256:abc')"
    )

    await db_sandbox.apply_through("0023_runpod_templates_config_sha")

    # The deterministic legacy identity: two md5 digests of the old image-only key.
    assert await connection.fetchval(
        "SELECT config_sha = md5('legacy:' || image_sha) || md5('legacy-config:' || image_sha) "
        "FROM pitwall.runpod_templates WHERE id = 'tpl_1'"
    )
