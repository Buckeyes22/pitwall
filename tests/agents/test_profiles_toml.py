"""Agent profiles live in ``[agents.profiles]`` of pitwall.toml, not in a routes JSON file."""

from __future__ import annotations

import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from pitwall.agents import profiles
from pitwall.agents.registry import load_registry

DOCUMENT = """\
# operator settings that must survive a profile edit
pitwall_mcp_transport = "stdio"

[agents.profiles.defaults]
harness = "opencode"
endpointHarness = "qwen"

[agents.profiles.endpoints.gpu]
baseUrl = "http://gpu-1:8000/v1"
apiKeyEnv = "GPU_API_KEY"  # pragma: allowlist secret

[agents.profiles.models.glimmer]
model = "meta-models/Muse-Glimmer-30B"
endpoint = "gpu"
seat = "local"

[agents.profiles.models.sol]
model = "gpt-5.6-sol"
args = ["-c", "model_reasoning_effort=high"]
seat = "critical"

[agents.profiles.models.sol.limits]
context = 200000
output = 32000
"""


class ProfilesTomlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.config = self.dir / "pitwall.toml"
        self.env = {"HOME": str(self.dir), "PITWALL_CONFIG_FILE": str(self.config)}

    def test_profiles_load_from_pitwall_toml(self) -> None:
        self.config.write_text(DOCUMENT, encoding="utf-8")
        loaded = profiles.load_profiles(self.env, registry=self.registry)
        self.assertEqual(["glimmer", "sol"], list(loaded["models"]))
        self.assertEqual("http://gpu-1:8000/v1", loaded["models"]["glimmer"]["endpoint"]["baseUrl"])
        self.assertEqual({"context": 200000, "output": 32000}, loaded["models"]["sol"]["limits"])
        self.assertEqual(self.config, profiles.profiles_path(self.env))

    def test_missing_file_or_table_yields_empty_defaults(self) -> None:
        self.assertEqual(
            profiles.empty_profiles(), profiles.load_profiles(self.env, registry=self.registry)
        )
        self.config.write_text('pitwall_mcp_transport = "stdio"\n', encoding="utf-8")
        self.assertEqual(
            profiles.empty_profiles(), profiles.load_profiles(self.env, registry=self.registry)
        )

    def test_default_path_is_under_xdg_config_when_no_file_is_configured(self) -> None:
        env = {"HOME": str(self.dir), "XDG_CONFIG_HOME": str(self.dir / "xdg")}
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.dir)  # no pitwall.toml here
        self.assertEqual(self.dir / "xdg" / "pitwall" / "pitwall.toml", profiles.profiles_path(env))

    def test_alternative_file_override(self) -> None:
        other = self.dir / "isolated.toml"
        other.write_text(DOCUMENT, encoding="utf-8")
        env = {**self.env, "PITWALL_AGENTS_PROFILES": str(other)}
        self.assertEqual(other, profiles.profiles_path(env))
        self.assertEqual(
            ["glimmer", "sol"],
            list(profiles.load_profiles(env, registry=self.registry)["models"]),
        )

    def test_invalid_profile_rejected_with_path(self) -> None:
        self.config.write_text(
            DOCUMENT + '\n[agents.profiles.models.bad]\nharness = "opencode"\n', encoding="utf-8"
        )
        with self.assertRaises(profiles.ProfilesError) as caught:
            profiles.load_profiles(self.env, registry=self.registry)
        message = str(caught.exception)
        self.assertIn(str(self.config), message)
        self.assertIn("models.bad.model", message)

    def test_structurally_invalid_table_rejected_with_path(self) -> None:
        self.config.write_text("[agents.profiles]\nmodelz = {}\n", encoding="utf-8")
        with self.assertRaises(profiles.ProfilesError) as caught:
            profiles.load_profiles(self.env, registry=self.registry)
        self.assertIn(str(self.config), str(caught.exception))
        self.assertIn("unknown fields: modelz", str(caught.exception))

    def test_malformed_toml_rejected_with_path(self) -> None:
        self.config.write_text("[agents.profiles\n", encoding="utf-8")
        with self.assertRaises(profiles.ProfilesError) as caught:
            profiles.load_profiles(self.env, registry=self.registry)
        self.assertIn(str(self.config), str(caught.exception))

    def test_save_round_trips_and_keeps_the_rest_of_the_file(self) -> None:
        self.config.write_text(DOCUMENT, encoding="utf-8")
        loaded = profiles.load_profiles(self.env, registry=self.registry)
        edited = profiles.add_profile(
            loaded,
            "review",
            model="gpt-5.6-terra",
            harness="codex",
            origin={
                "kind": "pitwall",
                "capability": "cap",
                "leaseId": None,
                "url": "http://127.0.0.1:9/v1",
            },
            base_url="http://gpu-2:8000/v1",
        )
        profiles.save_profiles(self.env, edited, registry=self.registry)
        text = self.config.read_text(encoding="utf-8")
        self.assertIn("# operator settings that must survive a profile edit", text)
        self.assertEqual("stdio", tomllib.loads(text)["pitwall_mcp_transport"])
        again = profiles.load_profiles(self.env, registry=self.registry)
        self.assertEqual(
            profiles.validate_profiles(edited, registry=self.registry)["models"],
            again["models"],
        )
        self.assertEqual(0o600, self.config.stat().st_mode & 0o777)
        profiles.save_profiles(self.env, again, registry=self.registry)
        self.assertEqual(text, self.config.read_text(encoding="utf-8"))

    def test_save_creates_the_file_and_removing_the_last_profile_empties_the_table(self) -> None:
        data = profiles.add_profile(
            profiles.empty_profiles(), "solo", model="gpt-5.6-sol", harness="codex"
        )
        path = profiles.save_profiles(self.env, data, registry=self.registry)
        self.assertEqual(self.config, path)
        self.assertEqual(
            "gpt-5.6-sol",
            tomllib.loads(path.read_text(encoding="utf-8"))["agents"]["profiles"]["models"]["solo"][
                "model"
            ],
        )
        emptied = profiles.remove_profile(
            profiles.load_profiles(self.env, registry=self.registry), "solo"
        )
        profiles.save_profiles(self.env, emptied, registry=self.registry)
        self.assertEqual(
            profiles.empty_profiles(), profiles.load_profiles(self.env, registry=self.registry)
        )


if __name__ == "__main__":
    unittest.main()
