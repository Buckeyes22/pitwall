"""Qwen Code CLI harness adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from pitwall.agents.errors import UsageError
from pitwall.agents.token_usage import ParsedOutput, TokenUsage, as_int, json_document

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

_PROMPT_MODE_RESERVED_FLAGS = ("-p", "--prompt", "--output-format")
_PROMPT_MODE_INCOMPATIBLE_FLAGS = ("-y", "--yolo", "--approval-mode")


class QwenAdapter(HarnessAdapter):
    harness_id = "qwen"
    output_mode = "json"
    effort_values: tuple[str, ...] | None = None
    prompt_delivery = "argv"
    binary_override_env = "QWEN_BIN"
    endpoint_delivery = "env"

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
        del effort  # no effort control; workflow validation rejects one
        return [str(prompt_path), "--model", model]

    def usage(self) -> str:
        return "qwen-shim: usage: qwen-shim.sh <prompt-source> [extra qwen args]"

    @staticmethod
    def _configured_model(env: Mapping[str, str], home: Path) -> str:
        environment_model = env.get("QWEN_MODEL")
        if environment_model:
            return environment_model
        root = Path(env.get("QWEN_CODE_HOME", str(home / ".qwen"))).expanduser()
        try:
            data = json.loads((root / "settings.json").read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            data = {}
        model = data.get("model")
        if isinstance(model, dict):
            model = model.get("name")
        if isinstance(model, str) and model:
            return model
        try:
            dotenv_lines = (root / ".env").read_text(encoding="utf-8").splitlines()
        except OSError:
            return "qwen-default"
        dotenv = {}
        for line in dotenv_lines:
            key, separator, value = line.partition("=")
            if separator:
                dotenv[key.strip()] = value.strip().strip("'\"")
        model = dotenv.get("QWEN_MODEL") or dotenv.get("OPENAI_MODEL")
        return model if isinstance(model, str) and model else "qwen-default"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument in _PROMPT_MODE_INCOMPATIBLE_FLAGS or any(
                argument.startswith(f"{flag}=") for flag in _PROMPT_MODE_INCOMPATIBLE_FLAGS
            ):
                raise UsageError(f"qwen-shim: {argument.split('=', 1)[0]} is managed by the shim")
            if argument in _PROMPT_MODE_RESERVED_FLAGS or any(
                argument.startswith(f"{flag}=") for flag in _PROMPT_MODE_RESERVED_FLAGS
            ):
                raise UsageError(f"qwen-shim: {argument.split('=', 1)[0]} is managed by the shim")
        model, has_model = self.parse_model_flag(argv[1:], self._configured_model(env, home))
        return ParsedRequest(argv[0], model, argv[1:], has_model)

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        if env.get("QWEN_BIN"):
            return env["QWEN_BIN"]
        discovered = self.which("qwen", env)
        if discovered:
            return discovered
        # The standalone installer writes its wrapper to ~/.local/bin/qwen (also npm's
        # global bin under a user prefix); cover shells whose PATH omits that directory.
        fallback = home / ".local" / "bin" / "qwen"
        return str(fallback) if os.access(fallback, os.X_OK) and fallback.is_file() else None

    def missing_binary_message(self) -> str:
        return "qwen-shim: Qwen Code CLI not found (see https://github.com/QwenLM/qwen-code)"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        del preflight_data
        child_env = dict(env)
        args = [*request.extra_args]
        if self.unrestricted(env):
            args.append("--yolo")
        args.extend(["--output-format", "json", "--prompt", self.argv_prompt_text(prompt)])
        return PreparedCommand(
            [binary, *args],
            child_env,
            None,
            self.sanitize_args(args[:-1] + ["<prompt>"]),
        )
