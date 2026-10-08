"""``pitwall mcp install`` / ``pitwall mcp uninstall``: harness registration CLI (plan Task 5).

Kept import-light on purpose: this module must never require the MCP server's
own runtime environment (``DATABASE_URL``, ``REDIS_URL``, ``RUNPOD_API_KEY``)
just to register or unregister the server with a harness.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from pitwall.cli.output import Output, json_mode
from pitwall.cli.runtime_errors import runtime_reason
from pitwall.mcp_install import (
    SERVER_NAME,
    VERIFY_HINT,
    InstallPlan,
    McpInstallError,
    Scope,
    apply_plan,
    config_path,
    detect_harnesses,
    plan_registration,
    render_snippet,
    server_command,
)

_RUNTIME_NOTE = (
    "the broker server needs DATABASE_URL, REDIS_URL, and RUNPOD_API_KEY where the harness runs; "
    "pitwall doctor checks them"
)


def cmd_mcp_install(args: argparse.Namespace) -> int:
    """Implement ``pitwall mcp install|uninstall`` from parsed argparse args."""
    out = Output(json_mode(args))
    environ = os.environ
    home = Path(environ.get("HOME") or Path.home())
    project_root = Path(args.project_root).resolve()
    remove = args.command == "uninstall"

    harnesses: list[str] = list(args.harnesses) or detect_harnesses(
        environ=environ, home=home, project_root=project_root
    )
    if not harnesses:
        message = "no supported harness found (claude-code, codex, opencode); name one explicitly"
        if out.json_mode:
            out.set_json({"results": [], "error": message})
            out.emit()
        else:
            _stderr_line(message)
        return 1

    command = server_command()
    results: list[dict[str, Any]] = []
    ok = True
    for harness in harnesses:
        result = _install_one(
            out,
            harness,
            args.scope,
            environ=environ,
            home=home,
            project_root=project_root,
            command=command,
            remove=remove,
            force=args.force,
            dry_run=args.dry_run,
        )
        if result["error"] is not None:
            ok = False
        results.append(result)

    registered = [str(one["harness"]) for one in results if one["error"] is None]
    channel_code = _register_channel(
        args, registered, remove=remove, json_mode=out.json_mode, project_root=project_root
    )

    if not remove and not environ.get("DATABASE_URL"):
        out.print(_RUNTIME_NOTE, soft_wrap=True)

    if out.json_mode:
        out.set_json({"results": results, "channel": {"exit_code": channel_code}})
        out.emit()
    return 0 if ok and channel_code == 0 else 1


def _register_channel(
    args: argparse.Namespace,
    harnesses: list[str],
    *,
    remove: bool,
    json_mode: bool,
    project_root: Path,
) -> int:
    """Register (or remove) the channel server beside the broker server, without prompting.

    *harnesses* are those whose broker step succeeded, and the channel follows the same scope.
    """
    from pitwall.agents.cli import setup_mcp

    channel_names = {"claude-code": "claude", "codex": "codex", "opencode": "opencode"}
    selected = [channel_names[name] for name in harnesses if name in channel_names]
    if not selected:
        return 0
    # --json keeps stdout a single document: the channel report goes to stderr.
    with contextlib.redirect_stdout(sys.stderr if json_mode else sys.stdout):
        print("== channel server (pitwall mcp serve channel)")
        return setup_mcp(
            selected,
            None,
            remove,
            args.dry_run,
            True,
            scope=args.scope,
            project_root=project_root,
        )


def _install_one(
    out: Output,
    harness: str,
    scope: Scope,
    *,
    environ: Any,
    home: Path,
    project_root: Path,
    command: list[str],
    remove: bool,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "harness": harness,
        "scope": scope,
        "path": None,
        "changed": False,
        "dry_run": dry_run,
        "backup": None,
        "commands": [],
        "verify": None,
        "error": None,
    }
    try:
        path = config_path(harness, scope, environ=environ, home=home, project_root=project_root)
    except McpInstallError as exc:
        out.print(f"== {harness} ({scope})", soft_wrap=True)
        result["error"] = str(exc)
        _report_error(out, exc)
        return result
    result["path"] = str(path)
    out.print(f"== {harness} ({scope}): {path}", soft_wrap=True)
    try:
        plan = plan_registration(
            harness,
            scope,
            environ=environ,
            home=home,
            project_root=project_root,
            command=command,
            remove=remove,
            force=force,
        )
    except (McpInstallError, OSError) as exc:
        result["error"] = _error_text(exc)
        _report_error(out, exc)
        return result

    _describe_plan(out, plan, remove=remove, command=command)
    result["commands"] = [list(one) for one in plan.commands]
    result["changed"] = plan.changed
    if dry_run:
        return result

    try:
        backup = apply_plan(plan)
    except (McpInstallError, OSError) as exc:
        result["error"] = _error_text(exc)
        _report_error(out, exc)
        return result

    result["backup"] = str(backup) if backup else None
    if plan.changed:
        suffix = f" (backup {backup})" if backup else ""
        out.print(f"wrote {path}{suffix}", soft_wrap=True)
        hint = VERIFY_HINT.get((harness, scope))
        result["verify"] = hint
        if hint:
            out.print(f"verify: {hint}", soft_wrap=True)
    return result


def _describe_plan(out: Output, plan: InstallPlan, *, remove: bool, command: list[str]) -> None:
    """Show only what pitwall changes: never echo the rest of a harness config.

    Harness configs can hold other servers' literal credentials, and this
    output often lands in an agent's transcript, so no diff of the file is
    printed.
    """
    if plan.before != plan.after:
        if remove:
            out.print(f"removes the {SERVER_NAME!r} server from {plan.path}", soft_wrap=True)
        else:
            out.print(f"adds this {SERVER_NAME!r} server to {plan.path}:", soft_wrap=True)
            out.print(render_snippet(plan.harness, plan.scope, command), soft_wrap=True)
            if plan.before.strip() and plan.harness != "codex":
                out.print(
                    "existing entries are kept; the file is rewritten with 2-space indentation",
                    soft_wrap=True,
                )
    for one in plan.commands:
        out.print("  $ " + shlex.join(one), soft_wrap=True)
    if not plan.changed:
        out.print("not registered" if remove else "already registered")


def _stderr_line(message: str) -> None:
    """One unwrapped stderr line: a boxed panel truncates the remedy at 80 columns."""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def _error_text(exc: McpInstallError | OSError) -> str:
    """The message for *exc*; an unwritable config path names the fix instead of an errno."""
    if isinstance(exc, McpInstallError):
        return str(exc)
    return runtime_reason(exc) or f"{type(exc).__name__}: {exc.strerror or exc}"


def _report_error(out: Output, exc: McpInstallError | OSError) -> None:
    if not out.json_mode:
        _stderr_line(f"error: {_error_text(exc)}")
