"""classify_429 reads every naive timestamp as UTC, in headers and in bodies."""

from __future__ import annotations

import datetime as dt
import time

import pytest

from pitwall.providers.gateway import classify_429

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def non_utc_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_naive_body_timestamp_is_utc(non_utc_host: None) -> None:
    reason, reset_at = classify_429(429, "quota resets at 2026-09-10T15:30:00", {}, now=NOW)
    assert reason == "quota_exhausted"
    assert reset_at == dt.datetime(2026, 9, 10, 15, 30, tzinfo=dt.UTC)


def test_naive_header_timestamp_is_utc(non_utc_host: None) -> None:
    _, reset_at = classify_429(429, "", {"retry-after": "2026-09-10T15:30:00"}, now=NOW)
    assert reset_at == dt.datetime(2026, 9, 10, 15, 30, tzinfo=dt.UTC)


def test_zulu_body_timestamp_is_unchanged(non_utc_host: None) -> None:
    _, reset_at = classify_429(429, "try again at 2026-09-10 15:30Z", {}, now=NOW)
    assert reset_at == dt.datetime(2026, 9, 10, 15, 30, tzinfo=dt.UTC)
