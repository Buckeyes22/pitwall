"""collect(): rows from accounts, the cache between reads, and failures that stay informative."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pitwall.agents import profiles, usage
from pitwall.agents.registry import (
    load_registry,
)
from pitwall.agents.usage import (
    cache,
)
from tests.agents.usage_test_support import (
    FakeOpener,
    claude_login,
    codex_login,
)

ROOT = Path(__file__).resolve().parents[2]

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
CLAUDE_OK = {
    "five_hour": {"utilization": 92, "resets_at": "2026-09-28T14:20:00Z"},
    "seven_day": {"utilization": 63, "resets_at": "2026-10-01T09:00:00Z"},
}
REFRESH_ADDRESSES = (
    "oauth/token",
    "auth.openai.com",
    "platform.claude.com",
    "console.anthropic.com",
)


class CollectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {"HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")}
        self.config = profiles.validate_profiles(profiles.empty_profiles(), registry=self.registry)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def collect(
        self,
        opener: FakeOpener,
        *,
        now: datetime = NOW,
        installed: tuple[str, ...] = (),
        config: dict[str, object] | None = None,
    ) -> list[usage.Row]:
        return usage.collect(
            self.env,
            registry=self.registry,
            routes_config=self.config if config is None else config,
            home=self.home,
            now=now,
            opener=opener,
            installed=lambda harness: harness in installed,
        )

    def test_nothing_configured(self) -> None:
        self.assertEqual([], self.collect(FakeOpener()))

    def test_a_reading_becomes_a_row_with_a_status(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 3600) * 1000)
        (row,) = self.collect(FakeOpener({"oauth/usage": CLAUDE_OK}))
        self.assertEqual(
            ("claude", "", (), "Claude", "Max 20x", "warn", "2026-09-28T12:00:00Z"),
            (row.plan, row.account, row.routes, row.label, row.tier, row.status, row.observed_at),
        )
        self.assertEqual([92, 63], [window.used_pct for window in row.windows])

    def test_plans_without_a_reader_are_unknown(self) -> None:
        rows = self.collect(FakeOpener(), installed=("kimi", "grok"))
        self.assertEqual(
            [("kimi", "unknown", "no usage source"), ("grok", "unknown", "no usage source")],
            [(row.plan, row.status, row.detail) for row in rows],
        )

    def test_inside_the_interval_the_cached_row_is_returned_without_a_request(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        first = self.collect(FakeOpener({"oauth/usage": CLAUDE_OK}))
        opener = FakeOpener({"oauth/usage": 500})
        again = self.collect(opener, now=NOW + timedelta(seconds=299))
        self.assertEqual(first, again)
        self.assertEqual([], opener.calls)
        self.collect(opener, now=NOW + timedelta(seconds=300))
        self.assertEqual(1, len(opener.calls))

    def test_a_failure_after_a_good_read_is_stale_with_the_old_numbers(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        self.collect(FakeOpener({"oauth/usage": CLAUDE_OK}))
        later = NOW + timedelta(seconds=300)
        (row,) = self.collect(FakeOpener({"oauth/usage": 429}), now=later)
        self.assertEqual(("stale", "HTTP 429"), (row.status, row.detail))
        self.assertEqual([92, 63], [window.used_pct for window in row.windows])
        self.assertEqual("2026-09-28T12:00:00Z", row.observed_at)
        # still stale, and still the old numbers, when served from the cache
        (cached,) = self.collect(FakeOpener(), now=later + timedelta(seconds=10))
        self.assertEqual(row, cached)

    def test_a_failure_with_no_earlier_row_is_an_error(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        (row,) = self.collect(FakeOpener({"oauth/usage": 401}))
        self.assertEqual(("error", "HTTP 401", ()), (row.status, row.detail, row.windows))

    def test_a_failing_source_is_throttled_too(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        self.collect(FakeOpener({"oauth/usage": 500}))
        opener = FakeOpener({"oauth/usage": CLAUDE_OK})
        (row,) = self.collect(opener, now=NOW + timedelta(seconds=30))
        self.assertEqual("error", row.status)
        self.assertEqual([], opener.calls)

    def test_the_attempt_is_recorded_before_the_request(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        seen: list[object] = []

        class Watching(FakeOpener):
            def __call__(inner, outbound, timeout=0.0):  # type: ignore[no-untyped-def,override]  # reason: test helper is intentionally left unannotated; test double overrides with a looser signature
                seen.append(cache.load(self.env, "claude", ""))
                return super().__call__(outbound, timeout)

        self.collect(Watching({"oauth/usage": CLAUDE_OK}))
        self.assertEqual(
            [{"attemptedAt": NOW.timestamp(), "row": None, "error": "read did not finish"}], seen
        )

    def test_an_expired_idle_account_is_stale_and_says_so(self) -> None:
        second = claude_login(
            self.home / "logins" / ".claude-home", expires_at_ms=(NOW.timestamp() + 3600) * 1000
        )
        document = profiles.empty_profiles()
        document["models"] = {
            "claude-home": {
                "model": "sonnet",
                "harness": "claude",
                "env": {"CLAUDE_CONFIG_DIR": str(second)},
            }
        }
        config = profiles.validate_profiles(document, registry=self.registry)
        self.collect(FakeOpener({"oauth/usage": CLAUDE_OK}), config=config)
        (row,) = self.collect(FakeOpener(), now=NOW + timedelta(hours=2), config=config)
        self.assertEqual(("home", ("claude-home",), "stale"), (row.account, row.routes, row.status))
        self.assertIn("login expired", row.detail)

    def test_a_route_with_a_missing_login_directory_is_an_error_that_names_it(self) -> None:
        document = profiles.empty_profiles()
        document["models"] = {
            "gone": {
                "model": "sonnet",
                "harness": "claude",
                "env": {"CLAUDE_CONFIG_DIR": str(self.home / "nowhere")},
            }
        }
        opener = FakeOpener()
        (row,) = self.collect(
            opener, config=profiles.validate_profiles(document, registry=self.registry)
        )
        self.assertEqual(
            ("error", "route gone names a login directory with no login in it"),
            (row.status, row.detail),
        )
        self.assertEqual([], opener.calls)

    def test_a_reader_that_trips_on_an_odd_shape_is_an_unexpected_response(self) -> None:
        self.env["GLM_API_KEY"] = "glm-key"  # pragma: allowlist secret

        def trips(*_args: object) -> usage.rows.Reading:
            raise KeyError("limits")

        rows = usage.collect(
            self.env,
            registry=self.registry,
            routes_config=self.config,
            home=self.home,
            now=NOW,
            opener=FakeOpener(),
            installed=lambda _harness: False,
            readers={"glm": trips},
        )
        self.assertEqual(
            [("glm", "error", "unexpected response")],
            [(row.plan, row.status, row.detail) for row in rows],
        )

    def test_a_damaged_cache_file_is_read_as_no_cache(self) -> None:
        self.env["GLM_API_KEY"] = "glm-key"  # pragma: allowlist secret
        path = cache.path_for(self.env, "glm", "")
        path.parent.mkdir(parents=True)
        path.write_text('{"attemptedAt": 17', encoding="utf-8")
        opener = FakeOpener({"quota/limit": {"data": {"level": "pro", "limits": []}}})
        (row,) = self.collect(opener)
        self.assertEqual("ok", row.status)
        self.assertEqual(1, len(opener.calls))

    def test_no_reader_asks_for_a_token_refresh_or_writes_a_credential_file(self) -> None:
        claude_dir = claude_login(
            self.home / ".claude", expires_at_ms=(NOW.timestamp() + 86400) * 1000
        )
        codex_dir = codex_login(self.home / ".codex", exp=NOW.timestamp() + 86400)
        self.env.update(
            {"GLM_API_KEY": "glm-key", "MINIMAX_API_KEY": "mm-key"}  # pragma: allowlist secret
        )
        before = {
            path: path.read_bytes()
            for path in (claude_dir / ".credentials.json", codex_dir / "auth.json")
        }
        for answers in (
            {},
            {"oauth/usage": 401, "wham/usage": 401, "quota/limit": 401, "token_plan/remains": 401},
        ):
            opener = FakeOpener(answers)
            self.collect(opener, now=NOW + timedelta(hours=len(answers)))
            self.assertEqual(4, len(opener.calls))
            for call in opener.calls:
                self.assertEqual("GET", call.get_method())
                self.assertIsNone(call.data)
                for address in REFRESH_ADDRESSES:
                    self.assertNotIn(address, call.full_url)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_cached_files_hold_no_credential(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        self.env["GLM_API_KEY"] = "glm-key"  # pragma: allowlist secret
        self.collect(FakeOpener({"oauth/usage": CLAUDE_OK, "quota/limit": 403}))
        stored = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.home / "state" / "pitwall" / "agents" / "usage").iterdir()
        )
        for value in ("claude-access", "claude-refresh", "glm-key", "body-that-must-not-leak"):
            self.assertNotIn(value, stored)
        self.assertEqual(
            {"claude.json", "glm.json"},
            {
                path.name
                for path in (self.home / "state" / "pitwall" / "agents" / "usage").iterdir()
            },
        )
        json.loads(
            (self.home / "state" / "pitwall" / "agents" / "usage" / "glm.json").read_text(
                encoding="utf-8"
            )
        )


if __name__ == "__main__":
    unittest.main()
