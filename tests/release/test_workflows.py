"""The repository has one CI workflow and one release workflow, and they do not overlap."""

from __future__ import annotations

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


def test_single_ci_workflow() -> None:
    names = {path.name for path in WORKFLOWS.glob("*.y*ml")}
    assert names == {"ci.yml", "model-facts.yml", "release.yml", "release-readiness.yml"}
    for workflow in names:
        triggers = _triggers(_load(workflow))
        # Only ci.yml runs on pull requests, so nothing else is a second CI.
        assert ("pull_request" in triggers) == (workflow == "ci.yml"), workflow
    ci = _load("ci.yml")
    for job in (
        "lint",
        "format",
        "typecheck",
        "docs",
        "security-sast",
        "security-secrets",
        "security-fuzz",
        "test",
        "integration",
        "gateway-catalog-drift",
        "pi-extensions",
    ):
        assert job in ci["jobs"], job
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


def test_pi_extensions_job_pins_node_and_runner_with_a_reason() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    job = _load("ci.yml")["jobs"]["pi-extensions"]
    assert job["runs-on"] == "ubuntu-26.04"
    setup_node = next(s for s in job["steps"] if "setup-node" in str(s.get("uses", "")))
    assert setup_node["with"]["node-version"] == "22.22.1"
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
