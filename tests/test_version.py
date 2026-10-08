"""The package version, the plugin manifests, and the changelog name one release."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pitwall

ROOT = Path(__file__).resolve().parents[1]

PLUGIN_MANIFESTS = (
    "plugins/claude/.claude-plugin/plugin.json",
    "plugins/codex/.codex-plugin/plugin.json",
    "plugins/copilot/plugin.json",
)
MARKETPLACES = (
    ".claude-plugin/marketplace.json",
    ".agents/plugins/marketplace.json",
    ".github/plugin/marketplace.json",
)


def _package_version() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject["project"]["version"])


def _load(relative: str) -> dict[str, object]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_versions_agree() -> None:
    expected = _package_version()
    assert pitwall.__version__ == expected
    for relative in PLUGIN_MANIFESTS:
        assert _load(relative)["version"] == expected, relative
    for relative in MARKETPLACES:
        plugins = _load(relative)["plugins"]
        assert isinstance(plugins, list) and plugins, relative
        for entry in plugins:
            assert entry["version"] == expected, f"{relative}: {entry['name']}"


def test_changelog_has_the_package_version() -> None:
    expected = _package_version()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{expected}]" in changelog
    assert "## History before unification" in changelog
    assert "agent-routing/v0.12.0" in changelog
    assert "gateway/v0.2.0" in changelog
