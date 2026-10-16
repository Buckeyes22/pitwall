"""Codex CLI adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from pitwall.agents.errors import UsageError
from pitwall.agents.paths import codex_home
from pitwall.agents.token_usage import ParsedOutput, TokenUsage, as_int, json_lines

from .base import HarnessAdapter, ParsedRequest, PreparedCommand


class CodexAdapter(HarnessAdapter):
    harness_id = "codex"
    output_mode = "json"
    effort_values: tuple[str, ...] | None = (
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    prompt_delivery = "stdin"
    binary_override_env = "CODEX_BIN"
    preflight_binary = False
    start_ledger_before_prompt = True

    def parse_output(self, raw: bytes) -> ParsedOutput:
        usage: TokenUsage | None = None
        text = ""
        pending = False
        for event in json_lines(raw):
            kind = event.get("type")
            if kind == "turn.started":
                pending = True
            elif kind == "item.completed":
                item = event.get("item")
                if (
                    isinstance(item, dict)
                    and item.get("type") == "agent_message"
                    and isinstance(item.get("text"), str)
                ):
                    text = item["text"]
            elif kind == "turn.completed":
                pending = False
                data = event.get("usage")
                if not isinstance(data, dict):
                    continue
                inp, out = as_int(data.get("input_tokens")), as_int(data.get("output_tokens"))
                if inp is None or out is None:
                    continue
                cached = as_int(data.get("cached_input_tokens"))
                if cached is not None and cached > inp:
                    continue
                turn = TokenUsage(
                    inp - (cached or 0),
                    out,
                    cache_read_tokens=cached,
                    cache_write_tokens=as_int(data.get("cache_write_input_tokens")),
                    reasoning_tokens=as_int(data.get("reasoning_output_tokens")),
                    total_tokens=inp + out,
                )
                usage = usage + turn if usage is not None else turn
        if usage is not None and pending:
            usage = replace(usage, complete=False)
        return ParsedOutput((text + "\n").encode() if text else b"", usage)

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "-m", model]
        if effort is not None:
            args.extend(["-c", f"model_reasoning_effort={effort}"])
        return args

    def usage(self) -> str:
        return "codex-shim: usage: codex-shim.sh <prompt-source> [extra codex-exec args]"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument.split("=", 1)[0] == "--json":
                raise UsageError("codex-shim: --json is managed by the shim")
        model = "codex-default"
        config = codex_home(env, home) / "config.toml"
        try:
            for line in config.read_text(encoding="utf-8").splitlines():
                match = re.match(r'^model\s*=\s*"(.*)"', line)
                if match:
                    model = match.group(1)
                    break
        except OSError:
            pass
        model, has_model = self.parse_model_flag(argv[1:], model)
        for argument in argv[1:]:
            if argument.startswith("model="):
                model, has_model = argument.removeprefix("model="), True
        return ParsedRequest(
            source=argv[0], model=model, extra_args=argv[1:], has_model_override=has_model
        )

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        return env.get("CODEX_BIN") or "codex"

    def missing_binary_message(self) -> str:
        return "codex-shim: codex CLI not found"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        child_env = dict(env)
        attributes = child_env.get("OTEL_RESOURCE_ATTRIBUTES", "")
        model_attribute = f"gen_ai.request.model={request.model}"
        child_env["OTEL_RESOURCE_ATTRIBUTES"] = (
            f"{attributes},{model_attribute}" if attributes else model_attribute
        )
        args = ["exec", "--json", "--skip-git-repo-check"]
        if self.unrestricted(env):
            args.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            args.extend(["--sandbox", "workspace-write"])
        args.extend(request.extra_args)
        return PreparedCommand(
            argv=[binary, *args],
            env=child_env,
            stdin=prompt,
            sanitized_args=self.sanitize_args(args),
        )
