"""Per-model lockout state machine (research §9.4); planner elimination is in test_production_plan_purity."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from pitwall.routing.lockout import LockoutKey, LockoutState, LockoutTable, backoff_for

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
KEY = LockoutKey("prov_gw", "beta/b1")


def test_backoff_table_matches_spec() -> None:
    assert [backoff_for(n).total_seconds() for n in (1, 2, 3, 4, 5, 10)] == [
        120,
        240,
        480,
        960,
        1800,
        1800,
    ]


def test_quota_exhausted_locks_until_reset_at() -> None:
    table = LockoutTable()
    table.record_failure(
        KEY, now=NOW, reason="quota_exhausted", reset_at=NOW + dt.timedelta(hours=3)
    )
    assert table.is_locked(KEY, now=NOW + dt.timedelta(hours=2, minutes=59))
    assert not table.is_locked(KEY, now=NOW + dt.timedelta(hours=3))


def test_success_halves_failure_count() -> None:
    table = LockoutTable()
    for _ in range(4):
        table.record_failure(KEY, now=NOW, reason="rate_limit_exceeded")
    table.record_success(KEY, now=NOW + dt.timedelta(hours=1))
    assert table.snapshot()["prov_gw/beta/b1"]["failures"] == 2


def test_permanent_ban_is_terminal_and_operator_visible() -> None:
    table = LockoutTable()
    table.record_failure(KEY, now=NOW, reason="permanent_ban")
    table.record_success(KEY, now=NOW + dt.timedelta(days=30))
    assert table.is_locked(KEY, now=NOW + dt.timedelta(days=365))
    assert table.snapshot()["prov_gw/beta/b1"]["permanent"] is True


@pytest.mark.anyio
async def test_record_failure_and_success_persist_through_the_configured_hook() -> None:
    """The table is process-local; the API's writes must reach provider_quotas.evidence."""
    import asyncio

    table = LockoutTable()
    persisted: list[tuple[LockoutKey, dict[str, object]]] = []

    async def persist(key: LockoutKey, state: dict[str, object]) -> None:
        persisted.append((key, state))

    table.set_persister(persist)
    table.record_failure(KEY, now=NOW, reason="rate_limit_exceeded")
    table.record_success(KEY, now=NOW + dt.timedelta(minutes=5))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert [k for k, _ in persisted] == [KEY, KEY]
    assert persisted[0][1]["failures"] == 1 and persisted[0][1]["reason"] == "rate_limit_exceeded"
    assert persisted[0][1]["locked_until"] == (NOW + backoff_for(1)).isoformat()
    assert persisted[1][1]["locked_until"] is None and persisted[1][1]["failures"] == 0


@pytest.mark.anyio
async def test_configure_lockout_persistence_restores_state_and_writes_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from decimal import Decimal
    from unittest.mock import AsyncMock

    from pitwall.routing import lockout
    from pitwall.routing.quota import QuotaRecord

    repo = AsyncMock()
    repo.list_all.return_value = (
        QuotaRecord(
            provider_id="prov_gw",
            pool_key="alpha-pool",
            free_type="recurring-monthly",
            window_start=None,
            reset_at=None,
            budget_units=Decimal(10),
            used_units=Decimal(0),
            tos_verdict="ok",
            evidence={
                "lockout": {
                    "model_id": "beta/b1",
                    "failures": 2,
                    "locked_until": (NOW + dt.timedelta(hours=1)).isoformat(),
                    "reason": "quota_exhausted",
                    "permanent": False,
                    "seq": 41,
                }
            },
        ),
    )
    monkeypatch.setattr(lockout, "_quota_repository", lambda _pool: repo)
    table = LockoutTable()
    monkeypatch.setattr(lockout, "_TABLE", table)

    await lockout.configure_lockout_persistence(object())

    assert table.is_locked(KEY, now=NOW)
    assert not table.is_locked(KEY, now=NOW + dt.timedelta(hours=2))

    table.record_failure(KEY, now=NOW, reason="permanent_ban")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    repo.set_lockout.assert_awaited_once()
    provider_id, model_id, state = repo.set_lockout.await_args.args
    assert (provider_id, model_id) == ("prov_gw", "beta/b1")
    assert state["permanent"] is True and state["failures"] == 3
    assert state["seq"] == 42


