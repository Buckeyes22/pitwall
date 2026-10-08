"""Secret-safe inventory of capabilities exposed by installed CLI harnesses."""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import codex_home, config_root
from .run_store import atomic_write_bytes, atomic_write_json, utc_now
from .setup import environment_with_user_bins, resolve_harness_binary
from .yaml_channel import YamlChannelError, read_entry

INVENTORY_JSON = "harness-capabilities.json"
INVENTORY_MARKDOWN = "harness-capabilities.md"
CAPABILITY_KINDS = ("agents", "commands", "extensions", "mcps", "plugins", "skills", "tools")
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 512
MAX_NAMES_PER_KIND = 512
_SECTION_KIND = {
    "agents": "agents",
    "commands": "commands",
    "extensions": "extensions",
    "mcp": "mcps",
    "mcpservers": "mcps",
    "plugin": "plugins",
    "plugins": "plugins",
    "enabledplugins": "plugins",
    "skills": "skills",
    "tools": "tools",
}
_NAME = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@._:/+-]{0,159}$")
_SENSITIVE = ("authorization", "password", "secret", "token", "apikey", "api_key")
#: Spec text for the Codex home directory; ``_path`` resolves it through ``paths.codex_home``.
CODEX_HOME_SPEC = "${CODEX_HOME:-~/.codex}"
_ENV_DEFAULT = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-([^}]*)}")


@dataclass(frozen=True, slots=True)
class HarnessProfile:
    files: tuple[str, ...] = ()
    directories: tuple[tuple[str, str], ...] = ()


