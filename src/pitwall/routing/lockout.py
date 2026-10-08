"""(provider, model) lockouts mirroring OmniRoute accountFallback semantics (research §9.4)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

log = logging.getLogger(__name__)
Persister = Callable[["LockoutKey", dict[str, Any]], Awaitable[None]]

_BASE = dt.timedelta(seconds=120)
_CAP = dt.timedelta(minutes=30)


def backoff_for(failures: int) -> dt.timedelta:
    base: dt.timedelta = _BASE * (2 ** max(0, failures - 1))
    capped: dt.timedelta = min(base, _CAP)
    return capped


@dataclass(frozen=True, slots=True)
class LockoutKey:
    provider_id: str
    model_id: str

    def __str__(self) -> str:
        return f"{self.provider_id}/{self.model_id}"


@dataclass(frozen=True, slots=True)
class LockoutState:
    failures: int = 0
    locked_until: dt.datetime | None = None
    reason: str | None = None
    permanent: bool = False

    def to_dict(self, model_id: str | None = None) -> dict[str, Any]:
        document: dict[str, Any] = {
            "failures": self.failures,
            "locked_until": self.locked_until.isoformat() if self.locked_until else None,
            "reason": self.reason,
            "permanent": self.permanent,
        }
        if model_id is not None:
            document["model_id"] = model_id
        return document

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> LockoutState:
        locked_until = raw.get("locked_until")
        parsed = dt.datetime.fromisoformat(str(locked_until)) if locked_until else None
        if parsed is not None and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        reason = raw.get("reason")
        return cls(
            failures=int(raw.get("failures") or 0),
            locked_until=parsed,
            reason=str(reason) if reason else None,
            permanent=bool(raw.get("permanent", False)),
        )


def _state_is_locked(state: LockoutState | None, *, now: dt.datetime) -> bool:
    if state is None:
        return False
    if state.permanent:
        return True
    return state.locked_until is not None and now < state.locked_until


@dataclass(frozen=True, slots=True)
class LockoutSnapshot:
    """Immutable copy of lockout state for planners that must stay free of I/O."""

    states: Mapping[LockoutKey, LockoutState] = field(default_factory=dict)

    def is_locked(self, key: LockoutKey, *, now: dt.datetime) -> bool:
        return _state_is_locked(self.states.get(key), now=now)


class LockoutTable:
    """In-memory per process; every change is also handed to the configured persister.

    The API process is the only one that records failures, so the exporter and the
    reconciler read the persisted copy in ``provider_quotas.evidence`` rather than their own,
    always empty, table.

    Each transition takes the next ``seq`` under the table lock and carries it in its persisted
    document, so a write that lands late is rejected by the store instead of replacing a newer
    state. The counter is table-wide, which also orders the transitions of different models
    that share one provider row.
    """

    def __init__(
        self,
        *,
        on_failure: Callable[[LockoutKey, str], None] | None = None,
    ) -> None:
        self._states: dict[LockoutKey, LockoutState] = {}
        self._seq = 0
        self._lock = threading.Lock()
        self._on_failure = on_failure
        self._persister: Persister | None = None

    def set_on_failure(self, callback: Callable[[LockoutKey, str], None] | None) -> None:
        """Replace the failure-side hook atomically. Pass ``None`` to clear it."""
        with self._lock:
            self._on_failure = callback

    def set_persister(self, persister: Persister | None) -> None:
        """Replace the async persistence hook. Pass ``None`` to clear it."""
        with self._lock:
            self._persister = persister

    def load(self, key: LockoutKey, state: LockoutState, *, seq: int = 0) -> None:
        """Seed one persisted state without triggering hooks (startup restore).

        ``seq`` is the persisted document's sequence: later transitions number above it.
        """
        with self._lock:
            self._states[key] = state
            self._seq = max(self._seq, seq)

    def advance_sequence(self, seq: int) -> None:
        """Make later transitions number above ``seq`` (a persisted document's sequence)."""
        with self._lock:
            self._seq = max(self._seq, seq)

    def _persist(self, key: LockoutKey, state: LockoutState, seq: int) -> None:
        persister = self._persister
        if persister is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop in this thread: the in-memory state still governs planning

        async def run() -> None:
            try:
                await persister(key, {**state.to_dict(key.model_id), "seq": seq})
            except Exception:  # reason: persistence is observability; it never fails routing
                log.warning("lockout persistence failed for %s", key, exc_info=True)

        task = loop.create_task(run())
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)

    def record_failure(
        self,
        key: LockoutKey,
        *,
        now: dt.datetime,
        reason: str,
        reset_at: dt.datetime | None = None,
    ) -> LockoutState:
        with self._lock:
            current = self._states.get(key, LockoutState())
            if current.permanent:
                return current
            failures = current.failures + 1
            if reason == "permanent_ban":
                state = LockoutState(
                    failures=failures, locked_until=None, reason=reason, permanent=True
                )
            elif reset_at is not None:
                state = LockoutState(failures=failures, locked_until=reset_at, reason=reason)
            else:
                state = LockoutState(
                    failures=failures, locked_until=now + backoff_for(failures), reason=reason
                )
            self._states[key] = state
            self._seq += 1
            seq = self._seq
            on_failure = self._on_failure
        if on_failure is not None:
            on_failure(key, reason)
        self._persist(key, state, seq)
        return state

    def record_success(self, key: LockoutKey, *, now: dt.datetime) -> LockoutState:
        with self._lock:
            current = self._states.get(key, LockoutState())
            if current.permanent:
                return current
            state = replace(current, failures=current.failures // 2, locked_until=None, reason=None)
            self._states[key] = state
            self._seq += 1
            seq = self._seq
        self._persist(key, state, seq)
        return state

    def is_locked(self, key: LockoutKey, *, now: dt.datetime) -> bool:
        return _state_is_locked(self._states.get(key), now=now)

    def frozen(self) -> LockoutSnapshot:
        """Copy the current states so a planner can read them without touching the table."""
        with self._lock:
            return LockoutSnapshot(dict(self._states))

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {str(key): s.to_dict() for key, s in self._states.items()}

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


_TABLE = LockoutTable()
_PENDING: set[asyncio.Task[None]] = set()


def get_lockout_table() -> LockoutTable:
    return _TABLE


def _quota_repository(pool: Any) -> Any:
    from pitwall.db.quota_repository import QuotaRepository  # local: the db package imports routing

    return QuotaRepository(pool)


async def configure_lockout_persistence(pool: Any) -> None:
    """Restore persisted lockouts from ``provider_quotas.evidence`` and persist future changes.

    Call once from the API lifespan after the pool exists. Reads never raise past this
    function: a missing table leaves the in-memory state empty and persistence disabled.
    """
    repo = _quota_repository(pool)
    table = get_lockout_table()
    try:
        records = await repo.list_all()
    except Exception:  # reason: startup restore is best effort; planning proceeds unlocked
        log.warning("lockout restore skipped: provider_quotas unavailable", exc_info=True)
        return
    for record in records:
        raw = record.evidence.get("lockout") if isinstance(record.evidence, Mapping) else None
        if not isinstance(raw, Mapping):
            continue
        # Seed the counter from every persisted document, even one whose state cannot restore.
        try:
            seq = int(raw.get("seq") or 0)
        except TypeError, ValueError:
            seq = 0
        table.advance_sequence(seq)
        if not raw.get("model_id"):
            continue
        try:
            state = LockoutState.from_dict(raw)
        except TypeError, ValueError:
            continue
        table.load(LockoutKey(record.provider_id, str(raw["model_id"])), state, seq=seq)

    async def persist(key: LockoutKey, state: dict[str, Any]) -> None:
        await repo.set_lockout(key.provider_id, key.model_id, state)

    table.set_persister(persist)


def model_lockout_key(provider: Any) -> LockoutKey | None:
    """Return the ``(provider_id, model_id)`` lockout key, or None if missing."""

    config = getattr(provider, "config", None)
    if config is None and isinstance(provider, dict):
        config = provider.get("config")
    if not isinstance(config, Mapping):
        return None
    # local import: the providers package imports routing
    from pitwall.providers.registry import declarations_for_provider

    model_id: object = None
    for declaration in declarations_for_provider(provider):
        for path in declaration.lockout_model_paths:
            node: object = config
            for key in path:
                node = node.get(key) if isinstance(node, Mapping) else None
            if node is not None:
                model_id = node
                break
        if model_id is not None:
            break
    if not isinstance(model_id, str) or not model_id:
        return None
    provider_id = getattr(provider, "id", None)
    if provider_id is None and isinstance(provider, dict):
        provider_id = provider.get("id")
    if not isinstance(provider_id, str) or not provider_id:
        return None
    return LockoutKey(provider_id=provider_id, model_id=model_id)


__all__ = [
    "LockoutKey",
    "LockoutSnapshot",
    "LockoutState",
    "LockoutTable",
    "backoff_for",
    "configure_lockout_persistence",
    "get_lockout_table",
    "model_lockout_key",
]
