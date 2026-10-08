"""Tests for the source-declaration operations inventory.

Discovery is grounded in the checked-in Dockerfiles, root Compose files,
migration files, and operational scripts: adding or removing a service, changing
a base image, or changing migration bytes changes the rows and contract hashes;
malformed known YAML fails; Compose ``${...}`` expressions stay raw and
explicitly unresolved; env values, URL userinfo, and ambient secrets are never
recorded. Nothing is executed and no Compose/docker command is run.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools.release_acceptance import ops_inventory

COMPOSE = """\
name: ${PROJECT_NAME:-pitwall}
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD:?required}
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - data:/var/lib/postgresql/data
  api:
    build:
      context: .
      dockerfile: docker/Dockerfile.api
    depends_on:
      db:
        condition: service_healthy
    environment:
      DB_PASSWORD: ${DB_PASSWORD:?required}
      DATABASE_URL: postgresql://user:${DB_PASSWORD}@db:5432/pitwall
    ports:
      - "127.0.0.1:8080:8080"
    profiles:
      - serve
volumes:
  data:
"""

DOCKERFILE = """\
ARG PYTHON_IMAGE=python:3.14-slim
FROM $PYTHON_IMAGE AS build
RUN true
FROM $UNPINNED AS runtime
ENTRYPOINT ["python", "-m", "pitwall"]
CMD ["pitwall", "serve"]
"""

_ENV_GUARD = """\
import os


class _NoReadEnviron:
    def _no(self, *args, **kwargs):
        raise AssertionError("ambient environment read")

    get = __getitem__ = __contains__ = __iter__ = _no
    keys = values = items = copy = setdefault = _no


os.environ = _NoReadEnviron()
from tools.release_acceptance import ops_inventory

