"""Accounts are found from login directories, keys, routes, and installed harnesses."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pitwall.agents import profiles
from pitwall.agents.registry import (
    load_registry,
)
from pitwall.agents.usage import (
    accounts,
)

ROOT = Path(__file__).resolve().parents[2]


class AccountDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {"HOME": str(self.home)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def login(self, directory: Path, name: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text("{}", encoding="utf-8")
        return directory

    def config(
        self, models: dict[str, object], endpoints: dict[str, object] | None = None
    ) -> dict[str, object]:
        document = {
            "schemaVersion": 1,
            "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
            "endpoints": endpoints or {},
            "models": models,
        }
        return profiles.validate_profiles(document, registry=self.registry)

    def discover(
        self, config: dict[str, object], installed: tuple[str, ...] = ()
    ) -> list[accounts.Account]:
        return accounts.discover(
            config,
            self.registry,
            self.env,
            self.home,
            installed=lambda harness: harness in installed,
        )

    def test_nothing_configured_gives_no_accounts(self) -> None:
        self.assertEqual([], self.discover(self.config({})))

    def test_one_login_has_no_label(self) -> None:
        self.login(self.home / ".claude", ".credentials.json")
        found = self.discover(
            self.config({"labelled": {"model": "sonnet", "harness": "claude", "account": "work"}})
        )
        self.assertEqual([accounts.Account("claude", "", (), self.home / ".claude")], found)

    def test_a_route_declares_a_second_login(self) -> None:
        self.login(self.home / ".claude", ".credentials.json")
        second = self.login(self.home / "logins" / ".claude-home", ".credentials.json")
        config = self.config(
            {
                "claude-home": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(second)},
                },
                "claude-home-opus": {
                    "model": "opus",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(second)},
                },
                "claude-work": {"model": "sonnet", "harness": "claude", "account": "work"},
            }
        )
        found = self.discover(config)
        self.assertEqual(
            [
                accounts.Account("claude", "work", (), self.home / ".claude"),
                accounts.Account("claude", "home", ("claude-home", "claude-home-opus"), second),
            ],
            found,
        )

    def test_an_explicit_label_beats_the_directory_name(self) -> None:
        second = self.login(self.home / "logins" / "codex-b", "auth.json")
        config = self.config(
            {
                "spare": {
                    "model": "gpt-5.6-sol",
                    "harness": "codex",
                    "env": {"CODEX_HOME": str(second)},
                    "account": "spare",
                }
            }
        )
        self.assertEqual(
            [accounts.Account("codex", "spare", ("spare",), second)], self.discover(config)
        )

    def test_a_home_relative_directory_is_expanded(self) -> None:
        second = self.login(self.home / "logins" / "second", ".credentials.json")
        config = self.config(
            {
                "two": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": "~/logins/second"},
                }
            }
        )
        self.assertEqual(
            [accounts.Account("claude", "second", ("two",), second)], self.discover(config)
        )

    def test_a_missing_or_relative_login_directory_is_a_problem_that_names_the_route(self) -> None:
        config = self.config(
            {
                "gone": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(self.home / "nowhere")},
                },
                "loose": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": "logins/relative"},
                },
            }
        )
        problems = {account.label: account.problem for account in self.discover(config)}
        self.assertEqual(
            "route gone names a login directory with no login in it", problems["nowhere"]
        )
        self.assertEqual(
            "route loose names a login directory that is not an absolute path", problems["relative"]
        )

    def test_labels_never_collide(self) -> None:
        first = self.login(self.home / "a" / "claude", ".credentials.json")
        second = self.login(self.home / "b" / "claude", ".credentials.json")
        config = self.config(
            {
                "one": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(first)},
                },
                "two": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(second)},
                },
            }
        )
        self.assertEqual(["2", "2-2"], [account.label for account in self.discover(config)])

    def test_derived_labels(self) -> None:
        for name, expected in (
            (".claude-work", "work"),
            (".claude", "2"),
            ("codex_b", "b"),
            ("my login!", "my-login"),
            ("x" * 40, "x" * 16),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    expected,
                    accounts.derived_label(
                        "claude" if "claude" in name else "codex", Path("/srv") / name
                    ),
                )

    def test_keyed_plans_come_from_the_environment_or_the_opencode_store(self) -> None:
        self.assertEqual([], self.discover(self.config({})))
        store = self.home / ".local" / "share" / "opencode"
        store.mkdir(parents=True)
        (store / "auth.json").write_text(
            json.dumps(
                {
                    "zai-coding-plan": {"type": "api", "key": "k1"},
                    "opencode-go": {"type": "api", "key": "k2"},
                }
            ),
            encoding="utf-8",
        )
        self.env["MINIMAX_API_KEY"] = "k3"  # pragma: allowlist secret
        self.assertEqual(
            ["glm", "minimax", "opencode-go"],
            [account.plan for account in self.discover(self.config({}))],
        )

    def test_a_store_that_is_not_an_object_is_ignored(self) -> None:
        store = self.home / ".local" / "share" / "opencode"
        store.mkdir(parents=True)
        (store / "auth.json").write_text("[]", encoding="utf-8")
        self.assertEqual([], self.discover(self.config({})))

    def test_plans_without_a_reader_need_their_harness(self) -> None:
        found = self.discover(self.config({}), installed=("kimi", "agy"))
        self.assertEqual(["kimi", "gemini"], [account.plan for account in found])

    def test_model_studio_token_plan_endpoints_make_one_account(self) -> None:
        endpoint = {
            "kind": "model-studio",
            "plan": "token-plan-personal",
            "tier": "pro",
            "apiKeyEnv": "MODEL_STUDIO_API_KEY",  # pragma: allowlist secret
            "renewsOn": "2026-09-21",
        }
        payg = {
            "kind": "model-studio",
            "plan": "pay-as-you-go",
            "region": "ap-southeast-1",
            "workspace": "ws-1",
            "apiKeyEnv": "MODEL_STUDIO_PAYG_KEY",  # pragma: allowlist secret
        }
        config = self.config(
            {"flash": {"model": "qwen3.8-flash", "endpoint": "ms", "harness": "opencode"}},
            endpoints={"ms": endpoint, "payg": payg},
        )
        found = self.discover(config)
        self.assertEqual(1, len(found))
        self.assertEqual(
            ("model-studio", "", ("flash",)), (found[0].plan, found[0].label, found[0].routes)
        )
        self.assertEqual(["ms"], [name for name, _endpoint in found[0].endpoints])


if __name__ == "__main__":
    unittest.main()
