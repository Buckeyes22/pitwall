"""Every raw Compose expression discovery flags renders to its pinned value.

Discovery records Compose substitutions (``${VAR:-default}``, ``${VAR:?}``) as raw
expressions and never evaluates them. This renders each Compose file with
``docker compose config`` in the canonical environment (every required secret set to a
distinct placeholder, everything else at its default) and asserts each flagged field in
``compose_rendered.json``: defaults resolve as documented, secrets reach the services
that read them, connection URLs carry the right credentials and hosts, and every
published port binds loopback. ``docker-compose.prod.yml`` only includes the canonical
stack, so it renders identically.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.release]

ROOT = Path(__file__).resolve().parents[2]
EXPECTED: dict[str, Any] = json.loads(
    (Path(__file__).parent / "compose_rendered.json").read_text(encoding="utf-8")
)
CANONICAL = {
    "PITWALL_ADMIN_SECRET": "canonical-admin-secret",
    "PITWALL_API_TOKEN": "canonical-api-token",
    "RUNPOD_API_KEY": "canonical-runpod-key",
    "POSTGRES_PASSWORD": "canonical-postgres-password",
    "REDIS_PASSWORD": "canonical-redis-password",
    "PITWALL_WEBHOOK_SECRET": "canonical-webhook-secret",
    "PITWALL_ARCHIVE_ENCRYPTION_KEY": "canonical-archive-key",
    "PITWALL_WEBHOOK_ENCRYPTION_KEYS": '{"v1": "canonical-webhook-key"}',
}
_FIELD = re.compile(r"(\w+)(?:\.(\w+))?(?:\[(\d+)\])?")


def _render(filename: str) -> dict[str, Any]:
    docker = shutil.which("docker")
    assert docker, "docker compose is required to render the Compose files"
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **CANONICAL}
    result = subprocess.run(
        [docker, "compose", "-f", str(ROOT / filename), "config", "--format", "json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return dict(json.loads(result.stdout)["services"])


@pytest.fixture(scope="module")
def rendered() -> dict[str, dict[str, Any]]:
    return {name: _render(name) for name in ("docker-compose.yml", "docker-compose.testinfra.yml")}


def _value(service: dict[str, Any], field: str) -> Any:
    match = _FIELD.fullmatch(field)
    assert match, field
    value = service[match.group(1)]
    if match.group(2):
        value = value[match.group(2)]
    if match.group(3):
        value = value[int(match.group(3))]
    if isinstance(value, dict) and "published" in value:
        return f"{value['host_ip']}:{value['published']}->{value['target']}/{value['protocol']}"
    return value


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_flagged_expression_renders_to_its_pinned_value(
    key: str, rendered: dict[str, dict[str, Any]]
) -> None:
    filename, service, field = key.split(" ")
    assert _value(rendered[filename][service], field) == EXPECTED[key]


def test_every_published_port_binds_loopback(rendered: dict[str, dict[str, Any]]) -> None:
    ports = [
        port
        for services in rendered.values()
        for service in services.values()
        for port in service.get("ports", [])
    ]
    assert len(ports) == 5
    assert {port["host_ip"] for port in ports} == {"127.0.0.1"}


def test_prod_compose_includes_the_canonical_stack_unchanged(
    rendered: dict[str, dict[str, Any]],
) -> None:
    assert _render("docker-compose.prod.yml") == rendered["docker-compose.yml"]


# service -> (networks, named volume mounts, dependencies)
WIRING = {
    "docker-compose.yml": {
        "api": (["backend", "egress"], [], ["migrate", "redis"]),
        "cost-exporter": (["backend", "egress"], [], ["migrate"]),
        "migrate": (["backend"], [], ["postgres"]),
        "postgres": (["backend"], [("postgres-data", "/var/lib/postgresql/data")], []),
        "reconciler": (
            ["backend", "egress"],
            [("retention-archive", "/var/lib/pitwall/archive")],
            ["migrate", "redis"],
        ),
        "redis": (["backend"], [("redis-data", "/data")], []),
        "webhook": (["backend", "egress"], [], ["migrate", "redis"]),
    },
    "docker-compose.testinfra.yml": {
        "postgres": (["default"], [("pitwall-test-pg", "/var/lib/postgresql/data")], []),
        "redis": (["default"], [("pitwall-test-redis", "/data")], []),
    },
}


@pytest.mark.parametrize("filename", sorted(WIRING))
def test_services_networks_volumes_and_dependencies(
    filename: str, rendered: dict[str, dict[str, Any]]
) -> None:
    services = rendered[filename]
    assert sorted(services) == sorted(WIRING[filename])
    for name, (networks, volumes, depends) in WIRING[filename].items():
        service = services[name]
        assert sorted(service.get("networks") or {}) == networks, name
        mounts = [(volume["source"], volume["target"]) for volume in service.get("volumes", [])]
        assert mounts == volumes, name
        assert sorted(service.get("depends_on") or {}) == depends, name


def test_the_backend_network_has_no_route_out() -> None:
    docker = shutil.which("docker")
    assert docker
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **CANONICAL}
    document = json.loads(
        subprocess.run(
            [
                docker,
                "compose",
                "-f",
                str(ROOT / "docker-compose.yml"),
                "config",
                "--format",
                "json",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        ).stdout
    )
    assert document["networks"]["backend"].get("internal") is True
    assert not document["networks"]["egress"].get("internal")
    assert sorted(document["volumes"]) == ["postgres-data", "redis-data", "retention-archive"]
