"""Canary tests for centralized secret-safe diagnostics."""

from __future__ import annotations

import logging

import pytest

from pitwall.security.redaction import (
    REDACTED,
    configure_logging_redaction,
    redact_text,
    safe_url_label,
)


def test_redact_text_removes_connection_and_authorization_credentials() -> None:
    canary = "canary-super-secret-value"
    message = (
        f"connect postgresql://operator:{canary}@db.example:5432/pitwall "
        f"Authorization: Bearer {canary} password={canary}"
    )
    redacted = redact_text(message, secrets=(canary,))
    assert canary not in redacted
    assert redacted.count(REDACTED) >= 3


@pytest.mark.parametrize(
    "token", ["hf_test_redaction_token", "sk-test-redaction-token", "rpa_test_token"]
)
def test_redact_text_removes_recognized_token_values(token: str) -> None:
    assert token not in redact_text(f"start arg token={token}")


def test_safe_url_label_has_no_userinfo_or_query() -> None:
    label = safe_url_label("redis://name:password@cache.example:6380/4?token=value")
    assert label == "redis://cache.example:6380/4"
    assert "name" not in label
    assert "password" not in label
    assert "token" not in label


def test_process_logging_factory_removes_explicit_canary(caplog) -> None:
    canary = "pitwall-log-canary-secret"
    configure_logging_redaction(secrets=(canary,))
    with caplog.at_level(logging.WARNING):
        logging.getLogger("pitwall.test.redaction").warning(
            "provider failed: %s", f"token={canary}"
        )
    assert canary not in caplog.text
    assert REDACTED in caplog.text


def test_configured_pitwall_hf_token_is_redacted_from_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    canary = "hf-test-log-canary-value"
    monkeypatch.setenv("PITWALL_HF_TOKEN", canary)
    configure_logging_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("pitwall.test").info("launch credential=%s", canary)
    assert canary not in caplog.text
    assert REDACTED in caplog.text


def test_configured_endpoint_key_is_redacted_from_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    canary = "endpoint-test-log-canary-value"
    monkeypatch.setenv("PITWALL_ENDPOINT_KEY", canary)
    configure_logging_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("pitwall.test").info("endpoint credential=%s", canary)
    assert canary not in caplog.text
    assert REDACTED in caplog.text
