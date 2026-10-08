"""Pi coding-agent CLI adapter (earendil-works/pi)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pitwall.agents.errors import ProfileSyncError, UsageError
from pitwall.providers.model_studio import catalog as model_studio

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

_RESERVED_FLAGS = ("-p", "--print", "--mode", "--export")


class PiAdapter(HarnessAdapter):
    harness_id = "pi"
    effort_values: tuple[str, ...] | None = (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    # `pi --help`: `pi [options] [--] [@files...] [messages...]`. The prompt goes
    # in a private file passed as `@<path>` so it never appears in argv.
    prompt_delivery = "file"
    private_prompt_file = True
    binary_override_env = "PI_BIN"
    endpoint_delivery = "config-sync"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "--model", model]
        if effort is not None:
            args.extend(["--thinking", effort])
        return args

    def usage(self) -> str:
        return "pi-shim: usage: pi-shim.sh <prompt-source> [extra pi args]"

    @staticmethod
    def agent_dir(env: Mapping[str, str], home: Path) -> Path:
        return Path(env.get("PI_CODING_AGENT_DIR", str(home / ".pi" / "agent"))).expanduser()

    @classmethod
    def _configured_model(cls, env: Mapping[str, str], home: Path) -> str:
        # Best effort: Pi keeps its default model in settings.json under the agent dir.
        try:
            data = json.loads(
                (cls.agent_dir(env, home) / "settings.json").read_text(encoding="utf-8")
            )
        except OSError, json.JSONDecodeError:
            return "pi-default"
        model = data.get("defaultModel") if isinstance(data, dict) else None
        if isinstance(model, dict):
            model = model.get("id")
        return model if isinstance(model, str) and model else "pi-default"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument in _RESERVED_FLAGS or any(
                argument.startswith(f"{flag}=") for flag in _RESERVED_FLAGS
            ):
                raise UsageError(f"pi-shim: {argument.split('=', 1)[0]} is managed by the shim")
        model, has_model = self.parse_model_flag(argv[1:], self._configured_model(env, home))
        return ParsedRequest(argv[0], model, argv[1:], has_model)

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        del home
        return env.get("PI_BIN") or self.which("pi", env)

    def missing_binary_message(self) -> str:
        return "pi-shim: Pi coding agent not found (npm install -g --ignore-scripts @earendil-works/pi-coding-agent)"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        del prompt
        args = [
            "-p",
            "--no-session",
            "--approve" if self.unrestricted(env) else "--no-approve",
            *request.extra_args,
            f"@{preflight_data['promptFile']}",
        ]
        return PreparedCommand([binary, *args], dict(env), None, self.sanitize_args(args))

    def endpoint_argv(self, name: str, entry: Mapping[str, Any]) -> list[str]:
        del entry
        return ["--provider", name]

    def _models_path(self, env: Mapping[str, str], home: Path) -> Path:
        return self.agent_dir(env, home) / "models.json"

    @staticmethod
    def _managed_provider_fields(entry: Mapping[str, Any]) -> dict[str, Any]:
        endpoint = entry["endpoint"]
        fields: dict[str, Any] = {
            "baseUrl": str(endpoint["baseUrl"]),
            "api": "openai-completions",
        }
        if endpoint.get("apiKeyEnv"):
            fields["apiKey"] = f"${endpoint['apiKeyEnv']}"
        return fields

    @staticmethod
    def _managed_model(entry: Mapping[str, Any]) -> dict[str, Any]:
        limits = entry.get("limits", {})
        model: dict[str, Any] = {
            "id": str(entry["model"]),
            "contextWindow": limits.get("context", 32768),
            "maxTokens": limits.get("output", 4096),
        }
        if entry["endpoint"].get("kind") == model_studio.KIND and model_studio.effort_values(
            str(entry["model"])
        ):
            model["reasoning"] = True
        return model

    @classmethod
    def _block(cls, name: str, entry: Mapping[str, Any]) -> dict[str, Any]:
        del name
        return {**cls._managed_provider_fields(entry), "models": [cls._managed_model(entry)]}

    def endpoint_sync_status(
        self, name: str, entry: Mapping[str, Any], env: Mapping[str, str], home: Path
    ) -> str:
        try:
            data = json.loads(self._models_path(env, home).read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return "missing"
        providers = data.get("providers") if isinstance(data, dict) else None
        existing = providers.get(name) if isinstance(providers, dict) else None
        if not isinstance(existing, dict):
            return "missing"
        expected = self._managed_provider_fields(entry)
        if existing.get("baseUrl") != expected["baseUrl"] or existing.get("api") != expected["api"]:
            return "stale"
        # An explicit route key reference is managed and must match. With no
        # reference, Pi's existing auth source is user-owned; sync stays
        # deliberately keyless and never invents a dummy credential.
        if "apiKey" in expected and existing.get("apiKey") != expected["apiKey"]:
            return "stale"
        models = existing.get("models")
        if not isinstance(models, list):
            return "stale"
        if any(not isinstance(model, dict) for model in models):
            return "stale"
        wanted = self._managed_model(entry)
        # A model-level API override takes precedence over the provider API in
        # Pi. It is therefore part of the collision check even though route
        # sync does not own or rewrite that model metadata.
        matches = [model for model in models if model.get("id") == wanted["id"]]
        if len(matches) != 1:
            return "stale"
        model = matches[0]
        if model.get("api") not in (None, expected["api"]) or model.get("baseUrl") not in (
            None,
            expected["baseUrl"],
        ):
            return "stale"
        return (
            "synced"
            if all(model.get(field) == wanted[field] for field in ("contextWindow", "maxTokens"))
            else "stale"
        )

    def plan_endpoint_sync(
        self, entries: Mapping[str, Mapping[str, Any]], env: Mapping[str, str], home: Path
    ) -> tuple[Path, str, str]:
        path = self._models_path(env, home)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        data: dict[str, Any] = {}
        if before.strip():
            try:
                loaded = json.loads(before)
            except json.JSONDecodeError as exc:
                raise ProfileSyncError(
                    f"{path} is not strict JSON: {exc.msg} at line {exc.lineno}"
                ) from exc
            if not isinstance(loaded, dict):
                raise ProfileSyncError(f"{path} must contain a JSON object")
            data = loaded
        providers = data.setdefault("providers", {})
        if not isinstance(providers, dict):
            raise ProfileSyncError(f"{path} 'providers' must be an object")
        for name in sorted(entries):
            entry = entries[name]
            managed = self._managed_provider_fields(entry)
            existing = providers.get(name)
            if existing is None:
                providers[name] = {**managed, "models": [self._managed_model(entry)]}
                continue
            if not isinstance(existing, dict):
                raise ProfileSyncError(
                    f"provider {name!r} in {path} must be an object before profiles sync can update it"
                )
            if existing.get("baseUrl") not in (None, managed["baseUrl"]):
                raise ProfileSyncError(
                    f"provider {name!r} in {path} points elsewhere; rename the route or remove that provider"
                )
            if existing.get("api") not in (None, managed["api"]):
                raise ProfileSyncError(
                    f"provider {name!r} in {path} uses API {existing.get('api')!r}; route requires {managed['api']!r}"
                )
            models = existing.get("models", [])
            if not isinstance(models, list) or any(not isinstance(model, dict) for model in models):
                raise ProfileSyncError(
                    f"provider {name!r} in {path} has an invalid models list; repair it before profiles sync"
                )
            wanted = self._managed_model(entry)
            matches = [
                index for index, model in enumerate(models) if model.get("id") == wanted["id"]
            ]
            if len(matches) > 1:
                raise ProfileSyncError(
                    f"provider {name!r} in {path} has duplicate model id {wanted['id']!r}; consolidate it before profiles sync"
                )
            updated = bool(matches)
            if updated:
                index = matches[0]
                model = models[index]
                if model.get("api") not in (None, managed["api"]):
                    raise ProfileSyncError(
                        f"model {wanted['id']!r} in provider {name!r} uses API {model.get('api')!r}; route requires {managed['api']!r}"
                    )
                if model.get("baseUrl") not in (None, managed["baseUrl"]):
                    raise ProfileSyncError(
                        f"model {wanted['id']!r} in provider {name!r} points elsewhere; reconcile its endpoint before profiles sync"
                    )
                # Preserve model metadata and update only route-owned caps.
                models[index] = {
                    **model,
                    "contextWindow": wanted["contextWindow"],
                    "maxTokens": wanted["maxTokens"],
                }
            if not updated:
                models.append(wanted)
            # Preserve every unowned provider field. Route auth is changed only
            # when the route explicitly supplies an environment reference.
            existing["baseUrl"] = managed["baseUrl"]
            existing["api"] = managed["api"]
            if "apiKey" in managed:
                existing["apiKey"] = managed["apiKey"]
            existing["models"] = models
        return path, before, json.dumps(data, indent=2) + "\n"
