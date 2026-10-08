"""J37: every configuration key discovery finds, from the environment, TOML, and its consumer.

For each of the 77 ``PitwallSettings`` fields: the valid environment value parses to the
pinned value, a ``pitwall.toml`` value applies, and the environment overrides TOML; an
invalid value makes ``pitwall config check --json`` exit ``EX_CONFIG`` with an error that
names the environment variable and never echoes the value. The four ``RoutingWeights``
fields go through ``PITWALL_ROUTING_WEIGHTS``. Every ``.env.example`` key either sets the
settings fields it aliases, or reaches the consumer that reads it directly (a probe in
``config_probes`` drives that consumer with and without the key and pins both results), or,
for the compose-only ``PITWALL_BIND_IP``, reaches every published port in
``docker compose config``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tests.hang_guard import HANG_GUARD_SECS

pytestmark = [pytest.mark.release]

ROOT = Path(__file__).resolve().parents[2]
FIXTURES: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parent / "config_fixtures.json").read_text()
)
_PREFIXES = ("PITWALL_", "RUNPOD_", "R2_", "CLOUDFLARE_", "CF_", "LANGFUSE_", "RESEND_")
_DEAD = "http://127.0.0.1:9"
_BASE = {
    "RUNPOD_API_KEY": "placeholder",
    "DATABASE_URL": "postgresql://placeholder@127.0.0.1:9/placeholder",
    "REDIS_URL": "redis://127.0.0.1:9/0",
}


def _of(kind: str) -> list[str]:
    return sorted(key for key, fixture in FIXTURES.items() if fixture["kind"] == kind)


def _settings(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    toml: str | None = None,
    tmp_path: Path | None = None,
) -> dict[str, Any]:
    from pitwall.config import PitwallSettings

    for name in tuple(os.environ):
        if name.startswith(_PREFIXES) or name in {"DATABASE_URL", "REDIS_URL"}:
            monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if toml is not None:
        assert tmp_path is not None
        path = tmp_path / "pitwall.toml"
        path.write_text(toml)
        monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))
    return dict(PitwallSettings().model_dump(mode="json"))


def _config_check(name: str, value: str) -> tuple[int, str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", **_BASE, name: value}
    result = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "pitwall"), "config", "check", "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
    )
    return result.returncode, result.stdout + result.stderr


def _invalid_cases() -> list[tuple[str, str, str]]:
    cases = []
    for key, fixture in FIXTURES.items():
        if fixture["kind"] == "field" and fixture.get("invalid") and fixture.get("env"):
            cases.append((key, fixture["env"], fixture["invalid"]))
        elif fixture["kind"] == "weight":
            weights = json.dumps({"*": {fixture["weight"]: fixture["invalid"]}})
            cases.append((key, "PITWALL_ROUTING_WEIGHTS", weights))
        elif fixture["kind"] == "consumer" and fixture.get("invalid"):
            cases.append((key, fixture["env"], fixture["invalid"]))
    return cases


@pytest.fixture(scope="module")
def invalid_outcomes() -> dict[str, tuple[int, str, str, str]]:
    cases = _invalid_cases()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda case: _config_check(case[1], case[2]), cases)
    return {
        key: (code, output, name, value)
        for (key, name, value), (code, output) in zip(cases, results, strict=True)
    }


def _probe(fixture: dict[str, Any], value: str | None, tmp_path: Path) -> Any:
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(ROOT),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "HTTP_PROXY": _DEAD,
        "HTTPS_PROXY": _DEAD,
        "NO_PROXY": "127.0.0.1,localhost",
        **_BASE,
    }
    env.update({k: v.replace("{tmp}", str(tmp_path)) for k, v in fixture["with"].items()})
    if value is not None:
        env[fixture["env"]] = value.replace("{tmp}", str(tmp_path))
        if fixture.get("create_file"):
            Path(env[fixture["env"]]).write_text("")
        if fixture["env"] == "PITWALL_CONFIG_FILE":
            Path(env["PITWALL_CONFIG_FILE"]).write_text("")
    code = (
        "import json, os\nfrom tests.release.config_probes import *\n"
        f"print(json.dumps(attempt(lambda: {fixture['probe']}), default=str))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
    )
    assert result.returncode == 0, (fixture["env"], result.stderr[-600:])
    # Paths inside the scratch dir or the checkout compare as placeholders, so the fixtures
    # hold no machine-specific absolute path.
    line = result.stdout.strip().splitlines()[-1]
    return json.loads(line.replace(str(tmp_path), "{tmp}").replace(str(ROOT), "{repo}"))


def test_every_config_surface_has_a_fixture() -> None:
    from tools.release_acceptance import config_inventory

    rows = config_inventory.discover_with_issues(ROOT)["surfaces"]
    assert sorted(FIXTURES) == sorted(row["surface_id"] for row in rows)
    # A key either has an invalid value J37 feeds it, or says why no value is invalid.
    unexplained = [
        key
        for key, fixture in FIXTURES.items()
        if fixture["kind"] in {"field", "consumer"}
        and not fixture.get("invalid")
        and not fixture.get("no_invalid_reason")
    ]
    assert unexplained == []


@pytest.mark.parametrize("key", _of("field"))
def test_setting_field(
    key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_outcomes: dict[str, tuple[int, str, str, str]],
) -> None:
    fixture = FIXTURES[key]
    field = fixture["field"]
    toml = f"{field} = {fixture['toml']}\n"
    assert _settings(monkeypatch, {}, toml, tmp_path)[field] == fixture["toml_expect"]
    if fixture["env"]:
        env = {fixture["env"]: fixture["valid"]}
        assert _settings(monkeypatch, env)[field] == fixture["expect"]
        assert _settings(monkeypatch, env, toml, tmp_path)[field] == fixture["expect"]
    if key in invalid_outcomes:
        code, output, name, value = invalid_outcomes[key]
        assert code == os.EX_CONFIG, (key, output[-400:])
        assert name in output and value not in output, (key, output[-400:])


@pytest.mark.parametrize("key", _of("agents"))
def test_agents_table_field(key: str, tmp_path: Path) -> None:
    import tomllib

    from pitwall.config import agents_settings_from_toml

    fixture = FIXTURES[key]
    table = tomllib.loads(fixture["toml"])["agents"]
    parsed = agents_settings_from_toml(table)
    assert parsed.model_dump(exclude_unset=True) == table, key
    # The same table in pitwall.toml loads, and a wrong-typed value exits EX_CONFIG naming
    # the [agents] table.
    for toml, expected in ((fixture["toml"], 0), (fixture["wrong_toml"], os.EX_CONFIG)):
        path = tmp_path / f"{expected}.toml"
        path.write_text(toml)
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp",
            "PITWALL_CONFIG_FILE": str(path),
            **_BASE,
        }
        result = subprocess.run(
            [str(ROOT / ".venv" / "bin" / "pitwall"), "config", "check", "--json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=HANG_GUARD_SECS,
        )
        output = result.stdout + result.stderr
        assert result.returncode == expected, (key, output[-400:])
        if expected:
            assert "[agents]" in output, (key, output[-400:])


@pytest.mark.parametrize("key", _of("weight"))
def test_routing_weight(
    key: str,
    monkeypatch: pytest.MonkeyPatch,
    invalid_outcomes: dict[str, tuple[int, str, str, str]],
) -> None:
    fixture = FIXTURES[key]
    weights = json.dumps({"*": {fixture["weight"]: fixture["valid"]}})
    parsed = _settings(monkeypatch, {"PITWALL_ROUTING_WEIGHTS": weights})
    assert parsed["pitwall_routing_weights"]["*"][fixture["weight"]] == fixture["expect"]
    code, output, name, _value = invalid_outcomes[key]
    assert code == os.EX_CONFIG and name in output, (key, output[-400:])


@pytest.mark.parametrize("key", _of("alias"))
def test_env_example_key_sets_its_fields(key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = FIXTURES[key]
    parsed = _settings(monkeypatch, {fixture["env"]: fixture["value"]})
    for field, expected in fixture["expect"].items():
        assert parsed[field] == expected, (fixture["env"], field)


@pytest.mark.parametrize("key", _of("consumer"))
def test_env_example_key_reaches_its_consumer(
    key: str, tmp_path: Path, invalid_outcomes: dict[str, tuple[int, str, str, str]]
) -> None:
    fixture = FIXTURES[key]
    configured = _probe(fixture, fixture["valid"], tmp_path / "set")
    default = _probe(fixture, None, tmp_path / "unset")
    assert configured == fixture["expect"], key
    assert default == fixture["expect_unset"], key
    if fixture.get("operator_value_ignored"):
        assert configured == default, key  # the launcher's own value wins over the operator's
    else:
        assert configured != default, key
    if key in invalid_outcomes:
        code, output, name, value = invalid_outcomes[key]
        assert code == os.EX_CONFIG, (key, output[-400:])
        assert name in output and value not in output, (key, output[-400:])


@pytest.mark.parametrize("key", _of("compose"))
def test_compose_only_key_reaches_every_published_port(key: str) -> None:
    fixture = FIXTURES[key]
    docker = shutil.which("docker")
    assert docker, "docker compose is required to render the compose-only configuration"
    placeholders = dict.fromkeys(
        (
            "PITWALL_ADMIN_SECRET",
            "PITWALL_API_TOKEN",
            "RUNPOD_API_KEY",
            "POSTGRES_PASSWORD",
            "REDIS_PASSWORD",
            "PITWALL_WEBHOOK_SECRET",
            "PITWALL_ARCHIVE_ENCRYPTION_KEY",
        ),
        "placeholder",
    )
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
        **placeholders,
        "PITWALL_WEBHOOK_ENCRYPTION_KEYS": '{"v1": "placeholder"}',
        fixture["env"]: fixture["valid"],
    }
    result = subprocess.run(
        [docker, "compose", "-f", str(ROOT / "docker-compose.yml"), "config"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    expected = fixture.get("expect_rendered", f"host_ip: {fixture['valid']}")
    assert result.stdout.count(expected) == fixture.get("expect_count", 3), key
    if fixture.get("required"):
        del env[fixture["env"]]
        refused = subprocess.run(
            [docker, "compose", "-f", str(ROOT / "docker-compose.yml"), "config"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert refused.returncode != 0, key
        assert f"set {fixture['env']}" in refused.stderr, (key, refused.stderr[-400:])
