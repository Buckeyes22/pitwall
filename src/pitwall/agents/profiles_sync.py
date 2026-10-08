"""Materialize endpoint routes into harnesses that only read their own config files."""

from __future__ import annotations

import difflib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .broker import resolve_pitwall_api_token
from .errors import ProfileSyncError
from .harnesses import get_adapter
from .profiles_resolve import effective_harness

SYNC_COMMAND_TIMEOUT_SECONDS = 300


@dataclass(frozen=True, slots=True)
class SyncPlan:
    harness: str
    path: Path
    before: str
    after: str
    routes: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...] = ()

    @property
    def changed(self) -> bool:
        return self.before != self.after or bool(self.commands)


def plan_sync(
    routes_config: Mapping[str, Any],
    *,
    registry: Mapping[str, Any],
    env: Mapping[str, str],
    home: Path,
    harness: str | None = None,
) -> list[SyncPlan]:
    # Adapters filter on entry["harness"], so every route carries its effective harness: a route
    # inheriting defaults.endpointHarness is synced exactly like one that pins it.
    entries = {
        name: {
            **entry,
            "harness": effective_harness(entry, routes=routes_config, registry=registry),
        }
        for name, entry in routes_config["models"].items()
        if "endpoint" in entry
    }
    plans: list[SyncPlan] = []
    for harness_id, harness_def in registry["harnesses"].items():
        if harness_def["endpointDelivery"] != "config-sync" or (
            harness is not None and harness_id != harness
        ):
            continue
        adapter = get_adapter(harness_id)
        try:
            rendered = adapter.plan_endpoint_sync(entries, env, home)
        except NotImplementedError:
            path = Path(os.path.expandvars(os.path.expanduser(harness_def["configSync"]["path"])))
            before = path.read_text(encoding="utf-8") if path.exists() else ""
            rendered = (path, before, before)
        rendered_plans = rendered if isinstance(rendered, list) else [rendered]
        commands = tuple(
            tuple(command) for command in adapter.endpoint_sync_commands(entries, env, home)
        )
        for index, (path, before, after) in enumerate(rendered_plans):
            plans.append(
                SyncPlan(
                    harness_id,
                    path,
                    before,
                    after,
                    tuple(sorted(entries)),
                    commands if index == 0 else (),  # a harness's commands run once, not per file
                )
            )
    return plans


def render_diff(plan: SyncPlan) -> str:
    rendered = "".join(
        difflib.unified_diff(
            plan.before.splitlines(keepends=True),
            plan.after.splitlines(keepends=True),
            fromfile=str(plan.path),
            tofile=f"{plan.path} (synced)",
        )
    )
    if plan.commands:
        rendered += "commands:\n" + "".join(
            f"  {shlex.join(command)}\n" for command in plan.commands
        )
    return rendered


def apply_plan(plan: SyncPlan) -> Path | None:
    """Write the plan atomically, preserving an existing file's mode. Returns the backup path."""
    if not plan.changed:
        return None
    backup: Path | None = None
    if plan.before != plan.after:
        mode = 0o600
        if plan.path.exists():
            mode = plan.path.stat().st_mode & 0o777
            backup = plan.path.with_name(
                f"{plan.path.name}.bak.{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
            )
            shutil.copy2(plan.path, backup)
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{plan.path.name}.", dir=plan.path.parent)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(plan.after)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, plan.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    placeholder = re.compile(r"\{env:([A-Z][A-Z0-9_]*)\}")
    for command in plan.commands:
        resolved_secrets: list[str] = []

        def resolve_placeholder(
            match: re.Match[str], resolved_secrets: list[str] = resolved_secrets
        ) -> str:
            value, _resolved_env = resolve_pitwall_api_token(
                os.environ,
                match.group(1),
            )
            if not value:
                raise KeyError(match.group(1))
            resolved_secrets.append(value)
            return value

        try:
            argv = [placeholder.sub(resolve_placeholder, item) for item in command]
        except KeyError as exc:
            variable = str(exc.args[0])
            raise ProfileSyncError(
                f"{variable} is not set; export it before profiles sync"
            ) from exc
        _run_sync_command(argv, command, resolved_secrets)
    return backup


def _run_sync_command(argv: list[str], command: Sequence[str], secrets: Sequence[str]) -> None:
    """Run one harness sync command with an explicit environment and no terminal input.

    The child gets a copy of this process's environment (harness CLIs read their
    own credentials from it), no stdin, and no stdout; stderr is captured so a
    failure can be reported with resolved secrets removed.
    """
    try:
        completed = subprocess.run(
            argv,
            check=False,
            timeout=SYNC_COMMAND_TIMEOUT_SECONDS,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProfileSyncError(
            f"route sync command timed out after {SYNC_COMMAND_TIMEOUT_SECONDS}s: "
            f"{shlex.join(command)}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")
        for secret in secrets:
            detail = detail.replace(secret, "<redacted>")
        detail = detail.strip()[-300:]
        raise ProfileSyncError(
            f"route sync command exited {completed.returncode}: {shlex.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
