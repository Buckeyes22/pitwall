"""Temporary-repository tests for isolated dispatch workspaces."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pitwall.agents.run_store import (
    RunStore,
    cleanup_runs,
)
from pitwall.agents.workspace import (
    BranchCollisionError,
    DirtySourceError,
    OwnershipError,
    UsageConfigurationError,
    WorkspaceRequest,
    apply_run,
    capture_changes,
    discard_run,
    prepare_isolated_worktree,
    resolve_workspace,
)
from tests.agents.shim_test_support import PITWALL

ROOT = Path(__file__).resolve().parents[2]


class TemporaryRepository:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pitwall-agents-workspace-")
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = dict(os.environ)
        self.env.update({"HOME": str(self.home), "PITWALL_AGENTS_STATE_HOME": str(self.state)})
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "tests@example.com")
        self.git("config", "user.name", "Workspace Tests")
        (self.repo / "shared.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "committed.txt").write_text("base commit\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")

    def close(self) -> None:
        self.temporary.cleanup()

    def git(
        self, *args: str, cwd: Path | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            env=self.env,
            capture_output=True,
            check=check,
        )

    def dispatch_id(self) -> str:
        return str(uuid.uuid4())

    def terminal_result(self, dispatch_id: str, status: str = "succeeded") -> Path:
        store = RunStore.create(self.env, dispatch_id)
        store.write_json(
            "result.json",
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "status": status,
                "integration": {"status": "not_applied", "appliedAt": None, "target": None},
            },
        )
        return store.artifact("result.json")


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TemporaryRepository()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_workspace_resolution_requires_declared_auto_task_mode(self) -> None:
        self.assertEqual("shared", resolve_workspace(WorkspaceRequest("shared")))
        self.assertEqual("isolated", resolve_workspace(WorkspaceRequest("isolated")))
        self.assertEqual("shared", resolve_workspace(WorkspaceRequest("auto", "read")))
        self.assertEqual("isolated", resolve_workspace(WorkspaceRequest("auto", "write")))
        with self.assertRaisesRegex(UsageConfigurationError, "requires --routing-task-mode"):
            resolve_workspace(WorkspaceRequest("auto"))

    def test_clean_creation_records_owned_branch_and_worktree(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        self.assertEqual(f"pitwall-agents/{dispatch_id}", metadata.branch)
        self.assertEqual(
            self.fixture.git("rev-parse", "HEAD").stdout.decode().strip(), metadata.baseSha
        )
        self.assertTrue(Path(metadata.path).is_dir())
        self.assertTrue(
            RunStore.create(self.fixture.env, dispatch_id).artifact("workspace.json").is_file()
        )

    def test_dirty_source_refuses_implicit_base_but_explicit_base_excludes_changes(self) -> None:
        (self.fixture.repo / "uncommitted.txt").write_text("not in base\n", encoding="utf-8")
        with self.assertRaisesRegex(DirtySourceError, "dirty"):
            prepare_isolated_worktree(
                self.fixture.env, self.fixture.dispatch_id(), self.fixture.repo
            )

        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(
            self.fixture.env, dispatch_id, self.fixture.repo, base_ref="HEAD"
        )
        self.assertFalse((Path(metadata.path) / "uncommitted.txt").exists())

    def test_existing_branch_collision_is_rejected(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        self.fixture.git("branch", f"pitwall-agents/{dispatch_id}")
        with self.assertRaises(BranchCollisionError):
            prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)

    def test_concurrent_isolated_worktrees_use_distinct_owned_branches(self) -> None:
        dispatch_ids = [self.fixture.dispatch_id(), self.fixture.dispatch_id()]
        with ThreadPoolExecutor(max_workers=2) as executor:
            records = list(
                executor.map(
                    lambda dispatch_id: prepare_isolated_worktree(
                        self.fixture.env, dispatch_id, self.fixture.repo
                    ),
                    dispatch_ids,
                )
            )
        self.assertEqual(2, len({record.branch for record in records}))
        self.assertTrue(all(Path(record.path).is_dir() for record in records))
        for index, record in enumerate(records):
            (Path(record.path) / "shared.txt").write_text(f"agent {index}\n", encoding="utf-8")
        changes = [capture_changes(self.fixture.env, dispatch_id) for dispatch_id in dispatch_ids]
        self.assertTrue(
            all("shared.txt" in {item["path"] for item in change.manifest} for change in changes)
        )
        self.assertEqual(
            "base\n",
            (self.fixture.repo / "shared.txt").read_text(encoding="utf-8"),
        )

    def test_symlinked_source_path_is_canonicalized_and_remains_owned(self) -> None:
        source_link = self.fixture.root / "repo-link"
        source_link.symlink_to(self.fixture.repo, target_is_directory=True)
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(
            self.fixture.env,
            dispatch_id,
            source_link,
        )
        self.assertEqual(str(self.fixture.repo.resolve()), metadata.sourceWorktree)
        discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertFalse(Path(metadata.path).exists())

    def test_repository_move_refuses_discard_and_preserves_retained_worktree(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(
            self.fixture.env,
            dispatch_id,
            self.fixture.repo,
        )
        moved = self.fixture.root / "repo-moved"
        self.fixture.repo.rename(moved)
        with self.assertRaisesRegex(OwnershipError, "repository common directory is unavailable"):
            discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertTrue(Path(metadata.path).is_dir())

    def _populated_isolated_run(self) -> tuple[str, Path, Path]:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        worktree = Path(metadata.path)
        (worktree / "committed.txt").write_text("child commit\n", encoding="utf-8")
        self.fixture.git("add", "committed.txt", cwd=worktree)
        self.fixture.git("commit", "-q", "-m", "child commit", cwd=worktree)
        (worktree / "shared.txt").write_text("working tree\n", encoding="utf-8")
        (worktree / "staged name.txt").write_text("staged\n", encoding="utf-8")
        self.fixture.git("add", "staged name.txt", cwd=worktree)
        (worktree / "unicode-π.txt").write_text("unicode\n", encoding="utf-8")
        (worktree / "binary data.bin").write_bytes(b"\x00\x01\xffbinary")
        result_path = self.fixture.terminal_result(dispatch_id)
        return dispatch_id, worktree, result_path

    def test_capture_includes_commits_staged_untracked_binary_spaces_and_unicode(self) -> None:
        dispatch_id, _, _ = self._populated_isolated_run()
        changes = capture_changes(self.fixture.env, dispatch_id)
        manifest = {entry["path"] for entry in changes.manifest}
        self.assertTrue(
            {"committed.txt", "shared.txt", "staged name.txt", "unicode-π.txt", "binary data.bin"}
            <= manifest
        )
        self.assertEqual(1, len(changes.commits))
        self.assertIn("staged name.txt", changes.stagedPaths)
        self.assertIn("unicode-π.txt", changes.untrackedPaths)
        self.assertIn("binary data.bin", changes.binaryPaths)
        patch = Path(changes.patchPath).read_bytes()
        self.assertIn(b"GIT binary patch", patch)
        self.assertEqual(len(manifest), changes.changedFileCount)

    def test_apply_patch_preserves_terminal_status_and_records_identity(self) -> None:
        dispatch_id, _, result_path = self._populated_isolated_run()
        capture_changes(self.fixture.env, dispatch_id)
        outcome = apply_run(self.fixture.env, dispatch_id, self.fixture.repo)
        self.assertEqual("applied", outcome.status)
        self.assertEqual(
            "working tree\n", (self.fixture.repo / "shared.txt").read_text(encoding="utf-8")
        )
        self.assertEqual(
            b"\x00\x01\xffbinary", (self.fixture.repo / "binary data.bin").read_bytes()
        )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual("succeeded", result["status"])
        self.assertEqual("applied", result["integration"]["status"])
        self.assertEqual("patch", result["integration"]["method"])
        self.assertRegex(result["integration"]["identity"], r"^[a-f0-9]{64}$")

    def test_apply_rejects_patch_paths_absent_from_manifest(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        (Path(metadata.path) / "shared.txt").write_text("isolated\n", encoding="utf-8")
        self.fixture.terminal_result(dispatch_id)
        changes = capture_changes(self.fixture.env, dispatch_id)
        with Path(changes.patchPath).open("ab") as handle:
            handle.write(
                b"diff --git a/rogue.txt b/rogue.txt\n"
                b"new file mode 100644\n"
                b"index 0000000..7f8f011\n"
                b"--- /dev/null\n"
                b"+++ b/rogue.txt\n"
                b"@@ -0,0 +1 @@\n"
                b"+rogue\n"
            )
        with self.assertRaisesRegex(OwnershipError, "absent from its manifest"):
            apply_run(self.fixture.env, dispatch_id, self.fixture.repo)
        self.assertFalse((self.fixture.repo / "rogue.txt").exists())

    def test_apply_conflict_is_recorded_without_changing_terminal_status(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        worktree = Path(metadata.path)
        (worktree / "shared.txt").write_text("isolated\n", encoding="utf-8")
        result_path = self.fixture.terminal_result(dispatch_id, status="failed")
        capture_changes(self.fixture.env, dispatch_id)

        (self.fixture.repo / "shared.txt").write_text("target divergence\n", encoding="utf-8")
        self.fixture.git("add", "shared.txt")
        self.fixture.git("commit", "-q", "-m", "diverge target")
        outcome = apply_run(self.fixture.env, dispatch_id, self.fixture.repo)
        self.assertEqual("conflicted", outcome.status)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", result["status"])
        self.assertEqual("conflicted", result["integration"]["status"])

    def test_explicit_commit_application_cherry_picks_then_applies_working_patch(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        worktree = Path(metadata.path)
        (worktree / "committed.txt").write_text("from commit\n", encoding="utf-8")
        self.fixture.git("add", "committed.txt", cwd=worktree)
        self.fixture.git("commit", "-q", "-m", "isolated commit", cwd=worktree)
        (worktree / "working.txt").write_text("working patch\n", encoding="utf-8")
        result_path = self.fixture.terminal_result(dispatch_id)
        capture_changes(self.fixture.env, dispatch_id)
        outcome = apply_run(self.fixture.env, dispatch_id, self.fixture.repo, apply_commits=True)
        self.assertEqual("applied", outcome.status)
        self.assertEqual("cherry-pick", outcome.method)
        self.assertEqual(1, len(outcome.appliedCommits))
        self.assertEqual(
            "from commit\n", (self.fixture.repo / "committed.txt").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "working patch\n", (self.fixture.repo / "working.txt").read_text(encoding="utf-8")
        )
        self.assertEqual("succeeded", json.loads(result_path.read_text(encoding="utf-8"))["status"])

    def test_cherry_pick_conflict_preserves_space_containing_path(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        worktree = Path(metadata.path)
        (worktree / "space name.txt").write_text("isolated\n", encoding="utf-8")
        self.fixture.git("add", "space name.txt", cwd=worktree)
        self.fixture.git("commit", "-q", "-m", "isolated space file", cwd=worktree)
        self.fixture.terminal_result(dispatch_id)
        capture_changes(self.fixture.env, dispatch_id)

        (self.fixture.repo / "space name.txt").write_text("target\n", encoding="utf-8")
        self.fixture.git("add", "space name.txt")
        self.fixture.git("commit", "-q", "-m", "target space file")
        outcome = apply_run(self.fixture.env, dispatch_id, self.fixture.repo, apply_commits=True)
        self.assertEqual("conflicted", outcome.status)
        self.assertEqual(["space name.txt"], outcome.conflictedFiles)
        self.assertIn("git cherry-pick --abort", outcome.message or "")

    def test_apply_commits_with_an_empty_commit_is_not_reported_applied(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        worktree = Path(metadata.path)
        self.fixture.git("commit", "-q", "--allow-empty", "-m", "empty commit", cwd=worktree)
        sha = self.fixture.git("rev-parse", "HEAD", cwd=worktree).stdout.decode().strip()
        (worktree / "working.txt").write_text("working patch\n", encoding="utf-8")
        result_path = self.fixture.terminal_result(dispatch_id)
        capture_changes(self.fixture.env, dispatch_id)

        outcome = apply_run(self.fixture.env, dispatch_id, self.fixture.repo, apply_commits=True)

        self.assertEqual("conflicted", outcome.status)
        self.assertEqual([], outcome.appliedCommits)
        self.assertEqual([f"<commit {sha[:12]}>"], outcome.conflictedFiles)
        integration = json.loads(result_path.read_text(encoding="utf-8"))["integration"]
        self.assertEqual("conflicted", integration["status"])
        self.assertEqual([f"<commit {sha[:12]}>"], integration["conflictedFiles"])
        self.assertFalse((self.fixture.repo / "working.txt").exists())
        self.assertIn("git cherry-pick --skip", outcome.message or "")
        self.assertIn("git cherry-pick --abort", outcome.message or "")

    def test_apply_commits_with_an_unknown_commit_reports_gits_reason(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        worktree = Path(metadata.path)
        (worktree / "committed.txt").write_text("from commit\n", encoding="utf-8")
        self.fixture.git("add", "committed.txt", cwd=worktree)
        self.fixture.git("commit", "-q", "-m", "isolated commit", cwd=worktree)
        self.fixture.terminal_result(dispatch_id)
        capture_changes(self.fixture.env, dispatch_id)
        changeset_path = RunStore.create(self.fixture.env, dispatch_id).artifact("changeset.json")
        changeset = json.loads(changeset_path.read_text(encoding="utf-8"))
        changeset["commits"][0]["sha"] = "0" * 40
        changeset_path.write_text(json.dumps(changeset), encoding="utf-8")

        outcome = apply_run(self.fixture.env, dispatch_id, self.fixture.repo, apply_commits=True)

        self.assertEqual("conflicted", outcome.status)
        self.assertEqual([f"<commit {'0' * 12}>"], outcome.conflictedFiles)
        message = outcome.message or ""
        self.assertIn("fatal: bad object", message)  # git's own reason
        self.assertIn("If a cherry-pick is in progress", message)
        self.assertIn("git cherry-pick --skip", message)
        self.assertIn("git cherry-pick --abort", message)
        self.assertNotIn(str(self.fixture.repo), message)

    def test_discard_requires_confirmation_and_verified_ownership(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        result_path = self.fixture.terminal_result(dispatch_id)
        with self.assertRaises(UsageConfigurationError):
            discard_run(self.fixture.env, dispatch_id)
        discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertFalse(Path(metadata.path).exists())
        self.assertNotIn(
            metadata.branch, self.fixture.git("branch", "--format=%(refname:short)").stdout.decode()
        )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual("succeeded", result["status"])
        self.assertEqual("discarded", result["integration"]["status"])

    def test_discard_rejects_tampered_owner(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        store = RunStore.create(self.fixture.env, dispatch_id)
        metadata = json.loads(store.artifact("workspace.json").read_text(encoding="utf-8"))
        metadata["ownerDispatchId"] = str(uuid.uuid4())
        store.write_json("workspace.json", metadata)
        with self.assertRaises(OwnershipError):
            discard_run(self.fixture.env, dispatch_id, yes=True)

    def test_discard_rejects_tampered_path_without_deleting_it(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        store = RunStore.create(self.fixture.env, dispatch_id)
        metadata = json.loads(store.artifact("workspace.json").read_text(encoding="utf-8"))
        victim = self.fixture.root / "victim"
        victim.mkdir()
        marker = victim / "must-survive.txt"
        marker.write_text("safe\n", encoding="utf-8")
        metadata["path"] = str(victim)
        store.write_json("workspace.json", metadata)
        with self.assertRaisesRegex(OwnershipError, "owned path"):
            discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertEqual("safe\n", marker.read_text(encoding="utf-8"))

    def _orphan_owned_worktree(self, dispatch_id: str) -> Path:
        """Leave the owned directory behind with no branch and no registered worktree entry."""
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        shutil.rmtree(self.fixture.repo / ".git" / "worktrees")
        self.fixture.git("branch", "-D", metadata.branch)
        return Path(metadata.path)

    def test_discard_removes_an_owned_worktree_whose_branch_and_git_link_are_gone(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        owned = self._orphan_owned_worktree(dispatch_id)
        result_path = self.fixture.terminal_result(dispatch_id)
        self.assertTrue(owned.is_dir())

        discard_run(self.fixture.env, dispatch_id, yes=True)

        self.assertFalse(owned.exists())
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual("discarded", result["integration"]["status"])

    def test_discard_with_a_gone_branch_never_deletes_outside_the_owned_path(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        owned = self._orphan_owned_worktree(dispatch_id)
        store = RunStore.create(self.fixture.env, dispatch_id)
        record = json.loads(store.artifact("workspace.json").read_text(encoding="utf-8"))
        victim = self.fixture.root / "victim"
        victim.mkdir()
        marker = victim / "must-survive.txt"
        marker.write_text("safe\n", encoding="utf-8")
        record["path"] = str(victim)
        store.write_json("workspace.json", record)
        with self.assertRaisesRegex(OwnershipError, "owned path"):
            discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertEqual("safe\n", marker.read_text(encoding="utf-8"))
        self.assertTrue(owned.is_dir())

    def test_discard_with_a_gone_branch_keeps_a_directory_that_is_a_live_repository(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        owned = self._orphan_owned_worktree(dispatch_id)
        shutil.rmtree(owned)
        owned.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=owned, env=self.fixture.env, check=True)
        marker = owned / "must-survive.txt"
        marker.write_text("safe\n", encoding="utf-8")
        with self.assertRaises(OwnershipError):
            discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertEqual("safe\n", marker.read_text(encoding="utf-8"))

    def test_discard_with_a_gone_branch_keeps_a_worktree_switched_to_another_branch(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        owned = Path(metadata.path)
        self.fixture.git("switch", "-c", "user-kept-work", cwd=owned)
        self.fixture.git("branch", "-D", metadata.branch)
        precious = owned / "precious.txt"
        precious.write_text("user work\n", encoding="utf-8")
        with self.assertRaisesRegex(OwnershipError, "user-kept-work|not the run's"):
            discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertEqual("user work\n", precious.read_text(encoding="utf-8"))

    def test_discard_with_a_gone_branch_keeps_a_worktree_detached_elsewhere(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        owned = Path(metadata.path)
        self.fixture.git("switch", "--detach", cwd=owned)
        (owned / "other.txt").write_text("elsewhere\n", encoding="utf-8")
        self.fixture.git("add", "other.txt", cwd=owned)
        self.fixture.git("commit", "-q", "-m", "elsewhere", cwd=owned)
        self.fixture.git("branch", "-D", metadata.branch)
        precious = owned / "precious.txt"
        precious.write_text("user work\n", encoding="utf-8")
        with self.assertRaises(OwnershipError):
            discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertTrue(precious.is_file())

    def test_discard_with_a_gone_branch_removes_a_worktree_detached_at_its_base(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        owned = Path(metadata.path)
        self.fixture.git("switch", "--detach", cwd=owned)
        self.fixture.git("branch", "-D", metadata.branch)
        (owned / "scratch.txt").write_text("run output\n", encoding="utf-8")
        discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertFalse(owned.exists())

    def test_discard_rejects_tampered_or_detached_branch(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        store = RunStore.create(self.fixture.env, dispatch_id)
        record = json.loads(store.artifact("workspace.json").read_text(encoding="utf-8"))
        record["branch"] = "pitwall-agents/not-the-owned-branch"
        store.write_json("workspace.json", record)
        with self.assertRaisesRegex(OwnershipError, "owned branch"):
            discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertTrue(Path(metadata.path).is_dir())

    def test_discard_can_clean_up_owned_worktree_without_terminal_result(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        discard_run(self.fixture.env, dispatch_id, yes=True)
        self.assertFalse(Path(metadata.path).exists())

    def test_run_cleanup_preserves_active_worktree_until_explicit_discard(self) -> None:
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        self.fixture.terminal_result(dispatch_id)
        self.assertEqual(
            [], cleanup_runs(self.fixture.env, older_than_seconds=None, remove_all=True)
        )
        self.assertTrue(Path(metadata.path).is_dir())
        discard_run(self.fixture.env, dispatch_id, yes=True)
        removed = cleanup_runs(self.fixture.env, older_than_seconds=None, remove_all=True)
        self.assertEqual([dispatch_id], [path.name for path in removed])

    def test_runs_diff_refreshes_a_paused_isolated_run(self) -> None:
        """Live diff: an isolated dispatch pauses with an ask; later edits are diffed (plan Task 5)."""
        dispatch_id = self.fixture.dispatch_id()
        metadata = prepare_isolated_worktree(self.fixture.env, dispatch_id, self.fixture.repo)
        worktree = Path(metadata.path)
        store = RunStore(Path(self.fixture.env["PITWALL_AGENTS_STATE_HOME"]), dispatch_id)
        store.write_json(
            "run.json",
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "state": "paused",
                "provider": "codex",
                "model": "m",
                "attempt": 1,
            },
        )
        (worktree / "notes.txt").write_text("first pass\n", encoding="utf-8")
        capture_changes(self.fixture.env, dispatch_id)
        self.assertIn(b"first pass", (store.artifact("changes.patch")).read_bytes())

        (worktree / "notes.txt").write_text("edited while paused\n", encoding="utf-8")
        cli_env = dict(self.fixture.env)
        cli_env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
        result = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "runs",
                "diff",
                dispatch_id,
            ],
            capture_output=True,
            env=cli_env,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(b"edited while paused", result.stdout)


if __name__ == "__main__":
    unittest.main()
