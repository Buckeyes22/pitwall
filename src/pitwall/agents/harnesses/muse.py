"""Muse Code CLI adapter (Meta)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pitwall.agents.errors import UsageError

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

_RESERVED_FLAGS = ("--json", "--prompt-file", "--session-id")


class MuseAdapter(HarnessAdapter):
    harness_id = "muse"
    effort_values: tuple[str, ...] | None = (
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    )
    model_bound = True
    prompt_delivery = "file"
    binary_override_env = "MUSE_BIN"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "--model", model]
        if effort is not None:
            args.extend(["--reasoning-effort", effort])
        return args

    def usage(self) -> str:
        return "muse-shim: usage: muse-shim.sh <prompt-source> [extra muse exec args]"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument in _RESERVED_FLAGS or any(
                argument.startswith(f"{flag}=") for flag in _RESERVED_FLAGS
            ):
                raise UsageError(f"muse-shim: {argument.split('=', 1)[0]} is managed by the shim")
        model, has_model = self.parse_model_flag(argv[1:], "muse-spark-1.3")
        return ParsedRequest(argv[0], model, argv[1:], has_model)

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        del home
        return env.get("MUSE_BIN") or self.which("muse", env)

    def missing_binary_message(self) -> str:
        return "muse-shim: Muse Code not found (curl -fsSL https://dev.meta.ai/install.sh | sh)"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        del prompt
        args = ["exec"]
        if self.unrestricted(env):
            args.append("--yolo")
        if not request.has_model_override:
            args.extend(["--model", request.model])
        args.extend(request.extra_args)
        args.extend(["--prompt-file", preflight_data["promptFile"]])
        return PreparedCommand([binary, *args], dict(env), None, self.sanitize_args(args))
