"""Agents tests can never run a real host CLI or touch the real home directory."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

HOST_CLIS = (
    "claude",
    "codex",
    "opencode",
    "copilot",
    "gh",
    "kimi",
    "grok",
    "qwen",
    "pi",
    "hermes",
    "cline",
    "goose",
    "agy",
    "muse",
    "dsh",
)
REFUSAL = "real host CLI invoked in a test"
REFUSAL_EXIT = 97


@pytest.fixture(scope="session")
def host_cli_stubs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A directory of executables that fail loudly, one per host CLI name."""
    directory = tmp_path_factory.mktemp("host-cli-stubs")
    for name in HOST_CLIS:
        stub = directory / name
        stub.write_text(f'#!/bin/sh\necho "{REFUSAL}: {name}" >&2\nexit {REFUSAL_EXIT}\n')
        stub.chmod(0o755)
    return directory


@pytest.fixture(autouse=True)
def isolated_host_environment(
    host_cli_stubs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point HOME and XDG at a temporary tree and put failing host CLI stubs first on PATH.

    A test that stubs a harness puts its own directory earlier on the ``PATH`` it passes, so it is
    unaffected; a test that forgets to stub one gets exit 97 instead of a real binary.
    """
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("PATH", f"{host_cli_stubs}{os.pathsep}{os.environ.get('PATH', '')}")
    yield


# Modules that exercise platform behaviour and therefore also run on the macOS CI job.
# Criterion: the module spawns subprocesses or shells (subprocess, Popen, /bin/sh, /bin/bash, shim
# runs), sends signals or kills processes or process groups (signal, os.kill, os.killpg,
# start_new_session, ps), asserts or sets file permissions (chmod, st_mode, S_IMODE), creates
# symlinks, or depends on OS semantics (os.pipe, sys.platform, the GNU-only timeout/gtimeout
# lookup). Modules that only exercise pure logic, JSON, or loopback HTTP on any OS are left out.
# The list is explicit, so selection never depends on the machine that collects it.
MACOS_MODULES = frozenset(
    {
        "test_answer_cli",
        "test_capability_inventory",
        "test_channel_broker",
        "test_channel_chaos",
        "test_channel_config",
        "test_channel_ledger",
        "test_channel_steer_integration",
        "test_channel_tier1_integration",
        "test_claude_tripwires",
        "test_client_rehearsal",
        "test_discovery",
        "test_dispatch",
        "test_dispatch_contract",
        "test_dispatch_import_weight",
        "test_distill_source",
        "test_doctor",
        "test_doctor_hooks",
        "test_endpoint_slots",
        "test_graceful_abort",
        "test_harnesses",
        "test_host_cli_isolation",
        "test_inbox_cli",
        "test_install",
        "test_journeys",
        "test_live_endpoint",
        "test_mailbox",
        "test_managed_channel_edges",
        "test_mcp_event_channel",
        "test_mcp_registration",
        "test_mcp_server",
        "test_migrate",
        "test_parity",
        "test_pause_contract",
        "test_pitwall",
        "test_pitwall_sync",
        "test_plugin_identity",
        "test_process",
        "test_profiles_toml",
        "test_provider_adapters",
        "test_provider_setup",
        "test_review_fixes_channel",
        "test_route_probe",
        "test_route_sync",
        "test_routes",
        "test_routes_discover",
        "test_routes_setup",
        "test_run_store",
        "test_runs_resume",
        "test_scheduler",
        "test_self_heal",
        "test_shim_contract",
        "test_steer_cli",
        "test_steer_gate",
        "test_steer_gate_fail_closed",
        "test_usage_cache",
        "test_workflow",
        "test_workflow_all_harnesses",
        "test_workflow_channel_e2e",
        "test_workspace",
    }
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every test in a platform-sensitive module with ``macos``."""
    for item in items:
        if item.path.parent == Path(__file__).parent and item.path.stem in MACOS_MODULES:
            item.add_marker(pytest.mark.macos)
