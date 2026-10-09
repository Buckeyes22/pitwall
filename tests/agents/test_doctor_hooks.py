"""`pitwall doctor` fails when a registered plugin hook calls a CLI that cannot be run."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pitwall.agents import doctor
from pitwall.agents.installation import install
from pitwall.install_hint import install_command

ROOT = Path(__file__).resolve().parents[2]


class DoctorHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="pitwall-doctor-hooks-")
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.bin.mkdir()

    def env(self, *, path: str) -> dict[str, str]:
        return {"HOME": str(self.home), "PATH": path, "XDG_STATE_HOME": str(self.root / "state")}

    def command(self, body: str) -> None:
        target = self.bin / "pitwall"
        target.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        target.chmod(0o755)

    def install_hooks(self) -> None:
        self.command("exit 0")
        install(
            self.env(path=f"{self.bin}:/usr/bin:/bin"),
            self.home,
            harnesses=(),
            plugin_hosts=(),
            runner=lambda argv: 0,
        )

    def test_doctor_fails_on_hook_with_unrunnable_cli(self) -> None:
        self.install_hooks()
        cases = {
            "missing": ("/nonexistent", None),
            "exits nonzero": (str(self.bin), "exit 1"),
        }
        for label, (path, body) in cases.items():
            with self.subTest(case=label):
                if body is not None:
                    self.command(body)
                else:
                    (self.bin / "pitwall").unlink(missing_ok=True)
                env = self.env(path=path)
                check = doctor._check_registered_hooks(ROOT, env)
                self.assertEqual("FAIL", check.status)
                self.assertIn(
                    "! " + install_command(),
                    check.remediation or "",
                )
                report = doctor.run_doctor(None, env, installation_only=True)
                by_id = {item["id"]: item for item in report["checks"]}
                self.assertEqual("FAIL", by_id["runtime.registered_hooks"]["status"])
                self.assertEqual("fail", report["status"])

    def test_doctor_passes_with_a_runnable_cli_or_no_hooks(self) -> None:
        env = self.env(path="/nonexistent")
        self.assertEqual("PASS", doctor._check_registered_hooks(ROOT, env).status)  # no hooks
        self.install_hooks()
        runnable = self.env(path=f"{self.bin}:/usr/bin:/bin")
        self.assertEqual("PASS", doctor._check_registered_hooks(ROOT, runnable).status)


if __name__ == "__main__":
    unittest.main()
