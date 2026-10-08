"""Acceptance coverage for the remaining loader gaps in config-source-review.json.

Closes the gateway and routing `remaining_configuration_discovery_gaps` plus
the `config_files` issue without duplicating the source-reviewed tests in
`tests/test_config_runtime_env.py`. Every test uses a temporary CWD and monkeypatched configuration environment.
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path

import pytest

from pitwall.config import (
    ConfigFileError,
    PitwallSettings,
    RoutingWeights,
    load_settings_from_env,
    require_runtime_env,
    resolve_config_file,
)
from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode
from pitwall.core.models import Capability
from pitwall.routing.production import routing_weights_for

_CONFIG_ENV = (
    "PITWALL_CONFIG_FILE",
    "PITWALL_GATEWAY_URL",
    "PITWALL_ROUTING_MODE",
    "PITWALL_ROUTING_WEIGHTS",
)
_NOW = dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _isolated_config_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _capability(
    *,
    capability_id: str = "cap_chat",
    name: str = "llm.chat",
    class_: CapabilityClass = CapabilityClass.LLM,
) -> Capability:
    return Capability(
        id=capability_id,
        name=name,
        version="1.0.0",
        class_=class_,
        cost_mode=CostMode.PER_TOKEN,
        source=CapabilitySource.API,
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "pitwall-config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_gateway_defaults_match_documented_loopback_shim() -> None:
    """Default loopback shim URL.

    The URL default is the documented `.env.example` value and the
    `pitwall gateway doctor` front-door default (`src/pitwall/cli_gateway.py`).
    """

    defaults = PitwallSettings()

    assert defaults.pitwall_gateway_url == "http://127.0.0.1:20130/v1"


def test_gateway_values_follow_toml_then_env_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config-file values first, then explicitly set env values win.

    Env-over-TOML precedence is documented in `docs/sdlc/16-core-config.md`
    (settings source order) and proven for other fields by
    `tests/test_config_runtime_env.py::test_settings_config_file_is_overridden_by_env`.
    """

    path = _write_config(
        tmp_path,
        'pitwall_gateway_url = "http://127.0.0.1:20131/v1"\n',
    )
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))

    from_file = load_settings_from_env()
    assert from_file.pitwall_gateway_url == "http://127.0.0.1:20131/v1"

    monkeypatch.setenv("PITWALL_GATEWAY_URL", "http://127.0.0.1:20132/v1")

    from_env = load_settings_from_env()
    assert from_env.pitwall_gateway_url == "http://127.0.0.1:20132/v1"


def test_routing_weights_four_nested_defaults_are_decimal_exact() -> None:
    """The four nested fallback weights, including the quota/reset values.

    `w_quota=10` and `w_reset=2.5` are documented at `docs/sdlc/04-routing.md:1025`;
    `cost=1` and `latency=0.001` are documented in
    `docs/sdlc/16-core-config.md` (Production routing configuration).
    """

    weights = RoutingWeights()

    assert weights.cost == Decimal("1")
    assert weights.latency == Decimal("0.001")
    assert weights.w_quota == Decimal("10")
    assert weights.w_reset == Decimal("2.5")

    resolved = routing_weights_for(PitwallSettings(), _capability())
    assert resolved == weights


def test_routing_weight_lookup_follows_documented_precedence() -> None:
    """Capability ID, then name, then class, then `*`.

    Documented in `docs/sdlc/16-core-config.md` (Production routing configuration)
    and `docs/operator/production-routing.md:19-22`.
    """

    capability = _capability()
    mapping = {
        "cap_chat": RoutingWeights(cost="1"),
        "llm.chat": RoutingWeights(cost="2"),
        "llm": RoutingWeights(cost="3"),
        "*": RoutingWeights(cost="4"),
    }

    def resolved(cost: str) -> Decimal:
        return (
            routing_weights_for(
                PitwallSettings(pitwall_routing_weights=dict(mapping)),
                capability,
            ).cost.quantize(Decimal("1"))
            if False
            else routing_weights_for(
                PitwallSettings(pitwall_routing_weights={**mapping}),
                capability,
            ).cost
        )

    assert routing_weights_for(
        PitwallSettings(pitwall_routing_weights=dict(mapping)), capability
    ).cost == Decimal("1")

    for key in ("cap_chat",):
        del mapping[key]
    assert routing_weights_for(
        PitwallSettings(pitwall_routing_weights=dict(mapping)), capability
    ).cost == Decimal("2")

    del mapping["llm.chat"]
    assert routing_weights_for(
        PitwallSettings(pitwall_routing_weights=dict(mapping)), capability
    ).cost == Decimal("3")

    del mapping["llm"]
    assert routing_weights_for(
        PitwallSettings(pitwall_routing_weights=dict(mapping)), capability
    ).cost == Decimal("4")


