"""Google Antigravity CLI harness adapter."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from pitwall.agents.errors import UsageError

from .base import HarnessAdapter, ParsedRequest, PreparedCommand, parse_duration_seconds

_RESERVED_FLAGS = (
    "-p",
    "--print",
    "--prompt",
    "-i",
    "--prompt-interactive",
    "--output-format",
    "--input-format",
    "--json-schema",
    "--print-timeout",
    "--dangerously-skip-permissions",
    "-c",
    "--continue",
    "--conversation",
)
_EFFORT_SUFFIX = re.compile(r"-(low|medium|high)$")
# Antigravity's headless mode auto-denies un-permitted tools, keeps exit 0, and
# prints exactly one diagnostic beginning with this marker (verified live on 1.1.22).
_SOFT_DENIAL_MARKER = b"jetski: no output produced"


class AgyAdapter(HarnessAdapter):
    harness_id = "agy"
    effort_values: tuple[str, ...] | None = (
        "low",
        "medium",
        "high",
    )
    model_bound = True
    prompt_delivery = "argv"
    binary_override_env = "AGY_BIN"
    endpoint_delivery = "none"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "--model", model]
        if effort is not None:
            args.extend(["--effort", effort])
        return args

    def usage(self) -> str:
        return "agy-shim: usage: agy-shim.sh <prompt-source> [extra agy args]"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        del env, home
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            flag = argument.split("=", 1)[0]
            if flag in _RESERVED_FLAGS:
                raise UsageError(f"agy-shim: {flag} is managed by the shim")

        model, has_model = self.parse_model_flag(argv[1:], "gemini-3.8-flash")
        effort: str | None = None
        previous = ""
        for argument in argv[1:]:
            if previous == "--effort":
                effort = argument
            elif argument.startswith("--effort="):
                effort = argument.removeprefix("--effort=")
            previous = argument

        suffix = _EFFORT_SUFFIX.search(model)
        if suffix is not None and effort is not None:
            raise UsageError(
                "agy-shim: --model <slug> already carries an effort suffix; drop --effort"
            )
        effective_effort = effort or (suffix.group(1) if suffix is not None else "medium")
        return ParsedRequest(
            argv[0],
            model,
            argv[1:],
            has_model,
            {"effort": effective_effort},
        )

    def detect_soft_denial(self, exit_code: int, stderr_tail: bytes) -> str | None:
        if exit_code == 0 and _SOFT_DENIAL_MARKER in stderr_tail:
            return (
                "Antigravity auto-denied a tool permission in headless mode and produced no "
                "output; reporting exit 77 instead of the CLI's exit 0. Re-run unrestricted "
                "(set PITWALL_AGENTS_UNRESTRICTED=1) or add a permissions.allow "
                "rule in ~/.gemini/antigravity-cli/settings.json."
            )
        return None

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        if env.get("AGY_BIN"):
            return env["AGY_BIN"]
        discovered = self.which("agy", env)
        if discovered:
            return discovered
        fallback = home / ".local" / "bin" / "agy"
        return str(fallback) if os.access(fallback, os.X_OK) and fallback.is_file() else None

    def missing_binary_message(self) -> str:
        return (
            "agy-shim: Antigravity CLI not found (see https://antigravity.google/docs/cli/install/)"
        )

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        args: list[str] = []
        if not request.has_model_override:
            args.extend(["--model", request.model])
        has_effort = any(
            argument == "--effort" or argument.startswith("--effort=")
            for argument in request.extra_args
        )
        if _EFFORT_SUFFIX.search(request.model) is None and not has_effort:
            args.extend(["--effort", "medium"])
        args.extend(["--add-dir", preflight_data.get("workspacePath", os.getcwd())])
        if self.unrestricted(env):
            args.append("--dangerously-skip-permissions")
        args.extend(request.extra_args)
        try:
            print_timeout = (
                int(parse_duration_seconds(env.get("PITWALL_AGENTS_TIMEOUT_SECS", "1140"))) + 60
            )
        except ValueError:
            print_timeout = 1200
        args.extend(
            [
                "--print-timeout",
                f"{print_timeout}s",
                "--output-format",
                "text",
                "-p",
                self.argv_prompt_text(prompt),
            ]
        )
        return PreparedCommand(
            [binary, *args],
            dict(env),
            None,
            self.sanitize_args([*args[:-1], "<prompt>"]),
        )