rows = ops_inventory.discover(ops_inventory.ROOT)
report = ops_inventory.discover_with_issues(ops_inventory.ROOT)
assert report["surfaces"] == rows
print("ENV_GUARD_OK", len(rows))
"""


def _root(
    tmp_path: Path,
    compose: str = COMPOSE,
    dockerfile: str = DOCKERFILE,
    migrations: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
) -> Path:
    (tmp_path / "docker").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docker-compose.yml").write_text(compose, encoding="utf-8")
    (tmp_path / "docker" / "Dockerfile.api").write_text(dockerfile, encoding="utf-8")
    for name, body in (migrations or {}).items():
        target = tmp_path / "db" / "migrations" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    for name, body in (scripts or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tmp_path


def _ids(rows: list[dict]) -> list[str]:
    return [row["surface_id"] for row in rows]


def _row(rows: list[dict], surface_id: str) -> dict:
    return next(row for row in rows if row["surface_id"] == surface_id)


def _contracts(rows: list[dict]) -> dict[str, str]:
    return {row["surface_id"]: row["metadata"]["contract_sha256"] for row in rows}


def test_real_repo_declarations_are_discovered() -> None:
    rows = ops_inventory.discover(ops_inventory.ROOT)
    ids = _ids(rows)
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert all(row["kind"] in {"install", "service", "ops"} for row in rows)
    for surface_id in (
        "install:dockerfile:docker/Dockerfile.api:builder",
        "install:dockerfile:docker/Dockerfile.api:runtime",
        "service:compose:docker-compose.yml:api",
        "service:compose:docker-compose.yml:migrate",
        "service:compose:docker-compose.yml:postgres",
        "service:compose:docker-compose.yml:redis",
        "service:compose-include:docker-compose.prod.yml:docker-compose.yml",
        "install:migration:db/migrations/0001_capabilities.sql",
        "ops:script:scripts/release/smoke_compose.sh",
        "ops:script:src/pitwall/ops/backup_drill.py",
        "ops:volume:docker-compose.yml:postgres-data",
    ):
        assert surface_id in ids
    migrations = [row for row in rows if row["metadata"]["category"] == "migration"]
    assert len(migrations) >= 34
    for row in rows:
        metadata = row["metadata"]
        assert re.fullmatch(r"[0-9a-f]{64}", metadata["source_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", metadata["contract_sha256"])
        assert re.fullmatch(r"[^\s:]+:\d+", row["source"])


def test_added_and_removed_compose_service_changes_discovery(tmp_path: Path) -> None:
    baseline = ops_inventory.discover(_root(tmp_path))
    assert "service:compose:docker-compose.yml:api" in _ids(baseline)
    added = ops_inventory.discover(
        _root(tmp_path, COMPOSE.replace("  api:", "  ghost:\n    image: busybox\n  api:"))
    )
    assert "service:compose:docker-compose.yml:ghost" in _ids(added)
    removed = ops_inventory.discover(
        _root(tmp_path, COMPOSE.replace("      - serve\n", "").replace("  api:", "  apix:"))
    )
    assert "service:compose:docker-compose.yml:api" not in _ids(removed)
    api_id = "service:compose:docker-compose.yml:api"
    assert _contracts(added)[api_id] != _contracts(baseline)[api_id]


def test_dependency_port_profile_and_volume_metadata(tmp_path: Path) -> None:
    rows = ops_inventory.discover(_root(tmp_path))
    api = _row(rows, "service:compose:docker-compose.yml:api")
    assert api["metadata"]["depends_on"] == ["db"]
    assert api["metadata"]["depends_on_conditions"] == {"db": "service_healthy"}
    assert api["metadata"]["ports"] == ["127.0.0.1:8080:8080"]
    assert api["metadata"]["profiles"] == ["serve"]
    db = _row(rows, "service:compose:docker-compose.yml:db")
    assert db["metadata"]["volumes"] == ["data:/var/lib/postgresql/data"]
    volume = _row(rows, "ops:volume:docker-compose.yml:data")
    assert volume["kind"] == "ops" and volume["operation"] == "volume"
    changed = ops_inventory.discover(
        _root(tmp_path, COMPOSE.replace("service_healthy", "service_started"))
    )
    assert (
        _contracts(changed)["service:compose:docker-compose.yml:api"]
        != api["metadata"]["contract_sha256"]
    )


def test_dockerfile_base_image_resolution_and_unresolved(tmp_path: Path) -> None:
    rows = ops_inventory.discover(_root(tmp_path))
    build = _row(rows, "install:dockerfile:docker/Dockerfile.api:build")
    assert build["metadata"]["base_image"] == "python:3.14-slim"
    assert build["metadata"]["base_unresolved"] is False
    runtime = _row(rows, "install:dockerfile:docker/Dockerfile.api:runtime")
    assert runtime["metadata"]["base_image"] is None
    assert runtime["metadata"]["base_expression"] == "$UNPINNED"
    assert runtime["metadata"]["base_unresolved"] is True
    assert runtime["source"] == "docker/Dockerfile.api:4"
    entrypoint = _row(rows, "service:entrypoint:docker/Dockerfile.api:runtime:entrypoint")
    assert entrypoint["metadata"]["argv"] == '["python", "-m", "pitwall"]'
    cmd = _row(rows, "service:entrypoint:docker/Dockerfile.api:runtime:cmd")
    assert cmd["kind"] == "service" and cmd["operation"] == "entrypoint"
    bumped = ops_inventory.discover(
        _root(tmp_path, dockerfile=DOCKERFILE.replace("python:3.14-slim", "python:3.14.7-slim"))
    )
    assert (
        _contracts(bumped)["install:dockerfile:docker/Dockerfile.api:build"]
        != build["metadata"]["contract_sha256"]
    )


def test_dockerfile_stage_local_arg_does_not_resolve_later_from(tmp_path: Path) -> None:
    dockerfile = """\
ARG BASE=python:3.14-slim
FROM $BASE AS builder
ARG STAGE_ONLY=private:latest
RUN true
FROM $STAGE_ONLY AS runtime
CMD ["python"]
"""
    rows = ops_inventory.discover(_root(tmp_path, dockerfile=dockerfile))
    runtime = _row(rows, "install:dockerfile:docker/Dockerfile.api:runtime")
    assert runtime["metadata"]["base_image"] is None
    assert runtime["metadata"]["base_expression"] == "$STAGE_ONLY"
    assert runtime["metadata"]["base_unresolved"] is True


def test_dockerfile_literal_base_and_stage_alias_are_resolved(tmp_path: Path) -> None:
    dockerfile = """\
ARG BASE=alpine:3.22
FROM alpine:3.22 AS builder
RUN true
FROM builder AS runtime
CMD ["true"]
FROM ${BASE}:suffix AS composite
"""
    rows = ops_inventory.discover(_root(tmp_path, dockerfile=dockerfile))
    builder = _row(rows, "install:dockerfile:docker/Dockerfile.api:builder")
    runtime = _row(rows, "install:dockerfile:docker/Dockerfile.api:runtime")
    assert builder["metadata"]["base_image"] == "alpine:3.22"
    assert builder["metadata"]["base_unresolved"] is False
    assert runtime["metadata"]["base_image"] == "builder"
    assert runtime["metadata"]["base_unresolved"] is False
    composite = _row(rows, "install:dockerfile:docker/Dockerfile.api:composite")
    assert composite["metadata"]["base_image"] is None
    assert composite["metadata"]["base_expression"] == "${BASE}:suffix"
    assert composite["metadata"]["base_unresolved"] is True


def test_dockerfile_duplicate_global_arg_uses_later_declaration(tmp_path: Path) -> None:
    dockerfile = """\
