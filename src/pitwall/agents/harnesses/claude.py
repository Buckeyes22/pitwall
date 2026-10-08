"""Claude Code CLI adapter."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .base import HarnessAdapter, ParsedRequest, PreparedCommand


class ClaudeAdapter(HarnessAdapter):
    harness_id = "claude"
    effort_values: tuple[str, ...] | None = (
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    prompt_delivery = "stdin"
    binary_override_env = "CLAUDE_BIN"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "--model", model]
        if effort is not None:
            args.extend(["--effort", effort])
        return args

    def usage(self) -> str:
        return "claude-shim: usage: claude-shim.sh <prompt-source> [extra claude args]"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        model, has_model = self.parse_model_flag(argv[1:], "sonnet")
        return ParsedRequest(argv[0], model, argv[1:], has_model)

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        return env.get("CLAUDE_BIN") or self.which("claude", env)

    def missing_binary_message(self) -> str:
        return "claude-shim: Claude Code CLI not found (see https://code.claude.com/docs/en/cli-reference)"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        args = ["-p", "--no-session-persistence"]
        if self.unrestricted(env):
            args.append("--dangerously-skip-permissions")
        if not request.has_model_override:
            args.extend(["--model", request.model])
        args.extend(request.extra_args)
        args.extend(["--output-format", "text"])
        # `claude -p` reads the prompt from standard input, so a prompt is not bounded by
        # the 128 KiB single-argument limit (nine Opus dispatches failed on it on 2026-10-05).
        return PreparedCommand([binary, *args], dict(env), prompt, self.sanitize_args(args))
