"""ZCode CLI using the operator's saved model and account configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pitwall.agents.errors import UsageError

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

# ZCode has no per-invocation model selector. This labels its saved selection;
# it is deliberately not a claim about the model served by the account.
DEFAULT_MODEL = "zcode-default"
_RESERVED_FLAGS = {
    "-p",
    "--prompt",
    "--target",
    "--target-replace",
    "-c",
    "--continue",
    "--resume",
    "--cwd",
    "--mode",
    "--json",
    "--output-format",
    "-m",
    "--model",
    "--web",
    "--help",
    "-h",
    "--version",
    "-v",
    "--enable-workflow",
    "--effort",
    "--reasoning-effort",
    "login",
    "logout",
    "app-server",
    "agent-server",
    "tui",
    "doctor",
    "commands",
    "plugins",
    "plugin",
    "skills",
    "version",
}


class ZCodeAdapter(HarnessAdapter):
    harness_id = "zcode"
    model_bound = True
    prompt_delivery = "argv"
    binary_override_env = "ZCODE_BIN"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        if model != DEFAULT_MODEL or effort is not None:
            raise UsageError("zcode-shim: ZCode uses its saved model; no model or effort override")
        return [str(prompt_path)]

    def usage(self) -> str:
        return "zcode-shim: usage: zcode-shim.sh <prompt-source> [extra zcode args]"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        del env, home
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            flag = argument.split("=", 1)[0]
            if flag in _RESERVED_FLAGS:
                raise UsageError(f"zcode-shim: {flag} is managed by the shim")
        return ParsedRequest(argv[0], DEFAULT_MODEL, argv[1:])

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        if env.get("ZCODE_BIN"):
            return env["ZCODE_BIN"]
        found = self.which("zcode", env)
        if found:
            return found
        fallback = home / ".local" / "bin" / "zcode"
        return str(fallback) if fallback.is_file() and os.access(fallback, os.X_OK) else None

    def missing_binary_message(self) -> str:
        return "zcode-shim: ZCode CLI not found; see docs/agents/zcode.md"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        args = [
            "--cwd",
            preflight_data.get("workspacePath", os.getcwd()),
            "--mode",
            "yolo" if self.unrestricted(env) else "build",
            "--no-color",
            "--output-format",
            "text",
            *request.extra_args,
            "--prompt",
            self.argv_prompt_text(prompt),
        ]
        return PreparedCommand(
            [binary, *args],
            dict(env),
            None,
            self.sanitize_args([*args[:-1], "<prompt>"]),
        )
