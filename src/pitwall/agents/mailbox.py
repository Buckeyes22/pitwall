"""Per-dispatch orchestrator channel mailbox (Phase A).

Files are the source of truth; HTTP/MCP/inbox surfaces are views over them.
Layout below ``runs/<dispatch_id>/``::

    mailbox/
      asks/0001.json        # written by the subagent (or its MCP tool)
      answers/0001.json     # written by the broker on behalf of the answerer
      steer/0001.json       # written by the orchestrator
      acks/0001.json        # subagent acknowledgement of steering
      dead-letter/…         # schema-invalid or oversized writes, quarantined

Subagent-written data is untrusted input: every document is size-capped
(64 KiB) and schema-validated on write **and** on read. Documents that fail
on read are moved to ``dead-letter/`` and never interpreted.

Standard library only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .run_store import (
    DIRECTORY_MODE,
    atomic_create_bytes,
    ensure_private_directory,
    utc_now,
)

SCHEMA_VERSION = 1
BODY_CAP_BYTES = 64 * 1024
MAX_OPTIONS = 8
DEFAULT_MAX_ASKS = 5  # D1: global cap of asks per run (overridable per dispatch)
MAX_DEADLINE_S = 3600  # D1: deadline_s never exceeds one hour
MAX_DEAD_LETTER_FILES = 20  # N3: newest files win; older dead letters are pruned

BLOCKED_ON = frozenset({"choice", "naming", "file-selection", "schema", "destructive", "spend"})
SEVERITIES = frozenset({"normal", "blocking"})
STEER_KINDS = frozenset({"note", "scope", "budget", "priority", "stop"})
ANSWERED_BY = frozenset({"operator", "orchestrator", "default"})
POLICY_PREFIX = "policy:"
_POLICY_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
# Sequence ids name files under mailbox/, so they are exactly four digits and
# nothing else: no traversal, no int() surprises in the cap check.
_SEQUENCE_ID = re.compile(r"^\d{4}$")

_BOXES = ("asks", "answers", "steer", "acks", "dead-letter")
_ANSWER_LOCKS_DIR = "answer-locks"
_OPEN_ASK_LOCK = "open-ask.lock"

_MAX_ALLOCATION_ATTEMPTS = 1000


class MailboxError(ValueError):
    """A mailbox document failed validation or a mailbox invariant was violated."""


class MailboxCapError(MailboxError):
    """The per-run ask cap (D1) refused a new ASK; the document itself was not at fault."""


class MailboxOpenAskError(MailboxCapError):
    """A programmatic writer tried to open a second ask while one is unresolved (§10)."""


@contextmanager
def _locked_file(path: Path) -> Iterator[None]:
    """Hold an inter-process advisory lock for one mailbox operation."""

    ensure_private_directory(path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def derive_deadline_s(remaining_timeout_s: float) -> int:
    """Derive an ask deadline from the dispatch's remaining timeout (D1).

    ``min(3600, 15% of remaining)`` with a one-second floor so the result is
    always a usable positive deadline. The share is rounded, not truncated:
    the epoch round-trip through a millisecond timestamp otherwise shaves a
    fraction of a second off the remaining timeout and off the cap.
    """
    if remaining_timeout_s <= 0:
        return 1
    return max(1, min(MAX_DEADLINE_S, round(remaining_timeout_s * 0.15)))


def _ask_deadline_expired(
    run_dir: Path, ask: Mapping[str, Any], *, now: float | None = None
) -> bool:
    """Read the shared D1 deadline without importing channel at module load."""

    # channel imports Mailbox, so this dependency must remain lazy. Every
    # answer writer still reaches this same check while holding the ask lock.
    from .channel import effective_deadline_s, epoch_of, load_channel_config

    created = epoch_of(ask.get("created_at"))
    if created is None:
        return False
    effective = effective_deadline_s(ask, load_channel_config(run_dir))
    return created + effective <= (time.time() if now is None else now)


def answer_source_valid(answered_by: str) -> bool:
    """operator | orchestrator | default | policy:<model> (D3 provenance)."""
    if answered_by in ANSWERED_BY:
        return True
    model = answered_by.removeprefix(POLICY_PREFIX)
    return answered_by.startswith(POLICY_PREFIX) and bool(_POLICY_MODEL.fullmatch(model))


def _require_str(doc: Mapping[str, Any], field: str) -> str:
    value = doc.get(field)
    if not isinstance(value, str) or not value:
        raise MailboxError(f"mailbox document field {field!r} must be a non-empty string")
    return value


def _require_int(doc: Mapping[str, Any], field: str, *, minimum: int, maximum: int) -> int:
    value = doc.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MailboxError(f"mailbox document field {field!r} must be an integer")
    if not minimum <= value <= maximum:
        raise MailboxError(f"mailbox document field {field!r} must be within {minimum}..{maximum}")
    return value


def _require_sequence_id(doc: Mapping[str, Any], field: str) -> str:
    value = _require_str(doc, field)
    if not _SEQUENCE_ID.fullmatch(value):
        raise MailboxError(f"mailbox document field {field!r} must be a four-digit sequence id")
    return value


def validate_ask(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an ASK document; raise :class:`MailboxError` on violation."""
    if not isinstance(doc, Mapping):
        raise MailboxError("ASK document must be an object")
    if doc.get("version") != SCHEMA_VERSION:
        raise MailboxError("ASK document has an unsupported version")
    ask = dict(doc)
    _require_str(ask, "dispatch_id")
    _require_sequence_id(ask, "ask_id")
    blocked_on = _require_str(ask, "blocked_on")
    if blocked_on not in BLOCKED_ON:
        raise MailboxError(f"ASK blocked_on {blocked_on!r} must be one of {sorted(BLOCKED_ON)}")
    _require_str(ask, "question")
    context = ask.get("context")
    if not isinstance(context, dict):
        raise MailboxError("ASK context must be an object")
    files_touched = context.get("files_touched", [])
    if not isinstance(files_touched, list) or not all(isinstance(f, str) for f in files_touched):
        raise MailboxError("ASK context.files_touched must be a list of strings")
    options = context.get("options", [])
    if not isinstance(options, list):
        raise MailboxError("ASK context.options must be a list")
    if len(options) > MAX_OPTIONS:
        raise MailboxError(f"ASK context.options holds at most {MAX_OPTIONS} entries")
    seen_ids: set[str] = set()
    for option in options:
        if not isinstance(option, dict):
            raise MailboxError("ASK context.options entries must be objects")
        option_id = option.get("id")
        text = option.get("text")
        if not isinstance(option_id, str) or not option_id:
            raise MailboxError("ASK option id must be a non-empty string")
        if not isinstance(text, str) or not text:
            raise MailboxError("ASK option text must be a non-empty string")
        if option_id in seen_ids:
            raise MailboxError(f"ASK option id {option_id!r} is duplicated")
        seen_ids.add(option_id)
    # default is mandatory (may be "abort"); it must name an option or abort.
    default = ask.get("default")
    if not isinstance(default, str) or not default:
        raise MailboxError("ASK default is mandatory and must be a non-empty string")
    if seen_ids and default != "abort" and default not in seen_ids:
        raise MailboxError("ASK default must name one of the provided options or 'abort'")
    _require_int(ask, "deadline_s", minimum=1, maximum=MAX_DEADLINE_S)
    severity = ask.get("severity", "normal")
    if severity not in SEVERITIES:
        raise MailboxError(f"ASK severity {severity!r} must be one of {sorted(SEVERITIES)}")
    ask["severity"] = severity
    _validate_delivery_id(ask)
    return ask


