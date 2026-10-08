"""Tests for the setup routes picker."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

from pitwall.agents import profiles_setup
from tests.agents.profiles_fixture import read_profiles
from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]


class FakeSession:
    def __init__(self, keys: list[str], *, term: str = "xterm") -> None:
        self.keys = iter(keys)
        self.term = term
        self.output = ""
        self.paints: list[tuple[str, ...]] = []

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read_key(self) -> str:
        return next(self.keys)

    def write(self, text: str) -> None:
        self.output += text

    def repaint(self, lines: Sequence[str]) -> None:
        self.paints.append(tuple(lines))

    def finish_repaint(self) -> None:
        self.output += "\n"


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class PickerPrimitiveTests(unittest.TestCase):
    def test_pick_one_moves_wraps_selects_and_cancels(self) -> None:
        choices = [profiles_setup.Choice("a", "A"), profiles_setup.Choice("b", "B")]
        session = FakeSession(["DOWN", "DOWN", "\r"])
        picked = profiles_setup.pick_one(session, "Title", "hint", choices, use_color=False)  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual("a", picked.key if picked else None)
        self.assertIsNone(
            profiles_setup.pick_one(FakeSession(["q"]), "T", "h", choices, use_color=False)
        )  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument

    def test_read_line_supports_default_backspace_and_escape(self) -> None:
        self.assertEqual(
            "dflt", profiles_setup.read_line(FakeSession(["\r"]), "Name", default="dflt")
        )  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual(
            "ab", profiles_setup.read_line(FakeSession(["a", "b", "c", "\x7f", "\r"]), "Name")
        )  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertIsNone(profiles_setup.read_line(FakeSession(["x", "ESC"]), "Name"))  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument


class SetupFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        bin_dir = self.root / "bin"
        for name in ("agy", "qwen", "opencode"):
            executable(bin_dir / name)
        self.env = {
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "PATH": str(bin_dir),
            "TERM": "xterm",
        }

    def tearDown(self) -> None:
        self.directory.cleanup()

    def routes_file(self) -> dict[str, object]:
        return read_profiles(self.root / "config" / "pitwall" / "pitwall.toml")

    def registry_row(self, model_id: str) -> int:
        registry = profiles_setup.load_registry()
        catalog = profiles_setup.load_catalog(registry=registry)
        return next(
            index
            for index, choice in enumerate(profiles_setup.model_choices(registry, catalog))
            if choice.key.endswith(f":{model_id}")
        )

    def test_custom_endpoint_on_qwen_is_saved_without_sync(self) -> None:
        keys = ["\r"]  # model screen: first row is "Custom endpoint…"
        keys += list("my-model") + ["\r"]  # model id
        keys += ["DOWN", "\r"]  # harness: opencode (needs sync) is first, qwen second
        keys += ["\r"]  # accept suggested name "my-model"
        keys += list("http://127.0.0.1:8080/v1") + ["\r"]
        keys += ["\r"]  # no key variable
        keys += ["\r"]  # seat: none
        fake = FakeSession(keys)
        with mock.patch.object(profiles_setup, "TtySession", return_value=fake):
            code = profiles_setup.run_profiles_setup(self.env, no_color=True)
        self.assertEqual(0, code, fake.output)
        entry = self.routes_file()["models"]["my-model"]  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        self.assertEqual(
            {
                "model": "my-model",
                "harness": "qwen",
                "endpoint": {"baseUrl": "http://127.0.0.1:8080/v1"},
            },
            entry,
        )
        self.assertNotIn("profiles sync", fake.output)

    def test_effort_prompt_offers_the_harness_values(self) -> None:
        keys = ["DOWN"] * self.registry_row("gemini-3.7-flash") + ["\r"]
        keys += ["DOWN", "DOWN", "\r"]  # harness: opencode, qwen, agy -> agy
        keys += ["\r"]  # accept suggested name
        keys += ["\r"]  # seat: none
        keys += ["DOWN", "DOWN", "\r"]  # effort: none, low, medium -> medium
        fake = FakeSession(keys)
        with mock.patch.object(profiles_setup, "TtySession", return_value=fake):
            code = profiles_setup.run_profiles_setup(self.env, no_color=True)
        self.assertEqual(0, code, fake.output)
        entry = self.routes_file()["models"]["gemini-3.7-flash"]  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        self.assertEqual("medium", entry["effort"])
        self.assertTrue(
            any("Effort" in line for paint in fake.paints for line in paint), fake.paints
        )

    def test_opencode_choice_offers_and_runs_sync(self) -> None:
        keys = (
            ["\r"]
            + list("m")
            + ["\r"]
            + ["\r"]
            + ["\r"]
            + list("http://h:1/v1")
            + ["\r"]
            + list("MY_KEY")
            + ["\r"]
            + ["\r"]
            + ["\r"]
            + ["y", "y"]
        )
        fake = FakeSession(keys)
        with mock.patch.object(profiles_setup, "TtySession", return_value=fake):
            code = profiles_setup.run_profiles_setup(self.env, no_color=True)
        self.assertEqual(0, code, fake.output)
        opencode = json.loads(
            (self.root / "config" / "opencode" / "opencode.json").read_text(encoding="utf-8")
        )
        self.assertEqual("{env:MY_KEY}", opencode["provider"]["m"]["options"]["apiKey"])

    def test_cancel_and_missing_tty(self) -> None:
        fake = FakeSession(["q"])
        with mock.patch.object(profiles_setup, "TtySession", return_value=fake):
            self.assertEqual(0, profiles_setup.run_profiles_setup(self.env, no_color=True))
        self.assertEqual(
            2, profiles_setup.run_profiles_setup(self.env, tty_path=str(self.root / "no-tty"))
        )
        cli = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "setup",
                "profiles",
            ],
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=HANG_GUARD_SECS,
        )
        self.assertEqual(2, cli.returncode, cli.stderr)
        self.assertIn("profiles add", cli.stderr)


if __name__ == "__main__":
    unittest.main()
