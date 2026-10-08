"""Per-dispatch orchestrator-channel settings and services (standard library only).

``channel.json`` in a run directory records what the dispatcher decided for
the channel: the D1 ask cap (default 5, overridable per dispatch), the
attempt timeout that D1 deadlines derive from, and the delivery tier. The
broker, MCP server, CLI, and workflow scheduler all read this one record.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import UsageError
from .mailbox import (
    DEFAULT_MAX_ASKS,
    MAX_DEADLINE_S,
    Mailbox,
    MailboxError,
    derive_deadline_s,
    validate_steer_request,
)
from .run_store import TERMINAL_STATES, RunStore, append_jsonl, ledger_path

if TYPE_CHECKING:
    from .events import EventEmitter


CHANNEL_DISPATCH_ENV = "PITWALL_AGENTS_CHANNEL_DISPATCH_ID"
CHANNEL_STATE_ROOT_ENV = "PITWALL_AGENTS_CHANNEL_STATE_ROOT"
CHANNEL_ATTEMPT_ENV = "PITWALL_AGENTS_CHANNEL_ATTEMPT"
CHANNEL_ARTIFACT = "channel.json"
MAX_ASKS_CEILING = 50
TIERS = ("1", "4")


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    dispatch_id: str
    max_asks: int = DEFAULT_MAX_ASKS
    max_open_asks: int = 1
    timeout_seconds: float = 1140.0
    attempt_started_epoch: float = 0.0
    wall_seconds_prior: int = 0
    tier: str = "4"

    def deadline_cap_s(self, at_epoch: float) -> int:
        """D1: ``min(3600, 15% of the remaining attempt timeout)`` at *at_epoch*."""
        return derive_deadline_s(self.attempt_started_epoch + self.timeout_seconds - at_epoch)

    def to_json(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "dispatchId": self.dispatch_id,
            "maxAsks": self.max_asks,
            "maxOpenAsks": self.max_open_asks,
            "timeoutSeconds": self.timeout_seconds,
            "attemptStartedEpoch": self.attempt_started_epoch,
            "wallSecondsPrior": self.wall_seconds_prior,
            "tier": self.tier,
        }


def _config_from_json(doc: Any) -> ChannelConfig | None:
    if not isinstance(doc, dict) or doc.get("schemaVersion") != 1:
        return None
    try:
        config = ChannelConfig(
            dispatch_id=str(doc["dispatchId"]),
            max_asks=int(doc["maxAsks"]),
            max_open_asks=int(doc["maxOpenAsks"]),
            timeout_seconds=float(doc["timeoutSeconds"]),
            attempt_started_epoch=float(doc["attemptStartedEpoch"]),
            wall_seconds_prior=int(doc["wallSecondsPrior"]),
            tier=str(doc["tier"]),
        )
    except KeyError, TypeError, ValueError:
        return None
    if not 1 <= config.max_asks <= MAX_ASKS_CEILING or config.max_open_asks < 1:
        return None
    if config.timeout_seconds <= 0 or config.wall_seconds_prior < 0 or config.tier not in TIERS:
        return None
    return config


def load_channel_config(run_dir: Path) -> ChannelConfig | None:
    """The run's channel record, or None when absent or out of bounds."""
    try:
        doc = json.loads((run_dir / CHANNEL_ARTIFACT).read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return _config_from_json(doc)


def write_channel_config(store: RunStore, config: ChannelConfig) -> None:
    store.write_json(CHANNEL_ARTIFACT, config.to_json())


def parse_max_asks(raw: str, *, source: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= MAX_ASKS_CEILING:
        raise UsageError(f"{source} expects an integer 1..{MAX_ASKS_CEILING}")
    return value


def epoch_of(value: Any) -> float | None:
    """Parse a mailbox ISO-8601 timestamp (``Z`` or offset) into epoch seconds."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def effective_deadline_s(ask: Mapping[str, Any], config: ChannelConfig | None) -> int:
    """The ask's own ``deadline_s`` clamped by the D1 cap at its creation time."""
    requested = int(ask.get("deadline_s", MAX_DEADLINE_S))
    created = epoch_of(ask.get("created_at"))
    if config is None or created is None:
        return requested
    return min(requested, config.deadline_cap_s(created))


def write_deadline_cap_s(config: ChannelConfig | None, now_epoch: float) -> int:
    """The largest deadline a writer may accept now (``MAX_DEADLINE_S`` without a record)."""
    return MAX_DEADLINE_S if config is None else config.deadline_cap_s(now_epoch)


def ledger_fields(store: RunStore, *, wall_seconds_total: int) -> dict[str, Any]:
    """§9.3 aggregates for the finished ledger row."""
    box = store.mailbox()
    asks = box.asks()
    answers = {answer["ask_id"]: answer for answer in box.answers()}
    first_ack: dict[str, float] = {}
    for ack in box.acks():
        acked = epoch_of(ack.get("acked_at"))
        if acked is not None:
            first_ack[ack["steer_id"]] = min(acked, first_ack.get(ack["steer_id"], acked))
    steers = box.steers()
    latencies: list[int] = []
    for steer in steers:
        created = epoch_of(steer.get("created_at"))
        if created is not None and steer["steer_id"] in first_ack:
            latencies.append(max(0, int(first_ack[steer["steer_id"]] - created)))
    return {
        "askCount": len(asks),
        "askRatePerHour": round(len(asks) * 3600 / max(wall_seconds_total, 1), 3),
        "askResolutions": [
            f"{ask['ask_id']}:{answers[ask['ask_id']]['answered_by']}"
            for ask in asks
            if ask["ask_id"] in answers
        ],
        "steerCount": len(steers),
        "steerAckLatencyS": latencies,
        "unackedSteerIds": [steer["steer_id"] for steer in box.unacked_steers()],
    }


def resolve_with_default(
    store: RunStore, ask: Mapping[str, Any], *, emitter: EventEmitter | None
) -> dict[str, Any]:
    """Apply an ask's stated default; if another answer won the race, return that one."""
    box = store.mailbox()
    ask_id = str(ask["ask_id"])
    try:
        answer = box.write_answer(
            ask_id,
            choice=str(ask["default"]),
            answered_by="default",
            note="deadline expired; the ask's stated default was applied",
        )
    except MailboxError:
        existing = box.get_answer(ask_id)
        if existing is None:
            raise
        return existing
    if emitter is not None:
        emitter.emit_ask_resolved(ask_id, "default")
    return answer


def expire_overdue(
    store: RunStore, *, emitter: EventEmitter | None, now_epoch: float | None = None
) -> list[dict[str, Any]]:
    """Resolve every pending ask whose effective deadline has passed with its default."""
    moment = time.time() if now_epoch is None else now_epoch
    config = load_channel_config(store.path)
    applied: list[dict[str, Any]] = []
    box = store.mailbox_if_present()
    for ask in box.pending_asks() if box is not None else []:
        created = epoch_of(ask.get("created_at"))
        if created is None or created + effective_deadline_s(ask, config) > moment:
            continue
        answer = resolve_with_default(store, ask, emitter=emitter)
        if answer.get("answered_by") == "default":
            applied.append(answer)
    return applied


class SteerWatcher:
    """Polled by the supervisor: flags overdue steers and enforces ignored stop steers."""

    def __init__(self, store: RunStore, emitter: EventEmitter, abort: threading.Event) -> None:
        self.store = store
        self.emitter = emitter
        self.abort = abort
        self.reason: str | None = None
        self._reported: set[str] = set()

    def __call__(self) -> None:
        from .mailbox import Mailbox

        box = Mailbox(
            self.store.path, self.store.dispatch_id
        )  # plain reader: no mailbox.json rewrite per poll
        if not (box.root / "steer").is_dir():
            return
        now = time.time()
        acked_at: dict[str, float] = {}
        for ack in box.acks():
            moment = epoch_of(ack.get("acked_at"))
            if moment is not None:
                acked_at[ack["steer_id"]] = min(moment, acked_at.get(ack["steer_id"], moment))
        for steer in box.steers():
            steer_id = steer["steer_id"]
            created = epoch_of(steer.get("created_at")) or now
            ack_moment = acked_at.get(steer_id)
            if (
                steer["requires_ack"]
                and ack_moment is None
                and now > created + int(steer["deadline_s"])
                and steer_id not in self._reported
            ):
                self._reported.add(steer_id)
                self.emitter.emit("steer.unacked", {"steerId": steer_id, "kind": steer["kind"]})
            if steer["kind"] == "stop" and not self.abort.is_set():
                window_start = ack_moment if ack_moment is not None else created
                if now > window_start + int(steer["deadline_s"]):
                    self.reason = f"stop steer {steer_id} not honored within {steer['deadline_s']}s"
                    self.abort.set()

    def report_unacked_at_exit(self) -> list[str]:
        """Emit ``steer.unacked`` once for every blocking steer still unacked as the run ends."""
        box = Mailbox(self.store.path, self.store.dispatch_id)
        if not (box.root / "steer").is_dir():
            return []
        pending = box.unacked_steers()
        for steer in pending:
            if steer["steer_id"] not in self._reported:
                self._reported.add(steer["steer_id"])
                self.emitter.emit(
                    "steer.unacked",
                    {"steerId": steer["steer_id"], "kind": steer["kind"], "atExit": True},
                )
        return [steer["steer_id"] for steer in pending]


def _finished(run_dir: Path) -> bool:
    """True for a terminal run; the all-runs inbox lists only live and paused runs."""
    try:
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8")).get("state")
    except OSError, ValueError, AttributeError:
        return False
    return state in TERMINAL_STATES


def read_channel_inbox(env: Mapping[str, str], dispatch_id: str | None) -> dict[str, Any]:
    """Unresolved asks plus unacknowledged steers for one run, or every run.

    Raises ``FileNotFoundError`` for an unknown run; HTTP surfaces wrap that
    in their own status type. Read-only: a plain ``Mailbox`` avoids touching
    ``mailbox.json`` and never creates ``mailbox/``.
    """
    from .mailbox import Mailbox
    from .run_store import find_run, list_runs

    runs = [find_run(env, dispatch_id)] if dispatch_id is not None else list_runs(env)
    asks: list[dict[str, Any]] = []
    steers: list[dict[str, Any]] = []
    sequence_gaps: list[dict[str, Any]] = []
    for path in runs:
        if dispatch_id is None and _finished(path):
            continue
        config = load_channel_config(path)
        box = Mailbox(path, path.name)
        for ask in box.pending_asks():
            remaining = box.deadline_remaining_s(ask, deadline_s=effective_deadline_s(ask, config))
            asks.append(
                {
                    "dispatchId": path.name,
                    "askId": ask["ask_id"],
                    "blockedOn": ask["blocked_on"],
                    "severity": ask.get("severity", "normal"),
                    "question": ask["question"],
                    "deadlineRemainingS": remaining,
                    "expired": remaining <= 0,
                    "createdAt": ask.get("created_at"),
                    "filesTouched": ask["context"].get("files_touched", []),
                    "options": ask["context"].get("options", []),
                    "default": ask["default"],
                    "defaultRationale": ask.get("default_rationale"),
                    "contextCommand": f"pitwall agents runs diff {path.name}",
                }
            )
        acked_at: dict[str, float] = {}
        for ack in box.acks():
            moment = epoch_of(ack.get("acked_at"))
            if moment is not None:
                acked_at[ack["steer_id"]] = min(moment, acked_at.get(ack["steer_id"], moment))
        for steer in box.unacked_steers():
            created = epoch_of(steer.get("created_at"))
            remaining = (
                int(created + int(steer["deadline_s"]) - time.time())
                if created is not None
                else int(steer["deadline_s"])
            )
            steers.append(
                {
                    "dispatchId": path.name,
                    "steerId": steer["steer_id"],
                    "kind": steer["kind"],
                    "message": steer["message"],
                    "requiresAck": steer["requires_ack"],
                    "deadlineRemainingS": remaining,
                    "ignored": bool(steer["requires_ack"]) and remaining < 0,
                    "createdAt": steer.get("created_at"),
                }
            )
        for gap_box, missing in box.sequence_gaps().items():
            sequence_gaps.append({"dispatchId": path.name, "box": gap_box, "missing": missing})
    return {"asks": asks, "steers": steers, "sequenceGaps": sequence_gaps}


def answer_ask(
    env: Mapping[str, str],
    dispatch_id: str,
    ask_id: str,
    *,
    choice: str,
    answered_by: str,
    note: str | None,
    harness: str,
) -> dict[str, Any]:
    """Record one answer and emit ``ask.resolved`` (the shared answer service)."""
    from .hooks import HookRunner
    from .run_store import find_run, state_root

    run_path = find_run(env, dispatch_id)
    store = RunStore(state_root(env), run_path.name)
    from .events import EventEmitter

    answer = store.mailbox().write_answer(ask_id, choice=choice, answered_by=answered_by, note=note)
    EventEmitter(
        store, harness=harness, model="channel", callback=HookRunner(env)
    ).emit_ask_resolved(ask_id, str(answer["answered_by"]))
    return answer


_STEERABLE_STATES = {
    "created",
    "preflighting",
    "ready",
    "workspace_preparing",
    "workspace_ready",
    "running",
    "paused",
}


_PRE_LAUNCH_STATES = {"created", "preflighting", "ready", "workspace_preparing", "workspace_ready"}


def steer_refusal(run_dir: Path, kind: str, state: object) -> str | None:
    """Why a *kind* steer to this run can never be read, or None when it can be delivered.

    A stop is always accepted: the dispatcher's watcher aborts the run at its
    deadline with or without the channel. Any other kind needs ``channel.json``.
    """
    if kind == "stop" or load_channel_config(run_dir) is not None:
        return None
    if (run_dir / CHANNEL_ARTIFACT).exists():
        return f"run {run_dir.name} has an unreadable channel.json; only a stop steer applies"
    if state in _PRE_LAUNCH_STATES:
        return (
            f"run {run_dir.name} is still preparing and has not recorded its orchestrator "
            "channel yet; retry the steer once it is running, or send a stop steer"
        )
    return (
        f"run {run_dir.name} was dispatched without the orchestrator channel, so it cannot "
        f"read a {kind} steer; only a stop steer applies, and it aborts the run at its deadline"
    )


def send_steer(
    env: Mapping[str, str],
    dispatch_id: str,
    *,
    kind: str,
    message: str,
    requires_ack: bool,
    deadline_s: int,
    harness: str,
) -> dict[str, Any]:
    """Write one STEER for a live or paused run and emit ``steer.sent``."""
    import json as _json

    from .events import EventEmitter
    from .hooks import HookRunner
    from .run_store import find_run, state_root

    run_path = find_run(env, dispatch_id)
    try:
        state = _json.loads((run_path / "run.json").read_text(encoding="utf-8")).get("state")
    except OSError, _json.JSONDecodeError:
        state = None
    if state not in _STEERABLE_STATES:
        raise MailboxError(
            f"run {run_path.name} is {state}; steering applies to running or paused runs"
        )
    validate_steer_request(
        run_path.name,
        kind=kind,
        message=message,
        requires_ack=requires_ack,
        deadline_s=deadline_s,
    )
    refusal = steer_refusal(run_path, kind, state)
    if refusal is not None:
        raise MailboxError(refusal)
    store = RunStore(state_root(env), run_path.name)
    return write_steer_logged(
        store,
        EventEmitter(store, harness=harness, model="channel", callback=HookRunner(env)),
        kind=kind,
        message=message,
        requires_ack=requires_ack,
        deadline_s=deadline_s,
    )


def write_steer_logged(
    store: RunStore,
    emitter: Any,
    *,
    kind: str,
    message: str,
    requires_ack: bool,
    deadline_s: int,
) -> dict[str, Any]:
    """Write one STEER, logging ``steer.sent`` before the consumer can see it.

    The consumer appends ``steer.acked`` to the same log the moment it reads the
    steer, so the send must be durable first. A publish that then fails is
    recorded as ``steer.failed`` instead of leaving a dangling send.
    """

    def journal(phase: str, doc: dict[str, Any]) -> None:
        name = "steer.sent" if phase == "sending" else "steer.failed"
        emitter.emit(name, {"steerId": doc["steer_id"], "kind": kind})

    return store.mailbox().write_steer(
        kind=kind,
        message=message,
        requires_ack=requires_ack,
        deadline_s=deadline_s,
        journal=journal,
    )


def channel_aggregate(run_dir: Path) -> dict[str, Any] | None:
    """Counts and timings for one run's channel traffic; no question text, options, or paths."""
    box = Mailbox(run_dir, run_dir.name)
    if not box.root.is_dir():
        return None
    asks, answers, steers = box.asks(), {a["ask_id"]: a for a in box.answers()}, box.steers()
    if not asks and not steers:
        return None
    latencies: list[int] = []
    for ask in asks:
        answer = answers.get(ask["ask_id"])
        created = epoch_of(ask.get("created_at"))
        answered = epoch_of(answer.get("answered_at")) if answer else None
        if created is not None and answered is not None:
            latencies.append(max(0, int(answered - created)))
    store = RunStore(run_dir.parent.parent, run_dir.name)
    return {
        "asks": len(asks),
        "blockedOn": dict(Counter(str(ask["blocked_on"]) for ask in asks)),
        "resolvedBy": dict(Counter(str(answer["answered_by"]) for answer in answers.values())),
        "latencyToAnswerS": latencies,
        "steers": len(steers),
        "steerAckLatencyS": ledger_fields(store, wall_seconds_total=1)["steerAckLatencyS"],
    }


def distill_before_cleanup(env: Mapping[str, str], run_dir: Path) -> bool:
    aggregate = channel_aggregate(run_dir)
    if aggregate is None:
        return False
    try:
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        run = {}
    row = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "shim",
        "event": "channel",
        "schema_version": 4,
        "dispatch_id": run_dir.name,
        "shim": run.get("provider"),
        "model": run.get("model"),
        "workflow_id": run.get("workflowId"),
        "task_id": run.get("taskId"),
        **aggregate,
    }
    append_jsonl(ledger_path(env), row)  # OSError propagates: cleanup keeps the run
    return True