@pytest.mark.parametrize("toml_key", ["pitwall_routing_weights", "PITWALL_ROUTING_WEIGHTS"])
def test_routing_weights_toml_and_env_precedence(
    toml_key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mapping loads from TOML field/alias keys, and env wins per key while
    TOML-only keys remain applied, mirroring the documented env-over-TOML order.
    """

    path = _write_config(
        tmp_path,
        f'[{toml_key}."llm.chat"]\n'
        'cost = "2"\n'
        'latency = "0.5"\n'
        'w_quota = "20"\n'
        'w_reset = "4"\n'
        f'[{toml_key}."*"]\n'
        'cost = "7"\n',
    )
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))

    from_file = load_settings_from_env()
    file_weights = from_file.pitwall_routing_weights["llm.chat"]
    assert file_weights.cost == Decimal("2")
    assert file_weights.latency == Decimal("0.5")
    assert file_weights.w_quota == Decimal("20")
    assert file_weights.w_reset == Decimal("4")
    assert from_file.pitwall_routing_weights["*"].cost == Decimal("7")

    monkeypatch.setenv("PITWALL_ROUTING_WEIGHTS", '{"llm.chat":{"cost":"5"}}')

    from_env = load_settings_from_env()
    assert from_env.pitwall_routing_weights["llm.chat"].cost == Decimal("5")
    assert from_env.pitwall_routing_weights["llm.chat"].latency == Decimal("0.5")
    assert from_env.pitwall_routing_weights["llm.chat"].w_quota == Decimal("20")
    assert from_env.pitwall_routing_weights["llm.chat"].w_reset == Decimal("4")
    assert from_env.pitwall_routing_weights["*"].cost == Decimal("7")


@pytest.mark.parametrize(
    "body",
    [
        '[pitwall_routing_weights."llm.chat"]\ncost = "1001"\n',
        '[pitwall_routing_weights."llm.chat"]\nquota = "1"\n',
    ],
)
def test_routing_weights_invalid_config_file_values_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    """Out-of-range and unknown nested keys fail settings validation.

    Documented in `docs/sdlc/16-core-config.md` (Production routing configuration).
    """

    path = _write_config(tmp_path, body)
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))

    with pytest.raises(ConfigFileError, match="pitwall_routing_weights"):
        load_settings_from_env()


def test_absent_discovered_config_file_contributes_no_settings(
    tmp_path: Path,
) -> None:
    """No explicit path and no `./pitwall.toml` is a valid, file-free load."""

    assert resolve_config_file() is None

    settings = load_settings_from_env()
    assert settings.pitwall_gateway_url == "http://127.0.0.1:20130/v1"
    assert settings.pitwall_routing_weights == {}


def test_explicit_missing_config_file_raises_config_file_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PITWALL_CONFIG_FILE` naming a missing file fails closed with the path.

    Documented in `docs/sdlc/16-core-config.md` (TOML Config File).
    """

    missing = tmp_path / "does-not-exist.toml"
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(missing))

    with pytest.raises(ConfigFileError) as raised:
        load_settings_from_env()

    assert "PITWALL_CONFIG_FILE" in str(raised.value)
    assert "does not exist" in str(raised.value)
    assert str(missing) in str(raised.value)


