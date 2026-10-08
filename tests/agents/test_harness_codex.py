"""The Codex adapter reads its default model from $CODEX_HOME/config.toml."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pitwall.agents.harnesses import get_adapter
from pitwall.agents.paths import codex_home


def write_config(directory: Path, model: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text(f'model = "{model}"\n', encoding="utf-8")


class CodexHomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.home = self.root / "home"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def model(self, env: dict[str, str]) -> str:
        return get_adapter("codex").parse(["prompt.md"], env, self.home).model

    def test_codex_home_config_wins_over_the_default_account(self) -> None:
        write_config(self.home / ".codex", "old-model")
        write_config(self.root / "alt", "new-model")
        self.assertEqual("new-model", self.model({"CODEX_HOME": str(self.root / "alt")}))

    def test_without_codex_home_the_default_directory_is_read(self) -> None:
        write_config(self.home / ".codex", "old-model")
        self.assertEqual("old-model", self.model({}))
        self.assertEqual("old-model", self.model({"CODEX_HOME": ""}))

    def test_codex_home_without_a_config_does_not_fall_back_to_the_default_account(self) -> None:
        write_config(self.home / ".codex", "old-model")
        (self.root / "empty").mkdir()
        self.assertEqual("codex-default", self.model({"CODEX_HOME": str(self.root / "empty")}))

    def test_codex_home_expands_a_leading_tilde(self) -> None:
        write_config(self.home / "alt", "tilde-model")
        self.assertEqual("tilde-model", self.model({"CODEX_HOME": "~/alt", "HOME": str(self.home)}))


class CodexHomeResolverTests(unittest.TestCase):
    def test_default_tilde_and_absolute_forms(self) -> None:
        home = Path("/h")
        self.assertEqual(Path("/h/.codex"), codex_home({}, home))
        self.assertEqual(Path("/h/.codex"), codex_home({"CODEX_HOME": ""}, home))
        self.assertEqual(Path("/h/alt"), codex_home({"CODEX_HOME": "~/alt"}, home))
        self.assertEqual(Path("/h"), codex_home({"CODEX_HOME": "~"}, home))
        self.assertEqual(Path("/x/y"), codex_home({"CODEX_HOME": "/x/y"}, home))

    def test_every_consumer_resolves_a_tilde_codex_home_against_the_given_home(self) -> None:
        from pitwall.agents import capability_inventory, discovery
        from pitwall.agents.usage import accounts
        from pitwall.mcp_install import config_path

        home = Path("/h")
        env = {"CODEX_HOME": "~/alt"}
        self.assertEqual(
            Path("/h/alt/config.toml"), capability_inventory.channel_config_path("codex", env, home)
        )
        self.assertEqual(
            Path("/h/alt/config.toml"),
            config_path("codex", "user", environ=env, home=home, project_root=Path("/p")),
        )
        with tempfile.TemporaryDirectory() as directory:
            real_home = Path(directory)
            cache = real_home / "alt" / "models_cache.json"
            cache.parent.mkdir()
            cache.write_text("{}", encoding="utf-8")
            self.assertEqual(
                cache,
                discovery._resolve_codex_cache_path({"HOME": directory, "CODEX_HOME": "~/alt"}),
            )
            auth = real_home / "alt" / "auth.json"
            auth.write_text("{}", encoding="utf-8")
            (account,) = accounts._login_accounts(
                "codex",
                {"models": {}, "defaults": {}},
                {},
                {"CODEX_HOME": "~/alt"},
                real_home,
            )
            self.assertEqual(real_home / "alt", account.login_dir)


if __name__ == "__main__":
    unittest.main()
