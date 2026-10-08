"""The serve mode: sampling, the desk meter contract, the token rule, and the HTTP surface."""

from __future__ import annotations

import http.client
import json
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pitwall.agents.usage import (
    accounts,
    serve,
)
from pitwall.agents.usage.rows import (
    Row,
    Window,
)

ROOT = Path(__file__).resolve().parents[2]

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
RECORDED = json.loads(
    (ROOT / "tests/agents" / "fixtures" / "usage" / "submeter-usage.json").read_text(
        encoding="utf-8"
    )
)


def row(
    plan: str,
    *,
    account: str = "",
    five: int | None = 10,
    week: int | None = 20,
    status: str = "ok",
    detail: str = "",
    tier: str = "Pro",
    long_name: str = "7d",
) -> Row:
    windows = (
        Window("5h", five, "2026-09-28T14:00:00Z"),
        Window(long_name, week, "2026-10-01T12:00:00Z"),
    )
    return Row(
        plan,
        account,
        (),
        accounts.LABELS.get(plan, plan),
        tier,
        windows,
        status,
        detail,
        "2026-09-28T12:00:00Z",
    )


class SamplerTests(unittest.TestCase):
    def test_no_rate_until_a_baseline_is_five_minutes_old(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=10)], 0.0)
        self.assertIsNone(sampler.rate(row("claude", five=12), "5h", 299.0))
        self.assertEqual(0.4, sampler.rate(row("claude", five=12), "5h", 300.0))

    def test_a_baseline_older_than_ten_minutes_is_not_used(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=10)], 0.0)
        self.assertIsNone(sampler.rate(row("claude", five=40), "5h", 601.0))

    def test_the_newest_aged_sample_is_the_baseline(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=10)], 0.0)
        sampler.observe([row("claude", five=20)], 200.0)
        self.assertEqual(2.0, sampler.rate(row("claude", five=30), "5h", 500.0))

    def test_a_window_reset_gives_no_rate(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=99)], 0.0)
        self.assertIsNone(sampler.rate(row("claude", five=2), "5h", 300.0))

    def test_time_to_full_needs_a_rate_above_the_floor(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=50, week=50)], 0.0)
        current = row("claude", five=60, week=50)
        self.assertEqual(20, sampler.eta_minutes(current, "5h", 300.0))
        self.assertIsNone(sampler.eta_minutes(current, "7d", 300.0))

    def test_failed_rows_add_no_samples_and_carry_no_rate(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=10)], 0.0)
        sampler.observe([row("claude", five=10, status="stale")], 300.0)
        self.assertEqual([10], sampler.history(row("claude")))
        self.assertIsNone(sampler.rate(row("claude", five=30, status="stale"), "5h", 300.0))
        self.assertIsNone(sampler.eta_minutes(row("claude", five=30, status="stale"), "5h", 300.0))

    def test_history_keeps_one_point_per_five_minutes_and_the_newest_twenty_four(self) -> None:
        sampler = serve.Sampler()
        for index in range(60):
            sampler.observe([row("claude", five=index)], index * 150.0)
        self.assertEqual(list(range(12, 60, 2)), sampler.history(row("claude")))

    def test_accounts_of_one_plan_keep_separate_series(self) -> None:
        sampler = serve.Sampler()
        sampler.observe(
            [row("claude", account="work", five=10), row("claude", account="home", five=70)], 0.0
        )
        self.assertEqual([10], sampler.history(row("claude", account="work")))
        self.assertEqual([70], sampler.history(row("claude", account="home")))


