"""DeepSeek Harness (dsh) adapter — experimental developer-preview CLI."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pitwall.agents.errors import ProfileSyncError, UsageError

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

_RESERVED_FLAGS = ("--patch", "--dump-config", "--dump-default-config", "web")
_MANAGED = "    # managed by pitwall\n"
_TOP_LEVEL = re.compile(r"^([A-Za-z0-9_-]+):(?:\s*#.*)?\n$")
_PROVIDER = re.compile(r"^    ([A-Za-z0-9_.-]+):(?:\s*#.*)?\n$")


class DshAdapter(HarnessAdapter):
    harness_id = "dsh"
    effort_values: tuple[str, ...] | None = None
    prompt_delivery = "argv"
    binary_override_env = "DSH_BIN"
    endpoint_delivery = "config-sync"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        del effort  # no effort control; workflow validation rejects one
        return [str(prompt_path), "--model", model]

    def usage(self) -> str:
        return "dsh-shim: usage: dsh-shim.sh <prompt-source> [extra dsh args]"

    @staticmethod
    def dsh_home(env: Mapping[str, str], home: Path) -> Path:
        return Path(env.get("DSH_HOME", str(home / ".dsh"))).expanduser()

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument in _RESERVED_FLAGS or any(
                argument.startswith(f"{flag}=") for flag in _RESERVED_FLAGS[:3]
            ):
                raise UsageError(f"dsh-shim: {argument.split('=', 1)[0]} is managed by the shim")
        if not self.unrestricted(env):
            # dsh's headless profile documents no approval setting, so a restricted
            # run cannot be honoured.
            raise UsageError(
                "dsh-shim: dsh --help documents no approval setting, so it cannot honour a "
                "restricted run; set PITWALL_AGENTS_UNRESTRICTED=1 to dispatch through dsh"
            )
        model, has_model = self.parse_model_flag(argv[1:], "dsh-default")
        return ParsedRequest(argv[0], model, argv[1:], has_model)

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        del home
        return env.get("DSH_BIN") or self.which("dsh", env)

    def missing_binary_message(self) -> str:
        return "dsh-shim: DeepSeek Harness not found (npm install -g @deepseek-ai/dsh)"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        del preflight_data
        args = list(request.extra_args)
        if not any(
            argument == "--profile" or argument.startswith("--profile=") for argument in args
        ):
            args = ["--profile", "headless", *args]
        args.append(self.argv_prompt_text(prompt))
        return PreparedCommand(
            [binary, *args], dict(env), None, self.sanitize_args(args[:-1] + ["<prompt>"])
        )

    def endpoint_argv(self, name: str, entry: Mapping[str, Any]) -> list[str]:
        del name, entry
        return ["--profile", "headless"]

    def _settings(self, env: Mapping[str, str], home: Path) -> Path:
        return self.dsh_home(env, home) / "settings.yaml"

    @staticmethod
    def _dsh_routes(entries: Mapping[str, Mapping[str, Any]]) -> list[str]:
        return sorted(name for name, entry in entries.items() if entry.get("harness") == "dsh")

    @staticmethod
    def _section(lines: list[str], name: str) -> tuple[int, int] | None:
        starts = [index for index, line in enumerate(lines) if line == f"{name}:\n"]
        if len(starts) > 1:
            raise ProfileSyncError(
                f"settings.yaml has duplicate {name!r} sections; consolidate them before profiles sync"
            )
        if not starts:
            if any(line.startswith(f"{name}:") for line in lines):
                raise ProfileSyncError(
                    f"settings.yaml {name!r} section must use an indented block mapping"
                )
            return None
        start = starts[0]
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if _TOP_LEVEL.match(lines[index]):
                end = index
                break
            if (
                lines[index]
                and not lines[index][0].isspace()
                and not lines[index].lstrip().startswith("#")
            ):
                raise ProfileSyncError(
                    "settings.yaml uses unsupported YAML syntax; normalize it to block mappings before profiles sync"
                )
        return start, end

    @staticmethod
    def _render_provider(name: str, entry: Mapping[str, Any]) -> list[str]:
        endpoint = entry["endpoint"]
        lines = [
            _MANAGED,
            f"    {name}:\n",
            "      api: openai-completions\n",
            f"      baseURL: {endpoint['baseUrl']}\n",
        ]
        if endpoint.get("apiKeyEnv"):
            lines.append(f"      apiKeyEnv: {endpoint['apiKeyEnv']}\n")
        # dsh's schema requires models entries to be objects with a required id.
        # Explicit window/output caps on every entry: dsh's own defaults
        # (262144/32768) hand a small local model a zero-token input budget.
        limits = entry.get("limits", {})
        lines.append("      models:\n")
        lines.append(f"        - id: {entry['model']}\n")
        lines.append(f"          contextWindow: {limits.get('context', 32768)}\n")
        lines.append(f"          maxTokens: {limits.get('output', 4096)}\n")
        return lines

    @classmethod
    def _merge_providers(
        cls, lines: list[str], targets: list[str], entries: Mapping[str, Mapping[str, Any]]
    ) -> list[str]:
        section = cls._section(lines, "llm-pi-ai")
        if section is None:
            if lines and lines[-1].strip():
                lines.append("\n")
            lines.extend(["llm-pi-ai:\n", "  providers:\n"])
            section = (len(lines) - 2, len(lines))
        start, end = section
        provider_lines = [
            index for index in range(start + 1, end) if lines[index] == "  providers:\n"
        ]
        if len(provider_lines) > 1:
            raise ProfileSyncError(
                "settings.yaml has duplicate llm-pi-ai.providers mappings; consolidate them before profiles sync"
            )
        if not provider_lines:
            if any(line.startswith("  providers:") for line in lines[start + 1 : end]):
                raise ProfileSyncError(
                    "settings.yaml llm-pi-ai.providers must use an indented block mapping"
                )
            lines.insert(end, "  providers:\n")
            provider_start = end
            provider_end = end + 1
        else:
            provider_start = provider_lines[0]
            provider_end = end
            for index in range(provider_start + 1, end):
                line = lines[index]
                if (
                    line.startswith("  ")
                    and not line.startswith("    ")
                    and line.strip()
                    and not line.lstrip().startswith("#")
                ):
                    provider_end = index
                    break

        index = provider_start + 1
        while index < provider_end:
            if lines[index] != _MANAGED:
                index += 1
                continue
            if index + 1 >= provider_end or _PROVIDER.match(lines[index + 1]) is None:
                raise ProfileSyncError(
                    "settings.yaml has a malformed pitwall provider marker; repair it before profiles sync"
                )
            finish = index + 2
            while (
                finish < provider_end
                and _PROVIDER.match(lines[finish]) is None
                and not (lines[finish].startswith("  ") and not lines[finish].startswith("    "))
            ):
                finish += 1
            del lines[index:finish]
            provider_end -= finish - index
        additions: list[str] = []
        for name in targets:
            additions.extend(cls._render_provider(name, entries[name]))
        lines[provider_end:provider_end] = additions
        return lines

    @classmethod
    def _merge_default(
        cls,
        lines: list[str],
        targets: list[str],
        entries: Mapping[str, Mapping[str, Any]],
        previously_managed: set[str],
    ) -> list[str]:
        section = cls._section(lines, "agent-default-model")
        if not targets:
            if section is not None:
                start, end = section
                providers = [
                    line.removeprefix("  provider:").strip()
                    for line in lines[start + 1 : end]
                    if line.startswith("  provider:")
                ]
                if providers and providers[0] in previously_managed:
                    del lines[start:end]
            return lines
        name = targets[0]
        wanted = [
            "agent-default-model:\n",
            f"  provider: {name}\n",
            f"  model: {entries[name]['model']}\n",
        ]
        if section is None:
            lines.extend(wanted)
            return lines
        start, end = section
        provider_indexes = [
            index for index in range(start + 1, end) if lines[index].startswith("  provider:")
        ]
        if (
            provider_indexes
            and lines[provider_indexes[0]].split("#", 1)[0].strip() != f"provider: {name}"
        ):
            raise ProfileSyncError(
                "settings.yaml agent-default-model is user-managed; select the dsh route there or remove that section before profiles sync"
            )
        if provider_indexes:
            suffix = (
                " #" + lines[provider_indexes[0]].split("#", 1)[1]
                if "#" in lines[provider_indexes[0]]
                else "\n"
            )
            lines[provider_indexes[0]] = f"  provider: {name}" + suffix
        else:
            lines.insert(end, wanted[1])
            end += 1
        models = [index for index in range(start + 1, end) if lines[index].startswith("  model:")]
        if models:
            suffix = " #" + lines[models[0]].split("#", 1)[1] if "#" in lines[models[0]] else "\n"
            lines[models[0]] = f"  model: {entries[name]['model']}" + suffix
        else:
            lines.insert(end, wanted[2])
        return lines

    @classmethod
    def _merged(
        cls, before: str, targets: list[str], entries: Mapping[str, Mapping[str, Any]]
    ) -> str:
        if before and not before.endswith("\n"):
            raise ProfileSyncError(
                "settings.yaml must end with a newline before profiles sync can preserve it safely"
            )
        lines = before.splitlines(keepends=True)
        previously_managed = {
            match.group(1)
            for index, line in enumerate(lines[:-1])
            if line == _MANAGED and (match := _PROVIDER.match(lines[index + 1])) is not None
        }
        lines = cls._merge_providers(lines, targets, entries)
        lines = cls._merge_default(lines, targets, entries, previously_managed)
        return "".join(lines).replace("\n\nllm-pi-ai:\n", "\nllm-pi-ai:\n")

    def endpoint_sync_status(
        self, name: str, entry: Mapping[str, Any], env: Mapping[str, str], home: Path
    ) -> str:
        path = self._settings(env, home)
        try:
            before = path.read_text(encoding="utf-8")
        except OSError:
            return "missing"
        marker = _MANAGED + f"    {name}:\n"
        if marker not in before:
            return "missing"
        try:
            lines = before.splitlines(keepends=True)
            marker_index = lines.index(_MANAGED)
            if marker_index + 1 >= len(lines) or lines[marker_index + 1] != f"    {name}:\n":
                marker_index = lines.index(_MANAGED, marker_index + 1)
            finish = marker_index + 2
            while (
                finish < len(lines)
                and _PROVIDER.match(lines[finish]) is None
                and not (lines[finish].startswith("  ") and not lines[finish].startswith("    "))
                and _TOP_LEVEL.match(lines[finish]) is None
            ):
                finish += 1
            if lines[marker_index:finish] != self._render_provider(name, entry):
                return "stale"
            section = self._section(lines, "agent-default-model")
            if section is None:
                return "stale"
            start, end = section
            values = {
                line.split(":", 1)[0].strip(): line.split(":", 1)[1].split("#", 1)[0].strip()
                for line in lines[start + 1 : end]
                if ":" in line
            }
        except ProfileSyncError:
            return "stale"
        return (
            "synced"
            if values.get("provider") == name and values.get("model") == entry["model"]
            else "stale"
        )

    def plan_endpoint_sync(
        self, entries: Mapping[str, Mapping[str, Any]], env: Mapping[str, str], home: Path
    ) -> list[tuple[Path, str, str]]:
        path = self._settings(env, home)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        targets = self._dsh_routes(entries)
        if len(targets) > 1:
            raise ProfileSyncError(
                f"dsh runs one managed default model at a time; routes {', '.join(targets)} resolve to harness dsh"
            )
        return [(path, before, self._merged(before, targets, entries))]