def _validate_delivery_id(doc: Mapping[str, Any]) -> None:
    delivery_id = doc.get("delivery_id")
    if delivery_id is None:
        return
    if not isinstance(delivery_id, str) or not delivery_id or len(delivery_id) > 128:
        raise MailboxError("delivery_id must be a non-empty string of at most 128 characters")


def validate_answer(doc: Mapping[str, Any], *, options: list[str] | None = None) -> dict[str, Any]:
    """Validate an ANSWER document; raise :class:`MailboxError` on violation."""
    if not isinstance(doc, Mapping):
        raise MailboxError("ANSWER document must be an object")
    if doc.get("version") != SCHEMA_VERSION:
        raise MailboxError("ANSWER document has an unsupported version")
    answer = dict(doc)
    _require_sequence_id(answer, "ask_id")
    answered_by = _require_str(answer, "answered_by")
    if not answer_source_valid(answered_by):
        raise MailboxError(
            f"ANSWER answered_by {answered_by!r} must be operator, orchestrator, default, "
            f"or {POLICY_PREFIX}<model>"
        )
    choice = _require_str(answer, "choice")
    if options is not None and options and choice != "abort" and choice not in options:
        raise MailboxError("ANSWER choice must select one of the ask's options or 'abort'")
    if answered_by.startswith(POLICY_PREFIX) and choice == "abort":
        raise MailboxError("a policy answer must select one of the ask's options")
    _require_str(answer, "answered_at")
    return answer