class LegacyPayloadTests(unittest.TestCase):
    def payload(
        self, rows: list[Row], sampler: serve.Sampler | None = None, now: datetime = NOW
    ) -> dict[str, object]:
        return serve.legacy_payload(rows, sampler or serve.Sampler(), now)

    def test_the_shape_matches_a_payload_recorded_from_submeter(self) -> None:
        sampler = serve.Sampler()
        rows = [
            row("claude", five=42, week=24, tier="Max 20x"),
            row("codex", five=35, week=10),
            row("glm", five=21, week=42, tier="Coding Plan"),
        ]
        sampler.observe(rows, NOW.timestamp())
        ours = self.payload(rows, sampler)
        self.assertEqual(list(RECORDED), list(ours))
        self.assertIsInstance(ours["updated"], int)
        for recorded, harness in zip(RECORDED["providers"], ours["providers"], strict=False):  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
            self.assertEqual(list(recorded), list(harness))
            for key, value in recorded.items():
                if value is not None:
                    self.assertIs(type(value), type(harness[key]), key)
            for key in (
                "name",
                "label",
                "tier",
                "s_pct",
                "w_pct",
                "status",
                "extra",
                "s_rate",
                "w_rate",
                "s_eta_min",
                "w_eta_min",
                "s_hist",
            ):
                self.assertEqual(recorded[key], harness[key], key)

    def test_minutes_are_computed_from_the_reset_instant_and_never_negative(self) -> None:
        (harness,) = self.payload([row("claude")])["providers"]  # type: ignore[misc]  # reason: the test navigates a loosely typed JSON document
        self.assertEqual((120, 4320), (harness["s_reset_min"], harness["w_reset_min"]))
        (late,) = self.payload([row("claude")], now=NOW + timedelta(days=30))["providers"]  # type: ignore[misc]  # reason: the test navigates a loosely typed JSON document
        self.assertEqual((0, 0), (late["s_reset_min"], late["w_reset_min"]))

    def test_the_thirty_day_window_stands_in_for_the_long_window(self) -> None:
        (harness,) = self.payload(
            [
                Row(
                    "model-studio",
                    "",
                    (),
                    "Model Studio",
                    "Pro",
                    (Window("30d", 75, "2026-10-08T12:00:00Z"),),
                    "ok",
                    "",
                    "2026-09-28T12:00:00Z",
                )
            ]
        )["providers"]  # type: ignore[misc]  # reason: the test navigates a loosely typed JSON document
        self.assertEqual(
            (None, None, 75, 14400),
            (
                harness["s_pct"],
                harness["s_reset_min"],
                harness["w_pct"],
                harness["w_reset_min"],
            ),
        )

    def test_unknown_rows_are_left_out_and_seven_rows_is_the_limit(self) -> None:
        rows = [row("kimi", status="unknown")] + [row(f"plan{index}") for index in range(9)]
        harnesses = self.payload(rows)["providers"]
        self.assertEqual(
            [f"plan{index}" for index in range(7)], [harness["name"] for harness in harnesses]
        )  # type: ignore[union-attr]  # reason: the test navigates a loosely typed JSON document

    def test_account_tags(self) -> None:
        def tags(*accounts: str) -> list[object]:
            harnesses = self.payload([row("claude", account=account) for account in accounts])[
                "providers"
            ]
            return [(harness["name"], harness.get("account")) for harness in harnesses]  # type: ignore[union-attr]  # reason: the test navigates a loosely typed JSON document

        self.assertEqual([("claude", None)], tags(""))
        self.assertEqual([("claude-work", "wor"), ("claude-home", "hom")], tags("work", "home"))
        self.assertEqual([("claude", "1"), ("claude-home", "hom")], tags("", "home"))
        self.assertEqual([("claude-work1", "1"), ("claude-work2", "2")], tags("work1", "work2"))

    def test_detail_is_cut_and_keyed_by_kind(self) -> None:
        (failed,) = self.payload(
            [row("glm", status="error", detail="x" * 200, five=None, week=None)]
        )["providers"]  # type: ignore[misc]  # reason: the test navigates a loosely typed JSON document
        self.assertEqual({"error": "x" * 80}, failed["extra"])
        (noted,) = self.payload([row("glm", detail="tools 16%")])["providers"]  # type: ignore[misc]  # reason: the test navigates a loosely typed JSON document
        self.assertEqual({"note": "tools 16%"}, noted["extra"])


class PlansPayloadTests(unittest.TestCase):
    def test_rows_gain_rate_time_to_full_and_history(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=50)], NOW.timestamp() - 300)
        payload = serve.plans_payload([row("claude", five=60)], sampler, NOW)
        self.assertEqual("2026-09-28T12:00:00Z", payload["observed_at"])
        five, week = payload["plans"][0]["windows"]
        self.assertEqual((2.0, 20, [50]), (five["rate"], five["eta_min"], five["history"]))
        self.assertEqual((0.0, None, []), (week["rate"], week["eta_min"], week["history"]))


