"""OpenCode CLI adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pitwall.agents.errors import ProfileSyncError
from pitwall.agents.paths import xdg_dir
from pitwall.agents.process import run_bounded_capture
from pitwall.providers.model_studio import catalog as model_studio

from .base import HarnessAdapter, ParsedRequest, PreparedCommand


class OpenCodeAdapter(HarnessAdapter):
    harness_id = "opencode"
    effort_values: tuple[str, ...] | None = ()
    prompt_delivery = "stdin"
    # `opencode run` prints the model's answer; an empty stdout with exit 0 is a stalled
    # free-tier model (70 such runs on 2026-09-28/29), not a result.
    empty_stdout_is_failure = True
    binary_override_env = "OPENCODE_BIN"
    missing_binary_ledger = "finished"
    start_ledger_before_prompt = True
    endpoint_delivery = "config-sync"
    model_positional = True
    MANAGED_PREFIX = "pitwall: "

    @staticmethod
    def config_path(env: Mapping[str, str], home: Path) -> Path:
        base = xdg_dir(env, "XDG_CONFIG_HOME", ".config", home=home)
        return base / "opencode" / "opencode.json"

    @staticmethod
    def expected_base_url(endpoint: Mapping[str, Any]) -> str:
        """The baseURL sync writes for ``endpoint``; status compares against the same value."""
        is_model_studio = endpoint.get("kind") == model_studio.KIND
        anthropic = is_model_studio and endpoint.get("protocol") == "anthropic"
        return str(endpoint["baseUrl"]).rstrip("/") + ("/v1" if anthropic else "")

    def endpoint_sync_status(
        self,
        name: str,
        entry: Mapping[str, Any],
        env: Mapping[str, str],
        home: Path,
    ) -> str:
        try:
            data = json.loads(self.config_path(env, home).read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return "missing"
        providers = data.get("provider") if isinstance(data, dict) else None
        block = providers.get(name) if isinstance(providers, dict) else None
        if not isinstance(block, dict):
            return "missing"
        options = block.get("options")
        if not isinstance(options, dict):
            options = {}
        models = block.get("models")
        if not isinstance(models, dict):
            models = {}
        endpoint = entry["endpoint"]
        key_env = endpoint.get("apiKeyEnv")
        expected_key = f"{{env:{key_env}}}" if key_env else None
        if (
            options.get("baseURL") != self.expected_base_url(endpoint)
            or options.get("apiKey") != expected_key
            or entry["model"] not in models
        ):
            return "stale"
        return "synced"

    def plan_endpoint_sync(
        self,
        entries: Mapping[str, Mapping[str, Any]],
        env: Mapping[str, str],
        home: Path,
    ) -> tuple[Path, str, str]:
        path = self.config_path(env, home)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        data: dict[str, Any]
        if before.strip():
            try:
                loaded = json.loads(before)
            except json.JSONDecodeError as exc:
                raise ProfileSyncError(
                    f"{path} is not strict JSON (JSONC comments?): {exc.msg} at line {exc.lineno}; "
                    "add the provider blocks by hand or convert the file to plain JSON first"
                ) from exc
            if not isinstance(loaded, dict):
                raise ProfileSyncError(f"{path} must contain a JSON object")
            data = loaded
        else:
            data = {"$schema": "https://opencode.ai/config.json"}
        providers = data.setdefault("provider", {})
        if not isinstance(providers, dict):
            raise ProfileSyncError(f"{path} 'provider' must be an object")
        for name in sorted(entries):
            entry = entries[name]
            existing = providers.get(name)
            if isinstance(existing, dict) and not str(existing.get("name", "")).startswith(
                self.MANAGED_PREFIX
            ):
                raise ProfileSyncError(
                    f"provider {name!r} in {path} is not managed by pitwall; rename the route or remove that provider"
                )
            endpoint = entry["endpoint"]
            is_model_studio = endpoint.get("kind") == model_studio.KIND
            protocol = str(endpoint.get("protocol", "openai")) if is_model_studio else "openai"
            npm = "@ai-sdk/anthropic" if protocol == "anthropic" else "@ai-sdk/openai-compatible"
            options: dict[str, Any] = {"baseURL": self.expected_base_url(endpoint)}
            key_env = endpoint.get("apiKeyEnv")
            if key_env:
                options["apiKey"] = f"{{env:{key_env}}}"
            if is_model_studio and protocol == "openai":
                options["includeUsage"] = True
            limits = entry.get("limits", {})
            model_id = str(entry["model"])
            model_block: dict[str, Any] = {
                "name": model_id,
                "limit": {
                    "context": limits.get("context", 32768),
                    "output": limits.get("output", 4096),
                },
            }
            if is_model_studio and protocol == "openai":
                values = model_studio.effort_values(model_id)
                if values:
                    model_block["variants"] = {
                        value: {"reasoningEffort": value} for value in values
                    }
            providers[name] = {
                "npm": npm,
                "name": f"{self.MANAGED_PREFIX}{name}",
                "options": options,
                "models": {model_id: model_block},
            }
        return path, before, json.dumps(data, indent=2) + "\n"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [model, str(prompt_path)]
        if effort is not None:
            args.extend(["--variant", effort])
        return args

    def usage(self) -> str:
        return "opencode-shim: usage: opencode-shim.sh <provider/model> <prompt-source> [extra opencode-run args]"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 2, self.usage())
        model, has_model = self.parse_model_flag(argv[2:], argv[0])
        return ParsedRequest(
            source=argv[1], model=model, extra_args=argv[2:], has_model_override=has_model
        )

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        if env.get("OPENCODE_BIN"):
            return env["OPENCODE_BIN"]
        discovered = self.which("opencode", env)
        if discovered:
            return discovered
        fallback = home / ".opencode" / "bin" / "opencode"
        return str(fallback) if os.access(fallback, os.X_OK) else None

    def missing_binary_message(self) -> str:
        return "opencode-shim: opencode CLI not found"

    def preflight(
        self,
        request: ParsedRequest,
        binary: str,
        env: Mapping[str, str],
    ) -> dict[str, str]:
        if not self.unrestricted(env):
            return {}
        try:
            result = run_bounded_capture(
                [binary, "run", "--help"],
                env=dict(env),
                timeout_seconds=30,
                max_bytes=1024 * 1024,
            )
            help_text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        except OSError:
            help_text = ""
        if "--dangerously-skip-permissions" in help_text:
            return {"permissionFlag": "--dangerously-skip-permissions"}
        if "--auto" in help_text:
            return {"permissionFlag": "--auto"}
        return {}

    def policy_profile(
        self,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str] | None = None,
    ) -> str:
        # Asking for unrestricted is not the same as getting it: the bypass
        # only applies if the installed opencode advertises a permission flag.
        # Without one the CLI keeps prompting, so report what actually ran.
        if not self.unrestricted(env):
            return "cli-policy"
        if preflight_data is None:
            return "unrestricted"
        return "unrestricted" if preflight_data.get("permissionFlag") else "cli-policy"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        child_env = dict(env)
        if child_env.get("OPENCODE_OTLP_ENDPOINT"):
            child_env.setdefault("OPENCODE_ENABLE_TELEMETRY", "1")
            child_env.setdefault("OPENCODE_OTLP_PROTOCOL", "http/protobuf")
            child_env.setdefault("OPENCODE_RESOURCE_ATTRIBUTES", "service.name=opencode")
        args = ["run"]
        if not request.has_model_override:
            args.extend(["-m", request.model])
        permission_flag = preflight_data.get("permissionFlag")
        if permission_flag:
            args.append(permission_flag)
        args.extend(request.extra_args)
        return PreparedCommand([binary, *args], child_env, prompt, self.sanitize_args(args))
