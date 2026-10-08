"""Tests for pitwall.core.idempotency.

Covers:
  - reserve_idempotency_key: fresh insert, replay, and mismatch detection
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.core.idempotency import (
    IdempotencyMismatch,
    IdempotencyReservation,
    reserve_idempotency_key,
)

pytestmark = pytest.mark.anyio


def _hash_input(data: dict | list | None) -> str:
    if data is None:
        return hashlib.sha256(b"null").hexdigest()
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _make_conn(
    *,
    fetchrow_side_effect: list[Any] | None = None,
    execute_return: str = "UPDATE 1",
) -> MagicMock:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    conn.execute = AsyncMock(return_value=execute_return)
    return conn


# ---------------------------------------------------------------------------
# reserve_idempotency_key
# ---------------------------------------------------------------------------


async def test_reserve_fresh_key_returns_is_new() -> None:
    conn = _make_conn(
        fetchrow_side_effect=[
            {"workload_id": "wkl_abc"},
        ]
    )
    result = await reserve_idempotency_key(conn, key="key-1", body_hash="h1", workload_id="wkl_abc")
    assert result == IdempotencyReservation(is_new=True, workload_id="wkl_abc")
    conn.fetchrow.assert_called_once()


async def test_reserve_replay_same_body_returns_existing() -> None:
    body = {"prompt": "hello"}
    body_hash = _hash_input(body)
    conn = _make_conn(
        fetchrow_side_effect=[
            None,
            {"workload_id": "wkl_orig"},
            {"input": body},
        ]
    )
    result = await reserve_idempotency_key(
        conn, key="key-1", body_hash=body_hash, workload_id="wkl_new"
    )
    assert result == IdempotencyReservation(is_new=False, workload_id="wkl_orig")


async def test_reserve_replay_mismatched_body_raises() -> None:
    original_body = {"prompt": "hello"}
    new_hash = _hash_input({"prompt": "different"})
    conn = _make_conn(
        fetchrow_side_effect=[
            None,
            {"workload_id": "wkl_orig"},
            {"input": original_body},
        ]
    )
    with pytest.raises(IdempotencyMismatch) as exc_info:
        await reserve_idempotency_key(conn, key="key-1", body_hash=new_hash, workload_id="wkl_new")
    assert exc_info.value.original_workload_id == "wkl_orig"


async def test_reserve_replay_no_workload_row_returns_existing() -> None:
    conn = _make_conn(
        fetchrow_side_effect=[
            None,
            {"workload_id": "wkl_orig"},
            None,
        ]
    )
    result = await reserve_idempotency_key(
        conn, key="key-1", body_hash="any", workload_id="wkl_new"
    )
    assert result == IdempotencyReservation(is_new=False, workload_id="wkl_orig")


async def test_reserve_replay_null_input_returns_existing() -> None:
    conn = _make_conn(
        fetchrow_side_effect=[
            None,
            {"workload_id": "wkl_orig"},
            {"input": None},
        ]
    )
    result = await reserve_idempotency_key(
        conn, key="key-1", body_hash="any", workload_id="wkl_new"
    )
    assert result == IdempotencyReservation(is_new=False, workload_id="wkl_orig")


# ---------------------------------------------------------------------------
# body_hash stored on the reservation (the winner may not have persisted input yet)
# ---------------------------------------------------------------------------


async def test_reserve_stores_the_body_hash() -> None:
    conn = _make_conn(fetchrow_side_effect=[{"workload_id": "wkl_abc"}])
    await reserve_idempotency_key(conn, key="key-1", body_hash="h1", workload_id="wkl_abc")
    assert conn.fetchrow.await_args.args[1:] == ("key-1", "wkl_abc", "h1")


async def test_reserve_different_body_is_refused_while_workload_input_is_null() -> None:
    hash_a = _hash_input({"prompt": "A"})
    hash_b = _hash_input({"prompt": "B"})
    conn = _make_conn(
        fetchrow_side_effect=[
            {"workload_id": "wkl_pending_key-1"},
            None,
            {"workload_id": "wkl_pending_key-1", "body_hash": hash_a},
            {"input": None},
        ]
    )
    first = await reserve_idempotency_key(
        conn, key="key-1", body_hash=hash_a, workload_id="wkl_pending_key-1"
    )
    assert first.is_new is True

    with pytest.raises(IdempotencyMismatch) as exc_info:
        await reserve_idempotency_key(
            conn, key="key-1", body_hash=hash_b, workload_id="wkl_pending_key-1"
        )
    assert exc_info.value.original_workload_id == "wkl_pending_key-1"


async def test_reserve_same_body_replays_while_workload_input_is_null() -> None:
    hash_a = _hash_input({"prompt": "A"})
    conn = _make_conn(
        fetchrow_side_effect=[
            None,
            {"workload_id": "wkl_pending_key-1", "body_hash": hash_a},
            {"input": None},
        ]
    )
    result = await reserve_idempotency_key(
        conn, key="key-1", body_hash=hash_a, workload_id="wkl_other"
    )
    assert result == IdempotencyReservation(is_new=False, workload_id="wkl_pending_key-1")