ARG BASE=first:latest
ARG BASE=second:latest
FROM $BASE AS runtime
"""
    rows = ops_inventory.discover(_root(tmp_path, dockerfile=dockerfile))
    runtime = _row(rows, "install:dockerfile:docker/Dockerfile.api:runtime")
    assert runtime["metadata"]["base_image"] == "second:latest"
    assert runtime["metadata"]["base_unresolved"] is False


def test_migration_content_drift_at_unchanged_path(tmp_path: Path) -> None:
    first = ops_inventory.discover(
        _root(tmp_path, migrations={"0001_init.sql": "CREATE TABLE a();"})
    )
    row = _row(first, "install:migration:db/migrations/0001_init.sql")
    assert row["metadata"]["sequence"] == 1
    second = ops_inventory.discover(
        _root(tmp_path, migrations={"0001_init.sql": "CREATE TABLE b();"})
    )
    drifted = _row(second, "install:migration:db/migrations/0001_init.sql")
    assert drifted["source"] == row["source"] == "db/migrations/0001_init.sql:1"
    assert drifted["metadata"]["source_sha256"] != row["metadata"]["source_sha256"]
    assert drifted["metadata"]["contract_sha256"] != row["metadata"]["contract_sha256"]


def test_malformed_known_yaml_fails_discovery(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "docker-compose.yml").write_text("services:\n  api: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid YAML"):
        ops_inventory.discover(root)
    (root / "docker-compose.yml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        ops_inventory.discover(root)
    (root / "docker-compose.yml").write_text("services:\n  api: 7\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        ops_inventory.discover(root)
    (root / "docker-compose.yml").unlink()
    with pytest.raises(ValueError, match="required compose file"):
        ops_inventory.discover(root)


def test_duplicate_compose_mapping_keys_fail_discovery(tmp_path: Path) -> None:
    duplicate = """\
services:
  api:
    image: one
    image: two
