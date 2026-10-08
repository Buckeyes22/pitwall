"""Opt-in local baseline acceptance (port of ``scripts/baseline-rpc.ts``).

This harness only edits its named, explicit disposable fixture and never becomes an alternative Pi
session manager. It drives one profile through identity, repair, compaction, vision, reasoning
control, cancellation, and accounting gates, and writes a redacted report.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pitwall.workbench.accounting import safe_usage
from pitwall.workbench.comparison._json import (
    JsonObject,
    as_list,
    as_number,
    as_object,
    get_object,
    get_str,
    parse_json_object,
)
from pitwall.workbench.comparison.session import (
    PI_STOP_KILL_AFTER_MS,
    PI_STOP_TERM_AFTER_MS,
    RpcError,
    RpcSession,
)
from pitwall.workbench.hosted.fixture import (
    PRESERVED_CONTENT,
    FixtureError,
    assert_fixture_unchanged,
    snapshot_fixture,
)
from pitwall.workbench.hosted.turn import (
    TURN_TIMEOUT_MS,
    GateFailure,
    gate_fixture_check,
    model_turn,
)
from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.profile import compile_profile, profile_from_config

GATE_IDS = (
    "identity",
    "repair",
    "compaction",
    "vision",
    "reasoning_control",
    "cancellation",
    "accounting",
)
LIMITATIONS = [
    "No hosted-account requests.",
    "No endpoint-wide admission claim.",
    "Tool-descendant cancellation and cross-process admission require separate hermetic fixtures.",
    "Bytes are not tokens; usage remains provider-reported or unavailable.",
]


class BaselineError(ValueError):
    """The baseline was invoked against something other than its designated fixture and profile."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_accounting(path: Path) -> list[JsonObject]:
    records: list[JsonObject] = []
    for line in path.read_text().strip().split("\n"):
        if line:
            record = parse_json_object(line)
            if record is None:
                raise ValueError("accounting record is not a JSON object")
            records.append(record)
    return records


def _safe_integer(value: object) -> bool:
    number = as_number(value)
    return (
        isinstance(value, int) and not isinstance(value, bool) and number is not None and number > 0
    )


