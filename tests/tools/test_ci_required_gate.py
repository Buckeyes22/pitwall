from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def _steps(job: str) -> str:
    return "\n".join(str(step.get("run", "")) for step in CI["jobs"][job]["steps"])


def test_required_needs_catalog_drift_and_every_listed_job_exists() -> None:
    needs = set(CI["jobs"]["required"]["needs"])
    assert "gateway-catalog-drift" in needs
    assert needs <= set(CI["jobs"])


def test_journey_harness_is_a_local_release_tool_not_a_ci_job() -> None:
    # The maintainer runs scripts/release/run-user-journeys.sh before releases; it is not a PR check.
    assert "journeys" not in CI["jobs"]
    assert "run-user-journeys.sh" not in "\n".join(_steps(job) for job in CI["jobs"])
    assert (ROOT / "scripts" / "release" / "run-user-journeys.sh").is_file()


def test_python_policy_runs_in_ci() -> None:
    lint = _steps("lint")
    assert "tools/guards/python_policy.py" in lint
    assert "git ls-files" in lint


def test_forbidden_imports_guard_is_gone() -> None:
    assert not (ROOT / "tools" / "guards" / "forbidden_imports.py").exists()
    assert "forbidden_imports" not in (ROOT / ".pre-commit-config.yaml").read_text("utf-8")
