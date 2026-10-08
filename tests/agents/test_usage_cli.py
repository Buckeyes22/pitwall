"""``pitwall usage``: both output forms and the exit codes."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pitwall import cli
from pitwall.agents import usage
from pitwall.agents.usage.rows import (
    Row,
    Window,
)
from pitwall.cli import usage as usage_cli

ROOT = Path(__file__).resolve().parents[2]

ROWS = [
    Row(
        "claude",
        "",
        (),
        "Claude",
        "Max 20x",
        (Window("5h", 41, None), Window("7d", 63, None)),
        "ok",
        "",
        "2026-09-28T12:00:00Z",
    ),
    Row("kimi", "", (), "Kimi", "", (), "unknown", "no usage source", "2026-09-28T12:00:00Z"),
]


class UsageCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            "HOME": self.tmp.name,
            "XDG_STATE_HOME": str(Path(self.tmp.name) / "state"),
            "XDG_CONFIG_HOME": str(Path(self.tmp.name) / "config"),
            "PATH": "/usr/bin:/bin",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *argv: str, rows: list[Row] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, self.env, clear=True))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            if rows is not None:
                stack.enter_context(mock.patch.object(usage, "collect", return_value=rows))
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_json(self) -> None:
        code, out, err = self.run_cli("usage", "--json", rows=ROWS)
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual(["observed_at", "plans"], sorted(payload))
        self.assertEqual([row.to_dict() for row in ROWS], payload["plans"])

    def test_table(self) -> None:
        code, out, _err = self.run_cli("usage", rows=ROWS)
        self.assertEqual(0, code)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("plan"))
        self.assertIn("5h 41%", lines[1])
        self.assertIn("unknown", lines[2])

    def test_rows_that_failed_still_exit_zero(self) -> None:
        failed = [Row("glm", "", (), "GLM", "", (), "error", "HTTP 401", "2026-09-28T12:00:00Z")]
        self.assertEqual(0, self.run_cli("usage", "--json", rows=failed)[0])

    def test_an_empty_machine_really_runs_and_prints_no_plans(self) -> None:
        code, out, err = self.run_cli("usage", "--json")
        self.assertEqual(0, code, err)
        self.assertEqual([], json.loads(out)["plans"])

    def test_an_unknown_flag_is_a_usage_error(self) -> None:
        code, _out, err = self.run_cli("usage", "--nope")
        self.assertEqual(2, code)
        self.assertIn("usage", err.lower())

    def test_an_unreadable_routes_file_exits_two(self) -> None:
        target = Path(self.tmp.name) / "config" / "pitwall" / "pitwall.toml"
        target.parent.mkdir(parents=True)
        target.write_text("[agents.profiles", encoding="utf-8")
        code, _out, err = self.run_cli("usage")
        self.assertEqual(2, code)
        self.assertIn("agent profiles", err)


class UsageServeCliTests(unittest.TestCase):
    def run_cli(self, *argv: str, env: dict[str, str] | None = None) -> tuple[int, str]:
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            base = {
                "HOME": directory,
                "XDG_STATE_HOME": str(Path(directory) / "state"),
                "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                "PATH": "/usr/bin:/bin",
            }
            with (
                mock.patch.dict(os.environ, {**base, **(env or {})}, clear=True),
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                try:
                    code = cli.main(list(argv))
                except SystemExit as exc:
                    code = int(exc.code or 0)
        return code, err.getvalue()

    def test_a_wide_bind_without_a_token_is_a_configuration_error(self) -> None:
        code, err = self.run_cli("usage", "serve", "--host", "0.0.0.0", "--port", "0")
        self.assertEqual(78, code)
        self.assertIn("PITWALL_AGENTS_USAGE_TOKEN", err)

    def test_an_interval_under_thirty_seconds_is_refused(self) -> None:
        code, err = self.run_cli("usage", "serve", "--port", "0", "--interval", "5")
        self.assertEqual(78, code)
        self.assertIn("at least 30 seconds", err)

    def test_a_port_that_cannot_be_bound_exits_one(self) -> None:
        with mock.patch.object(
            usage_cli.usage_serve, "make_server", side_effect=OSError(98, "Address already in use")
        ):
            code, err = self.run_cli("usage", "serve", "--port", "0")
        self.assertEqual(1, code)
        self.assertIn("cannot serve on 127.0.0.1:0", err)

    def test_the_server_is_started_with_the_arguments_and_an_interrupt_exits_zero(self) -> None:
        with mock.patch.object(usage_cli.usage_serve, "run", side_effect=KeyboardInterrupt) as run:
            code, _err = self.run_cli(
                "usage", "serve", "--host", "::1", "--port", "9001", "--interval", "60"
            )
        self.assertEqual(0, code)
        self.assertEqual(("::1", 9001, 60.0), run.call_args.args[:3])


if __name__ == "__main__":
    unittest.main()
