"""Golden characterization tests for the v0.2 public shim behavior."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.agents.shim_test_support import ROUTE_SHIM, SHIMS, ShimSandbox
from tests.hang_guard import HANG_GUARD_SECS

HARNESS_ARGS = {
    "agy": lambda prompt: [str(prompt)],
    "codex": lambda prompt: [str(prompt)],
    "claude": lambda prompt: [str(prompt)],
    "cline": lambda prompt: [str(prompt)],
    "dsh": lambda prompt: [str(prompt)],
    "goose": lambda prompt: [str(prompt)],
    "grok": lambda prompt: [str(prompt)],
    "hermes": lambda prompt: [str(prompt)],
    "kimi": lambda prompt: [str(prompt)],
    "muse": lambda prompt: [str(prompt)],
    "pi": lambda prompt: [str(prompt)],
    "qwen": lambda prompt: [str(prompt)],
    "zcode": lambda prompt: [str(prompt)],
    "opencode": lambda prompt: ["test-provider/test-model", str(prompt)],
}


class ShimContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandboxes: list[ShimSandbox] = []

    def tearDown(self) -> None:
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def sandbox(self) -> ShimSandbox:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        return sandbox

    def test_shim_without_pitwall_on_path_exits_127_naming_the_command(self) -> None:
        # A generated shim guards `command -v pitwall` itself, so a missing `pitwall` is 127
        # rather than a SHIM-DONE line (bash's own exec error is 126 on a directory hit).
        # An empty PATH searches the cwd, so also run from a cwd holding a `pitwall` directory.
        with tempfile.TemporaryDirectory() as cwd:
            (Path(cwd) / "pitwall").mkdir()
            for shim in [*SHIMS.values(), ROUTE_SHIM]:
                with self.subTest(shim=shim.name):
                    result = subprocess.run(
                        ["/bin/bash", str(shim)],
                        env={"HOME": "/nonexistent", "PATH": ""},
                        cwd=cwd,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(127, result.returncode)
                    self.assertIn(b"pitwall", result.stderr)

    def test_usage_errors_emit_only_plain_sentinel_and_no_ledger(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                result = sandbox.run(shim, [])
                self.assertEqual(64, result.returncode)
                self.assertEqual(b"SHIM-DONE exit=64\n", result.stdout)
                self.assertEqual([], sandbox.ledger_records())

    def test_qwen_prompt_mode_conflicts_are_usage_errors_before_harness_start(self) -> None:
        for flag in ("-y", "--yolo", "--approval-mode", "-p", "--prompt=other", "--output-format"):
            with self.subTest(flag=flag):
                sandbox = self.sandbox()
                sandbox.install_harness("qwen")
                prompt = sandbox.prompt()
                result = sandbox.run("qwen", [str(prompt), flag])
                self.assertEqual(64, result.returncode)
                self.assertEqual(b"SHIM-DONE exit=64\n", result.stdout)
                self.assertEqual([], sandbox.ledger_records())

    def test_cline_reserved_flags_are_usage_errors_before_harness_start(self) -> None:
        for flag in ("--json", "-i", "--tui", "--acp", "-z", "--zen"):
            with self.subTest(flag=flag):
                sandbox = self.sandbox()
                sandbox.install_harness("cline")
                prompt = sandbox.prompt()
                result = sandbox.run("cline", [str(prompt), flag])
                self.assertEqual(64, result.returncode)
                self.assertEqual(b"SHIM-DONE exit=64\n", result.stdout)
                self.assertEqual([], sandbox.ledger_records())

    def test_kimi_prompt_mode_conflicts_are_usage_errors_before_harness_start(self) -> None:
        for flag in (
            "-y",
            "--yolo",
            "--auto",
            "-p",
            "--prompt=other",
            "--output-format=stream-json",
        ):
            with self.subTest(flag=flag):
                sandbox = self.sandbox()
                sandbox.install_harness("kimi")
                prompt = sandbox.prompt()
                result = sandbox.run("kimi", [str(prompt), flag])
                self.assertEqual(64, result.returncode)
                self.assertEqual(b"SHIM-DONE exit=64\n", result.stdout)
                self.assertEqual([], sandbox.ledger_records())
                self.assertEqual([], sandbox.captured_args())

    def test_kimi_environment_model_attribution_and_cli_precedence(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("kimi")
        prompt = sandbox.prompt()
        environment = sandbox.environment(
            KIMI_MODEL_NAME="environment-model", PITWALL_AGENTS_UNRESTRICTED="1"
        )
        result = sandbox.run("kimi", [str(prompt)], env=environment)
        self.assertEqual(0, result.returncode)
        self.assertEqual("environment-model", sandbox.ledger_records()[-1]["model"])

        explicit = sandbox.run(
            "kimi",
            [str(prompt), "--model", "explicit-model"],
            env=environment,
        )
        self.assertEqual(0, explicit.returncode)
        self.assertEqual("explicit-model", sandbox.ledger_records()[-1]["model"])

    def test_missing_harness_binary_preserves_per_shim_ledger_asymmetry(self) -> None:
        expected_events = {
            "agy": [],
            "codex": ["started", "finished"],
            "claude": [],
            "cline": [],
            "dsh": [],
            "goose": [],
            "grok": [],
            "hermes": [],
            "kimi": [],
            "muse": [],
            "pi": [],
            "qwen": [],
            "zcode": [],
            "opencode": ["finished"],
        }
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                prompt = sandbox.prompt()
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](prompt),
                    env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(127, result.returncode)
                expected_stdout = (
                    b"\nSHIM-DONE exit=127\n" if shim == "codex" else b"SHIM-DONE exit=127\n"
                )
                self.assertEqual(expected_stdout, result.stdout)
                records = sandbox.ledger_records()
                self.assertEqual(expected_events[shim], [record["event"] for record in records])
                if records and records[-1]["event"] == "finished":
                    self.assertEqual(127, records[-1]["exit"])
                    self.assertEqual("error", records[-1]["outcome"])

    def test_unreadable_prompt_preserves_started_ordering(self) -> None:
        expected_events = {
            "agy": ["finished"],
            "codex": ["started", "finished"],
            "claude": ["finished"],
            "cline": ["finished"],
            "dsh": ["finished"],
            "goose": ["finished"],
            "grok": ["finished"],
            "hermes": ["finished"],
            "kimi": ["finished"],
            "muse": ["finished"],
            "pi": ["finished"],
            "qwen": ["finished"],
            "zcode": ["finished"],
            "opencode": ["started", "finished"],
        }
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                missing = sandbox.root / "missing-prompt.md"
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](missing),
                    env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(66, result.returncode)
                self.assertEqual(b"SHIM-DONE exit=66\n", result.stdout)
                records = sandbox.ledger_records()
                self.assertEqual(expected_events[shim], [record["event"] for record in records])
                terminal = records[-1]
                self.assertEqual(66, terminal["exit"])
                self.assertEqual(0, terminal["wall_s"])
                self.assertEqual("error", terminal["outcome"])

    def test_success_records_started_shape_and_exact_sentinel_suffix(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt()
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](prompt),
                    env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(0, result.returncode)
                harness_output = b"test prompt\n" if shim == "agy" else b"fake-harness\n"
                self.assertTrue(result.stdout.endswith(harness_output + b"\nSHIM-DONE exit=0\n"))
                records = sandbox.ledger_records()
                self.assertEqual(["started", "finished"], [record["event"] for record in records])
                started = records[0]
                for absent in ("exit", "wall_s", "outcome"):
                    self.assertNotIn(absent, started)
                self.assertEqual(0, records[1]["exit"])
                self.assertEqual("ok", records[1]["outcome"])

    def test_prompt_delivery_matches_harness_contract(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt_text = "prompt beginning with --help\nsecond line\n"
                prompt = sandbox.prompt(prompt_text)
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](prompt),
                    env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(0, result.returncode)
                args = sandbox.captured_args()
                if shim in {"claude", "codex", "goose", "opencode"}:
                    self.assertEqual(prompt_text.encode(), sandbox.captured_stdin())
                    self.assertNotIn(prompt_text.rstrip("\n"), args)
                elif shim in {"kimi", "qwen", "zcode"}:
                    self.assertEqual([], sandbox.captured_stdin().splitlines())
                    self.assertEqual(prompt_text.rstrip("\n"), args[args.index("--prompt") + 1])
                elif shim == "muse":
                    self.assertEqual([], sandbox.captured_stdin().splitlines())
                    self.assertEqual(str(prompt), args[args.index("--prompt-file") + 1])
                elif shim == "grok":
                    self.assertEqual([], sandbox.captured_stdin().splitlines())
                    self.assertEqual(
                        "prompt.deliver.md", Path(args[args.index("--prompt-file") + 1]).name
                    )
                    self.assertFalse(any(prompt_text.rstrip("\n") in argument for argument in args))
                elif shim == "hermes":
                    self.assertEqual([], sandbox.captured_stdin().splitlines())
                    self.assertEqual(prompt_text.rstrip("\n"), args[args.index("-z") + 1])
                elif shim == "pi":
                    self.assertEqual([], sandbox.captured_stdin().splitlines())
                    self.assertTrue(args[-1].startswith("@"))
                    self.assertEqual("prompt.deliver.md", Path(args[-1][1:]).name)
                    self.assertFalse(any(prompt_text.rstrip("\n") in argument for argument in args))
                elif shim == "cline" or shim == "dsh":
                    self.assertEqual([], sandbox.captured_stdin().splitlines())
                    self.assertEqual(prompt_text.rstrip("\n"), args[-1])
                else:
                    self.assertEqual([], sandbox.captured_stdin().splitlines())
                    self.assertEqual(prompt_text.rstrip("\n"), args[args.index("-p") + 1])

    def test_agy_fake_harness_accepts_contract_and_echoes_prompt(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("agy")
        prompt = sandbox.prompt("Antigravity fixture prompt\n")
        result = sandbox.run(
            "agy",
            [str(prompt), "--sandbox", "--agent", "reviewer"],
            env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
        )
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        self.assertTrue(result.stdout.startswith(b"Antigravity fixture prompt\n"))
        args = sandbox.captured_args()
        self.assertEqual(["-p", "Antigravity fixture prompt"], args[-2:])
        self.assertEqual("text", args[args.index("--output-format") + 1])
        self.assertIn("--add-dir", args)
        self.assertIn("--print-timeout", args)
        self.assertIn("--model", args)
        self.assertIn("--effort", args)
        self.assertIn("--dangerously-skip-permissions", args)

    def test_agy_soft_denied_run_is_converted_to_exit_77(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("agy")
        prompt = sandbox.prompt("please run ls\n")
        denial = (
            'jetski: no output produced — a tool required the "command" permission '
            "that headless mode cannot prompt for, so it was auto-denied. Add an "
            "allow-rule under permissions.allow in settings.json.\n"
        )
        result = sandbox.run(
            "agy",
            [str(prompt)],
            env=sandbox.environment(FAKE_STDOUT="", FAKE_STDERR=denial),
        )
        self.assertEqual(77, result.returncode, result.stderr.decode(errors="replace"))
        self.assertTrue(result.stdout.rstrip().endswith(b"SHIM-DONE exit=77"), result.stdout)
        self.assertIn(b"agy-shim:", result.stderr)
        self.assertIn(b"auto-denied", result.stderr)
        terminal = sandbox.ledger_records()[-1]
        self.assertEqual(("error", 77), (terminal["outcome"], terminal["exit"]))
        # An ordinary zero-exit run with unrelated stderr is untouched.
        clean = sandbox.run(
            "agy",
            [str(sandbox.prompt("pong\n"))],
            env=sandbox.environment(FAKE_STDERR="warning: nothing\n"),
        )
        self.assertEqual(0, clean.returncode, clean.stderr.decode(errors="replace"))

    def test_hermes_http_error_on_stdout_is_converted_to_exit_77(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("hermes")
        prompt = sandbox.prompt("please answer\n")
        result = sandbox.run(
            "hermes",
            [str(prompt)],
            env=sandbox.environment(
                FAKE_STDOUT="HTTP 401: Missing Authentication header\n",
            ),
        )
        self.assertEqual(77, result.returncode, result.stderr.decode(errors="replace"))
        self.assertTrue(result.stdout.rstrip().endswith(b"SHIM-DONE exit=77"), result.stdout)
        self.assertIn(b"hermes-shim:", result.stderr)
        self.assertIn(b"did not attach credentials", result.stderr)
        self.assertIn(b"profiles sync --harness hermes", result.stderr)
        self.assertIn(b"loopback address", result.stderr)
        terminal = sandbox.ledger_records()[-1]
        self.assertEqual(("error", 77), (terminal["outcome"], terminal["exit"]))

        clean = sandbox.run(
            "hermes",
            [str(sandbox.prompt("pong\n"))],
            env=sandbox.environment(FAKE_STDOUT="normal answer\n"),
        )
        self.assertEqual(0, clean.returncode, clean.stderr.decode(errors="replace"))

    def test_stdin_prompt_source_matches_file_delivery(self) -> None:
        prompt = b"stdin prompt\nsecond line\n"
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                args = ["test-provider/test-model", "-"] if shim == "opencode" else ["-"]
                result = sandbox.run(
                    shim,
                    args,
                    input_bytes=prompt,
                    env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(0, result.returncode)
                captured_args = sandbox.captured_args()
                if shim in {"claude", "codex", "goose", "opencode"}:
                    self.assertEqual(prompt, sandbox.captured_stdin())
                elif shim in {"kimi", "qwen", "zcode"}:
                    self.assertEqual(
                        prompt.decode().rstrip("\n"),
                        captured_args[captured_args.index("--prompt") + 1],
                    )
                elif shim == "muse":
                    delivery = Path(captured_args[captured_args.index("--prompt-file") + 1])
                    self.assertEqual("prompt.deliver.md", delivery.name)
                    self.assertFalse(delivery.exists())
                    retained = sandbox.run(
                        shim,
                        ["--routing-retain-prompt", "-"],
                        input_bytes=prompt,
                    )
                    self.assertEqual(0, retained.returncode)
                    retained_args = sandbox.captured_args()
                    retained_delivery = Path(
                        retained_args[retained_args.index("--prompt-file") + 1]
                    )
                    self.assertEqual(prompt, retained_delivery.read_bytes())
                elif shim == "grok":
                    self.assertEqual(
                        "prompt.deliver.md",
                        Path(captured_args[captured_args.index("--prompt-file") + 1]).name,
                    )
                elif shim == "hermes":
                    self.assertEqual(
                        prompt.decode().rstrip("\n"), captured_args[captured_args.index("-z") + 1]
                    )
                elif shim == "pi":
                    self.assertEqual("prompt.deliver.md", Path(captured_args[-1][1:]).name)
                    self.assertTrue(captured_args[-1].startswith("@"))
                elif shim == "cline" or shim == "dsh":
                    self.assertEqual(prompt.decode().rstrip("\n"), captured_args[-1])
                else:
                    self.assertEqual(
                        prompt.decode().rstrip("\n"), captured_args[captured_args.index("-p") + 1]
                    )

    def test_effective_model_parsing_forms_are_preserved(self) -> None:
        cases = {
            "codex": [
                (["-m", "gpt-a"], "gpt-a"),
                (["--model", "gpt-b"], "gpt-b"),
                (["--model=gpt-c"], "gpt-c"),
                (["-m=gpt-d"], "gpt-d"),
                (["model=gpt-e"], "gpt-e"),
            ],
            "claude": [
                (["--model", "opus"], "opus"),
                (["--model=fable"], "fable"),
            ],
            "grok": [
                (["-m", "grok-a"], "grok-a"),
                (["--model", "grok-b"], "grok-b"),
                (["-m=grok-c"], "grok-c"),
                (["--model=grok-d"], "grok-d"),
            ],
            "kimi": [
                (["-m", "kimi-a"], "kimi-a"),
                (["--model", "kimi-b"], "kimi-b"),
                (["-m=kimi-c"], "kimi-c"),
                (["--model=kimi-d"], "kimi-d"),
            ],
            "qwen": [
                (["-m", "qwen-a"], "qwen-a"),
                (["--model", "qwen-b"], "qwen-b"),
                (["-m=qwen-c"], "qwen-c"),
                (["--model=qwen-d"], "qwen-d"),
            ],
            "hermes": [
                (["-m", "hermes-a"], "hermes-a"),
                (["--model", "hermes-b"], "hermes-b"),
                (["--model=hermes-c"], "hermes-c"),
            ],
            "agy": [
                (["--model", "gemini-3.7-flash-high"], "gemini-3.7-flash-high"),
                (["--model=gemini-3.1-pro"], "gemini-3.1-pro"),
            ],
        }
        for shim, forms in cases.items():
            for forwarded, expected in forms:
                with self.subTest(shim=shim, forwarded=forwarded):
                    sandbox = self.sandbox()
                    sandbox.install_harness(shim)
                    prompt = sandbox.prompt()
                    result = sandbox.run(
                        shim,
                        [str(prompt), *forwarded],
                        env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                    )
                    self.assertEqual(0, result.returncode)
                    self.assertEqual(expected, sandbox.ledger_records()[-1]["model"])

        sandbox = self.sandbox()
        sandbox.install_harness("opencode")
        prompt = sandbox.prompt()
        result = sandbox.run("opencode", ["custom/harness-model", str(prompt)])
        self.assertEqual(0, result.returncode)
        self.assertEqual("custom/harness-model", sandbox.ledger_records()[-1]["model"])

    def test_restricted_policy_flags_are_preserved(self) -> None:
        expectations = {
            "agy": None,
            "codex": ("--sandbox", "workspace-write"),
            "claude": None,
            "cline": None,
            "dsh": None,
            "goose": None,
            "grok": None,
            "kimi": None,
            "muse": None,
            "pi": None,
            "qwen": None,
            "zcode": ("--mode", "build"),
            "hermes": None,
            "opencode": None,
        }
        forbidden = {
            "agy": "--dangerously-skip-permissions",
            "codex": "--dangerously-bypass-approvals-and-sandbox",
            "claude": "--dangerously-skip-permissions",
            "dsh": "--patch",
            "goose": "GOOSE_MODE",
            "grok": "--always-approve",
            "kimi": "--yolo",
            "muse": "--yolo",
            "pi": "--approve",
            "qwen": "--yolo",
            "zcode": "yolo",
            "hermes": "--yolo",
            "opencode": "--dangerously-skip-permissions",
        }
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt()
                env = sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="0")
                result = sandbox.run(shim, HARNESS_ARGS[shim](prompt), env=env)
                if shim in {"kimi", "dsh"}:
                    # No documented approval flag: a restricted run is refused.
                    self.assertEqual(64, result.returncode)
                    self.assertFalse(sandbox.args_file.exists())
                    continue
                self.assertEqual(0, result.returncode)
                args = sandbox.captured_args()
                if shim == "cline":
                    self.assertEqual("false", args[args.index("--auto-approve") + 1])
                else:
                    self.assertNotIn(forbidden[shim], args)
                if expectations[shim]:
                    flag, value = expectations[shim]
                    self.assertEqual(value, args[args.index(flag) + 1])

    def test_opencode_unrestricted_help_probe_supports_both_known_flags(self) -> None:
        for advertised in ("--dangerously-skip-permissions", "--auto"):
            with self.subTest(advertised=advertised):
                sandbox = self.sandbox()
                sandbox.install_harness("opencode")
                prompt = sandbox.prompt()
                env = sandbox.environment(
                    FAKE_HELP=f"usage: opencode run {advertised}\n", PITWALL_AGENTS_UNRESTRICTED="1"
                )
                result = sandbox.run(
                    "opencode",
                    ["test-provider/test-model", str(prompt)],
                    env=env,
                )
                self.assertEqual(0, result.returncode)
                self.assertIn(advertised, sandbox.captured_args())

    def test_exit_code_and_error_outcome_propagate(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt()
                env = sandbox.environment(FAKE_EXIT="7", PITWALL_AGENTS_UNRESTRICTED="1")
                result = sandbox.run(shim, HARNESS_ARGS[shim](prompt), env=env)
                self.assertEqual(7, result.returncode)
                self.assertTrue(result.stdout.endswith(b"\nSHIM-DONE exit=7\n"))
                terminal = sandbox.ledger_records()[-1]
                self.assertEqual(7, terminal["exit"])
                self.assertEqual("error", terminal["outcome"])

    def test_signaled_child_maps_exit_and_preserves_exact_sentinel_tail(self) -> None:
        expected_exit = 128 + signal.SIGTERM
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt()
                env = sandbox.environment(
                    FAKE_SIGNAL=str(signal.SIGTERM), PITWALL_AGENTS_UNRESTRICTED="1"
                )
                result = sandbox.run(shim, HARNESS_ARGS[shim](prompt), env=env)
                self.assertEqual(expected_exit, result.returncode)
                self.assertTrue(
                    result.stdout.endswith(f"\nSHIM-DONE exit={expected_exit}\n".encode("ascii")),
                    result.stdout,
                )
                terminal = sandbox.ledger_records()[-1]
                self.assertEqual(expected_exit, terminal["exit"])
                self.assertEqual("error", terminal["outcome"])
                structured = json.loads(
                    (sandbox.run_directories()[0] / "result.json").read_text(encoding="utf-8")
                )
                self.assertEqual(signal.SIGTERM, structured["signal"])
                self.assertEqual(expected_exit, structured["exitCode"])

    def test_timeout_records_124_and_timeout_outcome(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt()
                # The 1 s supervisor timeout is the behaviour under test. The harness sleeps
                # past the hang guard, so it can never finish first however long a loaded
                # host takes to start it or to schedule the supervisor; the run bound is
                # only the hang guard.
                env = sandbox.environment(
                    FAKE_SLEEP_SECS=str(2 * HANG_GUARD_SECS),
                    PITWALL_AGENTS_TIMEOUT_SECS="1",
                    PITWALL_AGENTS_UNRESTRICTED="1",
                )
                result = sandbox.run(shim, HARNESS_ARGS[shim](prompt), env=env)
                self.assertEqual(124, result.returncode)
                terminal = sandbox.ledger_records()[-1]
                self.assertEqual(124, terminal["exit"])
                self.assertEqual("timeout", terminal["outcome"])
                self.assertTrue(terminal["supervisor_timeout"])

    def test_harness_exit_124_preserves_legacy_timeout_label_with_discriminator(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        result = sandbox.run("codex", [str(prompt)], env=sandbox.environment(FAKE_EXIT="124"))
        self.assertEqual(124, result.returncode)
        terminal = sandbox.ledger_records()[-1]
        self.assertEqual("timeout", terminal["outcome"])
        self.assertFalse(terminal["supervisor_timeout"])
        structured = json.loads(
            (sandbox.run_directories()[0] / "result.json").read_text(encoding="utf-8")
        )
        self.assertEqual("failed", structured["status"])
        self.assertEqual("error", structured["outcome"])

    def test_interrupted_run_leaves_orphaned_started_record(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        harness_pid_file = sandbox.root / "harness.pid"
        env = sandbox.environment(
            FAKE_PID_FILE=str(harness_pid_file), FAKE_SLEEP_SECS=str(2 * HANG_GUARD_SECS)
        )
        process = subprocess.Popen(
            ["/bin/bash", str(SHIMS["codex"]), str(prompt)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + HANG_GUARD_SECS
            while time.monotonic() < deadline:
                records = sandbox.ledger_records()
                if records and harness_pid_file.exists():
                    break
                time.sleep(0.02)
            else:
                self.fail(
                    "shim did not start the harness and append its ledger record before deadline"
                )
            records = sandbox.ledger_records()
            self.assertEqual(["started"], [record["event"] for record in records])
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=HANG_GUARD_SECS)
            if harness_pid_file.exists():
                harness_pid = int(harness_pid_file.read_text(encoding="ascii"))
                with contextlib.suppress(PermissionError, ProcessLookupError):
                    os.killpg(harness_pid, signal.SIGKILL)
                deadline = time.monotonic() + HANG_GUARD_SECS
                while time.monotonic() < deadline:
                    try:
                        os.killpg(harness_pid, 0)
                    except PermissionError, ProcessLookupError:
                        # The harness was started by this same user. EPERM
                        # after the kill therefore means that macOS has
                        # already retired/reused the original group identity.
                        break
                    time.sleep(0.02)
                else:
                    self.fail("interrupted fake-harness process group did not exit before cleanup")

    def test_telemetry_environment_mutations_are_preserved(self) -> None:
        codex = self.sandbox()
        codex.install_harness("codex")
        prompt = codex.prompt()
        result = codex.run(
            "codex",
            [str(prompt), "--model=gpt-telemetry"],
            env=codex.environment(OTEL_RESOURCE_ATTRIBUTES="service.name=test"),
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(
            "service.name=test,gen_ai.request.model=gpt-telemetry",
            codex.captured_env()["OTEL_RESOURCE_ATTRIBUTES"],
        )

        opencode = self.sandbox()
        opencode.install_harness("opencode")
        prompt = opencode.prompt()
        result = opencode.run(
            "opencode",
            ["harness/model", str(prompt)],
            env=opencode.environment(OPENCODE_OTLP_ENDPOINT="http://collector:4318"),
        )
        self.assertEqual(0, result.returncode)
        captured = opencode.captured_env()
        self.assertEqual("1", captured["OPENCODE_ENABLE_TELEMETRY"])
        self.assertEqual("http/protobuf", captured["OPENCODE_OTLP_PROTOCOL"])
        self.assertEqual("http://collector:4318", captured["OPENCODE_OTLP_ENDPOINT"])
        self.assertEqual("service.name=opencode", captured["OPENCODE_RESOURCE_ATTRIBUTES"])

    def test_codex_binary_override_is_additive(self) -> None:
        sandbox = self.sandbox()
        custom = sandbox.install_harness("custom-codex")
        prompt = sandbox.prompt()
        result = sandbox.run("codex", [str(prompt)], env=sandbox.environment(CODEX_BIN=str(custom)))
        self.assertEqual(0, result.returncode)
        self.assertTrue(result.stdout.endswith(b"fake-harness\n\nSHIM-DONE exit=0\n"))
        self.assertEqual("codex-default", sandbox.ledger_records()[-1]["model"])

    def test_success_creates_private_structured_run_without_retaining_prompt(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt("sensitive prompt\n")
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](prompt),
                    env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(0, result.returncode)
                run_directories = sandbox.run_directories()
                self.assertEqual(1, len(run_directories))
                run = run_directories[0]
                self.assertEqual(0o700, run.stat().st_mode & 0o777)
                self.assertFalse((run / "prompt.md").exists())
                request = json.loads((run / "request.json").read_text(encoding="utf-8"))
                terminal = json.loads((run / "result.json").read_text(encoding="utf-8"))
                events = [
                    json.loads(line)
                    for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(len("sensitive prompt\n"), request["promptSource"]["bytes"])
                self.assertEqual("succeeded", terminal["status"])
                self.assertEqual(0, terminal["exitCode"])
                self.assertEqual("dispatch.created", events[0]["event"])
                self.assertEqual("dispatch.succeeded", events[-1]["event"])
                for artifact in run.iterdir():
                    if artifact.is_file():
                        self.assertEqual(0o600, artifact.stat().st_mode & 0o777, artifact)

    def test_concurrent_dispatches_leave_parseable_ledger_lines(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        env = sandbox.environment()
        processes = [
            sandbox.popen(
                ["/bin/bash", str(SHIMS["codex"]), str(prompt), f"--model=gpt-{index}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            for index in range(8)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=HANG_GUARD_SECS)
            self.assertEqual(0, process.returncode, stderr.decode(errors="replace"))
            self.assertTrue(stdout.endswith(b"SHIM-DONE exit=0\n"))
        raw_lines = sandbox.ledger.read_text(encoding="utf-8").splitlines()
        self.assertEqual(16, len(raw_lines))
        records = [json.loads(line) for line in raw_lines]
        self.assertEqual(8, sum(record["event"] == "started" for record in records))
        self.assertEqual(8, sum(record["event"] == "finished" for record in records))


GLIMMER_ROUTES = {
    "schemaVersion": 1,
    "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
    "models": {
        "glimmer": {
            "model": "meta-models/Muse-Glimmer-30B",
            "endpoint": {"baseUrl": "http://gpu-1:8000/v1", "apiKeyEnv": "GLIMMER_API_KEY"},
        },
        "glm": {"model": "zai-coding-plan/glm-5.3", "harness": "opencode"},
    },
}


class RouteShimContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandboxes: list[ShimSandbox] = []

    def tearDown(self) -> None:
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def sandbox(self) -> ShimSandbox:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        return sandbox

    def test_usage_and_unknown_route_are_plain_64_sentinels_without_ledger(self) -> None:
        for args in ([], ["glimmer"], ["nosuch", "prompt.md"], ["@qwen", "prompt.md"]):
            with self.subTest(args=args):
                sandbox = self.sandbox()
                sandbox.write_routes(GLIMMER_ROUTES)
                result = sandbox.run_route(args)
                self.assertEqual(64, result.returncode)
                self.assertEqual(b"SHIM-DONE exit=64\n", result.stdout)
                self.assertEqual([], sandbox.ledger_records())

    def test_incomplete_configuration_is_a_78_sentinel_without_ledger(self) -> None:
        sandbox = self.sandbox()
        sandbox.write_routes(GLIMMER_ROUTES)
        sandbox.install_harness("qwen")
        prompt = sandbox.prompt()
        missing_key = sandbox.run_route(["glimmer", str(prompt)])
        self.assertEqual(78, missing_key.returncode)
        self.assertEqual(b"SHIM-DONE exit=78\n", missing_key.stdout)
        self.assertIn(b"GLIMMER_API_KEY", missing_key.stderr)
        self.assertEqual([], sandbox.ledger_records())
        unsynced = sandbox.run_route(
            ["glimmer@opencode", str(prompt)], env=sandbox.environment(GLIMMER_API_KEY="k")
        )
        self.assertEqual(78, unsynced.returncode)
        self.assertIn(b"profiles sync --harness opencode", unsynced.stderr)
        sandbox.write_routes({"schemaVersion": 1, "models": {"bad name!": {"model": "x"}}})
        invalid = sandbox.run_route(["glm", str(prompt)])
        self.assertEqual(78, invalid.returncode)
        self.assertEqual([], sandbox.ledger_records())

    def test_env_delivery_dispatch_records_route_and_injects_endpoint(self) -> None:
        sandbox = self.sandbox()
        sandbox.write_routes(GLIMMER_ROUTES)
        sandbox.install_harness("qwen")
        prompt = sandbox.prompt("hello\n")
        result = sandbox.run_route(
            ["glimmer", str(prompt)], env=sandbox.environment(GLIMMER_API_KEY="sk-test")
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(result.stdout.endswith(b"\nSHIM-DONE exit=0\n"))
        self.assertIn(
            b"route-shim: glimmer -> qwen meta-models/Muse-Glimmer-30B (endpoint gpu-1:8000)",
            result.stderr,
        )
        self.assertNotIn(b"sk-test", result.stdout + result.stderr)
        args = sandbox.captured_args()
        self.assertEqual(["-m", "meta-models/Muse-Glimmer-30B"], args[:2])
        self.assertEqual(
            {
                "OPENAI_BASE_URL": "http://gpu-1:8000/v1",
                "OPENAI_API_KEY": "sk-test",
                "QWEN_MODEL": "meta-models/Muse-Glimmer-30B",
            },
            {
                key: value
                for key, value in sandbox.captured_env().items()
                if key in {"OPENAI_BASE_URL", "OPENAI_API_KEY", "QWEN_MODEL"}
            },
        )
        records = sandbox.ledger_records()
        self.assertEqual(["started", "finished"], [record["event"] for record in records])
        self.assertTrue(
            all(
                record["shim"] == "qwen"
                and record["route"] == "glimmer"
                and record["schema_version"] == 4
                for record in records
            )
        )
        result_json = json.loads(
            (sandbox.run_directories()[0] / "result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                "spec": "glimmer",
                "name": "glimmer",
                "harness": "qwen",
                "model": "meta-models/Muse-Glimmer-30B",
                "endpointHost": "gpu-1:8000",
                "effort": None,
                "effortSource": None,
            },
            result_json["route"],
        )
        self.assertNotIn("sk-test", json.dumps(result_json))

    def test_hermes_config_sync_selects_the_materialized_harness(self) -> None:
        sandbox = self.sandbox()
        routes = json.loads(json.dumps(GLIMMER_ROUTES))
        routes["defaults"]["endpointHarness"] = "hermes"
        sandbox.write_routes(routes)
        sandbox.install_harness("hermes")
        config = sandbox.home / ".hermes" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "providers:\n"
            "  # managed by pitwall\n"
            "  glimmer:\n"
            '    base_url: "http://gpu-1:8000/v1"\n'
            "    api_mode: chat_completions\n"
            "    key_env: GLIMMER_API_KEY\n",
            encoding="utf-8",
        )
        prompt = sandbox.prompt("hello\n")
        result = sandbox.run_route(
            ["glimmer@hermes", str(prompt)],
            env=sandbox.environment(GLIMMER_API_KEY="sk-test"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        args = sandbox.captured_args()
        # the route name selects the materialized harness, not a fixed "custom"
        self.assertIn("--provider", args)
        self.assertEqual("glimmer", args[args.index("--provider") + 1])
        self.assertEqual(["-m", "meta-models/Muse-Glimmer-30B"], args[:2])
        # the secret is never written into the harness config; only its name is
        self.assertNotIn("sk-test", config.read_text(encoding="utf-8"))

    def test_hermes_refuses_dispatch_until_the_endpoint_is_materialized(self) -> None:
        sandbox = self.sandbox()
        routes = json.loads(json.dumps(GLIMMER_ROUTES))
        routes["defaults"]["endpointHarness"] = "hermes"
        sandbox.write_routes(routes)
        sandbox.install_harness("hermes")
        prompt = sandbox.prompt("hello\n")
        result = sandbox.run_route(
            ["glimmer@hermes", str(prompt)],
            env=sandbox.environment(GLIMMER_API_KEY="sk-test"),
        )
        self.assertEqual(78, result.returncode, result.stderr)
        self.assertIn(b"profiles sync --harness hermes", result.stderr)

    def test_goose_env_delivery_splits_openai_host_and_base_path(self) -> None:
        sandbox = self.sandbox()
        routes = json.loads(json.dumps(GLIMMER_ROUTES))
        routes["defaults"]["endpointHarness"] = "goose"
        sandbox.write_routes(routes)
        sandbox.install_harness("goose")
        prompt = sandbox.prompt("hello\\n")
        result = sandbox.run_route(
            ["glimmer@goose", str(prompt)],
            env=sandbox.environment(GLIMMER_API_KEY="sk-test"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"hello\\n", sandbox.captured_stdin())
        captured_env = sandbox.captured_env()
        self.assertEqual("http://gpu-1:8000", captured_env["OPENAI_HOST"])
        self.assertEqual("v1/chat/completions", captured_env["OPENAI_BASE_PATH"])
        self.assertEqual("openai", captured_env["GOOSE_PROVIDER"])
        self.assertEqual("meta-models/Muse-Glimmer-30B", captured_env["GOOSE_MODEL"])

    def test_config_sync_dispatch_uses_the_route_name_as_opencode_harness(self) -> None:
        sandbox = self.sandbox()
        sandbox.write_routes(GLIMMER_ROUTES)
        sandbox.install_harness("opencode")
        config = sandbox.config / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "provider": {
                        "glimmer": {
                            "npm": "@ai-sdk/openai-compatible",
                            "name": "pitwall: glimmer",
                            "options": {
                                "baseURL": "http://gpu-1:8000/v1",
                                "apiKey": "{env:GLIMMER_API_KEY}",
                            },
                            "models": {
                                "meta-models/Muse-Glimmer-30B": {
                                    "name": "meta-models/Muse-Glimmer-30B"
                                }
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        prompt = sandbox.prompt()
        env = sandbox.environment(GLIMMER_API_KEY="k", FAKE_HELP="--dangerously-skip-permissions")
        result = sandbox.run_route(["glimmer@opencode", str(prompt)], env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["run", "-m", "glimmer/meta-models/Muse-Glimmer-30B"], sandbox.captured_args()[:3]
        )
        self.assertEqual("glimmer@opencode", sandbox.ledger_records()[-1]["route"])

    def test_registry_model_and_routing_flags_pass_through(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        result = sandbox.run_route(["gpt-5.6-sol", str(prompt), "--routing-retain-prompt"])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("-m", sandbox.captured_args())
        self.assertIn("gpt-5.6-sol", sandbox.captured_args())
        self.assertNotIn("--routing-retain-prompt", sandbox.captured_args())
        self.assertEqual("gpt-5.6-sol", sandbox.ledger_records()[-1]["route"])
        self.assertTrue((sandbox.run_directories()[0] / "prompt.md").is_file())


if __name__ == "__main__":
    unittest.main()
