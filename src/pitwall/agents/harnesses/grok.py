"""Grok Build CLI adapter."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .base import HarnessAdapter, ParsedRequest, PreparedCommand


class GrokAdapter(HarnessAdapter):
    harness_id = "grok"
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

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "--model", model]
        if effort is not None:
            args.extend(["--effort", effort])
        return args

    def usage(self) -> str:
        return "grok-shim: usage: grok-shim.sh <prompt-source> [extra grok args]"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
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
        args.extend(["--output-format", "plain", "--prompt-file", preflight_data["promptFile"]])
        return PreparedCommand([binary, *args], dict(env), None, self.sanitize_args(args))
