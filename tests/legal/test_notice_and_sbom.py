"""NOTICE carries every third-party permission notice; the SBOM describes this version."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTICE = (ROOT / "NOTICE").read_text(encoding="utf-8")


def test_notice_reproduces_the_omniroute_mit_notice() -> None:
    lock = json.loads((ROOT / "config/gateway-catalog.lock.json").read_text(encoding="utf-8"))
    assert NOTICE.count("Permission is hereby granted, free of charge") == 2
    assert "diegosouzapw" in NOTICE
    assert lock["tarball_sha256"] in NOTICE


def test_notice_disclaims_affiliation_with_every_named_provider() -> None:
    for name in (
        "RunPod",
        "OpenAI",
        "Anthropic",
        "Google",
        "GitHub",
        "Vast.ai",
        "Together",
        "Lambda",
    ):
        assert name in NOTICE, name
    assert "interoperability" in NOTICE


def test_committed_sbom_describes_the_current_version_and_lock() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    sbom = json.loads((ROOT / "docs/sbom/pitwall-sbom.cdx.json").read_text(encoding="utf-8"))
    assert sbom["metadata"]["component"]["version"] == version
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {(p["name"], p["version"]) for p in lock["package"] if "version" in p}
    for component in sbom["components"]:
        assert (component["name"], component["version"]) in locked, component["name"]