PROFILES: Mapping[str, HarnessProfile] = {
    "agy": HarnessProfile(
        files=("~/.gemini/settings.json", "~/.gemini/antigravity-cli/settings.json"),
        directories=(
            ("extensions", "~/.gemini/extensions"),
            ("skills", "~/.gemini/skills"),
            ("agents", "~/.gemini/agents"),
            ("commands", "~/.gemini/commands"),
        ),
    ),
    "claude": HarnessProfile(
        files=(
            "~/.claude.json",
            "${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json",
            "${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/installed_plugins.json",
        ),
        directories=(
            ("plugins", "${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/cache"),
            ("skills", "${CLAUDE_CONFIG_DIR:-~/.claude}/skills"),
            ("agents", "${CLAUDE_CONFIG_DIR:-~/.claude}/agents"),
            ("commands", "${CLAUDE_CONFIG_DIR:-~/.claude}/commands"),
        ),
    ),
    "cline": HarnessProfile(
        files=(
            "${CLINE_DIR:-~/.cline}/data/settings/settings.json",
            "${CLINE_DIR:-~/.cline}/data/settings/cline_mcp_settings.json",
        ),
        directories=(
            ("skills", "${CLINE_DIR:-~/.cline}/skills"),
            ("agents", "${CLINE_DIR:-~/.cline}/agents"),
        ),
    ),
    "codex": HarnessProfile(
        files=(
            f"{CODEX_HOME_SPEC}/config.toml",
            f"{CODEX_HOME_SPEC}/plugins/installed.json",
        ),
        directories=(
            ("plugins", f"{CODEX_HOME_SPEC}/plugins/cache"),
            ("skills", f"{CODEX_HOME_SPEC}/skills"),
            ("skills", "~/.agents/skills"),
            ("agents", f"{CODEX_HOME_SPEC}/agents"),
        ),
    ),
    "copilot": HarnessProfile(
        files=("~/.copilot/config.json", "~/.copilot/mcp-config.json"),
        directories=(
            ("plugins", "~/.copilot/plugins"),
            ("skills", "~/.copilot/skills"),
            ("agents", "~/.copilot/agents"),
        ),
    ),
    "dsh": HarnessProfile(
        files=("${DSH_HOME:-~/.dsh}/settings.yaml",),
        directories=(
            ("skills", "${DSH_HOME:-~/.dsh}/skills"),
            ("agents", "${DSH_HOME:-~/.dsh}/agents"),
        ),
    ),
    "goose": HarnessProfile(
        files=("${XDG_CONFIG_HOME}/goose/config.yaml",),
        directories=(
            ("extensions", "${XDG_CONFIG_HOME}/goose/extensions"),
            ("skills", "${XDG_CONFIG_HOME}/goose/skills"),
        ),
    ),
    "grok": HarnessProfile(
        files=("~/.grok/config.json", "~/.grok/settings.json"),
        directories=(("skills", "~/.grok/skills"), ("agents", "~/.grok/agents")),
    ),
    "hermes": HarnessProfile(
        files=("${HERMES_HOME:-~/.hermes}/config.yaml",),
        directories=(
            ("skills", "${HERMES_HOME:-~/.hermes}/skills"),
            ("agents", "${HERMES_HOME:-~/.hermes}/agents"),
            ("tools", "${HERMES_HOME:-~/.hermes}/tools"),
        ),
    ),
    "kimi": HarnessProfile(
        files=(
            "${KIMI_CODE_HOME:-~/.kimi-code}/config.toml",
            "${KIMI_CODE_HOME:-~/.kimi-code}/mcp.json",
        ),
        directories=(
            ("skills", "${KIMI_CODE_HOME:-~/.kimi-code}/skills"),
            ("agents", "${KIMI_CODE_HOME:-~/.kimi-code}/agents"),
        ),
    ),
    "muse": HarnessProfile(
        files=("~/.muse/config.json", "~/.muse/settings.json"),
        directories=(("skills", "~/.muse/skills"), ("agents", "~/.muse/agents")),
    ),
    "opencode": HarnessProfile(
        files=("${XDG_CONFIG_HOME}/opencode/opencode.json",),
        directories=(
            ("plugins", "${XDG_CONFIG_HOME}/opencode/plugins"),
            ("skills", "${XDG_CONFIG_HOME}/opencode/skills"),
            ("agents", "${XDG_CONFIG_HOME}/opencode/agents"),
            ("commands", "${XDG_CONFIG_HOME}/opencode/commands"),
        ),
    ),
    "pi": HarnessProfile(
        files=("${PI_CODING_AGENT_DIR:-~/.pi/agent}/settings.json",),
        directories=(
            ("extensions", "${PI_CODING_AGENT_DIR:-~/.pi/agent}/extensions"),
            ("skills", "${PI_CODING_AGENT_DIR:-~/.pi/agent}/skills"),
            ("agents", "${PI_CODING_AGENT_DIR:-~/.pi/agent}/agents"),
            ("commands", "${PI_CODING_AGENT_DIR:-~/.pi/agent}/prompts"),
        ),
    ),
    "qwen": HarnessProfile(
        files=("${QWEN_CODE_HOME:-~/.qwen}/settings.json",),
        directories=(
            ("extensions", "${QWEN_CODE_HOME:-~/.qwen}/extensions"),
            ("skills", "${QWEN_CODE_HOME:-~/.qwen}/skills"),
            ("agents", "${QWEN_CODE_HOME:-~/.qwen}/agents"),
            ("commands", "${QWEN_CODE_HOME:-~/.qwen}/commands"),
        ),
    ),
}


def _path(spec: str, env: Mapping[str, str], home: Path) -> Path:
    xdg_config = env.get("XDG_CONFIG_HOME") or str(home / ".config")
    expanded = spec.replace(CODEX_HOME_SPEC, str(codex_home(env, home)))
    expanded = expanded.replace("${XDG_CONFIG_HOME}", xdg_config)
    expanded = _ENV_DEFAULT.sub(lambda match: env.get(match.group(1)) or match.group(2), expanded)
    if expanded == "~" or expanded.startswith("~/"):
        return home / expanded.removeprefix("~/") if expanded != "~" else home
    return Path(expanded)


CHANNEL_SERVER_NAME = "pitwall-channel"
CHANNEL_HARNESSES = (
    "claude",
    "codex",
    "copilot",
    "opencode",
    "kimi",
    "cline",
    "qwen",
    "zcode",
    "grok",
    "agy",
    "muse",
    "hermes",
    "goose",
)
#: Harnesses whose user config nests the server map below more than one key.
CHANNEL_NESTED_SECTION: dict[str, tuple[str, ...]] = {"zcode": ("mcp", "servers")}
#: YAML harnesses and the top-level section that holds their MCP servers.
CHANNEL_YAML_SECTION: dict[str, str] = {"hermes": "mcp_servers", "goose": "extensions"}


