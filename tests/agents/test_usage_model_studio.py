"""The Model Studio reader: statistics, the lockout, and the unmeasured case."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from pitwall.agents import endpoints
from pitwall.agents.usage import (
    model_studio,
)
from pitwall.agents.usage.accounts import (
    Account,
)
from pitwall.agents.usage.rows import (
    ReadError,
    Unmeasured,
    Window,
)
from tests.agents.usage_test_support import (
    EPOCH,
    NOW,
    FakeOpener,
    ReaderCase,
)

ROOT = Path(__file__).resolve().parents[2]


class ModelStudioReaderTests(ReaderCase):
    ENDPOINT = {
        "kind": "model-studio",
        "plan": "token-plan-personal",
        "tier": "pro",
        "renewsOn": "2026-09-21",
    }

    def account(self, endpoint: dict[str, object] | None = None) -> Account:
        return Account(
            "model-studio", "", ("flash",), endpoints=(("ms", endpoint or self.ENDPOINT),)
        )

    def test_without_an_access_key_usage_is_unmeasured_and_names_the_renewal(self) -> None:
        opener = FakeOpener()
        with self.assertRaises(Unmeasured) as caught:
            model_studio.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual("usage needs an AccessKey pair; renews 2026-10-21", str(caught.exception))
        self.assertEqual([], opener.calls)

    def test_without_a_renewal_date(self) -> None:
        endpoint = {"kind": "model-studio", "plan": "token-plan-personal", "tier": "pro"}
        with self.assertRaises(Unmeasured) as caught:
            model_studio.read(self.account(endpoint), self.env, self.home, NOW, FakeOpener())
        self.assertEqual("usage needs an AccessKey pair", str(caught.exception))

    def test_stats_become_a_thirty_day_window(self) -> None:
        self.env.update(
            {
                "ALIBABA_CLOUD_ACCESS_KEY_ID": "TESTAKID",
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "test-value",  # pragma: allowlist secret
            }
        )
        reset_ms = int((EPOCH + 10 * 86400) * 1000)
        payload = {
            "Data": {
                "Items": [
                    {
                        "SeatCredits": 180000,
                        "SeatRemainingCredits": 45000,
                        "SeatRefreshTime": reset_ms,
                    }
                ]
            }
        }
        reading = model_studio.read(
            self.account(),
            self.env,
            self.home,
            NOW,
            FakeOpener({"tokenplan/subscription/stats": payload}),
        )
        self.assertEqual((Window("30d", 75, "2026-10-08T12:00:00Z"),), reading.windows)
        self.assertEqual(
            ("Pro", "45000 of 180000 Credits remaining"), (reading.tier, reading.detail)
        )

    def test_a_local_lockout_is_a_limit_without_a_request(self) -> None:
        endpoints.write_lockout(self.env, "ms", datetime(2026, 10, 21, tzinfo=UTC))
        opener = FakeOpener()
        reading = model_studio.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual((Window("30d", 100, "2026-10-21T00:00:00Z"),), reading.windows)
        self.assertTrue(reading.limit_reached)
        self.assertEqual([], opener.calls)

    def test_a_refused_stats_read(self) -> None:
        self.env.update(
            {
                "ALIBABA_CLOUD_ACCESS_KEY_ID": "TESTAKID",
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "test-value",  # pragma: allowlist secret
            }
        )
        with self.assertRaises(ReadError) as caught:
            model_studio.read(
                self.account(), self.env, self.home, NOW, FakeOpener({"subscription/stats": 403})
            )
        self.assertEqual("HTTP 403", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
