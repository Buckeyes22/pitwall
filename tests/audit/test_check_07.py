"""A-49: check 07 verifies webhook idempotency through the receiver's dedupe path."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from pitwall.audit import _probes, checks
from pitwall.audit._runtime_config import RuntimeAuditConfig


class _AssertedConfig:
    """Config that asserts idempotent and fast-200; the check must still verify the code."""

    def webhook_config(self) -> dict[str, Any]:
        return {"idempotent": True, "fast_200": True}


def test_verifies_receiver_dedupe(monkeypatch: pytest.MonkeyPatch) -> None:
    # The receiver validates its environment when imported; the check must not import it.
    sys.modules.pop("pitwall.webhook_receiver", None)

    message = checks.check_07_webhook_idempotent_fast200(RuntimeAuditConfig())

    assert "insert_or_skip" in message
    assert "pitwall.webhook_receiver" not in sys.modules

    # Handler no longer dedupes through the repository: the check fails even though the
    # supplied configuration asserts idempotency.
    monkeypatch.setattr(_probes, "webhook_dedupes_through_repository", lambda: False)
    with pytest.raises(checks.CheckFailed, match="insert_or_skip"):
        checks.check_07_webhook_idempotent_fast200(_AssertedConfig())  # type: ignore[arg-type]  # reason: partial config stub for one check


def test_repository_dedupe_probe_detects_a_non_deduping_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _probes.webhook_repository_skips_duplicates() is True

    monkeypatch.setattr(
        "pitwall.db.repository._INSERT_WEBHOOK_DELIVERY_SQL", "INSERT INTO t VALUES ($1, $2, $3)"
    )
    assert _probes.webhook_repository_skips_duplicates() is False
