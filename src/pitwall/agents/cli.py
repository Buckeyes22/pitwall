"""Command-line interface for dispatch and run inspection."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from pitwall.providers.model_studio import catalog as model_studio

from .broker import (
    PITWALL_SUBSCRIPTION_TOKEN_ENV,
    PITWALL_TOKEN_ENV,
    PITWALL_URL_ENV,
    PitwallError,
    PitwallSyncError,
    create_subscription,
    fetch_capability,
    parse_interval,
    proxy_base_url,
    read_channel_inbox,
    record_sync,
    refreshed_entry,
    resolve_pitwall_api_token,
    resolve_subscription_token,
    run_receiver,
    webhook_secret_export_env,
)
from .capability_inventory import write_inventory
from .channel import answer_ask
from .dispatch import dispatch_legacy, resume_legacy
from .doctor import run_doctor
from .endpoints import DiscoveryError, discover_models
from .errors import EX_CONFIG, ProfileConfigError, ProfileSyncError, UsageError
from .execution import ExecutionError, child_execution, distribution_version
from .harnesses.inventory import inspect_harnesses, render_table
from .installation import python_requirement, supported_python
from .mailbox import STEER_KINDS, MailboxError
from .migrate_env import legacy_env_refusal
from .profiles import (
    ENV_NAME,
    SEATS,
    SECRET_KEY,
    TASK_MODES,
    WORKSPACES,
    ProfilesError,
    add_profile,
    load_profiles,
    parse_auto_serve,
    parse_limits,
    remove_profile,
    resolved_endpoint,
    save_profiles,
)
from .profiles_probe import DEFAULT_TIMEOUT, probe_profile
from .profiles_resolve import describe_profile, dispatch_profile, resolve_profile
from .profiles_setup import run_profiles_setup
from .profiles_sync import apply_plan, plan_sync, render_diff
from .registry import RegistryError, load_registry
from .resources import ResourceError, read_resource_json
from .run_store import (
    TERMINAL_STATES,
    cleanup_runs,
    find_run,
    list_runs,
    reconcile_run,
    terminate_harness,
)
from .scheduler import (
    WorkflowRunError,
    cancel_workflow,
    list_workflows,
    resume_workflow,
    run_workflow,
    show_workflow,
)
from .setup import HarnessSetupError, run_harness_setup, run_pi_setup
from .workflow import WorkflowError
from .workspace import WorkspaceError, apply_run, discard_run, inspect_run, load_worktree_metadata


def _runs_list() -> int:
    for path in list_runs(os.environ):
        with contextlib.suppress(OSError, ValueError):
            reconcile_run(os.environ, path)
        result_path = path / "result.json"
        run_path = path / "run.json"
        try:
            value = json.loads(
                (result_path if result_path.is_file() else run_path).read_text(encoding="utf-8")
            )
            print(
                f"{path.name}\t{value.get('provider', '-')}\t{value.get('model', '-')}\t{value.get('status', value.get('state', '-'))}"
            )
        except OSError, json.JSONDecodeError:
            print(f"{path.name}\t-\t-\tcorrupt")
    return 0


def _runs_show(dispatch_id: str) -> int:
    try:
        path = find_run(os.environ, dispatch_id)
    except FileNotFoundError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    with contextlib.suppress(OSError, ValueError):
        reconcile_run(os.environ, path)
    target = path / "result.json"
    if not target.is_file():
        target = path / "run.json"
    try:
        sys.stdout.write(target.read_text(encoding="utf-8"))
        return 0
    except OSError as exc:
        print(f"pitwall agents: cannot read run: {exc}", file=sys.stderr)
        return 1


def _runs_logs(dispatch_id: str, channel: str) -> int:
    try:
        path = find_run(os.environ, dispatch_id)
    except FileNotFoundError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    names = ["stdout.log", "stderr.log"] if channel == "both" else [f"{channel}.log"]
    for name in names:
        target = path / name
        try:
            sys.stdout.buffer.write(target.read_bytes())
        except OSError as exc:
            print(f"pitwall agents: cannot read {name}: {exc}", file=sys.stderr)
            return 1
    return 0


def _parse_age(value: str) -> float:
    raw = value.strip().lower()
    if raw.endswith("d"):
        raw = raw[:-1]
    days = float(raw)
    if days < 0:
        raise argparse.ArgumentTypeError("age must be non-negative")
    return days


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _runs_cleanup(days: float | None, remove_all: bool) -> int:
    if not remove_all and days is None:
        print(
            "pitwall agents: runs cleanup requires --older-than DAYS or --all",
            file=sys.stderr,
        )
        return 2
    removed = cleanup_runs(
        os.environ,
        older_than_seconds=None if days is None else days * 86400,
        remove_all=remove_all,
    )
    for path in removed:
        print(path.name)
    return 0


def _runs_diff(dispatch_id: str) -> int:
    try:
        path = find_run(os.environ, dispatch_id)
    except FileNotFoundError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    workspace_record = path / "workspace.json"
    if not workspace_record.is_file():
        print(
            f"pitwall agents: run {path.name} uses the shared workspace; "
            "inspect the files named by its asks in place",
            file=sys.stderr,
        )
        return 1
    try:
        run_state = str(json.loads((path / "run.json").read_text(encoding="utf-8")).get("state"))
    except OSError, json.JSONDecodeError:
        run_state = ""
    if run_state in ("running", "paused"):
        from .workspace import WorkspaceError, capture_changes

        try:
            capture_changes(os.environ, path.name)
        except WorkspaceError as exc:
            print(f"pitwall agents: keeping the last snapshot: {exc}", file=sys.stderr)
    target = path / "changes.patch"
    if not target.is_file():
        print(f"pitwall agents: run {path.name!r} has no captured changes", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(target.read_bytes())
    return 0


def _runs_apply(dispatch_id: str, target: Path, commits: bool) -> int:
    try:
        outcome = apply_run(os.environ, dispatch_id, target, apply_commits=commits)
    except WorkspaceError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            outcome.__dict__
            if hasattr(outcome, "__dict__")
            else {
                "status": outcome.status,
                "appliedAt": outcome.appliedAt,
                "target": outcome.target,
                "appliedCommits": outcome.appliedCommits,
                "conflictedFiles": outcome.conflictedFiles,
                "method": outcome.method,
                "identity": outcome.identity,
                "message": outcome.message,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if outcome.status == "conflicted" else 0


def _runs_discard(dispatch_id: str, yes: bool) -> int:
    try:
        metadata = load_worktree_metadata(os.environ, dispatch_id)
        if not yes:
            print(f"branch: {metadata.branch}")
            print(f"worktree: {metadata.path}")
            try:
                details = inspect_run(os.environ, dispatch_id).get("changeset") or {}
                print(f"changed files: {details.get('changedFileCount', 'unknown')}")
            except WorkspaceError:
                pass
            if not sys.stdin.isatty() or input(
                "Discard this owned worktree and branch? [y/N] "
            ).strip().lower() not in {"y", "yes"}:
                print("pitwall agents: discard cancelled", file=sys.stderr)
                return 2
        discard_run(os.environ, dispatch_id, yes=True)
        return 0
    except WorkspaceError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1


def _format_remaining(seconds: int) -> str:
    """Render a D1 deadline countdown for the inbox table."""
    if seconds < 0:
        return "expired"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _inbox(json_output: bool) -> int:
    """List unresolved asks and unacked steers across all runs.

    Same view the broker's ``GET /inbox`` serves; one schema, one builder.
    """
    inbox = read_channel_inbox(os.environ, None)
    asks = inbox["asks"]
    steers = inbox["steers"]
    if json_output:
        print(json.dumps(inbox, indent=2, sort_keys=True))
        return 0
    for gap in inbox.get("sequenceGaps", []):
        print(
            f"warning: {gap['dispatchId']} {gap['box']} sequence gap: {', '.join(gap['missing'])}",
            file=sys.stderr,
        )
    if not asks and not steers:
        print("inbox empty: nothing waits")
        return 0
    print("DISPATCH\tKIND\tID\tREMAINING\tSEVERITY\tTEXT")
    for ask in asks:
        remaining = (
            "expired"
            if ask.get("expired")
            else _format_remaining(cast(int, ask["deadlineRemainingS"]))
        )
        text = str(ask["question"]).replace("\n", " ")
        print(
            f"{ask['dispatchId']}\tASK\t{ask['askId']}\t{remaining}"
            f"\t{ask['severity']}/{ask['blockedOn']}\t{text[:80]}"
        )
    for steer in steers:
        text = str(steer["message"]).replace("\n", " ")
        remaining = "ignored" if steer.get("ignored") else "-"
        print(
            f"{steer['dispatchId']}\tSTEER\t{steer['steerId']}\t{remaining}\t{steer['kind']}\t{text[:80]}"
        )
    for dispatch_id in sorted({str(ask["dispatchId"]) for ask in asks}):
        print(f"context: pitwall agents runs diff {dispatch_id}")
    return 0


def _answer(
    dispatch_id: str,
    ask_id: str,
    choice: str,
    note: str | None,
    answered_by: str,
    json_output: bool,
) -> int:
    try:
        answer = answer_ask(
            os.environ,
            dispatch_id,
            ask_id,
            choice=choice,
            answered_by=answered_by,
            note=note,
            harness="operator",
        )
    except (FileNotFoundError, MailboxError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    if json_output:
        print(json.dumps(answer, indent=2, sort_keys=True))
    else:
        run_id = find_run(os.environ, dispatch_id).name
        print(f"answered {run_id}/{ask_id}: {answer['choice']} (by {answer['answered_by']})")
    return 0


def _steer_gate() -> int:
    from .steer_gate import run_stdin

    return run_stdin(sys.stdin.buffer.read(1024 * 1024), os.environ, sys.stdout, sys.stderr)


def _install(harnesses: list[str] | None, plugin_hosts: list[str] | None) -> int:
    from . import installation

    try:
        result = installation.install(
            os.environ,
            installation.default_home(os.environ),
            harnesses=harnesses,
            plugin_hosts=plugin_hosts,
        )
    except (installation.InstallationError, OSError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    print(f"installed {len(result.files)} files")
    if result.mcp_harnesses:
        print("registered the channel server with: " + ", ".join(result.mcp_harnesses))
    if result.skipped:
        print("skipped (CLI not found): " + "; ".join(result.skipped))
        print("install those CLIs, then run `pitwall agents install` again to register them")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _uninstall() -> int:
    from . import installation

    try:
        result = installation.uninstall(os.environ, installation.default_home(os.environ))
    except (installation.InstallationError, OSError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    print(f"removed {len(result.removed)} files")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _steer(args: Any) -> int:
    from .channel import send_steer

    try:
        steer = send_steer(
            os.environ,
            args.dispatch_id,
            kind=args.kind,
            message=args.message,
            requires_ack=not args.no_ack,
            deadline_s=args.deadline,
            harness="operator",
        )
    except (FileNotFoundError, MailboxError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(steer, indent=2, sort_keys=True))
    else:
        print(
            f"steer {steer['steer_id']} sent to {args.dispatch_id}: [{steer['kind']}] {steer['message']}"
        )
    return 0


def _runs_stop(dispatch_id: str, message: str | None, grace: int) -> int:
    from .channel import load_channel_config, send_steer

    try:
        run_path = find_run(os.environ, dispatch_id)
        state = str(json.loads((run_path / "run.json").read_text(encoding="utf-8")).get("state"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    try:
        reason = reconcile_run(os.environ, run_path)
    except (OSError, ValueError) as exc:
        print(f"pitwall agents: cannot reconcile run {run_path.name}: {exc}", file=sys.stderr)
        return 1
    if reason is not None:
        print(
            f"pitwall agents: run {run_path.name} was abandoned ({reason}); recorded as failed",
            file=sys.stderr,
        )
        return 1
    if state == "paused":
        print(
            f"pitwall agents: run {run_path.name} is paused; nothing is running (resume it or discard it)",
            file=sys.stderr,
        )
        return 1
    if state in TERMINAL_STATES:
        # A terminal record can still front a running harness (its supervisor was killed and
        # the reader that recorded the failure could not end it, e.g. it ran inside that
        # group): end it when the record names its process group. Records written before
        # process groups were recorded name none, so those harnesses are not reachable here.
        try:
            document = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"pitwall agents: {exc}", file=sys.stderr)
            return 1
        pgid = terminate_harness(document, float(grace)) if isinstance(document, dict) else None
        if pgid is not None:
            print(
                f"run {run_path.name} is {state}, but its harness (process group {pgid}) "
                "was still running; it has been stopped"
            )
            return 0
    text = message if message else "stop: wrap up the current work and report"
    try:
        steer = send_steer(
            os.environ,
            dispatch_id,
            kind="stop",
            message=text,
            requires_ack=True,
            deadline_s=grace,
            harness="operator",
        )
    except (FileNotFoundError, MailboxError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    if load_channel_config(run_path) is None:
        print(
            f"stop requested: steer {steer['steer_id']}; the run has no orchestrator channel, "
            f"so it is aborted in {grace}s"
        )
    else:
        print(
            f"stop requested: steer {steer['steer_id']}; the run is aborted if it has not "
            f"wrapped up within {grace}s"
        )
    return 0


def _doctor(
    harness: str | None,
    installation_only: bool,
    live_auth: bool,
    discover_models: bool,
    probe_routes: bool,
    json_output: bool,
) -> int:
    try:
        report = run_doctor(
            None,
            os.environ,
            harness=harness,
            installation_only=installation_only,
            live_auth=live_auth,
            discover_models=discover_models,
            probe_routes=probe_routes,
        )
    except ValueError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            scope = f"/{check['provider']}" if check.get("provider") else ""
            print(
                f"[{check['status']}] {check['category']}{scope} {check['id']}: {check['summary']}"
            )
            if check.get("remediation") and check["status"] != "PASS":
                print(f"  remediation: {check['remediation']}")
        print(f"doctor: {report['status']} ({report['summary']})")
    registry_failure = any(
        check["status"] == "FAIL" and check["id"].startswith("runtime.registry")
        for check in report["checks"]
    )
    if registry_failure:
        return 2
    return 1 if report["summary"]["fail"] else 0


def _setup_harnesses(dry_run: bool, no_color: bool) -> int:
    try:
        return run_harness_setup(
            os.environ,
            dry_run=dry_run,
            no_color=no_color,
        )
    except HarnessSetupError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2


def _setup_pi(dry_run: bool) -> int:
    try:
        return run_pi_setup(os.environ, dry_run=dry_run)
    except HarnessSetupError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2


def _setup_inventory(json_output: bool) -> int:
    try:
        inventory, json_path, markdown_path = write_inventory(_workflow_registry(), os.environ)
    except (OSError, RegistryError, ValueError) as exc:
        print(f"pitwall agents: cannot inventory harness capabilities: {exc}", file=sys.stderr)
        return 2
    if json_output:
        print(json.dumps(inventory, indent=2, sort_keys=True))
    else:
        capability_count = sum(
            len(names)
            for harness in inventory["harnesses"]
            for names in harness["capabilities"].values()
        )
        print(
            f"Inventoried {len(inventory['harnesses'])} detected harnesses and "
            f"{capability_count} capabilities."
        )
        print(f"  JSON: {json_path}")
        print(f"  Model context: {markdown_path}")
        if inventory["errors"]:
            print(f"  Warnings: {len(inventory['errors'])} config files could not be parsed.")
    return 0


def _workflow_registry() -> dict[str, object]:
    return load_registry()


def _harnesses(json_output: bool) -> int:
    try:
        registry = _workflow_registry()
        installers = read_resource_json("config/harness-installers.json")
    except (RegistryError, ResourceError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    home = _home_dir()
    try:
        config: dict[str, Any] | None = load_profiles(os.environ, registry=registry)
    except ProfilesError as exc:
        print(
            f"pitwall agents: agent profiles (pitwall.toml [agents.profiles]) not counted: {exc}",
            file=sys.stderr,
        )
        config = None
    rows = inspect_harnesses(registry, installers, config, os.environ, home)
    if json_output:
        print(json.dumps([row.to_dict() for row in rows], indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_table(rows))
    return 0


def _home_dir() -> Path:
    """The invoking user's home directory: ``$HOME``, else the account's home."""

    environment: Mapping[str, str] = os.environ
    return Path(environment.get("HOME", "~")).expanduser()


def _routes_context() -> tuple[dict[str, Any], dict[str, Any], Path]:
    registry = _workflow_registry()
    return (
        registry,
        load_profiles(os.environ, registry=registry),
        _home_dir(),
    )


def _routes_list(json_output: bool) -> int:
    try:
        registry, config, home = _routes_context()
    except (ProfilesError, RegistryError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    rows = [
        describe_profile(name, registry=registry, routes=config, env=os.environ, home=home)
        for name in config["models"]
    ]
    if json_output:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    for row in rows:
        expires_in = row["expiresIn"]
        if expires_in is None:
            expiry = "-"
        elif expires_in < 0:
            expiry = f"expired {abs(expires_in) // 60}m ago"
        else:
            expiry = f"expires in {expires_in // 60}m"
        print(
            f"{row['name']}\t{row['model']}\t{row['harness'] or '-'}\t{row['seat'] or '-'}\t{row['endpointHost'] or '-'}\t{row['syncStatus']}\t{row['status']}\t{expiry}\t{row['effort'] or '-'}"
        )
    return 0


def _routes_show(name: str) -> int:
    try:
        registry, config, home = _routes_context()
    except (ProfilesError, RegistryError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    if name not in config["models"]:
        print(f"pitwall agents: no route named {name!r}", file=sys.stderr)
        return 1
    entry = config["models"][name]
    payload: dict[str, object] = {
        "entry": entry,
        "summary": describe_profile(
            name, registry=registry, routes=config, env=os.environ, home=home
        ),
    }
    if isinstance(entry.get("endpoint"), str):
        payload["resolvedEndpoint"] = resolved_endpoint(entry)
    try:
        resolved = resolve_profile(
            name,
            registry=registry,
            routes=config,
            env=os.environ,
            home=home,
            prompt_source="<prompt>",
            caller_args=(),
        )
        payload["resolved"] = {
            **resolved.to_public_dict(),
            "argv": list(resolved.argv),
            "envKeys": list(resolved.env_keys),
            "nativeTo": list(resolved.native_to),
            "syncStatus": resolved.sync_status,
            "notices": list(resolved.notices),
            "effort": resolved.effort,
            "effortSource": resolved.effort_source,
        }
    except (ProfileConfigError, UsageError) as exc:
        payload["resolved"] = {"error": str(exc)}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _routes_probe(name: str, json_output: bool, timeout: float) -> int:
    try:
        registry, config, _home = _routes_context()
    except (ProfilesError, RegistryError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    if name not in config["models"]:
        print(f"pitwall agents: no route named {name!r}", file=sys.stderr)
        return 1
    result = probe_profile(name, config["models"][name], env=os.environ, timeout=timeout)
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"{name}: {result.status} — {result.detail}"
            + (f"\nlease: {result.lease_id}" if result.lease_id else "")
            + (f"\nremedy: {result.remedy}" if result.remedy else "")
        )
    return 0 if result.status in {"reachable", "warming", "not-applicable"} else 1


def _routes_discover(args: argparse.Namespace) -> int:
    name = args.name
    if name is not None and args.api_key_env is not None:
        print("pitwall agents: --api-key-env can only be used with --base-url", file=sys.stderr)
        return 2
    if name is not None:
        try:
            _registry, config, _home = _routes_context()
        except (ProfilesError, RegistryError) as exc:
            print(f"pitwall agents: {exc}", file=sys.stderr)
            return 2
        if name not in config["models"]:
            print(f"pitwall agents: no route named {name!r}", file=sys.stderr)
            return 1
        endpoint = config["models"][name].get("endpoint")
        if not endpoint:
            print(
                f"pitwall agents: route {name!r} has no endpoint; add one with --base-url",
                file=sys.stderr,
            )
            return 1
        base_url = str(endpoint["baseUrl"])
        key_env = endpoint.get("apiKeyEnv")
    else:
        base_url = str(args.base_url)
        key_env = args.api_key_env

    api_key = None
    if key_env:
        api_key, _resolved_key_env = resolve_pitwall_api_token(os.environ, str(key_env))
        if not api_key:
            if name is not None:
                message = (
                    f"route-shim: route {name!r} needs its configured API-key environment variable"
                )
            else:
                message = (
                    "profiles discover needs the --api-key-env variable set in the environment"
                )
            print(f"pitwall agents: {message}", file=sys.stderr)
            return 1
    try:
        result = discover_models(base_url, api_key=api_key, timeout=args.timeout)
    except DiscoveryError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        for model in result.models:
            print(f"{model.id}\t{model.state}")
    return 0


def _routes_refresh(args: argparse.Namespace) -> int:
    try:
        registry, config, home = _routes_context()
    except (ProfilesError, RegistryError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    if args.all_pitwall:
        names = [
            name
            for name, entry in config["models"].items()
            if (entry.get("origin") or {}).get("kind") == "pitwall"
        ]
    else:
        if args.name not in config["models"]:
            print(f"pitwall agents: no route named {args.name!r}", file=sys.stderr)
            return 1
        origin = config["models"][args.name].get("origin") or {}
        if origin.get("kind") != "pitwall":
            print(
                f"pitwall agents: route {args.name!r} has no Pitwall origin; recreate it with `pitwall agents profiles add`",
                file=sys.stderr,
            )
            return 2
        names = [args.name]

    failed = False
    for name in names:
        entry = config["models"][name]
        origin = entry.get("origin") or {}
        pitwall_url = (args.pitwall_url or str(origin["url"])).rstrip("/")
        key_env = str((entry.get("endpoint") or {}).get("apiKeyEnv") or PITWALL_TOKEN_ENV)
        token, token_env = resolve_pitwall_api_token(os.environ, key_env)
        if not token:
            print(
                "pitwall agents: the route's configured Pitwall token environment variable "
                "must be set (it is used for the capability lookup and never stored)",
                file=sys.stderr,
            )
            if args.all_pitwall:
                failed = True
                continue
            return 2
        try:
            info = fetch_capability(
                pitwall_url,
                str(origin["capability"]),
                token,
                token_env=token_env,
            )
            updated = {
                **config,
                "models": {
                    **config["models"],
                    name: refreshed_entry(entry, info, pitwall_url),
                },
            }
            save_profiles(os.environ, updated, registry=registry)
            config = load_profiles(os.environ, registry=registry)
            record_sync(os.environ, name, "refresh")
        except PitwallError as exc:
            print(f"pitwall agents: {exc}", file=sys.stderr)
            if args.all_pitwall:
                failed = True
                continue
            return 1
        except (OSError, ProfilesError, RegistryError) as exc:
            print(f"pitwall agents: {exc}", file=sys.stderr)
            if args.all_pitwall:
                failed = True
                continue
            return 2
        row = describe_profile(name, registry=registry, routes=config, env=os.environ, home=home)
        if args.json_output:
            print(json.dumps(row, indent=2, sort_keys=True))
        else:
            print(
                f"refreshed {name!r}: model {row['model']}; expiresAt {row.get('expiresAt') or 'none'}"
            )
    return 1 if failed else 0


def _routes_resolve(spec: str, json_output: bool) -> int:
    try:
        registry, config, home = _routes_context()
        resolved = resolve_profile(
            spec,
            registry=registry,
            routes=config,
            env=os.environ,
            home=home,
            prompt_source="<prompt>",
            caller_args=(),
        )
    except UsageError as exc:
        print(str(exc), file=sys.stderr)
        return 64
    except (ProfileConfigError, ProfilesError, RegistryError) as exc:
        print(str(exc), file=sys.stderr)
        return EX_CONFIG
    payload = {
        **resolved.to_public_dict(),
        "argv": list(resolved.argv),
        "envKeys": list(resolved.env_keys),
        "nativeTo": list(resolved.native_to),
        "syncStatus": resolved.sync_status,
        "notices": list(resolved.notices),
        "effort": resolved.effort,
        "effortSource": resolved.effort_source,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"{resolved.spec} -> {resolved.harness} {resolved.model}")
        print(f"effort={resolved.effort or '-'}")
        print("argv: " + " ".join(resolved.argv))
        print("env: " + (", ".join(resolved.env_keys) or "-"))
        print(f"native to: {', '.join(resolved.native_to) or '-'}; sync: {resolved.sync_status}")
        for notice in resolved.notices:
            print(notice)
    return 0


def _routes_add(args: argparse.Namespace) -> int:
    if args.api_key is not None:
        print(
            "pitwall agents: agent profiles (pitwall.toml [agents.profiles]) never store keys; pass --api-key-env VAR and export VAR instead (apiKeyEnv)",
            file=sys.stderr,
        )
        return 2
    if args.auto_serve is not None and not args.from_pitwall:
        print("pitwall agents: --auto-serve requires --from-pitwall", file=sys.stderr)
        return 2
    try:
        auto_serve = parse_auto_serve(args.auto_serve) if args.auto_serve is not None else None
        limits = parse_limits(args.limits) if args.limits is not None else None
    except ProfilesError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    try:
        registry, config, _home = _routes_context()
    except (ProfilesError, RegistryError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    existing = config["models"].get(args.name)
    if args.from_pitwall and existing is not None:
        existing_origin = existing.get("origin") or {}
        if (
            existing_origin.get("kind") == "pitwall"
            and existing_origin.get("capability") == args.from_pitwall
        ):
            print(
                f"pitwall agents: route {args.name!r} is already registered from Pitwall; "
                f"run `pitwall agents profiles refresh {args.name}`",
                file=sys.stderr,
            )
            return 4
        print(f"pitwall agents: route {args.name!r} already exists", file=sys.stderr)
        return 2
    extra_env: dict[str, str] = {}
    for item in args.env or []:
        key, separator, value = item.partition("=")
        if not separator or not key:
            print(f"pitwall agents: --env expects KEY=VALUE, got {item!r}", file=sys.stderr)
            return 2
        if ENV_NAME.fullmatch(key) is None:
            print(
                f"pitwall agents: --env name must be an UPPER_CASE environment variable, got {key!r}",
                file=sys.stderr,
            )
            return 2
        if SECRET_KEY.search(key):
            print(
                f"pitwall agents: --env {key} looks like an inline secret; agent profiles (pitwall.toml [agents.profiles]) only store apiKeyEnv names",
                file=sys.stderr,
            )
            return 2
        extra_env[key] = value
    expires_at = origin = None
    endpoint = args.endpoint
    base_url = args.base_url
    api_key_env = args.api_key_env
    model = args.model
    if endpoint is not None and (base_url is not None or api_key_env is not None):
        print(
            "pitwall agents: --endpoint is mutually exclusive with --base-url and --api-key-env",
            file=sys.stderr,
        )
        return 2
    if args.from_pitwall:
        if base_url or endpoint:
            print(
                "pitwall agents: --from-pitwall, --endpoint, and --base-url are mutually exclusive",
                file=sys.stderr,
            )
            return 2
        pitwall_url = (args.pitwall_url or os.environ.get(PITWALL_URL_ENV) or "").rstrip("/")
        if not pitwall_url:
            print(
                f"pitwall agents: set {PITWALL_URL_ENV} or pass --pitwall-url",
                file=sys.stderr,
            )
            return 2
        requested_api_key_env = api_key_env or PITWALL_TOKEN_ENV
        token, token_env = resolve_pitwall_api_token(os.environ, requested_api_key_env)
        if not token:
            print(
                "pitwall agents: the configured Pitwall token environment variable must be set "
                "(it is used for the capability lookup and never stored)",
                file=sys.stderr,
            )
            return 2
        api_key_env = requested_api_key_env
        base_url = proxy_base_url(pitwall_url, args.from_pitwall)
        lease_id = None
        if not model:
            try:
                info = fetch_capability(
                    pitwall_url,
                    args.from_pitwall,
                    token,
                    token_env=token_env,
                )
            except PitwallError as exc:
                print(f"pitwall agents: {exc}", file=sys.stderr)
                return 1
            if info.served_model_id is None:
                print(
                    "pitwall agents: Pitwall reports no served model for that capability; pass --model",
                    file=sys.stderr,
                )
                return 2
            model, expires_at, lease_id = (
                info.served_model_id,
                info.expires_at,
                info.lease_id,
            )
        origin = {
            "kind": "pitwall",
            "capability": args.from_pitwall,
            "leaseId": lease_id,
            "url": pitwall_url,
        }
    if not model:
        print("pitwall agents: profiles add requires --model or --from-pitwall", file=sys.stderr)
        return 2
    harness = args.harness
    if endpoint is not None:
        shared = config.get("endpoints", {}).get(endpoint)
        if isinstance(shared, Mapping) and shared.get("kind") == model_studio.KIND:
            if limits is None:
                try:
                    limits = model_studio.route_limits(model)
                except model_studio.ModelStudioConfigError as exc:
                    print(f"pitwall agents: {exc}", file=sys.stderr)
                    return 2
                if limits is None:
                    print(
                        f"pitwall agents: the catalog publishes no limits for {model!r}; pass --limits",
                        file=sys.stderr,
                    )
                    return 2
            harness = harness or str(config["defaults"]["endpointHarness"])
    try:
        updated = add_profile(
            config,
            args.name,
            model=model,
            harness=harness,
            endpoint=endpoint,
            base_url=base_url,
            api_key_env=api_key_env,
            args=tuple(args.arg or []),
            env=extra_env,
            limits=limits,
            seat=args.seat,
            effort=args.effort,
            workspace=args.workspace,
            task_mode=args.task_mode,
            expires_at=expires_at,
            origin=origin,
            auto_serve=auto_serve,
            account=args.account,
        )
        path = save_profiles(os.environ, updated, registry=registry)
    except (ProfilesError, RegistryError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    print(f"saved {args.name} to {path}")
    return 0


def _routes_add_model_studio_endpoint(args: argparse.Namespace) -> int:
    env = os.environ
    plan = args.plan or env.get("MODEL_STUDIO_PLAN")
    raw: dict[str, Any] = {
        "kind": model_studio.KIND,
        "apiKeyEnv": args.api_key_env or "MODEL_STUDIO_API_KEY",
    }
    for key, value in (
        ("plan", plan),
        ("tier", args.tier or env.get("MODEL_STUDIO_TIER")),
        ("region", args.region or env.get("MODEL_STUDIO_REGION")),
        ("workspace", args.model_studio_workspace or env.get("MODEL_STUDIO_WORKSPACE_ID")),
        ("protocol", args.protocol),
        ("renewsOn", args.renews_on or env.get("MODEL_STUDIO_RENEWS_ON")),
    ):
        if value:
            raw[key] = value
    if args.concurrency is not None:
        raw["concurrency"] = args.concurrency
    token_plan = (
        model_studio.load_catalog()["plans"].get(plan or "", {}).get("family") == "token-plan"
    )
    if token_plan and (
        args.accept_token_plan_automation
        or env.get(model_studio.AUTOMATION_ENV) == model_studio.AUTOMATION_ACCEPT
    ):
        raw["tokenPlanAutomation"] = model_studio.AUTOMATION_ACCEPT
    try:
        registry, config, _home = _routes_context()
        updated: dict[str, Any] = json.loads(json.dumps(config))
        updated.setdefault("endpoints", {})[args.name] = raw
        path = save_profiles(os.environ, updated, registry=registry)
    except (ProfilesError, RegistryError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    print(f"saved endpoint {args.name} to {path}")
    if token_plan and "tokenPlanAutomation" not in raw:
        print(
            "note: headless dispatch through this Token Plan endpoint is refused until you accept the "
            "automation risk (--accept-token-plan-automation); the Token Plan terms allow interactive use only",
        )
    return 0


def _routes_remove(name: str) -> int:
    try:
        registry, config, _home = _routes_context()
        save_profiles(os.environ, remove_profile(config, name), registry=registry)
    except ProfilesError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    except RegistryError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    print(f"removed {name}")
    return 0


def _routes_sync(harness: str | None, dry_run: bool, yes: bool) -> int:
    try:
        registry, config, home = _routes_context()
        plans = plan_sync(config, registry=registry, env=os.environ, home=home, harness=harness)
    except (ProfilesError, RegistryError, ProfileSyncError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, (ProfilesError, RegistryError)) else 1
    if not plans:
        print("no config-sync harness selected; nothing to do")
        return 0
    for plan in plans:
        print(f"== {plan.harness}: {plan.path} ({', '.join(plan.routes) or 'no endpoint routes'})")
        if not plan.changed:
            print("already synced")
            continue
        print(render_diff(plan), end="")
        if dry_run:
            continue
        if plan.harness == "cline":
            print(
                "WARNING: cline auth stores the key value in ~/.cline/data/settings/providers.json"
            )
        if not yes:
            if not sys.stdin.isatty():
                print(
                    "pitwall agents: refusing to write without confirmation; rerun with --yes",
                    file=sys.stderr,
                )
                return 2
            if input("Apply this change? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("skipped")
                continue
        backup = apply_plan(plan)
        print(f"wrote {plan.path}" + (f" (backup {backup})" if backup else ""))
    return 0


def _render_channel_preview(plan: Any, harness: str, command: str, remove: bool) -> str:
    """The path and the managed channel entry only, never the rest of the harness config.

    A unified diff would echo unchanged context lines, and harness configs can hold other
    servers' literal credentials.
    """
    from .capability_inventory import CHANNEL_SERVER_NAME
    from .mcp_registration import render_entry

    lines: list[str] = []
    if plan.before != plan.after:
        if remove:
            lines.append(f"removes the {CHANNEL_SERVER_NAME!r} server from {plan.path}")
        else:
            entry = render_entry(harness, command)
            text = entry.rstrip("\n") if isinstance(entry, str) else json.dumps(entry, indent=2)
            lines.append(f"adds this {CHANNEL_SERVER_NAME!r} server to {plan.path}:")
            lines.append(text)
    if plan.commands:
        lines.append("commands:")
        lines.extend(f"  {shlex.join(one)}" for one in plan.commands)
    return "".join(f"{line}\n" for line in lines)


def setup_mcp(
    harnesses: list[str] | None,
    command: str | None,
    remove: bool,
    dry_run: bool,
    yes: bool,
    *,
    scope: str = "user",
    project_root: Path | None = None,
) -> int:
    from .capability_inventory import CHANNEL_HARNESSES
    from .mcp_registration import (
        PROJECT_CHANNEL_FILES,
        RegistrationError,
        channel_server_command,
        plan_project_registration,
        plan_registration,
    )
    from .profiles_sync import apply_plan
    from .setup import resolve_harness_binary

    if scope == "project":
        unsupported = [name for name in harnesses or [] if name not in PROJECT_CHANNEL_FILES]
        if project_root is None or not harnesses or unsupported:
            print(
                "pitwall agents: project-scope channel registration supports only "
                f"{', '.join(sorted(PROJECT_CHANNEL_FILES))}"
                + (f"; {', '.join(unsupported)} register at user scope" if unsupported else ""),
                file=sys.stderr,
            )
            return 2

    if command is None:
        try:
            command = channel_server_command(os.environ)
        except RegistrationError as exc:
            print(f"pitwall agents: {exc}", file=sys.stderr)
            return 2
    home = _home_dir()
    detection_env = dict(os.environ)
    detection_env.setdefault("PATH", "/usr/bin:/bin")
    if harnesses:
        selected = list(harnesses)
    elif remove:
        selected = list(CHANNEL_HARNESSES)
    else:
        selected = [
            harness
            for harness in CHANNEL_HARNESSES
            if (
                resolve_harness_binary(harness, detection_env, home)
                if harness != "copilot"
                else shutil.which("copilot", path=detection_env.get("PATH"))
            )
            is not None
        ]
    exit_code = 0
    for harness in selected:
        try:
            if scope == "project":
                assert project_root is not None
                plan = plan_project_registration(harness, project_root, command, remove=remove)
            else:
                plan = plan_registration(harness, os.environ, home, command=command, remove=remove)
        except (RegistrationError, KeyError) as exc:
            if remove:
                print(f"skipped {harness}: {exc}")
                continue
            print(f"pitwall agents: {exc}", file=sys.stderr)
            return 2
        print(f"== {harness}: {plan.path}")
        if not plan.changed:
            print("not registered" if remove else "already registered")
            continue
        print(_render_channel_preview(plan, harness, command, remove), end="")
        if dry_run:
            continue
        if not yes:
            if not sys.stdin.isatty():
                print(
                    "pitwall agents: refusing to write without confirmation; rerun with --yes",
                    file=sys.stderr,
                )
                return 2
            if input("Apply this change? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("skipped")
                continue
        backup = apply_plan(plan)
        print(f"wrote {plan.path}" + (f" (backup {backup})" if backup else ""))
    return exit_code


def _workflow_run(path: Path, host: str) -> int:
    try:
        state = run_workflow(
            path,
            host=host,
            repo_root=Path.cwd(),
            env=os.environ,
            registry=_workflow_registry(),
        )
    except (
        ExecutionError,
        WorkflowError,
        WorkflowRunError,
        RegistryError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state["status"] == "succeeded" else 1


def _workflow_resume(workflow_id: str, host: str | None) -> int:
    try:
        state = resume_workflow(
            workflow_id,
            repo_root=Path.cwd(),
            env=os.environ,
            registry=_workflow_registry(),
            declared_host=host,
        )
    except (
        ExecutionError,
        WorkflowError,
        WorkflowRunError,
        RegistryError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state["status"] == "succeeded" else 1


def _workflow_list(json_output: bool) -> int:
    states = list_workflows(os.environ)
    if json_output:
        print(json.dumps(states, indent=2, sort_keys=True))
    else:
        for state in states:
            print(
                f"{state.get('workflowId', '-')}\t{state.get('status', '-')}\t{state.get('name', '-')}"
            )
    return 0


def _workflow_show(workflow_id: str) -> int:
    try:
        state = show_workflow(os.environ, workflow_id)
    except WorkflowRunError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def _workflow_cancel(workflow_id: str) -> int:
    try:
        state = cancel_workflow(os.environ, workflow_id)
    except WorkflowRunError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def _pitwall_install(host: str, port: int, enable: bool) -> int:
    try:
        descriptor = child_execution(os.environ)
    except ExecutionError as exc:
        print(f"pitwall agents: cannot generate receiver service: {exc}", file=sys.stderr)
        return 2
    command = shlex.join(
        [*descriptor.argv, "broker", "receiver", "--host", host, "--port", str(port)]
    )
    if sys.platform == "darwin":
        try:
            path = _write_protected_handoff(
                prefix="pitwall-agents-launchagent-",
                suffix=".command",
                contents=f"{command}\n",
            )
        except OSError:
            print(
                "pitwall agents: could not create the protected LaunchAgent command file",
                file=sys.stderr,
            )
            return 1
        print(
            f"Install a LaunchAgent using the protected command file {path} (mode 0600); "
            "delete the file after configuring ProgramArguments.\n"
            "Set the documented receiver credential in the launchd environment; "
            "credentials must not be placed in the plist."
        )
        return 0
    home = _home_dir()
    unit = home / ".config" / "systemd" / "user" / "pitwall-agents-broker.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\n"
        "Description=Pitwall agents broker webhook receiver\n\n"
        "[Service]\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    print(f"wrote {unit}")
    if enable:
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", unit.name],
            check=False,
        )
        return result.returncode
    print(f"run `systemctl --user enable --now {unit.name}` to enable it")
    return 0


def _pitwall_receiver(args: argparse.Namespace) -> int:
    if args.host not in {"127.0.0.1", "::1"}:
        print(
            "pitwall agents: receiver host must be loopback (127.0.0.1 or ::1)",
            file=sys.stderr,
        )
        return 2
    if args.enable and not args.install:
        print("pitwall agents: --enable requires --install", file=sys.stderr)
        return 2
    if args.install:
        return _pitwall_install(args.host, args.port, args.enable)
    try:
        run_receiver(args.host, args.port, os.environ, _workflow_registry())
    except (PitwallSyncError, OSError, RegistryError) as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


def _write_protected_handoff(*, prefix: str, suffix: str, contents: str) -> Path:
    """Write sensitive local handoff text to a fresh mode-0600 file."""

    descriptor, raw_path = tempfile.mkstemp(
        prefix=prefix,
        suffix=suffix,
        text=True,
    )
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    return path


def _pitwall_subscribe(receiver_url: str) -> int:
    pitwall_url = os.environ.get(PITWALL_URL_ENV, "").rstrip("/")
    token, _token_env = resolve_subscription_token(os.environ)
    if not pitwall_url or not token:
        print(
            f"pitwall agents: set {PITWALL_URL_ENV} and {PITWALL_SUBSCRIPTION_TOKEN_ENV}; "
            f"the token needs the webhook:admin scope",
            file=sys.stderr,
        )
        return 2
    secret_env = webhook_secret_export_env(os.environ)
    if ENV_NAME.fullmatch(secret_env) is None:
        print(
            "pitwall agents: the configured receiver credential environment name is invalid",
            file=sys.stderr,
        )
        return 2
    try:
        subscription = create_subscription(pitwall_url, token, receiver_url)
    except PitwallSyncError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    secret = subscription.get("signing_secret") or subscription.get("secret")
    if isinstance(secret, str) and secret:
        try:
            path = _write_protected_handoff(
                prefix="pitwall-agents-subscription-",
                suffix=".env",
                contents=f"export {secret_env}={shlex.quote(secret)}\n",
            )
        except OSError:
            print(
                "pitwall agents: could not create the protected subscription credential file",
                file=sys.stderr,
            )
            return 1
        print(
            f"subscription created; wrote one-time credential file {path} (mode 0600); "
            "source it, then delete it"
        )
    else:
        print("subscription created, but the API response did not include its signing credential")
    return 0


def _pitwall_watch(interval: float) -> int:
    stopping = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopping.set()

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    failed = False
    try:
        while not stopping.is_set():
            result = _routes_refresh(
                argparse.Namespace(
                    all_pitwall=True,
                    name=None,
                    pitwall_url=None,
                    json_output=False,
                )
            )
            failed = failed or result != 0
            stopping.wait(interval)
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
    return 1 if failed else 0


def _interval_argument(value: str) -> float:
    try:
        return parse_interval(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


class _VersionAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del namespace, values, option_string
        try:
            version = distribution_version(os.environ)
        except ExecutionError as exc:
            parser.exit(2, f"{parser.prog}: {exc}\n")
        print(f"pitwall agents {version}")
        parser.exit(0)


def _program_name() -> str:
    return "pitwall agents"


def _add_dispatch_commands(subparsers: Any) -> None:
    dispatch = subparsers.add_parser(
        "dispatch",
        help="Run one prompt through a harness or a named profile (what the shims call).",
    )
    dispatch.add_argument(
        "harness",
        choices=(
            "agy",
            "cline",
            "codex",
            "claude",
            "dsh",
            "goose",
            "grok",
            "hermes",
            "kimi",
            "muse",
            "opencode",
            "pi",
            "qwen",
            "zcode",
            "route",
        ),
    )
    dispatch.add_argument("harness_args", nargs=argparse.REMAINDER)
    internal = subparsers.add_parser(
        "_shim", add_help=False, help="Internal: entry point of the installed shims."
    )
    internal.add_argument(
        "harness",
        choices=(
            "agy",
            "cline",
            "codex",
            "claude",
            "dsh",
            "goose",
            "grok",
            "hermes",
            "kimi",
            "muse",
            "opencode",
            "pi",
            "qwen",
            "zcode",
            "route",
        ),
    )
    internal.add_argument("harness_args", nargs=argparse.REMAINDER)
    subparsers.add_parser(
        "_steer-gate", add_help=False, help="Internal: the steering gate the plugin hooks call."
    )
    install = subparsers.add_parser(
        "install",
        help="Write the shims, install the plugins, and register the channel MCP server.",
    )
    install.add_argument(
        "--harness",
        action="append",
        dest="harnesses",
        help="Register the channel server with this harness (repeatable; default: detected).",
    )
    install.add_argument(
        "--plugin-host",
        action="append",
        dest="plugin_hosts",
        help="Register the plugins with this host CLI (repeatable; default: found on PATH).",
    )
    subparsers.add_parser("uninstall", help="Remove exactly what `install` wrote.")
    subparsers.add_parser(
        "migrate", help="Report and move state left by the standalone Agent Routing tool."
    )


def _add_run_commands(subparsers: Any) -> None:
    runs = subparsers.add_parser(
        "runs", help="List, inspect, resume, stop, apply, and clean up dispatch runs."
    )
    run_commands = runs.add_subparsers(dest="runs_command", required=True)
    run_commands.add_parser("list", help="List recorded runs.")
    show = run_commands.add_parser("show", help="Show one run record.")
    show.add_argument("dispatch_id")
    logs = run_commands.add_parser("logs", help="Print a run's stdout and/or stderr.")
    logs.add_argument("dispatch_id")
    logs.add_argument("--channel", choices=("stdout", "stderr", "both"), default="stdout")
    cleanup = run_commands.add_parser(
        "cleanup", help="Remove old finished runs and their worktrees."
    )
    cleanup.add_argument("--older-than", type=_parse_age, metavar="DAYS")
    cleanup.add_argument("--all", action="store_true")
    diff = run_commands.add_parser("diff", help="Show the changes a worktree run made.")
    diff.add_argument("dispatch_id")
    apply = run_commands.add_parser(
        "apply", help="Apply a worktree run's changes to a target checkout."
    )
    apply.add_argument("dispatch_id")
    apply.add_argument("--target", type=Path, default=Path.cwd())
    apply.add_argument(
        "--commits",
        action="store_true",
        help="cherry-pick captured commits before applying working changes",
    )
    discard = run_commands.add_parser(
        "discard", help="Discard a worktree run's branch and worktree."
    )
    discard.add_argument("dispatch_id")
    discard.add_argument("--yes", action="store_true")
    resume = run_commands.add_parser(
        "resume", help="Resume a run paused for answers to its questions."
    )
    resume.add_argument("dispatch_id")
    stop = run_commands.add_parser(
        "stop", help="Ask a running dispatch to stop, then terminate it after a grace period."
    )
    stop.add_argument("dispatch_id")
    stop.add_argument("--message")
    stop.add_argument("--grace", type=int, default=60)
    steer = subparsers.add_parser(
        "steer", help="Send a steer (priority, scope, stop, ...) to a running dispatch."
    )
    steer.add_argument("dispatch_id")
    steer.add_argument("--kind", choices=sorted(STEER_KINDS), required=True)
    steer.add_argument("--message", required=True)
    steer.add_argument("--no-ack", action="store_true", dest="no_ack")
    steer.add_argument("--deadline", type=int, default=300)
    steer.add_argument("--json", action="store_true", dest="json_output")


def _add_doctor_commands(subparsers: Any) -> None:
    doctor = subparsers.add_parser(
        "doctor", help="Check the installation, harness CLIs, profiles, and channel; read-only."
    )
    doctor.add_argument("--json", action="store_true", dest="json_output")
    doctor.add_argument(
        "--harness",
        choices=(
            "agy",
            "cline",
            "codex",
            "claude",
            "dsh",
            "goose",
            "grok",
            "hermes",
            "kimi",
            "muse",
            "opencode",
            "pi",
            "qwen",
            "zcode",
        ),
    )
    doctor.add_argument("--installation-only", action="store_true")
    doctor.add_argument("--live-auth", action="store_true")
    doctor.add_argument("--discover-models", action="store_true")
    doctor.add_argument("--probe-routes", action="store_true")
    harnesses_parser = subparsers.add_parser(
        "harnesses", help="Show which harness CLIs are installed and usable."
    )
    harnesses_parser.add_argument("--json", action="store_true", dest="json_output")
    inbox_parser = subparsers.add_parser(
        "inbox", help="List the questions running dispatches are waiting on."
    )
    inbox_parser.add_argument("--json", action="store_true", dest="json_output")
    answer = subparsers.add_parser("answer", help="Answer a question a running dispatch asked.")
    answer.add_argument("dispatch_id")
    answer.add_argument("ask_id")
    answer.add_argument("choice")
    answer.add_argument("--note")
    answer.add_argument(
        "--by", choices=("operator", "orchestrator"), default="operator", dest="answered_by"
    )
    answer.add_argument("--json", action="store_true", dest="json_output")


def _add_setup_commands(subparsers: Any) -> None:
    setup = subparsers.add_parser(
        "setup",
        help="Install harness CLIs, Pi, the capability inventory, profiles, and MCP registration.",
    )
    setup_commands = setup.add_subparsers(dest="setup_command", required=True)
    setup_harnesses = setup_commands.add_parser(
        "harnesses",
        help="Interactively install missing harness CLIs (checksum-verified installers).",
    )
    setup_harnesses.add_argument("--dry-run", action="store_true")
    setup_harnesses.add_argument("--no-color", action="store_true")
    setup_pi = setup_commands.add_parser(
        "pi", help="Install the pinned Pi and pi-subagents that `pitwall workbench` requires."
    )
    setup_pi.add_argument("--dry-run", action="store_true")
    setup_inventory = setup_commands.add_parser(
        "inventory", help="Write the harness capability inventory."
    )
    setup_inventory.add_argument("--json", action="store_true", dest="json_output")
    setup_routes = setup_commands.add_parser(
        "profiles", help="Interactively create route profiles."
    )
    setup_mcp = setup_commands.add_parser(
        "mcp", help="Register or remove the channel MCP server in harness configs."
    )
    setup_mcp.add_argument("--harness", action="append", dest="harnesses")
    setup_mcp.add_argument("--command", dest="server_command")
    setup_mcp.add_argument("--remove", action="store_true")
    setup_mcp.add_argument("--dry-run", action="store_true")
    setup_mcp.add_argument("--yes", action="store_true")
    setup_routes.add_argument("--no-color", action="store_true")


def _add_routes_commands(subparsers: Any) -> None:
    routes_parser = subparsers.add_parser(
        "profiles", help="List, add, probe, and sync named route profiles from pitwall.toml."
    )
    routes_commands = routes_parser.add_subparsers(dest="routes_command", required=True)
    routes_list = routes_commands.add_parser("list", help="List profiles.")
    routes_list.add_argument("--json", action="store_true", dest="json_output")
    routes_show = routes_commands.add_parser("show", help="Show one profile.")
    routes_show.add_argument("name")
    routes_resolve = routes_commands.add_parser(
        "resolve", help="Resolve a profile spec to the harness, model, and endpoint it would use."
    )
    routes_resolve.add_argument("spec")
    routes_resolve.add_argument("--json", action="store_true", dest="json_output")
    routes_probe = routes_commands.add_parser("probe", help="Probe a profile's endpoint.")
    routes_probe.add_argument("name")
    routes_probe.add_argument("--json", action="store_true", dest="json_output")
    routes_probe.add_argument(
        "--timeout", type=_positive_float, default=DEFAULT_TIMEOUT, metavar="SECONDS"
    )
    routes_discover = routes_commands.add_parser(
        "discover", help="Discover the models an endpoint serves."
    )
    discover_target = routes_discover.add_mutually_exclusive_group(required=True)
    discover_target.add_argument("name", nargs="?")
    discover_target.add_argument("--base-url", dest="base_url")
    routes_discover.add_argument("--api-key-env", dest="api_key_env")
    routes_discover.add_argument("--json", action="store_true", dest="json_output")
    routes_discover.add_argument("--timeout", type=_positive_float, default=10.0, metavar="SECONDS")
    routes_refresh = routes_commands.add_parser(
        "refresh", help="Refresh profiles that came from a Pitwall broker."
    )
    refresh_target = routes_refresh.add_mutually_exclusive_group(required=True)
    refresh_target.add_argument("name", nargs="?")
    refresh_target.add_argument("--all-pitwall", action="store_true", dest="all_pitwall")
    routes_refresh.add_argument("--pitwall-url", dest="pitwall_url")
    routes_refresh.add_argument("--json", action="store_true", dest="json_output")
    routes_add = routes_commands.add_parser("add", help="Add or update a profile.")
    routes_add.add_argument("name")
    routes_add.add_argument("--model", required=False)
    routes_add.add_argument("--from-pitwall", metavar="CAPABILITY", dest="from_pitwall")
    routes_add.add_argument("--auto-serve", metavar="SPEC", dest="auto_serve")
    routes_add.add_argument("--pitwall-url", dest="pitwall_url")
    routes_add.add_argument("--harness")
    routes_add.add_argument("--endpoint")
    routes_add.add_argument("--base-url", dest="base_url")
    routes_add.add_argument("--api-key-env", dest="api_key_env")
    routes_add.add_argument("--api-key", dest="api_key", help=argparse.SUPPRESS)
    routes_add.add_argument("--arg", action="append")
    routes_add.add_argument("--limits", metavar="SPEC")
    routes_add.add_argument("--env", action="append", metavar="KEY=VALUE")
    routes_add.add_argument("--seat", choices=SEATS)
    routes_add.add_argument("--account", metavar="LABEL")
    routes_add.add_argument("--effort")
    routes_add.add_argument("--workspace", choices=WORKSPACES)
    routes_add.add_argument("--task-mode", dest="task_mode", choices=TASK_MODES)
    ms_endpoint = routes_commands.add_parser(
        "add-model-studio-endpoint", help="Add an Alibaba Model Studio endpoint."
    )
    ms_endpoint.add_argument("name")
    ms_endpoint.add_argument(
        "--plan", choices=("token-plan-personal", "token-plan-team", "pay-as-you-go")
    )
    ms_endpoint.add_argument("--api-key-env", dest="api_key_env")
    ms_endpoint.add_argument("--region")
    ms_endpoint.add_argument("--model-studio-workspace", dest="model_studio_workspace")
    ms_endpoint.add_argument("--protocol", choices=("openai", "anthropic"))
    ms_endpoint.add_argument("--tier")
    ms_endpoint.add_argument("--concurrency", type=int)
    ms_endpoint.add_argument("--renews-on", dest="renews_on", metavar="YYYY-MM-DD")
    ms_endpoint.add_argument(
        "--accept-token-plan-automation", action="store_true", dest="accept_token_plan_automation"
    )
    routes_remove = routes_commands.add_parser("remove", help="Remove a profile.")
    routes_remove.add_argument("name")
    routes_sync = routes_commands.add_parser(
        "sync", help="Write profiles into the harnesses configs that need them."
    )
    routes_sync.add_argument("--harness")
    routes_sync.add_argument("--dry-run", action="store_true")
    routes_sync.add_argument("--yes", action="store_true")


def _add_pitwall_and_workflow_commands(subparsers: Any) -> None:
    pitwall = subparsers.add_parser(
        "broker", help="Connect to a Pitwall broker: receiver, subscription, and watch."
    )
    pitwall_commands = pitwall.add_subparsers(dest="pitwall_command", required=True)
    pitwall_receiver = pitwall_commands.add_parser(
        "receiver", help="Run the local receiver for broker notifications."
    )
    pitwall_receiver.add_argument("--host", default="127.0.0.1")
    pitwall_receiver.add_argument("--port", type=int, default=8765)
    pitwall_receiver.add_argument("--install", action="store_true")
    pitwall_receiver.add_argument("--enable", action="store_true")
    pitwall_subscribe = pitwall_commands.add_parser(
        "subscribe", help="Subscribe a receiver URL to broker events."
    )
    pitwall_subscribe.add_argument("--receiver-url", required=True)
    pitwall_watch = pitwall_commands.add_parser(
        "watch", help="Poll the broker and refresh broker-derived profiles."
    )
    pitwall_watch.add_argument("--interval", type=_interval_argument, default=300.0)
    workflow = subparsers.add_parser(
        "workflow", help="Run, list, resume, and cancel dependency-ordered workflows."
    )
    workflow_commands = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_run = workflow_commands.add_parser("run", help="Run a workflow file.")
    workflow_run.add_argument("path", type=Path)
    workflow_run.add_argument("--host", choices=("claude", "codex", "copilot"), required=True)
    workflow_list = workflow_commands.add_parser("list", help="List workflows.")
    workflow_list.add_argument("--json", action="store_true", dest="json_output")
    workflow_show = workflow_commands.add_parser("show", help="Show one workflow.")
    workflow_show.add_argument("workflow_id")
    workflow_resume = workflow_commands.add_parser("resume", help="Resume a workflow.")
    workflow_resume.add_argument("workflow_id")
    workflow_resume.add_argument("--host", choices=("claude", "codex", "copilot"))
    workflow_cancel = workflow_commands.add_parser("cancel", help="Cancel a workflow.")
    workflow_cancel.add_argument("workflow_id")
    subparsers.add_parser(
        "mcp",
        help="Serve the channel MCP server over stdio (what `pitwall mcp serve channel` runs).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=_program_name())
    parser.add_argument("--version", action=_VersionAction, nargs=0)
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_dispatch_commands(subparsers)
    _add_run_commands(subparsers)
    _add_doctor_commands(subparsers)
    _add_setup_commands(subparsers)
    _add_routes_commands(subparsers)
    _add_pitwall_and_workflow_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    if not supported_python(sys.version_info):
        print(f"pitwall agents requires Python {python_requirement()}", file=sys.stderr)
        return 127
    requested = sys.argv[1:] if argv is None else argv
    if requested[:1] == ["migrate"] and not {"-h", "--help"} & set(requested):
        # migrate exists to report the legacy variables, so it must run when they are set.
        from . import migrate

        return migrate.main(requested[1:])
    refusal = legacy_env_refusal(os.environ)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
    # A non-interactive bash wrapper that waits on a command substitution
    # (``HERE="$(cd ... && pwd -P)"``) before ``exec`` leaves SIGINT ignored in
    # this image, and CPython preserves an inherited SIG_IGN for SIGINT — the
    # supervisor then cannot be interrupted at all. Every invocation owns its
    # SIGINT disposition: take it back whenever it was inherited as ignored.
    try:
        if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
            signal.signal(signal.SIGINT, signal.default_int_handler)
    except ValueError:
        pass  # not the main thread; this entry point cannot own signals
    args = build_parser().parse_args(argv)
    if args.command == "_steer-gate":
        return _steer_gate()
    if args.command == "install":
        return _install(args.harnesses, args.plugin_hosts)
    if args.command == "uninstall":
        return _uninstall()
    if args.command == "mcp":
        from . import mcp_server

        return mcp_server.main(os.environ)
    if args.command in {"dispatch", "_shim"}:
        if args.harness == "route":
            return dispatch_profile(args.harness_args)
        return dispatch_legacy(args.harness, args.harness_args)
    if args.command == "doctor":
        return _doctor(
            args.harness,
            args.installation_only,
            args.live_auth,
            args.discover_models,
            args.probe_routes,
            args.json_output,
        )
    if args.command == "harnesses":
        return _harnesses(args.json_output)
    if args.command == "inbox":
        return _inbox(args.json_output)
    if args.command == "answer":
        return _answer(
            args.dispatch_id,
            args.ask_id,
            args.choice,
            args.note,
            args.answered_by,
            args.json_output,
        )
    if args.command == "steer":
        return _steer(args)
    if args.command == "setup":
        if args.setup_command == "profiles":
            return run_profiles_setup(os.environ, no_color=args.no_color)
        if args.setup_command == "pi":
            return _setup_pi(args.dry_run)
        if args.setup_command == "inventory":
            return _setup_inventory(args.json_output)
        if args.setup_command == "mcp":
            return setup_mcp(
                args.harnesses or [],
                args.server_command,
                args.remove,
                args.dry_run,
                args.yes,
            )
        return _setup_harnesses(args.dry_run, args.no_color)
    if args.command == "profiles":
        if args.routes_command == "list":
            return _routes_list(args.json_output)
        if args.routes_command == "show":
            return _routes_show(args.name)
        if args.routes_command == "resolve":
            return _routes_resolve(args.spec, args.json_output)
        if args.routes_command == "probe":
            return _routes_probe(args.name, args.json_output, args.timeout)
        if args.routes_command == "discover":
            return _routes_discover(args)
        if args.routes_command == "refresh":
            return _routes_refresh(args)
        if args.routes_command == "add":
            return _routes_add(args)
        if args.routes_command == "add-model-studio-endpoint":
            return _routes_add_model_studio_endpoint(args)
        if args.routes_command == "remove":
            return _routes_remove(args.name)
        return _routes_sync(args.harness, args.dry_run, args.yes)
    if args.command == "workflow":
        if args.workflow_command == "run":
            return _workflow_run(args.path, args.host)
        if args.workflow_command == "list":
            return _workflow_list(args.json_output)
        if args.workflow_command == "show":
            return _workflow_show(args.workflow_id)
        if args.workflow_command == "resume":
            return _workflow_resume(args.workflow_id, args.host)
        return _workflow_cancel(args.workflow_id)
    if args.command == "broker":
        if args.pitwall_command == "receiver":
            return _pitwall_receiver(args)
        if args.pitwall_command == "subscribe":
            return _pitwall_subscribe(args.receiver_url)
        return _pitwall_watch(args.interval)
    if args.runs_command == "list":
        return _runs_list()
    if args.runs_command == "show":
        return _runs_show(args.dispatch_id)
    if args.runs_command == "logs":
        return _runs_logs(args.dispatch_id, args.channel)
    if args.runs_command == "diff":
        return _runs_diff(args.dispatch_id)
    if args.runs_command == "apply":
        return _runs_apply(args.dispatch_id, args.target, args.commits)
    if args.runs_command == "discard":
        return _runs_discard(args.dispatch_id, args.yes)
    if args.runs_command == "resume":
        return resume_legacy(args.dispatch_id, environ=os.environ)
    if args.runs_command == "stop":
        return _runs_stop(args.dispatch_id, args.message, args.grace)
    return _runs_cleanup(args.older_than, args.all)


if __name__ == "__main__":
    raise SystemExit(main())
