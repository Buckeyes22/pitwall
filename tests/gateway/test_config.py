"""Config cases ported from the Node gateway tests/config.test.ts, plus the route-table loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pitwall.gateway.config import (
    DEFAULT_BIND,
    DEFAULT_BODY_CAP_BYTES,
    DEFAULT_PORT,
    ConfigError,
    GatewayConfig,
    parse_compression_header,
    resolve_config,
)
from tests.gateway.test_app_support import ROUTE_KEY

ENV = {
    "PITWALL_GATEWAY_TOKEN": "fixture-token",
    "PITWALL_GATEWAY_UPSTREAM_URL": "http://upstream.invalid/v1",
}


def port_of(port: str | None = None) -> int:
    return resolve_config(ENV, port=port).port


@pytest.mark.parity
def test_defaults_to_the_documented_port_when_port_is_absent() -> None:
    # Source: config.test.ts "resolveConfig --port parsing > defaults to the documented port"
    assert port_of() == DEFAULT_PORT


@pytest.mark.parity
def test_accepts_full_unsigned_decimal_integers_in_range() -> None:
    # Source: config.test.ts "resolveConfig --port parsing > accepts full unsigned decimal integers"
    assert port_of("0") == 0
    assert port_of("1") == 1
    assert port_of("20130") == 20130
    assert port_of("65535") == 65535


@pytest.mark.parity
@pytest.mark.parametrize(
    "raw", ["123junk", "2.5", "1e3", "0x10", "-1", "+1", " 1", "1 ", "", "NaN", "Infinity"]
)
def test_rejects_non_integer_port_value(raw: str) -> None:
    # Source: config.test.ts "resolveConfig --port parsing > rejects non-integer port value %j"
    with pytest.raises(ConfigError, match="--port"):
        port_of(raw)


@pytest.mark.parity
def test_rejects_ports_above_65535() -> None:
    # Source: config.test.ts "resolveConfig --port parsing > rejects ports above 65535"
    with pytest.raises(ConfigError, match="--port"):
        port_of("65536")


@pytest.mark.parity
def test_rejects_an_empty_port_value() -> None:
    # A --port flag with no value at all is an argparse usage error (tests/cli/test_gateway_cli.py).
    with pytest.raises(ConfigError, match="--port"):
        port_of("")


@pytest.mark.parity
def test_defaults_to_the_documented_loopback_bind() -> None:
    # Source: config.test.ts "resolveConfig --bind parsing > defaults to the documented loopback bind"
    assert resolve_config(ENV).bind == DEFAULT_BIND


@pytest.mark.parity
@pytest.mark.parametrize(
    "bind",
    [
        "127.0.0.1",
        "127.0.0.2",
        "127.255.255.254",
        "::1",
        "0:0:0:0:0:0:0:1",
        "localhost",
        "ip6-localhost",
        "ip6-loopback",
    ],
)
def test_accepts_loopback_bind(bind: str) -> None:
    # Source: config.test.ts "resolveConfig --bind parsing > accepts loopback bind %j"
    assert resolve_config(ENV, bind=bind).bind == bind


@pytest.mark.parity
@pytest.mark.parametrize(
    "bind",
    [
        "127.example.com",
        "127.0.0.1.example.com",
        "127.0.0.1.5",
        "127.0.0.256",
        "127.0.0.0.1",
        "127.",
        "127",
        "0.0.0.0",
        "::ffff:127.0.0.1",
        "localhost.example.com",
        "notlocalhost",
    ],
)
def test_rejects_non_loopback_bind(bind: str) -> None:
    # Source: config.test.ts "resolveConfig --bind parsing > rejects non-loopback bind %j"
    with pytest.raises(ConfigError, match="(?i)loopback"):
        resolve_config(ENV, bind=bind)


def test_direct_construction_also_refuses_a_non_loopback_bind() -> None:
    with pytest.raises(ConfigError, match="(?i)loopback"):
        GatewayConfig(token="t", bind="0.0.0.0", upstream_base_url="http://u.invalid")


def test_body_cap_and_rate_limit_are_configurable() -> None:
    config = resolve_config(
        {
            **ENV,
            "PITWALL_GATEWAY_BODY_CAP_BYTES": "2048",
            "PITWALL_GATEWAY_RATE_LIMIT_RPM": "7",
        },
    )
    assert (config.body_cap_bytes, config.rate_limit_rpm) == (2048, 7)
    assert resolve_config(ENV).body_cap_bytes == DEFAULT_BODY_CAP_BYTES


@pytest.mark.parametrize(
    "name", ["PITWALL_GATEWAY_BODY_CAP_BYTES", "PITWALL_GATEWAY_RATE_LIMIT_RPM"]
)
@pytest.mark.parametrize("raw", ["0", "-5", "abc", "1.5"])
def test_invalid_cap_or_rate_settings_are_refused(name: str, raw: str) -> None:
    with pytest.raises(ConfigError, match=name):
        resolve_config({**ENV, name: raw})


def test_upstream_url_must_be_a_url() -> None:
    with pytest.raises(ConfigError, match="PITWALL_GATEWAY_UPSTREAM_URL"):
        resolve_config({**ENV, "PITWALL_GATEWAY_UPSTREAM_URL": "not a url"})


def write_routes(tmp_path: Path, payload: object) -> str:
    path = tmp_path / "routes.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return str(path)


def routes_env(tmp_path: Path, payload: object, **extra: str) -> dict[str, str]:
    return {
        "PITWALL_GATEWAY_TOKEN": "t",
        "PITWALL_GATEWAY_ROUTES": write_routes(tmp_path, payload),
        **extra,
    }


def test_route_table_resolves_keys_from_the_environment(tmp_path: Path) -> None:
    payload = {
        "schema_version": 1,
        "routes": {
            "a": {"base_url": "http://a.test/v1", "model_id": "m-a"},
            "b": {
                "base_url": "http://b.test/v1",
                "model_id": "m-b",
                "api_key_env": "KEY_B",  # pragma: allowlist secret
                "key_required": True,
            },
        },
    }
    config = resolve_config(routes_env(tmp_path, payload, KEY_B=" k-b "))
    assert config.routes is not None
    assert config.routes["a"].api_key is None
    assert config.routes["b"].api_key == ROUTE_KEY
    assert config.routes["b"].key_required is True
    assert ROUTE_KEY not in repr(config.routes["b"])


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not json", "could not be read as JSON"),
        ({"schema_version": 2, "routes": {}}, "schema_version 1"),
        ({"schema_version": 1}, "schema_version 1"),
        ({"schema_version": 1, "routes": {"a": {"base_url": "http://a.test"}}}, "route a needs"),
        (
            {"schema_version": 1, "routes": {"a": {"base_url": "nope", "model_id": "m"}}},
            "route a base_url is not a valid URL",
        ),
    ],
)
def test_malformed_route_table_is_refused(tmp_path: Path, payload: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        resolve_config(routes_env(tmp_path, payload))


def test_missing_route_table_file_is_refused() -> None:
    env = {"PITWALL_GATEWAY_TOKEN": "t", "PITWALL_GATEWAY_ROUTES": "/nonexistent/routes.json"}
    with pytest.raises(ConfigError, match="could not be read as JSON"):
        resolve_config(env)


@pytest.mark.parametrize(
    ("value", "policy"),
    [
        ("rtk", "rtk"),
        ("caveman", "caveman"),
        ("stacked", "stacked"),
        ("off", "off"),
        ("bogus", "off"),
        (None, "off"),
    ],
)
def test_compression_header_parsing(value: str | None, policy: str) -> None:
    assert parse_compression_header(value) == policy