def run_baseline(
    config_path: Path | str,
    profile_name: str,
    cwd: Path | str,
    report_path: Path | str,
) -> int:
    """Run the baseline gates for ``profile_name`` against the disposable fixture ``cwd``.

    Returns the process exit status: 0 only when every gate passed, the child exited, and the
    accounting file recorded requests.
    """
    fixture = Path(os.path.abspath(cwd))
    report_file = Path(os.path.abspath(report_path))
    config = json.loads(Path(os.path.abspath(config_path)).read_text())
    profile = profile_from_config(config, profile_name)
    key_env = profile.get("apiKeyEnv")
    if not key_env or not os.environ.get(key_env):
        raise BaselineError(
            "baseline requires the selected endpoint credential in its referenced environment variable"
        )
    # Refuse arbitrary repositories: this harness only edits its named, explicit fixture.
    if (fixture / "preserve.txt").read_text() != PRESERVED_CONTENT:
        raise BaselineError("not the designated disposable baseline fixture")
    original_tests = (fixture / "add.test.mjs").read_text()
    protected = snapshot_fixture(fixture)
    agent_dir = Path(tempfile.mkdtemp(prefix="pi-workbench-agent-"))
    compiled = compile_profile(profile_name, profile, agent_dir)
    accounting_path = agent_dir / "accounting.jsonl"
    settings = {"compaction": {"enabled": False, "keepRecentTokens": 256, "reserveTokens": 4096}}
    settings_path = agent_dir / "settings.json"
    settings_path.write_text(json.dumps(settings))
    settings_path.chmod(0o600)
    session = RpcSession(
        launch_pi(
            PiLaunchOptions(
                cwd=fixture,
                profile=compiled,
                extension=extension_path("extension"),
                env={
                    "PITWALL_WORKBENCH_ACCOUNTING_PATH": str(accounting_path),
                    "BASELINE_FIXTURE": str(fixture),
                },
                passthrough_env=["BASELINE_FIXTURE"],
            )
        ),
        id_prefix="baseline",
        max_observed=2000,
    )
    gates: JsonObject = {
        gate: {
            "expected": f"{gate} acceptance check",
            "status": "unexecuted",
            "actual": "Earlier prerequisite prevented this check",
        }
        for gate in GATE_IDS
    }
    state = {"failed": False, "idle_confirmed": False}

    def gate(gate_id: str, expected: str, action: Callable[[], object]) -> None:
        try:
            gates[gate_id] = {"expected": expected, "status": "passed", "actual": action()}
        except (GateFailure, FixtureError, RpcError, OSError, ValueError) as error:
            state["failed"] = True
            gates[gate_id] = {
                "expected": expected,
                "status": "failed",
                "actual": str(error) or "gate failed",
            }
            raise

    def identity() -> object:
        model = get_object(session.response_data("get_state"), "model")
        if (model or {}).get("provider") != profile["provider"] or (model or {}).get(
            "id"
        ) != profile["modelId"]:
            raise GateFailure("selected provider/model differs from profile")
        session.request("set_auto_retry", {"enabled": False})
        session.request("set_auto_compaction", {"enabled": False})
        return {"provider": (model or {}).get("provider"), "model": (model or {}).get("id")}

    def repair() -> object:
        before = gate_fixture_check(fixture, "fixture test before repair")
        if before.exit_code == 0:
            raise GateFailure(
                "fixture already passes; reset the designated fixture before repeating"
            )
        result = model_turn(
            session,
            "Read add.mjs and add.test.mjs. Run node --test add.test.mjs to observe its failure. "
            "Repair only add.mjs so addition works. Run the same test again. Do not change tests "
            "or preserve.txt. Report concise evidence.",
        )
        after = gate_fixture_check(fixture, "fixture test after repair")
        if (
            after.exit_code != 0
            or (fixture / "preserve.txt").read_text() != PRESERVED_CONTENT
            or (fixture / "add.test.mjs").read_text() != original_tests
        ):
            raise GateFailure("independent checks failed")
        assert_fixture_unchanged(protected, snapshot_fixture(fixture))
        if "bash" not in result.tools or not any(
            tool in ("edit", "write") for tool in result.tools
        ):
            raise GateFailure("required edit/test tool activity missing")
        return {
            "beforeExitNonzero": True,
            "afterExit": 0,
            "tools": result.tools,
            "reportedUsage": result.usage,
        }

    def compaction() -> object:
        compact_start = session.last_seq()
        data = session.response_data(
            "compact",
            {
                "customInstructions": "Preserve the addition repair, its acceptance test, unchanged "
                "preserve.txt, and next action: rerun node --test add.test.mjs."
            },
        )
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise GateFailure("compaction returned no summary")
        if not _safe_integer(data.get("tokensBefore")):
            raise GateFailure("compaction returned invalid tokensBefore")
        if not _safe_integer(data.get("estimatedTokensAfter")):
            raise GateFailure("compaction returned invalid estimatedTokensAfter")
        if not any(
            entry.event.get("type") == "compaction_end" and entry.event.get("result")
            for entry in session.events_after(compact_start)
        ):
            raise GateFailure("compaction response had no corresponding RPC compaction_end event")
        records = _read_accounting(accounting_path) if accounting_path.exists() else []
        record = next(
            (item for item in reversed(records) if item.get("type") == "compaction"), None
        )
        if record is None or record.get("tokensBefore") != data.get("tokensBefore"):
            raise GateFailure("compaction accounting record did not match RPC tokensBefore")
        result = model_turn(
            session,
            "Continue the same task after compaction. Rerun its acceptance test without changing "
            "any files and report whether it passes.",
        )
        validation = gate_fixture_check(fixture, "fixture test after compaction")
        if "bash" not in result.tools or validation.exit_code != 0:
            raise GateFailure("post-compaction validation missing or failed")
        assert_fixture_unchanged(protected, snapshot_fixture(fixture))
        return {
            "tokensBefore": data.get("tokensBefore"),
            "estimatedTokensAfter": data.get("estimatedTokensAfter"),
            "reportedUsage": safe_usage(data.get("usage")),
            "tools": result.tools,
        }

    def vision() -> object:
        image = base64.b64encode((fixture / "colors.png").read_bytes()).decode()
        result = model_turn(
            session,
            "Look only at the attached image. What color is the LEFT half and what color is the "
            "RIGHT half? Reply LEFT=<color>; RIGHT=<color>. Do not use tools.",
            {"images": [{"type": "image", "mimeType": "image/png", "data": image}]},
        )
        if not re.search(r"left\s*[=:]\s*red", result.text, re.IGNORECASE) or not re.search(
            r"right\s*[=:]\s*blue", result.text, re.IGNORECASE
        ):
            raise GateFailure("image answer did not match controlled fixture")
        return {"matched": True, "reportedUsage": result.usage}

    def reasoning_control() -> object:
        session.request("set_thinking_level", {"level": "off"})
        result = model_turn(session, "Reply with exactly 4. Do not use tools.")
        if result.text.strip() != "4":
            raise GateFailure("thinking-off fixture answer mismatch")
        return {"requestedLevel": "off", "reportedUsage": result.usage}

    def cancellation() -> object:
        start = session.last_seq()
        session.request(
            "prompt",
            {"message": "Count integers from 1 to 100000, one per line. Do not use tools."},
        )
        session.wait_event(
            "message_update",
            start,
            TURN_TIMEOUT_MS,
            lambda event: get_str(event.get("assistantMessageEvent"), "type") == "text_delta",
        )
        session.request(
            "follow_up",
            {"message": "After counting, count again from 1 to 100000. Do not use tools."},
        )
        session.request("clear_queue")
        session.request("abort")
        session.wait_event("agent_settled", start, TURN_TIMEOUT_MS)
        after = session.last_seq()
        time.sleep(0.75)
        current = session.response_data("get_state")
        pending = as_number(current.get("pendingMessageCount")) or 0
        if (
            current.get("isStreaming")
            or pending > 0
            or any(
                entry.event.get("type") == "agent_start" for entry in session.events_after(after)
            )
        ):
            raise GateFailure("work continued after cancellation")
        state["idle_confirmed"] = True
        return {
            "idle": True,
            "pendingMessageCount": current.get("pendingMessageCount"),
            "serverTermination": "client abort confirmed; no claim about server GPU activity",
        }

    try:
        gate("identity", "Runtime selects exactly the requested provider and model.", identity)
        gate(
            "repair",
            "Failing fixture is read, repaired, and independently passes; unrelated content survives.",
            repair,
        )
        gate(
            "compaction",
            "Manual compaction succeeds and a continued native session can revalidate the repair.",
            compaction,
        )
        if "image" in (as_list(profile.get("inputModalities")) or []):
            gate(
                "vision",
                "Image reaches the model; it identifies red on the left and blue on the right.",
                vision,
            )
        else:
            gates["vision"] = {
                "expected": "Vision only when profile advertises image input.",
                "status": "unexecuted",
                "actual": "text-only profile",
            }
        gate(
            "reasoning_control",
            "Thinking-off turn completes; outgoing flags are recorded for comparison.",
            reasoning_control,
        )
        gate(
            "cancellation",
            "Clear queued work, abort active generation, and confirm the session remains idle.",
            cancellation,
        )
    except GateFailure, FixtureError, RpcError, OSError, ValueError:
        # The failing gate already recorded its own message; the run continues to termination.
        state["failed"] = True
    finally:
        outcome = session.stop(
            term_after_ms=PI_STOP_TERM_AFTER_MS, kill_after_ms=PI_STOP_KILL_AFTER_MS
        )
        accounting: list[JsonObject] = []
        try:
            accounting = _read_accounting(accounting_path)
        except OSError, ValueError:
            # A missing or unreadable accounting file fails the run and the accounting gate.
            state["failed"] = True
        closed = session.closed
        forced = outcome.forced
        idle = state["idle_confirmed"]
        unresolved = forced and not idle
        if not forced:
            disposition = (
                "Pi exited after native queue clear/abort; descendants were not enumerated."
            )
        elif idle:
            disposition = "Pi required forced termination after confirmed idle abort; descendants were not enumerated."
        else:
            disposition = (
                "Pi required forced termination before idle was confirmed; "
                "descendant processes remain unresolved."
            )
        gates["accounting"] = {
            "expected": "Capture real serialized request metadata without raw transcripts or secrets.",
            "status": "passed" if accounting else "failed",
            "actual": {"records": len(accounting), "path": str(accounting_path)},
        }
        protocol_error = session.protocol_error
        if protocol_error or not closed or not accounting or unresolved:
            state["failed"] = True
        report: JsonObject = {
            "timestamp": _now(),
            "profile": {"provider": profile["provider"], "modelId": profile["modelId"]},
            "agentDir": str(agent_dir),
            "gates": gates,
            "accounting": accounting,
            "protocolError": protocol_error,
            "stderrBytes": session.stderr_bytes,
            "childExited": closed,
            "termination": {
                "forced": forced,
                "unresolved": unresolved,
                "descendantDisposition": disposition,
            },
            "status": "failed" if state["failed"] else "passed",
            "limitations": LIMITATIONS,
        }
        report_file.write_text(json.dumps(report, indent=2) + "\n")
        report_file.chmod(0o600)
        print(
            json.dumps(
                {
                    "reportPath": str(report_file),
                    "status": report["status"],
                    "gates": {
                        key: (as_object(value) or {}).get("status") for key, value in gates.items()
                    },
                }
            )
        )
    return 1 if state["failed"] else 0
