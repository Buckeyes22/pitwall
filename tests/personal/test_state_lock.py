"""Concurrent writers to the personal state files never lose a record."""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest

from pitwall.personal.state import PersonalLease, StateStore


def _lease(route: str) -> PersonalLease:
    now = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.UTC)
    return PersonalLease(
        route=route,
        pod_id=f"pod-{route}",
        model="m",
        served_model_id="m",
        engine="llama.cpp",
        variant=None,
        image="img",
        gpu_class="gpu",
        gpu_count=1,
        cloud="community",
        price_per_hour_usd="0.220000",
        endpoint_url="https://pod.example/v1",
        key_env="PITWALL_ENDPOINT_KEY",
        launched_at=now,
        deadline_at=now + dt.timedelta(minutes=45),
        state="launching",
    )


def _widen_the_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every read pauses, so an unlocked read-modify-write interleaves reliably."""
    original = StateStore.load

    def slow_load(self: StateStore) -> list[PersonalLease]:
        leases = original(self)
        time.sleep(0.05)
        return leases

    monkeypatch.setattr(StateStore, "load", slow_load)


def _run_together(*jobs: Callable[[], object]) -> None:
    barrier = threading.Barrier(len(jobs))
    errors: list[BaseException] = []

    def worker(job: Callable[[], object]) -> None:
        barrier.wait()
        try:
            job()
        except BaseException as exc:  # reason: surfaced after the join
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(job,)) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_concurrent_upserts_keep_both_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pitwall"
    _widen_the_race(monkeypatch)

    _run_together(
        lambda: StateStore(root).upsert(_lease("alpha")),
        lambda: StateStore(root).upsert(_lease("beta")),
    )

    assert sorted(lease.route for lease in StateStore(root).load()) == ["alpha", "beta"]


def test_concurrent_updates_and_upserts_keep_every_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pitwall"
    StateStore(root).upsert(_lease("alpha"))
    _widen_the_race(monkeypatch)

    _run_together(
        lambda: StateStore(root).update("alpha", state="ready"),
        lambda: StateStore(root).upsert(_lease("beta")),
    )

    leases = {lease.route: lease for lease in StateStore(root).load()}
    assert set(leases) == {"alpha", "beta"}
    assert leases["alpha"].state == "ready"


def test_concurrent_spend_records_add_up(tmp_path: Path) -> None:
    root = tmp_path / "pitwall"
    StateStore(root).record_spend("2026-09", Decimal("0"))

    _run_together(*[lambda: StateStore(root).record_spend("2026-09", Decimal("1"))] * 8)

    assert StateStore(root).month_spend("2026-09") == Decimal("8.000000")
