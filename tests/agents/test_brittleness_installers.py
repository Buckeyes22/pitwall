"""The reviewed installer manifest allows the vendors' redirects and pins every script."""

from __future__ import annotations

import re
import unittest

from pitwall.agents import setup


class InstallerManifestTests(unittest.TestCase):
    def specs(self) -> dict[str, setup.HarnessInstallSpec]:
        return {spec.harness_id: spec for spec in setup.load_install_specs()}

    def test_codex_installer_may_redirect_to_releases_openai_com(self) -> None:
        self.assertIn("releases.openai.com", self.specs()["codex"].allowed_redirect_hosts)

    def test_every_script_installer_pins_a_sha256(self) -> None:
        for harness, spec in self.specs().items():
            if spec.recipe.kind == "script":
                self.assertRegex(spec.recipe.sha256 or "", re.compile(r"^[0-9a-f]{64}$"), harness)
