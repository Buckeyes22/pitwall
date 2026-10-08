"""Per-harness pitwall-channel registration (plan Task 16)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import yaml

from pitwall.agents import yaml_channel
from pitwall.agents.capability_inventory import (
    mcp_channel_registered,
)
from pitwall.agents.mcp_registration import (
    FORWARDED_ENV,
    RegistrationError,
    current_entry,
    plan_project_registration,
    plan_registration,
)
from pitwall.agents.profiles_sync import apply_plan
from tests.agents.shim_test_support import PITWALL

ROOT = Path(__file__).resolve().parents[2]

CMD = "/opt/pitwall/bin/pitwall"
ARGS = ["mcp", "serve", "channel"]


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.home = Path(self._temp.name)
        self.env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _apply(self, harness: str, *, remove: bool = False):  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
        plan = plan_registration(harness, self.env, self.home, command=CMD, remove=remove)
        apply_plan(plan)
        return plan

    def test_codex_block_is_valid_toml_idempotent_and_removable(self) -> None:
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir()
        config.write_text(
            'model = "gpt-5.6-sol"\n\n[mcp_servers.docs]\ncommand = "docs"\n', encoding="utf-8"
        )
        original = config.read_text(encoding="utf-8")
        self._apply("codex")
        data = tomllib.loads(config.read_text(encoding="utf-8"))
        entry = data["mcp_servers"]["pitwall-channel"]
        self.assertEqual(
            (CMD, ARGS, list(FORWARDED_ENV), 3660),
            (entry["command"], entry["args"], entry["env_vars"], entry["tool_timeout_sec"]),
        )
        self.assertEqual("docs", data["mcp_servers"]["docs"]["command"])
        self.assertFalse(plan_registration("codex", self.env, self.home, command=CMD).changed)
        self.assertTrue(mcp_channel_registered("codex", self.env, self.home))
        self._apply("codex", remove=True)
        self.assertEqual(original, config.read_text(encoding="utf-8"))

    def test_codex_tables_written_inside_the_block_survive_a_resync(self) -> None:
        # Codex appends new tables at the end of the file, which can land before our end marker.
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir()
        config.write_text('model = "gpt-5.6-sol"\n', encoding="utf-8")
        self._apply("codex")
        text = config.read_text(encoding="utf-8")
        foreign = '\n[marketplaces.openai-bundled]\nsource_type = "local"\nsource = "/x/bundled"\n'
        config.write_text(
            text.replace("# <<< pitwall-channel", foreign.lstrip("\n") + "# <<< pitwall-channel"),
            encoding="utf-8",
        )
        self._apply_command("codex", "/new/pitwall")
        data = tomllib.loads(config.read_text(encoding="utf-8"))
        self.assertEqual("/x/bundled", data["marketplaces"]["openai-bundled"]["source"])
        self.assertEqual("/new/pitwall", data["mcp_servers"]["pitwall-channel"]["command"])
        self._apply("codex", remove=True)
        data = tomllib.loads(config.read_text(encoding="utf-8"))
        self.assertEqual("/x/bundled", data["marketplaces"]["openai-bundled"]["source"])
        self.assertNotIn("pitwall-channel", data.get("mcp_servers", {}))

    def test_grok_block_is_valid_toml_idempotent_and_removable(self) -> None:
        config = self.home / ".grok" / "config.toml"
        config.parent.mkdir()
        config.write_text('[mcp_servers.docs]\ncommand = "docs"\nargs = []\n', encoding="utf-8")
        original = config.read_text(encoding="utf-8")
        plan = self._apply("grok")
        self.assertEqual(config, plan.path)
        entry = tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]["pitwall-channel"]
        self.assertEqual({"command": CMD, "args": ARGS, "enabled": True}, entry)
        self.assertTrue(mcp_channel_registered("grok", self.env, self.home))
        self.assertFalse(plan_registration("grok", self.env, self.home, command=CMD).changed)
        self._apply("grok", remove=True)
        self.assertEqual(original, config.read_text(encoding="utf-8"))
        self.assertFalse(mcp_channel_registered("grok", self.env, self.home))

    def test_grok_reformatted_block_resyncs_to_one_table(self) -> None:
        config = self.home / ".grok" / "config.toml"
        config.parent.mkdir()
        self._apply("grok")
        text = config.read_text(encoding="utf-8")
        # `grok mcp add` rewrites arrays one item per line inside our markers.
        config.write_text(
            text.replace(
                'args = ["mcp", "serve", "channel"]',
                'args = [\n    "mcp",\n    "serve",\n    "channel",\n]',
            ),
            encoding="utf-8",
        )
        self.assertNotIn('args = ["mcp", "serve", "channel"]', config.read_text(encoding="utf-8"))
        self._apply_command("grok", "/new/pitwall")
        rewritten = config.read_text(encoding="utf-8")
        self.assertEqual(1, rewritten.count("[mcp_servers.pitwall-channel]"))
        self.assertEqual(1, rewritten.count("# >>> pitwall-channel"))
        self.assertIn('args = ["mcp", "serve", "channel"]\n', rewritten)
        self.assertNotIn("args = [\n", rewritten)
        entry = tomllib.loads(rewritten)["mcp_servers"]["pitwall-channel"]
        self.assertEqual("/new/pitwall", entry["command"])

    UNMARKED_GROK = (
        "[mcp_servers.pitwall-channel]\n"
        f'command = "{CMD}"\n'
        'args = [\n    "mcp",\n    "serve",\n    "channel",\n]\n'
        "enabled = true\n"
        "\n"
        "[mcp_servers.other]\n"
        'command = "/bin/true"\n'
        "args = []\n"
        "enabled = true\n"
    )

    def test_grok_unmarked_own_table_is_adopted_on_resync(self) -> None:
        config = self.home / ".grok" / "config.toml"
        config.parent.mkdir()
        config.write_text(self.UNMARKED_GROK, encoding="utf-8")
        self._apply_command("grok", "/new/pitwall")
        rewritten = config.read_text(encoding="utf-8")
        self.assertEqual(1, rewritten.count("[mcp_servers.pitwall-channel]"))
        self.assertEqual(1, rewritten.count("# >>> pitwall-channel"))
        servers = tomllib.loads(rewritten)["mcp_servers"]
        self.assertEqual("/new/pitwall", servers["pitwall-channel"]["command"])
        self.assertEqual("/bin/true", servers["other"]["command"])

    def test_grok_unmarked_own_table_is_removed_keeping_other_servers(self) -> None:
        config = self.home / ".grok" / "config.toml"
        config.parent.mkdir()
        config.write_text(self.UNMARKED_GROK, encoding="utf-8")
        self._apply("grok", remove=True)
        servers = tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]
        self.assertEqual(["other"], list(servers))
        self.assertFalse(mcp_channel_registered("grok", self.env, self.home))

    def test_grok_foreign_pitwall_channel_table_is_refused(self) -> None:
        config = self.home / ".grok" / "config.toml"
        config.parent.mkdir()
        config.write_text(
            '[mcp_servers.pitwall-channel]\ncommand = "someone-else"\nargs = []\n',
            encoding="utf-8",
        )
        with self.assertRaises(RegistrationError):
            plan_registration("grok", self.env, self.home, command=CMD)

    def _assert_unmarked_table_refused(self, text: str) -> None:
        config = self.home / ".grok" / "config.toml"
        config.parent.mkdir()
        config.write_text(text, encoding="utf-8")
        for remove in (False, True):
            with self.subTest(remove=remove):
                with self.assertRaises(RegistrationError) as caught:
                    self._apply("grok", remove=remove)
                self.assertIn("cannot isolate", str(caught.exception))
                self.assertEqual(text, config.read_text(encoding="utf-8"))

    def test_grok_unmarked_table_with_quoted_key_is_refused_unchanged(self) -> None:
        self._assert_unmarked_table_refused(
            '[mcp_servers."pitwall-channel"]\n'
            f'command = "{CMD}"\n'
            'args = ["mcp", "serve", "channel"]\n'
            "\n"
            "[mcp_servers.other]\n"
            'command = "/bin/true"\n'
        )

    def test_grok_unmarked_table_with_multiline_string_is_refused_unchanged(self) -> None:
        self._assert_unmarked_table_refused(
            "[mcp_servers.pitwall-channel]\n"
            f'command = "{CMD}"\n'
            'args = ["mcp", "serve", "channel"]\n'
            'note = """\n[mcp_servers.other]\nkept = true\n"""\n'
            "\n"
            "[mcp_servers.other]\n"
            'command = "/bin/true"\n'
        )

    OURS = (
        "[mcp_servers.pitwall-channel]\n"
        f'command = "{CMD}"\n'
        'args = ["mcp", "serve", "channel"]\n'
        "enabled = true\n"
    )

    def _assert_unmarked_table_is_edited(self, text: str, survivors: tuple[str, ...]) -> None:
        config = self.home / ".grok" / "config.toml"
        config.parent.mkdir(exist_ok=True)
        for remove in (False, True):
            with self.subTest(remove=remove):
                config.write_bytes(text.encode())
                if remove:
                    self._apply("grok", remove=True)
                else:
                    self._apply_command("grok", "/new/pitwall")
                after = config.read_text(encoding="utf-8")
                for survivor in survivors:
                    self.assertIn(survivor, after)
                servers = tomllib.loads(after).get("mcp_servers", {})
                if remove:
                    self.assertNotIn("pitwall-channel", servers)
                    self.assertNotIn("pitwall-channel", after)
                else:
                    self.assertEqual("/new/pitwall", servers["pitwall-channel"]["command"])
                    self.assertEqual(1, after.count("[mcp_servers.pitwall-channel]"))

    def test_grok_unmarked_table_before_a_slash_quoted_table_keeps_it_byte_for_byte(self) -> None:
        projects = '[projects."/srv/work/project"]\ntrust_level = "trusted"\n'
        self._assert_unmarked_table_is_edited(self.OURS + "\n" + projects, (projects,))

    def test_grok_unmarked_table_after_an_empty_parent_header_is_edited(self) -> None:
        self._assert_unmarked_table_is_edited("[mcp_servers]\n\n" + self.OURS, ())

    def test_grok_normal_unmarked_shapes_are_not_refused(self) -> None:
        other = '[x]\nkey = "value"\n'
        shapes = {
            "following table": (self.OURS + "\n" + other, (other,)),
            "slash-quoted table": (
                self.OURS + '\n[projects."/p"]\nt = 1\n',
                ('[projects."/p"]\nt = 1\n',),
            ),
            "crlf": ((self.OURS + "\n" + other).replace("\n", "\r\n"), (other,)),
            "comments": (
                "[mcp_servers.pitwall-channel]  # ours\n"
                f'command = "{CMD}"  # the binary\n'
                "# a comment line\n"
                'args = ["mcp", "serve", "channel"]\n'
                "enabled = true\n"
                "# trailing comment\n"
                "\n" + other,
                (other,),
            ),
            "inline table": (
                self.OURS + 'env = { A = "1", B = "[x]" }\n\n' + other,
                (other,),
            ),
            "array of tables after": (
                self.OURS + '\n[[hooks]]\nname = "a"\n',
                ('[[hooks]]\nname = "a"\n',),
            ),
        }
        for name, (text, survivors) in shapes.items():
            with self.subTest(shape=name):
                self._assert_unmarked_table_is_edited(text, survivors)

    def test_agy_and_muse_entries(self) -> None:
        expected = {
            "agy": (
                self.home / ".gemini" / "config" / "mcp_config.json",
                {"command": CMD, "args": ARGS, "disabled": False},
            ),
            "muse": (
                self.home / ".config" / "muse" / "settings.json",
                {
                    "command": CMD,
                    "args": ARGS,
                    "env_vars": list(FORWARDED_ENV),
                    "startup_timeout_sec": 30,
                },
            ),
        }
        for harness, (path, entry) in expected.items():
            with self.subTest(harness=harness):
                plan = self._apply(harness)
                self.assertEqual(path, plan.path)
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(entry, data["mcpServers"]["pitwall-channel"])
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
                self.assertTrue(mcp_channel_registered(harness, self.env, self.home))
                self.assertFalse(
                    plan_registration(harness, self.env, self.home, command=CMD).changed
                )
                self._apply(harness, remove=True)
                self.assertFalse(mcp_channel_registered(harness, self.env, self.home))

    def test_muse_settings_gain_schema_version_and_keep_keys(self) -> None:
        fresh = self._apply("muse")
        self.assertEqual(1, json.loads(fresh.path.read_text(encoding="utf-8"))["schema_version"])
        fresh.path.write_text(
            '{"model": "muse-spark-1.3", "tui": {"theme": "dark"}}', encoding="utf-8"
        )
        self._apply("muse")
        data = json.loads(fresh.path.read_text(encoding="utf-8"))
        self.assertEqual(
            (1, "muse-spark-1.3", {"theme": "dark"}),
            (data["schema_version"], data["model"], data["tui"]),
        )

    def test_muse_non_integer_schema_version_survives_register_and_unregister(self) -> None:
        path = self.home / ".config" / "muse" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"schema_version": "2-beta"}', encoding="utf-8")
        self._apply("muse")
        self.assertEqual("2-beta", json.loads(path.read_text(encoding="utf-8"))["schema_version"])
        self._apply("muse", remove=True)
        self.assertEqual("2-beta", json.loads(path.read_text(encoding="utf-8"))["schema_version"])

    def test_muse_schema_version_survives_unregister(self) -> None:
        plan = self._apply("muse")
        self._apply("muse", remove=True)
        data = json.loads(plan.path.read_text(encoding="utf-8"))
        self.assertEqual(1, data["schema_version"])
        self.assertNotIn("pitwall-channel", data.get("mcpServers", {}))

    def test_muse_settings_path_follows_xdg_config_home(self) -> None:
        self.env["XDG_CONFIG_HOME"] = str(self.home / "xdg")
        plan = self._apply("muse")
        self.assertEqual(self.home / "xdg" / "muse" / "settings.json", plan.path)
        self.assertFalse((self.home / ".config" / "muse").exists())

    def _apply_command(self, harness: str, command: str):  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
        plan = plan_registration(harness, self.env, self.home, command=command)
        apply_plan(plan)
        return plan

    def test_json_harness_entries(self) -> None:
        expected = {
            "copilot": (
                "mcpServers",
                {
                    "type": "local",
                    "command": CMD,
                    "args": ARGS,
                    "env": {v: "${" + v + "}" for v in FORWARDED_ENV},
                    "tools": ["*"],
                },
            ),
            "opencode": (
                "mcp",
                {
                    "type": "local",
                    "command": [CMD, *ARGS],
                    "enabled": True,
                    "environment": {v: "{env:" + v + "}" for v in FORWARDED_ENV},
                },
            ),
            "kimi": ("mcpServers", {"command": CMD, "args": ARGS, "toolTimeoutMs": 3660000}),
            "cline": (
                "mcpServers",
                {
                    "type": "stdio",
                    "command": CMD,
                    "args": ARGS,
                    "timeout": 3660,
                    "disabled": False,
                },
            ),
            "qwen": ("mcpServers", {"command": CMD, "args": ARGS, "timeout": 3660000}),
        }
        for harness, (section, entry) in expected.items():
            with self.subTest(harness=harness):
                plan = self._apply(harness)
                data = json.loads(plan.path.read_text(encoding="utf-8"))
                self.assertEqual(entry, data[section]["pitwall-channel"])
                self.assertEqual(0o600, plan.path.stat().st_mode & 0o777)
                self.assertTrue(mcp_channel_registered(harness, self.env, self.home))
                self._apply(harness, remove=True)
                self.assertNotIn(
                    "pitwall-channel",
                    json.loads(plan.path.read_text(encoding="utf-8")).get(section, {}),
                )

    def test_qwen_registration_keeps_existing_settings(self) -> None:
        settings = self.home / ".qwen" / "settings.json"
        settings.parent.mkdir()
        original = {
            "$version": 3,
            "model": {"name": "qwen3.8"},
            "mcpServers": {"docs": {"command": "docs"}},
        }
        settings.write_text(json.dumps(original), encoding="utf-8")
        plan = self._apply("qwen")
        self.assertEqual(settings, plan.path)
        data = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(3, data["$version"])
        self.assertEqual({"name": "qwen3.8"}, data["model"])
        self.assertEqual({"command": "docs"}, data["mcpServers"]["docs"])
        self.assertTrue(mcp_channel_registered("qwen", self.env, self.home))
        self.assertFalse(plan_registration("qwen", self.env, self.home, command=CMD).changed)
        self._apply("qwen", remove=True)
        self.assertEqual(original, json.loads(settings.read_text(encoding="utf-8")))

    def test_zcode_registers_under_nested_mcp_servers_and_keeps_settings(self) -> None:
        config = self.home / ".zcode" / "cli" / "config.json"
        config.parent.mkdir(parents=True)
        original = {
            "theme": "dark",
            "mcp": {"servers": {"docs": {"command": "docs"}}, "autoStart": True},
        }
        config.write_text(json.dumps(original), encoding="utf-8")
        plan = self._apply("zcode")
        self.assertEqual(config, plan.path)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "type": "stdio",
                "command": CMD,
                "args": ARGS,
                "enabled": True,
                "timeoutMs": 3660000,
            },
            data["mcp"]["servers"]["pitwall-channel"],
        )
        self.assertEqual({"command": "docs"}, data["mcp"]["servers"]["docs"])
        self.assertEqual(("dark", True), (data["theme"], data["mcp"]["autoStart"]))
        self.assertTrue(mcp_channel_registered("zcode", self.env, self.home))
        self.assertFalse(plan_registration("zcode", self.env, self.home, command=CMD).changed)
        self._apply("zcode", remove=True)
        self.assertEqual(original, json.loads(config.read_text(encoding="utf-8")))
        self.assertFalse(mcp_channel_registered("zcode", self.env, self.home))

    def test_zcode_fresh_registration_and_foreign_entry_refusal(self) -> None:
        plan = self._apply("zcode")
        data = json.loads(plan.path.read_text(encoding="utf-8"))
        self.assertIn("pitwall-channel", data["mcp"]["servers"])
        self.assertEqual(0o600, plan.path.stat().st_mode & 0o777)
        plan.path.write_text(
            '{"mcp": {"servers": {"pitwall-channel": {"command": "someone-else"}}}}',
            encoding="utf-8",
        )
        with self.assertRaises(RegistrationError):
            plan_registration("zcode", self.env, self.home, command=CMD)

    def test_claude_registers_through_its_cli(self) -> None:
        plan = plan_registration("claude", self.env, self.home, command=CMD)
        self.assertEqual(plan.before, plan.after)  # ~/.claude.json is never edited directly
        (command,) = plan.commands
        self.assertEqual(
            ["claude", "mcp", "add-json", "--scope", "user", "pitwall-channel"], list(command[:6])
        )
        entry = json.loads(command[6])
        self.assertEqual({v: "${" + v + ":-}" for v in FORWARDED_ENV}, entry["env"])

    def test_foreign_entries_and_jsonc_are_refused(self) -> None:
        kimi = self.home / ".kimi-code" / "mcp.json"
        kimi.parent.mkdir()
        kimi.write_text(
            '{"mcpServers": {"pitwall-channel": {"command": "someone-else"}}}', encoding="utf-8"
        )
        with self.assertRaises(RegistrationError):
            plan_registration("kimi", self.env, self.home, command=CMD)
        opencode = self.home / ".config" / "opencode" / "opencode.json"
        opencode.parent.mkdir(parents=True)
        opencode.write_text("{\n  // comment\n}\n", encoding="utf-8")
        with self.assertRaises(RegistrationError):
            plan_registration("opencode", self.env, self.home, command=CMD)

    def test_hermes_entry_is_one_managed_line_under_mcp_servers(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        original = (
            "model:\n  default: glm-5.2\n\n"
            "mcp_servers:\n  docs:\n    command: docs\n    args: []\n\n"
            'providers:\n  # managed by pitwall\n  local:\n    base_url: "http://127.0.0.1:8000/v1"\n'
        )
        config.write_text(original, encoding="utf-8")
        self._apply("hermes")
        text = config.read_text(encoding="utf-8")
        self.assertIn('  # managed by pitwall\n  pitwall-channel: {"command": ', text)
        self.assertLess(text.index("pitwall-channel"), text.index("providers:"))
        self.assertEqual(
            {
                "command": CMD,
                "args": ARGS,
                "env": {v: "${" + v + "}" for v in FORWARDED_ENV},
                "timeout": 3660,
            },
            current_entry("hermes", self.env, self.home),
        )
        self.assertTrue(mcp_channel_registered("hermes", self.env, self.home))
        self.assertFalse(plan_registration("hermes", self.env, self.home, command=CMD).changed)
        self._apply("hermes", remove=True)
        self.assertEqual(original, config.read_text(encoding="utf-8"))
        self.assertFalse(mcp_channel_registered("hermes", self.env, self.home))

    def test_goose_entry_and_block_style_rewrite(self) -> None:
        plan = self._apply("goose")
        self.assertEqual(self.home / ".config" / "goose" / "config.yaml", plan.path)
        entry = current_entry("goose", self.env, self.home)
        assert entry is not None
        self.assertEqual(
            ("stdio", CMD, CMD, ARGS, [], 3660),
            (
                entry["type"],
                entry["cmd"],
                entry["command"],
                entry["args"],
                entry["env_keys"],
                entry["timeout"],
            ),
        )
        # `goose configure` rewrites config.yaml in block style and drops comments.
        plan.path.write_text(
            "extensions:\n  pitwall-channel:\n    enabled: true\n    type: stdio\n"
            f"    cmd: {CMD}\n    args:\n    - mcp\n    - serve\n    - channel\n"
            "    timeout: 3660\nGOOSE_MODEL: x\n",
            encoding="utf-8",
        )
        self.assertTrue(mcp_channel_registered("goose", self.env, self.home))
        self._apply_command("goose", "/new/pitwall")
        rewritten = current_entry("goose", self.env, self.home)
        assert rewritten is not None
        self.assertEqual("/new/pitwall", rewritten["cmd"])
        self.assertEqual(1, plan.path.read_text(encoding="utf-8").count("pitwall-channel:"))
        self._apply("goose", remove=True)
        # The rewrite dropped the header's marker, so the header may be the user's: it stays.
        self.assertEqual("extensions:\nGOOSE_MODEL: x\n", plan.path.read_text(encoding="utf-8"))

    def test_yaml_foreign_entry_is_refused(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        config.write_text(
            "mcp_servers:\n  pitwall-channel:\n    command: someone-else\n", encoding="utf-8"
        )
        with self.assertRaises(RegistrationError):
            plan_registration("hermes", self.env, self.home, command=CMD)

    def test_yaml_inline_section_is_refused(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        config.write_text("mcp_servers: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(RegistrationError, "not a block mapping"):
            plan_registration("hermes", self.env, self.home, command=CMD)

    def test_yaml_without_trailing_newline_is_refused(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        config.write_text("model:\n  default: glm-5.2", encoding="utf-8")
        with self.assertRaisesRegex(RegistrationError, "newline"):
            plan_registration("hermes", self.env, self.home, command=CMD)

    def test_yaml_section_not_indented_two_spaces_is_refused_unchanged(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        original = "mcp_servers:\n    docs:\n        command: docs\nother: 1\n"
        config.write_text(original, encoding="utf-8")
        with self.assertRaisesRegex(RegistrationError, "4 spaces.*2 spaces"):
            plan_registration("hermes", self.env, self.home, command=CMD)
        self.assertEqual(original, config.read_text(encoding="utf-8"))
        indented = original + "extensions:\n    pitwall-channel:\n        cmd: x\n"
        config.write_text(indented, encoding="utf-8")
        self.assertIsNone(current_entry("hermes", self.env, self.home))
        goose = self.home / ".config" / "goose" / "config.yaml"
        goose.parent.mkdir(parents=True)
        goose.write_text("extensions:\n    pitwall-channel:\n        cmd: x\n", encoding="utf-8")
        with self.assertRaisesRegex(RegistrationError, "4 spaces"):
            plan_registration("goose", self.env, self.home, command=CMD)
        # Reading does not depend on indentation; only editing is refused.
        self.assertEqual({"cmd": "x", "command": "x"}, current_entry("goose", self.env, self.home))

    def test_yaml_quoted_top_level_key_with_colon_is_refused(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        original = 'mcp_servers:\n  docs:\n    command: docs\n"http://x": 1\n'
        config.write_text(original, encoding="utf-8")
        with self.assertRaises(RegistrationError):
            plan_registration("hermes", self.env, self.home, command=CMD)
        self.assertEqual(original, config.read_text(encoding="utf-8"))

    def test_yaml_crlf_block_entry_is_replaced_once(self) -> None:
        plan = plan_registration("goose", self.env, self.home, command=CMD)
        plan.path.parent.mkdir(parents=True)
        plan.path.write_bytes(
            (
                "extensions:\r\n  pitwall-channel:\r\n    enabled: true\r\n    type: stdio\r\n"
                f"    cmd: {CMD}\r\n    args:\r\n    - mcp\r\n    - serve\r\n    - channel\r\n"
                "GOOSE_MODEL: x\r\n"
            ).encode()
        )
        self.assertTrue(mcp_channel_registered("goose", self.env, self.home))
        self._apply_command("goose", "/new/pitwall")
        text = plan.path.read_bytes().decode()
        self.assertEqual(1, text.count("pitwall-channel:"))
        loaded = yaml.safe_load(text)
        self.assertEqual("/new/pitwall", loaded["extensions"]["pitwall-channel"]["cmd"])
        self.assertEqual("x", loaded["GOOSE_MODEL"])
        # read_text normalises newlines, so also hand the module CRLF text directly.
        crlf = "extensions:\r\n  pitwall-channel:\r\n    cmd: a\r\nGOOSE_MODEL: x\r\n"
        self.assertEqual(
            {"cmd": "a"}, yaml_channel.read_entry(crlf, "extensions", "pitwall-channel")
        )
        replaced = yaml_channel.plan_text(
            crlf, "extensions", "pitwall-channel", {"cmd": "b"}, lambda _entry: True
        )
        self.assertEqual(1, replaced.count("pitwall-channel:"))
        self.assertEqual("b", yaml.safe_load(replaced)["extensions"]["pitwall-channel"]["cmd"])

    def test_yaml_removal_keeps_a_user_written_header_and_drops_its_own(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        user_header = "model: x\nmcp_servers:\nother: 1\n"
        config.write_text(user_header, encoding="utf-8")
        self._apply("hermes")
        self.assertTrue(mcp_channel_registered("hermes", self.env, self.home))
        self._apply("hermes", remove=True)
        self.assertEqual(user_header, config.read_text(encoding="utf-8"))
        config.write_text("model: x\n", encoding="utf-8")
        self._apply("hermes")
        self._apply("hermes", remove=True)
        self.assertEqual("model: x\n", config.read_text(encoding="utf-8"))

    def _goose_config(self, body: str) -> Path:
        path = self.home / ".config" / "goose" / "config.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(body, encoding="utf-8")
        return path

    def test_yaml_entry_with_comments_is_still_ours(self) -> None:
        path = self._goose_config(
            "extensions:\n  pitwall-channel:\n    enabled: true\n    type: stdio\n"
            "    cmd: /x/pitwall # note\n    args:\n    - mcp # c\n    - serve\n"
            "    - channel\nGOOSE_MODEL: x\n"
        )
        entry = current_entry("goose", self.env, self.home)
        assert entry is not None
        self.assertEqual(("/x/pitwall", ["mcp", "serve", "channel"]), (entry["cmd"], entry["args"]))
        self._apply_command("goose", "/new/pitwall")
        self.assertEqual(1, path.read_text(encoding="utf-8").count("pitwall-channel:"))
        rewritten = current_entry("goose", self.env, self.home)
        assert rewritten is not None
        self.assertEqual("/new/pitwall", rewritten["cmd"])
        self._apply("goose", remove=True)
        self.assertEqual("extensions:\nGOOSE_MODEL: x\n", path.read_text(encoding="utf-8"))

    def test_yaml_double_quoted_escape_reads_back(self) -> None:
        entry = yaml_channel.read_entry(
            'extensions:\n  pitwall-channel:\n    cmd: "/x/a\\"b/pitwall"\n',
            "extensions",
            "pitwall-channel",
        )
        assert entry is not None
        self.assertEqual('/x/a"b/pitwall', entry["cmd"])

    def test_yaml_entry_with_block_list_beside_cmd_and_args_is_read(self) -> None:
        path = self._goose_config(
            f"extensions:\n  pitwall-channel:\n    cmd: {CMD}\n    env_keys:\n    - FOO\n"
            "    args:\n    - mcp\n    - serve\n    - channel\n    timeout: 3660\n"
        )
        entry = current_entry("goose", self.env, self.home)
        assert entry is not None
        self.assertEqual(
            (CMD, ARGS, ["FOO"], 3660),
            (entry["cmd"], entry["args"], entry["env_keys"], entry["timeout"]),
        )
        self._apply("goose")
        self.assertEqual(1, path.read_text(encoding="utf-8").count("pitwall-channel:"))

    def test_yaml_section_that_is_not_a_mapping_is_refused_unchanged(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        for original in ("mcp_servers:\n  - a\n", "mcp_servers:\n  a: [unclosed\n"):
            with self.subTest(original=original):
                config.write_text(original, encoding="utf-8")
                with self.assertRaises(RegistrationError):
                    plan_registration("hermes", self.env, self.home, command=CMD)
                self.assertEqual(original, config.read_text(encoding="utf-8"))
        self.assertFalse(mcp_channel_registered("hermes", self.env, self.home))
        self.assertIsNone(current_entry("hermes", self.env, self.home))

    def test_yaml_command_path_outside_ascii_round_trips(self) -> None:
        for command in (
            "/home/\U0001f600/pitwall",
            "/opt/café \x85\x9f/pitwall",
            "/opt/del\x7f/pitwall",
        ):
            for harness, key in (("hermes", "command"), ("goose", "cmd")):
                with self.subTest(command=command, harness=harness):
                    plan = self._apply_command(harness, command)
                    entry = current_entry(harness, self.env, self.home)
                    assert entry is not None
                    self.assertEqual(command, entry[key])
                    if "\U0001f600" in command:
                        self.assertIn(command, plan.path.read_bytes().decode("utf-8"))

    def test_yaml_parse_error_refusal_never_echoes_the_config(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        config.write_text(
            "model: x\nmcp_servers:\n  docs:\n    TOKEN: hunter2\n    API_KEY: sk-SECRET123: x\n",
            encoding="utf-8",
        )
        for remove in (False, True):
            with self.subTest(remove=remove):
                with self.assertRaises(RegistrationError) as caught:
                    plan_registration("hermes", self.env, self.home, command=CMD, remove=remove)
                message = str(caught.exception)
                self.assertIn("line 5, column 26", message)
                self.assertNotIn("sk-SECRET123", message)
                self.assertNotIn("hunter2", message)
                self.assertIsNone(caught.exception.__cause__.__cause__)  # type: ignore[union-attr]  # reason: the cause is the YamlChannelError, asserted non-None by the access
        with self.assertRaises(yaml_channel.YamlChannelError) as reader:
            yaml_channel.read_entry("a: b\x01\n", "mcp_servers", "pitwall-channel")
        self.assertNotIn("\x01", str(reader.exception))
        self.assertNotIn("a: b", str(reader.exception))

    def test_yaml_entry_the_line_editor_cannot_locate_is_refused_unchanged(self) -> None:
        original = (
            f'extensions:\n  "pitwall-channel": {{"cmd": "{CMD}", "args": {ARGS!r}}}\n'.replace(
                "'", '"'
            )
        )
        path = self._goose_config(original)
        self.assertTrue(mcp_channel_registered("goose", self.env, self.home))
        with self.assertRaisesRegex(RegistrationError, "cannot locate"):
            plan_registration("goose", self.env, self.home, command=CMD)
        self.assertEqual(original, path.read_text(encoding="utf-8"))

    def test_setup_mcp_dry_run_writes_nothing(self) -> None:
        result = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "setup",
                "mcp",
                "--harness",
                "kimi",
                "--command",
                CMD,
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            env={**self.env, "PATH": "/usr/bin:/bin"},
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("pitwall-channel", result.stdout)
        self.assertFalse((self.home / ".kimi-code" / "mcp.json").exists())

    def _setup_mcp_output(self, *args: object, **kwargs: object) -> tuple[int, str]:
        from pitwall.agents.cli import setup_mcp

        buffer = io.StringIO()
        with (
            mock.patch.dict(os.environ, self.env, clear=False),
            contextlib.redirect_stdout(buffer),
        ):
            code = setup_mcp(*args, **kwargs)  # type: ignore[arg-type]  # reason: test forwards arbitrary setup_mcp arguments
        return code, buffer.getvalue()

    def test_setup_mcp_preview_shows_only_the_path_and_the_managed_entry(self) -> None:
        config = self.home / ".kimi-code" / "mcp.json"
        config.parent.mkdir()
        original = json.dumps(
            {"mcpServers": {"other": {"env": {"TOKEN": "SYNTHETIC_TEST_MARKER"}}}}
        )
        config.write_text(original, encoding="utf-8")
        code, output = self._setup_mcp_output(["kimi"], CMD, False, True, True)
        self.assertEqual(0, code)
        self.assertNotIn("SYNTHETIC_TEST_MARKER", output)
        self.assertNotIn('"other"', output)
        self.assertIn(str(config), output)
        self.assertIn("pitwall-channel", output)
        self.assertIn(CMD, output)
        self.assertEqual(original, config.read_text(encoding="utf-8"))

    def test_setup_mcp_removal_preview_never_echoes_the_rest_of_the_config(self) -> None:
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir()
        config.write_text('token = "SYNTHETIC_TEST_MARKER"\n', encoding="utf-8")
        self._apply("codex")
        code, output = self._setup_mcp_output(["codex"], CMD, True, True, True)
        self.assertEqual(0, code)
        self.assertNotIn("SYNTHETIC_TEST_MARKER", output)
        self.assertIn(str(config), output)
        self.assertIn("pitwall-channel", output)

    def test_setup_mcp_project_scope_writes_the_project_file_only(self) -> None:
        project = self.home / "project"
        project.mkdir()
        code, _output = self._setup_mcp_output(
            ["opencode"], CMD, False, False, True, scope="project", project_root=project
        )
        self.assertEqual(0, code)
        entry = json.loads((project / "opencode.json").read_text(encoding="utf-8"))["mcp"]
        self.assertEqual([CMD, *ARGS], entry["pitwall-channel"]["command"])
        self.assertFalse((self.home / ".config" / "opencode" / "opencode.json").exists())

    def test_project_registration_plans_the_project_file(self) -> None:
        project = self.home / "project"
        plan = plan_project_registration("claude", project, CMD)
        self.assertEqual(project / ".mcp.json", plan.path)
        self.assertEqual(
            [CMD, ARGS],
            [
                json.loads(plan.after)["mcpServers"]["pitwall-channel"][k]
                for k in ("command", "args")
            ],
        )
        self.assertEqual(
            project / "opencode.json", plan_project_registration("opencode", project, CMD).path
        )
        with self.assertRaisesRegex(RegistrationError, "kimi"):
            plan_project_registration("kimi", project, CMD)
        self.assertFalse(project.exists())

    def test_setup_mcp_project_scope_refuses_a_harness_without_project_support(self) -> None:
        project = self.home / "project"
        project.mkdir()
        code, _output = self._setup_mcp_output(
            ["kimi"], CMD, False, False, True, scope="project", project_root=project
        )
        self.assertEqual(2, code)
        self.assertFalse((self.home / ".kimi-code" / "mcp.json").exists())
        self.assertEqual([], list(project.iterdir()))


if __name__ == "__main__":
    unittest.main()