class TokenRuleTests(unittest.TestCase):
    def test_loopback_needs_no_token(self) -> None:
        for host in ("127.0.0.1", "::1", "localhost"):
            self.assertIsNone(serve.resolve_token(host, {}))
        self.assertEqual("t", serve.resolve_token("127.0.0.1", {serve.TOKEN_ENV: " t "}))

    def test_any_other_address_needs_one(self) -> None:
        for host in ("0.0.0.0", "192.0.2.10", "::"):
            with self.assertRaises(serve.ServeConfigError) as caught:
                serve.resolve_token(host, {serve.TOKEN_ENV: "  "})
            self.assertIn(serve.TOKEN_ENV, str(caught.exception))
        self.assertEqual("t", serve.resolve_token("0.0.0.0", {serve.TOKEN_ENV: "t"}))

    def test_run_refuses_before_binding(self) -> None:
        with self.assertRaises(serve.ServeConfigError):
            serve.run("0.0.0.0", 0, 45.0, {}, lambda _now: [])
        with self.assertRaises(serve.ServeConfigError) as caught:
            serve.run("127.0.0.1", 0, 29.0, {}, lambda _now: [])
        self.assertIn("at least 30 seconds", str(caught.exception))


class RefreshTests(unittest.TestCase):
    def test_a_refresh_that_raises_keeps_the_rows_already_held(self) -> None:
        state = serve.UsageState()
        self.assertTrue(
            serve.refresh_once(state, lambda _now: [row("claude", five=33)], lambda: NOW)
        )

        def broken(_now: datetime) -> list[Row]:
            raise RuntimeError("routes file vanished")

        self.assertFalse(serve.refresh_once(state, broken, lambda: NOW))
        self.assertEqual(33, state.legacy(NOW)["providers"][0]["s_pct"])


class HttpTests(unittest.TestCase):
    def serve(self, token: str | None) -> tuple[str, int]:
        state = serve.UsageState()
        state.update([row("claude", five=41, week=63)], NOW)
        server = serve.make_server("127.0.0.1", 0, token, state, lambda: NOW)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "127.0.0.1", server.server_address[1]

    def get(
        self, address: tuple[str, int], path: str, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(*address, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return (
                response.status,
                {key.lower(): value for key, value in response.getheaders()},
                response.read(),
            )
        finally:
            connection.close()

    def test_every_response_has_a_length_and_is_never_chunked(self) -> None:
        address = self.serve("desk-token")
        auth = {"Authorization": "Bearer desk-token"}
        for path, headers, expected in (
            ("/health", {}, 200),
            ("/usage", auth, 200),
            ("/plans", auth, 200),
            ("/usage", {}, 401),
            ("/nope", auth, 404),
        ):
            with self.subTest(path=path, expected=expected):
                status, sent, body = self.get(address, path, headers)
                self.assertEqual(expected, status)
                self.assertEqual(str(len(body)), sent["content-length"])
                self.assertNotIn("transfer-encoding", sent)
                json.loads(body)

    def test_the_token_is_required_and_compared_whole(self) -> None:
        address = self.serve("desk-token")
        for offered in (
            "",
            "Bearer",
            "Bearer desk-toke",
            "Bearer desk-token-more",
            "desk-token",
            "bearer desk-token",
        ):
            with self.subTest(offered=offered):
                self.assertEqual(401, self.get(address, "/plans", {"Authorization": offered})[0])
        self.assertEqual(
            200, self.get(address, "/plans", {"Authorization": "Bearer desk-token"})[0]
        )

    def test_health_is_open_and_says_nothing_else(self) -> None:
        status, _sent, body = self.get(self.serve("desk-token"), "/health")
        self.assertEqual((200, {"ok": True}), (status, json.loads(body)))

    def test_without_a_token_loopback_is_open(self) -> None:
        status, _sent, body = self.get(self.serve(None), "/usage?x=1")
        self.assertEqual(200, status)
        self.assertEqual(41, json.loads(body)["providers"][0]["s_pct"])


if __name__ == "__main__":
    unittest.main()
