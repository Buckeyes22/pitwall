from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from pitwall.config import (
    ConfigFileError,
    PitwallSettings,
    check_domain_config,
    is_loopback_host,
    load_settings_from_env,
    require_credentials_for_bind,
    require_runtime_env,
    required_runtime_env_vars,
)
from tests._hermetic_env import HERMETIC_REQUEST_BEHAVIOR_ENV_VARS

_ALL_RUNTIME_ENV = (
    "RUNPOD_API_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "PITWALL_ADMIN_SECRET",
    "PITWALL_CONFIG_FILE",
    "PITWALL_MONTHLY_BUDGET_USD",
    "PITWALL_PRICE_MAX_AGE_S",
    "PITWALL_RUNPOD_MARKET_CACHE_TTL_S",
    "PITWALL_HF_TOKEN",
    "PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST",
    "PITWALL_ROUTING_MODE",
    "PITWALL_ROUTING_WEIGHTS",
    "PITWALL_ROUTING_MAX_ATTEMPTS",
    "PITWALL_JOB_EVENT_LIMIT",
    "PITWALL_ROUTING_CLI",
)


@pytest.fixture(autouse=True)
def _clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALL_RUNTIME_ENV:
        monkeypatch.delenv(name, raising=False)


def test_hermetic_session_bootstrap_removes_request_behavior_env() -> None:
    assert not set(HERMETIC_REQUEST_BEHAVIOR_ENV_VARS) & os.environ.keys()


def test_api_missing_runtime_env_exits_ex_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        require_runtime_env("api")

    assert raised.value.code == os.EX_CONFIG
    err = capsys.readouterr().err
    assert "RUNPOD_API_KEY" not in err
    assert "DATABASE_URL" in err
    assert "REDIS_URL" in err


