"""An empty XDG_* variable means "unset" (the XDG spec), never "the current directory"."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pitwall.agents import paths, profiles
from pitwall.agents.harnesses.goose import GooseAdapter
from pitwall.agents.harnesses.opencode import OpenCodeAdapter


class EmptyXdgTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="pitwall-xdg-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)

    def env(self, **extra: str) -> dict[str, str]:
        return {"HOME": str(self.home), **extra}

    def test_state_root_ignores_empty_xdg_state_home(self) -> None:
        for value in ("", "   "):
            with self.subTest(value=value):
                self.assertEqual(
                    self.home / ".local" / "state" / "pitwall" / "agents",
                    paths.state_root(self.env(XDG_STATE_HOME=value)),
                )

    def test_config_root_ignores_empty_xdg_config_home(self) -> None:
        self.assertEqual(
            self.home / ".config" / "pitwall" / "agents",
            paths.config_root(self.env(XDG_CONFIG_HOME="")),
        )

    def test_a_set_xdg_value_is_still_honoured(self) -> None:
        self.assertEqual(
            self.home / "cfg" / "pitwall" / "agents",
            paths.config_root(self.env(XDG_CONFIG_HOME=str(self.home / "cfg"))),
        )

    def test_profiles_path_ignores_empty_xdg_config_home(self) -> None:
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        previous = Path.cwd()
        import os

        os.chdir(elsewhere)
        try:
            result = profiles.profiles_path(self.env(XDG_CONFIG_HOME=""))
        finally:
            os.chdir(previous)
        self.assertEqual(self.home / ".config" / "pitwall" / "pitwall.toml", result)

    def test_opencode_and_goose_config_paths_ignore_empty_xdg_config_home(self) -> None:
        env = self.env(XDG_CONFIG_HOME="")
        self.assertEqual(
            self.home / ".config" / "opencode" / "opencode.json",
            OpenCodeAdapter.config_path(env, self.home),
        )
        config = self.home / ".config" / "goose"
        config.mkdir(parents=True)
        (config / "config.yaml").write_text("GOOSE_MODEL: some-model\n", encoding="utf-8")
        self.assertEqual("some-model", GooseAdapter._configured_model(env, self.home))
