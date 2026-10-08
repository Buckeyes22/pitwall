#!/usr/bin/env python3
"""Resolve the writable source targets for the Claude /distill command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

LEDGER_RELATIVE = {
    "pitwall": Path("plugins/claude/skills/subagent-model-routing/ledger"),
    "standalone": Path("plugins/pitwall/skills/subagent-model-routing/ledger"),
}
SKILL_RELATIVE = {
    "pitwall": Path("plugins/claude/skills/subagent-model-routing/SKILL.md"),
    "standalone": Path("plugins/pitwall/skills/subagent-model-routing/SKILL.md"),
}


class ResolutionError(ValueError):
    """A proposed distillation source is not an approved writable layout."""


def _canonical(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser())).resolve()


def _inside_plugin_cache(path: Path) -> bool:
    normalized = path.as_posix()
    return any(
        marker in normalized
        for marker in (
            "/.claude/plugins/",
            "/.codex/plugins/",
            "/.config/github-copilot/",
        )
    )


def _git_top(path: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
            timeout=5.0,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResolutionError(f"cannot inspect Git checkout {path}: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        detail = result.stderr.strip() or "not a Git checkout"
        raise ResolutionError(f"{path}: {detail}")
    return _canonical(Path(result.stdout.strip()))


def _validate(root: Path, *, layout: str, source: str) -> dict[str, object]:
    root = _canonical(root)
    if _inside_plugin_cache(root):
        raise ResolutionError(f"{root}: installed plugin caches are not writable source")
    if not root.is_dir():
        raise ResolutionError(f"{root}: directory does not exist")
    top = _git_top(root)
    if top != root:
        raise ResolutionError(f"{root}: Git top level is {top}, not the proposed root")
    if layout in {"pitwall", "standalone"}:
        component = root
    else:  # pragma: no cover - callers enumerate the two supported layouts
        raise ResolutionError(f"unsupported source layout: {layout}")
    component = _canonical(component)
    ledger = component / LEDGER_RELATIVE[layout]
    skill = component / SKILL_RELATIVE[layout]
    if not ledger.is_dir() or not skill.is_file():
        raise ResolutionError(
            f"{root}: expected ledger and SKILL.md are absent for {layout} layout"
        )
    return {
        "source": source,
        "layout": layout,
        "root": str(root),
        "componentRoot": str(component),
        "ledgerDir": str(ledger),
        "skill": str(skill),
    }


def _current_git_candidate(cwd: Path) -> tuple[Path, str] | None:
    try:
        root = _git_top(cwd)
    except ResolutionError:
        return None
    if (root / LEDGER_RELATIVE["pitwall"]).is_dir():
        return root, "pitwall"
    if (root / LEDGER_RELATIVE["standalone"]).is_dir():
        return root, "standalone"
    return root, "unknown"


def resolve(env: Mapping[str, str], *, cwd: Path) -> dict[str, object]:
    """Resolve current Git, then the Pitwall home."""

    rejected: list[str] = []
    current = _current_git_candidate(cwd)
    if current is not None:
        root, layout = current
        try:
            if layout == "unknown":
                raise ResolutionError(f"{root}: no supported Agent Routing source layout")
            result = _validate(root, layout=layout, source="current-git")
            result["durablePayload"] = False
            return result
        except ResolutionError as exc:
            rejected.append(f"current-git: {exc}")

    home = _canonical(Path(env.get("HOME", "~")))
    new_raw = env.get("PITWALL_AGENTS_HOME", "")
    new_home = Path(new_raw) if new_raw else home / ".local/share/pitwall"

    try:
        result = _validate(new_home, layout="pitwall", source="pitwall-home")
        result["durablePayload"] = True
        return result
    except ResolutionError as exc:
        rejected.append(f"pitwall-home: {exc}")

    raise ResolutionError("no writable distillation source qualified:\n- " + "\n- ".join(rejected))


def main() -> int:
    try:
        result = resolve(os.environ, cwd=Path.cwd())
    except ResolutionError as exc:
        print(f"distill source resolution failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
