"""The fast suite never reaches Postgres; DB-touching tests carry the integration marker."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest

from tests import _hermetic_env

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Modules that open a real Postgres connection or shell out to psql / docker exec
# against the test database.
_DB_MODULES = (
    "tests/test_workloads.py",
    "tests/test_lease_active_readiness.py",
    "tests/test_provider_volume_secure_cloud.py",
    "tests/test_lease_state_transitions.py",
    "tests/test_canonical_gpu_constraints.py",
    "tests/test_webhook_duplicate_delivery_stress.py",
    "tests/db/test_repository.py",
    "tests/db/test_migration_indexes.py",
    "tests/db/test_cover_duplicate_rejection.py",
    "tests/db/test_alert_events_migration.py",
    "tests/db/test_config_audit_migration.py",
    "tests/db/test_cost_daily_migration.py",
    "tests/db/test_kill_log_migration.py",
    "tests/db/test_migration_full.py",
    "tests/db/test_reset_safety.py",
    "tests/db/test_runpod_templates_migration.py",
    "tests/db/test_workload_cost_columns.py",
)
# Individual connecting tests inside otherwise pure modules.
_DB_TEST_IDS = (
    "tests/test_reconcile_query.py::test_reconcile_query_selects_active_with_runpod_job_id",
    "tests/test_health_probe.py::test_health_probe_sql_selects_enabled_lb_providers",
    "tests/test_aggregate_daily.py::test_aggregate_daily_integration",
    "tests/test_actual_cost_reconcile.py::TestApplyTerminalStateIntegration",
)


def _node(keywords: dict[str, object]) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        nodeid="tests/x.py::test_x", get_closest_marker=lambda name: keywords.get(name)
    )


def test_non_integration_postgres_connect_is_refused() -> None:
    # This test is itself non-integration, so the autouse guard is live here.
    with pytest.raises(pytest.fail.Exception, match="mark it integration"):
        asyncio.run(asyncpg.connect("postgresql://u@127.0.0.1:5444/db"))
    with pytest.raises(pytest.fail.Exception, match="mark it integration"):
        asyncpg.create_pool("postgresql://u@127.0.0.1:5444/db")
    with pytest.raises(pytest.fail.Exception, match="mark it integration"):
        subprocess.run(["psql", "--version"], check=False)
    with pytest.raises(pytest.fail.Exception, match="mark it integration"):
        subprocess.run(["docker", "exec", "c", "true"], check=False)
    with (
        socket.socket() as sock,
        pytest.raises(pytest.fail.Exception, match="test_fast_suite_hermetic"),
    ):
        sock.connect(("127.0.0.1", 5444))


def test_integration_marked_tests_may_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    original_popen_init = subprocess.Popen.__init__
    monkeypatch.setenv("PITWALL_TEST_DATABASE_URL", "postgresql://real@db:5444/pitwall_test")
    with pytest.MonkeyPatch.context() as inner:
        # The autouse guard already patched this test; the integration policy must
        # add no further patching and must hand the test URL to DATABASE_URL.
        before = asyncpg.connect
        _hermetic_env.apply_postgres_policy(_node({"integration": object()}), inner)
        assert asyncpg.connect is before
        assert subprocess.Popen.__init__ is original_popen_init
        assert os.environ["DATABASE_URL"] == "postgresql://real@db:5444/pitwall_test"


def test_every_db_module_is_integration_marked() -> None:
    files = [*_DB_MODULES, *sorted({t.split("::")[0] for t in _DB_TEST_IDS})]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "not integration", *files],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    collected = [line for line in result.stdout.splitlines() if "::" in line]
    leaked = [
        line
        for line in collected
        if line.split("::")[0] in _DB_MODULES
        or any(line.startswith(test_id) for test_id in _DB_TEST_IDS)
    ]
    assert leaked == []


def test_hermetic_placeholder_is_unreachable() -> None:
    placeholder = urlparse(_hermetic_env.HERMETIC_PLACEHOLDER_DATABASE_URL)
    assert placeholder.port not in {5432, 5444}
    assert "pitwall_test" not in placeholder.path


def test_marker_checks_ignore_a_checkout_directory_named_like_a_marker(tmp_path: Path) -> None:
    # pytest puts parent directory names in item.keywords, so a checkout named
    # "integration" must not make DB modules skip the integration marker.
    checkout = tmp_path / "integration"
    checkout.mkdir()
    for name in ("pyproject.toml", "conftest.py"):
        if (_REPO_ROOT / name).exists():
            shutil.copy2(_REPO_ROOT / name, checkout / name)
    shutil.copytree(
        _REPO_ROOT / "tests", checkout / "tests", ignore=shutil.ignore_patterns("__pycache__")
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            "-m",
            "not integration",
            "tests/db/test_repository.py",
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert "deselected" in result.stdout, result.stdout[-2000:]
    assert not [line for line in result.stdout.splitlines() if "::" in line]
