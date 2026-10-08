"""R2: ``pitwall gateway sync`` must not depend on the repo-only ``tools`` package."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_gateway_sync_help_runs_without_the_repo_on_sys_path(tmp_path: Path) -> None:
    """From an unrelated cwd, with the repo root removed from sys.path, the command still loads."""
    (tmp_path / "seed").mkdir()
    (tmp_path / "config").mkdir()
    fixture = REPO / "tests" / "tools" / "fixtures" / "mini-catalog.json"
    code = (
        f"import sys; sys.path[:] = [p for p in sys.path if p not in ('', {str(REPO)!r})]; "
        "from pitwall import cli; sys.exit(cli.main(['gateway', 'sync', '--version', '3.8.51', "
        f"'--from-json', {str(fixture)!r}, '--repo-root', {str(tmp_path)!r}]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert (tmp_path / "config" / "gateway-catalog.lock.json").exists()
    assert not (REPO / "config" / "gateway-catalog.lock.json").read_text().startswith("\n")


def test_sync_implementation_lives_in_the_package() -> None:
    from pitwall.gateway_catalog import sync
    from pitwall.gateway_catalog.schema import CatalogRow

    assert sync.__file__ and "/src/pitwall/" in sync.__file__
    assert CatalogRow.__module__ == "pitwall.gateway_catalog.schema"
    assert callable(sync.main)


def test_tools_wrapper_still_answers_to_python_m(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.gateway.sync_catalog", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--apply-verdicts" in result.stdout


def test_route_table_covers_every_enabled_fork_row() -> None:
    import json

    import yaml

    from pitwall.gateway_catalog.sync import FORK_BASE_URL

    routes = json.loads((REPO / "config" / "gateway-routes.json").read_text())
    assert routes["schema_version"] == 1
    seeds = yaml.safe_load((REPO / "seed" / "gateway-providers.yaml").read_text())["providers"]
    fork = [r for r in seeds if r["enabled"] and r["gateway"]["base_url"] == FORK_BASE_URL]
    assert fork, "expected enabled fork rows"
    for row in fork:
        route = routes["routes"][row["name"]]
        assert route["model_id"] == row["gateway"]["model_id"]
        assert route["base_url"].startswith(("https://", "http://"))
        assert route["base_url"] != FORK_BASE_URL
        assert route["key_required"] is (row["gateway"]["catalog"]["auth_type"] == "apikey")


def test_route_key_env_is_derived_from_the_pool() -> None:
    from pitwall.gateway_catalog.schema import CatalogRow
    from pitwall.gateway_catalog.sync import route_key_env

    def row(**changes: object) -> CatalogRow:
        values: dict[str, object] = {
            "provider": "groq",
            "model_id": "m",
            "display_name": "M",
            "monthly_tokens": 0,
            "credit_tokens": 0,
            "free_type": "recurring-daily",
            "pool_key": "groq-free",
            "tos": "ok",
            "base_url": "https://api.groq.com/openai/v1",
            "auth_type": "apikey",
        }
        values.update(changes)
        return CatalogRow(**values)  # type: ignore[arg-type]  # reason: row values come from a dict[str, object] of overrides

    assert route_key_env(row()) == "PITWALL_GATEWAY_KEY_GROQ_FREE"
    assert route_key_env(row(pool_key=None, provider="ai-horde")) == "PITWALL_GATEWAY_KEY_AI_HORDE"
    assert route_key_env(row(auth_type="none")) is None