class _NewestWinsStore:
    """Mirrors QuotaRepository.set_lockout: a write lands only over an older ``seq``."""

    def __init__(self) -> None:
        self.stored: dict[str, Any] | None = None

    def apply(self, document: dict[str, Any]) -> None:
        seq = int(document["seq"])
        if self.stored is None or int(self.stored["seq"]) < seq:
            self.stored = document


async def _delayed_persist_run(
    transitions: list[str], *, release_order: list[int]
) -> tuple[LockoutTable, _NewestWinsStore]:
    """Run transitions, letting their persisters complete in ``release_order`` (indexes)."""
    import asyncio

    table = LockoutTable()
    store = _NewestWinsStore()
    gates = [asyncio.Event() for _ in transitions]
    started = 0

    async def persist(_key: LockoutKey, document: dict[str, Any]) -> None:
        nonlocal started
        index = started
        started += 1
        await gates[index].wait()
        store.apply(document)

    table.set_persister(persist)
    for step in transitions:
        if step == "failure":
            table.record_failure(KEY, now=NOW, reason="rate_limit_exceeded")
        else:
            table.record_success(KEY, now=NOW + dt.timedelta(minutes=5))
    await asyncio.sleep(0)
    for index in release_order:
        gates[index].set()
        for _ in range(3):
            await asyncio.sleep(0)
    return table, store


@pytest.mark.anyio
async def test_a_delayed_older_failure_never_replaces_a_newer_failure() -> None:
    table, store = await _delayed_persist_run(["failure", "failure"], release_order=[1, 0])

    assert store.stored is not None
    assert store.stored["failures"] == 2
    assert store.stored["failures"] == table.snapshot()["prov_gw/beta/b1"]["failures"]


@pytest.mark.anyio
async def test_a_delayed_failure_never_replaces_a_newer_success() -> None:
    table, store = await _delayed_persist_run(["failure", "success"], release_order=[1, 0])

    assert store.stored is not None
    assert store.stored["locked_until"] is None
    assert store.stored["failures"] == 0
    assert table.snapshot()["prov_gw/beta/b1"]["locked_until"] is None


@pytest.mark.anyio
async def test_a_delayed_success_never_replaces_a_newer_failure() -> None:
    table, store = await _delayed_persist_run(
        ["failure", "success", "failure"], release_order=[2, 1, 0]
    )

    assert store.stored is not None
    assert store.stored["failures"] == table.snapshot()["prov_gw/beta/b1"]["failures"]
    assert store.stored["locked_until"] is not None


@pytest.mark.anyio
async def test_restored_sequence_seeds_the_counter_so_new_writes_stay_newer() -> None:
    import asyncio

    table = LockoutTable()
    documents: list[dict[str, Any]] = []

    async def persist(_key: LockoutKey, document: dict[str, Any]) -> None:
        documents.append(document)

    table.set_persister(persist)
    table.load(KEY, LockoutState(failures=1), seq=41)
    table.record_failure(KEY, now=NOW, reason="rate_limit_exceeded")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert documents[0]["seq"] == 42


@pytest.mark.anyio
async def test_a_row_with_a_malformed_state_still_advances_the_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from decimal import Decimal
    from unittest.mock import AsyncMock

    from pitwall.routing import lockout
    from pitwall.routing.quota import QuotaRecord

    repo = AsyncMock()
    repo.list_all.return_value = (
        QuotaRecord(
            provider_id="prov_gw",
            pool_key="alpha-pool",
            free_type="recurring-monthly",
            window_start=None,
            reset_at=None,
            budget_units=Decimal(10),
            used_units=Decimal(0),
            tos_verdict="ok",
            evidence={
                "lockout": {
                    "model_id": "beta/b1",
                    "failures": 1,
                    "locked_until": "not-a-timestamp",
                    "seq": 7,
                }
            },
        ),
    )
    monkeypatch.setattr(lockout, "_quota_repository", lambda _pool: repo)
    table = LockoutTable()
    monkeypatch.setattr(lockout, "_TABLE", table)

    await lockout.configure_lockout_persistence(object())
    table.record_failure(KEY, now=NOW, reason="rate_limit_exceeded")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert repo.set_lockout.await_args.args[2]["seq"] > 7
