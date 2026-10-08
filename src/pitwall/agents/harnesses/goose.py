"""goose CLI adapter (aaif-goose/goose)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pitwall.agents.errors import UsageError

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

_RESERVED_FLAGS = (
    "--instructions",
    "-t",
    "--text",
    "--output-format",
    "-s",
    "--interactive",
    "-q",
    "--quiet",
    "--no-session",
)
_CONFIG_MODEL_RE = re.compile(r"^GOOSE_MODEL:\s*['\"]?([^'\"\n#]+)", re.M)


class GooseAdapter(HarnessAdapter):
    harness_id = "goose"
    effort_values: tuple[str, ...] | None = None
    prompt_delivery = "stdin"
    binary_override_env = "GOOSE_BIN"
    endpoint_delivery = "env"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        del effort  # no effort control; workflow validation rejects one
        return [str(prompt_path), "--model", model]

    def usage(self) -> str:
        return "goose-shim: usage: goose-shim.sh <prompt-source> [extra goose run args]"

    @staticmethod
    def _configured_model(env: Mapping[str, str], home: Path) -> str:
        configured = env.get("GOOSE_MODEL")
        if configured:
            return configured
        base = Path(env.get("XDG_CONFIG_HOME", str(home / ".config"))).expanduser()
        try:
            text = (base / "goose" / "config.yaml").read_text(encoding="utf-8")
        except OSError:
            return "goose-default"
        match = _CONFIG_MODEL_RE.search(text)
        return match.group(1).strip() if match else "goose-default"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument in _RESERVED_FLAGS or any(
                argument.startswith(f"{flag}=")
                for flag in ("--instructions", "--text", "--output-format")
            ):
                raise UsageError(f"goose-shim: {argument.split('=', 1)[0]} is managed by the shim")
        model, has_model = self.parse_model_flag(argv[1:], self._configured_model(env, home))
        return ParsedRequest(argv[0], model, argv[1:], has_model)

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        del home
        return env.get("GOOSE_BIN") or self.which("goose", env)

    def missing_binary_message(self) -> str:
        return "goose-shim: goose not found (see https://goose-docs.ai/docs/getting-started/installation/)"

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
        if self.unrestricted(env):
            child_env["GOOSE_MODE"] = "auto"
        args = [
            "run",
            "--no-session",
            "-q",
            "--output-format",
            "text",
            *request.extra_args,
            "--instructions",
            "-",
        ]
        return PreparedCommand([binary, *args], child_env, prompt, self.sanitize_args(args))

    def endpoint_environment_extras(
        self, entry: Mapping[str, Any], env_updates: Mapping[str, str]
    ) -> dict[str, str]:
        del entry
        parsed = urlsplit(env_updates["OPENAI_HOST"])
        path = parsed.path.strip("/")
        return {
            "OPENAI_HOST": f"{parsed.scheme}://{parsed.netloc}",
            "OPENAI_BASE_PATH": f"{path}/chat/completions" if path else "v1/chat/completions",
            "GOOSE_PROVIDER": "openai",
        }
