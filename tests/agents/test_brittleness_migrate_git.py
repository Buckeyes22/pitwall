"""`pitwall agents migrate` reports a missing git instead of crashing with a traceback."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents import migrate
from pitwall.agents.migrate_env import LEGACY_BRANCH_PREFIX

DISPATCH_ID = "00000000-0000-4000-8000-0000000000b1"


class MissingGitTests(unittest.TestCase):
    def test_worktree_branch_migration_without_git_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pitwall-migrate-git-") as directory:
            root = Path(directory)
            state = root / "state"
            record_path = state / "runs" / DISPATCH_ID / "workspace.json"
            record_path.parent.mkdir(parents=True)
            repo = root / "repo"
            repo.mkdir()
            original = {
                "branch": LEGACY_BRANCH_PREFIX + DISPATCH_ID,
                "repositoryCommonDir": str(repo),
            }
            record_path.write_text(json.dumps(original), encoding="utf-8")
            errors: list[str] = []
            with mock.patch.object(migrate.subprocess, "run", side_effect=FileNotFoundError("git")):
                changed, conflict = migrate._migrate_worktree_branches(
                    {"HOME": str(root)}, state, lambda _m: None, errors.append
                )
            self.assertEqual((False, True), (changed, conflict))
            self.assertTrue(any("git" in message and "not found" in message for message in errors))
            # The record is left untouched so a later run, with git installed, can rename it.
            self.assertEqual(original, json.loads(record_path.read_text(encoding="utf-8")))
