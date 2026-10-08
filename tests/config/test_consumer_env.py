"""Environment keys that services read directly are validated like settings, by name."""

from __future__ import annotations

import importlib
import os

import pytest

from pitwall.config import check_domain_config, parse_inbound_rate_limit, parse_rate_limit

_BASE = {
    "RUNPOD_API_KEY": "placeholder-key",
    "DATABASE_URL": "postgresql://pitwall@127.0.0.1:9/pitwall",
    "REDIS_URL": "redis://127.0.0.1:9/0",
}

INVALID = {
    "PITWALL_API_PORT": "not-a-port",
    "PITWALL_API_MAX_CONCURRENCY": "0",
    "PITWALL_API_MAX_BODY_BYTES": "lots",
    "PITWALL_COST_EXPORTER_MAX_CONCURRENCY": "-1",
    "PITWALL_WEBHOOK_MAX_BODY_BYTES": "0",
    "PITWALL_WEBHOOK_MAX_CONCURRENCY": "many",
    "PITWALL_WEBHOOK_RATE_LIMIT": "fast",
    "PITWALL_WEBHOOK_PREVIOUS_SECRETS": '{"not": "a list"}',
    "PITWALL_RETENTION_MODE": "sometimes",
    "PITWALL_RETENTION_DAYS": "0",
    "PITWALL_RETENTION_BATCH_SIZE": "100000",
    "PITWALL_RUN_LIVE": "ture",
    "RUNPOD_LIVE": "maybe",
    "PITWALL_UNSAFE_ALLOW_INSECURE_BIND": "yes-please",
    "PITWALL_ARCHIVE_ENCRYPTION_KEY": "not-a-32-byte-key",
    "PITWALL_API_SCOPED_TOKENS": '{"secret-canary-token": ["godmode"]}',
    "PITWALL_INBOUND_RATE_LIMIT": "lots",
    "PITWALL_LEASE_ADVANCE_WARNING_MIN": "soon",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in tuple(os.environ):
        if name.startswith(("PITWALL_", "RUNPOD_", "R2_")) or name in {"DATABASE_URL", "REDIS_URL"}:
            monkeypatch.delenv(name, raising=False)
    for name, value in _BASE.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


@pytest.mark.parametrize(("name", "value"), sorted(INVALID.items()))
def test_config_check_names_an_invalid_service_key_without_echoing_it(
    clean_env: pytest.MonkeyPatch, name: str, value: str
) -> None:
    clean_env.setenv(name, value)

    result = check_domain_config("api")

    messages = [issue.message for issue in result.errors]
    assert any(message.startswith(name) for message in messages), messages
    assert all(value not in message for message in messages)


def test_malformed_webhook_encryption_keys_are_named(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PITWALL_WEBHOOK_ENCRYPTION_KEYS", '{"v1": "secret-canary-short"}')
    clean_env.setenv("PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY", "v1")

    messages = [issue.message for issue in check_domain_config("api").errors]
    assert any(message.startswith("PITWALL_WEBHOOK_ENCRYPTION_KEYS") for message in messages)
    assert all("secret-canary" not in message for message in messages)


def test_valid_service_keys_raise_no_issue(clean_env: pytest.MonkeyPatch) -> None:
    for name, value in {
        "PITWALL_API_PORT": "8080",
        "PITWALL_API_MAX_BODY_BYTES": "1048576",
        "PITWALL_WEBHOOK_RATE_LIMIT": "120/60s",
        "PITWALL_WEBHOOK_PREVIOUS_SECRETS": '["old-secret"]',
        "PITWALL_RETENTION_MODE": "off",
        "PITWALL_RUN_LIVE": "1",
        "PITWALL_UNSAFE_ALLOW_INSECURE_BIND": "0",
        "PITWALL_ARCHIVE_ENCRYPTION_KEY": "a" * 43 + "=",
        "PITWALL_API_SCOPED_TOKENS": '{"token-one": ["read", "spend"]}',
        "PITWALL_INBOUND_RATE_LIMIT": "30/2 hours",
        "PITWALL_LEASE_ADVANCE_WARNING_MIN": "30, 10,1",
        "PITWALL_WEBHOOK_ENCRYPTION_KEYS": '{"v1": "' + "b" * 43 + '="}',
        "PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY": "v1",
    }.items():
        clean_env.setenv(name, value)

    assert check_domain_config("api").errors == ()


def test_enabled_retention_needs_its_archive_settings_for_the_reconciler(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("PITWALL_RETENTION_MODE", "archive")

    messages = [issue.message for issue in check_domain_config("reconciler").errors]
    assert any("PITWALL_ARCHIVE_DIR" in message for message in messages), messages
    assert any("PITWALL_ARCHIVE_ENCRYPTION_KEY" in message for message in messages)
    assert check_domain_config("api").errors == ()


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("pitwall.api", "PITWALL_API_PORT"),
        ("pitwall.webhook_receiver", "PITWALL_WEBHOOK_MAX_CONCURRENCY"),
        ("pitwall.cost", "PITWALL_COST_EXPORTER_MAX_CONCURRENCY"),
    ],
)
def test_service_entry_points_refuse_a_bad_key_by_name_before_starting(
    clean_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    module: str,
    name: str,
) -> None:
    clean_env.setenv(name, "not-a-number")
    started: list[object] = []
    clean_env.setattr("uvicorn.run", lambda *args, **kwargs: started.append(args))

    with pytest.raises(SystemExit) as raised:
        importlib.import_module(f"{module}.__main__").main([])

    assert raised.value.code == os.EX_CONFIG
    assert name in capsys.readouterr().err
    assert started == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("60/60s", (60, 60.0)),
        ("10/30 seconds", (10, 30.0)),
        ("5/10 secs", (5, 10.0)),
        ("5/2 minutes", (5, 120.0)),
        ("5/2 mins", (5, 120.0)),
        ("30/2 hours", (30, 7200.0)),
        ("30/3 hrs", (30, 10800.0)),
        ("30/hour", (30, 3600.0)),
        ("off", None),
        ("", None),
    ],
)
def test_inbound_rate_limit_accepts_every_documented_window_unit(
    raw: str, expected: tuple[int, float] | None
) -> None:
    assert parse_inbound_rate_limit(raw) == expected


@pytest.mark.parametrize("raw", ["lots", "5/m", "0/60s", "5/0s", "5/-1m"])
def test_inbound_rate_limit_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_inbound_rate_limit(raw)


@pytest.mark.parametrize("raw", ["1/nan", "1/inf", "1/-inf", "1/infs", "1/nan s", "1/1e999"])
def test_rate_limit_parsers_reject_non_finite_windows(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_inbound_rate_limit(raw)
    with pytest.raises(ValueError):
        parse_rate_limit(raw)
