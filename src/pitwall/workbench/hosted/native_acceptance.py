"""Bounded hosted native-child acceptance for one exact profile (port of ``scripts/hosted-native-acceptance.ts``)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pitwall.workbench.comparison._json import (
    JsonObject,
    as_object,
    as_str,
    get_object,
    parse_json_object,
)
from pitwall.workbench.comparison.session import (
    PI_STOP_KILL_AFTER_MS,
    PI_STOP_TERM_AFTER_MS,
    TINTIN_ENTRY,
    TINTIN_PACKAGE,
    RpcError,
    RpcSession,
    pinned_node_module,
)
from pitwall.workbench.hosted.native_evaluation import (
    ChildSession,
    ExpectedChild,
    evaluate_native_child_evidence,
)
from pitwall.workbench.hosted_profiles import CREDENTIAL_ENV, credential_value
from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.native_profile import configure_native_profile
from pitwall.workbench.profile import compile_profile, profile_from_config

MARKER = "HOSTED_NATIVE_CHILD_ORACLE_7F31"
PRESERVE_CONTENT = "PRESERVE_NATIVE_FILE\n"
WAIT_MS = 180_000
COMMAND_MS = 30_000
DELEGATION_PROMPT = (
    "Delegate exactly one read-only scout child with agent_task. Ask it to read witness.txt and "
    "report the file contents. Do not read the file yourself and do not edit any file."
)


def _write_private(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o600)


def _read_jsonl(path: Path) -> list[object] | None:
    """Every line of a JSONL file, or ``None`` when the file is missing or any line is malformed."""
    try:
        lines = [line for line in path.read_text().split("\n") if line]
        return [json.loads(line) for line in lines]
    except OSError, ValueError:
        # A missing or malformed child session fails the evidence helper.
        return None


def _accounting_records(path: Path) -> list[JsonObject]:
    try:
        text = path.read_text().strip()
    except OSError:
        # The accounting gate below records a missing file.
        return []
    return [record for line in text.split("\n") if line and (record := parse_json_object(line))]


def _walk_files(root: Path) -> list[Path]:
    suffixes = (".json", ".jsonl", ".md")
    return sorted(
        path for path in root.rglob("*") if path.is_file() and path.name.endswith(suffixes)
    )


def run_hosted_native_acceptance(
    config_path: Path | str,
    auth_path: Path | str,
    output_dir: Path | str,
    profile_name: str = "minimax-coding-plan",
) -> int:
    """Delegate one native scout child under an exact hosted profile and gate on its own session."""
    config = json.loads(Path(os.path.abspath(config_path)).read_text())
    auth = json.loads(Path(os.path.abspath(auth_path)).read_text())
    configured = profile_from_config(config, profile_name)
    if not configured.get("accountRef") or configured.get("apiKeyEnv") != CREDENTIAL_ENV:
        raise ValueError(
            "hosted native profile requires the selected account reference and credential variable"
        )
    credential = credential_value(auth, configured["accountRef"])
    limit = os.environ.get("PITWALL_WORKBENCH_HOSTED_MAX_COMPLETION_TOKENS")
    maximum = int(limit) if limit else configured.get("maxCompletionTokens")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 256:
        raise ValueError("bounded hosted maxCompletionTokens must be at least 256")
    profile = {**configured, "maxCompletionTokens": maximum}
    output = Path(os.path.abspath(output_dir))
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="native-run-", dir=output))
    fixture = Path(tempfile.mkdtemp(prefix="fixture-", dir=run_root))
    _write_private(fixture / "witness.txt", f"{MARKER}\n")
    _write_private(fixture / "preserve.txt", PRESERVE_CONTENT)
    agent_dir = Path(tempfile.mkdtemp(prefix="agent-", dir=run_root))
    compiled = compile_profile(profile_name, profile, agent_dir)
    native = configure_native_profile(compiled, fixture)
    tintin = pinned_node_module(TINTIN_PACKAGE, TINTIN_ENTRY)
    if tintin is None:
        raise ValueError("the pinned @tintinweb/pi-subagents backend is not installed")
    session = RpcSession(
        launch_pi(
            PiLaunchOptions(
                cwd=fixture,
                profile=compiled,
                env={CREDENTIAL_ENV: credential, **native.env},
                extensions=[tintin, extension_path("native-extension")],
            )
        ),
        id_prefix="native",
        max_observed=1_000_000,
    )
    gates: JsonObject = {}
    report: JsonObject = {
        "profile": {
            "name": profile_name,
            "provider": profile["provider"],
            "modelId": profile["modelId"],
            "endpoint": profile["endpoint"],
            "api": profile["api"],
            "accountRef": profile["accountRef"],
            "resourceGroup": profile["resourceGroup"],
            "maxCompletionTokens": profile["maxCompletionTokens"],
        },
        "fixture": str(fixture),
        "agentDir": str(agent_dir),
        "gates": gates,
        "termination": {},
        "stderrBytes": 0,
    }
    try:
        state = session.response_data("get_state", None, COMMAND_MS)
        model = get_object(state, "model") or {}
        identity = {
            "provider": model.get("provider"),
            "modelId": model.get("id"),
            "exact": model.get("provider") == profile["provider"]
            and model.get("id") == profile["modelId"],
        }
        session.request("set_auto_retry", {"enabled": False}, COMMAND_MS)
        session.request("prompt", {"message": DELEGATION_PROMPT}, COMMAND_MS)
        session.wait_until(
            lambda: any(
                entry.event.get("type") in ("agent_settled", "extension_error")
                for entry in session.snapshot()
            ),
            WAIT_MS,
            "the hosted native child to settle",
        )
        events = [entry.event for entry in session.snapshot()]
        starts = [e for e in events if e.get("type") == "tool_execution_start"]
        ends = [e for e in events if e.get("type") == "tool_execution_end"]
        task_start = next((e for e in starts if e.get("toolName") == "agent_task"), None)
        task_end = (
            next(
                (
                    e
                    for e in ends
                    if e.get("toolName") == "agent_task"
                    and e.get("toolCallId") == task_start.get("toolCallId")
                ),
                None,
            )
            if task_start is not None
            else None
        )
        task_details = get_object(get_object(task_end, "result"), "details")
        task_child_id = as_str((task_details or {}).get("agentId"))
        files = _walk_files(agent_dir)
        protected_unchanged = (fixture / "preserve.txt").read_text() == PRESERVE_CONTENT
        task_records: list[object] = []
        for path in files:
            if "/task-records/" in str(path) and path.name.endswith(".json"):
                try:
                    task_records.append(json.loads(path.read_text()))
                except OSError, ValueError:
                    # An unreadable record is treated as absent evidence.
                    task_records.append(None)
        child_sessions: list[ChildSession] = []
        for task_record in task_records:
            session_file = as_str((as_object(task_record) or {}).get("backendSessionFile"))
            if not session_file:
                continue
            lines = _read_jsonl(Path(session_file))
            if lines is not None:
                child_sessions.append(ChildSession(session_file, lines))
        evidence = evaluate_native_child_evidence(
            task_records=task_records,
            child_sessions=child_sessions,
            expected=ExpectedChild(
                name=profile_name,
                provider=str(profile["provider"]),
                model_id=str(profile["modelId"]),
                workspace=str(fixture),
            ),
            marker=MARKER,
            protected_unchanged=protected_unchanged,
            task_started=task_start is not None,
            task_finished=task_end is not None,
            task_error=(task_end or {}).get("isError") is True,
            task_child_id=task_child_id,
        )
        accounting = _accounting_records(agent_dir / "native-accounting.jsonl")
        accounting_types = list(dict.fromkeys(str(item.get("type")) for item in accounting))
        credential_captured = credential in json.dumps(accounting)
        gates["identity"] = {
            "expected": "exact hosted provider/model",
            "status": "passed" if identity["exact"] else "failed",
            "actual": identity,
        }
        gates["child"] = {
            "expected": "succeeded exact-profile native child session contains a correlated read(witness.txt) result with the fixture marker",
            "status": evidence.status,
            "actual": {
                **evidence.actual.to_json(),
                "parentToolNames": [e.get("toolName") for e in starts],
                "sessionFileCount": len(files),
            },
        }
        gates["accounting"] = {
            "expected": "native request/settled accounting without credential values",
            "status": "passed"
            if "native_request" in accounting_types
            and "native_settled" in accounting_types
            and not credential_captured
            else "failed",
            "actual": {
                "records": len(accounting),
                "types": accounting_types,
                "credentialCaptured": credential_captured,
            },
        }
        gates["termination"] = {"expected": "clean bounded shutdown", "status": "unexecuted"}
    except (RpcError, OSError, ValueError) as error:
        report["error"] = str(error) or "native hosted validation failed"
    finally:
        outcome = session.stop(
            term_after_ms=PI_STOP_TERM_AFTER_MS, kill_after_ms=PI_STOP_KILL_AFTER_MS
        )
        closed = session.closed
        report["termination"] = {"forced": outcome.forced, "childExited": closed}
        report["stderrBytes"] = session.stderr_bytes
        gates["termination"] = {
            "expected": "clean bounded shutdown",
            "status": "passed" if closed else "failed",
            "actual": {"forced": outcome.forced, "childExited": closed},
        }
        report["status"] = (
            "passed"
            if len(gates) >= 3
            and all((as_object(gate) or {}).get("status") == "passed" for gate in gates.values())
            and closed
            else "failed"
        )
    report_path = output / f"{profile_name}.json"
    _write_private(report_path, json.dumps(report, indent=2) + "\n")
    print(json.dumps({"reportPath": str(report_path), "status": report["status"]}))
    return 0 if report["status"] == "passed" else 1
