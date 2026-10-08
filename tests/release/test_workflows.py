"""The repository has one CI workflow and one release workflow, and they do not overlap."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.release
WORKFLOWS = ROOT / ".github" / "workflows"
# CI jobs that only run on the weekly schedule, so they cannot gate a pull request.
SCHEDULE_ONLY = "github.event_name == 'schedule'"


def _load(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on") or workflow[True]
    assert isinstance(triggers, dict)
    return triggers


def _step_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


def _needs(job: dict[str, Any]) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _gated_jobs(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The jobs the `required` gate waits for, found from its `needs`."""
    jobs = workflow["jobs"]
    return {name: jobs[name] for name in _needs(jobs["required"])}


def test_single_ci_workflow() -> None:
    names = {path.name for path in WORKFLOWS.glob("*.y*ml")}
    assert names == {"ci.yml", "model-facts.yml", "release.yml", "release-readiness.yml"}
    for workflow in names:
        triggers = _triggers(_load(workflow))
        # Only ci.yml runs on pull requests, so nothing else is a second CI.
        assert ("pull_request" in triggers) == (workflow == "ci.yml"), workflow
    ci = _load("ci.yml")
    gated = _gated_jobs(ci)
    assert set(gated) <= set(ci["jobs"])
    # Every gate runs inside a job the `required` check waits for, whatever the job is named.
    gated_text = "\n".join(_step_text(job) for job in gated.values())
    for command in (
        "ruff check",
        "ruff format --check",
        "mypy --strict src/",
        "check_markdown_links.py",
        "bandit",
        "semgrep",
        "check_secrets.py",
        "check_catalog_drift.py",
        "check_workflows.py",
        "check_dco.py",
        "make pi-extensions-check",
        'pytest -m "fuzz',
        'pytest -m "integration',
        '-m "not integration and not slow and not live"',
    ):
        assert command in gated_text, command
    # The journey harness is a local release tool, not a pull-request check.
    assert "journeys" not in ci["jobs"]
    selector = '-m "not integration and not slow and not live"'
    hermetic = [name for name, job in ci["jobs"].items() if selector in _step_text(job)]
    # The hermetic suite runs once per CI run; the weekly compatibility matrix reruns it
    # against other dependency resolutions on purpose.
    assert hermetic == ["test", "dependency-compatibility"]
    assert ci["jobs"]["dependency-compatibility"]["if"] == SCHEDULE_ONLY


def test_required_lists_every_non_scheduled_job() -> None:
    jobs = _load("ci.yml")["jobs"]
    required = jobs["required"]
    gated = {
        name for name, job in jobs.items() if name != "required" and job.get("if") != SCHEDULE_ONLY
    }
    assert set(required["needs"]) == gated
    assert required["if"] == "always()"
    # Branch protection requires the status check named `CI`.
    assert required["name"] == "CI"
    # The macOS Agent Routing job is a pull-request gate, not an optional extra.
    assert "agents-macos" in gated


def test_coverage_combined_reuses_the_suites_coverage_data() -> None:
    jobs = _load("ci.yml")["jobs"]
    combined = jobs["coverage-combined"]
    assert combined["if"] == SCHEDULE_ONLY
    assert set(combined["needs"]) == {"test", "integration"}
    text = _step_text(combined)
    assert "coverage combine" in text
    assert "pytest" not in text and "coverage run" not in text
    downloads = [s for s in combined["steps"] if "download-artifact" in str(s.get("uses", ""))]
    assert {s["with"]["name"] for s in downloads} == {"coverage-hermetic", "coverage-integration"}
    for producer, artifact in (
        ("test", "coverage-hermetic"),
        ("integration", "coverage-integration"),
    ):
        uploads = [
            s["with"]["name"]
            for s in jobs[producer]["steps"]
            if "upload-artifact" in str(s.get("uses", ""))
        ]
        assert artifact in uploads, producer


def _node_versions(workflow: dict[str, Any]) -> dict[str, str]:
    """Every `setup-node` version in the workflow, keyed by `job/step index`."""
    versions: dict[str, str] = {}
    for name, job in workflow["jobs"].items():
        for index, step in enumerate(job.get("steps", [])):
            if "setup-node" in str(step.get("uses", "")):
                versions[f"{name}/{index}"] = str(step["with"]["node-version"])
    return versions


def test_pi_extensions_job_pins_node_and_runner_with_a_reason() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    ci = _load("ci.yml")
    job = ci["jobs"]["pi-extensions"]
    # A floating label would change the util-linux (setpriv --seccomp-filter needs 2.41, Ubuntu
    # 25.10 and later) and the compiler output under the byte-for-byte comparison.
    label = re.fullmatch(r"ubuntu-(\d+)\.(\d+)", str(job["runs-on"]))
    assert label is not None, "pi-extensions needs an explicit Ubuntu release label"
    assert (int(label[1]), int(label[2])) >= (25, 10)
    # One Node pin everywhere, so the extension check and the tests run on the same toolchain.
    versions = _node_versions(ci)
    assert any(key.startswith("pi-extensions/") for key in versions)
    assert len(set(versions.values())) == 1, versions
    (pinned,) = set(versions.values())
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned), "pin an exact Node version"
    assert "make pi-extensions-check" in _step_text(job)
    header = text.split("\n  pi-extensions:\n", 1)[1].split("steps:", 1)[0]
    assert "# " in header, "the runner and Node pins need an explanatory comment"
    assert "PITWALL_PI_MODULES" in _step_text(job)
    # A skipped sandbox test would hide a broken restricted mode, so the job fails on it.
    assert "SKIPPED" in _step_text(job)


def test_release_triggers_only_v_tags() -> None:
    release = _triggers(_load("release.yml"))
    assert list(release) == ["push"]
    assert release["push"] == {"tags": ["v[0-9]+.[0-9]+.[0-9]+*"]}
    readiness = _triggers(_load("release-readiness.yml"))
    assert set(readiness) == {"workflow_dispatch", "workflow_call"}
    for path in WORKFLOWS.glob("*.y*ml"):
        text = path.read_text(encoding="utf-8")
        assert "agent-routing/v" not in text, path.name
        assert "gateway/v" not in text, path.name


def test_readiness_does_not_rerun_ci_jobs() -> None:
    jobs = _load("release-readiness.yml")["jobs"]
    assert set(jobs) == {"ci-succeeded", "artifacts", "journeys"}
    assert jobs["artifacts"]["needs"] == "ci-succeeded"
    assert jobs["journeys"]["needs"] == "ci-succeeded"
    check = _step_text(jobs["ci-succeeded"])
    assert "--workflow ci.yml" in check and '--commit "$GITHUB_SHA"' in check
    assert jobs["ci-succeeded"]["permissions"]["actions"] == "read"
    everything = "\n".join(_step_text(job) for job in jobs.values())
    for rerun in (
        "ruff",
        "mypy",
        "bandit",
        "pip-audit",
        "semgrep",
        "coverage",
        "mutation-gate",
        "not integration and not slow",
        "check_workflows.py",
        "repo_text_policy.py",
    ):
        assert rerun not in everything, rerun
    assert "uv build" in everything
    assert "inspect_artifacts.py" in everything
    assert "smoke_artifacts.py" in everything
    release = _load("release.yml")["jobs"]
    assert release["readiness"]["uses"] == "./.github/workflows/release-readiness.yml"
    assert release["readiness"]["permissions"]["actions"] == "read"
    assert release["package"]["needs"] == "readiness"