"""
    with pytest.raises(ValueError, match=r"invalid YAML \(DuplicateKeyError\) at line 4"):
        ops_inventory.discover(_root(tmp_path, compose=duplicate))


def test_secret_canary_values_never_appear(tmp_path: Path) -> None:
    canary = "canary-secret-9c1e"
    compose = COMPOSE.replace(
        "postgresql://user:${DB_PASSWORD}@db:5432/pitwall",
        f"postgresql://user:{canary}@db:5432/pitwall",
    ).replace("${DB_PASSWORD:?required}", canary)
    rows = ops_inventory.discover(_root(tmp_path, compose))
    report = ops_inventory.discover_with_issues(_root(tmp_path, compose))
    dumped = json.dumps([rows, report])
    assert canary not in dumped
    api = _row(rows, "service:compose:docker-compose.yml:api")
    assert api["metadata"]["env_names"] == ["DATABASE_URL", "DB_PASSWORD"]
    db = _row(rows, "service:compose:docker-compose.yml:db")
    assert db["metadata"]["env_names"] == ["POSTGRES_PASSWORD"]


def test_secret_canary_in_literal_command_and_dockerfile_is_redacted(tmp_path: Path) -> None:
    canary = "canary-secret-9c1e"
    compose = COMPOSE.replace(
        "volumes:\n  data:",
        f'''  worker:\n    image: alpine\n    command: ["--token", "{canary}"]\nvolumes:\n  data:''',
    )
    dockerfile = DOCKERFILE.replace('CMD ["pitwall", "serve"]', f'CMD ["--token", "{canary}"]')
    rows = ops_inventory.discover(_root(tmp_path, compose=compose, dockerfile=dockerfile))
    report = ops_inventory.discover_with_issues(
        _root(tmp_path, compose=compose, dockerfile=dockerfile)
    )
    dumped = json.dumps([rows, report])
    assert canary not in dumped
    worker = _row(rows, "service:compose:docker-compose.yml:worker")
    assert worker["metadata"]["command"] == ["--token", "<redacted>"]
    cmd = _row(rows, "service:entrypoint:docker/Dockerfile.api:runtime:cmd")
    assert "<redacted>" in cmd["metadata"]["argv"]


def test_symlinked_declarations_outside_root_are_ignored(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "compose.yml").write_text(
        "services:\n  escape:\n    image: alpine\n", encoding="utf-8"
    )
    (outside / "Dockerfile.escape").write_text("FROM alpine\n", encoding="utf-8")
    (outside / "0002_escape.sql").write_text("CREATE TABLE escape();\n", encoding="utf-8")
    (outside / "backup_escape.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    root = _root(
        tmp_path,
        compose=COMPOSE.replace(
            "name: ${PROJECT_NAME:-pitwall}", "include: included.yml\nname: pitwall"
        ),
    )
    (root / "included.yml").symlink_to(outside / "compose.yml")
    (root / "compose-escape.yml").symlink_to(outside / "compose.yml")
    (root / "docker" / "Dockerfile.escape").symlink_to(outside / "Dockerfile.escape")
    migration_dir = root / "db" / "migrations"
    migration_dir.mkdir(parents=True)
    (migration_dir / "0002_escape.sql").symlink_to(outside / "0002_escape.sql")
    scripts_dir = root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "backup_escape.sh").symlink_to(outside / "backup_escape.sh")

    report = ops_inventory.discover_with_issues(root)
    rows = report["surfaces"]
    ids = _ids(rows)
    assert "service:compose:compose-escape.yml:escape" not in ids
    assert "service:compose:included.yml:escape" not in ids
    assert "install:dockerfile:docker/Dockerfile.escape:stage1" not in ids
    assert "install:migration:db/migrations/0002_escape.sql" not in ids
    assert "ops:script:scripts/backup_escape.sh" not in ids
    containment = [issue for issue in report["issues"] if issue["topic"] == "source_containment"]
    assert len(containment) == 5
    assert {issue["status"] for issue in containment} == {"unresolved"}
    assert {issue["source"] for issue in containment} >= {
        "compose-escape.yml:1",
        "docker-compose.yml:1",
        "docker/Dockerfile.escape:1",
        "db/migrations/0002_escape.sql:1",
        "scripts/backup_escape.sh:1",
    }


def test_dynamic_compose_expressions_stay_raw_and_unresolved(tmp_path: Path) -> None:
    rows = ops_inventory.discover(_root(tmp_path))
    api = _row(rows, "service:compose:docker-compose.yml:api")
    assert api["metadata"]["dynamic_unresolved"] is True
    assert "environment.DATABASE_URL" in api["metadata"]["dynamic_expressions"]
    report = ops_inventory.discover_with_issues(_root(tmp_path))
    dynamic = [
        issue for issue in report["issues"] if issue["topic"] == "dynamic_compose_expression"
    ]
    assert dynamic
    assert all(issue["status"] == "unresolved" for issue in dynamic)
    assert all("no Compose substitution" in issue["reason"] for issue in dynamic)


def test_added_operational_and_release_scripts_are_discovered(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        scripts={
            "scripts/restore_db.sh": "#!/bin/sh\n",
            "scripts/release/smoke_compose.sh": "#!/bin/sh\n",
            "scripts/unrelated_tool.py": "print('x')\n",
            "src/pitwall/ops/backup_drill.py": "BACKUP = 1\n",
        },
    )
    ids = _ids(ops_inventory.discover(root))
    assert "ops:script:scripts/restore_db.sh" in ids
    assert "ops:script:scripts/release/smoke_compose.sh" in ids
    assert "ops:script:src/pitwall/ops/backup_drill.py" in ids
    assert "ops:script:scripts/unrelated_tool.py" not in ids
    (root / "scripts/restore_db.sh").unlink()
    assert "ops:script:scripts/restore_db.sh" not in _ids(ops_inventory.discover(root))


def test_report_is_deterministic_and_issues_explicit(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = ops_inventory.discover(root)
    assert first == ops_inventory.discover(root)
    report = ops_inventory.discover_with_issues(root)
    assert report["schema_version"] == ops_inventory.SCHEMA_VERSION
    assert report["surfaces"] == first
    issues = {issue["topic"]: issue for issue in report["issues"]}
    for topic in ("runtime_execution", "runtime_substitution", "secret_values", "docs_runbooks"):
        assert issues[topic]["status"] == "unresolved"
        assert "UNRESOLVED" in issues[topic]["reason"]
    assert issues["scope"]["status"] == "partial"


def test_discovery_never_reads_ambient_environment() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _ENV_GUARD],
        cwd=ops_inventory.ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ENV_GUARD_OK")
