"""Repeated controlled comparison for architecture section 15 (port of ``scripts/comparison-runner.ts``).

This is an evidence runner, not a benchmark. It uses fresh disposable fixtures, records provider
reported usage when present, and treats missing candidate surfaces as unexecuted rather than
fabricating equivalence. Provider calls happen only when the caller supplies a profile whose
endpoint is a real provider; the tests drive it against loopback fixtures.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pitwall.workbench.comparison._json import (
    JsonObject,
    as_number,
    as_object,
    as_str,
    first_non_negative,
    get_list,
    get_object,
    get_str,
)
from pitwall.workbench.comparison.acceptance import (
    RepairInput,
    assertion_failure_observed,
    capture_disposition,
    command_evidence,
    compaction_summary_facts,
    generated_tests_have_oracle,
    medium_search_accepted,
    preserve_read_evidence,
    protected_tree_hash,
    repair_accepted,
    suite_passed,
)
from pitwall.workbench.comparison.child_evidence import (
    CHILD_MARKER,
    child_execution_evidence_accepted,
    child_model_matches,
    child_receipt,
    durable_child_execution_evidence_accepted,
    durable_child_record_matches,
)
from pitwall.workbench.comparison.evidence import (
    find_parent_session_file,
    persisted_child_evidence,
    resolve_path,
)
from pitwall.workbench.comparison.fixture import (
    ComparisonFixture,
    build_fixture,
    file_snapshot,
    run_node,
    sha256_text,
)
from pitwall.workbench.comparison.lifecycle import (
    called_child_tool,
    correlated_tool_interval,
    count_child_events,
    read_lifecycle_observer,
)
from pitwall.workbench.comparison.metrics import (
    TurnMeasurement,
    UsageTotals,
    add_usage,
    empty_usage,
    measure_child_lifecycle,
    measure_turn,
    summarize_admission_accounting,
    task_accepted,
)
from pitwall.workbench.comparison.recovery import (
    RecoveryDependencies,
    RecoveryDisposition,
    recover_turn,
)
from pitwall.workbench.comparison.session import (
    TINTIN_ENTRY,
    TINTIN_PACKAGE,
    RecordedEvent,
    RpcError,
    RpcSession,
    now_ms,
    pinned_node_module,
)
from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.pi_pin import (
    PI_PACKAGE,
    PINNED_PI_VERSION,
    PINNED_SUBAGENTS_VERSION,
    SUBAGENTS_PACKAGE,
)
from pitwall.workbench.profile import (
    CompiledProfile,
    compile_profile,
    configure_provider_profile,
    profile_from_config,
)
from pitwall.workbench.runtime_settings import derive_compaction_settings, enforce_runtime_settings

STOCK_LABEL = f"{PI_PACKAGE}@{PINNED_PI_VERSION}"
TINTIN_LABEL = f"{SUBAGENTS_PACKAGE}@{PINNED_SUBAGENTS_VERSION}"
STOCK = "A-stock"
NICOBAILON = "B-nicobailon"
TINTIN = "C-tintin"
TASK_FILES = ("notes.txt", "add.mjs", "add.test.mjs")
TEST_COMMAND = "node --test add.test.mjs"
CHECKPOINT_MARKER = "COMPARISON_COMPACT_CHECKPOINT_7F31"
PENDING_ACTION = "read preserve.txt after compaction and report COMPARISON_PROTECTED_CONTENT"
DEFAULT_CANDIDATE_B = ".local/state/pitwall-readiness/pi-workbench/backend-review/fixture/node_modules/pi-subagents/index.ts"
_TASK_ERRORS = (OSError, ValueError, RuntimeError)


class ComparisonUsageError(ValueError):
    """The comparison was invoked with an unusable option combination."""


@dataclass(frozen=True)
class Candidate:
    id: str
    version: str
    surface: str
    extension: Path | None = None


@dataclass(frozen=True)
class ComparisonOptions:
    profile_config: Path
    output_dir: Path
    repetitions: int = 2
    candidates: tuple[str, ...] = ()
    bounded_single: bool = False
    compact_only: bool = False
    automatic_compact_only: bool = False
    child_only: bool = False
    workbench_admission: bool = False
    warm: bool = False
    automatic_filler_words: int = 2600
    automatic_final_filler_words: int = 400
    automatic_prelude_turns: int = 5
    automatic_settle_timeout_ms: int = 90_000
    profile_name: str = "local-coder"


@dataclass(frozen=True)
class TaskResult:
    id: str
    acceptance: str
    effect: JsonObject
    turns: list[TurnMeasurement]
    assistant_text_sha256: str
    assistant_text_bytes: int
    errors: list[str] | None = None

    def to_json(self) -> JsonObject:
        payload: JsonObject = {
            "id": self.id,
            "acceptance": self.acceptance,
            "effect": self.effect,
            "turns": [turn.to_json() for turn in self.turns],
            "assistantTextSha256": self.assistant_text_sha256,
            "assistantTextBytes": self.assistant_text_bytes,
        }
        if self.errors:
            payload["errors"] = list(self.errors)
        return payload


@dataclass(frozen=True)
class Turn:
    text: str
    measurement: TurnMeasurement
    events: list[RecordedEvent]


@dataclass(frozen=True)
class AdmissionSetup:
    env: dict[str, str]
    extensions: list[Path]
    accounting_file: Path | None = None


def parse_comparison_options(
    profile_config: str, output_dir: str, repetitions: str = "2", *flags: str
) -> ComparisonOptions:
    """Build options from the positional arguments and flags of ``pitwall workbench compare``."""
    bounded_single = "--bounded-single" in flags
    try:
        count = int(repetitions)
    except ValueError:
        count = 0
    minimum = 1 if bounded_single else 2
    if count < minimum or count > 3:
        raise ComparisonUsageError(
            f"repetitions must be {'1, 2, or 3' if bounded_single else '2 or 3'}"
        )
    compact_only = "--compact-only" in flags
    automatic = "--automatic-compact-only" in flags
    child_only = "--child-only" in flags
    warm = "--warm" in flags
    if compact_only and automatic:
        raise ComparisonUsageError("choose either --compact-only or --automatic-compact-only")

    def numeric(prefix: str, fallback: int) -> int:
        raw = next((flag[len(prefix) :] for flag in flags if flag.startswith(prefix)), None)
        if raw is None:
            return fallback
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value <= 0:
            raise ComparisonUsageError(f"{prefix} requires a positive integer")
        return value

    requested = tuple(flag for flag in flags if not flag.startswith("--"))
    options = ComparisonOptions(
        profile_config=Path(profile_config),
        output_dir=Path(output_dir),
        repetitions=count,
        candidates=requested,
        bounded_single=bounded_single,
        compact_only=compact_only,
        automatic_compact_only=automatic,
        child_only=child_only,
        workbench_admission="--workbench-admission" in flags,
        warm=warm,
        automatic_filler_words=numeric("--automatic-filler-words=", 2600),
        automatic_final_filler_words=numeric("--automatic-final-filler-words=", 400),
        automatic_prelude_turns=numeric("--automatic-prelude-turns=", 5),
        automatic_settle_timeout_ms=numeric("--automatic-settle-timeout-ms=", 90_000),
    )
    if warm and (
        compact_only or automatic or child_only or bounded_single or options.repetitions != 2
    ):
        raise ComparisonUsageError(
            "--warm runs the core tasks twice in one session; use it with 2 repetitions and no other mode"
        )
    return options


def _text_of(events: Sequence[RecordedEvent]) -> str:
    texts: list[str] = []
    for entry in events:
        message = get_object(entry.event, "message")
        if entry.event.get("type") != "message_end" or get_str(message, "role") != "assistant":
            continue
        for part in get_list(message, "content") or []:
            item = as_object(part)
            if item is not None and item.get("type") == "text":
                texts.append(str(item.get("text")))
    return "\n".join(texts)


def _event_tools(events: Sequence[RecordedEvent]) -> list[str]:
    return [
        str(entry.event["toolName"])
        for entry in events
        if entry.event.get("type") == "tool_execution_start"
        and isinstance(entry.event.get("toolName"), str)
    ]


def _provider_usage(value: object) -> UsageTotals:
    usage = as_object(value) or {}
    return UsageTotals(
        first_non_negative(usage, "input", "inputTokens", "prompt_tokens"),
        first_non_negative(usage, "output", "outputTokens", "completion_tokens"),
        first_non_negative(usage, "cacheRead", "cache_read_input_tokens"),
        first_non_negative(usage, "cacheWrite", "cache_creation_input_tokens"),
        first_non_negative(usage, "reasoning", "reasoningTokens"),
        1,
        1,
    )


def _error_text(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _read_json(path: Path) -> object:
    return json.loads(path.read_text())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sum_known(values: Sequence[float | None]) -> float | None:
    return (
        None if any(value is None for value in values) else sum(v for v in values if v is not None)
    )


def _session_identity(state: JsonObject) -> str | None:
    for key in ("sessionFile", "sessionPath", "sessionId"):
        value = as_str(state.get(key))
        if value is not None:
            return value
    return None


class ComparisonRunner:
    """One bounded comparison batch over the selected candidates."""

    def __init__(self, options: ComparisonOptions) -> None:
        self.options = options
        config = json.loads(Path(os.path.abspath(options.profile_config)).read_text())
        self.profile = profile_from_config(config, options.profile_name)
        key_env = self.profile.get("apiKeyEnv")
        if not key_env or not os.environ.get(key_env):
            raise ComparisonUsageError(f"comparison requires {key_env} in the runner environment")
        self.output_dir = Path(os.path.abspath(options.output_dir))
        self.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Candidate B is an isolated fixture outside this repository; C is the pinned Tintin backend.
        candidate_b = os.environ.get("PITWALL_WORKBENCH_CANDIDATE_B_EXTENSION")
        tintin = pinned_node_module(TINTIN_PACKAGE, TINTIN_ENTRY)
        self.candidates = [
            Candidate(
                STOCK,
                STOCK_LABEL,
                "stock Pi tools; child launch unsupported",
            ),
            Candidate(
                NICOBAILON,
                "pi-subagents@0.69.0",
                "stripped installed pi-subagents candidate",
                Path(candidate_b) if candidate_b else Path.home() / DEFAULT_CANDIDATE_B,
            ),
            Candidate(
                TINTIN,
                TINTIN_LABEL,
                "installed Tintin backend extension; no Workbench adapter claim",
                tintin,
            ),
        ]
        requested = options.candidates
        if requested:
            self.selected = [c for c in self.candidates if c.id in requested]
        else:
            self.selected = [c for c in self.candidates if not options.child_only or c.id != STOCK]
        if not self.selected:
            ids = ", ".join(c.id for c in self.candidates)
            raise ComparisonUsageError(f"unknown candidate; choose {ids}")
        if options.child_only and any(c.id == STOCK for c in self.selected):
            raise ComparisonUsageError(
                "--child-only requires B-nicobailon and/or C-tintin; stock Pi has no child surface"
            )
        self.reasoning_level = str(self.profile.get("reasoningLevel") or "low")

    # -- session plumbing -----------------------------------------------------------------------

    def _start_session(
        self,
        candidate: Candidate,
        cwd: Path,
        compiled: CompiledProfile,
        env: dict[str, str],
        admission: AdmissionSetup,
    ) -> RpcSession:
        child = launch_pi(
            PiLaunchOptions(
                cwd=cwd,
                profile=compiled,
                extension=candidate.extension,
                extensions=[extension_path("comparison-lifecycle-observer"), *admission.extensions],
                env=env,
                passthrough_env=["COMPARISON_FIXTURE", "COMPARISON_LIFECYCLE_PATH"],
            )
        )
        return RpcSession(
            child,
            id_prefix="comparison",
            max_observed=4000,
            default_timeout_ms=90_000,
        )

    def _turn(
        self,
        session: RpcSession,
        message: str,
        extra: JsonObject | None = None,
        settle_timeout_ms: int = 90_000,
    ) -> Turn:
        start_seq = session.last_seq()
        started_at = now_ms()
        session.request("prompt", {"message": message, **(extra or {})})
        session.wait_event("agent_settled", start_seq, settle_timeout_ms)
        events = session.events_after(start_seq)
        finished_at = now_ms()
        return Turn(
            _text_of(events),
            measure_turn([e.event for e in events], started_at, finished_at),
            events,
        )

    def _recover(self, session: RpcSession) -> RecoveryDisposition:
        after = session.last_seq()
        return recover_turn(
            RecoveryDependencies(
                request=lambda kind, fields, timeout_ms: session.request(kind, fields, timeout_ms),
                wait_for_settled=lambda: session.wait_event("agent_settled", after, 10_000),
                invalidate=session.invalidate,
            )
        )

    def _settle_timeout(self) -> int:
        return (
            self.options.automatic_settle_timeout_ms
            if self.options.automatic_compact_only
            else 90_000
        )

    # -- tasks ----------------------------------------------------------------------------------

    def _run_task(
        self,
        session: RpcSession,
        task_id: str,
        prompts: Sequence[str],
        acceptance: Callable[[str, list[RecordedEvent]], JsonObject],
    ) -> TaskResult:
        turns: list[TurnMeasurement] = []
        events: list[RecordedEvent] = []
        text = ""
        errors: list[str] = []
        for prompt in prompts:
            try:
                turn = self._turn(session, prompt, None, self._settle_timeout())
                turns.append(turn.measurement)
                events.extend(turn.events)
                text += f"{turn.text}\n"
            except RpcError as error:
                errors.append(_error_text(error))
                recovery = self._recover(session)
                if not recovery.settled:
                    errors.append(f"downstream unavailable: {recovery.reason}")
                break
        try:
            effect = acceptance(text, events)
            tools = [tool for turn in turns for tool in turn.tools]
            effect["toolsObserved"] = tools
            requires_search_read = effect.get("requiredToolKind") == "search-read"
            effect["accepted"] = (
                effect.get("accepted") is True
                and len(tools) > 0
                and (
                    not requires_search_read
                    or any(re.search(r"read|grep|find|bash", tool, re.I) for tool in tools)
                )
            )
            return TaskResult(
                task_id,
                "passed" if task_accepted(effect, errors) else "failed",
                effect,
                turns,
                sha256_text(text),
                len(text.encode()),
                errors or None,
            )
        except _TASK_ERRORS as error:
            errors.append(_error_text(error))
            return TaskResult(
                task_id, "failed", {}, turns, sha256_text(text), len(text.encode()), errors
            )

    def _run_forced_repair_task(
        self, session: RpcSession, cwd: Path, protected_hash: str
    ) -> TaskResult:
        turns: list[TurnMeasurement] = []
        text = ""
        errors: list[str] = []
        forced_failure_observed = first_failure_result = False
        tests_sha256 = ""
        first_check = None
        none = command_evidence([], TEST_COMMAND)
        first_command = final_command = none
        try:
            first = self._turn(
                session,
                "Create add.mjs exporting add(a,b) and add.test.mjs testing add(2,3)=5 and "
                "add(-2,3)=1. Intentionally implement add incorrectly, run node --test add.test.mjs "
                "to produce a failure, and leave add.mjs broken for the next turn. Do not change "
                "preserve.txt.",
                None,
                self._settle_timeout(),
            )
            turns.append(first.measurement)
            text += f"{first.text}\n"
            first_check = run_node(cwd, ["--test", "add.test.mjs"])
            tests_sha256 = sha256_text((cwd / "add.test.mjs").read_text())
            first_command = command_evidence([e.event for e in first.events], TEST_COMMAND)
            first_failure_result = assertion_failure_observed(first_check.output)
            forced_failure_observed = (
                first_check.code != 0
                and first_failure_result
                and first_command.started
                and first_command.completed
                and first_command.failed
            )
            if not forced_failure_observed:
                errors.append("forced failure was not observed between turns")
            if sha256_text((cwd / "preserve.txt").read_text()) != protected_hash:
                errors.append("protected file changed before repair")
            second = self._turn(
                session,
                "Now inspect the failing test result, repair only add.mjs, and run node --test "
                "add.test.mjs again. Leave add.test.mjs and preserve.txt unchanged.",
                None,
                self._settle_timeout(),
            )
            final_command = command_evidence([e.event for e in second.events], TEST_COMMAND)
            turns.append(second.measurement)
            text += f"{second.text}\n"
        except _TASK_ERRORS as error:
            errors.append(_error_text(error))
            recovery = self._recover(session)
            if not recovery.settled:
                errors.append(f"downstream unavailable: {recovery.reason}")
        try:
            after = run_node(cwd, ["--test", "add.test.mjs"])
            add = (cwd / "add.mjs").read_text()
            tests = (cwd / "add.test.mjs").read_text()
            tests_have_oracle = generated_tests_have_oracle(tests)
            host_oracle = run_node(
                cwd,
                [
                    "--input-type=module",
                    "-e",
                    "import { add } from './add.mjs'; if (add(2, 3) !== 5 || add(-2, 3) !== 1) process.exit(1);",
                ],
            )
            suite = suite_passed(after.code, after.output)
            tests_unchanged = tests_sha256 == sha256_text(tests)
            protected_unchanged = sha256_text((cwd / "preserve.txt").read_text()) == protected_hash
            accepted = repair_accepted(
                RepairInput(
                    forced_failure_observed=forced_failure_observed,
                    first_failure_result=first_failure_result,
                    final_command=final_command,
                    tests_have_oracle=tests_have_oracle,
                    test_suite_passed=suite,
                    host_oracle_exit_code=host_oracle.code,
                    tests_unchanged=tests_unchanged,
                    protected_unchanged=protected_unchanged,
                )
            )
            effect: JsonObject = {
                "accepted": accepted,
                "forcedFailureObserved": forced_failure_observed,
                "firstFailureResult": first_failure_result,
                "firstCommand": first_command.to_json(),
                "finalCommand": final_command.to_json(),
                "testsHaveOracle": tests_have_oracle,
                "testSuitePassed": suite,
                "hostOracleExitCode": host_oracle.code,
                "hostOracleDurationMs": host_oracle.duration_ms,
                "forcedFailureDurationMs": first_check.duration_ms if first_check else None,
                "finalExitCode": after.code,
                "finalDurationMs": after.duration_ms,
                "finalOutputSha256": sha256_text(after.output),
                "implementation": add,
                "testsSha256": sha256_text(tests),
                "testsUnchanged": tests_unchanged,
                "protectedUnchanged": protected_unchanged,
            }
            return TaskResult(
                "forced-failure-repair",
                "passed" if task_accepted(effect, errors) else "failed",
                effect,
                turns,
                sha256_text(text),
                len(text.encode()),
                errors or None,
            )
        except _TASK_ERRORS as error:
            errors.append(_error_text(error))
            return TaskResult(
                "forced-failure-repair",
                "failed",
                {},
                turns,
                sha256_text(text),
                len(text.encode()),
                errors,
            )

    def _run_core_tasks(
        self, session: RpcSession, cwd: Path, oracle_path: Path, protected_hash: str
    ) -> list[TaskResult]:
        def protected() -> bool:
            return sha256_text((cwd / "preserve.txt").read_text()) == protected_hash

        def create_read_edit_read(text: str, events: list[RecordedEvent]) -> JsonObject:
            notes = (cwd / "notes.txt").read_text()
            return {
                "accepted": notes in ("FIRST_LINE\nSECOND_LINE", "FIRST_LINE\nSECOND_LINE\n")
                and "SECOND_LINE" in text,
                "notes": notes,
                "protectedUnchanged": protected(),
            }

        def medium_search(text: str, events: list[RecordedEvent]) -> JsonObject:
            expected_path = oracle_path.relative_to(cwd).as_posix()
            oracle = run_node(
                cwd,
                [
                    "--input-type=module",
                    "-e",
                    f"import {{ add }} from './{expected_path}'; if (add(-2, 3) !== 1) process.exit(1);",
                ],
            )
            answer_matched = medium_search_accepted(text, expected_path, [e.event for e in events])
            return {
                "accepted": answer_matched and oracle.code == 0,
                "requiredToolKind": "search-read",
                "oraclePath": expected_path,
                "oracleExitCode": oracle.code,
                "oracleDurationMs": oracle.duration_ms,
                "answerMatched": answer_matched,
                "protectedUnchanged": protected(),
            }

        return [
            self._run_task(
                session,
                "create-read-edit-read",
                [
                    "Create notes.txt containing exactly FIRST_LINE. Do not change preserve.txt. Use a "
                    "file tool and report only after the file exists.",
                    "Read notes.txt, append a second line SECOND_LINE, then read it again. Do not "
                    "change preserve.txt.",
                    "Read notes.txt and report its exact two lines. Do not change any file.",
                ],
                create_read_edit_read,
            ),
            self._run_forced_repair_task(session, cwd, protected_hash),
            self._run_task(
                session,
                "medium-search",
                [
                    "Search the medium directory recursively for the sector-7 implementation whose "
                    "exported add function returns 1 for inputs (-2, 3). Do not edit files. Report the "
                    "exact relative path, exported function name, and return expression."
                ],
                medium_search,
            ),
        ]

    # -- admission ------------------------------------------------------------------------------

    def _admission_for(
        self, compiled: CompiledProfile, cwd: Path, run_root: Path
    ) -> AdmissionSetup:
        """Configure before the baseline snapshot: provider-profile setup may write runtime settings."""
        if not self.options.workbench_admission:
            return AdmissionSetup({}, [])
        configured = configure_provider_profile(compiled, cwd)
        resource_dir = Path(tempfile.mkdtemp(prefix="admission-", dir=run_root))
        return AdmissionSetup(
            {
                "PITWALL_WORKBENCH_PROVIDER_PROFILE": str(configured.profile_path),
                "PITWALL_WORKBENCH_RESOURCE_DIR": str(resource_dir),
            },
            [extension_path("extension")],
            configured.profile_path.parent / "native-accounting.jsonl",
        )

    def _admission_accounting(self, admission: AdmissionSetup, since: int = 0) -> JsonObject:
        if admission.accounting_file is None:
            return {
                "instrumented": False,
                "queueMs": None,
                "requestCount": 0,
                "settledCount": 0,
                "unavailable": 0,
                "queueScope": "Workbench provider-admission wrapper not attached",
                "upstreamQueue": "unknown",
                "reason": "comparison sessions load stock/candidate extensions only; the Workbench "
                "provider-admission extension is not attached, so upstream queue wait is unobservable",
            }
        try:
            raw = admission.accounting_file.read_text()
        except OSError:
            # No request reached the wrapper.
            raw = ""
        records: list[object] = []
        for line in raw.split("\n"):
            if not line:
                continue
            record = as_object(json.loads(line))
            queued = as_number((record or {}).get("queuedAt"))
            if record is not None and (queued is None or queued >= since):
                records.append(record)
        return {
            **summarize_admission_accounting(records).to_json(),
            "queueScope": "Workbench provider-admission lock for this run's resource group",
            "upstreamQueue": "unknown",
            "reason": "Workbench provider-admission extension attached; queueMs sums the lock waits "
            "(acquiredAt - queuedAt)",
        }

    # -- one candidate repetition ---------------------------------------------------------------

    def _child_phase(
        self,
        candidate: Candidate,
        session: RpcSession,
        agent_dir: Path,
        cwd: Path,
        state: JsonObject,
        reasoning: JsonObject,
        lifecycle_path: Path,
        auxiliary: list[TurnMeasurement],
    ) -> tuple[JsonObject, UsageTotals]:
        """Run the foreground child task and gather its persisted evidence."""
        profile = self.profile
        child_usage = empty_usage()
        child_start = session.last_seq()
        try:
            child = self._turn(
                session,
                "Use the candidate child tool to launch exactly one foreground child of the explicit "
                "Explore agent type (call the field subagent_type or agent as the installed schema "
                f"requires), with the configured {reasoning['level']} thinking level inherited from "
                "this parent. The child must be read-only: read child-probe.txt and return the exact "
                f"marker {CHILD_MARKER}, then finish. Wait for that child to finish and report its "
                "exact result. Do not edit any file and do not choose reviewer, worker, "
                "general-purpose, or another agent type.",
                None,
                self._settle_timeout(),
            )
            auxiliary.append(child.measurement)
            child_events = session.events_after(child_start)
            lifecycle = count_child_events([e.event for e in child_events])
            called = called_child_tool(_event_tools(child_events))
            model_marker_observed = CHILD_MARKER in child.text
            completion_wall_ms: float | None = None
            receipt = child_receipt([e.event for e in child_events])
            status_inspected = False
            if candidate.id == NICOBAILON and receipt.child_id and not receipt.completed:
                status_turn = self._turn(
                    session,
                    "Inspect the already-launched child run using the candidate's supported status "
                    f'tool: call subagent({{ action: "status", id: "{receipt.child_id}", view: '
                    '"transcript" }) once. Wait for its terminal result and report the exact child '
                    "result; do not launch another child.",
                    None,
                    self._settle_timeout(),
                )
                auxiliary.append(status_turn.measurement)
                completion_wall_ms = status_turn.measurement.wall_ms
                status_inspected = True
                child_events = session.events_after(child_start)
                receipt = child_receipt([e.event for e in child_events])
            if receipt.completed and completion_wall_ms is None:
                completion_wall_ms = child.measurement.wall_ms
            parent_session_file = _session_identity_file(state) or find_parent_session_file(
                agent_dir, cwd
            )
            persisted = persisted_child_evidence(
                agent_dir, cwd, CHILD_MARKER, receipt, parent_session_file
            )
            child_usage = persisted.child_usage
            observed = read_lifecycle_observer(lifecycle_path, session.child.pid)
            child_timing = measure_child_lifecycle([*(e.event for e in child_events), *observed])
            durable_parent_record = False
            if receipt.child_id and parent_session_file:
                try:
                    durable_parent_record = durable_child_record_matches(
                        Path(resolve_path(agent_dir, parent_session_file)).read_text(),
                        receipt.child_id,
                        CHILD_MARKER,
                    )
                except OSError:
                    # Durable parent evidence remains unavailable.
                    durable_parent_record = False
            durable_marker = (
                receipt.result_marker is True
                or durable_parent_record
                or (persisted.correlated and model_marker_observed)
            )
            model_observed = (
                child_model_matches(
                    [receipt.model_name], str(profile["modelId"]), str(profile["provider"])
                )
                if receipt.model_name
                else False
            )
            # --child-only is the matched-reasoning fixture and therefore requires an authoritative
            # persisted child read. The general comparison keeps the narrower public candidate
            # contract: a completed, exact-model receipt plus its durable parent record is
            # sufficient when a backend does not expose a separate child session file (Tintin C).
            if self.options.child_only:
                passed = child_execution_evidence_accepted(
                    invoked=called,
                    completed=receipt.completed,
                    model_matched=model_observed,
                    persisted_read=persisted.correlated,
                    result_marker=durable_marker,
                )
            else:
                passed = durable_child_execution_evidence_accepted(
                    invoked=called,
                    completed=receipt.completed,
                    model_matched=model_observed,
                    result_marker=durable_marker,
                )
            parent_level = profile.get("reasoningLevel")
            levels = persisted.child_thinking_levels
            evidence: JsonObject = {
                "status": "passed" if passed else "unexecuted",
                "launchStatus": "observed" if called else "unexecuted",
                "completionStatus": "observed"
                if receipt.completed or status_inspected
                else "unexecuted",
                "launchWallMs": child.measurement.wall_ms,
                "completionWallMs": completion_wall_ms,
                "parentToolIntervalMs": correlated_tool_interval(
                    [e.event for e in child_events], receipt.tool_call_id
                ),
                "persistedChildIntervalMs": persisted.session_timing.duration_ms
                if persisted.session_timing
                else None,
                "lifecycleTiming": child_timing.to_json(),
                "tools": _event_tools(child_events),
                "lifecycle": lifecycle,
                "observedLifecycleEvents": len(observed),
                "statusInspected": status_inspected,
                "modelMarkerObserved": model_marker_observed,
                "durableResultMarker": receipt.result_marker is True,
                "durableParentRecord": durable_parent_record,
                "modelObserved": model_observed,
                "parentReasoningLevel": parent_level,
                "childThinkingLevels": levels,
                "reasoningControlMatch": all(level == parent_level for level in levels)
                if levels
                else None,
                "childUsage": persisted.child_usage.to_json(),
                "toolResult": {
                    "toolCallId": receipt.tool_call_id,
                    "childId": receipt.child_id,
                    "completed": receipt.completed,
                    "modelName": receipt.model_name,
                },
                "persistedChildEvidence": persisted.to_json(),
                "reason": (
                    "completed child result correlated to the selected child session"
                    if persisted.correlated
                    else persisted.reason
                    or (
                        "completed child result correlated to the durable parent backend record"
                        if durable_parent_record
                        else "child result lacked a correlated durable backend record"
                    )
                )
                if called
                else "model did not invoke a candidate child surface",
            }
            return evidence, child_usage
        except _TASK_ERRORS as error:
            self._recover(session)
            return {"status": "failed", "error": _error_text(error)}, child_usage

    def _compaction_phase(
        self,
        session: RpcSession,
        cwd: Path,
        protected_hash: str,
        auxiliary: list[TurnMeasurement],
    ) -> tuple[JsonObject, UsageTotals]:
        options = self.options
        automatic = options.automatic_compact_only
        attempted = False
        try:
            if options.child_only:
                raise ComparisonUsageError("child-only mode skips compaction")
            # Keep automatic turns small enough that a completed response crosses the derived
            # threshold before the provider truncates the next response. A length-truncated
            # response is correctly classified by Pi as overflow.
            filler = " ".join(
                f"retained-history-{i % 17}"
                for i in range(options.automatic_filler_words if automatic else 2500)
            )
            final_filler = " ".join(
                f"retained-history-final-{i % 17}"
                for i in range(options.automatic_final_filler_words)
            )
            compact_start = session.last_seq()
            attempted = True
            compaction_event: JsonObject | None = None
            compact_data: JsonObject = {}
            prelude_turns = 0

            def prelude_prompt(index: int, body: str) -> str:
                return (
                    "Record this bounded history checkpoint without using tools. Marker "
                    f"{CHECKPOINT_MARKER}. Acceptance criteria are add(2,3)=5 and add(-2,3)=1. "
                    f"Protected file preserve.txt must remain unchanged. Pending action: {PENDING_ACTION}. "
                    f"This is history block {index}; retain the marker and pending action. {body}"
                )

            def find_compaction_end() -> JsonObject | None:
                for entry in session.events_after(compact_start):
                    if entry.event.get("type") == "compaction_end" and entry.event.get("result"):
                        return entry.event
                return None

            if automatic:
                # Automatic mode must observe Pi's threshold event during a normal prompt turn. No
                # compact RPC is sent in this branch.
                for index in range(1, options.automatic_prelude_turns + 1):
                    prelude = self._turn(
                        session,
                        prelude_prompt(index, final_filler if index == 7 else filler),
                        None,
                        self._settle_timeout(),
                    )
                    auxiliary.append(prelude.measurement)
                    prelude_turns += 1
                    compaction_event = find_compaction_end()
                    if compaction_event:
                        break
                if not compaction_event:
                    raise RpcError(
                        "automatic threshold compaction did not fire during bounded history turns"
                    )
                compact_data = as_object(compaction_event.get("result")) or {}
            else:
                for index in range(1, 5):
                    prelude = self._turn(
                        session, prelude_prompt(index, filler), None, self._settle_timeout()
                    )
                    auxiliary.append(prelude.measurement)
                    prelude_turns += 1
                compact = session.request(
                    "compact",
                    {
                        "customInstructions": "Preserve the checkpoint marker, acceptance criteria, "
                        "protected file state, and pending reread action without changing files."
                    },
                )
                time.sleep(0.05)
                compact_data = as_object(compact.get("data")) or {}
                compaction_event = find_compaction_end()
            summary = str(compact_data.get("summary") or "")
            usage = (
                _provider_usage(compact_data["usage"])
                if compact_data.get("usage")
                else empty_usage()
            )
            event_seen = compaction_event is not None
            continuation = self._turn(
                session,
                "Continue after compaction. Perform the pending action: read preserve.txt with a file "
                "tool without changing files, then report the checkpoint marker, acceptance criteria, "
                "and exact protected contents.",
                None,
                self._settle_timeout(),
            )
            auxiliary.append(continuation.measurement)
            protected_contents = (cwd / "preserve.txt").read_text()
            summary_facts = compaction_summary_facts(summary)
            said = continuation.text
            continuation_facts = (
                CHECKPOINT_MARKER in said
                and bool(re.search(r"add\s*\(\s*2\s*,\s*3\s*\)\s*=\s*5", said))
                and bool(re.search(r"add\s*\(\s*-2\s*,\s*3\s*\)\s*=\s*1", said))
                and protected_contents.strip() in said
            )
            continuation_read = preserve_read_evidence(
                [e.event for e in continuation.events], "preserve.txt", str(cwd)
            )
            reason = (compaction_event or {}).get("reason")
            if automatic:
                trigger = (
                    "automatic-threshold"
                    if reason == "threshold"
                    else "automatic-overflow"
                    if reason == "overflow"
                    else "automatic-unclassified"
                )
            else:
                trigger = "forced-rpc"
            trigger_accepted = not automatic or trigger == "automatic-threshold"
            protected_ok = sha256_text(protected_contents) == protected_hash
            passed = (
                trigger_accepted
                and event_seen
                and summary_facts.all()
                and continuation_facts
                and continuation_read
                and len(continuation.measurement.tools) > 0
                and protected_ok
            )
            evidence: JsonObject = {
                "status": "passed" if passed else "failed",
                "trigger": trigger,
                "compactRpcIssued": not automatic,
                "compactionEventObserved": event_seen,
                "compactionEventReason": reason,
                "triggerAccepted": trigger_accepted,
                "semanticRetention": "summary facts plus independent continuation read; no prose-only acceptance",
                "summaryFacts": summary_facts.to_json(),
                "continuationFacts": continuation_facts,
                "continuationRead": continuation_read,
                "preludeTurns": prelude_turns,
                "tokensBefore": compact_data.get("tokensBefore"),
                "estimatedTokensAfter": compact_data.get("estimatedTokensAfter"),
                "summarySha256": sha256_text(summary),
                "summaryLength": len(summary),
                "continuationTools": continuation.measurement.tools,
                "continuationWallMs": continuation.measurement.wall_ms,
                "continuationModelElapsedMs": continuation.measurement.model_elapsed_ms,
                "usage": usage.to_json(),
                "protectedUnchanged": protected_ok,
            }
            return evidence, usage
        except _TASK_ERRORS as error:
            return (
                {"status": "failed" if attempted else "unexecuted", "reason": _error_text(error)},
                empty_usage(),
            )

    def _run_candidate(
        self, candidate: Candidate, repetition: int, root: Path, compact_only_run: bool = False
    ) -> JsonObject:
        options = self.options
        profile = self.profile
        automatic = options.automatic_compact_only
        run_root = Path(tempfile.mkdtemp(prefix=f"{candidate.id}-rep-{repetition}-", dir=root))
        fixture: ComparisonFixture = build_fixture(run_root, self.reasoning_level)
        cwd = fixture.cwd
        agent_dir = Path(tempfile.mkdtemp(prefix="agent-", dir=run_root))
        home_dir = Path(tempfile.mkdtemp(prefix="home-", dir=run_root))
        compiled = compile_profile(options.profile_name, profile, agent_dir)
        accounting_path = run_root / "accounting.jsonl"
        admission = self._admission_for(compiled, cwd, run_root)
        initial_snapshot = file_snapshot(cwd)
        tree_hash_before = protected_tree_hash(initial_snapshot, TASK_FILES)
        if automatic:
            enforce_runtime_settings(agent_dir, cwd, profile)
        lifecycle_path = run_root / "comparison-lifecycle.jsonl"
        session = self._start_session(
            candidate,
            cwd,
            compiled,
            {
                "HOME": str(home_dir),
                "PITWALL_WORKBENCH_ACCOUNTING_PATH": str(accounting_path),
                "COMPARISON_FIXTURE": str(cwd),
                "COMPARISON_LIFECYCLE_PATH": str(lifecycle_path),
                **admission.env,
            },
            admission,
        )
        run_started = now_ms()
        tasks: list[TaskResult] = []
        auxiliary: list[TurnMeasurement] = []
        compact_usage = empty_usage()
        child_usage = empty_usage()
        compact_evidence: JsonObject = {"status": "unexecuted", "reason": "not attempted"}
        child_evidence: JsonObject = {
            "status": "unexecuted",
            "reason": "stock Pi has no child surface"
            if candidate.id == STOCK
            else "candidate child surface was not invoked",
        }
        base: JsonObject = {
            "candidate": candidate.id,
            "candidateVersion": candidate.version,
            "surface": candidate.surface,
            "repetition": repetition,
        }
        try:
            state = session.response_data("get_state")
            session.request("set_auto_retry", {"enabled": False})
            session.request("set_auto_compaction", {"enabled": automatic})
            protected_hash = sha256_text((cwd / "preserve.txt").read_text())
            if not compact_only_run and not automatic and not options.child_only:
                tasks.extend(
                    self._run_core_tasks(session, cwd, fixture.oracle_path, protected_hash)
                )
            if (
                (not compact_only_run and not automatic) or options.child_only
            ) and candidate.id != STOCK:
                child_evidence, child_usage = self._child_phase(
                    candidate,
                    session,
                    agent_dir,
                    cwd,
                    state,
                    fixture.child_reasoning_fixture.to_json(),
                    lifecycle_path,
                    auxiliary,
                )
            compact_evidence, compact_usage = self._compaction_phase(
                session, cwd, protected_hash, auxiliary
            )
            final_snapshot = file_snapshot(cwd)
            changed = sorted(
                path
                for path in {*initial_snapshot, *final_snapshot}
                if initial_snapshot.get(path) != final_snapshot.get(path)
            )
            tree_hash_after = protected_tree_hash(final_snapshot, TASK_FILES)
            tree_unchanged = tree_hash_before == tree_hash_after
            protected_unchanged = sha256_text((cwd / "preserve.txt").read_text()) == protected_hash
            accepted = sum(1 for task in tasks if task.acceptance == "passed")
            all_turns = [*(turn for task in tasks for turn in task.turns), *auxiliary]
            captured = add_usage(
                compact_usage,
                child_usage,
            )
            for turn in all_turns:
                captured = add_usage(captured, turn.usage)
            truncated = session.was_capture_truncated()
            all_usage = (
                UsageTotals(
                    None,
                    None,
                    None,
                    None,
                    None,
                    captured.reported_messages,
                    captured.assistant_messages,
                )
                if truncated
                else captured
            )
            accounting = self._admission_accounting(admission)
            skip_core = compact_only_run or automatic or options.child_only
            core_status = (
                "unexecuted" if skip_core else ("passed" if accepted == len(tasks) else "failed")
            )
            identity_model = get_object(state, "model") or {}
            identity = {
                "provider": identity_model.get("provider"),
                "modelId": identity_model.get("id"),
                "exact": identity_model.get("provider") == profile["provider"]
                and identity_model.get("id") == profile["modelId"],
            }
            if candidate.id == STOCK:
                child_acceptance = False
            elif options.child_only:
                child_acceptance = (
                    child_evidence.get("status") == "passed"
                    and child_evidence.get("reasoningControlMatch") is True
                )
            else:
                child_acceptance = child_evidence.get("status") == "passed"
            if options.child_only:
                mode_ok = child_acceptance
            else:
                mode_ok = (
                    core_status == "passed"
                    and compact_evidence.get("status") == "passed"
                    and (
                        child_evidence.get("status") == "unexecuted"
                        if candidate.id == STOCK
                        else child_acceptance
                    )
                )
            overall = (
                "passed"
                if mode_ok
                and identity["exact"]
                and protected_unchanged
                and tree_unchanged
                and not truncated
                else "partial"
            )
            warm_reuse: JsonObject = (
                {
                    "status": "passed",
                    "scope": "continued turns in the same native session",
                    "turnCount": len(all_turns),
                }
                if len(all_turns) > 1
                else {
                    "status": "unexecuted",
                    "reason": "fewer than two completed turns were observed",
                }
            )
            model_elapsed = _sum_known([t.model_elapsed_ms for t in all_turns])
            non_tool = _sum_known([t.non_tool_elapsed_ms for t in all_turns])
            tool_exec = _sum_known([t.tool_execution_ms for t in all_turns])
            first_request = next(
                (
                    t.first_request_usage
                    for t in all_turns
                    if t.first_request_usage.assistant_messages > 0
                ),
                empty_usage(),
            )
            first_input = first_request.input
            test_execution_ms = sum(
                sum(
                    value
                    for value in (
                        task.effect.get("forcedFailureDurationMs"),
                        task.effect.get("finalDurationMs"),
                        task.effect.get("hostOracleDurationMs"),
                        task.effect.get("oracleDurationMs"),
                    )
                    if isinstance(value, int | float) and not isinstance(value, bool)
                )
                for task in tasks
            )
            unavailable = session.unavailable_reason()
            rate: object = "unexecuted" if skip_core else f"{accepted}/{len(tasks)}"
            derived = _derived_compaction(profile) if automatic else None
            return {
                **base,
                "compactOnly": compact_only_run or automatic,
                "automaticCompactOnly": automatic,
                "childOnly": options.child_only,
                "coreTasksStatus": core_status,
                "overallStatus": overall,
                "fixtureRevision": fixture.revision,
                "mediumFixture": {
                    "files": fixture.medium_stats.files,
                    "lines": fixture.medium_stats.lines,
                    "bytes": fixture.medium_stats.bytes,
                },
                "childReasoningFixture": fixture.child_reasoning_fixture.to_json(),
                "freshSession": True,
                "warmReuse": warm_reuse,
                "warmAcrossRepetitions": {
                    "status": "unexecuted",
                    "reason": "each repetition uses a fresh disposable fixture and native session",
                },
                "profile": {
                    "provider": profile["provider"],
                    "modelId": profile["modelId"],
                    "endpoint": profile["endpoint"],
                    "resourceGroup": profile["resourceGroup"],
                    "reasoningLevel": profile.get("reasoningLevel"),
                    "derivedCompaction": derived,
                },
                "identity": identity,
                "tasks": [task.to_json() for task in tasks],
                "acceptedTaskRate": rate,
                "failureInclusiveAcceptedTaskRate": rate,
                "usage": all_usage.to_json(),
                "firstRequestUsage": first_request.to_json(),
                "firstRequestInput": first_input,
                "compactionUsage": compact_usage.to_json(),
                "accounting": accounting,
                "timing": {
                    "wallMs": now_ms() - run_started,
                    "modelElapsedMs": model_elapsed,
                    "nonToolElapsedMs": non_tool,
                    "toolExecutionMs": tool_exec,
                    "testExecutionMs": test_execution_ms,
                    "queueMs": accounting.get("queueMs"),
                    "queueDisposition": accounting.get("reason"),
                },
                "phases": {
                    "fresh": {
                        "status": "unmeasured" if first_input is None else "measured",
                        "firstRequestUsage": first_request.to_json(),
                        "firstRequestInput": first_input,
                    },
                    "warm": warm_reuse,
                    "child": child_evidence,
                    "postCompaction": {
                        "status": "unexecuted"
                        if options.child_only
                        else compact_evidence.get("status"),
                        "continuationWallMs": compact_evidence.get("continuationWallMs"),
                    },
                },
                "compact": {"status": "unexecuted", "reason": "child-only batch"}
                if options.child_only
                else compact_evidence,
                "child": child_evidence,
                "protectedUnchanged": protected_unchanged,
                "protectedTreeHashBefore": tree_hash_before,
                "protectedTreeHashAfter": tree_hash_after,
                "protectedTreeUnchanged": tree_unchanged,
                "filesChangedOutsideExpected": [name for name in changed if name not in TASK_FILES],
                "stderrBytes": session.stderr_bytes,
                "capture": capture_disposition(truncated).to_json(),
                "sessionAvailability": {"status": "unavailable", "reason": unavailable}
                if unavailable
                else {"status": "available"},
                "protocolError": None,
            }
        except _TASK_ERRORS as error:
            unavailable = session.unavailable_reason()
            return {
                **base,
                "compactOnly": compact_only_run or automatic,
                "automaticCompactOnly": automatic,
                "coreTasksStatus": "unexecuted" if compact_only_run or automatic else "failed",
                "overallStatus": "partial",
                "warmReuse": {
                    "status": "unexecuted",
                    "reason": "run failed before a warm reuse measurement",
                },
                "capture": capture_disposition(session.was_capture_truncated()).to_json(),
                "error": _error_text(error),
                "compact": compact_evidence,
                "child": child_evidence,
                "sessionAvailability": {"status": "unavailable", "reason": unavailable}
                if unavailable
                else {"status": "available"},
                "stderrBytes": session.stderr_bytes,
            }
        finally:
            session.stop()

    # -- two repetitions in one native session --------------------------------------------------

    def _run_warm_candidate(self, candidate: Candidate, root: Path) -> list[JsonObject]:
        profile = self.profile
        run_root = Path(tempfile.mkdtemp(prefix=f"{candidate.id}-warm-", dir=root))
        fixture = build_fixture(run_root, self.reasoning_level)
        cwd = fixture.cwd
        agent_dir = Path(tempfile.mkdtemp(prefix="agent-", dir=run_root))
        home_dir = Path(tempfile.mkdtemp(prefix="home-", dir=run_root))
        compiled = compile_profile(self.options.profile_name, profile, agent_dir)
        admission = self._admission_for(compiled, cwd, run_root)
        initial_snapshot = file_snapshot(cwd)
        tree_hash_before = protected_tree_hash(initial_snapshot, TASK_FILES)
        original: dict[str, str | None] = {}
        for name in TASK_FILES:
            try:
                original[name] = (cwd / name).read_text()
            except OSError:
                # A task file the fixture does not contain is removed again between repetitions.
                original[name] = None
        session = self._start_session(
            candidate,
            cwd,
            compiled,
            {
                "HOME": str(home_dir),
                "PITWALL_WORKBENCH_ACCOUNTING_PATH": str(run_root / "accounting.jsonl"),
                "COMPARISON_FIXTURE": str(cwd),
                "COMPARISON_LIFECYCLE_PATH": str(run_root / "comparison-lifecycle.jsonl"),
                **admission.env,
            },
            admission,
        )
        rows: list[JsonObject] = []
        passes: list[JsonObject] = []
        identities: list[str | None] = []
        pid = session.child.pid
        base: JsonObject = {
            "candidate": candidate.id,
            "candidateVersion": candidate.version,
            "surface": candidate.surface,
        }
        try:
            session.request("set_auto_retry", {"enabled": False})
            session.request("set_auto_compaction", {"enabled": False})
            protected_hash = sha256_text((cwd / "preserve.txt").read_text())
            for repetition in (1, 2):
                if repetition == 2:
                    for name, content in original.items():
                        if content is None:
                            (cwd / name).unlink(missing_ok=True)
                        else:
                            (cwd / name).write_text(content)
                state = session.response_data("get_state")
                identities.append(_session_identity(state))
                started_at = now_ms()
                tasks = self._run_core_tasks(session, cwd, fixture.oracle_path, protected_hash)
                finished_at = now_ms()
                pass_summary = _pass_summary(tasks, started_at, finished_at)
                passes.append(pass_summary)
                accounting = self._admission_accounting(admission, started_at)
                accepted = sum(1 for task in tasks if task.acceptance == "passed")
                model = get_object(state, "model") or {}
                identity = {
                    "provider": model.get("provider"),
                    "modelId": model.get("id"),
                    "exact": model.get("provider") == profile["provider"]
                    and model.get("id") == profile["modelId"],
                }
                rows.append(
                    {
                        **base,
                        "repetition": repetition,
                        "warm": repetition == 2,
                        "coreTasksStatus": "passed" if accepted == len(tasks) else "failed",
                        "fixtureRevision": fixture.revision,
                        "mediumFixture": {
                            "files": fixture.medium_stats.files,
                            "lines": fixture.medium_stats.lines,
                            "bytes": fixture.medium_stats.bytes,
                        },
                        "freshSession": repetition == 1,
                        "identity": identity,
                        "tasks": [task.to_json() for task in tasks],
                        "acceptedTaskRate": f"{accepted}/{len(tasks)}",
                        "failureInclusiveAcceptedTaskRate": f"{accepted}/{len(tasks)}",
                        "accounting": accounting,
                        "timing": {
                            "wallMs": pass_summary["wallMs"],
                            "modelElapsedMs": pass_summary["modelElapsedMs"],
                            "queueMs": accounting.get("queueMs"),
                            "queueDisposition": accounting.get("reason"),
                        },
                    }
                )
            same_session = (
                len(identities) == 2
                and identities[0] is not None
                and identities[0] == identities[1]
                and session.child.pid == pid
            )
            final_snapshot = file_snapshot(cwd)
            tree_unchanged = protected_tree_hash(final_snapshot, TASK_FILES) == tree_hash_before
            warm_across: JsonObject = {
                "status": "passed" if same_session and len(passes) == 2 else "failed",
                "scope": "the same native session and fixture across two repetitions; task files restored between them",
                "sessionIdentity": identities[0],
                "processId": pid,
                "sameSession": same_session,
                "taskFilesRestored": list(TASK_FILES),
                "cold": passes[0] if passes else None,
                "warm": passes[1] if len(passes) > 1 else None,
            }
            truncated = session.was_capture_truncated()
            for row in rows:
                identity_ok = as_object(row.get("identity")) or {}
                row.update(
                    {
                        "warmAcrossRepetitions": warm_across,
                        "protectedTreeUnchanged": tree_unchanged,
                        "overallStatus": "passed"
                        if row.get("coreTasksStatus") == "passed"
                        and warm_across["status"] == "passed"
                        and identity_ok.get("exact") is True
                        and tree_unchanged
                        and not truncated
                        else "partial",
                        "capture": capture_disposition(truncated).to_json(),
                        "stderrBytes": session.stderr_bytes,
                    }
                )
            return rows
        except _TASK_ERRORS as error:
            message = _error_text(error)
            return [
                {
                    **base,
                    "repetition": len(rows) + 1,
                    "coreTasksStatus": "failed",
                    "overallStatus": "partial",
                    "warmAcrossRepetitions": {"status": "failed", "reason": message},
                    "error": message,
                },
                *rows,
            ]
        finally:
            session.stop()

    # -- batch ----------------------------------------------------------------------------------

    def run(self) -> JsonObject:
        """Run every selected candidate and write ``comparison.json``; returns the printed summary."""
        options = self.options
        run_root = Path(tempfile.mkdtemp(prefix="comparison-run-", dir=self.output_dir))
        results: list[JsonObject] = []
        for candidate in self.selected:
            if options.warm:
                results.extend(self._run_warm_candidate(candidate, run_root))
                continue
            for repetition in range(1, options.repetitions + 1):
                results.append(
                    self._run_candidate(candidate, repetition, run_root, options.compact_only)
                )
        profile = self.profile
        automatic = options.automatic_compact_only
        derived = _derived_compaction(profile) if automatic else None
        summary: JsonObject = {
            "schemaVersion": 1,
            "generatedAt": _now(),
            "architectureSection": "§15",
            "repetitions": options.repetitions,
            "compactOnly": options.compact_only,
            "automaticCompactOnly": automatic,
            "childOnly": options.child_only,
            "candidates": [
                {"id": c.id, "version": c.version, "surface": c.surface} for c in self.selected
            ],
            "unexecuted": [
                {"candidate": c.id, "reason": "not selected in this bounded batch"}
                for c in self.candidates
                if c not in self.selected
            ],
            "controls": {
                "endpoint": profile["endpoint"],
                "model": profile["modelId"],
                "reasoningLevel": profile.get("reasoningLevel"),
                "derivedCompaction": derived,
                "automaticSchedule": {
                    "fillerWords": options.automatic_filler_words,
                    "finalFillerWords": options.automatic_final_filler_words,
                    "preludeTurns": options.automatic_prelude_turns,
                    "settleTimeoutMs": options.automatic_settle_timeout_ms,
                    "boundedSingle": options.bounded_single,
                }
                if automatic
                else None,
                "childReasoningFixture": "project Explore definition plus backend settings override written before baseline snapshot and fixed-date commit"
                if options.child_only
                else None,
                "fixture": "one disposable fixture per candidate, task files restored between repetitions"
                if options.warm
                else "fresh disposable fixture per candidate/repetition",
                "sessionFreshness": "one native session per candidate across both repetitions"
                if options.warm
                else "fresh per repetition",
                "warmContinuation": "measured per row when at least two turns complete",
                "warmAcrossRepetitions": "measured" if options.warm else "unexecuted",
                "workbenchAdmission": options.workbench_admission,
                "independentAcceptance": "host-side file/test/oracle checks",
                "secretValuesPrinted": False,
                "bytesAreNotTokens": True,
            },
            "results": results,
        }
        report_path = self.output_dir / "comparison.json"
        report_path.write_text(json.dumps(summary, indent=2) + "\n")
        report_path.chmod(0o600)
        printed: JsonObject = {
            "report": str(report_path),
            "runs": len(results),
            "candidates": [c.id for c in self.selected],
            "unexecuted": [c.id for c in self.candidates if c not in self.selected],
        }
        print(json.dumps(printed))
        return printed


def _derived_compaction(profile: Mapping[str, object]) -> JsonObject:
    settings = derive_compaction_settings(profile)
    return {
        "enabled": True,
        "reserveTokens": settings.reserve_tokens,
        "keepRecentTokens": settings.keep_recent_tokens,
        "safetyReserveTokens": settings.safety_reserve_tokens,
    }


def _session_identity_file(state: JsonObject) -> str | None:
    return as_str(state.get("sessionFile")) or as_str(state.get("sessionPath"))


def _pass_summary(tasks: Sequence[TaskResult], started_at: int, finished_at: int) -> JsonObject:
    turns = [turn for task in tasks for turn in task.turns]
    first = next(
        (t.first_request_usage for t in turns if t.first_request_usage.assistant_messages > 0),
        empty_usage(),
    )
    return {
        "acceptedTasks": sum(1 for task in tasks if task.acceptance == "passed"),
        "taskCount": len(tasks),
        "wallMs": finished_at - started_at,
        "modelElapsedMs": _sum_known([t.model_elapsed_ms for t in turns]),
        "firstRequestInput": first.input,
        "firstRequestCacheRead": first.cache_read,
        "turnCount": len(turns),
    }


def run_comparison(options: ComparisonOptions) -> JsonObject:
    """``pitwall workbench compare``: run the batch and return the printed summary."""
    return ComparisonRunner(options).run()
