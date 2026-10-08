"""The reviewed installer manifest matches what the vendors serve today (hashes: see report)."""

from __future__ import annotations

import re
import unittest

from pitwall.agents import setup


class InstallerManifestTests(unittest.TestCase):
    def specs(self) -> dict[str, setup.HarnessInstallSpec]:
        return {spec.harness_id: spec for spec in setup.load_install_specs()}

    def test_codex_installer_may_redirect_to_releases_openai_com(self) -> None:
        self.assertIn("releases.openai.com", self.specs()["codex"].allowed_redirect_hosts)

    def test_repinned_installer_hashes(self) -> None:
        pinned = {
            "codex": "150e3cf675682efeaac115aa3747add3f27887896d04ce6d0b56478d8b428bf6",
            "claude": "3a68d3406cf674e17bed1733a4dcf37805e2e47d87417700007d7e1aa766a944",
            "grok": "7fd6fdc75d9418b2e58356726fcbf1ae849416f773925da07d0ccc7a60d3e791",
            "hermes": "14b4c89518cf0e4e71841708dd5d8a28f1f494d22cd20e12f4d5eeb29c7d2fdb",
            "agy": "62966c07365423bd4dc209355060744058fb30d60f5323e2d360e39de64e5042",
        }
        specs = self.specs()
        for harness, digest in pinned.items():
            self.assertEqual(digest, specs[harness].recipe.sha256, harness)

    def test_every_script_installer_pins_a_sha256(self) -> None:
        for harness, spec in self.specs().items():
            if spec.recipe.kind == "script":
                self.assertRegex(spec.recipe.sha256 or "", re.compile(r"^[0-9a-f]{64}$"), harness)
