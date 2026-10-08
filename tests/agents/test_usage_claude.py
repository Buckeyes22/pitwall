"""The Claude reader against recorded response shapes."""

from __future__ import annotations

import unittest
from pathlib import Path

from pitwall.agents.usage import (
    claude,
)
from pitwall.agents.usage.accounts import (
    Account,
)
from pitwall.agents.usage.rows import (
    ReadError,
    Window,
)
from tests.agents.usage_test_support import (
    EPOCH,
    NOW,
    FakeOpener,
    ReaderCase,
    claude_login,
)

ROOT = Path(__file__).resolve().parents[2]


class ClaudeReaderTests(ReaderCase):
    def account(self, *, expires_in: float = 3600.0, **kwargs: str) -> Account:
        return Account(
            "claude",
            "",
            (),
            claude_login(
                self.home / ".claude", expires_at_ms=(EPOCH + expires_in) * 1000, **kwargs
            ),
        )

    def test_windows_tier_and_headers(self) -> None:
        opener = FakeOpener(
            {
                "api.anthropic.com/api/oauth/usage": {
                    "five_hour": {"utilization": 41.5, "resets_at": "2026-09-28T14:20:00+00:00"},
                    "seven_day": {"utilization": 63, "resets_at": "2026-10-01T09:00:00Z"},
                    "seven_day_opus": {"utilization": 12.4},
                    "seven_day_sonnet": None,
                }
            }
        )
        reading = claude.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual(
            (Window("5h", 42, "2026-09-28T14:20:00Z"), Window("7d", 63, "2026-10-01T09:00:00Z")),
            reading.windows,
        )
        self.assertEqual("Max 20x", reading.tier)
        self.assertEqual("opus 7d 12%", reading.detail)
        sent = opener.calls[0]
        self.assertEqual("GET", sent.get_method())
        self.assertEqual("Bearer claude-access", sent.get_header("Authorization"))
        self.assertEqual("oauth-2025-04-20", sent.get_header("Anthropic-beta"))

    def test_tiers(self) -> None:
        for tier, kind, expected in (
            ("default_claude_max_5x", "max", "Max 5x"),
            ("", "max", "Max"),
            ("", "pro", "Pro"),
            ("", "team", "team"),
        ):
            with self.subTest(expected=expected):
                opener = FakeOpener({"oauth/usage": {}})
                self.assertEqual(
                    expected,
                    claude.read(
                        self.account(tier=tier, kind=kind), self.env, self.home, NOW, opener
                    ).tier,
                )

    def test_an_expired_login_makes_no_request(self) -> None:
        opener = FakeOpener({"oauth/usage": {}})
        with self.assertRaises(ReadError) as caught:
            claude.read(self.account(expires_in=-1.0), self.env, self.home, NOW, opener)
        self.assertIn("login expired", str(caught.exception))
        self.assertEqual([], opener.calls)

    def test_a_credentials_file_of_the_wrong_shape_is_a_read_error(self) -> None:
        directory = self.home / ".claude"
        directory.mkdir()
        for content in (
            "",
            "not json",
            "[]",
            "null",
            '{"claudeAiOauth": []}',
            '{"claudeAiOauth": {"accessToken": ""}}',
        ):
            with self.subTest(content=content):
                (directory / ".credentials.json").write_text(content, encoding="utf-8")
                with self.assertRaises(ReadError) as caught:
                    claude.read(
                        Account("claude", "", (), directory), self.env, self.home, NOW, FakeOpener()
                    )
                self.assertIn(".credentials.json", str(caught.exception))
                self.assertNotIn("claude-access", str(caught.exception))

    def test_refusals_carry_only_the_status_code(self) -> None:
        with self.assertRaises(ReadError) as caught:
            claude.read(self.account(), self.env, self.home, NOW, FakeOpener({"oauth/usage": 429}))
        self.assertEqual("HTTP 429", str(caught.exception))

    def test_a_response_that_is_not_an_object_is_unexpected(self) -> None:
        with self.assertRaises(ReadError) as caught:
            claude.read(
                self.account(), self.env, self.home, NOW, FakeOpener({"oauth/usage": [1, 2]})
            )
        self.assertEqual("unexpected response", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
