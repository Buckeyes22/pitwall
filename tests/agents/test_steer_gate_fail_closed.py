"""The steer-gate hook fails closed: a gate that cannot be evaluated blocks the tool call."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from unittest import mock

from pitwall.agents.installation import install, uninstall

ROOT = Path(__file__).resolve().parents[2]
HOOKS = (ROOT / "plugins/claude/hooks/steer-gate.py", ROOT / "plugins/codex/hooks/steer-gate.py")
DISPATCH = {"PITWALL_AGENTS_CHANNEL_DISPATCH_ID": "00000000-0000-4000-8000-0000000000a1"}


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("steer_gate_hook", HOOKS[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HookSandbox:
    def __init__(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="pitwall-steer-gate-test-")
        self.root = Path(self._temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def cleanup(self) -> None:
        self._temp.cleanup()

    def command(self, body: str) -> None:
        target = self.bin / "pitwall"
        target.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        target.chmod(0o755)

    def run(self, *, dispatched: bool = True, path: str | None = None) -> tuple[int, str]:
        env = {
            "PATH": path if path is not None else f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.root),
        }
        if dispatched:
            env.update(DISPATCH)
        result = subprocess.run(
            [sys.executable, str(HOOKS[0])],
            input=b'{"tool_name": "Bash"}',
            capture_output=True,
            env=env,
            check=False,
        )
        return result.returncode, result.stdout.decode()


def denial(stdout: str) -> str:
    output = json.loads(stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    reason: str = output["permissionDecisionReason"]
    return reason


class SteerGateFailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = HookSandbox()
        self.addCleanup(self.sandbox.cleanup)

    def assert_recovery(self, reason: str) -> None:
        self.assertIn(
            "! uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v",
            reason,
        )
        self.assertIn("/plugin", reason)
        self.assertIn("pitwall doctor", reason)
        self.assertIn("Do not try to fix this with a tool call", reason)

    def test_missing_cli_blocks_a_dispatched_harness_with_recovery_message(self) -> None:
        code, stdout = self.sandbox.run(dispatched=True, path="/nonexistent")
        self.assertEqual(0, code)
        reason = denial(stdout)
        self.assertIn("`pitwall` command was not found", reason)
        self.assert_recovery(reason)

    def test_missing_cli_never_blocks_a_harness_that_is_not_dispatched(self) -> None:
        self.assertEqual((0, ""), self.sandbox.run(dispatched=False, path="/nonexistent"))

    def test_timeout_blocks(self) -> None:
        self.assertEqual(10, load_hook().TIMEOUT_SECONDS)
        self.sandbox.command("sleep 5")
        hook = load_hook()
        hook.TIMEOUT_SECONDS = 0.3
        stdout = io.StringIO()
        with (
            mock.patch.dict(
                "os.environ", {"PATH": f"{self.sandbox.bin}:/usr/bin:/bin", **DISPATCH}, clear=True
            ),
            mock.patch.object(sys, "stdin", mock.Mock(buffer=io.BytesIO(b"{}"))),
            mock.patch.object(sys, "stdout", stdout),
        ):
            self.assertEqual(0, hook.main())
        reason = denial(stdout.getvalue())
        self.assertIn("timed out", reason)
        self.assert_recovery(reason)

    def test_gate_error_blocks(self) -> None:
        cases = {
            "nonzero exit": ('echo "gate exploded" >&2; exit 2', "exited 2: gate exploded"),
            "non-json output": ("echo not-json", "not JSON"),
        }
        for label, (body, cause) in cases.items():
            with self.subTest(case=label):
                self.sandbox.command(body)
                code, stdout = self.sandbox.run()
                self.assertEqual(0, code)
                reason = denial(stdout)
                self.assertIn(cause, reason)
                self.assert_recovery(reason)

    def test_a_healthy_gate_passes_its_decision_through(self) -> None:
        deny = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "ack the steer first",
                }
            }
        )
        self.sandbox.command(f"cat > /dev/null; echo '{deny}'")
        code, stdout = self.sandbox.run()
        self.assertEqual((0, "ack the steer first"), (code, denial(stdout)))
        self.sandbox.command("cat > /dev/null")
        self.assertEqual((0, ""), self.sandbox.run())

    def test_without_a_dispatch_a_present_cli_is_never_run(self) -> None:
        marker = self.sandbox.root / "ran"
        self.sandbox.command(f'touch "{marker}"')
        self.assertEqual((0, ""), self.sandbox.run(dispatched=False))
        self.assertFalse(marker.exists())

    def test_both_hosts_ship_the_same_hook(self) -> None:
        self.assertEqual(HOOKS[0].read_bytes(), HOOKS[1].read_bytes())

    def test_the_gate_command_exits_2_when_it_cannot_evaluate(self) -> None:
        from pitwall.agents.steer_gate import run_stdin

        stdout, stderr = io.StringIO(), io.StringIO()
        self.assertEqual(2, run_stdin(b"{not json", {}, stdout, stderr))
        self.assertEqual("", stdout.getvalue())
        self.assertIn("could not be evaluated", stderr.getvalue())
        self.assertEqual(0, run_stdin(b'{"tool_name": "Bash"}', {}, stdout, stderr))
        self.assertEqual("", stdout.getvalue())


class UninstallOrderTests(unittest.TestCase):
    def test_uninstall_removes_hooks_first(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pitwall-uninstall-order-") as directory:
            root = Path(directory)
            home = root / "home"
            bin_dir = root / "bin"
            home.mkdir()
            bin_dir.mkdir()
            (bin_dir / "pitwall").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (bin_dir / "pitwall").chmod(0o755)
            env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"}
            events: list[tuple[str, str]] = []

            def runner(argv: Sequence[str]) -> int:
                events.append(("run", " ".join(argv)))
                return 0

            install(env, home, harnesses=("codex",), plugin_hosts=("claude",), runner=runner)
            events.clear()
            from pitwall.agents import installation

            original = installation._remove_file

            def recording(path: Path, result: installation.InstallResult) -> None:
                events.append(("remove", str(path)))
                original(path, result)

            with mock.patch.object(installation, "_remove_file", recording):
                uninstall(env, home, runner=runner)

        hooks = [
            index
            for index, (kind, value) in enumerate(events)
            if kind == "remove" and value.endswith("/hooks/hooks.json")
        ]
        others = [
            index
            for index, (kind, value) in enumerate(events)
            if not (kind == "remove" and value.endswith("/hooks/hooks.json"))
        ]
        self.assertEqual(2, len(hooks))  # the claude and codex plugins each register hooks
        self.assertLess(max(hooks), min(others), events)


if __name__ == "__main__":
    unittest.main()