def channel_config_path(harness_id: str, env: Mapping[str, str], home: Path) -> Path:
    """User-scope file that registers MCP servers for *harness_id* (spec §6.3, Cline 3.x corrected)."""
    if harness_id == "claude":
        root = env.get("CLAUDE_CONFIG_DIR")
        return Path(root).expanduser() / ".claude.json" if root else home / ".claude.json"
    if harness_id == "codex":
        return _path(f"{CODEX_HOME_SPEC}/config.toml", env, home)
    if harness_id == "copilot":
        return home / ".copilot" / "mcp-config.json"
    if harness_id == "opencode":
        return _path("${XDG_CONFIG_HOME}/opencode/opencode.json", env, home)
    if harness_id == "kimi":
        return _path("${KIMI_CODE_HOME:-~/.kimi-code}/mcp.json", env, home)
    if harness_id == "cline":
        if env.get("CLINE_MCP_SETTINGS_PATH"):
            return Path(env["CLINE_MCP_SETTINGS_PATH"]).expanduser()
        data = env.get("CLINE_DATA_DIR") or str(_path("${CLINE_DIR:-~/.cline}", env, home) / "data")
        return Path(data).expanduser() / "settings" / "cline_mcp_settings.json"
    if harness_id == "qwen":
        return _path("${QWEN_CODE_HOME:-~/.qwen}/settings.json", env, home)
    if harness_id == "zcode":
        # ZCode CLI reads user-scope servers from ``mcp.servers`` in this file.
        return home / ".zcode" / "cli" / "config.json"
    if harness_id == "grok":
        # `grok mcp add --scope user` writes this file.
        return home / ".grok" / "config.toml"
    if harness_id == "agy":
        # `agy mcp add` writes user-scope servers here.
        return home / ".gemini" / "config" / "mcp_config.json"
    if harness_id == "muse":
        return _path("${XDG_CONFIG_HOME:-~/.config}/muse/settings.json", env, home)
    if harness_id == "hermes":
        return _path("${HERMES_HOME:-~/.hermes}/config.yaml", env, home)
    if harness_id == "goose":
        return _path("${XDG_CONFIG_HOME:-~/.config}/goose/config.yaml", env, home)
    raise KeyError(harness_id)


def channel_entry_disabled(entry: Any) -> bool:
    """True when a registered entry carries a host's off switch.

    Hosts spell it ``enabled: false`` (ZCode, goose, OpenCode, Grok) or ``disabled: true``
    (Antigravity, Cline). A disabled entry is still present in the file but the harness does
    not start it, so it is not a usable channel.
    """
    return isinstance(entry, Mapping) and (
        entry.get("enabled") is False or entry.get("disabled") is True
    )


def mcp_channel_registered(harness_id: str, env: Mapping[str, str], home: Path) -> bool:
    """True when the harness's user-scope MCP config names an enabled channel server."""
    if not _channel_entry_named(harness_id, env, home):
        return False
    from .mcp_registration import current_entry

    return not channel_entry_disabled(current_entry(harness_id, env, home))


def _channel_entry_named(harness_id: str, env: Mapping[str, str], home: Path) -> bool:
    if harness_id not in CHANNEL_HARNESSES:
        return False
    path = channel_config_path(harness_id, env, home)
    if not path.is_file():
        return False
    nested = CHANNEL_NESTED_SECTION.get(harness_id)
    if nested is not None:
        return CHANNEL_SERVER_NAME in (_nested_section_names(path, nested) or set())
    yaml_section = CHANNEL_YAML_SECTION.get(harness_id)
    if yaml_section is not None:
        try:
            text = path.read_text(encoding="utf-8")
            return read_entry(text, yaml_section, CHANNEL_SERVER_NAME) is not None
        except OSError, UnicodeDecodeError, YamlChannelError:
            return False
    found: dict[str, set[str]] = {kind: set() for kind in CAPABILITY_KINDS}
    return _read_structured(path, found) is None and CHANNEL_SERVER_NAME in found["mcps"]


