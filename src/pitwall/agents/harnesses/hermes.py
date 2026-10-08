"""Hermes Agent CLI adapter (NousResearch/hermes-agent)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pitwall.agents.errors import ProfileSyncError, UsageError

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

_RESERVED_FLAGS = ("-z", "-q", "--query", "--query-file", "-Q", "--quiet")
_DEFAULT_MODEL_RE = re.compile(
    r"^model:\s*$(?:\n(?:[ \t]+.*|\s*)$)*?\n[ \t]+default:\s*['\"]?([^'\"\n#]+)", re.M
)
_HTTP_ERROR_RE = re.compile(rb"^HTTP \d{3}:")
# Hermes' config.yaml is a large user-managed file. Managed provider blocks are
# fenced by this marker so a sync rewrites only what it wrote.
_MANAGED = "  # managed by pitwall\n"
_TOP_LEVEL = re.compile(r"^([A-Za-z0-9_-]+):(?:\s*#.*)?\n$")
_PROVIDER = re.compile(r"^  ([A-Za-z0-9_.-]+):(?:\s*#.*)?\n$")


class HermesAdapter(HarnessAdapter):
    harness_id = "hermes"
    effort_values: tuple[str, ...] | None = None
    prompt_delivery = "argv"
    binary_override_env = "HERMES_BIN"
    # Hermes resolves credentials from its own provider configuration: an
    # OPENAI_API_KEY in the environment is not attached to a non-loopback
    # endpoint, so the endpoint has to be named in config.yaml.
    endpoint_delivery = "config-sync"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        del effort  # no effort control; workflow validation rejects one
        return [str(prompt_path), "--model", model]

    def usage(self) -> str:
        return "hermes-shim: usage: hermes-shim.sh <prompt-source> [extra hermes args]"

    @staticmethod
    def _configured_model(env: Mapping[str, str], home: Path) -> str:
        configured = env.get("HERMES_MODEL")
        if configured:
            return configured
        root = Path(env.get("HERMES_HOME", str(home / ".hermes"))).expanduser()
        try:
            text = (root / "config.yaml").read_text(encoding="utf-8")
        except OSError:
            return "hermes-default"
        match = _DEFAULT_MODEL_RE.search(text)
        return match.group(1).strip() if match else "hermes-default"

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            if argument in _RESERVED_FLAGS or any(
                argument.startswith(f"{flag}=") for flag in ("--query", "--query-file")
            ):
                raise UsageError(f"hermes-shim: {argument.split('=', 1)[0]} is managed by the shim")
        model, has_model = self.parse_model_flag(argv[1:], self._configured_model(env, home))
        return ParsedRequest(argv[0], model, argv[1:], has_model)

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        del home
        return env.get("HERMES_BIN") or self.which("hermes", env)

    def missing_binary_message(self) -> str:
        return (
            "hermes-shim: Hermes Agent not found (see https://github.com/NousResearch/hermes-agent)"
        )

    def detect_soft_denial(self, exit_code: int, stderr_tail: bytes) -> str | None:
        last_line = next(
            (line.strip() for line in reversed(stderr_tail.splitlines()) if line.strip()),
            b"",
        )
        if exit_code == 0 and _HTTP_ERROR_RE.match(last_line):
            return (
                "hermes reported an HTTP failure after exiting 0: hermes did not attach "
                "credentials for a non-loopback endpoint. Hermes resolves credentials from "
                "its own provider config, so the endpoint must be materialized there: run "
                "pitwall agents profiles sync --harness hermes. A loopback address needs none "
                "of this. See docs/attach-local-endpoint.md"
            )
        return None

    # ---- endpoint materialization -------------------------------------------

    @staticmethod
    def hermes_home(env: Mapping[str, str], home: Path) -> Path:
        return Path(env.get("HERMES_HOME", str(home / ".hermes"))).expanduser()

    def _config(self, env: Mapping[str, str], home: Path) -> Path:
        return self.hermes_home(env, home) / "config.yaml"

    def endpoint_argv(self, name: str, entry: Mapping[str, Any]) -> list[str]:
        """Select the materialized provider for this route."""
        del entry
        return ["--provider", name]

    @staticmethod
    def _render_provider(name: str, entry: Mapping[str, Any]) -> list[str]:
        endpoint = entry["endpoint"]
        lines = [
            _MANAGED,
            f"  {name}:\n",
            f'    base_url: "{endpoint["baseUrl"]}"\n',
            "    api_mode: chat_completions\n",
        ]
        # key_env names the variable; the secret itself never reaches the file.
        if endpoint.get("apiKeyEnv"):
            lines.append(f"    key_env: {endpoint['apiKeyEnv']}\n")
        return lines

    @staticmethod
    def _hermes_routes(entries: Mapping[str, Mapping[str, Any]]) -> list[str]:
        return sorted(name for name, entry in entries.items() if entry.get("harness") == "hermes")

    @staticmethod
    def _section(lines: list[str], name: str) -> tuple[int, int] | None:
        """Bounds of a top-level block mapping, or None when it is absent."""
        starts = [index for index, line in enumerate(lines) if line == f"{name}:\n"]
        if len(starts) > 1:
            raise ProfileSyncError(
                f"config.yaml has duplicate {name!r} sections; consolidate them before profiles sync"
            )
        if not starts:
            return None
        start = starts[0]
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if _TOP_LEVEL.match(lines[index]) is not None:
                end = index
                break
        return start, end

    @classmethod
    def _strip_managed(cls, lines: list[str], start: int, end: int) -> tuple[list[str], int]:
        """Remove managed provider blocks inside [start, end); returns (lines, new_end)."""
        index = start + 1
        while index < end:
            if lines[index] != _MANAGED:
                index += 1
                continue
            if index + 1 >= end or _PROVIDER.match(lines[index + 1]) is None:
                raise ProfileSyncError(
                    "config.yaml has a malformed pitwall provider marker; repair it before profiles sync"
                )
            finish = index + 2
            while (
                finish < end
                and _PROVIDER.match(lines[finish]) is None
                and lines[finish] != _MANAGED
                and _TOP_LEVEL.match(lines[finish]) is None
            ):
                finish += 1
            removed = finish - index
            del lines[index:finish]
            end -= removed
        return lines, end

    @classmethod
    def _merged(
        cls, before: str, targets: list[str], entries: Mapping[str, Mapping[str, Any]]
    ) -> str:
        if before and not before.endswith("\n"):
            raise ProfileSyncError(
                "config.yaml must end with a newline before profiles sync can preserve it safely"
            )
        lines = before.splitlines(keepends=True)
        section = cls._section(lines, "providers")
        if section is None:
            if lines and lines[-1].strip():
                lines.append("\n")
            lines.append("providers:\n")
            section = (len(lines) - 1, len(lines))
        start, end = section
        lines, end = cls._strip_managed(lines, start, end)
        rendered: list[str] = []
        for name in targets:
            rendered.extend(cls._render_provider(name, entries[name]))
        lines[end:end] = rendered
        return "".join(lines)

    def endpoint_sync_status(
        self, name: str, entry: Mapping[str, Any], env: Mapping[str, str], home: Path
    ) -> str:
        path = self._config(env, home)
        try:
            before = path.read_text(encoding="utf-8")
        except OSError:
            return "missing"
        lines = before.splitlines(keepends=True)
        try:
            section = self._section(lines, "providers")
        except ProfileSyncError:
            return "stale"
        if section is None:
            return "missing"
        start, end = section
        expected = self._render_provider(name, entry)
        index = start + 1
        while index < end:
            if lines[index] == _MANAGED and index + 1 < end and lines[index + 1] == f"  {name}:\n":
                finish = index + 2
                while (
                    finish < end
                    and _PROVIDER.match(lines[finish]) is None
                    and lines[finish] != _MANAGED
                    and _TOP_LEVEL.match(lines[finish]) is None
                ):
                    finish += 1
                return "synced" if lines[index:finish] == expected else "stale"
            index += 1
        return "missing"

    def plan_endpoint_sync(
        self, entries: Mapping[str, Mapping[str, Any]], env: Mapping[str, str], home: Path
    ) -> list[tuple[Path, str, str]]:
        path = self._config(env, home)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        targets = self._hermes_routes(entries)
        return [(path, before, self._merged(before, targets, entries))]

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        child_env = dict(env)
        workspace = preflight_data.get("workspacePath")
        if workspace:
            # Hermes oneshot bypasses the interactive CLI cwd initialization.
            # Use the prepared dispatch workspace, including isolated worktrees.
            child_env["TERMINAL_CWD"] = workspace
        args = [*request.extra_args]
        if self.unrestricted(env):
            args.append("--yolo")
        args.extend(["-z", self.argv_prompt_text(prompt)])
        return PreparedCommand(
            [binary, *args], child_env, None, self.sanitize_args(args[:-1] + ["<prompt>"])
        )
