"""Install writes shims and the manifest through a temp file + os.replace, never in place."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents import installation
from pitwall.agents.installation import install


class AtomicInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="pitwall-atomic-install-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        pitwall = self.bin / "pitwall"
        pitwall.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        pitwall.chmod(0o755)
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin"}

    def run_install(self) -> None:
        install(self.env, self.home, harnesses=(), plugin_hosts=(), runner=lambda _a: 0)

    def test_a_failed_publish_leaves_the_previous_shim_and_no_temp_files(self) -> None:
        self.run_install()
        shim = self.home / ".claude" / "scripts" / "codex-shim.sh"
        shim.write_text("#!/bin/sh\n# older shim written by an earlier install\n", encoding="utf-8")
        before = shim.read_bytes()
        manifest = installation.InstallLocations.for_home(self.home, self.env).manifest
        manifest_before = manifest.read_bytes()
        with (
            mock.patch.object(installation.os, "replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.run_install()
        self.assertEqual(before, shim.read_bytes())
        self.assertEqual(manifest_before, manifest.read_bytes())
        for directory in {shim.parent, manifest.parent}:
            self.assertEqual([], [p.name for p in directory.iterdir() if p.name.startswith(".")])

    def test_files_are_chmodded_before_they_are_published(self) -> None:
        modes: list[int] = []
        real_replace = os.replace

        def recording(source: object, target: object) -> None:
            modes.append(stat.S_IMODE(os.stat(source).st_mode))  # type: ignore[arg-type]  # reason: os.replace passes str/Path
            real_replace(source, target)  # type: ignore[arg-type]  # reason: os.replace passes str/Path

        with mock.patch.object(installation.os, "replace", recording):
            self.run_install()
        self.assertIn(0o755, modes)  # the shims
        self.assertNotIn(0o600, modes)  # nothing is published with mkstemp's private mode
