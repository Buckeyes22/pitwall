"""Usage rows: status rules, rounding, instants, the JSON fetch, and the table."""

from __future__ import annotations

import io
import unittest
from datetime import UTC, datetime
from pathlib import Path
from urllib import error

from pitwall.agents.usage import (
    rows,
)

ROOT = Path(__file__).resolve().parents[2]

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class StatusTests(unittest.TestCase):
    def test_thresholds(self) -> None:
        def status(*values: int | None) -> str:
            return rows.derive_status([rows.Window("5h", value, None) for value in values])

        self.assertEqual("ok", status(0, 79))
        self.assertEqual("warn", status(80, 10))
        self.assertEqual("warn", status(None, 99))
        self.assertEqual("limit", status(100, 0))
        self.assertEqual("ok", status(None, None))

    def test_a_reported_limit_wins_over_the_numbers(self) -> None:
        windows = [rows.Window("5h", 10, None)]
        self.assertEqual("limit", rows.derive_status(windows, limit_reached=True))

    def test_a_vendor_value_above_one_hundred_is_kept_and_is_a_limit(self) -> None:
        self.assertEqual(104, rows.percent(104.2))
        self.assertEqual("limit", rows.derive_status([rows.Window("7d", 104, None)]))


class PercentTests(unittest.TestCase):
    def test_rounds_half_up_and_refuses_non_numbers(self) -> None:
        self.assertEqual(1, rows.percent(0.5))
        self.assertEqual(42, rows.percent(41.5))
        self.assertEqual(0, rows.percent(0))
        for value in (None, "41", True, float("nan"), float("inf")):
            self.assertIsNone(rows.percent(value))


class InstantTests(unittest.TestCase):
    def test_instants_are_normalised_to_utc(self) -> None:
        self.assertEqual(
            "2026-09-28T14:20:00Z", rows.iso_utc(rows.parse_instant("2026-09-28T16:20:00+02:00"))
        )
        self.assertEqual(
            "2026-09-28T14:20:00Z", rows.iso_utc(rows.parse_instant("2026-09-28T14:20:00Z"))
        )
        self.assertEqual("2026-09-28T12:00:00Z", rows.from_epoch(NOW.timestamp()))


class RowTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        row = rows.Row(
            "claude",
            "work",
            ("claude-work",),
            "Claude",
            "Max 20x",
            (rows.Window("5h", 41, "2026-09-28T14:20:00Z"),),
            "ok",
            "",
            "2026-09-28T12:00:00Z",
        )
        self.assertEqual(row, rows.Row.from_dict(row.to_dict()))

    def test_an_unknown_status_is_refused(self) -> None:
        value = rows.Row("glm", "", (), "GLM", "", (), "ok", "", "2026-09-28T12:00:00Z").to_dict()
        value["status"] = "fine"
        with self.assertRaises(ValueError):
            rows.Row.from_dict(value)


class FetchTests(unittest.TestCase):
    def test_json_is_returned(self) -> None:
        self.assertEqual(
            {"a": 1}, rows.fetch_json(object(), lambda *_a, **_k: _Response(b'{"a": 1}'))
        )  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument

    def test_failures_carry_a_code_and_never_a_body(self) -> None:
        def refuse(*_a: object, **_k: object) -> object:
            raise error.HTTPError(
                "https://example.invalid",
                401,
                "Unauthorized",
                None,
                io.BytesIO(b"echoed-credential"),
            )  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument

        with self.assertRaises(rows.ReadError) as caught:
            rows.fetch_json(object(), refuse)  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual("HTTP 401", str(caught.exception))

        def unreachable(*_a: object, **_k: object) -> object:
            raise error.URLError("refused")

        with self.assertRaises(rows.ReadError) as caught:
            rows.fetch_json(object(), unreachable)  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual("network failure", str(caught.exception))

        with self.assertRaises(rows.ReadError) as caught:
            rows.fetch_json(object(), lambda *_a, **_k: _Response(b"<html>"))  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual("unexpected response", str(caught.exception))


class TableTests(unittest.TestCase):
    def test_rows_render_with_reset_times_and_routes(self) -> None:
        table = rows.render_table(
            [
                rows.Row(
                    "claude",
                    "work",
                    (),
                    "Claude",
                    "Max 20x",
                    (
                        rows.Window("5h", 92, "2026-09-28T14:20:00Z"),
                        rows.Window("7d", 63, "2026-10-01T09:00:00Z"),
                    ),
                    "warn",
                    "",
                    "2026-09-28T12:00:00Z",
                ),
                rows.Row(
                    "claude",
                    "home",
                    ("claude-home",),
                    "Claude",
                    "Max 5x",
                    (rows.Window("5h", 4, None),),
                    "ok",
                    "",
                    "2026-09-28T12:00:00Z",
                ),
                rows.Row(
                    "model-studio",
                    "",
                    (),
                    "Model Studio",
                    "Pro",
                    (),
                    "unknown",
                    "renews 2026-10-21",
                    "2026-09-28T12:00:00Z",
                ),
            ],
            now=NOW,
        )
        lines = table.splitlines()
        self.assertIn("5h 92% resets 14:20", lines[1])
        self.assertIn("7d 63% resets Thu 09:00", lines[1])
        self.assertTrue(lines[1].rstrip().endswith("(default)"))
        self.assertTrue(lines[2].rstrip().endswith("route claude-home"))
        self.assertIn("renews 2026-10-21", lines[3])

    def test_no_rows(self) -> None:
        self.assertEqual("no subscriptions found on this machine\n", rows.render_table([], now=NOW))


if __name__ == "__main__":
    unittest.main()
