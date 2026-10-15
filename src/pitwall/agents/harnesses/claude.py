"""Claude Code CLI adapter."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pitwall.agents.errors import UsageError
from pitwall.agents.token_usage import ParsedOutput, TokenUsage, as_int, json_document

from .base import HarnessAdapter, ParsedRequest, PreparedCommand


class ClaudeAdapter(HarnessAdapter):
    harness_id = "claude"
    output_mode = "json"
    effort_values: tuple[str, ...] | None = (
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    prompt_delivery = "stdin"
    binary_override_env = "CLAUDE_BIN"

    def parse_output(self, raw: bytes) -> ParsedOutput:
        data = json_document(raw)
        if not isinstance(data, list):
            return ParsedOutput(b"", None)
        result = next(
            (
                item
                for item in reversed(data)
                if isinstance(item, dict) and item.get("type") == "result"
            ),
            None,
        )
        if result is None:
            return ParsedOutput(b"", None)
        text = result.get("result")
        answer = (text + "\n").encode() if isinstance(text, str) and text else b""
        data = result.get("usage")
        if not isinstance(data, dict):
            return ParsedOutput(answer, None)
        inp, out = as_int(data.get("input_tokens")), as_int(data.get("output_tokens"))
        if inp is None or out is None:
            return ParsedOutput(answer, None)
        cache_read = as_int(data.get("cache_read_input_tokens"))
        cache_write = as_int(data.get("cache_creation_input_tokens"))
        cost = result.get("total_cost_usd")
        return ParsedOutput(
            answer,
            TokenUsage(
                inp,
                out,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                total_tokens=inp + out + (cache_read or 0) + (cache_write or 0),
                cost_usd=cost
                if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0
                else None,
            ),
        )

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "--model", model]
        if effort is not None:
            args.extend(["--effort", effort])
        return args

    def usage(self) -> str:
        return "claude-shim: usage: claude-shim.sh <prompt-source> [extra claude args]"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument.split("=", 1)[0] == "--output-format":
                raise UsageError("claude-shim: --output-format is managed by the shim")
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
        args.extend(["--output-format", "json"])
        # `claude -p` reads the prompt from standard input, so a prompt is not bounded by
        # the 128 KiB single-argument limit (nine Opus dispatches failed on it on 2026-10-05).
        return PreparedCommand([binary, *args], dict(env), prompt, self.sanitize_args(args))
