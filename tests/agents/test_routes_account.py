"""The optional ``account`` field on a route entry."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents import cli, profiles
from pitwall.agents.registry import (
    load_registry,
)
from tests.agents.profiles_fixture import read_profiles

ROOT = Path(__file__).resolve().parents[2]


def document(entry: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
        "models": {"second": entry},
    }


class AccountFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def test_a_label_is_kept(self) -> None:
        entry = {
            "model": "sonnet",
            "harness": "claude",
            "env": {"CLAUDE_CONFIG_DIR": "/srv/logins/home"},
            "account": "home",
        }
        normalized = profiles.validate_profiles(document(entry), registry=self.registry)
        self.assertEqual("home", normalized["models"]["second"]["account"])

    def test_bad_labels_are_refused(self) -> None:
        for label in ("", "has space", "a-label-longer-than-sixteen", "under_score", 7):
            with self.subTest(label=label):
                with self.assertRaises(profiles.ProfilesError) as caught:
                    profiles.validate_profiles(
                        document({"model": "sonnet", "harness": "claude", "account": label}),
                        registry=self.registry,
                    )
                self.assertIn(
                    "models.second.account must be 1 to 16 letters, digits, or hyphens",
                    str(caught.exception),
                )

    def test_add_route_writes_the_label(self) -> None:
        updated = profiles.add_profile(
            profiles.empty_profiles(),
            "second",
            model="sonnet",
            harness="claude",
            env={"CLAUDE_CONFIG_DIR": "/srv/logins/home"},
            account="home",
        )
        self.assertEqual("home", updated["models"]["second"]["account"])


class AccountCliTests(unittest.TestCase):
    def test_routes_add_accepts_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "profiles.toml"
            env = {
                "HOME": directory,
                "PITWALL_AGENTS_PROFILES": str(target),
                "PATH": os.environ.get("PATH", ""),
            }
            err = io.StringIO()
            with (
                mock.patch.dict(os.environ, env, clear=True),
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = cli.main(
                    [
                        "profiles",
                        "add",
                        "claude-home",
                        "--model",
                        "sonnet",
                        "--harness",
                        "claude",
                        "--env",
                        "CLAUDE_CONFIG_DIR=/srv/logins/home",  # pragma: allowlist secret
                        "--account",
                        "home",
                    ]
                )
            self.assertEqual(0, code, err.getvalue())
            entry = read_profiles(target)["models"]["claude-home"]
            self.assertEqual("home", entry["account"])
            self.assertEqual({"CLAUDE_CONFIG_DIR": "/srv/logins/home"}, entry["env"])


if __name__ == "__main__":
    unittest.main()
