"""Tests for the harness inventory table."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from pitwall.agents import profiles
from pitwall.agents.harnesses import inventory
from pitwall.agents.harnesses.base import parse_duration_seconds
from pitwall.agents.registry import (
    load_registry,
)
from pitwall.agents.resources import (
    read_resource_json,
)
from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]


def executable(path: Path, body: str = '#!/bin/sh\necho "${0##*/} 9.9.9"\n') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


class HarnessInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()
        cls.installers = read_resource_json("config/harness-installers.json")

    def test_rows_report_installed_version_effort_and_route_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bin_dir = home / "bin"
            executable(bin_dir / "agy")
            executable(bin_dir / "codex")
            env = {"HOME": str(home), "PATH": str(bin_dir)}
            config = profiles.validate_profiles(
                {
                    "schemaVersion": 1,
                    "models": {
                        "fast": {"model": "gemini-3.7-flash"},
                        "sol": {"model": "gpt-5.6-sol"},
                        "x": {"model": "a/b", "harness": "opencode"},
                    },
                },
                registry=self.registry,
            )
            rows = {
                row.id: row
                for row in inventory.inspect_harnesses(
                    self.registry, self.installers, config, env, home
                )
            }
        self.assertEqual(sorted(self.registry["harnesses"]), sorted(rows))
        self.assertEqual(str((bin_dir / "agy").resolve()), rows["agy"].installed)
        self.assertEqual("agy 9.9.9", rows["agy"].version)
        self.assertEqual(("fast",), rows["agy"].routes)
        self.assertEqual(("sol",), rows["codex"].routes)
        self.assertEqual(("x",), rows["opencode"].routes)
        self.assertIsNone(rows["pi"].installed)
        self.assertIsNone(rows["pi"].version)
        self.assertEqual("--effort low|medium|high", rows["agy"].effort)
        self.assertEqual(
            "-c model_reasoning_effort=none|minimal|low|medium|high|xhigh|max", rows["codex"].effort
        )
        self.assertEqual("-", rows["kimi"].effort)
        self.assertEqual("--variant <harness-defined>", rows["opencode"].effort)
        self.assertEqual(("model-bound", "none"), (rows["agy"].kind, rows["agy"].endpoint_delivery))
        payload = rows["agy"].to_dict()
        self.assertEqual(
            {
                "id",
                "displayName",
                "installed",
                "version",
                "kind",
                "endpointDelivery",
                "effort",
                "defaultModelSource",
                "routes",
            },
            set(payload),
        )

    def test_version_probe_is_bounded_and_never_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            slow = executable(Path(directory) / "slow", "#!/bin/sh\nsleep 30\n")
            broken = executable(Path(directory) / "broken", "#!/bin/sh\nexit 3\n")
            env = {"PATH": directory}
            self.assertIsNone(
                inventory.probe_version(str(slow), ("--version",), env, timeout_seconds=0.2)
            )
            self.assertIsNone(inventory.probe_version(str(broken), ("--version",), env))
            self.assertIsNone(
                inventory.probe_version(str(Path(directory) / "missing"), ("--version",), env)
            )

    def test_table_and_cli_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            executable(home / "bin" / "agy")
            env = {
                **os.environ,
                "HOME": str(home),
                "PATH": str(home / "bin"),
                "XDG_CONFIG_HOME": str(home / "config"),
            }
            text = subprocess.run(
                [
                    str(PITWALL),
                    "agents",
                    "harnesses",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=HANG_GUARD_SECS,
            )
            self.assertEqual(0, text.returncode, text.stderr)
            header, *lines = text.stdout.splitlines()
            self.assertTrue(header.startswith("harness"), header)
            self.assertEqual(len(self.registry["harnesses"]), len(lines))
            as_json = subprocess.run(
                [
                    str(PITWALL),
                    "agents",
                    "harnesses",
                    "--json",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=HANG_GUARD_SECS,
            )
            rows = json.loads(as_json.stdout)
            agy = next(row for row in rows if row["id"] == "agy")
            self.assertEqual(str((home / "bin" / "agy").resolve()), agy["installed"])
            self.assertEqual([], agy["routes"])


class ParseDurationSecondsTests(unittest.TestCase):
    def test_each_suffix_scales_to_seconds(self) -> None:
        for raw, expected in (
            ("30s", 30.0),
            ("1.5s", 1.5),
            ("2m", 120.0),
            ("1.5m", 90.0),
            ("0.5h", 1800.0),
            ("1d", 86400.0),
            ("1140", 1140.0),
            ("1140S", 1140.0),
            ("2M", 120.0),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(expected, parse_duration_seconds(raw))

    def test_rejects_non_positive_non_finite_and_malformed_values(self) -> None:
        for raw in ("0", "0s", "-5", "-1m", "nan", "inf", "1x2", "", "s"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_duration_seconds(raw)


if __name__ == "__main__":
    unittest.main()
