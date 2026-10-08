"""Tests for the release-acceptance candidate identity snapshot.

Every test builds a disposable git repository and asserts the snapshot's
content identity: only git-listed, non-generated inputs count, gitignored files
are neither read nor listed, symlinks are hashed literally without being
followed, binaries participate, deletions are marked, generated results never
self-invalidate, and unknown states never claim release readiness. Tool version
probes are stubbed so the suite is hermetic.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import candidate

STUB_TOOLS = {
    "python": "0.0-test",
    "platform": "stub-platform",
    "machine": "stub-machine",
    "node": None,
    "uv": None,
}


def git(root: Path, *args: str, commit: bool = False) -> str:
    command = ["git", "-C", str(root)]
    if commit:
        command += ["-c", "user.name=Test User", "-c", "user.email=test@example.invalid"]
    command += list(args)
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def entry(record: dict[str, Any], path: str) -> dict[str, Any] | None:
    return next((item for item in record["manifest"] if item["path"] == path), None)


def symlink(root: Path, target: Path, name: str, *, replace: bool = False) -> None:
    link = root / name
    if replace:
        link.unlink()
    link.symlink_to(target)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "uv.lock").write_text("root lock v1\n")
    (root / "src.txt").write_text("source one\n")
    (root / "data.bin").write_bytes(b"\x00\x01\x02\xff")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init", commit=True)
    monkeypatch.setattr(candidate, "_tool_versions", lambda: dict(STUB_TOOLS))
    return root


def test_clean_repository_is_release_ready(repo: Path) -> None:
    record = candidate.build_candidate(repo)
    assert record["clean"] is True
    assert record["dirty"] is False
    assert record["release_ready"] is True
    assert record["git"]["branch"]
    assert entry(record, "uv.lock") is not None
    assert record["tools"] == STUB_TOOLS


def test_identity_is_repeatable(repo: Path) -> None:
    first = candidate.build_candidate(repo)
    second = candidate.build_candidate(repo)
    assert first["candidate_id"] == second["candidate_id"]
    assert first["generated_at"] != "" and second["generated_at"] != ""


def test_tracked_and_untracked_edits_change_identity(repo: Path) -> None:
    base = candidate.build_candidate(repo)["candidate_id"]
    (repo / "src.txt").write_text("source two\n")
    tracked = candidate.build_candidate(repo)
    assert tracked["candidate_id"] != base
    assert tracked["clean"] is False and tracked["release_ready"] is False
    assert "src.txt" in tracked["git_status"]["paths"]

    git(repo, "checkout", "--", "src.txt")
    (repo / "fresh.txt").write_text("first\n")
    before = candidate.build_candidate(repo)
    (repo / "fresh.txt").write_text("second\n")
    after = candidate.build_candidate(repo)
    assert before["candidate_id"] != after["candidate_id"]
    assert entry(before, "fresh.txt")["sha256"] != entry(after, "fresh.txt")["sha256"]


def test_deletion_is_marked_missing(repo: Path) -> None:
    base = candidate.build_candidate(repo)["candidate_id"]
    (repo / "src.txt").unlink()
    record = candidate.build_candidate(repo)
    deleted = entry(record, "src.txt")
    assert deleted is not None
    assert deleted["mode"] == "missing"
    assert deleted["sha256"] is None
    assert record["candidate_id"] != base
    assert record["release_ready"] is False


def test_executable_mode_is_part_of_identity(repo: Path) -> None:
    script = repo / "run.sh"
    script.write_text("#!/bin/sh\necho ok\n")
    script.chmod(0o755)
    git(repo, "add", "run.sh")
    git(repo, "commit", "-qm", "add script", commit=True)
    record = candidate.build_candidate(repo)
    assert entry(record, "run.sh")["mode"] == "executable"
    assert entry(record, "run.sh")["index_mode"] == "100755"

    script.chmod(0o644)
    plain = candidate.build_candidate(repo)
    assert entry(plain, "run.sh")["mode"] == "file"
    assert plain["candidate_id"] != record["candidate_id"]


def test_symlink_target_hashed_literally_and_never_followed(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("OUTSIDE-SENTINEL\n")
    symlink(repo, secret, "leak")
    outside_dir = outside / "tree"
    outside_dir.mkdir()
    (outside_dir / "nested.txt").write_text("outside nested\n")
    symlink(repo, outside_dir, "dirlink")

    record = candidate.build_candidate(repo)
    leak = entry(record, "leak")
    assert leak["mode"] == "symlink"
    assert leak["symlink_target"] == str(secret)
    assert leak["sha256"] is not None
    directory_link = entry(record, "dirlink")
    assert directory_link["mode"] == "symlink"
    assert directory_link["symlink_target"] == str(outside_dir)
    assert "OUTSIDE-SENTINEL" not in json.dumps(record)
    assert entry(record, "dirlink/nested.txt") is None

    symlink(repo, repo / "src.txt", "leak", replace=True)
    retargeted = candidate.build_candidate(repo)
    assert entry(retargeted, "leak")["symlink_target"] == str(repo / "src.txt")
    assert retargeted["candidate_id"] != record["candidate_id"]


def test_tracked_binary_edit_invalidates(repo: Path) -> None:
    base = candidate.build_candidate(repo)
    assert entry(base, "data.bin")["sha256"] is not None
    (repo / "data.bin").write_bytes(b"\x00\x01\x02\xfe")
    edited = candidate.build_candidate(repo)
    assert edited["candidate_id"] != base["candidate_id"]
    assert entry(edited, "data.bin")["sha256"] != entry(base, "data.bin")["sha256"]


def test_gitignored_secret_is_not_read_or_listed(repo: Path) -> None:
    (repo / ".gitignore").write_text(".secret\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "ignore secret", commit=True)
    (repo / ".secret").write_text("TOPSECRET-ALPHA\n")
    first = candidate.build_candidate(repo)
    assert entry(first, ".secret") is None
    assert "TOPSECRET-ALPHA" not in json.dumps(first)
    (repo / ".secret").write_text("TOPSECRET-BETA\n")
    second = candidate.build_candidate(repo)
    assert second["candidate_id"] == first["candidate_id"]


def test_generated_roots_and_release_outputs_are_excluded(repo: Path) -> None:
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("fake\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "pkg.js").write_text("module\n")
    packet = repo / "release_acceptance" / "final-packet"
    packet.mkdir(parents=True)
    (packet / "packet.json").write_text("{}\n")
    (repo / "release_acceptance" / "runs").mkdir()
    (repo / "release_acceptance" / "runs" / "run.json").write_text("{}\n")

    record = candidate.build_candidate(repo)
    assert entry(record, ".venv/bin/python") is None
    assert entry(record, "node_modules/pkg.js") is None
    assert entry(record, "release_acceptance/final-packet/packet.json") is None
    assert entry(record, "release_acceptance/runs/run.json") is None
    excluded = {item["path"]: item["reason"] for item in record["excluded"]}
    assert excluded[".venv/bin/python"] == "generated:.venv"
    assert excluded["node_modules/pkg.js"] == "generated:node_modules"
    assert excluded["release_acceptance/final-packet/packet.json"].startswith("generated:")
    assert excluded["release_acceptance/runs/run.json"].startswith("generated:")


def test_output_path_does_not_change_identity(repo: Path, tmp_path: Path) -> None:
    base = candidate.build_candidate(repo)["candidate_id"]
    external = tmp_path / "outside-report.json"
    outside = candidate.build_candidate(repo, output=external)
    assert outside["candidate_id"] == base
    assert external.exists() and entry(outside, "outside-report.json") is None

    internal = repo / "candidate.json"
    first = candidate.build_candidate(repo, output=internal)
    assert first["candidate_id"] == base
    assert internal.exists()
    second = candidate.build_candidate(repo, output=internal)
    assert second["candidate_id"] == base
    assert any(item["reason"] == "output" for item in second["excluded"])
    assert entry(second, "candidate.json") is None


def test_declaration_change_changes_identity(repo: Path) -> None:
    base = candidate.build_candidate(repo)
    (repo / "uv.lock").write_text("root lock v2\n")
    changed = candidate.build_candidate(repo)
    assert changed["candidate_id"] != base["candidate_id"]
    assert entry(changed, "uv.lock")["sha256"] != entry(base, "uv.lock")["sha256"]


def test_missing_required_lock_blocks_release_readiness(repo: Path) -> None:
    (repo / "uv.lock").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "drop lock", commit=True)
    record = candidate.build_candidate(repo)
    lock = next(item for item in record["dependencies"] if item["path"] == "uv.lock")
    assert lock["present"] is False and lock["sha256"] is None
    assert record["clean"] is True
    assert record["release_ready"] is False
    assert any("missing required lock" in issue for issue in record["issues"])


def test_artifact_digest_and_name_change_identity(repo: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "dist" / "wheel.bin"
    artifact.parent.mkdir()
    artifact.write_bytes(b"wheel-v1")
    first = candidate.build_candidate(repo, artifacts={"wheel": str(artifact)})
    second = candidate.build_candidate(repo, artifacts={"wheel": str(artifact)})
    assert first["candidate_id"] == second["candidate_id"]
    assert first["artifacts"][0]["name"] == "wheel"
    assert first["artifacts"][0]["sha256"]

    artifact.write_bytes(b"wheel-v2")
    changed = candidate.build_candidate(repo, artifacts={"wheel": str(artifact)})
    assert changed["candidate_id"] != first["candidate_id"]
    renamed = candidate.build_candidate(repo, artifacts={"other": str(artifact)})
    assert renamed["candidate_id"] != changed["candidate_id"]


def test_artifact_path_inside_root_is_excluded_from_manifest(repo: Path) -> None:
    artifact = repo / "exports" / "pack.tar"
    artifact.parent.mkdir()
    artifact.write_bytes(b"payload")
    record = candidate.build_candidate(repo, artifacts={"pack": "exports/pack.tar"})
    assert entry(record, "exports/pack.tar") is None
    assert any(item["reason"] == "artifact" for item in record["excluded"])


def test_gitlink_records_commit_and_requires_component_proof(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},vendor/component")
    record = candidate.build_candidate(repo)
    link = entry(record, "vendor/component")
    assert link["mode"] == "gitlink"
    assert link["mode160000"] is True
    assert link["index_mode"] == "160000"
    assert link["git_commit"] == head
    assert link["sha256"] is None
    assert "external proof required" in link["issue"]
    assert record["release_ready"] is False


def test_cli_writes_output_and_has_no_include_ignored_switch(repo: Path, tmp_path: Path) -> None:
    output = tmp_path / "candidate.json"
    code = candidate.main(
        ["--root", str(repo), "--output", str(output), "--artifact", f"src={repo / 'src.txt'}"]
    )
    assert code == 0
    written = json.loads(output.read_text())
    assert (
        written["candidate_id"]
        == candidate.build_candidate(repo, artifacts={"src": str(repo / "src.txt")})["candidate_id"]
    )
    with pytest.raises(SystemExit):
        candidate.main(["--root", str(repo), "--include-ignored"])


def test_unreadable_state_never_claims_release_readiness(tmp_path: Path) -> None:
    not_a_repository = tmp_path / "plain"
    not_a_repository.mkdir()
    (not_a_repository / "uv.lock").write_text("lock\n")
    record = candidate.build_candidate(not_a_repository)
    assert record["clean"] is False
    assert record["release_ready"] is False
    assert record["git"]["commit"] is None
    assert any(
        "unknowable" in issue or "git metadata error" in issue or "file list unavailable" in issue
        for issue in record["issues"]
    )


def test_missing_uv_lock_reports_missing_and_unready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "src.txt").write_text("only source\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init", commit=True)
    monkeypatch.setattr(candidate, "_tool_versions", lambda: dict(STUB_TOOLS))
    record = candidate.build_candidate(root)
    assert record["dependencies"] == [{"path": "uv.lock", "present": False, "sha256": None}]
    assert record["release_ready"] is False


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_unreadable_input_blocks_release_readiness(repo: Path) -> None:
    secret = repo / "locked.bin"
    secret.write_bytes(b"hidden")
    git(repo, "add", "locked.bin")
    git(repo, "commit", "-qm", "add locked", commit=True)
    secret.chmod(0)
    try:
        record = candidate.build_candidate(repo)
    finally:
        secret.chmod(0o644)
    locked = entry(record, "locked.bin")
    assert locked["sha256"] is None
    assert locked["issue"] == "unreadable"
    assert record["release_ready"] is False
    assert any("unreadable or unproven" in issue for issue in record["issues"])


def test_paths_with_spaces_and_newlines_are_hashed_safely(repo: Path) -> None:
    spaced = repo / "a file with spaces.txt"
    spaced.write_text("spaced\n")
    newlined = repo / "line\nbreak.txt"
    newlined.write_text("newlined\n")
    binary = repo / "payload.bin"
    binary.write_bytes(bytes(range(256)) * 4)

    record = candidate.build_candidate(repo)
    assert entry(record, "a file with spaces.txt")["sha256"]
    assert entry(record, "line\nbreak.txt")["sha256"]
    assert entry(record, "payload.bin")["sha256"]
    assert record["candidate_id"] == candidate.build_candidate(repo)["candidate_id"]


def test_tracked_leaf_behind_parent_symlink_is_never_read(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = repo / "owned"
    owned.mkdir()
    (owned / "fixture.txt").write_text("source fixture\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "track child", commit=True)

    outside = tmp_path / "outside-owned"
    shutil.move(str(owned), str(outside))
    outside_child = outside / "fixture.txt"
    outside_child.write_text("owned outside sentinel\n")
    owned.symlink_to(outside, target_is_directory=True)

    reads: list[str] = []
    real_sha256_file = candidate._sha256_file

    def recording_sha256_file(path: Path) -> str | None:
        reads.append(str(path))
        return real_sha256_file(path)

    monkeypatch.setattr(candidate, "_sha256_file", recording_sha256_file)
    record = candidate.build_candidate(repo)
    monkeypatch.setattr(candidate, "_sha256_file", real_sha256_file)

    outside_digest = hashlib.sha256(outside_child.read_bytes()).hexdigest()
    assert not any(item.get("sha256") == outside_digest for item in record["manifest"])
    assert not any(Path(read) == owned / "fixture.txt" for read in reads)
    blocked = entry(record, "owned/fixture.txt")
    assert blocked["mode"] == "blocked"
    assert blocked["sha256"] is None
    assert "outside repository root" in blocked["issue"]
    assert record["release_ready"] is False
    assert any("outside repository root" in issue for issue in record["issues"])


def test_required_lock_symlink_outside_root_blocks_readiness(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside_lock = tmp_path / "outside.lock"
    outside_lock.write_text("outside lock fixture\n")
    (repo / "uv.lock").unlink()
    (repo / "uv.lock").symlink_to(outside_lock)

    reads: list[str] = []
    real_sha256_file = candidate._sha256_file

    def recording_sha256_file(path: Path) -> str | None:
        reads.append(str(path))
        return real_sha256_file(path)

    monkeypatch.setattr(candidate, "_sha256_file", recording_sha256_file)
    record = candidate.build_candidate(repo)
    monkeypatch.setattr(candidate, "_sha256_file", real_sha256_file)

    outside_digest = hashlib.sha256(outside_lock.read_bytes()).hexdigest()
    assert not any(Path(read) == repo / "uv.lock" for read in reads)
    assert not any(item.get("sha256") == outside_digest for item in record["dependencies"])
    lock = next(item for item in record["dependencies"] if item["path"] == "uv.lock")
    assert lock["present"] is False and lock["sha256"] is None
    assert record["release_ready"] is False
    assert any(
        "uv.lock: lock resolves outside repository root" in issue for issue in record["issues"]
    )
    assert any("missing required lock(s): uv.lock" in issue for issue in record["issues"])


def test_remote_query_fragment_userinfo_secrets_removed(repo: Path) -> None:
    secret_url = (
        "https://fixture-user:fixture-password@example.invalid/repo"
        "?token=fixture-query-secret#fixture-fragment"
    )
    git(repo, "remote", "add", "origin", secret_url)

    redacted = candidate._redact(secret_url)
    assert redacted.startswith("https://REDACTED@example.invalid/repo")
    for secret in ("fixture-password", "fixture-query-secret", "fixture-fragment"):
        assert secret not in redacted

    record = candidate.build_candidate(repo)
    serialized = json.dumps(record)
    assert record["git"]["remote"] == redacted
    for secret in ("fixture-password", "fixture-query-secret", "fixture-fragment"):
        assert secret not in serialized

    clean = candidate._redact("https://example.invalid/repo")
    assert clean == "https://example.invalid/repo"


def test_cli_prints_record_json_without_output(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = candidate.main(["--root", str(repo)])
    stdout = capsys.readouterr().out.strip()
    assert code == 0
    emitted = json.loads(stdout)
    assert emitted["schema_version"] == candidate.SCHEMA_VERSION
    assert emitted["candidate_id"] == candidate.build_candidate(repo)["candidate_id"]


def test_module_entry_point_is_importable() -> None:
    assert candidate.SCHEMA_VERSION == "release-acceptance-candidate.v1"
    assert candidate.__name__ in sys.modules
