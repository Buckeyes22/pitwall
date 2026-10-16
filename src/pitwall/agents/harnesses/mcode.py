"""MiniMax Code CLI (`mcode exec`) using the operator's MiniMax login."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from pitwall.agents.errors import UsageError
from pitwall.agents.registry import load_registry
from pitwall.agents.token_usage import ParsedOutput, TokenUsage, as_int, json_document

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

DEFAULT_MODEL = "minimax/MiniMax-M3.1-Flash-Preview"
# mcode's own rule for --model (verified: anything else exits 2).
_MODEL_FORM = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:#[A-Za-z0-9_-]+)?")
_MODEL_FLAGS = ("-m", "--model")
_RESERVED_FLAGS = {
    "--input",
    "--input-format",
    "--cwd",
    "--permission",
    "--output-format",
    "--session",
    "--continue",
    "-o",
    "--output-last-message",
    "--config",
    "--diagnostics-dir",
    "--output-schema",
    "-h",
    "--help",
    "review",
}
# Under --permission smart, mcode declines a risky command, keeps exit 0, and the
# refusal reaches only the answer text (verified on 0.6.12).
_SOFT_DENIAL_MARKER = b"HOST_CAPABILITY_UNAVAILABLE"


def _without_model_flags(arguments: list[str]) -> list[str]:
    kept: list[str] = []
    skip = False
    for argument in arguments:
        if skip:
            skip = False
            continue
        if argument in _MODEL_FLAGS:
            skip = True
            continue
        if any(argument.startswith(f"{flag}=") for flag in _MODEL_FLAGS):
            continue
        kept.append(argument)
    return kept


class MCodeAdapter(HarnessAdapter):
    harness_id = "mcode"
    DEFAULT_MODEL = DEFAULT_MODEL
    output_mode = "json"
    effort_values: tuple[str, ...] | None = ("low", "medium", "high", "xhigh", "max")
    model_bound = True
    prompt_delivery = "stdin"
    binary_override_env = "MCODE_BIN"
    endpoint_delivery = "none"

    def parse_output(self, raw: bytes) -> ParsedOutput:
        document = json_document(raw)
        if not isinstance(document, dict):
            return ParsedOutput(b"", None)
        text = document.get("output")
        answer = (text + "\n").encode() if isinstance(text, str) and text else b""
        data = document.get("usage")
        if not isinstance(data, dict):
            return ParsedOutput(answer, None)
        inp, out = as_int(data.get("inputTokens")), as_int(data.get("outputTokens"))
        if inp is None or out is None:
            return ParsedOutput(answer, None)
        return ParsedOutput(
            answer,
            TokenUsage(
                inp,
                out,
                cache_read_tokens=as_int(data.get("cacheReadTokens")),
                total_tokens=as_int(data.get("totalTokens")),
                complete=document.get("status") == "succeeded",
            ),
        )

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "--model", model]
        if effort is not None:
            args.extend(["--effort", effort])
        return args

    def usage(self) -> str:
        return (
            "mcode-shim: usage: mcode-shim.sh <prompt-source> "
            "[--model provider/model[#variant]] [--effort level] [extra mcode exec args]"
        )

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        del env, home
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            flag = argument.split("=", 1)[0]
            if flag in _RESERVED_FLAGS:
                raise UsageError(f"mcode-shim: {flag} is managed by the shim")
        model, has_model = self.parse_model_flag(argv[1:], DEFAULT_MODEL)
        if _MODEL_FORM.fullmatch(model) is None:
            raise UsageError(
                f"mcode-shim: --model must be provider/model or provider/model#variant, got {model!r}"
            )
        base = model.split("#", 1)[0]
        known = load_registry()["harnesses"]["mcode"]["models"]
        if base.startswith("minimax/") and base not in known:
            # mcode accepts an unknown minimax/ id and answers anyway; refuse it here.
            raise UsageError(
                f"mcode-shim: {base} is not a registered MiniMax Code model "
                f"({', '.join(sorted(known))})"
            )
        return ParsedRequest(argv[0], model, _without_model_flags(argv[1:]), has_model)

    def detect_soft_denial(self, exit_code: int, stderr_tail: bytes) -> str | None:
        if exit_code == 0 and _SOFT_DENIAL_MARKER in stderr_tail:
            return (
                "MiniMax Code declined a command it could not ask permission for "
                "(HOST_CAPABILITY_UNAVAILABLE) and exited 0; reporting exit 77. Re-run "
                "unrestricted (set PITWALL_AGENTS_UNRESTRICTED=1) to use --permission full."
            )
        return None

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        if env.get("MCODE_BIN"):
            return env["MCODE_BIN"]
        found = self.which("mcode", env)
        if found:
            return found
        fallback = home / ".minimax-code" / "bin" / "mcode"
        return str(fallback) if fallback.is_file() and os.access(fallback, os.X_OK) else None

    def missing_binary_message(self) -> str:
        return "mcode-shim: MiniMax Code CLI not found; see docs/agents/mcode.md"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        args = [
            "exec",
            "--input",
            "-",
            "--cwd",
            preflight_data.get("workspacePath", os.getcwd()),
            "--permission",
            "full" if self.unrestricted(env) else "smart",
            "--output-format",
            "json",
            "--model",
            request.model,
            *request.extra_args,
        ]
        return PreparedCommand([binary, *args], dict(env), prompt, self.sanitize_args(args))
