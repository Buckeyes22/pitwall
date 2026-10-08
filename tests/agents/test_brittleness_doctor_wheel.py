"""On an installed wheel, clone-only doctor checks say "not applicable" and give no clone advice."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest import mock

from pitwall.agents import doctor
from pitwall.agents.registry import load_registry

CLONE_ONLY = (
    "runtime.source_registry_layout",
    "runtime.generated_routes",
    "runtime.install_links",
    "plugin.claude.marketplace_present",
    "plugin.codex.marketplace_present",
    "plugin.copilot.marketplace_present",
    "plugin.claude.native_host_exclusions",
    "plugin.runtime_reference_shared",
    "plugin.version_alignment",
)


class WheelDoctorTests(unittest.TestCase):
    def skips(self) -> dict[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="pitwall-wheel-doctor-") as directory:
            root = Path(directory)
            (root / "home").mkdir()
            env = {
                "HOME": str(root / "home"),
                "PATH": "/usr/bin:/bin",
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            fake = subprocess.CompletedProcess([], 0, b"1.0\n", b"")
            with mock.patch.object(doctor.subprocess, "run", return_value=fake):
                report = doctor.run_doctor(None, env, installation_only=True)
        checks = list(report["checks"])
        checks += [asdict(check) for check in doctor._artifact_plugin_skips(load_registry(None))]
        return {check["id"]: check for check in checks if check["status"] == "SKIP"}

    def test_clone_only_skips_are_not_applicable_with_no_remediation(self) -> None:
        skips = self.skips()
        for check_id in CLONE_ONLY:
            check = skips[check_id]
            self.assertIn("not applicable to an installed wheel", check["summary"], check_id)
            self.assertFalse(check.get("remediation"), check_id)
            self.assertNotIn("clone", json.dumps(check), check_id)