def validate_steer(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a STEER document; raise :class:`MailboxError` on violation."""
    if not isinstance(doc, Mapping):
        raise MailboxError("STEER document must be an object")
    if doc.get("version") != SCHEMA_VERSION:
        raise MailboxError("STEER document has an unsupported version")
    steer = dict(doc)
    _require_str(steer, "dispatch_id")
    _require_sequence_id(steer, "steer_id")
    kind = _require_str(steer, "kind")
    if kind not in STEER_KINDS:
        raise MailboxError(f"STEER kind {kind!r} must be one of {sorted(STEER_KINDS)}")
    _require_str(steer, "message")
    requires_ack = steer.get("requires_ack", True)
    if not isinstance(requires_ack, bool):
        raise MailboxError("STEER requires_ack must be a boolean")
    steer["requires_ack"] = requires_ack
    _require_int(steer, "deadline_s", minimum=1, maximum=MAX_DEADLINE_S)
    _require_str(steer, "created_at")
    _validate_delivery_id(steer)
    return steer


def steer_document(
    dispatch_id: str,
    steer_id: str,
    *,
    kind: object,
    message: object,
    requires_ack: object,
    deadline_s: object,
    delivery_id: str | None = None,
) -> dict[str, Any]:
    """The one place a STEER document is assembled; callers validate it."""
    doc: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "dispatch_id": dispatch_id,
        "steer_id": steer_id,
        "kind": kind,
        "message": message,
        "requires_ack": requires_ack,
        "deadline_s": deadline_s,
        "created_at": utc_now(),
    }
    if delivery_id is not None:
        doc["delivery_id"] = delivery_id
    return doc


def validate_steer_request(
    dispatch_id: str,
    *,
    kind: object,
    message: object,
    requires_ack: object,
    deadline_s: object,
    delivery_id: str | None = None,
) -> None:
    """Check a steer's caller-supplied fields without touching the filesystem.

    Raises :class:`MailboxError` exactly as :meth:`Mailbox.write_steer` would, so a
    caller can reject a malformed steer before it opens or creates any mailbox.
    """
    validate_steer(
        steer_document(
            dispatch_id,
            "0001",
            kind=kind,
            message=message,
            requires_ack=requires_ack,
            deadline_s=deadline_s,
            delivery_id=delivery_id,
        )
    )


def validate_ack(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an ACK document; raise :class:`MailboxError` on violation."""
    if not isinstance(doc, Mapping):
        raise MailboxError("ACK document must be an object")
    if doc.get("version") != SCHEMA_VERSION:
        raise MailboxError("ACK document has an unsupported version")
    ack = dict(doc)
    _require_sequence_id(ack, "steer_id")
    _require_str(ack, "acked_at")
    return ack


_DEAD_LETTER_NAME = re.compile(r"^(?P<prefix>.+?)(?:-(?P<counter>\d+))?\.json$")


def _dead_letter_parts(name: str) -> tuple[str, int]:
    """Split ``<box>_<stem>[-N].json`` into its prefix and sequence (bare name is 1)."""
    match = _DEAD_LETTER_NAME.match(name)
    if match is None:
        return (name, 0)
    counter = match.group("counter")
    return (match.group("prefix"), int(counter) if counter is not None else 1)


def _dead_letter_age_key(path: Path) -> tuple[int, str, int]:
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    prefix, counter = _dead_letter_parts(path.name)
    return (mtime_ns, prefix, counter)


def _require_file_id(doc_id: str, path: Path, box: str) -> None:
    """A document's id names its file; a mismatch is quarantined like any invalid write."""
    if path.stem != doc_id:
        raise MailboxError(f"{box}/{path.name} carries id {doc_id!r}, which is not its file name")


def _check_cap(raw: bytes, *, what: str) -> None:
    if len(raw) > BODY_CAP_BYTES:
        raise MailboxError(f"{what} exceeds the {BODY_CAP_BYTES}-byte body cap")


class Mailbox:
    """Own one dispatch's ``mailbox/`` directory and its message documents."""

    def __init__(
        self, run_dir: Path, dispatch_id: str, *, max_asks: int = DEFAULT_MAX_ASKS
    ) -> None:
        self.run_dir = run_dir
        self.dispatch_id = dispatch_id
        self.max_asks = max_asks
        self.root = run_dir / "mailbox"
        self.quarantined = 0  # bumps on every dead-letter deposit; readers use it to spot change

    def ensure(self) -> Path:
        for box in _BOXES:
            ensure_private_directory(self.root / box)
        with suppress(OSError):
            self.root.chmod(DIRECTORY_MODE)
        return self.root

    # -- sequencing ------------------------------------------------------

    def _next_id(self, box: str) -> str:
        """Next number above every live and quarantined document in *box*."""
        self.ensure()
        taken = 0
        for path in (self.root / box).glob("*.json"):
            if len(path.stem) >= 4 and path.stem.isdigit():
                taken = max(taken, int(path.stem))
        for path in (self.root / "dead-letter").glob(f"{box}_*.json"):
            stem = _dead_letter_parts(path.name)[0].removeprefix(f"{box}_")
            if len(stem) >= 4 and stem.isdigit():
                taken = max(taken, int(stem))
        return f"{taken + 1:04d}"

    def _write_doc(self, box: str, name: str, doc: Mapping[str, Any]) -> dict[str, Any]:
        """Publish *doc* as ``box/name``; raise FileExistsError when the name is taken."""
        self.ensure()
        raw = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _check_cap(raw, what=f"{box}/{name}")
        if not atomic_create_bytes(self.root / box / name, raw):
            raise FileExistsError(f"{box}/{name} already exists")
        return dict(doc)

    def _append(
        self,
        box: str,
        build: Callable[[str], dict[str, Any]],
        journal: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Publish ``build(seq)`` under the next free sequence number in *box*.

        ``build`` validates and may raise (schema, caps). Losing a race for a
        number retries with the next one, so concurrent writers never overwrite.
        ``journal`` is write-ahead: ``journal("sending", doc)`` runs before the
        document becomes visible, and ``journal("failed", doc)`` if it never did.
        """
        for _attempt in range(_MAX_ALLOCATION_ATTEMPTS):
            seq = self._next_id(box)
            doc = build(seq)
            if journal is not None:
                journal("sending", doc)
            try:
                return self._write_doc(box, f"{seq}.json", doc)
            except BaseException as exc:
                if journal is not None:
                    journal("failed", doc)
                if isinstance(exc, FileExistsError):
                    continue
                raise
        raise MailboxError(f"could not allocate a sequence number in {box}/")

    # -- writes ----------------------------------------------------------

    def _answer_lock(self, ask_id: str) -> AbstractContextManager[None]:
        """Serialize all writers that can resolve one ask."""

        return _locked_file(self.root / _ANSWER_LOCKS_DIR / f"{ask_id}.lock")

    def write_ask(
        self,
        *,
        blocked_on: str,
        question: str,
        options: list[Mapping[str, str]],
        default: str | None = None,
        deadline_s: int = 0,
        files_touched: list[str] | None = None,
        worktree: str | None = None,
        default_rationale: str | None = None,
        severity: str = "normal",
        max_open: int | None = None,
        reenter_open: bool = False,
        delivery_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one ASK; enforce the per-run ask cap (D1).

        ``default`` is mandatory (validation rejects a missing one) but taken
        as an explicit argument so callers cannot forget it silently.
        ``max_open`` limits how many asks may sit unresolved at once; programmatic
        writers pass 1 (Decision 10), while direct tier-4 callers stay unlimited.
        ``reenter_open`` returns the oldest open ask instead of writing when it asks the same
        ``question`` (a client retrying after its own timeout); the lookup and the write share
        one lock, so concurrent identical retries all land on a single ask.
        """

        def build(ask_id: str) -> dict[str, Any]:
            if int(ask_id) > self.max_asks:
                raise MailboxCapError(
                    f"ask cap exceeded: ask {ask_id} is past the limit of {self.max_asks} asks per run"
                )
            context: dict[str, Any] = {
                "files_touched": list(files_touched or []),
                "options": [dict(o) for o in options],
            }
            if worktree is not None:
                context["worktree"] = worktree
            ask: dict[str, Any] = {
                "version": SCHEMA_VERSION,
                "dispatch_id": self.dispatch_id,
                "ask_id": ask_id,
                "created_at": utc_now(),
                "blocked_on": blocked_on,
                "question": question,
                "context": context,
                "default": default,
                "deadline_s": deadline_s,
                "severity": severity,
            }
            if default_rationale is not None:
                ask["default_rationale"] = default_rationale
            if delivery_id is not None:
                ask["delivery_id"] = delivery_id
            return validate_ask(ask)

        if max_open is None and not reenter_open:
            return self._append("asks", build)
        # The limit (and the re-entry lookup) is a check-then-publish, so one lock covers both: MCP worker threads
        # and separate processes would otherwise each pass the check. Each _locked_file
        # call opens its own descriptor, so flock serializes threads as well. The name
        # cannot collide with the four-digit per-ask lock files beside it.
        self.ensure()
        with _locked_file(self.root / _ANSWER_LOCKS_DIR / _OPEN_ASK_LOCK):
            open_asks = self.pending_asks()
            if reenter_open and open_asks and open_asks[0]["question"] == question:
                return open_asks[0]
            open_ids = [a["ask_id"] for a in open_asks]
            if max_open is not None and len(open_ids) >= max_open:
                raise MailboxOpenAskError(
                    f"ask {open_ids[0]} is still open; one open ask at a time"
                )
            return self._append("asks", build)

    def write_answer(
        self,
        ask_id: str,
        *,
        choice: str,
        answered_by: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Record the single broker-side answer resolving *ask_id*.

        Explicit and default answers share an ask-scoped inter-process lock.
        The deadline is checked while holding that lock, immediately before
        the exclusive answer create. This closes the parent check/write gap:
        a late explicit writer is converted to the ask's validated default,
        while a default writer cannot be overtaken by a racing explicit one.
        """

        if not _SEQUENCE_ID.fullmatch(ask_id):
            raise MailboxError(f"ask id {ask_id!r} is not a four-digit sequence id")
        self.ensure()
        with self._answer_lock(ask_id):
            ask = self.get_ask(ask_id)
            option_ids = [o["id"] for o in ask["context"].get("options", []) if isinstance(o, dict)]
            if not isinstance(answered_by, str) or not answer_source_valid(answered_by):
                raise MailboxError(
                    "ANSWER answered_by must be operator, orchestrator, default, "
                    f"or {POLICY_PREFIX}<model>"
                )
            if not isinstance(choice, str) or not choice:
                raise MailboxError("ANSWER choice must be a non-empty string")
            if note is not None and not isinstance(note, str):
                raise MailboxError("ANSWER note must be a string")

            # Re-evaluate after all caller-side work and while the shared lock
            # is held. If an explicit caller crossed the deadline, only the
            # validated default may win. The child uses answered_by=default
            # through this same path when its own wait expires.
            expired = _ask_deadline_expired(self.run_dir, ask)
            if expired and answered_by != "default":
                answered_by = "default"
                choice = str(ask["default"])
                note = "deadline expired; the ask's stated default was applied"
            elif answered_by == "default" and not expired:
                raise MailboxError("the ask deadline has not expired")

            answer: dict[str, Any] = {
                "version": SCHEMA_VERSION,
                "ask_id": ask_id,
                "answered_by": answered_by,
                "choice": choice,
                "answered_at": utc_now(),
            }
            if note is not None:
                answer["note"] = note
            validated = validate_answer(answer, options=option_ids)
            # Validation and serialization are intentionally followed by one
            # final clock read while the ask lock is still held. A caller may
            # have spent enough time in input validation to cross the
            # deadline after the first check; convert that attempt before the
            # exclusive create rather than publishing a late explicit answer.
            if answered_by != "default" and _ask_deadline_expired(self.run_dir, ask):
                answer["answered_by"] = "default"
                answer["choice"] = str(ask["default"])
                answer["answered_at"] = utc_now()
                answer["note"] = "deadline expired; the ask's stated default was applied"
                validated = validate_answer(answer, options=option_ids)
            try:
                return self._write_doc("answers", f"{ask_id}.json", validated)
            except FileExistsError as exc:
                raise MailboxError(f"ask {ask_id} already has an answer") from exc

    def write_steer(
        self,
        *,
        kind: str,
        message: str,
        requires_ack: bool = True,
        deadline_s: int = 300,
        delivery_id: str | None = None,
        journal: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        def build(steer_id: str) -> dict[str, Any]:
            return validate_steer(
                steer_document(
                    self.dispatch_id,
                    steer_id,
                    kind=kind,
                    message=message,
                    requires_ack=requires_ack,
                    deadline_s=deadline_s,
                    delivery_id=delivery_id,
                )
            )

        return self._append("steer", build, journal)

    def write_ack(self, steer_id: str, *, note: str | None = None) -> dict[str, Any]:
        ack: dict[str, Any] = {
            "version": SCHEMA_VERSION,
            "steer_id": steer_id,
            "acked_at": utc_now(),
        }
        if note is not None:
            ack["note"] = note
        validated = validate_ack(ack)
        return self._append("acks", lambda _seq: validated)

    # -- dead letter -----------------------------------------------------

    def quarantine(self, box: str, filename: str, raw: bytes, reason: str) -> Path:
        """Deposit an invalid payload in ``dead-letter/``; never interpret it."""
        self.ensure()
        self.quarantined += 1
        stem = Path(filename).stem or "unknown"
        directory = self.root / "dead-letter"
        prefix = f"{box}_{stem}"
        # Sequence numbers only ever grow, even after pruning, so a name is a
        # faithful write-order tiebreak when several files share one mtime.
        latest = max(
            (
                _dead_letter_parts(path.name)[1]
                for path in directory.glob(f"{prefix}*.json")
                if _dead_letter_parts(path.name)[0] == prefix
            ),
            default=0,
        )
        if (directory / f"{prefix}.json").exists():
            # A stem that itself ends in "-<digits>" parses as someone else's
            # sequence; the bare file existing still means sequence 1 is taken.
            latest = max(latest, 1)
        header = f"quarantined {box}/{filename}: {reason}\n".encode()[:512]
        while True:
            if latest == 0:
                target = directory / f"{prefix}.json"
            else:
                target = directory / f"{prefix}-{latest + 1}.json"
            if atomic_create_bytes(target, header + raw[:BODY_CAP_BYTES]):
                break
            latest += 1
        # Newest by write time; the counter suffix breaks ties numerically so
        # "-10" never sorts before "-2" when writes land in one clock tick.
        files = sorted(directory.glob("*.json"), key=_dead_letter_age_key)
        for path in files[:-MAX_DEAD_LETTER_FILES]:
            with suppress(OSError):
                path.unlink()
        return target

    def _read_box(self, box: str) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        directory = self.root / box
        if not directory.is_dir():
            return docs
        for path in sorted(directory.glob("*.json")):
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            try:
                if len(raw) > BODY_CAP_BYTES:
                    raise MailboxError(f"{box}/{path.name} exceeds the body cap")
                parsed = json.loads(raw.decode("utf-8"))
                if box == "asks":
                    doc = validate_ask(parsed)
                    _require_file_id(doc["ask_id"], path, box)
                elif box == "answers":
                    doc = validate_answer(parsed)
                    _require_file_id(doc["ask_id"], path, box)
                elif box == "steer":
                    doc = validate_steer(parsed)
                    _require_file_id(doc["steer_id"], path, box)
                elif box == "acks":
                    doc = validate_ack(parsed)
                else:
                    continue
                docs.append(doc)
            except (MailboxError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.quarantine(box, path.name, raw, str(exc))
                with suppress(OSError):
                    path.unlink()
        return docs

    # -- reads -----------------------------------------------------------

    def get_ask(self, ask_id: str) -> dict[str, Any]:
        for ask in self._read_box("asks"):
            if ask["ask_id"] == ask_id:
                return ask
        raise MailboxError(f"unknown ask {ask_id}")

    def get_answer(self, ask_id: str) -> dict[str, Any] | None:
        for answer in self._read_box("answers"):
            if answer["ask_id"] == ask_id:
                return answer
        return None

    def asks(self) -> list[dict[str, Any]]:
        """All asks in ask-id order (invalid files are quarantined)."""
        return sorted(self._read_box("asks"), key=lambda ask: ask["ask_id"])

    def answers(self) -> list[dict[str, Any]]:
        """All answers in ask-id order (invalid files are quarantined)."""
        return sorted(self._read_box("answers"), key=lambda answer: answer["ask_id"])

    def is_resolved(self, ask_id: str) -> bool:
        return self.get_answer(ask_id) is not None

    def pending_asks(self) -> list[dict[str, Any]]:
        """Unresolved asks in ask-id order (invalid files are quarantined)."""
        return [ask for ask in self._read_box("asks") if not self.is_resolved(ask["ask_id"])]

    def steers(self) -> list[dict[str, Any]]:
        """All steers in steer-id order (invalid files are quarantined)."""
        return sorted(self._read_box("steer"), key=lambda steer: steer["steer_id"])

    def acks(self) -> list[dict[str, Any]]:
        """All acknowledgements in file order (invalid files are quarantined)."""
        return self._read_box("acks")

    def find_by_delivery(self, box: str, delivery_id: str) -> dict[str, Any] | None:
        """The ask or steer first written with *delivery_id*, or None."""
        documents = self.asks() if box == "asks" else self.steers() if box == "steer" else []
        for document in documents:
            if document.get("delivery_id") == delivery_id:
                return document
        return None

    def unacked_steers(self) -> list[dict[str, Any]]:
        """Steers with ``requires_ack`` and no ack yet, in steer-id order."""
        acked = {ack["steer_id"] for ack in self._read_box("acks")}
        return [
            steer
            for steer in self._read_box("steer")
            if steer["requires_ack"] and steer["steer_id"] not in acked
        ]

    def sequence_gaps(self) -> dict[str, list[str]]:
        """Missing sequence numbers per box (tolerated, crash-safe, but reported)."""
        gaps: dict[str, list[str]] = {}
        for box in ("asks", "steer", "acks"):
            present = {int(p.stem) for p in (self.root / box).glob("*.json") if p.stem.isdigit()}
            explained = {
                int(stem)
                for p in (self.root / "dead-letter").glob(f"{box}_*.json")
                if (stem := _dead_letter_parts(p.name)[0].removeprefix(f"{box}_")).isdigit()
            }
            if present:
                missing = [
                    f"{n:04d}" for n in range(1, max(present)) if n not in present | explained
                ]
                if missing:
                    gaps[box] = missing
        return gaps

    def deadline_remaining_s(
        self,
        ask: Mapping[str, Any],
        *,
        now: datetime | None = None,
        deadline_s: int | None = None,
    ) -> int:
        """Seconds until an ask's deadline (D1 display helper; may be negative)."""
        created_raw = str(ask.get("created_at", ""))
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        moment = now or datetime.now(UTC)
        elapsed = (moment - created).total_seconds()
        return int(deadline_s if deadline_s is not None else ask.get("deadline_s", 0)) - int(
            elapsed
        )

    def enforce_cap(self) -> list[str]:
        """Quarantine asks numbered above ``max_asks`` (§10 cap at the pause boundary)."""
        removed: list[str] = []
        for ask in self.asks():
            if int(ask["ask_id"]) > self.max_asks:
                path = self.root / "asks" / f"{ask['ask_id']}.json"
                try:
                    raw = path.read_bytes()
                except OSError:
                    continue
                self.quarantine(
                    "asks",
                    path.name,
                    raw,
                    f"ask cap exceeded (max {self.max_asks} per run)",
                )
                with suppress(OSError):
                    path.unlink()
                removed.append(ask["ask_id"])
        return removed

    def summary(self) -> dict[str, Any]:
        """Counts, unresolved ask ids, and last acked steer id for ``mailbox.json``."""
        asks = self._read_box("asks")
        answers = self._read_box("answers")
        steers = self._read_box("steer")
        acks = self._read_box("acks")
        answered = {a["ask_id"] for a in answers}
        acked = sorted({a["steer_id"] for a in acks})
        return {
            "schemaVersion": 1,
            "dispatchId": self.dispatch_id,
            "asks": len(asks),
            "answers": len(answers),
            "unresolvedAskIds": sorted(a["ask_id"] for a in asks if a["ask_id"] not in answered),
            "steers": len(steers),
            "unackedSteerIds": sorted(
                s["steer_id"]
                for s in steers
                if s["requires_ack"] and s["steer_id"] not in set(acked)
            ),
            "lastAckedSteerId": acked[-1] if acked else None,
            "sequenceGaps": self.sequence_gaps(),
        }


__all__ = [
    "ANSWERED_BY",
    "BLOCKED_ON",
    "BODY_CAP_BYTES",
    "DEFAULT_MAX_ASKS",
    "MAX_DEADLINE_S",
    "MAX_DEAD_LETTER_FILES",
    "MAX_OPTIONS",
    "POLICY_PREFIX",
    "SCHEMA_VERSION",
    "SEVERITIES",
    "STEER_KINDS",
    "Mailbox",
    "MailboxCapError",
    "MailboxError",
    "MailboxOpenAskError",
    "answer_source_valid",
    "derive_deadline_s",
    "validate_ack",
    "validate_answer",
    "validate_ask",
    "validate_steer",
    "validate_steer_request",
]
