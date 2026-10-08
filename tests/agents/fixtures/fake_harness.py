#!/usr/bin/python3
"""Deterministic fake coding-agent CLI used by shim contract tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _write_bytes(env_name: str, data: bytes) -> None:
    target = os.environ.get(env_name)
    if target:
        Path(target).write_bytes(data)


def _agy_prompt(args: list[str]) -> str | None:
    if len(args) < 2 or args[-2] != "-p":
        return None
    required_value_flags = ("--output-format", "--add-dir", "--print-timeout")
    for flag in required_value_flags:
        if flag not in args or args.index(flag) + 1 >= len(args):
            return None
    has_model = "--model" in args or "-m" in args or any(arg.startswith("--model=") for arg in args)
    if not has_model or args[args.index("--output-format") + 1] != "text":
        return None
    return args[-1]


def _write_atomic(target: Path, text: str) -> None:
    """Publish ``text`` at ``target`` so that the file existing implies it is complete."""
    staging = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    staging.write_text(text, encoding="ascii")
    os.replace(staging, target)


def main() -> int:
    import signal as signal_module

    pid_target = os.environ.get("FAKE_PID_FILE")
    if pid_target:
        _write_atomic(Path(pid_target), str(os.getpid()))

    if os.environ.get("FAKE_IGNORE_TERM") == "1":
        signal_module.signal(signal_module.SIGTERM, signal_module.SIG_IGN)

    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "run" and args[1] == "--help":
        sys.stdout.write(os.environ.get("FAKE_HELP", ""))
        return int(os.environ.get("FAKE_HELP_EXIT", "0"))

    nested = os.environ.pop("FAKE_NESTED_DISPATCH", None)
    if nested:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write("nested dispatch\n")
            nested_prompt = handle.name
        completed = subprocess.run(
            [
                os.environ.get("FAKE_NESTED_PYTHON") or sys.executable,
                nested,
                "dispatch",
                "codex",
                nested_prompt,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
        sys.stderr.buffer.write(completed.stderr)
        return completed.returncode

    _write_bytes("FAKE_ARGS_FILE", b"\0".join(arg.encode() for arg in args))
    _write_bytes("FAKE_STDIN_FILE", sys.stdin.buffer.read())

    at_report = os.environ.get("FAKE_AT_FILE_REPORT")
    if at_report:
        at_files = [arg for arg in args if arg.startswith("@")]
        report = []
        for arg in at_files:
            target = Path(arg[1:])
            report.append(
                {
                    "path": str(target),
                    "mode": target.stat().st_mode & 0o777 if target.is_file() else None,
                    "content": target.read_text(encoding="utf-8") if target.is_file() else None,
                }
            )
        Path(at_report).write_text(json.dumps(report), encoding="utf-8")

    env_target = os.environ.get("FAKE_ENV_FILE")
    if env_target:
        keys = [
            "OPENCODE_ENABLE_TELEMETRY",
            "OPENCODE_OTLP_PROTOCOL",
            "OPENCODE_OTLP_ENDPOINT",
            "OPENCODE_RESOURCE_ATTRIBUTES",
            "OTEL_RESOURCE_ATTRIBUTES",
            "KIMI_CODE_NO_AUTO_UPDATE",
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
            "QWEN_MODEL",
            "HERMES_MODEL",
            "GOOSE_MODE",
            "GOOSE_MODEL",
            "GOOSE_PROVIDER",
            "OPENAI_HOST",
            "OPENAI_BASE_PATH",
            "PITWALL_AGENTS_CHANNEL_DISPATCH_ID",
            "PITWALL_AGENTS_CHANNEL_STATE_ROOT",
            "PITWALL_AGENTS_CHANNEL_ATTEMPT",
            "MCP_TOOL_TIMEOUT",
            "PITWALL_AGENTS_DISPATCH_ID",
            "PITWALL_AGENTS_ATTEMPT",
            "PITWALL_AGENTS_WORKFLOW_ID",
            "PITWALL_AGENTS_TASK_ID",
            "PITWALL_AGENTS_MANAGED_LAUNCH",
        ]
        Path(env_target).write_text(
            json.dumps({key: os.environ.get(key) for key in keys}, sort_keys=True),
            encoding="utf-8",
        )

    sleep_seconds = float(os.environ.get("FAKE_SLEEP_SECS", "0"))
    child_sleep = float(os.environ.get("FAKE_SPAWN_CHILD_SECS", "0"))
    if child_sleep:
        child = subprocess.Popen(
            [sys.executable, "-c", f"import time; time.sleep({child_sleep!r})"]
        )
        child_pid_file = os.environ.get("FAKE_CHILD_PID_FILE")
        if child_pid_file:
            _write_atomic(Path(child_pid_file), str(child.pid))
    if sleep_seconds:
        time.sleep(sleep_seconds)

    fake_signal = os.environ.get("FAKE_SIGNAL")
    if fake_signal:
        os.kill(os.getpid(), int(fake_signal))

    relative_write = os.environ.get("FAKE_WRITE_RELATIVE")
    if relative_write:
        target = Path(relative_write)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(os.environ.get("FAKE_WRITE_CONTENT", "fake edit\n"), encoding="utf-8")

    default_stdout = "fake-harness\n"
    if Path(sys.argv[0]).name == "agy":
        agy_prompt = _agy_prompt(args)
        if agy_prompt is None:
            sys.stderr.write("invalid fake agy argv\n")
            return 2
        default_stdout = agy_prompt + "\n"
    sys.stdout.write(os.environ.get("FAKE_STDOUT", default_stdout))
    sys.stderr.write(os.environ.get("FAKE_STDERR", ""))
    return int(os.environ.get("FAKE_EXIT", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
