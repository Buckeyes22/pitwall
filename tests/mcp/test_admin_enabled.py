"""The update tools apply ``enabled`` through the repositories' enable/disable methods."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.mcp.tools.admin import pitwall_update_capability, pitwall_update_provider
from tests._fake_transaction_pool import FakePool

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _cap(*, enabled: bool) -> Capability:
    return Capability(
        id="cap_1",
        name="embedding.test",
        version="1.0.0",
        class_=CapabilityClass.EMBEDDING,
        cost_mode=CostMode.PER_REQUEST,
        enabled=enabled,
        description="d",
        input_schema={},
        output_schema={},
        hints_supported=[],
        source=CapabilitySource.API,
        created_at=NOW,
        updated_at=NOW,
    )


def _prov(*, enabled: bool) -> Provider:
    return Provider(
        id="prov_1",
        capability_id="cap_1",
        name="p",
        provider_type=ProviderType.SERVERLESS_LB,
        runpod_endpoint_id="ep_test",
        enabled=enabled,
        health_status="healthy",
        priority=1,
        source=CapabilitySource.API,
        updated_at=NOW,
    )


def _repo(make: Any, *, current: bool, pool: FakePool | None = None) -> MagicMock:
    """A repository double; given a pool, every write also lands on its transaction trail."""
    repo = MagicMock()
    repo.get = AsyncMock(return_value=make(enabled=current))
    repo.patch = AsyncMock(return_value=make(enabled=current))
    repo.enable = AsyncMock(return_value=make(enabled=True))
    repo.disable = AsyncMock(return_value=make(enabled=False))
    if pool is not None:
        for name in ("patch", "enable", "disable"):
            _record(pool.events, getattr(repo, name), name)
    return repo


def _record(events: list[str], method: AsyncMock, name: str) -> None:
    returned = method.return_value

    async def run(*args: Any, **kwargs: Any) -> Any:
        events.append(name)
        return returned

    method.side_effect = run


async def _run_capability(
    repo: MagicMock, pool: FakePool | None = None, **kwargs: Any
) -> tuple[dict[str, Any], AsyncMock]:
    with (
        patch("pitwall.mcp.tools.admin.get_pool", AsyncMock(return_value=pool or FakePool())),
        patch("pitwall.mcp.tools.admin.insert_audit", new_callable=AsyncMock) as audit,
        patch("pitwall.mcp.tools.admin.CapabilityRepository", return_value=repo),
    ):
        result = await pitwall_update_capability(capability_id="cap_1", **kwargs)
    return result, audit


async def _run_provider(
    repo: MagicMock, pool: FakePool | None = None, **kwargs: Any
) -> tuple[dict[str, Any], AsyncMock]:
    with (
        patch("pitwall.mcp.tools.admin.get_pool", AsyncMock(return_value=pool or FakePool())),
        patch("pitwall.mcp.tools.admin.insert_audit", new_callable=AsyncMock) as audit,
        patch("pitwall.mcp.tools.admin.ProviderRepository", return_value=repo),
    ):
        result = await pitwall_update_provider(provider_id="prov_1", **kwargs)
    return result, audit


class TestUpdateCapabilityEnabled:
    async def test_disable_applies_and_audits(self) -> None:
        repo = _repo(_cap, current=True)
        result, audit = await _run_capability(repo, enabled=False)
        repo.disable.assert_awaited_once_with("cap_1", conn=ANY)
        repo.enable.assert_not_called()
        assert result["enabled"] is False
        audit.assert_awaited_once()
        kwargs = audit.call_args.kwargs
        assert kwargs["action"] == "disable"
        assert kwargs["old_value"]["enabled"] is True
        assert kwargs["new_value"]["enabled"] is False

    async def test_enable_applies_after_other_fields(self) -> None:
        repo = _repo(_cap, current=False)
        result, audit = await _run_capability(repo, enabled=True, description="new")
        repo.patch.assert_awaited_once()
        repo.enable.assert_awaited_once_with("cap_1", conn=ANY)
        repo.disable.assert_not_called()
        assert result["enabled"] is True
        assert audit.call_args.kwargs["old_value"]["enabled"] is False
        assert audit.call_args.kwargs["new_value"]["enabled"] is True

    async def test_same_value_does_nothing(self) -> None:
        repo = _repo(_cap, current=True)
        result, audit = await _run_capability(repo, enabled=True)
        repo.enable.assert_not_called()
        repo.disable.assert_not_called()
        assert result["enabled"] is True
        audit.assert_not_awaited()

    async def test_omitted_leaves_state_unchanged(self) -> None:
        repo = _repo(_cap, current=False)
        result, _ = await _run_capability(repo, description="new")
        repo.enable.assert_not_called()
        repo.disable.assert_not_called()
        assert result["enabled"] is False


class TestUpdateProviderEnabled:
    async def test_disable_applies_and_audits(self) -> None:
        repo = _repo(_prov, current=True)
        result, audit = await _run_provider(repo, enabled=False)
        repo.disable.assert_awaited_once_with("prov_1", conn=ANY)
        repo.enable.assert_not_called()
        assert result["enabled"] is False
        audit.assert_awaited_once()
        kwargs = audit.call_args.kwargs
        assert kwargs["old_value"]["enabled"] is True
        assert kwargs["new_value"]["enabled"] is False

    async def test_enable_applies_after_other_fields(self) -> None:
        repo = _repo(_prov, current=False)
        result, audit = await _run_provider(repo, enabled=True, priority=3)
        repo.patch.assert_awaited_once()
        repo.enable.assert_awaited_once_with("prov_1", conn=ANY)
        repo.disable.assert_not_called()
        assert result["enabled"] is True
        assert audit.call_args.kwargs["new_value"]["enabled"] is True

    async def test_same_value_does_nothing(self) -> None:
        repo = _repo(_prov, current=False)
        result, audit = await _run_provider(repo, enabled=False)
        repo.enable.assert_not_called()
        repo.disable.assert_not_called()
        assert result["enabled"] is False
        audit.assert_not_awaited()

    async def test_omitted_leaves_state_unchanged(self) -> None:
        repo = _repo(_prov, current=True)
        result, _ = await _run_provider(repo, priority=2)
        repo.enable.assert_not_called()
        repo.disable.assert_not_called()
        assert result["enabled"] is True


class TestEnabledToggleOrderAndAtomicity:
    """The patch runs first, the toggle second, both in one transaction (G1 6b, 6c)."""

    async def test_provider_patch_precedes_toggle_in_one_transaction(self) -> None:
        pool = FakePool()
        repo = _repo(_prov, current=False, pool=pool)
        await _run_provider(repo, pool, enabled=True, priority=3)
        assert pool.events == ["begin", "patch", "enable", "commit"]

    async def test_capability_patch_precedes_toggle_in_one_transaction(self) -> None:
        pool = FakePool()
        repo = _repo(_cap, current=True, pool=pool)
        await _run_capability(repo, pool, enabled=False, description="new")
        assert pool.events == ["begin", "patch", "disable", "commit"]

    async def test_a_failing_provider_toggle_rolls_the_patch_back(self) -> None:
        pool = FakePool()
        repo = _repo(_prov, current=True, pool=pool)
        repo.disable.side_effect = RuntimeError("toggle failed")
        audit = AsyncMock()
        with (
            patch("pitwall.mcp.tools.admin.get_pool", AsyncMock(return_value=pool)),
            patch("pitwall.mcp.tools.admin.insert_audit", audit),
            patch("pitwall.mcp.tools.admin.ProviderRepository", return_value=repo),
            pytest.raises(RuntimeError),
        ):
            await pitwall_update_provider(provider_id="prov_1", priority=9, enabled=False)
        assert pool.events == ["begin", "patch", "rollback"]
        audit.assert_not_awaited()

    async def test_a_failing_capability_toggle_rolls_the_patch_back(self) -> None:
        pool = FakePool()
        repo = _repo(_cap, current=False, pool=pool)
        repo.enable.side_effect = RuntimeError("toggle failed")
        audit = AsyncMock()
        with (
            patch("pitwall.mcp.tools.admin.get_pool", AsyncMock(return_value=pool)),
            patch("pitwall.mcp.tools.admin.insert_audit", audit),
            patch("pitwall.mcp.tools.admin.CapabilityRepository", return_value=repo),
            pytest.raises(RuntimeError),
        ):
            await pitwall_update_capability(capability_id="cap_1", description="n", enabled=True)
        assert pool.events == ["begin", "patch", "rollback"]
        audit.assert_not_awaited()

    async def test_a_provider_toggle_that_finds_no_row_is_not_found(self) -> None:
        # Resolved at run time: other suites reload pitwall.api, so an import-time class goes stale.
        from pitwall.api.exceptions import ProviderNotFound

        pool = FakePool()
        repo = _repo(_prov, current=True)
        repo.disable.return_value = None
        audit = AsyncMock()
        with (
            patch("pitwall.mcp.tools.admin.get_pool", AsyncMock(return_value=pool)),
            patch("pitwall.mcp.tools.admin.insert_audit", audit),
            patch("pitwall.mcp.tools.admin.ProviderRepository", return_value=repo),
            pytest.raises(ProviderNotFound),
        ):
            await pitwall_update_provider(provider_id="prov_1", enabled=False)
        assert pool.events == ["begin", "rollback"]
        audit.assert_not_awaited()

    async def test_a_capability_toggle_that_finds_no_row_is_not_found(self) -> None:
        from pitwall.api.exceptions import CapabilityNotFound

        pool = FakePool()
        repo = _repo(_cap, current=False)
        repo.enable.return_value = None
        audit = AsyncMock()
        with (
            patch("pitwall.mcp.tools.admin.get_pool", AsyncMock(return_value=pool)),
            patch("pitwall.mcp.tools.admin.insert_audit", audit),
            patch("pitwall.mcp.tools.admin.CapabilityRepository", return_value=repo),
            pytest.raises(CapabilityNotFound),
        ):
            await pitwall_update_capability(capability_id="cap_1", enabled=True)
        assert pool.events == ["begin", "rollback"]
        audit.assert_not_awaited()


class TestEnabledToggleAuditAction:
    """A toggle audits as enable/disable so pitwall_audit_log action filters find it."""

    async def test_provider_enable_audits_as_enable(self) -> None:
        _, audit = await _run_provider(_repo(_prov, current=False), enabled=True)
        assert [c.kwargs["action"] for c in audit.call_args_list] == ["enable"]
        assert audit.call_args.kwargs["old_value"] == {"enabled": False}
        assert audit.call_args.kwargs["new_value"] == {"enabled": True}

    async def test_provider_toggle_with_other_fields_adds_an_update_row(self) -> None:
        _, audit = await _run_provider(_repo(_prov, current=True), enabled=False, priority=2)
        assert [c.kwargs["action"] for c in audit.call_args_list] == ["disable", "update"]

    async def test_provider_patch_without_toggle_audits_as_update(self) -> None:
        _, audit = await _run_provider(_repo(_prov, current=True), priority=2)
        assert [c.kwargs["action"] for c in audit.call_args_list] == ["update"]

    async def test_capability_enable_audits_as_enable(self) -> None:
        _, audit = await _run_capability(_repo(_cap, current=False), enabled=True)
        assert [c.kwargs["action"] for c in audit.call_args_list] == ["enable"]

    async def test_capability_toggle_with_other_fields_adds_an_update_row(self) -> None:
        _, audit = await _run_capability(
            _repo(_cap, current=True), enabled=False, description="new"
        )
        assert [c.kwargs["action"] for c in audit.call_args_list] == ["disable", "update"]

    async def test_provider_null_field_is_not_a_change(self) -> None:
        _, audit = await _run_provider(_repo(_prov, current=True), enabled=False, priority=None)
        assert [c.kwargs["action"] for c in audit.call_args_list] == ["disable"]

    async def test_provider_old_value_is_the_locked_row_not_the_earlier_read(self) -> None:
        # A toggle that lands between the first read and the patch must not be audited as the old
        # value: the patch's own row (held under its lock) is what the toggle replaces.
        repo = _repo(_prov, current=True)
        repo.patch.return_value = _prov(enabled=False)
        repo.enable.return_value = _prov(enabled=True)
        _, audit = await _run_provider(repo, enabled=True)
        assert audit.call_args.kwargs["action"] == "enable"
        assert audit.call_args.kwargs["old_value"] == {"enabled": False}

    async def test_capability_old_value_is_the_locked_row_not_the_earlier_read(self) -> None:
        repo = _repo(_cap, current=True)
        repo.patch.return_value = _cap(enabled=False)
        repo.enable.return_value = _cap(enabled=True)
        _, audit = await _run_capability(repo, enabled=True)
        assert audit.call_args.kwargs["action"] == "enable"
        assert audit.call_args.kwargs["old_value"] == {"enabled": False}
