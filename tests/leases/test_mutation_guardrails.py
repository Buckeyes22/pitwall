"""Pre-write guardrail coverage for shared lease mutations."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

from pitwall.api.exceptions import PreSpendPayloadRejected
from pitwall.db.repository import LeaseRepository
from pitwall.leases import mutations
from pitwall.security.pre_spend import PreSpendInspectionService

pytestmark = pytest.mark.anyio


async def test_renew_rejects_secret_identifier_before_repository_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-1234567890abcdef1234567890abcdef"
    guardrail = PreSpendInspectionService()
    repo = AsyncMock()
    monkeypatch.setattr(mutations, "get_pre_spend_inspection_service", lambda: guardrail)

    with pytest.raises(PreSpendPayloadRejected) as exc_info:
        await mutations.renew_lease(
            cast(LeaseRepository, repo),
            secret,
            extends_minutes=60,
            actor="rest:lease",
        )

    assert secret not in repr(exc_info.value.to_response_body())
    repo.renew.assert_not_awaited()
    assert guardrail.status().counters.block == 1


async def test_settings_patch_rejects_secret_idempotency_key_before_repository_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-1234567890abcdef1234567890abcdef"
    guardrail = PreSpendInspectionService()
    repo = AsyncMock()
    monkeypatch.setattr(mutations, "get_pre_spend_inspection_service", lambda: guardrail)

    with pytest.raises(PreSpendPayloadRejected) as exc_info:
        await mutations.patch_lease_settings(
            cast(LeaseRepository, repo),
            "lease-safe",
            renewal_policy="manual",
            auto_teardown_on_expiry=True,
            actor="rest:lease",
            idempotency_key=secret,
        )

    assert secret not in repr(exc_info.value.to_response_body())
    repo.patch_settings.assert_not_awaited()
    assert guardrail.status().counters.block == 1
