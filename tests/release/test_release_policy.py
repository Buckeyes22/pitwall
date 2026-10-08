"""Release archive and workflow policy regression tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.release


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_workflows_satisfy_supply_chain_policy() -> None:
    module = _load("check_workflows", ROOT / "tools/ci/check_workflows.py")
    assert module.main() == 0


class _FakeReleaseItem:
    def __init__(self) -> None:
        self.keywords = {"release"}
        self.markers: list[object] = []

    def add_marker(self, marker: object) -> None:
        self.markers.append(marker)

    def get_closest_marker(self, name: str) -> object | None:
        return name if name in self.keywords else None


class _FakeSelectorConfig:
    def __init__(self, selector: str | None) -> None:
        self.selector = selector

    def getoption(self, name: str, default: object = None) -> object:
        return self.selector if name == "-m" else default


def test_release_fixture_accepts_both_exact_release_selectors() -> None:
    conftest = _load("release_conftest", ROOT / "tests/release/conftest.py")
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")

    for selector in ("release", "release and not live"):
        item = _FakeReleaseItem()
        conftest.pytest_collection_modifyitems(_FakeSelectorConfig(selector), [item])
        assert item.markers == [], selector
    for selector in (None, "live", "release or live"):
        item = _FakeReleaseItem()
        conftest.pytest_collection_modifyitems(_FakeSelectorConfig(selector), [item])
        assert len(item.markers) == 1, selector
    assert 'uv run pytest -m "release and not live" tests/release' in readiness
    assert 'uv run pytest -m "release" tests/release' not in readiness


def test_dependency_compatibility_installs_the_selected_resolution_frozen() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    compatibility_job = workflow.split("  dependency-compatibility:", 1)[1].split(
        "\n  integration:", 1
    )[0]
    assert 'uv lock --upgrade --resolution "${{ matrix.resolution }}"' in compatibility_job
    assert "uv sync --frozen --extra dev" in compatibility_job
    assert (
        'uv run --frozen pytest -n logical -m "not integration and not slow and not live"'
        in compatibility_job
    )


def test_artifact_path_policy_rejects_private_and_traversal_paths() -> None:
    module = _load("inspect_artifacts", ROOT / "scripts/release/inspect_artifacts.py")
    assert module._safe("pitwall/module.py")
    assert not module._safe("../secret")
    assert not module._safe("project/.remember/events.jsonl")
    assert not module._safe("project/PRIVATE_EVIDENCE_PITWALL.md")
    assert not module._safe("project/packages/agent-routing/README.md")
    assert not module._safe("project/model_routing/cli.py")


def test_github_first_release_requires_ghcr_but_not_deferred_pypi() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    github_release = workflow.split("  github-release:", 1)[1]
    assert "needs: [package, package-provenance, publish-image]" in github_release
    assert "publish-pypi" not in github_release
    assert "name: python-distributions" in github_release
    assert "name: package-evidence" in github_release
    assert "body_path: docs/releases/${{ github.ref_name }}.md" in github_release


def test_release_assets_verify_with_plain_sha256sum_and_carry_image_evidence() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    package = workflow.split("  package:", 1)[1].split("\n  package-provenance:", 1)[0]
    assert "(cd dist && sha256sum -- *) > artifacts/package/SHA256SUMS" in package
    assert "sha256sum dist/*" not in workflow
    publish = workflow.split("  publish-image:", 1)[1].split("\n  github-release:", 1)[0]
    assert "name: image-evidence-${{ matrix.service }}" in publish
    github_release = workflow.split("  github-release:", 1)[1]
    assert "pattern: image-evidence-*" in github_release
    assert "name: compose-smoke-evidence" in github_release


def test_release_candidate_requires_versioned_public_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = _load("validate_candidate", ROOT / "scripts/release/validate_candidate.py")
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
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert workflow.count("${GITHUB_REPOSITORY_OWNER,,}") == 2
    assert "ghcr.io/${{ github.repository_owner }}" not in workflow


def test_python_registries_are_not_in_the_github_first_workflow() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "PITWALL_PYPI_RELEASE_ENABLED" not in workflow
    assert "publish-testpypi" not in workflow
    assert "publish-pypi" not in workflow
    assert "gh-action-pypi-publish" not in workflow


def test_release_automation_never_requests_provider_credentials() -> None:
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for workflow in (readiness, release):
        assert "RUNPOD_API_KEY" not in workflow
        assert "PITWALL_LIVE_" not in workflow
        assert "--run-live" not in workflow
        assert "live-provider-acceptance" not in workflow
    assert "secrets: inherit" not in release


def test_pull_requests_use_normal_ci_not_the_full_release_suite() -> None:
    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pull_request:" not in readiness
    assert "  required:" in ci
    assert "    name: CI" in ci


def test_agent_routing_gates_run_from_the_root_ci_workflow() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert not (ROOT / ".github/workflows/agent-routing-ci.yml").exists()
    assert not (ROOT / "packages/agent-routing/.github/workflows/ci.yml").exists()
    for gate in (
        "tools/agents/validate_json_schemas.py",
        "tools/agents/validate_plugins.py",
        "tools/agents/validate_registry.py",
        "tools/agents/check_generated.py",
        "tools/agents/sync_routes.py --check",
        "SHIM-DONE exit=64",
    ):
        assert gate in workflow
    required = workflow.split("\n  required:\n", 1)[1]
    assert "      - agents-macos\n" in required


def test_release_uses_only_the_v_tag_namespace() -> None:
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert '- "v[0-9]+.[0-9]+.[0-9]+*"' in release
    assert "agent-routing/v" not in release
    assert not (ROOT / ".github/workflows/agent-routing-release.yml").exists()


def test_repository_wide_policy_and_dependency_authorities_cover_component() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    policy_command = "xargs -0 -r -n 200 uv run python tools/guards/repo_text_policy.py"
    assert policy_command in ci
    assert "git ls-files -z" in ci

    assert "uv lock --check" in ci
    assert "uv sync --frozen --extra dev" in ci

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
