"""A-27: the docker psql fallback targets the database ``DATABASE_URL`` names."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pitwall import db


def _fake_docker(
    monkeypatch: pytest.MonkeyPatch, *, running: bool = True, host_port: str = "5444"
) -> None:
    ports = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}]}

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        assert args[1] == "inspect"
        stdout = f"{'true' if running else 'false'} {json.dumps(ports)}\n"
        return type("Result", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(db.shutil, "which", lambda command: "/usr/bin/docker")
    monkeypatch.setattr(db.subprocess, "run", fake_run)


def test_uses_database_url_components(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_docker(monkeypatch)
    url = "postgresql://appuser:s3cret@127.0.0.1:5444/mydb"  # pragma: allowlist secret

    target = db._docker_psql(url)

    assert target is not None
    command, env = target
    assert command[:4] == ["/usr/bin/docker", "exec", "-i", "-e"]
    assert command[4] == "PGPASSWORD"
    for flag, value in (("-h", "127.0.0.1"), ("-p", "5432"), ("-U", "appuser"), ("-d", "mydb")):
        assert command[command.index(flag) + 1] == value
    assert env["PGPASSWORD"] == "s3cret"  # pragma: allowlist secret
    assert all("s3cret" not in argument for argument in command)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db.example.com:5444/mydb",
        "postgresql://u:p@127.0.0.1:6000/mydb",
    ],
)
def test_refuses_url_not_pointing_at_the_container(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _fake_docker(monkeypatch)

    assert db._docker_psql(url) is None


def test_refuses_when_container_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_docker(monkeypatch, running=False)

    assert db._docker_psql("postgresql://u:p@127.0.0.1:5444/mydb") is None
