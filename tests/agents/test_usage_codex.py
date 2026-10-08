"""The Codex reader against recorded response shapes."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pitwall.agents.usage import (
    codex,
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
    codex_login,
)

ROOT = Path(__file__).resolve().parents[2]


class CodexReaderTests(ReaderCase):
    def account(self, *, exp_in: float = 3600.0, account_id: str | None = "acct-1") -> Account:
        return Account(
            "codex",
            "",
            (),
            codex_login(self.home / ".codex", exp=EPOCH + exp_in, account_id=account_id),
        )

    def payload(
        self,
        primary: dict[str, object] | None,
        secondary: dict[str, object] | None,
        **extra: object,
    ) -> dict[str, object]:
        return {
            "plan_type": "pro",
            "rate_limit": {
                "allowed": True,
                "limit_reached": False,
                "primary_window": primary,
                "secondary_window": secondary,
                **extra,
            },
        }

    def test_windows_are_chosen_by_duration_not_position(self) -> None:
        week = {"used_percent": 40, "limit_window_seconds": 604800, "reset_at": EPOCH + 86400}
        five = {"used_percent": 8.4, "limit_window_seconds": 18000, "reset_at": EPOCH + 3600}
        for primary, secondary in ((five, week), (week, five)):
            with self.subTest(first=primary["limit_window_seconds"]):
                opener = FakeOpener({"wham/usage": self.payload(primary, secondary)})
                reading = codex.read(self.account(), self.env, self.home, NOW, opener)
                self.assertEqual(
                    (
                        Window("5h", 8, "2026-09-28T13:00:00Z"),
                        Window("7d", 40, "2026-09-29T12:00:00Z"),
                    ),
                    reading.windows,
                )
                self.assertEqual("Pro", reading.tier)

    def test_position_is_the_fallback_when_durations_are_missing(self) -> None:
        opener = FakeOpener({"wham/usage": self.payload({"used_percent": 5}, {"used_percent": 50})})
        reading = codex.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual((Window("5h", 5, None), Window("7d", 50, None)), reading.windows)

    def test_one_window_only(self) -> None:
        opener = FakeOpener(
            {"wham/usage": self.payload({"used_percent": 5, "limit_window_seconds": 604800}, None)}
        )
        reading = codex.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual((Window("5h", None, None), Window("7d", 5, None)), reading.windows)

    def test_a_reported_limit_is_carried(self) -> None:
        opener = FakeOpener(
            {
                "wham/usage": self.payload(
                    {"used_percent": 99, "limit_window_seconds": 18000}, None, limit_reached=True
                )
            }
        )
        self.assertTrue(codex.read(self.account(), self.env, self.home, NOW, opener).limit_reached)

    def test_the_account_id_header(self) -> None:
        opener = FakeOpener({"wham/usage": self.payload(None, None)})
        codex.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual("acct-1", opener.calls[0].get_header("Chatgpt-account-id"))
        opener = FakeOpener({"wham/usage": self.payload(None, None)})
        codex.read(self.account(account_id=None), self.env, self.home, NOW, opener)
        self.assertEqual("acct-from-id-token", opener.calls[0].get_header("Chatgpt-account-id"))

    def test_an_expired_or_unreadable_token_makes_no_request(self) -> None:
        opener = FakeOpener({"wham/usage": self.payload(None, None)})
        with self.assertRaises(ReadError) as caught:
            codex.read(self.account(exp_in=60.0), self.env, self.home, NOW, opener)
        self.assertIn("login expired", str(caught.exception))
        (self.home / ".codex" / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "not-a-jwt"}}), encoding="utf-8"
        )
        with self.assertRaises(ReadError):
            codex.read(
                Account("codex", "", (), self.home / ".codex"), self.env, self.home, NOW, opener
            )
        self.assertEqual([], opener.calls)

    def test_a_response_without_rate_limits_is_unexpected(self) -> None:
        with self.assertRaises(ReadError) as caught:
            codex.read(
                self.account(),
                self.env,
                self.home,
                NOW,
                FakeOpener({"wham/usage": {"plan_type": "pro"}}),
            )
        self.assertEqual("unexpected response", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
