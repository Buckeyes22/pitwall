"""Profiles refuse credentials embedded in endpoint URLs (userinfo or credential-named queries)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from pitwall.agents import profiles
from pitwall.agents.registry import load_registry
from tests.agents.profiles_fixture import write_profiles
from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

LEAKY_URLS = {
    "userinfo": "https://user:sk-leaked-value@host.example/v1",
    "username only": "https://sk-leaked-value@host.example/v1",
    "key": "https://host.example/v1?key=sk-leaked-value",
    "api_key": "https://host.example/v1?api_key=sk-leaked-value",
    "token": "https://host.example/v1?a=1&token=sk-leaked-value",
    "secret": "https://host.example/v1?secret=sk-leaked-value",
    "password mixed case": "https://host.example/v1?PassWord=sk-leaked-value",
    "pwd": "https://host.example/v1?pwd=sk-leaked-value",
    "passwd": "https://host.example/v1?Passwd=sk-leaked-value",
    "API_KEY upper": "https://host.example/v1?API_KEY=sk-leaked-value",
}
LEAKED = "sk-leaked-value"


def _document(endpoint_base_url: str, *, shared: bool) -> dict[str, object]:
    if shared:
        return {
            "schemaVersion": 1,
            "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
            "endpoints": {"local": {"baseUrl": endpoint_base_url}},
            "models": {"glimmer": {"model": "a/b", "endpoint": "local"}},
        }
    return {
        "schemaVersion": 1,
        "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
        "models": {"glimmer": {"model": "a/b", "endpoint": {"baseUrl": endpoint_base_url}}},
    }


class EndpointUrlCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def test_validator_rejects_credentials_in_inline_and_shared_endpoints(self) -> None:
        for label, url in LEAKY_URLS.items():
            for shared in (False, True):
                with self.subTest(case=label, shared=shared):
                    with self.assertRaises(profiles.ProfilesError) as caught:
                        profiles.validate_profiles(
                            _document(url, shared=shared), registry=self.registry
                        )
                    message = str(caught.exception)
                    self.assertIn("baseUrl", message)
                    self.assertNotIn(LEAKED, message)
                    self.assertNotIn("host.example", message)

    def test_plain_urls_with_harmless_queries_still_validate(self) -> None:
        for url in (
            "https://host.example/v1",
            "http://gpu-1:8000/v1",
            "https://host.example/v1?api-version=2024-06-01&max_tokens=5",
        ):
            with self.subTest(url=url):
                normalized = profiles.validate_profiles(
                    _document(url, shared=False), registry=self.registry
                )
                self.assertEqual(url, normalized["models"]["glimmer"]["endpoint"]["baseUrl"])


class EndpointUrlCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.env = {
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "PATH": os.environ.get("PATH", ""),
        }
        self.toml = root / "config" / "pitwall" / "pitwall.toml"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(PITWALL), "agents", "profiles", *args],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=HANG_GUARD_SECS,
        )

    def test_add_refuses_a_credential_url_and_saves_nothing(self) -> None:
        added = self.cli("add", "x", "--model", "a/b", "--base-url", LEAKY_URLS["userinfo"])
        self.assertEqual(2, added.returncode)
        self.assertIn("baseUrl", added.stderr)
        self.assertNotIn(LEAKED, added.stdout + added.stderr)
        self.assertFalse(self.toml.exists())

    def test_show_and_list_never_print_a_credential_url_from_a_hand_edited_file(self) -> None:
        for label in ("userinfo", "api_key"):
            for shared in (False, True):
                with self.subTest(case=label, shared=shared):
                    write_profiles(self.toml, _document(LEAKY_URLS[label], shared=shared))
                    for command in (("show", "glimmer"), ("list",)):
                        result = self.cli(*command)
                        self.assertNotEqual(0, result.returncode)
                        self.assertNotIn(LEAKED, result.stdout + result.stderr)
                        self.assertIn("baseUrl", result.stderr)


if __name__ == "__main__":
    unittest.main()
