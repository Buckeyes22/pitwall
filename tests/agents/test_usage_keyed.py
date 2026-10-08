"""The GLM and MiniMax readers against recorded response shapes."""

from __future__ import annotations

import unittest
from pathlib import Path

from pitwall.agents.usage import (
    glm,
    minimax,
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
)

ROOT = Path(__file__).resolve().parents[2]


class GlmReaderTests(ReaderCase):
    def setUp(self) -> None:
        super().setUp()
        self.env["GLM_API_KEY"] = "glm-key"  # pragma: allowlist secret
        self.account = Account("glm", "")

    def limits(self, *entries: dict[str, object]) -> dict[str, object]:
        return {"code": 200, "data": {"level": "pro", "limits": list(entries)}}

    def test_codes_identify_the_windows_even_when_reset_order_disagrees(self) -> None:
        five = {
            "type": "TOKENS_LIMIT",
            "unit": 3,
            "number": 5,
            "percentage": 7,
            "nextResetTime": (EPOCH + 4 * 3600) * 1000,
        }
        week = {
            "type": "TOKENS_LIMIT",
            "unit": 6,
            "number": 1,
            "percentage": 31,
            "nextResetTime": (EPOCH + 3600) * 1000,
        }
        tools = {"type": "TIME_LIMIT", "percentage": 16}
        reading = glm.read(
            self.account,
            self.env,
            self.home,
            NOW,
            FakeOpener({"quota/limit": self.limits(week, tools, five)}),
        )
        self.assertEqual(
            (Window("5h", 7, "2026-09-28T16:00:00Z"), Window("7d", 31, "2026-09-28T13:00:00Z")),
            reading.windows,
        )
        self.assertEqual(("Pro", "tools 16%"), (reading.tier, reading.detail))

    def test_reset_order_is_the_fallback_without_codes(self) -> None:
        later = {"type": "TOKENS_LIMIT", "percentage": 31, "nextResetTime": (EPOCH + 86400) * 1000}
        sooner = {"type": "TOKENS_LIMIT", "percentage": 7, "nextResetTime": (EPOCH + 3600) * 1000}
        reading = glm.read(
            self.account,
            self.env,
            self.home,
            NOW,
            FakeOpener({"quota/limit": self.limits(later, sooner)}),
        )
        self.assertEqual([7, 31], [window.used_pct for window in reading.windows])

    def test_the_key_is_sent_and_never_reported(self) -> None:
        opener = FakeOpener({"quota/limit": 401})
        with self.assertRaises(ReadError) as caught:
            glm.read(self.account, self.env, self.home, NOW, opener)
        self.assertEqual("HTTP 401", str(caught.exception))
        self.assertEqual("Bearer glm-key", opener.calls[0].get_header("Authorization"))

    def test_an_unexpected_shape(self) -> None:
        for payload in ({"data": {"limits": "none"}}, {"data": None}, []):
            with self.subTest(payload=payload):
                with self.assertRaises(ReadError) as caught:
                    glm.read(
                        self.account, self.env, self.home, NOW, FakeOpener({"quota/limit": payload})
                    )
                self.assertEqual("unexpected response", str(caught.exception))


class MiniMaxReaderTests(ReaderCase):
    def setUp(self) -> None:
        super().setUp()
        self.env["MINIMAX_API_KEY"] = "mm-key"  # pragma: allowlist secret
        self.account = Account("minimax", "")

    def test_counts_become_percentages_for_the_coding_model(self) -> None:
        payload = {
            "model_remains": [
                {
                    "model_name": "Hailuo-02",
                    "current_interval_total_count": 10,
                    "current_interval_usage_count": 9,
                },
                {
                    "model_name": "MiniMax-M3",
                    "remains_time": 3600_000,
                    "current_interval_total_count": 1200,
                    "current_interval_usage_count": 300,
                    "current_weekly_total_count": 8000,
                    "current_weekly_usage_count": 4000,
                    "weekly_remains_time": 86_400_000,
                },
            ]
        }
        opener = FakeOpener({"api.minimax.io/v1/token_plan/remains": payload})
        reading = minimax.read(self.account, self.env, self.home, NOW, opener)
        self.assertEqual(
            (Window("5h", 25, "2026-09-28T13:00:00Z"), Window("7d", 50, "2026-09-29T12:00:00Z")),
            reading.windows,
        )
        self.assertEqual("5h requests 300/1200, 7d requests 4000/8000", reading.detail)

    def test_a_total_of_zero_gives_no_percentage(self) -> None:
        payload = {
            "model_remains": [
                {
                    "model_name": "MiniMax-M3",
                    "current_interval_total_count": 0,
                    "current_interval_usage_count": 0,
                }
            ]
        }
        reading = minimax.read(
            self.account, self.env, self.home, NOW, FakeOpener({"token_plan/remains": payload})
        )
        self.assertEqual([None, None], [window.used_pct for window in reading.windows])

    def test_a_vendor_status_is_reported_by_code_only(self) -> None:
        payload = {"base_resp": {"status_code": 2049, "status_msg": "invalid api key mm-key"}}
        with self.assertRaises(ReadError) as caught:
            minimax.read(
                self.account, self.env, self.home, NOW, FakeOpener({"token_plan/remains": payload})
            )
        self.assertEqual("vendor status 2049", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
