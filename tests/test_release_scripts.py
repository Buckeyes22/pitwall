"""The release shell scripts and the artifact smoke refuse unsafe input and run what they claim."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "release"


def _run(argv: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    return subprocess.run(
        argv,
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
        check=False,
    )


def test_harness_lists_every_journey() -> None:
    harness = (RELEASE / "run-user-journeys.sh").read_text(encoding="utf-8")
    catalogue = (ROOT / "docs" / "operator" / "user-journey-catalog.md").read_text("utf-8")
    hermetic = catalogue.split("## Hermetic journeys (automated)", 1)[1].split("\n## ", 1)[0]
    ids = re.findall(r"^\| (J\d\d) \|", hermetic, re.M)
    sequence = harness.split('case "${JOURNEY_FILTER}" in', 1)[1].split("*)", 1)[1]
    called = set(re.findall(r"\bj(\d\d)\b", sequence))
    assert ids and len(ids) == len(set(ids))
    # A function may run a range of journeys from one test file, labelled "(J28-J31)".
    ranges = {
        (int(low), int(high)): owner
        for owner, low, high in re.findall(
            r"^j(\d\d)\(\) \{\n(?:.*\n)*?.*\(J(\d\d)-J(\d\d)", harness, re.M
        )
    }
    for journey in ids:
        number = int(journey[1:])
        owners = (
            [journey[1:]]
            if re.search(rf"^j{journey[1:]}\(\) \{{", harness, re.M)
            else [owner for (low, high), owner in ranges.items() if low <= number <= high]
        )
        assert owners and all(owner in called for owner in owners), journey
    in_process = re.search(r'^IN_PROCESS_JOURNEYS="([^"]+)"', harness, re.M)
    assert in_process
    for journey in in_process.group(1).split():
        assert f"    {journey}) j{journey[1:]} ;;" in harness, journey


def test_alpha_readiness_runs_the_journey_harness() -> None:
    script = RELEASE / "run-alpha-readiness.sh"
    refused = _run(["bash", str(script)])
    assert refused.returncode != 0
    assert "DATABASE_URL environment variable is required" in refused.stderr
    text = script.read_text(encoding="utf-8")
    suite = text.index("uv run pytest tests/release/ -v -m release")
    audit = text.index("uv run python -m pitwall.audit.checks --strict")
    assert suite < audit


def test_smoke_compose_brings_up_and_tears_down() -> None:
    script = RELEASE / "smoke_compose.sh"
    unsafe = _run(["bash", str(script)], PITWALL_IMAGE_TAG="t", COMPOSE_PROJECT_NAME="evil")
    assert unsafe.returncode == 2
    assert "refusing unsafe Compose project name: evil" in unsafe.stderr
    untagged = _run(["bash", str(script)])
    assert untagged.returncode != 0 and "PITWALL_IMAGE_TAG is required" in untagged.stderr
    text = script.read_text(encoding="utf-8")
    assert "trap cleanup EXIT" in text
    assert "docker compose down --volumes --remove-orphans" in text


@pytest.mark.parametrize(
    "names", [[], ["pitwall-1-py3-none-any.whl"], ["a.whl", "b.whl", "c.tar.gz"]]
)
def test_smoke_artifacts_requires_exactly_one_wheel_and_one_sdist(
    names: list[str], tmp_path: Path
) -> None:
    for name in names:
        (tmp_path / name).write_bytes(b"")
    result = _run([sys.executable, str(RELEASE / "smoke_artifacts.py"), str(tmp_path)])
    assert result.returncode == 2
    assert "exactly one wheel and one sdist are required" in result.stderr


def test_run_bound_tests_requires_one_new_output_directory(tmp_path: Path) -> None:
    script = RELEASE / "run_bound_tests.py"
    usage = _run([sys.executable, str(script)])
    assert usage.returncode == 2 and "Usage: run_bound_tests.py OUTPUT_DIR" in usage.stderr
    (tmp_path / "results").mkdir()
    reused = _run([sys.executable, str(script), str(tmp_path)])
    assert reused.returncode != 0 and "FileExistsError" in reused.stderr


HARNESS_ONLY = (
    "tests/release/test_cli_all_commands_journey.py",
    "tests/release/test_mcp_all_tools_journey.py",
    "tests/release/test_rest_all_operations_journey.py",
    "tests/release/test_matrix_complete.py",
)


@pytest.mark.parametrize("harness", [False, True])
def test_release_readiness_lane_leaves_harness_only_journeys_to_the_local_harness(
    harness: bool,
) -> None:
    # Harness-only journeys run through scripts/release/run-user-journeys.sh, a local release
    # tool; no pull-request workflow runs the harness.
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert "journeys" not in ci["jobs"]
    assert "journeys" not in ci["jobs"]["required"]["needs"]
    base = {k: v for k, v in os.environ.items() if k != "PITWALL_JOURNEY_HARNESS"}
    env = {"PITWALL_JOURNEY_HARNESS": "1"} if harness else {}
    selector = "release and not live"  # release-readiness.yml's exact selector
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            "-m",
            selector,
            *HARNESS_ONLY,
        ],
        cwd=ROOT,
        env={**base, "DATABASE_URL": "", "REDIS_URL": "", **env},
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
        check=False,
    )
    collected = {line.split("::", 1)[0] for line in result.stdout.splitlines() if "::" in line}
    assert collected == (set(HARNESS_ONLY) if harness else set()), result.stdout[-800:]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, RELEASE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agents_release_tools_are_merged_into_scripts_release() -> None:
    assert not (ROOT / "tools" / "agents" / "release").exists()
    assert not (ROOT / ".github" / "workflows" / "agent-routing-release.yml").exists()
    for name in ("validate_candidate", "inspect_artifacts", "smoke_artifacts"):
        assert (RELEASE / f"{name}.py").is_file()


def test_validate_candidate_accepts_only_v_tags_for_the_project_version() -> None:
    validator = _load("validate_candidate")
    version = validator._version()
    assert not [e for e in validator.validate(f"v{version}", allow_dirty=True) if "tag" in e]
    for tag in (f"agent-routing/v{version}", f"gateway/v{version}", version, "v9.9.9"):
        assert any("tag" in error for error in validator.validate(tag, allow_dirty=True)), tag


def test_validate_candidate_requires_plugin_versions_to_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = _load("validate_candidate")
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    for relative, name in validator.PLUGIN_MANIFESTS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"name": name, "version": "0.0.1"}), encoding="utf-8")
    for relative, name in validator.MARKETPLACES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True)
        target.write_text(
            json.dumps({"plugins": [{"name": name, "version": "0.0.1"}]}), encoding="utf-8"
        )
    assert validator._plugin_errors("0.0.1") == []
    errors = validator._plugin_errors("0.0.2")
    assert len(errors) == len(validator.PLUGIN_MANIFESTS) + len(validator.MARKETPLACES)


def test_inspect_artifacts_rejects_wrong_metadata_and_private_paths(tmp_path: Path) -> None:
    inspector = _load("inspect_artifacts")
    metadata = b"Metadata-Version: 2.4\nName: pitwall-agent-routing\nVersion: 0.12.0\n"
    assert any("Name must be" in e for e in inspector._metadata_errors(metadata, "x.whl"))
    assert inspector.PRIVATE_TEXT.search(b"path=/home/someone/checkout")
    assert inspector.PRIVATE_TEXT.search(b"file:///tmp/x")
    wheel = tmp_path / "pitwall-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("pitwall/py.typed", "")
        archive.writestr("pitwall/leak.py", "ROOT = '/home/someone/checkout'\n")
    errors = inspector.inspect_wheel(wheel)
    assert any("pitwall/leak.py contains a checkout or private path" in e for e in errors)
    assert any("exactly one METADATA file is required" in e for e in errors)
