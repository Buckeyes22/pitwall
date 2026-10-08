"""Release-tier test fixtures for Pitwall's public-alpha validation harness.

This package holds the tiered release tests:
  - dry-run    : validate configuration without spending
  - sovereignty: validate data residency / region constraints
  - BGE-M3 smoke: validate live BGE-M3 endpoint exit criteria
  - kill drill : validate L15 kill-switch separation

Release tests are gated behind the ``release`` marker. They are skipped by
default and must be run explicitly with ``pytest -m release`` or via the
CI release workflow.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "release: marks a test as a public-alpha validation tier (skipped by default)",
    )
    config.addinivalue_line(
        "markers",
        "journey_harness: runs only inside run-user-journeys.sh (PITWALL_JOURNEY_HARNESS=1)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Journeys that reset the disposable database or read a harness run's results belong
    # to scripts/release/run-user-journeys.sh, which opts in; every other run leaves them.
    if os.environ.get("PITWALL_JOURNEY_HARNESS") != "1":
        harness_only = [
            item for item in items if item.get_closest_marker("journey_harness") is not None
        ]
        if harness_only:
            config.hook.pytest_deselected(items=harness_only)
            items[:] = [
                item for item in items if item.get_closest_marker("journey_harness") is None
            ]
    if config.getoption("-m", default=None) in {"release", "release and not live"}:
        return
    for item in items:
        if "release" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="release tier: run with -m release"))


_DEAD_PROXY = "http://127.0.0.1:9"


@pytest.fixture(scope="module")
def journey_env() -> dict[str, str]:
    """A freshly seeded disposable database, with every outbound HTTP call refused locally.

    Mirrors the harness's J07/J10 seeding (``db reset``, ``db migrate``, ``init
    --non-interactive``). Provider calls go through a dead loopback proxy and dead RunPod REST
    URLs, so a tool that needs a live provider returns its typed unavailable code and nothing
    leaves the machine. Credentials are placeholders.
    """
    import os
    import subprocess

    database_url = os.environ.get("DATABASE_URL")
    redis_url = os.environ.get("REDIS_URL")
    if not database_url or not redis_url:
        pytest.fail("this journey needs the disposable DATABASE_URL and REDIS_URL")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "RUNPOD_API_KEY": "journey-placeholder-key",
        "PITWALL_MCP_TRANSPORT": "stdio",
        "HTTP_PROXY": _DEAD_PROXY,
        "HTTPS_PROXY": _DEAD_PROXY,
        "ALL_PROXY": _DEAD_PROXY,
        "NO_PROXY": "127.0.0.1,localhost",
        "RUNPOD_REST_API_URL": f"{_DEAD_PROXY}/v2",
        "RUNPOD_REST_V1_API_URL": f"{_DEAD_PROXY}/v1",
        "RUNPOD_S3_ACCESS_KEY": "journey-s3-access-placeholder",
        "RUNPOD_S3_SECRET_KEY": "journey-s3-secret-placeholder",
        "J34_REGISTRY_PASSWORD": "journey-registry-placeholder",
    }
    for argv in (
        ["db", "reset", "--force"],
        ["db", "migrate"],
        ["init", "--non-interactive"],
    ):
        subprocess.run(
            ["uv", "run", "--frozen", "pitwall", *argv],
            env=env,
            check=True,
            capture_output=True,
            timeout=300,
        )
    return env


@pytest.fixture(scope="module")
def registry_journey_env(
    journey_env: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, str]:
    """``journey_env`` on the Postgres registry backend.

    The registry backend is an explicit choice (``DATABASE_URL`` alone never selects it), so the
    journeys that exercise it say so in pitwall.toml, as an operator would.
    """
    config_file = tmp_path_factory.mktemp("journey-config") / "pitwall.toml"
    config_file.write_text('[personal]\nbackend = "registry"\n', encoding="utf-8")
    return {**journey_env, "PITWALL_CONFIG_FILE": str(config_file)}