def _nested_section_names(path: Path, keys: tuple[str, ...]) -> set[str] | None:
    """Names under the nested mapping *keys* of a JSON file, or None when unreadable."""
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_CONFIG_BYTES + 1)
        if len(content) > MAX_CONFIG_BYTES:
            return None
        value: Any = json.loads(content.decode("utf-8"))
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return None
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return {str(name) for name in value} if isinstance(value, Mapping) else set()


def _display_path(path: Path, home: Path) -> str:
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    return "~" if not relative.parts else f"~/{relative.as_posix()}"


def _safe_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    lowered = candidate.lower()
    if (
        not _NAME.fullmatch(candidate)
        or "://" in candidate
        or any(word in lowered for word in _SENSITIVE)
    ):
        return None
    return candidate


def _markdown_code(value: Any) -> str:
    text = "".join(
        character if character.isprintable() and character != "`" else "?"
        for character in str(value)
    )
    return f"`{text}`"


def _collect_section(kind: str, value: Any, found: dict[str, set[str]]) -> None:
    candidates: list[Any] = []
    if isinstance(value, Mapping):
        candidates.extend(value.keys())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, Mapping):
                candidates.append(item.get("name") or item.get("id"))
    for candidate in candidates:
        if (name := _safe_name(candidate)) is not None:
            found[kind].add(name)


def _walk_document(value: Any, found: dict[str, set[str]]) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            for key, child in current.items():
                normalized = re.sub(r"[^a-z]", "", str(key).lower())
                kind = _SECTION_KIND.get(normalized)
                if kind is not None:
                    _collect_section(kind, child, found)
                pending.append(child)
        elif isinstance(current, list):
            pending.extend(current)


def _read_structured(path: Path, found: dict[str, set[str]]) -> str | None:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_CONFIG_BYTES + 1)
    except OSError:
        return "unreadable"
    if len(content) > MAX_CONFIG_BYTES:
        return "too large"
    try:
        text = content.decode("utf-8")
        value = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
    except json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeDecodeError:
        return f"malformed {path.suffix.removeprefix('.').upper()}"
    _walk_document(value, found)
    return None


def _read_yaml(path: Path, found: dict[str, set[str]]) -> str | None:
    """Collect direct mapping keys below recognized YAML sections, never values."""

    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_CONFIG_BYTES + 1)
    except OSError:
        return "unreadable"
    if len(content) > MAX_CONFIG_BYTES:
        return "too large"
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return "malformed YAML"
    active: tuple[int, str, int | None] | None = None
    for raw in lines:
        line = raw.split("#", 1)[0].rstrip()
        if not line or line.lstrip().startswith(("-", "{", "[")):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if active is not None and indent <= active[0]:
            active = None
        match = re.match(r"^\s*([^:\s]+)\s*:\s*(?:\{\}|\s*)$", line)
        if match is None:
            continue
        key = match.group(1).strip("'\"")
        normalized = re.sub(r"[^a-z]", "", key.lower())
        if normalized in _SECTION_KIND:
            active = (indent, _SECTION_KIND[normalized], None)
        elif active is not None:
            section_indent, kind, member_indent = active
            if member_indent is None or indent == member_indent:
                if (name := _safe_name(key)) is not None:
                    found[kind].add(name)
                active = (section_indent, kind, indent)
    return None


