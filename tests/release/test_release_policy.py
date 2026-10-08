"""Release archive and workflow policy regression tests.

Workflow invariants are read from the parsed YAML (jobs, ``needs``, step ``run`` text), never
from raw text, so a comment, a reordered job, or different indentation cannot satisfy or break
them. The parsing helpers live in ``test_workflows.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.release.test_workflows import _gated_jobs, _load, _needs, _step_text

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.release


def _module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _all_run_text(workflow: dict[str, Any]) -> str:
    return "\n".join(_step_text(job) for job in workflow["jobs"].values())


def _uploads_and_downloads(job: dict[str, Any], action: str) -> list[dict[str, Any]]:
    return [
        step.get("with", {}) for step in job.get("steps", []) if action in str(step.get("uses", ""))
    ]


def test_all_workflows_satisfy_supply_chain_policy() -> None:
    module = _module("check_workflows", ROOT / "tools/ci/check_workflows.py")
    assert module.main() == 0


class _FakeItem:
    """A collected test item. It has no `keywords`: the directory name must not matter."""

    def __init__(self, *markers: str) -> None:
        self.marker_names = set(markers)
        self.added: list[object] = []

    def add_marker(self, marker: object) -> None:
        self.added.append(marker)

    def get_closest_marker(self, name: str) -> object | None:
        return name if name in self.marker_names else None


class _FakeSelectorConfig:
    def __init__(self, selector: str | None) -> None:
        self.selector = selector

    def getoption(self, name: str, default: object = None) -> object:
        return self.selector if name == "-m" else default


def test_release_fixture_accepts_both_exact_release_selectors() -> None:
    conftest = _module("release_conftest", ROOT / "tests/release/conftest.py")

    for selector in ("release", "release and not live"):
        item = _FakeItem("release")
        conftest.pytest_collection_modifyitems(_FakeSelectorConfig(selector), [item])
        assert item.added == [], selector
    for selector in (None, "live", "release or live"):
        item = _FakeItem("release")
        conftest.pytest_collection_modifyitems(_FakeSelectorConfig(selector), [item])
        assert len(item.added) == 1, selector


def test_release_fixture_leaves_unmarked_items_alone() -> None:
    # Items collected under tests/release/ that carry no `release` marker (the directory name
    # is not a marker) are not skipped, whatever the selector is.
    conftest = _module("release_conftest", ROOT / "tests/release/conftest.py")

    for selector in (None, "live", "release or live", "release", "release and not live"):
        item = _FakeItem()
        conftest.pytest_collection_modifyitems(_FakeSelectorConfig(selector), [item])
        assert item.added == [], selector


def test_release_readiness_selects_exactly_the_release_tier() -> None:
    readiness = _all_run_text(_load("release-readiness.yml"))
    assert 'uv run pytest -m "release and not live" tests/release' in readiness
    assert 'uv run pytest -m "release" tests/release' not in readiness


def test_dependency_compatibility_installs_the_selected_resolution_frozen() -> None:
    job = _load("ci.yml")["jobs"]["dependency-compatibility"]
    text = _step_text(job)
    assert 'uv lock --upgrade --resolution "${{ matrix.resolution }}"' in text
    assert "uv sync --frozen --extra dev" in text
    assert (
        'uv run --frozen pytest -n logical -m "not integration and not slow and not live"' in text
    )


def test_artifact_path_policy_rejects_private_and_traversal_paths() -> None:
    module = _module("inspect_artifacts", ROOT / "scripts/release/inspect_artifacts.py")
    assert module._safe("pitwall/module.py")
    assert not module._safe("../secret")
    assert not module._safe("project/.remember/events.jsonl")
    assert not module._safe("project/PRIVATE_EVIDENCE_PITWALL.md")
    assert not module._safe("project/packages/agent-routing/README.md")
    assert not module._safe("project/model_routing/cli.py")


def test_github_first_release_requires_ghcr_but_not_deferred_pypi() -> None:
    jobs = _load("release.yml")["jobs"]
    github_release = jobs["github-release"]
    assert _needs(github_release) == {"package", "package-provenance", "publish-image"}
    assert not [name for name in jobs if "pypi" in name]
    downloads = _uploads_and_downloads(github_release, "download-artifact")
    assert {d.get("name") for d in downloads} >= {
        "python-distributions",
        "package-evidence",
        "compose-smoke-evidence",
    }
    assert "image-evidence-*" in {d.get("pattern") for d in downloads}
    publish = _uploads_and_downloads(github_release, "action-gh-release")
    assert [p["body_path"] for p in publish] == ["docs/releases/${{ github.ref_name }}.md"]


def test_release_assets_verify_with_plain_sha256sum_and_carry_image_evidence() -> None:
    workflow = _load("release.yml")
    package = _step_text(workflow["jobs"]["package"])
    assert "(cd dist && sha256sum -- *) > artifacts/package/SHA256SUMS" in package
    assert "sha256sum dist/*" not in _all_run_text(workflow)
    uploads = _uploads_and_downloads(workflow["jobs"]["publish-image"], "upload-artifact")
    assert "image-evidence-${{ matrix.service }}" in {u.get("name") for u in uploads}


def test_release_candidate_requires_versioned_public_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = _module("validate_candidate", ROOT / "scripts/release/validate_candidate.py")
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(validator, "_plugin_errors", lambda _version: [])
    monkeypatch.setattr(validator, "_install_reference_errors", lambda _version: [])
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pitwall"\nversion = "0.2.0a1"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text("## [0.2.0a1] - 2026-09-29\n", encoding="utf-8")
    assert validator.validate("v0.2.0a1", allow_dirty=True) == [
        "docs/releases/v0.2.0a1.md must contain release notes"
    ]
    notes = tmp_path / "docs" / "releases" / "v0.2.0a1.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("GitHub-first alpha notes\n", encoding="utf-8")
    assert validator.validate("v0.2.0a1", allow_dirty=True) == []


def test_ghcr_paths_normalize_the_repository_owner_to_lowercase() -> None:
    workflow = _load("release.yml")
    ghcr_lines = [
        line
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        for line in str(step.get("run", "")).splitlines()
        if "ghcr.io/" in line
    ]
    assert ghcr_lines, "release.yml no longer pushes to ghcr.io"
    for line in ghcr_lines:
        assert "${GITHUB_REPOSITORY_OWNER,,}" in line, line
    # Registry paths must not come from the mixed-case owner context anywhere in the workflow.
    assert "ghcr.io/${{ github.repository_owner }}" not in json.dumps(workflow)


def test_python_registries_are_not_in_the_github_first_workflow() -> None:
    workflow = _load("release.yml")
    serialized = json.dumps(workflow)
    for forbidden in (
        "PITWALL_PYPI_RELEASE_ENABLED",
        "publish-testpypi",
        "publish-pypi",
        "gh-action-pypi-publish",
    ):
        assert forbidden not in serialized, forbidden


def test_release_automation_never_requests_provider_credentials() -> None:
    # Serialized from the parsed workflow, so only real keys, values, and commands count.
    workflows = {name: _load(name) for name in ("release-readiness.yml", "release.yml")}
    for name, workflow in workflows.items():
        serialized = json.dumps(workflow)
        for forbidden in (
            "RUNPOD_API_KEY",
            "PITWALL_LIVE_",
            "--run-live",
            "live-provider-acceptance",
        ):
            assert forbidden not in serialized, (name, forbidden)
        for job_name, job in workflow["jobs"].items():
            assert job.get("secrets") != "inherit", (name, job_name)


def test_agent_routing_gates_run_from_the_root_ci_workflow() -> None:
    gated = "\n".join(_step_text(job) for job in _gated_jobs(_load("ci.yml")).values())

    assert not (ROOT / "packages/agent-routing/.github/workflows/ci.yml").exists()
    for gate in (
        "tools/agents/validate_json_schemas.py",
        "tools/agents/validate_plugins.py",
        "tools/agents/validate_registry.py",
        "tools/agents/check_generated.py",
        "tools/agents/sync_routes.py --check",
        "SHIM-DONE exit=64",
    ):
        assert gate in gated, gate


def test_repository_wide_policy_and_dependency_authorities_cover_component() -> None:
    gated = "\n".join(_step_text(job) for job in _gated_jobs(_load("ci.yml")).values())

    assert "xargs -0 -r -n 200 uv run python tools/guards/repo_text_policy.py" in gated
    assert "git ls-files -z" in gated
    assert "uv lock --check" in gated
    assert "uv sync --frozen --extra dev" in gated

    root_lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    component_manifest = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "pitwall-agent-routing" not in root_lock
    assert "[tool.uv.workspace]" not in component_manifest
    # Dependabot version updates are configured at the repository root
    # (tests/release/test_dependabot.py pins the ecosystems); no component copy.
    assert (ROOT / ".github/dependabot.yml").exists()
    assert not (ROOT / "packages/agent-routing/.github/dependabot.yml").exists()


def test_live_endpoint_ids_are_external_inputs() -> None:
    lb_test = (ROOT / "tests/api/test_e2e_sync_inference.py").read_text(encoding="utf-8")
    queue_test = (ROOT / "tests/api/test_e2e_async_job_webhook.py").read_text(encoding="utf-8")

    assert "PITWALL_LIVE_LB_ENDPOINT_ID" in lb_test
    assert "PITWALL_LIVE_QUEUE_ENDPOINT_ID" in queue_test
    assert "eptest00000000" not in lb_test
    assert "rdhwjnr3j6b98y" not in queue_test
