"""Static policy checks for GitHub Actions trust boundaries."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
IMMUTABLE_ACTION = re.compile(r"^\s*uses:\s+([^\s#]+)(?:\s+#.*)?$")
SHA = re.compile(r"^[0-9a-f]{40}$")
RELEASE_WORKFLOWS = {"release.yml"}


def _steps(job: Any) -> list[dict[str, Any]]:
    return list((job or {}).get("steps") or [])


def unprovisioned_uv_sync_jobs(text: str) -> list[str]:
    """Jobs that run ``uv sync`` without first provisioning Python with ``actions/setup-python``.

    A runner without the pinned interpreter fails ``uv sync`` (scheduled CI, 2026-09-21), and
    ``uv python install`` cannot fetch a CPython release newer than the pinned uv knows
    (release run 37827528877). ``actions/setup-python`` is the only accepted provisioner.
    """
    document = yaml.safe_load(text) or {}
    missing: list[str] = []
    for name, job in (document.get("jobs") or {}).items():
        steps = _steps(job)
        if not any("uv sync" in str(step.get("run", "")) for step in steps):
            continue
        if not any("actions/setup-python@" in str(step.get("uses", "")) for step in steps):
            missing.append(str(name))
    return missing


def setup_python_without_version_file(text: str) -> list[str]:
    """Jobs whose ``actions/setup-python`` step does not read ``.python-version``."""
    document = yaml.safe_load(text) or {}
    wrong: list[str] = []
    for name, job in (document.get("jobs") or {}).items():
        for step in _steps(job):
            if "actions/setup-python@" not in str(step.get("uses", "")):
                continue
            with_ = step.get("with") or {}
            if with_.get("python-version-file") != ".python-version" or "python-version" in with_:
                wrong.append(str(name))
    return wrong


def check_workflow(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if "pull_request_target:" in text:
        errors.append("pull_request_target is forbidden")
    if "permissions:" not in text:
        errors.append("explicit permissions are required")
    if "concurrency:" not in text:
        errors.append("a concurrency policy is required")
    errors.extend(
        f"job {job} runs uv sync without actions/setup-python"
        for job in unprovisioned_uv_sync_jobs(text)
    )
    errors.extend(
        f"job {job}: actions/setup-python must use python-version-file: .python-version alone"
        for job in setup_python_without_version_file(text)
    )
    for line_number, line in enumerate(text.splitlines(), 1):
        match = IMMUTABLE_ACTION.match(line)
        if match is None:
            continue
        reference = match.group(1)
        if reference.startswith("./"):
            continue
        if "@" not in reference or not SHA.fullmatch(reference.rsplit("@", 1)[1]):
            errors.append(f"line {line_number}: action is not pinned to a 40-character SHA")
    if path.name in RELEASE_WORKFLOWS and (
        "types: [published]" in text or re.search(r"^\s+release:\s*$", text, re.M)
    ):
        errors.append("release publication must have only the immutable tag trigger")
    if path.name == "release.yml":
        if "PITWALL_RELEASE_ENABLED" not in text:
            errors.append("production publication needs the explicit repository enable gate")
        if '- "v[0-9]+.[0-9]+.[0-9]+*"' not in text:
            errors.append("broker release must use only the broker tag namespace")
        if 'tags:\n      - "agent-routing/v*"' in text:
            errors.append("broker release must not use Agent Routing tags")
    return errors


def main() -> int:
    failures: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        failures.extend(f"{path.relative_to(ROOT)}: {error}" for error in check_workflow(path))
    for failure in failures:
        print(failure, file=sys.stderr)
    if not failures:
        print("workflow policy passed")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