def _directory_names(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        for index, child in enumerate(path.iterdir()):
            if index >= MAX_DIRECTORY_ENTRIES:
                break
            if child.name.startswith("."):
                continue
            candidate = child.stem if child.is_file() else child.name
            if (name := _safe_name(candidate)) is not None:
                names.add(name)
    except OSError:
        pass
    return names


def _detected_harnesses(
    registry: Mapping[str, Any], env: Mapping[str, str], home: Path
) -> dict[str, str]:
    detection_env = environment_with_user_bins(env, home)
    detected = {
        harness_id: binary
        for harness_id in registry.get("harnesses", {})
        if (binary := resolve_harness_binary(harness_id, detection_env, home)) is not None
    }
    if "copilot" not in detected and (
        binary := shutil.which("copilot", path=detection_env.get("PATH"))
    ):
        detected["copilot"] = str(Path(binary).resolve())
    return detected


def build_inventory(
    registry: Mapping[str, Any],
    env: Mapping[str, str],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Inspect known config surfaces for detected harnesses without retaining values."""

    home = Path(env.get("HOME", "~")).expanduser()
    harnesses: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for harness_id, binary in sorted(_detected_harnesses(registry, env, home).items()):
        found: dict[str, set[str]] = {kind: set() for kind in CAPABILITY_KINDS}
        sources: list[str] = []
        profile = PROFILES.get(harness_id, HarnessProfile())
        for spec in profile.files:
            path = _path(spec, env, home)
            if not path.is_file():
                continue
            display = _display_path(path, home)
            sources.append(display)
            error = (
                _read_yaml(path, found)
                if path.suffix in {".yaml", ".yml"}
                else _read_structured(path, found)
            )
            if error is not None:
                errors.append({"harness": harness_id, "path": display, "reason": error})
        for kind, spec in profile.directories:
            path = _path(spec, env, home)
            if not path.is_dir():
                continue
            sources.append(_display_path(path, home))
            found[kind].update(_directory_names(path))
        harnesses.append(
            {
                "id": harness_id,
                "installed": _display_path(Path(binary), home),
                "sources": sorted(dict.fromkeys(sources)),
                "capabilities": {
                    kind: sorted(found[kind])[:MAX_NAMES_PER_KIND] for kind in CAPABILITY_KINDS
                },
                "mcpChannel": mcp_channel_registered(harness_id, env, home),
            }
        )
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at or utc_now(),
        "harnesses": harnesses,
        "errors": errors,
    }


def render_context(inventory: Mapping[str, Any]) -> str:
    lines = [
        "# Local harness capability inventory",
        "",
        f"Generated: {inventory['generatedAt']}",
        "",
        "This is availability context, not authorization. Mention a capability in a delegated prompt only when the task needs it; the receiving harness still enforces its own policy and credentials.",
    ]
    labels = {**{kind: kind.title() for kind in CAPABILITY_KINDS}, "mcps": "MCP servers"}
    for harness in inventory["harnesses"]:
        lines.extend(
            ["", f"## {harness['id']}", "", f"Detected at: {_markdown_code(harness['installed'])}"]
        )
        if harness["sources"]:
            lines.append(
                "Config sources: " + ", ".join(_markdown_code(path) for path in harness["sources"])
            )
        if harness["id"] in CHANNEL_HARNESSES:
            if harness.get("mcpChannel"):
                lines.append(
                    "Orchestrator channel: MCP server "
                    f"{CHANNEL_SERVER_NAME} registered; interactive delivery unverified"
                )
            else:
                lines.append(
                    "Orchestrator channel: MCP server not registered (run `pitwall agents setup mcp`)"
                )
        populated = False
        for kind in CAPABILITY_KINDS:
            names = harness["capabilities"][kind]
            if names:
                populated = True
                lines.append(
                    f"- {labels[kind]}: " + ", ".join(_markdown_code(name) for name in names)
                )
        if not populated:
            lines.append("No recognized capability metadata was found.")
    if inventory["errors"]:
        lines.extend(["", "## Scan warnings", ""])
        for error in inventory["errors"]:
            lines.append(
                f"- {error['harness']}: {_markdown_code(error['path'])} ({error['reason']})"
            )
    return "\n".join(lines) + "\n"


def write_inventory(
    registry: Mapping[str, Any], env: Mapping[str, str]
) -> tuple[dict[str, Any], Path, Path]:
    inventory = build_inventory(registry, env)
    root = config_root(env)
    json_path = root / INVENTORY_JSON
    markdown_path = root / INVENTORY_MARKDOWN
    atomic_write_json(json_path, inventory)
    atomic_write_bytes(markdown_path, render_context(inventory).encode("utf-8"))
    return inventory, json_path, markdown_path


__all__ = [
    "CAPABILITY_KINDS",
    "CHANNEL_HARNESSES",
    "channel_entry_disabled",
    "CHANNEL_SERVER_NAME",
    "CHANNEL_YAML_SECTION",
    "INVENTORY_JSON",
    "INVENTORY_MARKDOWN",
    "MAX_CONFIG_BYTES",
    "PROFILES",
    "build_inventory",
    "channel_config_path",
    "mcp_channel_registered",
    "render_context",
    "write_inventory",
]