def test_api_accepts_required_env_without_admin_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://pitwall:pitwall@localhost/pitwall")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/4")

    require_runtime_env("pitwall-api")


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_loopback_hosts_are_recognized(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "api.internal"])
def test_non_loopback_hosts_are_rejected_without_credentials(
    host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        require_credentials_for_bind("api", host, ("PITWALL_API_TOKEN",))
    assert raised.value.code == os.EX_CONFIG
    assert "PITWALL_API_TOKEN" in capsys.readouterr().err


def test_non_loopback_bind_accepts_required_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_API_TOKEN", "configured")
    require_credentials_for_bind("api", "0.0.0.0", ("PITWALL_API_TOKEN",))


def test_insecure_bind_override_is_explicit_and_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PITWALL_UNSAFE_ALLOW_INSECURE_BIND", "1")
    require_credentials_for_bind("api", "0.0.0.0", ("PITWALL_API_TOKEN",))
    assert "WARNING" in capsys.readouterr().err


def test_empty_runtime_env_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/4")

    with pytest.raises(SystemExit) as raised:
        require_runtime_env("api")

    assert raised.value.code == os.EX_CONFIG


def test_cost_exporter_only_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://pitwall:pitwall@localhost/pitwall")

    require_runtime_env("pitwall-cost-exporter")


def test_required_runtime_env_vars_defaults_to_core_for_unknown_service() -> None:
    assert required_runtime_env_vars("future-service") == (
        "RUNPOD_API_KEY",
        "DATABASE_URL",
        "REDIS_URL",
    )


def test_mcp_requires_core_runtime_env(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        require_runtime_env("mcp")

    assert raised.value.code == os.EX_CONFIG
    err = capsys.readouterr().err
    assert "RUNPOD_API_KEY" in err
    assert "DATABASE_URL" in err
    assert "REDIS_URL" in err


def test_mcp_accepts_required_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://pitwall:pitwall@localhost/pitwall")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/4")

    require_runtime_env("mcp")


def test_settings_load_default_pitwall_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pitwall.toml").write_text(
        """
runpod_api_key = "file-runpod-key"
database_url = "postgresql://file-db/pitwall"
redis_url = "redis://file-redis:6379/4"
pitwall_monthly_budget_usd = 125.5
""".lstrip(),
        encoding="utf-8",
    )

    settings = load_settings_from_env()

    assert settings.runpod_api_key == "file-runpod-key"
    assert settings.database_url == "postgresql://file-db/pitwall"
    assert settings.redis_url == "redis://file-redis:6379/4"
    assert settings.pitwall_monthly_budget_usd == 125.5


def test_settings_config_file_is_overridden_by_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "custom-pitwall.toml"
    config_file.write_text(
        """
RUNPOD_API_KEY = "file-runpod-key"
DATABASE_URL = "postgresql://file-db/pitwall"
REDIS_URL = "redis://file-redis:6379/4"
PITWALL_MONTHLY_BUDGET_USD = 125.5
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("RUNPOD_API_KEY", "env-runpod-key")
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "25.0")

    settings = load_settings_from_env()

    assert settings.runpod_api_key == "env-runpod-key"
    assert settings.database_url == "postgresql://file-db/pitwall"
    assert settings.redis_url == "redis://file-redis:6379/4"
    assert settings.pitwall_monthly_budget_usd == 25.0


def test_budget_env_values_are_decimal_exact_without_float_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "123.456789123456789")
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "0.000000000000000001")
    monkeypatch.setenv(
        "PITWALL_BUDGET_BREACH_KILL_HEADROOM_FLOOR_USD",
        "0.123456789123456789",
    )

    settings = load_settings_from_env()

    assert settings.pitwall_monthly_budget_usd == Decimal("123.456789123456789")
    assert settings.pitwall_per_request_max_usd == Decimal("0.000000000000000001")
    assert settings.pitwall_budget_breach_kill_headroom_floor_usd == Decimal("0.123456789123456789")


def test_hf_token_loads_into_explicit_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITWALL_HF_TOKEN", "hf-test-canary-value")
    settings = load_settings_from_env()
    assert settings.pitwall_hf_token == "hf-test-canary-value"
    assert "HF_TOKEN" not in settings.model_dump()


def test_endpoint_key_loads_into_explicit_setting_without_defaulting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert PitwallSettings().pitwall_endpoint_key == ""
    monkeypatch.setenv("PITWALL_ENDPOINT_KEY", "endpoint-test-secret")
    settings = load_settings_from_env()
    assert settings.pitwall_endpoint_key == "endpoint-test-secret"
    assert "PITWALL_ENDPOINT_KEY" not in settings.model_dump()


def test_price_max_age_setting_is_optional_and_reads_its_env_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert PitwallSettings().pitwall_price_max_age_s is None
    monkeypatch.setenv("PITWALL_PRICE_MAX_AGE_S", "120")
    assert load_settings_from_env().pitwall_price_max_age_s == 120


def test_runpod_market_cache_ttl_is_typed_non_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert PitwallSettings().runpod_market_cache_ttl_s == 300.0
    monkeypatch.setenv("PITWALL_RUNPOD_MARKET_CACHE_TTL_S", "0")
    assert load_settings_from_env().runpod_market_cache_ttl_s == 0.0

    monkeypatch.setenv("PITWALL_RUNPOD_MARKET_CACHE_TTL_S", "-1")
    with pytest.raises(ConfigFileError, match="runpod_market_cache_ttl_s"):
        load_settings_from_env()


def test_saturation_settings_default_and_load_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = PitwallSettings()
    assert defaults.pitwall_saturation_window_s == 60
    assert defaults.pitwall_saturation_4xx_threshold == 30

    monkeypatch.setenv("PITWALL_SATURATION_WINDOW_S", "45")
    monkeypatch.setenv("PITWALL_SATURATION_4XX_THRESHOLD", "12")
    loaded = load_settings_from_env()

    assert loaded.pitwall_saturation_window_s == 45
    assert loaded.pitwall_saturation_4xx_threshold == 12


def test_pre_spend_mode_defaults_and_loads_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert PitwallSettings().pitwall_pre_spend_mode == "balanced"
    monkeypatch.setenv("PITWALL_PRE_SPEND_MODE", "block")
    assert load_settings_from_env().pitwall_pre_spend_mode == "block"


def test_routing_cli_default_is_pitwall_agents() -> None:
    assert PitwallSettings().pitwall_routing_cli == "pitwall agents"


def test_pre_spend_mode_rejects_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITWALL_PRE_SPEND_MODE", "monitor")
    with pytest.raises(ConfigFileError, match="pitwall_pre_spend_mode"):
        load_settings_from_env()


def test_production_routing_settings_are_typed_and_decimal_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = PitwallSettings()
    assert defaults.pitwall_routing_mode == "priority"
    assert defaults.pitwall_routing_max_attempts == 3
    assert defaults.pitwall_job_event_limit == 25

    monkeypatch.setenv("PITWALL_ROUTING_MODE", "weighted")
    monkeypatch.setenv(
        "PITWALL_ROUTING_WEIGHTS",
        '{"llm.chat":{"cost":"1.25","latency":"0.0005"}}',
    )
    monkeypatch.setenv("PITWALL_ROUTING_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("PITWALL_JOB_EVENT_LIMIT", "40")

    loaded = load_settings_from_env()

    assert loaded.pitwall_routing_mode == "weighted"
    assert loaded.pitwall_routing_weights["llm.chat"].cost == Decimal("1.25")
    assert loaded.pitwall_routing_weights["llm.chat"].latency == Decimal("0.0005")
    assert loaded.pitwall_routing_max_attempts == 5
    assert loaded.pitwall_job_event_limit == 40


def test_empty_routing_weights_env_keeps_default_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_ROUTING_WEIGHTS", "")

    assert load_settings_from_env().pitwall_routing_weights == {}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PITWALL_ROUTING_MODE", "random"),
        ("PITWALL_ROUTING_WEIGHTS", '{"*":{"cost":"NaN","latency":"1"}}'),
        ("PITWALL_ROUTING_WEIGHTS", '{"":{"cost":"1","latency":"1"}}'),
        ("PITWALL_ROUTING_MAX_ATTEMPTS", "11"),
        ("PITWALL_JOB_EVENT_LIMIT", "0"),
    ],
)
def test_production_routing_settings_reject_unsafe_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):  # ConfigFileError, or pydantic-settings' SettingsError
        load_settings_from_env()


def test_lease_max_lifetime_defaults_and_loads_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert PitwallSettings().pitwall_lease_max_lifetime_min == 1440
    monkeypatch.setenv("PITWALL_LEASE_MAX_LIFETIME_MIN", "720")
    assert load_settings_from_env().pitwall_lease_max_lifetime_min == 720


def test_webhook_loopback_allowlist_defaults_empty_and_parses_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert PitwallSettings().pitwall_webhook_loopback_allowlist == ()
    monkeypatch.setenv(
        "PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST",
        "127.0.0.1:8765,[::1]:8766",
    )
    assert load_settings_from_env().pitwall_webhook_loopback_allowlist == (
        "127.0.0.1:8765",
        "[::1]:8766",
    )


@pytest.mark.parametrize(
    "value",
    ["localhost:8765", "10.0.0.1:8765", "127.0.0.1", "127.0.0.1:0"],
)
def test_webhook_loopback_allowlist_rejects_invalid_entries(value: str) -> None:
    with pytest.raises(ValidationError, match="loopback allow-list"):
        PitwallSettings(pitwall_webhook_loopback_allowlist=(value,))


def test_models_dir_loads_from_env_as_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PITWALL_MODELS_DIR", str(tmp_path))
    assert load_settings_from_env().pitwall_models_dir == tmp_path


def test_domain_config_check_flags_broken_config() -> None:
    settings = PitwallSettings(
        runpod_api_key="",
        database_url="",
        redis_url="redis://localhost:6379/4",
        pitwall_monthly_budget_usd=-1,
        pitwall_per_request_max_usd=-0.01,
        pitwall_embedding_via_pitwall=True,
        pitwall_base_url="",
    )

    result = check_domain_config("api", settings=settings)

    assert not result.ok
    error_text = "\n".join(issue.message for issue in result.errors)
    assert "RUNPOD_API_KEY" not in error_text
    assert "DATABASE_URL" in error_text
    assert "PITWALL_MONTHLY_BUDGET_USD" in error_text
    assert "PITWALL_PER_REQUEST_MAX_USD" in error_text
    assert "PITWALL_BASE_URL" in error_text


def test_domain_config_check_passes_good_config() -> None:
    settings = PitwallSettings(
        runpod_api_key="test-key",
        database_url="postgresql://pitwall:pitwall@localhost/pitwall",
        redis_url="redis://localhost:6379/4",
        pitwall_monthly_budget_usd=50.0,
        pitwall_per_request_max_usd=10.0,
        r2_temp_credentials_enabled="false",
    )

    result = check_domain_config("api", settings=settings)

    assert result.ok
    assert result.errors == ()


def test_the_documented_routing_cli_override_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """serve-quickstart and SDLC 16 document PITWALL_ROUTING_CLI; it was never read."""
    monkeypatch.setenv("PITWALL_ROUTING_CLI", "/opt/routing/bin/pitwall-router")

    assert load_settings_from_env().pitwall_routing_cli == "/opt/routing/bin/pitwall-router"
