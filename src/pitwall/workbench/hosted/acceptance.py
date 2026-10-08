"""Bounded hosted acceptance runner (port of ``scripts/hosted-acceptance.ts``).

This is deliberately separate from the baseline runner: it resolves one existing OpenCode account in
memory, runs one fresh fixture per exact hosted profile, and records only redacted runtime
evidence. It never writes credentials to a profile, fixture, report, or stderr capture.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pitwall.workbench.accounting import safe_usage
from pitwall.workbench.comparison._json import (
    JsonObject,
    as_list,
    as_object,
    as_str,
    get_object,
    parse_json_object,
)
from pitwall.workbench.comparison.session import (
    PI_STOP_KILL_AFTER_MS,
    PI_STOP_TERM_AFTER_MS,
    RpcError,
    RpcSession,
)
from pitwall.workbench.hosted.evaluation import (
    ModelCommandEvaluation,
    evaluate_compaction_retention,
    evaluate_model_command,
)
from pitwall.workbench.hosted.fixture import (
    PRESERVED_CONTENT,
    FixtureError,
    assert_fixture_unchanged,
    create_temporary_fixture,
    snapshot_fixture,
)
from pitwall.workbench.hosted.turn import (
    TURN_TIMEOUT_MS,
    GateFailure,
    gate_fixture_check,
    model_turn,
)
from pitwall.workbench.hosted_profiles import CREDENTIAL_ENV, credential_value
from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.pi_pin import PI_PACKAGE, PINNED_PI_VERSION
from pitwall.workbench.profile import (
    compile_profile,
    configure_provider_profile,
    profile_from_config,
)

DEFAULT_PROFILES = ("minimax-coding-plan", "zai-coding-plan", "alibaba-token-plan")
GATE_IDS = ("identity", "coding", "compaction", "task_state", "continuation", "accounting")
RUNTIME = f"{PI_PACKAGE}@{PINNED_PI_VERSION}"
ACCEPTANCE_COMMAND = "node --test add.test.mjs"
COMPACT_INSTRUCTIONS: JsonObject = {
    "customInstructions": "Preserve the task marker, acceptance criteria, changed file, "
    "validation state, and pending next action from the conversation."
}
_GATE_ERRORS = (GateFailure, FixtureError, RpcError, OSError, ValueError)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_private(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o600)


def _session_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.jsonl") if path.is_file()) if root.is_dir() else []


def _entry_text(entry: JsonObject) -> str:
    if entry.get("type") == "compaction":
        return as_str(entry.get("summary")) or ""
    if entry.get("type") != "message":
        return ""
    content = (as_object(entry.get("message")) or {}).get("content")
    if isinstance(content, str):
        return content
    parts = as_list(content)
    if parts is None:
        return ""
    return "\n".join(
        text for part in parts if (text := as_str((as_object(part) or {}).get("text"))) is not None
    )


def _retained_session_suffix(agent_dir: Path) -> tuple[str | None, str]:
    """The retained transcript after the latest compaction, as (firstKeptEntryId, text)."""
    # The compaction RPC response can precede the JSONL flush by a short interval.
    for _attempt in range(20):
        for path in _session_files(agent_dir / "sessions"):
            entries = [
                entry
                for line in path.read_text().split("\n")
                if line.strip() and (entry := parse_json_object(line)) is not None
            ]
            compaction_index = next(
                (
                    index
                    for index in range(len(entries) - 1, -1, -1)
                    if entries[index].get("type") == "compaction"
                    and isinstance(entries[index].get("firstKeptEntryId"), str)
                ),
                -1,
            )
            if compaction_index < 0:
                continue
            kept_id = str(entries[compaction_index]["firstKeptEntryId"])
            kept_index = next(
                (index for index, entry in enumerate(entries) if entry.get("id") == kept_id), -1
            )
            if kept_index < 0:
                continue
            text = "\n".join(filter(None, (_entry_text(entry) for entry in entries[kept_index:])))
            return kept_id, text
        time.sleep(0.05)
    return None, ""


def _command_evaluation_report(evaluation: ModelCommandEvaluation) -> JsonObject:
    """The persisted form of a command evaluation, with earlier runs kept as diagnostics."""
    return {
        "matched": evaluation.matched,
        "commandSeen": evaluation.command_seen,
        "toolCallId": evaluation.tool_call_id,
        "isError": evaluation.is_error,
        "reason": evaluation.reason,
        "earlier": [
            {
                "matched": item.matched,
                "commandSeen": item.command_seen,
                "toolCallId": item.tool_call_id,
                "isError": item.is_error,
                "reason": item.reason,
            }
            for item in evaluation.earlier
        ],
    }


def _acceptance_commands(fixture: Path) -> list[str]:
    command = ACCEPTANCE_COMMAND
    directories = (str(fixture), f"'{fixture}'", f'"{fixture}"')
    return [
        command,
        f'{command}; echo "exit=$?"',
        f'{command}; echo "EXIT:$?"',
        f'{command}; echo "EXIT: $?"',
        f'{command}; echo "exit code: $?"',
        *[f'cd {cwd} && {command}; echo "exit code: $?"' for cwd in directories],
        *[
            f'cd {cwd} && {command}; echo "{label}"'
            for cwd in directories
            for label in ("EXIT:$?", "EXIT: $?")
        ],
    ]


def _read_accounting(paths: Sequence[Path]) -> list[JsonObject]:
    records: list[JsonObject] = []
    for path in paths:
        try:
            lines = path.read_text().strip().split("\n")
        except OSError:
            # An absent accounting file is recorded by the accounting gate below.
            continue
        records.extend(record for line in lines if line and (record := parse_json_object(line)))
    return records


def _run_profile(
    profile_name: str,
    profile: JsonObject,
    account_key: str,
    run_root: Path,
    max_completion_override: int | None,
) -> JsonObject:
    task_marker = f"PW_{profile_name.replace('-', '_').upper()}_7391"
    fixture = create_temporary_fixture(run_root)
    protected = snapshot_fixture(fixture)
    agent_dir = Path(tempfile.mkdtemp(prefix="agent-", dir=run_root))
    effective_profile = (
        profile
        if max_completion_override is None
        else {**profile, "maxCompletionTokens": max_completion_override}
    )
    compiled = compile_profile(profile_name, effective_profile, agent_dir)
    provider = configure_provider_profile(compiled, fixture)
    accounting_path = agent_dir / "accounting.jsonl"
    _write_private(
        agent_dir / "settings.json",
        json.dumps(
            {"compaction": {"enabled": False, "keepRecentTokens": 256, "reserveTokens": 4096}}
        ),
    )
    session = RpcSession(
        launch_pi(
            PiLaunchOptions(
                cwd=fixture,
                profile=compiled,
                extension=extension_path("extension"),
                env={
                    CREDENTIAL_ENV: account_key,
                    "PITWALL_WORKBENCH_ACCOUNTING_PATH": str(accounting_path),
                    **provider.env,
                },
                passthrough_env=["PITWALL_WORKBENCH_ACCOUNTING_PATH"],
            )
        ),
        id_prefix="hosted",
        max_observed=2500,
        default_timeout_ms=TURN_TIMEOUT_MS,
    )
    report: JsonObject = {
        "profile": {
            "name": profile_name,
            "provider": profile["provider"],
            "modelId": profile["modelId"],
            "endpoint": profile["endpoint"],
            "api": profile["api"],
            "accountRef": profile["accountRef"],
            "resourceGroup": profile["resourceGroup"],
            "configuredMaxCompletionTokens": profile["maxCompletionTokens"],
            "effectiveMaxCompletionTokens": effective_profile["maxCompletionTokens"],
        },
        "fixture": str(fixture),
        "agentDir": str(agent_dir),
        "accountingPath": str(accounting_path),
        "gates": {},
        "termination": {},
        "stderrBytes": 0,
    }
    gates: dict[str, JsonObject] = {
        gate: {"expected": gate, "status": "unexecuted"} for gate in GATE_IDS
    }
    idle_confirmed = False
    try:
        state = session.response_data("get_state")
        model = get_object(state, "model") or {}
        if model.get("provider") != profile["provider"] or model.get("id") != profile["modelId"]:
            raise GateFailure("selected provider/model differs from profile")
        session.request("set_auto_retry", {"enabled": False})
        session.request("set_auto_compaction", {"enabled": False})
        gates["identity"] = {
            "expected": "Pi selects the exact hosted provider and model from the profile.",
            "status": "passed",
            "actual": {"provider": model.get("provider"), "modelId": model.get("id")},
        }

        before = gate_fixture_check(fixture, "fixture test before repair")
        if before.exit_code == 0:
            raise GateFailure("designated fixture did not begin as a bounded failing task")
        coding = model_turn(
            session,
            "Read add.mjs and add.test.mjs. Run node --test add.test.mjs to observe its failure. "
            "Repair only add.mjs so addition works. Run the same test again. Do not change "
            "add.test.mjs or preserve.txt. In your concise report include task marker "
            f"{task_marker}, criteria add(2,3)=5 and add(-2,3)=1, changed file add.mjs only, "
            "validation state node --test add.test.mjs exit code 0, and next action rerun "
            "node --test add.test.mjs.",
        )
        after_repair = gate_fixture_check(fixture, "fixture test after repair")
        if after_repair.exit_code != 0:
            raise GateFailure("independent acceptance check after repair failed")
        if (fixture / "preserve.txt").read_text() != PRESERVED_CONTENT:
            raise GateFailure("preserve.txt changed")
        assert_fixture_unchanged(protected, snapshot_fixture(fixture))
        if "bash" not in coding.tools or not any(
            tool in ("edit", "write") for tool in coding.tools
        ):
            raise GateFailure("coding turn did not use required tool path")
        gates["coding"] = {
            "expected": "Pi repairs the disposable fixture and host-side acceptance passes independently.",
            "status": "passed",
            "actual": {
                "beforeExitCode": before.exit_code,
                "afterRepairExitCode": after_repair.exit_code,
                "tools": coding.tools,
                "reportedUsage": coding.usage,
            },
        }

        checkpoint = model_turn(
            session,
            f"Before compaction, create a concise task checkpoint. State the task marker {task_marker}, "
            "criteria add(2,3)=5 and add(-2,3)=1, changed file add.mjs only, validation state host "
            f"acceptance exit code {after_repair.exit_code}, and next action rerun node --test "
            "add.test.mjs. Do not use tools.",
        )
        text = checkpoint.text
        checkpoint_facts = [
            task_marker in text,
            bool(re.search(r"add|addition", text, re.I))
            and bool(re.search(r"\b5\b", text))
            and bool(re.search(r"\b1\b", text)),
            "add.mjs" in text,
            bool(re.search(r"exit.?code.?0|passed|pass", text, re.I)),
            bool(re.search(r"node\s+--test\s+add\.test\.mjs", text, re.I)),
        ]
        if not all(checkpoint_facts):
            raise GateFailure("model checkpoint did not state all task facts before compaction")

        compact_start = session.last_seq()
        compact_response = session.request("compact", COMPACT_INSTRUCTIONS)
        compact = as_object(compact_response.get("data")) or {}
        summary = compact.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise GateFailure("compaction returned no summary")
        for field, label in (
            ("tokensBefore", "tokensBefore"),
            ("estimatedTokensAfter", "estimatedTokensAfter"),
        ):
            value = compact.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise GateFailure(f"compaction returned invalid {label}")
        if not any(
            entry.event.get("type") == "compaction_end" and entry.event.get("result")
            for entry in session.events_after(compact_start)
        ):
            raise GateFailure("RPC compaction had no compaction_end event")
        kept_entry_id, suffix_text = _retained_session_suffix(agent_dir)
        retention = evaluate_compaction_retention(summary, suffix_text, task_marker)
        gates["compaction"] = {
            "expected": "Forced RPC compaction succeeds, emits a native compaction event, and retains the assisted checkpoint task state.",
            "status": "passed" if retention.retained_suffix_complete else "failed",
            "actual": {
                "checkpointMode": "assisted-model-checkpoint",
                "tokensBefore": compact["tokensBefore"],
                "estimatedTokensAfter": compact["estimatedTokensAfter"],
                "usage": safe_usage(compact.get("usage")),
                "summaryFacts": retention.summary_facts.to_json(),
                "retainedSuffixFacts": retention.retained_suffix_facts.to_json(),
                "effectiveFacts": retention.effective_facts.to_json(),
                "retainedSuffixFirstKeptEntryId": kept_entry_id,
                "summarySha256": hashlib.sha256(summary.encode()).hexdigest(),
                "retainedSuffixSha256": hashlib.sha256(suffix_text.encode()).hexdigest(),
            },
        }
        if gates["compaction"]["status"] == "failed":
            raise GateFailure(
                "compaction retained neither a complete summary nor a complete retained suffix"
            )

        continuation_start = session.last_seq()
        continuation = model_turn(
            session,
            "Recover the task state from the compaction summary. Perform the pending validation "
            "action from that summary without changing files. Reply with the recovered task marker, "
            "criteria, changed file, validation state, and the exact next action text from the "
            "summary; do not derive any fact from this message.",
        )
        continuation_events = [entry.event for entry in session.events_after(continuation_start)]
        evaluation = evaluate_model_command(continuation_events, _acceptance_commands(fixture))
        command_result = next(
            (
                event
                for event in continuation_events
                if event.get("type") == "tool_execution_end"
                and event.get("toolName") == "bash"
                and event.get("toolCallId") == evaluation.tool_call_id
            ),
            None,
        )
        result_text = (
            json.dumps((command_result or {}).get("result") or {}) if command_result else ""
        )
        command_ran = evaluation.matched
        said = continuation.text
        continuation_facts = {
            "taskMarker": task_marker in said,
            "criteria": bool(re.search(r"add|addition", said, re.I))
            and bool(re.search(r"\b5\b", said))
            and bool(re.search(r"\b1\b", said)),
            "changedFile": "add.mjs" in said,
            "validationState": bool(
                re.search(r"passed|pass|success|green|exit.?code.?0|acceptance", said, re.I)
            ),
            "nextAction": bool(re.search(r"next|continue|rerun|follow", said, re.I))
            and bool(re.search(r"test|validat|accept", said, re.I)),
        }
        after_compaction = gate_fixture_check(fixture, "fixture test after compaction")
        assert_fixture_unchanged(protected, snapshot_fixture(fixture))
        if after_compaction.exit_code != 0:
            raise GateFailure("independent acceptance check after compaction failed")
        result_object = as_object((command_result or {}).get("result")) or {}
        gates["task_state"] = {
            "expected": "Continuation recovers marker, criteria, changed-file, validation, and next-action facts, and the model's correlated bash tool call executes the exact acceptance command successfully.",
            "status": "passed" if all(continuation_facts.values()) and command_ran else "failed",
            "actual": {
                "continuationFacts": continuation_facts,
                "commandRan": command_ran,
                "commandEvaluation": _command_evaluation_report(evaluation),
                "modelToolResult": {
                    "isError": command_result.get("isError"),
                    "resultKeys": list(result_object),
                    "detailKeys": list(get_object(result_object, "details") or {}),
                    "resultSha256": hashlib.sha256(result_text.encode()).hexdigest(),
                }
                if command_result
                else None,
            },
        }
        if gates["task_state"]["status"] == "failed" or not command_ran:
            raise GateFailure(
                "continuation did not recover task state and execute the summarized acceptance command"
            )
        gates["continuation"] = {
            "expected": "The same native Pi session continues after compaction and the independent check still passes.",
            "status": "passed",
            "actual": {
                "afterCompactionExitCode": after_compaction.exit_code,
                "tools": continuation.tools,
                "commandRan": command_ran,
                "reportedUsage": continuation.usage,
            },
        }
        session.request("clear_queue")
        session.request("abort")
        idle_confirmed = True
    except _GATE_ERRORS as error:
        # The first unexecuted gate takes the failure; the report carries the message.
        pending = next((key for key, gate in gates.items() if gate["status"] == "unexecuted"), None)
        if pending is not None:
            gates[pending] = {
                **gates[pending],
                "status": "failed",
                "actual": str(error) or "validation failed",
            }
        report["error"] = str(error) or "validation failed"
    finally:
        outcome = session.stop(
            term_after_ms=PI_STOP_TERM_AFTER_MS, kill_after_ms=PI_STOP_KILL_AFTER_MS
        )
        accounting = _read_accounting([accounting_path, agent_dir / "native-accounting.jsonl"])
        accounting_types = {str(item.get("type")) for item in accounting}
        missing = [
            kind
            for kind in ("native_request", "native_settled", "compaction")
            if kind not in accounting_types
        ]
        credential_captured = account_key in json.dumps(accounting)
        gates["accounting"] = {
            "expected": "Wrapped request/settled and compaction records are captured without prompt or credential values.",
            "status": "passed"
            if accounting and not missing and not credential_captured
            else "failed",
            "actual": {
                "records": len(accounting),
                "missingTypes": missing,
                "credentialCaptured": credential_captured,
            },
        }
        closed = session.closed
        report["gates"] = gates
        report["stderrBytes"] = session.stderr_bytes
        report["accounting"] = accounting
        report["childExited"] = closed
        report["termination"] = {
            "forced": outcome.forced,
            "unresolved": outcome.forced and not idle_confirmed,
            "idleConfirmed": idle_confirmed,
        }
        protocol_error = session.protocol_error
        report["status"] = (
            "passed"
            if all(gate["status"] == "passed" for gate in gates.values())
            and closed
            and not protocol_error
            else "failed"
        )
        if protocol_error:
            report["protocolError"] = protocol_error
    return report


def run_hosted_acceptance(
    config_path: Path | str,
    auth_path: Path | str,
    output_dir: Path | str,
    profile_names: Sequence[str] = (),
) -> int:
    """Run the bounded hosted gates for each named profile; the exit status is 0 only if all pass."""
    names = list(profile_names) or list(DEFAULT_PROFILES)
    override_text = os.environ.get("PITWALL_WORKBENCH_HOSTED_MAX_COMPLETION_TOKENS")
    override: int | None = None
    if override_text:
        try:
            override = int(override_text)
        except ValueError:
            raise ValueError(
                "PITWALL_WORKBENCH_HOSTED_MAX_COMPLETION_TOKENS must be an integer from 256 through 8192"
            ) from None
        if not 256 <= override <= 8192:
            raise ValueError(
                "PITWALL_WORKBENCH_HOSTED_MAX_COMPLETION_TOKENS must be an integer from 256 through 8192"
            )
    config = json.loads(Path(os.path.abspath(config_path)).read_text())
    auth = json.loads(Path(os.path.abspath(auth_path)).read_text())
    output = Path(os.path.abspath(output_dir))
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="hosted-run-", dir=output))
    results: list[JsonObject] = []
    for name in names:
        profile = profile_from_config(config, name)
        if not profile.get("accountRef") or profile.get("apiKeyEnv") != CREDENTIAL_ENV:
            raise ValueError(
                f"{name} must use the selected hosted account reference and credential variable"
            )
        # credential_value validates the auth shape and returns the secret only to the child launch
        # environment; it is never assigned to a report field.
        key = credential_value(auth, profile["accountRef"])
        result = _run_profile(name, profile, key, run_root, override)
        report_path = output / f"{name}.json"
        _write_private(report_path, json.dumps(result, indent=2) + "\n")
        results.append(
            {"profile": name, "status": result["status"], "reportPath": str(report_path)}
        )
    index_path = output / "index.json"
    _write_private(
        index_path,
        json.dumps(
            {
                "generatedAt": _now(),
                "runtime": RUNTIME,
                "secretValuesPrinted": False,
                "runRoot": str(run_root),
                "results": results,
            },
            indent=2,
        )
        + "\n",
    )
    print(json.dumps({"indexPath": str(index_path), "results": results}))
    return 1 if any(result["status"] != "passed" for result in results) else 0
