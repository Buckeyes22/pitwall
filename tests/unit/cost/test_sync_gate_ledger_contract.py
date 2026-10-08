"""Behaviour pins for gate_sync_inference against an in-memory workload ledger.

The fake connection answers the idempotency, budget, and workload queries the gate
issues and records every statement, so these tests pin what the gate persists and
logs for new admissions, replays, lost admission races, and provider failures.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from decimal import Decimal
from typing import Any

import pytest

from pitwall.core.idempotency import IdempotencyMismatch
from pitwall.core.models import Capability
from pitwall.cost.budget_gate import BudgetGate
from pitwall.cost.sync_gate import gate_sync_inference

_PROVIDER_COST = {"per_request": Decimal("0.01")}
_PLACEHOLDER = re.compile(r"\$(\d+)")


def _normalise(sql: str) -> str:
    return " ".join(sql.split())


def _bound(expression: str, args: tuple[Any, ...]) -> Any:
    placeholder = _PLACEHOLDER.search(expression)
    return args[int(placeholder.group(1)) - 1] if placeholder else expression.strip("'")


def _top_level_split(text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in text:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    return [*parts, current]


def _write_columns(sql: str, args: tuple[Any, ...]) -> tuple[str, str, dict[str, Any]]:
    """Return (operation, table, column -> bound value) for an INSERT or UPDATE by id."""
    text = _normalise(sql)
    insert = re.match(r"INSERT INTO (\S+) \((.*?)\) VALUES \((.*?)\)", text)
    if insert is not None:
        table, names, values = insert.groups()
        columns = {
            name.strip(): _bound(value.strip(), args)
            for name, value in zip(names.split(","), values.split(","), strict=True)
        }
        return "insert", table, columns
    update = re.match(r"UPDATE (\S+) SET (.*) WHERE id = (\$\d+)$", text)
    assert update is not None, text
    table, assignments, id_ref = update.groups()
    columns = {"id": _bound(id_ref, args)}
    for assignment in _top_level_split(assignments):
        name, expression = assignment.split("=", 1)
        columns[name.strip()] = _bound(expression.strip(), args)
    return "update", table, columns


def _capability() -> Capability:
    return Capability(
        id="cap_sync_ledger",
        name="embedding.sync.ledger",
        version="1.0.0",
        **{"class": "embedding"},
        cost_mode="per_request",
        defaults={"execution_timeout_ms": 60_000},
        created_at="2026-05-26T14:00:00Z",
        updated_at="2026-05-26T14:00:00Z",
    )


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Ledger:
    """One connection that plays the idempotency, budget, and workload tables."""

    def __init__(
        self,
        *,
        key_owner: str | None = None,
        admitted_owner: str | None = None,
        replay_row: dict[str, Any] | None = None,
    ) -> None:
        self.key_owner = key_owner
        self.admitted_owner = admitted_owner
        self.replay_row = replay_row
        self.writes: list[tuple[str, str, dict[str, Any]]] = []

    def transaction(self) -> _Tx:
        return _Tx()

    async def execute(self, sql: str, *args: Any) -> str:
        if _normalise(sql) == "SELECT pg_advisory_xact_lock($1)":
            return "SELECT 1"
        self.writes.append(_write_columns(sql, args))
        return "UPDATE 1"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        text = _normalise(sql)
        if text.startswith("INSERT INTO pitwall.idempotency_keys"):
            row = _write_columns(sql, args)[2]
            return None if self.key_owner else {"workload_id": row["workload_id"]}
        if "FROM pitwall.idempotency_keys" in text:
            return {"workload_id": self.key_owner}
        if "FROM pitwall.budget_limits" in text:
            return None
        selected = re.match(r"SELECT (.*?) FROM pitwall\.workloads WHERE id =", text)
        if selected is not None:
            columns = {column.strip() for column in selected.group(1).split(",")}
            return self.replay_row if "result" in columns else {"input": None}
        assert "FROM pitwall.workloads" in text, text
        return {"s": Decimal("0")}

    async def fetchval(self, sql: str, *args: Any) -> Any:
        text = _normalise(sql)
        if text.startswith("SELECT id FROM pitwall.workloads WHERE idempotency_key ="):
            return self.admitted_owner
        write = _write_columns(sql, args)
        self.writes.append(write)
        return write[2]["id"]

    def workload_writes(self, operation: str, **expected: Any) -> list[dict[str, Any]]:
        return [
            columns
            for op, table, columns in self.writes
            if op == operation
            and table == "pitwall.workloads"
            and all(columns.get(key) == value for key, value in expected.items())
        ]


class _Acquire:
    def __init__(self, conn: _Ledger) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Ledger:
        return self.conn

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Pool:
    def __init__(self, conn: _Ledger) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


def _gate(ledger: _Ledger, workload_id: str = "wkl_new") -> BudgetGate:
    return BudgetGate(
        _Pool(ledger),
        monthly_budget_usd=Decimal("100"),
        per_request_max_usd=Decimal("10"),
        workload_id_factory=lambda: workload_id,
    )


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records if record.name == "pitwall.cost.sync_gate"
    ]


async def test_new_keyed_admission_logs_once_and_records_submission_and_input_size(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ledger = _Ledger()
    submitted = dt.datetime(2026, 5, 1, 9, tzinfo=dt.UTC)

    async def call() -> dict[str, Any]:
        return {"status": "COMPLETED", "output": [1]}

    with caplog.at_level(logging.INFO, logger="pitwall.cost.sync_gate"):
        result = await gate_sync_inference(
            capability=_capability(),
            provider_id="prov_1",
            provider_cost=_PROVIDER_COST,
            payload={"prompt": "hello"},
            budget_gate=_gate(ledger),
            runpod_caller=call,
            idempotency_key="key-new",
            submitted_at=submitted,
        )

    assert result.workload_id == "wkl_new"
    assert _messages(caplog) == [
        "sync inference admitted: workload_id=wkl_new estimate_usd=0.010000"
    ]
    (insert,) = ledger.workload_writes("insert")
    assert insert["submitted_at"] == submitted
    (running,) = ledger.workload_writes("update", state="running")
    # input_bytes is the compact JSON size of {"prompt":"hello"}
    assert running["input_bytes"] == 18
    assert running["input"] == {"prompt": "hello"}


async def test_replayed_key_returns_the_stored_result_and_logs_each_replay_step(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {"prompt": "hello"}
    ledger = _Ledger(
        key_owner="wkl_old",
        admitted_owner="wkl_old",
        replay_row={"state": "completed", "result": {"answer": 42}, "input": payload},
    )
    calls: list[str] = []

    async def call() -> dict[str, Any]:
        calls.append("called")
        return {}

    with caplog.at_level(logging.INFO, logger="pitwall.cost.sync_gate"):
        result = await gate_sync_inference(
            capability=_capability(),
            provider_id="prov_1",
            provider_cost=_PROVIDER_COST,
            payload=payload,
            budget_gate=_gate(ledger),
            runpod_caller=call,
            idempotency_key="key-old",
        )

    assert result.workload_id == "wkl_old"
    assert result.runpod_result == {"answer": 42}
    assert calls == []
    assert _messages(caplog) == [
        "idempotency replay: workload_id=wkl_old",
        "sync inference admitted: workload_id=wkl_old estimate_usd=0.010000",
        "sync inference idempotency replay: workload_id=wkl_old",
    ]


async def test_losing_the_admission_race_with_a_different_body_names_the_winner() -> None:
    ledger = _Ledger(
        admitted_owner="wkl_winner",
        replay_row={"state": "running", "result": None, "input": {"prompt": "other"}},
    )

    async def call() -> dict[str, Any]:
        raise AssertionError("a replay must not reach the provider")

    with pytest.raises(IdempotencyMismatch) as mismatch:
        await gate_sync_inference(
            capability=_capability(),
            provider_id="prov_1",
            provider_cost=_PROVIDER_COST,
            payload={"prompt": "hello"},
            budget_gate=_gate(ledger),
            runpod_caller=call,
            idempotency_key="key-race",
        )

    assert mismatch.value.original_workload_id == "wkl_winner"


async def test_provider_failure_records_completion_time_and_elapsed_ms() -> None:
    ledger = _Ledger()

    async def call() -> dict[str, Any]:
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError, match="provider down"):
        await gate_sync_inference(
            capability=_capability(),
            provider_id="prov_1",
            provider_cost=_PROVIDER_COST,
            payload={"prompt": "hello"},
            budget_gate=_gate(ledger),
            runpod_caller=call,
        )

    (failed,) = ledger.workload_writes("update", state="failed")
    completed_at = failed["completed_at"]
    execution_ms = failed["execution_ms"]
    assert failed["id"] == "wkl_new"
    assert isinstance(completed_at, dt.datetime)
    assert completed_at.tzinfo is not None
    assert isinstance(execution_ms, int)
    assert execution_ms >= 0
    assert failed["error"] == {"type": "RuntimeError", "message": "provider down"}


class _Opaque:
    def __repr__(self) -> str:
        return "<opaque provider object>"


@pytest.mark.parametrize(
    ("provider_result", "stored"),
    [
        (b"\xffok", {"result": "�ok"}),
        (_Opaque(), {"result": "<opaque provider object>"}),
    ],
)
async def test_non_json_provider_results_are_stored_as_safe_text(
    provider_result: object, stored: dict[str, Any]
) -> None:
    ledger = _Ledger()

    async def call() -> object:
        return provider_result

    await gate_sync_inference(
        capability=_capability(),
        provider_id="prov_1",
        provider_cost=_PROVIDER_COST,
        payload={"prompt": "hello"},
        budget_gate=_gate(ledger),
        runpod_caller=call,
    )

    (terminal,) = ledger.workload_writes("update", state="completed")
    assert terminal["result"] == stored
