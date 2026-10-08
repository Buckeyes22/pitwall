"""sanitize_args redacts secret-looking values by pattern as well as by flag name."""

from __future__ import annotations

import unittest
from pathlib import Path

from pitwall.agents.harnesses.base import HarnessAdapter

ROOT = Path(__file__).resolve().parents[2]

SECRET_SHAPES = {
    "openai": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",  # pragma: allowlist secret
    "anthropic": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",  # pragma: allowlist secret
    "github-classic": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",  # pragma: allowlist secret
    "github-fine-grained": "github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz",  # pragma: allowlist secret
    "slack": "xoxb-1234567890-abcdefghijkl",  # pragma: allowlist secret
    "aws": "AKIAIOSFODNN7EXAMPLE",  # pragma: allowlist secret
    "google": "AIzaSyA-abcdefghijklmnopqrstuvwxyz012345",  # pragma: allowlist secret
    "hugging-face": "hf_abcdefghijklmnopqrstuvwxyz0123456789",  # pragma: allowlist secret
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",  # pragma: allowlist secret
    "long-opaque": "Zm9vYmFyYmF6cXV4MDEyMzQ1Njc4OWFiY2RlZg",  # pragma: allowlist secret
}


class SanitizeArgsTests(unittest.TestCase):
    def test_positional_secret_redacted(self) -> None:
        for name, secret in SECRET_SHAPES.items():
            with self.subTest(shape=name):
                self.assertEqual(
                    ["run", "<redacted>", "--verbose"],
                    HarnessAdapter.sanitize_args(["run", secret, "--verbose"]),
                )

    def test_secret_in_flag_value_or_header_is_redacted_by_pattern(self) -> None:
        self.assertEqual(
            ["--endpoint-note=<redacted>"],
            HarnessAdapter.sanitize_args([f"--endpoint-note={SECRET_SHAPES['openai']}"]),
        )
        self.assertEqual(
            ["<redacted>"],
            HarnessAdapter.sanitize_args(["Authorization: Bearer abcdefghijklmnop0123456789"]),
        )

    def test_ordinary_arguments_are_preserved(self) -> None:
        ordinary = [
            "exec",
            "-m",
            "gpt-5.6-terra",
            "--model=qwen3.8-flash",
            "--output-format",
            "text",
            "/home/user/work/prompt-file.md",
            "https://example.com/v1",
            "@/tmp/run/prompt.md",
            "<prompt>",
        ]
        self.assertEqual(ordinary, HarnessAdapter.sanitize_args(list(ordinary)))

    def test_flag_named_secrets_are_still_redacted(self) -> None:
        self.assertEqual(
            ["--api-key", "<redacted>", "--token=<redacted>"],
            HarnessAdapter.sanitize_args(["--api-key", "secret-one", "--token=secret-two"]),
        )


if __name__ == "__main__":
    unittest.main()
