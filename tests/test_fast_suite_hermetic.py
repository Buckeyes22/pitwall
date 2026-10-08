"""The fast suite never reaches Postgres; DB-touching tests carry the integration marker."""

from __future__ import annotations

import ast
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
from tests.hang_guard import HANG_GUARD_SECS

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
        timeout=HANG_GUARD_SECS,
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


# Fixtures that hand a test a live Postgres: the integration lane's per-test schema pool and the
# migrated-schema fixtures in tests/db/conftest.py.
_DB_FIXTURES = frozenset({"pg_pool", "migrated_schema", "db_catalog", "db_sandbox"})


def _integration_marked(decorators: list[ast.expr]) -> bool:
    return any("mark.integration" in ast.unparse(decorator) for decorator in decorators)


def _module_integration_marked(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        )
        and "mark.integration" in ast.unparse(node.value)
        for node in tree.body
    )


def _requests_db_fixture(node: ast.AST) -> bool:
    return isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
        argument.arg in _DB_FIXTURES for argument in node.args.args + node.args.kwonlyargs
    )


def _unmarked_tests(body: list[ast.stmt], inherited: bool) -> list[str]:
    unmarked: list[str] = []
    for node in body:
        if isinstance(node, ast.ClassDef):
            class_marked = (
                inherited
                or _integration_marked(node.decorator_list)
                or any(
                    isinstance(item, ast.Assign) and "mark.integration" in ast.unparse(item.value)
                    for item in node.body
                )
            )
            unmarked += [f"{node.name}.{name}" for name in _unmarked_tests(node.body, class_marked)]
        elif (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test")
            and not (inherited or _integration_marked(node.decorator_list))
        ):
            unmarked.append(node.name)
    return unmarked


def test_every_module_using_a_database_fixture_is_integration_marked() -> None:
    """A module that takes a live-database fixture is marked ``integration`` (module-level
    ``pytestmark``), or else each of its tests carries the marker itself."""
    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / "tests").rglob("*.py")):
        if path.name == "conftest.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not any(_requests_db_fixture(node) for node in ast.walk(tree)):
            continue
        offenders += [
            f"{path.relative_to(_REPO_ROOT)}::{name}"
            for name in _unmarked_tests(tree.body, _module_integration_marked(tree))
        ]
    assert offenders == [], (
        "tests in modules that use a live-database fixture must be integration-marked "
        f"(add `pytestmark = pytest.mark.integration`): {offenders}"
    )


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

    def collect(marker_expression: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:randomly",
                "-m",
                marker_expression,
                "tests/db/test_repository.py",
            ],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=HANG_GUARD_SECS,
            check=False,
        )

    def collected(result: subprocess.CompletedProcess[str]) -> list[str]:
        return [line for line in result.stdout.splitlines() if "::" in line]

    # Exit 0 (something collected) or 5 (everything deselected) are the only clean outcomes; 2 or
    # 4 would be a collection error, which must not read as "nothing leaked".
    fast = collect("not integration")
    assert fast.returncode in (0, 5), fast.stdout[-2000:] + fast.stderr[-2000:]
    assert collected(fast) == []
    # The module's tests are really there, and selected by the integration marker alone.
    integration = collect("integration")
    assert integration.returncode == 0, integration.stdout[-2000:] + integration.stderr[-2000:]
    assert collected(integration)
