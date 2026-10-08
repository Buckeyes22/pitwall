"""CI's Pi test dependencies are locked and match the installer pins."""

from __future__ import annotations

import json
from pathlib import Path

from pitwall.workbench.pi_pin import PINNED_PI_VERSION, PINNED_SUBAGENTS_VERSION

ROOT = Path(__file__).resolve().parents[2]
DEPS = ROOT / "tools" / "pi-deps"


def test_manifest_pins_the_installer_versions_exactly() -> None:
    deps = json.loads((DEPS / "package.json").read_text(encoding="utf-8"))["dependencies"]
    assert deps == {
        "@earendil-works/pi-ai": PINNED_PI_VERSION,
        "@earendil-works/pi-coding-agent": PINNED_PI_VERSION,
        "@earendil-works/pi-tui": PINNED_PI_VERSION,
        "@tintinweb/pi-subagents": PINNED_SUBAGENTS_VERSION,
    }


def test_lock_resolves_the_manifest_and_records_integrity() -> None:
    lock = json.loads((DEPS / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["lockfileVersion"] == 3
    packages = lock["packages"]
    assert packages["node_modules/@earendil-works/pi-coding-agent"]["version"] == PINNED_PI_VERSION
    for path, entry in packages.items():
        if path and not entry.get("link"):
            assert entry.get("integrity", "").startswith("sha512-"), path


def test_ci_installs_from_the_lock_only() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--no-package-lock" not in ci
    assert ci.count("npm ci --prefix tools/pi-deps --ignore-scripts") == 3
