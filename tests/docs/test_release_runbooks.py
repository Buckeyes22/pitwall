"""Release runbooks match the one-package v* workflow and the public-root process."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_runbooks_use_the_current_version_and_no_component_tags() -> None:
    version = tomllib.loads(_read("pyproject.toml"))["project"]["version"]
    releasing = _read("RELEASING.md")
    assert f"git tag -s v{version}" in releasing
    assert "git tag -v" in releasing
    assert "0.1.0a2" not in releasing
    for relative in (
        "docs/release/external-release-gates.md",
        "docs/release/repository-settings-checklist.md",
    ):
        text = _read(relative)
        assert "agent-routing/v" not in text and "gateway/v" not in text, relative


def test_runbook_requires_public_root_ci_and_public_packages() -> None:
    releasing = _read("RELEASING.md")
    assert "git rev-list --count" in releasing
    assert "successful `CI` run for that exact commit" in releasing
    assert "Change visibility" in releasing
    assert "docker logout ghcr.io" in releasing


def test_tag_signatures_are_not_described_as_a_ruleset_check() -> None:
    checklist = _read("docs/release/repository-settings-checklist.md")
    assert "requires signatures" not in checklist


def test_policies_name_dco_supported_version_and_conduct_mailbox() -> None:
    version = tomllib.loads(_read("pyproject.toml"))["project"]["version"]
    assert "https://developercertificate.org/" in _read("CONTRIBUTING.md")
    assert "signed off" in _read("CONTRIBUTING.md")
    assert f"`v{version}`" in _read("SECURITY.md")
    assert "chris@lateapexllc.com" in _read("CODE_OF_CONDUCT.md")
    assert "PITWALL_AGENTS_UNRESTRICTED=1` setting bypasses" not in _read("SECURITY.md")
