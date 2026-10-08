"""``pitwall.toml`` carries an ``[agents]`` table and ``[agents.profiles]`` that config.py validates."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pitwall.config import (
    AgentsSettings,
    ConfigFileError,
    PitwallSettings,
    agents_settings_from_toml,
)

VALID = """
[agents.profiles.defaults]
harness = "opencode"
endpointHarness = "qwen"

[agents.profiles.models.review]
model = "gpt-5.6"
harness = "codex"
"""


@pytest.fixture
def config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in tuple(os.environ):
        if name.startswith(("PITWALL_", "R2_", "RUNPOD_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "pitwall.toml"
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))
    return path


def test_agents_table_validated(config_file: Path) -> None:
    config_file.write_text(VALID, encoding="utf-8")
    PitwallSettings()  # a valid [agents] table loads beside the other settings

    parsed = agents_settings_from_toml({"profiles": {"models": {"a": {"model": "m"}}}})
    assert isinstance(parsed, AgentsSettings)
    assert parsed.profiles.models == {"a": {"model": "m"}}

    for bad in (
        "[agents]\nunknown_setting = 1\n",
        "[agents.profiles]\nmodelz = {}\n",
        "[agents.profiles]\nmodels = 3\n",
        '[agents.profiles.defaults]\nharness = "opencode"\nbogus = "x"\n',
    ):
        config_file.write_text(bad, encoding="utf-8")
        with pytest.raises(ConfigFileError, match=r"\[agents\]"):
            PitwallSettings()


def test_agents_table_optional(config_file: Path) -> None:
    config_file.write_text('pitwall_mcp_transport = "stdio"\n', encoding="utf-8")
    PitwallSettings()
    assert agents_settings_from_toml({}).profiles.models == {}
