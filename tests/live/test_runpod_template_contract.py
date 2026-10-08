"""Round-trip a template against the real API. Opt-in; never runs in CI."""

from __future__ import annotations

import pytest

from pitwall.runpod_client.templates import delete_template, get_template

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_template_create_get_delete_round_trip(require_live_runpod: None) -> None:
    from pitwall.runpod_client.templates import create_template_rest

    template_id = await create_template_rest(
        name="pitwall-live-template-contract",
        image_name="python:3.13-alpine",
        container_disk_in_gb=5,
        volume_mount_path=None,
        env={},
        is_serverless=False,
        registry_auth_id=None,
        ports=["8000/http"],
    )
    try:
        fetched = await get_template(template_id)
        assert fetched.id == template_id
        assert "8000/http" in (fetched.ports or ""), "ports must survive the round trip"
    finally:
        assert await delete_template(template_id) is True
    assert await delete_template(template_id) is True, "delete must be idempotent"
