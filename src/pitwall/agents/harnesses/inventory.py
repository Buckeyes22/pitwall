"""Read-only inventory of every registered harness: installed, version, contract facts, route usage."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..process import run_bounded_capture
from ..profiles import entry_harness
from ..setup import environment_with_user_bins, resolve_harness_binary

PROBE_TIMEOUT = 5.0
PROBE_MAX_BYTES = 64 * 1024
VERSION_WIDTH = 60


@dataclass(frozen=True, slots=True)
class HarnessRow:
    id: str
    display_name: str
    installed: str | None
    version: str | None
    kind: str
    endpoint_delivery: str
    effort: str
    default_model_source: str
    routes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "id": payload["id"],
            "displayName": payload["display_name"],
            "installed": payload["installed"],
            "version": payload["version"],
            "kind": payload["kind"],
            "endpointDelivery": payload["endpoint_delivery"],
            "effort": payload["effort"],
            "defaultModelSource": payload["default_model_source"],
            "routes": list(payload["routes"]),
        }


def effort_label(harness: Mapping[str, Any]) -> str:
    effort = harness["effort"]
    kind, key, values = effort["kind"], effort.get("key"), list(effort.get("values") or [])
    if kind == "none" or not key:
        return "-"
    joined = "|".join(values) if values else "<harness-defined>"
    return f"{key} {joined}" if kind == "harness-flag" else f"-c {key}={joined}"


def probe_version(
    binary: str,
    verify_args: Sequence[str],
    env: Mapping[str, str],
    *,
    timeout_seconds: float = PROBE_TIMEOUT,
) -> str | None:
    try:
        result = run_bounded_capture(
            [binary, *verify_args],
            env=dict(env),
            timeout_seconds=timeout_seconds,
            max_bytes=PROBE_MAX_BYTES,
        )
    except OSError, ValueError:
        return None
    if getattr(result, "timed_out", False) or result.returncode != 0:
        return None
    for stream in (result.stdout or b"", result.stderr or b""):
        for line in stream.decode("utf-8", errors="replace").splitlines():
            if line.strip():
                return line.strip()[:VERSION_WIDTH]
    return None


def inspect_harnesses(
    registry: Mapping[str, Any],
    installers: Mapping[str, Any],
    routes_config: Mapping[str, Any] | None,
    env: Mapping[str, str],
    home: Path,
    *,
    probe: Callable[..., str | None] = probe_version,
) -> list[HarnessRow]:
    detection_env = environment_with_user_bins(env, home)
    usage: dict[str, list[str]] = {harness_id: [] for harness_id in registry["harnesses"]}
    if routes_config:
        for name, entry in routes_config["models"].items():
            usage.setdefault(entry_harness(entry, routes_config["defaults"], registry), []).append(
                name
            )
    rows: list[HarnessRow] = []
    for harness_id in sorted(registry["harnesses"]):
        harness = registry["harnesses"][harness_id]
        installed = resolve_harness_binary(harness_id, detection_env, home)
        recipe = installers.get("harnesses", {}).get(harness_id, {})
        verify_args = tuple(recipe.get("verifyArgs") or ("--version",))
        version = probe(installed, verify_args, detection_env) if installed else None
        rows.append(
            HarnessRow(
                id=harness_id,
                display_name=str(harness["displayName"]),
                installed=installed,
                version=version,
                kind=str(harness["harnessKind"]),
                endpoint_delivery=str(harness["endpointDelivery"]),
                effort=effort_label(harness),
                default_model_source=str(harness["defaultModel"]["source"]),
                routes=tuple(sorted(usage.get(harness_id, []))),
            )
        )
    return rows


def render_table(rows: Sequence[HarnessRow]) -> str:
    header = ("harness", "installed", "version", "kind", "endpoint", "effort", "routes")
    cells = [
        (
            row.id,
            row.installed or "-",
            row.version or "-",
            row.kind,
            row.endpoint_delivery,
            row.effort,
            str(len(row.routes)),
        )
        for row in rows
    ]
    widths = [
        max(len(header[index]), *(len(cell[index]) for cell in cells))
        if cells
        else len(header[index])
        for index in range(len(header))
    ]
    lines = ["  ".join(text.ljust(widths[index]) for index, text in enumerate(header)).rstrip()]
    lines.extend(
        "  ".join(text.ljust(widths[index]) for index, text in enumerate(cell)).rstrip()
        for cell in cells
    )
    return "\n".join(lines) + "\n"
