"""Tests for materializing endpoint routes into config-sync harnesses."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from pitwall.agents import profiles, profiles_sync
from pitwall.agents.broker import PITWALL_TOKEN_ENV
from pitwall.agents.errors import (
    ProfileSyncError,
)
from pitwall.agents.registry import (
    load_registry,
)
from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
BROKER_TOKEN_ENV = "PITWALL_API_TOKEN"  # the broker's own variable; agents never read it


def config_with_endpoint() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "models": {
            "glimmer": {
                "model": "meta-models/Muse-Glimmer-30B",
                "endpoint": {"baseUrl": "http://gpu-1:8000/v1", "apiKeyEnv": "GLIMMER_API_KEY"},
            },
            "local": {
                "model": "my-model",
                "endpoint": {"baseUrl": "http://127.0.0.1:8080/v1"},
                "harness": "qwen",
            },
            "glm": {"model": "zai-coding-plan/glm-5.3"},
        },
    }


class RouteSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        self.env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "PATH": os.environ.get("PATH", ""),
        }
        self.opencode = self.home / "config" / "opencode" / "opencode.json"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def plans(self, harness: str = "opencode") -> list[profiles_sync.SyncPlan]:
        config = profiles.validate_profiles(config_with_endpoint(), registry=self.registry)
        return profiles_sync.plan_sync(
            config, registry=self.registry, env=self.env, home=self.home, harness=harness
        )

    def test_plan_materializes_every_endpoint_entry_with_env_references(self) -> None:
        (plan,) = self.plans()
        self.assertEqual(
            ("opencode", self.opencode, "", ("glimmer", "local")),
            (plan.harness, plan.path, plan.before, plan.routes),
        )
        after = json.loads(plan.after)
        block = after["provider"]["glimmer"]
        self.assertEqual("@ai-sdk/openai-compatible", block["npm"])
        self.assertEqual("pitwall: glimmer", block["name"])
        self.assertEqual(
            {"baseURL": "http://gpu-1:8000/v1", "apiKey": "{env:GLIMMER_API_KEY}"}, block["options"]
        )
        self.assertEqual(
            {
                "meta-models/Muse-Glimmer-30B": {
                    "name": "meta-models/Muse-Glimmer-30B",
                    "limit": {"context": 32768, "output": 4096},
                }
            },
            block["models"],
        )
        self.assertNotIn("apiKey", after["provider"]["local"]["options"])
        self.assertNotIn("glm", after["provider"])
        self.assertIn("+", profiles_sync.render_diff(plan))

    def test_plan_preserves_unrelated_content_and_is_idempotent(self) -> None:
        self.opencode.parent.mkdir(parents=True)
        self.opencode.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "provider": {"mine": {"npm": "x", "name": "Mine", "options": {}, "models": {}}},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.opencode.chmod(0o644)
        (plan,) = self.plans()
        self.assertTrue(plan.changed)
        after = json.loads(plan.after)
        self.assertEqual("dark", after["theme"])
        self.assertEqual("Mine", after["provider"]["mine"]["name"])
        backup = profiles_sync.apply_plan(plan)
        self.assertIsNotNone(backup)
        self.assertTrue(str(backup).startswith(str(self.opencode) + ".bak."))
        self.assertEqual(0o644, self.opencode.stat().st_mode & 0o777)
        (again,) = self.plans()
        self.assertFalse(again.changed)
        self.assertIsNone(profiles_sync.apply_plan(again))

    def test_new_file_is_private_and_jsonc_or_foreign_blocks_refuse(self) -> None:
        (plan,) = self.plans()
        profiles_sync.apply_plan(plan)
        self.assertEqual(0o600, self.opencode.stat().st_mode & 0o777)
        self.opencode.write_text("// comment\n{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ProfileSyncError, "strict JSON"):
            self.plans()
        self.opencode.write_text(
            json.dumps(
                {
                    "provider": {
                        "glimmer": {"npm": "x", "name": "Someone else", "options": {}, "models": {}}
                    }
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ProfileSyncError, "not managed"):
            self.plans()

    def test_command_backed_plans_run_after_confirmation_with_placeholders(self) -> None:
        from unittest import mock

        from pitwall.agents.harnesses.opencode import OpenCodeAdapter

        commands = [
            [
                "/bin/sh",
                "-c",
                'printf %s "$1" > "$2"',
                "sh",
                "{env:MY_KEY}",
                str(self.home / "seen.txt"),
            ]
        ]
        with mock.patch.object(OpenCodeAdapter, "endpoint_sync_commands", return_value=commands):
            (plan,) = self.plans()
        self.assertTrue(plan.changed)
        self.assertIn("{env:MY_KEY}", profiles_sync.render_diff(plan))
        with mock.patch.dict(os.environ, {"MY_KEY": "sekrit"}):
            profiles_sync.apply_plan(plan)
        self.assertEqual("sekrit", (self.home / "seen.txt").read_text(encoding="utf-8"))
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ProfileSyncError, "MY_KEY"),
        ):
            profiles_sync.apply_plan(plan)

    def _command_plan(self, argv: list[str]):
        from unittest import mock

        from pitwall.agents.harnesses.opencode import OpenCodeAdapter

        with mock.patch.object(OpenCodeAdapter, "endpoint_sync_commands", return_value=[argv]):
            (plan,) = self.plans()
        return plan

    def test_sync_command_gets_explicit_environment_and_stdio(self) -> None:
        from unittest import mock

        plan = self._command_plan(["/bin/true", "{env:MY_KEY}"])
        completed = subprocess.CompletedProcess(["/bin/true"], 0, b"", b"")
        with (
            mock.patch.dict(os.environ, {"MY_KEY": "sekrit", "EXTRA_MARKER": "1"}),
            mock.patch.object(profiles_sync.subprocess, "run", return_value=completed) as run,
        ):
            profiles_sync.apply_plan(plan)
        kwargs = run.call_args.kwargs
        self.assertIsInstance(kwargs["env"], dict)
        self.assertEqual("1", kwargs["env"]["EXTRA_MARKER"])
        self.assertIsNot(kwargs["env"], os.environ)
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stdout"])
        self.assertEqual(subprocess.PIPE, kwargs["stderr"])
        self.assertEqual(300, kwargs["timeout"])

    def test_sync_command_never_reads_the_terminal_and_failure_output_is_redacted(self) -> None:
        from unittest import mock

        plan = self._command_plan(
            ["/bin/sh", "-c", 'cat >/dev/null; echo "bad key $1" >&2; exit 3', "sh", "{env:MY_KEY}"]
        )
        with (
            mock.patch.dict(os.environ, {"MY_KEY": "sekrit"}),
            self.assertRaises(ProfileSyncError) as raised,
        ):
            profiles_sync.apply_plan(plan)
        message = str(raised.exception)
        self.assertIn("exited 3", message)
        self.assertIn("bad key <redacted>", message)
        self.assertNotIn("sekrit", message)

    def test_sync_command_timeout_is_a_route_sync_error(self) -> None:
        from unittest import mock

        plan = self._command_plan(["/bin/true"])
        with (
            mock.patch.object(
                profiles_sync.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["/bin/true"], 300),
            ),
            self.assertRaisesRegex(ProfileSyncError, "timed out"),
        ):
            profiles_sync.apply_plan(plan)

    def test_command_placeholders_never_fall_back_to_the_broker_token(self) -> None:
        from unittest import mock

        seen = self.home / "seen-token.txt"

        def plan_for(variable: str) -> profiles_sync.SyncPlan:
            return profiles_sync.SyncPlan(
                "test",
                self.home / "unchanged.json",
                "",
                "",
                (),
                (
                    (
                        "/bin/sh",
                        "-c",
                        'printf %s "$1" > "$2"',
                        "sh",
                        f"{{env:{variable}}}",
                        str(seen),
                    ),
                ),
            )

        with mock.patch.dict(
            os.environ,
            {
                PITWALL_TOKEN_ENV: "new-token",
                BROKER_TOKEN_ENV: "broker-token",
            },
            clear=True,
        ):
            profiles_sync.apply_plan(plan_for(PITWALL_TOKEN_ENV))
            self.assertEqual("new-token", seen.read_text(encoding="utf-8"))
            profiles_sync.apply_plan(plan_for(BROKER_TOKEN_ENV))
            self.assertEqual("broker-token", seen.read_text(encoding="utf-8"))

        with (
            mock.patch.dict(
                os.environ,
                {BROKER_TOKEN_ENV: "broker-token"},
                clear=True,
            ),
            self.assertRaisesRegex(ProfileSyncError, PITWALL_TOKEN_ENV),
        ):
            profiles_sync.apply_plan(plan_for(PITWALL_TOKEN_ENV))

        with (
            mock.patch.dict(
                os.environ,
                {PITWALL_TOKEN_ENV: "new-token"},
                clear=True,
            ),
            self.assertRaisesRegex(ProfileSyncError, "CUSTOM_PITWALL_TOKEN"),
        ):
            profiles_sync.apply_plan(plan_for("CUSTOM_PITWALL_TOKEN"))

    def test_dsh_settings_sync_attaches_commands_to_its_plan(self) -> None:
        # dsh now materializes its shared settings document only.
        from unittest import mock

        from pitwall.agents.harnesses.dsh import DshAdapter

        with mock.patch.object(DshAdapter, "endpoint_sync_commands", return_value=[["true"]]):
            plans = self.plans("dsh")
        self.assertEqual(1, len(plans))
        self.assertEqual(1, sum(1 for plan in plans if plan.commands))

    def cli(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "profiles",
                "sync",
                *args,
            ],
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=HANG_GUARD_SECS,
        )

    def test_cli_dry_run_never_writes_and_yes_applies_then_resolves(self) -> None:
        profiles.save_profiles(self.env, config_with_endpoint(), registry=self.registry)
        dry = self.cli("--dry-run")
        self.assertEqual(0, dry.returncode, dry.stderr)
        self.assertIn("+", dry.stdout)
        self.assertFalse(self.opencode.exists())
        refused = self.cli()
        self.assertEqual(2, refused.returncode)
        self.assertIn("--yes", refused.stderr)
        applied = self.cli("--yes", "--harness", "opencode")
        self.assertEqual(0, applied.returncode, applied.stderr)
        self.assertTrue(self.opencode.exists())
        resolved = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "profiles",
                "resolve",
                "glimmer@opencode",
                "--json",
            ],
            env={**self.env, "GLIMMER_API_KEY": "k"},
            capture_output=True,
            text=True,
            check=False,
            timeout=HANG_GUARD_SECS,
        )
        self.assertEqual(0, resolved.returncode, resolved.stderr)
        self.assertEqual("synced", json.loads(resolved.stdout)["syncStatus"])
        self.assertEqual(0, self.cli("--yes").returncode)  # idempotent


if __name__ == "__main__":
    unittest.main()