def test_explicit_non_toml_suffix_raises_config_file_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only `.toml` is accepted for an explicit config path, even when it exists.

    Documented in `docs/sdlc/16-core-config.md` (TOML Config File).
    """

    path = tmp_path / "pitwall.yaml"
    path.write_text("pitwall_routing_mode: weighted\n", encoding="utf-8")
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))

    with pytest.raises(ConfigFileError, match="must be TOML"):
        load_settings_from_env()


def test_malformed_toml_raises_config_file_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read/parse failure is wrapped as `ConfigFileError`, not a raw TOML error."""

    path = _write_config(tmp_path, "pitwall_gateway_url = = broken\n")
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))

    with pytest.raises(ConfigFileError) as raised:
        load_settings_from_env()

    assert "could not read Pitwall config file" in str(raised.value)
    assert str(path) in str(raised.value)


def test_init_settings_override_env_and_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct init values outrank env, TOML, and defaults.

    Documented in `docs/sdlc/16-core-config.md` (settings source order).
    """

    path = _write_config(
        tmp_path,
        'pitwall_gateway_url = "http://127.0.0.1:20131/v1"\n',
    )
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))
    monkeypatch.setenv("PITWALL_GATEWAY_URL", "http://127.0.0.1:20132/v1")

    settings = PitwallSettings(pitwall_gateway_url="http://127.0.0.1:20133/v1")

    assert settings.pitwall_gateway_url == "http://127.0.0.1:20133/v1"


def test_require_runtime_env_exits_ex_config_and_sanitizes_config_file_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A broken config file exits `os.EX_CONFIG` with path-only, secret-free stderr.

    Documented in `docs/sdlc/16-core-config.md` (Fail-Closed Boot).
    """

    missing = tmp_path / "missing.toml"
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(missing))
    monkeypatch.setenv("RUNPOD_API_KEY", "acceptance-canary-secret")

    with pytest.raises(SystemExit) as raised:
        require_runtime_env("api")

    assert raised.value.code == os.EX_CONFIG
    err = capsys.readouterr().err
    assert "ERROR [invalid-settings]" in err
    assert str(missing) in err
    assert "acceptance-canary-secret" not in err


def test_unconfigured_budget_and_r2_defaults_match_operator_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-run caps and credential scope match the operator contract."""
    for name in tuple(os.environ):
        if name.startswith(("PITWALL_", "R2_")):
            monkeypatch.delenv(name, raising=False)
    settings = load_settings_from_env()
    assert settings.pitwall_monthly_budget_usd == Decimal("50.0")
    assert settings.pitwall_per_request_max_usd == Decimal("10.0")
    assert settings.pitwall_budget_breach_kill_headroom_floor_usd == Decimal("0.0")
    assert settings.pitwall_budget_breach_kill_mode == "disabled"
    assert settings.pitwall_routing_weights == {}
    assert settings.r2_temp_credential_ttl_s == 21600
    assert settings.r2_temp_credential_permission == "object-read-write"
    assert settings.r2_temp_credentials_enabled == "auto"
    assert settings.r2_temp_credentials_required is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PITWALL_ROUTING_MAX_ATTEMPTS", "secret-canary-attempts"),
        ("PITWALL_MONTHLY_BUDGET_USD", "secret-canary-budget"),
        ("R2_TEMP_CREDENTIAL_TTL_S", "secret-canary-ttl"),
        ("PITWALL_ROUTING_WEIGHTS", "{secret-canary-weights"),
        ("R2_TEMP_CREDENTIAL_PERMISSION", "secret-canary-permission"),
    ],
)
def test_an_unparsable_env_value_names_the_variable_and_never_echoes_it(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    from pitwall.config import format_settings_load_error

    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError) as raised:
        load_settings_from_env()
    message = format_settings_load_error(raised.value)
    assert name in message
    assert "secret-canary" not in message


def test_a_rejected_setting_names_its_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.config import format_settings_load_error

    monkeypatch.setenv("PITWALL_PRE_SPEND_MODE", "sometimes")
    with pytest.raises(ConfigFileError) as raised:
        load_settings_from_env()
    message = format_settings_load_error(raised.value)
    assert "PITWALL_PRE_SPEND_MODE" in message
    assert "sometimes" not in message
