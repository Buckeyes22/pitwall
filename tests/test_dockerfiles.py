"""Each service image builds from the frozen lock and runs one service as a non-root user.

The five Dockerfiles share one shape. The builder stage starts from the digest-pinned
Python image, installs hash-pinned requirements exported from ``uv.lock`` and the
project wheel into ``/install``. The runtime stage copies only ``/install``, removes pip,
runs as uid 10001, declares a health check, and starts the service with
``python -m pitwall.<module>`` — the module whose entry point answers ``--help``
(``tests/test_service_entry_points.py``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {
    "api": "pitwall.api",
    "cost-exporter": "pitwall.cost.exporter",
    "mcp": "pitwall.mcp",
    "reconciler": "pitwall.reconciler",
    "webhook": "pitwall.webhook_receiver",
}


def _stages(name: str) -> dict[str, str]:
    text = (ROOT / "docker" / f"Dockerfile.{name}").read_text(encoding="utf-8")
    parts = re.split(r"^FROM python:3\.14\.7-slim@sha256:[0-9a-f]{64} AS (\w+)\n", text, flags=re.M)
    assert parts[0].strip() == ""
    return dict(zip(parts[1::2], parts[2::2], strict=True))


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_builder_installs_the_frozen_lock_and_the_wheel(name: str) -> None:
    builder = _stages(name)["builder"]
    assert "uv export --frozen --no-dev" in builder
    assert "--require-hashes --prefix=/install" in builder
    assert "uv build --wheel" in builder
    assert "--no-deps --prefix=/install /tmp/dist/*.whl" in builder


@pytest.mark.parametrize(("name", "module"), sorted(IMAGES.items()))
def test_runtime_runs_one_service_unprivileged(name: str, module: str) -> None:
    stages = _stages(name)
    assert list(stages) == ["builder", "runtime"]
    runtime = stages["runtime"]
    assert "COPY --from=builder /install /usr/local" in runtime
    assert "python -m pip uninstall --yes pip" in runtime
    assert re.search(r"^USER 10001:10001$", runtime, re.M)
    assert re.search(r"^HEALTHCHECK ", runtime, re.M)
    commands = re.findall(r"^CMD (\[.*\])$", runtime, re.M)
    assert [json.loads(command) for command in commands][-1] == ["python", "-m", module]


_OS_UPGRADE = "RUN apt-get update && apt-get upgrade --yes --no-install-recommends"


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_runtime_applies_os_security_updates_before_dropping_root(name: str) -> None:
    # The scheduled Trivy scan fails on fixed OS-package CVEs the pinned base still carries.
    runtime = _stages(name)["runtime"]
    upgrade = re.search(rf"^{re.escape(_OS_UPGRADE)}\b", runtime, re.M)
    assert upgrade is not None
    user = re.search(r"^USER 10001:10001$", runtime, re.M)
    assert user is not None
    assert upgrade.start() < user.start()


def _reconciler_runtime() -> str:
    return _stages("reconciler")["runtime"]


def test_reconciler_image_has_pg_dump() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    server = re.search(r"^\s+image: postgres:(\d+)", compose, re.M)
    assert server is not None
    runtime = _reconciler_runtime()
    assert f"ARG POSTGRES_MAJOR={server.group(1)}\n" in runtime
    assert '"postgresql-client-${POSTGRES_MAJOR}"' in runtime


def test_reconciler_archive_dir_owned_by_service_user() -> None:
    runtime = _reconciler_runtime()
    assert "install -d -o 10001 -g 10001 -m 0700 /var/lib/pitwall/archive" in runtime
    assert runtime.index("/var/lib/pitwall/archive") < runtime.index("USER 10001:10001")


def test_reconciler_healthcheck_is_not_pid1() -> None:
    healthcheck = re.search(r"^HEALTHCHECK .* CMD (\[.*\])$", _reconciler_runtime(), re.M)
    assert healthcheck is not None
    command = json.loads(healthcheck.group(1))
    script = command[-1]
    assert "os.kill(1" not in script
    assert "from arq.worker import check_health" in script
    assert "from pitwall.reconciler import WorkerSettings" in script
    assert "check_health(WorkerSettings)" in script


def test_cost_exporter_image_runs_the_cost_package_module() -> None:
    text = (ROOT / "docker" / "Dockerfile.cost-exporter").read_text(encoding="utf-8")
    assert 'CMD ["python", "-m", "pitwall.cost.exporter"]' in text
    assert "pitwall.cost_exporter" not in text


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_images_install_the_base_license_profile_only(name: str) -> None:
    builder = _stages(name)["builder"]
    assert "--no-dev" in builder
    assert "--extra" not in builder and "--all-extras" not in builder


def test_container_license_doc_names_the_current_base_digest() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.api").read_text(encoding="utf-8")
    match = re.search(r"sha256:[0-9a-f]{64}", dockerfile)
    assert match is not None
    doc = (ROOT / "docs/legal/container-image-licenses.md").read_text(encoding="utf-8")
    assert match.group(0) in doc
