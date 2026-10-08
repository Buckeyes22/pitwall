"""The Pi pin has one source, harness-installers.json; everything else is derived or checked."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pitwall.workbench import doctor
from pitwall.workbench.comparison import runner
from pitwall.workbench.hosted import acceptance

ROOT = Path(__file__).resolve().parents[2]
INSTALLERS = ROOT / "src/pitwall/agents/resources/config/harness-installers.json"
PI_PACKAGE = "@earendil-works/pi-coding-agent"
SUBAGENTS_PACKAGE = "@tintinweb/pi-subagents"


def _pin() -> tuple[str, str]:
    platforms = json.loads(INSTALLERS.read_text())["harnesses"]["pi"]["platforms"]
    pins = {(p["version"], p["extraPackages"][0]["version"]) for p in platforms.values()}
    assert len(pins) == 1, "darwin and linux must pin the same Pi versions"
    return pins.pop()


def _versions(text: str, package: str) -> list[str]:
    return re.findall(re.escape(package) + r"@(\d+\.\d+\.\d+)", text)


def test_workbench_constants_come_from_the_installer_json() -> None:
    pi, subagents = _pin()
    assert pi == doctor.PINNED_PI_VERSION
    assert subagents == doctor.PINNED_SUBAGENTS_VERSION
    assert f"{PI_PACKAGE}@{pi}" == acceptance.RUNTIME


def test_comparison_runner_labels_use_the_pin() -> None:
    pi, subagents = _pin()
    assert f"{PI_PACKAGE}@{pi}" == runner.STOCK_LABEL
    assert f"{SUBAGENTS_PACKAGE}@{subagents}" == runner.TINTIN_LABEL


def test_workbench_source_has_no_pin_literal() -> None:
    pi, subagents = _pin()
    for path in (ROOT / "src/pitwall/workbench").rglob("*.py"):
        text = path.read_text()
        assert f"@{pi}" not in text, path
        assert f'"{pi}"' not in text, path
        assert f'"{subagents}"' not in text, path


def test_ci_install_steps_use_the_pin() -> None:
    pi, subagents = _pin()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    # CI installs the Pi packages only from the committed lock, whose manifest carries the pin.
    assert ci.count("npm ci --prefix tools/pi-deps --ignore-scripts") == 3
    assert "--no-package-lock" not in ci
    deps = json.loads((ROOT / "tools/pi-deps/package.json").read_text())["dependencies"]
    for package in (PI_PACKAGE, "@earendil-works/pi-ai", "@earendil-works/pi-tui"):
        assert deps[package] == pi, package
    assert deps[SUBAGENTS_PACKAGE] == subagents


def test_docs_state_the_pin() -> None:
    pi, subagents = _pin()
    for name in ("docs/agents/harness-cli-setup.md", "docs/sdlc/25-pi-workbench.md"):
        text = (ROOT / name).read_text()
        assert set(_versions(text, PI_PACKAGE)) == {pi}, name
        assert set(_versions(text, SUBAGENTS_PACKAGE)) == {subagents}, name
    sdlc = (ROOT / "docs/sdlc/25-pi-workbench.md").read_text()
    assert "The pin is stated once" not in sdlc
    assert "harness-installers.json" in sdlc


EXTENSION_FILES = (
    "native-extension.ts",
    "native-extension.js",
    "native-profile.ts",
    "native-profile.js",
)
EXTENSION_VERSION = re.compile(r'\bversion\s*(?:!==|===|:)\s*"(\d+\.\d+\.\d+)"')


def _extension_literals(directory: Path) -> dict[str, list[str]]:
    return {
        name: EXTENSION_VERSION.findall((directory / name).read_text()) for name in EXTENSION_FILES
    }


def _mismatched_extension_files(directory: Path, subagents: str) -> list[str]:
    literals = _extension_literals(directory)
    return [name for name, found in literals.items() if not found or set(found) != {subagents}]


def test_pi_extensions_validate_the_pinned_subagents_version() -> None:
    _, subagents = _pin()
    directory = ROOT / "src/pitwall/workbench/pi_extensions"
    assert _mismatched_extension_files(directory, subagents) == []


def test_extension_pin_check_detects_a_differing_literal(tmp_path: Path) -> None:
    _, subagents = _pin()
    source = ROOT / "src/pitwall/workbench/pi_extensions"
    for name in EXTENSION_FILES:
        (tmp_path / name).write_text((source / name).read_text())
    assert _mismatched_extension_files(tmp_path, subagents) == []
    stale = tmp_path / "native-profile.ts"
    stale.write_text(stale.read_text().replace(f'"{subagents}"', '"0.0.1"'))
    assert _mismatched_extension_files(tmp_path, subagents) == ["native-profile.ts"]
    empty = tmp_path / "native-extension.js"
    empty.write_text("")
    assert _mismatched_extension_files(tmp_path, subagents) == [
        "native-extension.js",
        "native-profile.ts",
    ]
