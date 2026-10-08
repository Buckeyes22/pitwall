"""Cline CLI 2.0 adapter (cline/cline apps/cli)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pitwall.agents.errors import ProfileSyncError, UsageError

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

_RESERVED_FLAGS = ("--json", "-i", "--tui", "--acp", "-z", "--zen")


class ClineAdapter(HarnessAdapter):
    harness_id = "cline"
    effort_values: tuple[str, ...] | None = (
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
    )
    prompt_delivery = "argv"
    binary_override_env = "CLINE_BIN"
    endpoint_delivery = "config-sync"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "--model", model]
        if effort is not None:
            args.extend(["--thinking", effort])
        return args

    def usage(self) -> str:
        return "cline-shim: usage: cline-shim.sh <prompt-source> [extra cline args]"

    @staticmethod
    def settings_dir(env: Mapping[str, str], home: Path) -> Path:
        return Path(env.get("CLINE_DIR", str(home / ".cline"))).expanduser() / "data" / "settings"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument in _RESERVED_FLAGS:
                raise UsageError(f"cline-shim: {argument} is managed by the shim")
        model, has_model = self.parse_model_flag(argv[1:], "cline-default")
        return ParsedRequest(argv[0], model, argv[1:], has_model)

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        del home
        return env.get("CLINE_BIN") or self.which("cline", env)

    def missing_binary_message(self) -> str:
        return "cline-shim: Cline CLI not found (npm install -g cline)"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        del preflight_data
        args = [
            *request.extra_args,
            "--auto-approve",
            "true" if self.unrestricted(env) else "false",
            self.argv_prompt_text(prompt),
        ]
        return PreparedCommand(
            [binary, *args], dict(env), None, self.sanitize_args(args[:-1] + ["<prompt>"])
        )

    def endpoint_argv(self, name: str, entry: Mapping[str, Any]) -> list[str]:
        del name, entry
        return ["-P", "openai-compatible"]

    def endpoint_sync_status(
        self, name: str, entry: Mapping[str, Any], env: Mapping[str, str], home: Path
    ) -> str:
        path = self.settings_dir(env, home) / "providers.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return "missing"
        text = json.dumps(data)
        # Cline's providers.json layout is not documented; treat "the base URL and model id both appear" as synced.
        if str(entry["endpoint"]["baseUrl"]) in text and str(entry["model"]) in text:
            return "synced"
        return "stale" if "openai-compatible" in text else "missing"

    @staticmethod
    def _cline_routes(entries: Mapping[str, Mapping[str, Any]]) -> list[str]:
        return sorted(name for name, entry in entries.items() if entry.get("harness") == "cline")

    def endpoint_sync_commands(
        self, entries: Mapping[str, Mapping[str, Any]], env: Mapping[str, str], home: Path
    ) -> list[list[str]]:
        targets = self._cline_routes(entries)
        if not targets:
            return []
        if len(targets) > 1:
            raise ProfileSyncError(
                f"Cline holds one custom endpoint at a time; routes {', '.join(targets)} resolve to harness cline"
            )
        name = targets[0]
        entry = entries[name]
        if self.endpoint_sync_status(name, entry, env, home) == "synced":
            return []
        binary = self.resolve_binary(env, home) or "cline"
        command = [
            binary,
            "auth",
            "--provider",
            "openai-compatible",
            "--baseurl",
            str(entry["endpoint"]["baseUrl"]),
            "--modelid",
            str(entry["model"]),
        ]
        key_env = entry["endpoint"].get("apiKeyEnv")
        if key_env:
            command.extend(["--apikey", f"{{env:{key_env}}}"])
        return [command]
