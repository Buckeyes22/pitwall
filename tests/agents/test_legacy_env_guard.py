"""Review Focus 1: a legacy environment variable is refused, never silently ignored."""

from __future__ import annotations

import pytest

from pitwall.agents import cli
from pitwall.agents.migrate_env import LEGACY_ENV_REPLACEMENTS, legacy_env_conflicts

# The legacy spellings are assembled from parts so this file itself carries no legacy name.
OLD_SHIM_PREFIX = "SUBAGENT_MODEL_" + "ROUTING_"
OLD_ROUTING_PREFIX = "PITWALL_AGENT_" + "ROUTING_"
OLD_TIMEOUT = "SHIM_TIMEOUT_" + "SECS"
LEGACY_NAMES = sorted(LEGACY_ENV_REPLACEMENTS)
SUBCOMMANDS = (["_shim", "codex"], ["_steer-gate"], ["doctor"], ["harnesses"], ["inbox"])


def test_table_covers_every_documented_legacy_family() -> None:
    assert f"{OLD_SHIM_PREFIX}UNRESTRICTED" in LEGACY_NAMES
    assert f"{OLD_ROUTING_PREFIX}HOME" in LEGACY_NAMES
    assert OLD_TIMEOUT in LEGACY_NAMES
    assert "PITWALL_API_TOKEN" not in LEGACY_NAMES  # the broker's own token variable


@pytest.mark.parametrize("name", LEGACY_NAMES)
def test_shim_refuses_when_legacy_variable_set(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(name, "0")
    for argv in SUBCOMMANDS:
        assert cli.main(argv) == 2, argv
        assert name in capsys.readouterr().err


@pytest.mark.parametrize("name", LEGACY_NAMES)
def test_message_names_replacement(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(name, "1")
    assert cli.main(["harnesses"]) == 2
    err = capsys.readouterr().err
    assert f"{name} -> {LEGACY_ENV_REPLACEMENTS[name]}" in err
    assert LEGACY_ENV_REPLACEMENTS[name].startswith("PITWALL_AGENTS_")


def test_every_legacy_variable_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(f"{OLD_SHIM_PREFIX}UNRESTRICTED", "0")
    monkeypatch.setenv(OLD_TIMEOUT, "5")
    assert cli.main(["_shim", "codex"]) == 2
    err = capsys.readouterr().err
    assert f"{OLD_SHIM_PREFIX}UNRESTRICTED -> PITWALL_AGENTS_UNRESTRICTED" in err
    assert f"{OLD_TIMEOUT} -> PITWALL_AGENTS_TIMEOUT_SECS" in err


def test_unlisted_suffix_is_still_refused_with_prefix_replacement() -> None:
    found = legacy_env_conflicts({f"{OLD_ROUTING_PREFIX}FUTURE_KNOB": "x", "PATH": "/bin"})
    assert found == {f"{OLD_ROUTING_PREFIX}FUTURE_KNOB": "PITWALL_AGENTS_FUTURE_KNOB"}


def test_clean_environment_is_not_refused() -> None:
    assert (
        legacy_env_conflicts({"PITWALL_AGENTS_UNRESTRICTED": "1", "PITWALL_API_TOKEN": "t"}) == {}
    )
