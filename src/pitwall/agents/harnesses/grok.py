"""Grok Build CLI adapter."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pitwall.agents.errors import UsageError
from pitwall.agents.token_usage import ParsedOutput, TokenUsage, as_int, json_document

from .base import HarnessAdapter, ParsedRequest, PreparedCommand


class GrokAdapter(HarnessAdapter):
    harness_id = "grok"
    output_mode = "json"
    effort_values: tuple[str, ...] | None = (
        "low",
        "medium",
        "high",
        "xhigh",
    )
    # `grok --help`: `--prompt-file <PATH>  Single-turn prompt from a file`. The
    # prompt goes in a private file so it never appears in argv.
    prompt_delivery = "file"
    private_prompt_file = True
    binary_override_env = "GROK_BIN"

    def parse_output(self, raw: bytes) -> ParsedOutput:
        document = json_document(raw)
        if not isinstance(document, dict):
            return ParsedOutput(b"", None)
        text = document.get("text")
        answer = (text + "\n").encode() if isinstance(text, str) and text else b""
        data = document.get("usage")
        if not isinstance(data, dict):
            return ParsedOutput(answer, None)
        inp, out = as_int(data.get("input_tokens")), as_int(data.get("output_tokens"))
        if inp is None or out is None:
            return ParsedOutput(answer, None)
        cost = document.get("total_cost_usd")
        return ParsedOutput(
            answer,
            TokenUsage(
                inp,
                out,
                cache_read_tokens=as_int(data.get("cache_read_input_tokens")),
                cache_write_tokens=as_int(data.get("cache_creation_input_tokens")),
                reasoning_tokens=as_int(data.get("reasoning_tokens")),
                total_tokens=as_int(data.get("total_tokens")),
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
        return "grok-shim: usage: grok-shim.sh <prompt-source> [extra grok args]"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument.split("=", 1)[0] == "--output-format":
                raise UsageError("grok-shim: --output-format is managed by the shim")
        model, has_model = self.parse_model_flag(argv[1:], "grok-4.7")
        return ParsedRequest(argv[0], model, argv[1:], has_model)

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        return env.get("GROK_BIN") or self.which("grok", env)

    def missing_binary_message(self) -> str:
        return "grok-shim: Grok Build CLI not found (install from https://docs.x.ai/build/overview)"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        args = ["--no-auto-update", "--no-alt-screen"]
        if self.unrestricted(env):
            args.append("--always-approve")
        if not request.has_model_override:
            args.extend(["-m", request.model])
        args.extend(request.extra_args)
        args.extend(["--output-format", "json", "--prompt-file", preflight_data["promptFile"]])
        return PreparedCommand([binary, *args], dict(env), None, self.sanitize_args(args))
