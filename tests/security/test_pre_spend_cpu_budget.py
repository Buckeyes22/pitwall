"""The pre-spend scan budget bounds scan CPU cost, not wall-clock scheduling delay."""

from __future__ import annotations

import time
from typing import Any

import pytest

from pitwall.security import pre_spend
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendInspectionService,
)

pytestmark = pytest.mark.security

_PROVIDER_PAYLOAD = {
    "name": "local-embed",
    "capability": "embedding.local",
    "endpoint_id": "ep-local",
    "provider_type": "runpod_serverless",
    "region": "US-OH-1",
    "gpu_class": "NVIDIA RTX A4000",
    "priority": 100,
    "cost": {"mode": "per_second_active", "per_second_active": "0.0004"},
}


def test_wall_clock_stall_does_not_refuse_valid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """A busy host that delays the scanning thread must not trip the fail-closed limit."""
    wall = {"now": 0}

    def stalled_wall_clock() -> int:
        wall["now"] += 1_000_000_000  # every read: one second of scheduler delay
        return wall["now"]

    monkeypatch.setattr(time, "monotonic_ns", stalled_wall_clock)
    monkeypatch.setattr(time, "perf_counter_ns", stalled_wall_clock)
    service = PreSpendInspectionService()

    result = service.preview(_PROVIDER_PAYLOAD)

    assert result.decision == PreSpendDecision.ALLOW
    assert result.limited is False
    assert result.limit_reason is None


class _CpuBurningPattern:
    """A matcher that spends real CPU, standing in for pathological regex backtracking."""

    def __init__(self, burn_ms: int) -> None:
        self._burn_ns = burn_ms * 1_000_000

    def _burn(self) -> None:
        start = time.thread_time_ns()
        while time.thread_time_ns() - start < self._burn_ns:
            pass

    def search(self, _value: str) -> None:
        self._burn()

    def sub(self, _repl: Any, value: str) -> str:
        self._burn()
        return value


def test_cpu_burning_scan_still_hits_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pre_spend,
        "_SECRET_VALUE_PATTERNS",
        (("slow_rule", _CpuBurningPattern(burn_ms=80), pre_spend.REDACTED_SECRET),),
    )
    service = PreSpendInspectionService()

    result = service.preview({"input": "value"})

    assert result.limited is True
    assert result.limit_reason == "timeout"
    assert result.decision == PreSpendDecision.BLOCK


def test_garbage_collection_during_a_scan_does_not_refuse_valid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The collector runs on whichever thread allocates; its CPU time is not the scan's cost.

    CI run 36799378175 blocked a two-integer payload with ``limit_reason='timeout'`` and
    ``inspected_bytes=0`` after 59 ms of thread CPU: a full collection on a large heap ran
    inside the scan. Here a collection worth 100 ms of CPU happens before the scan's first check.
    """
    clock = {"cpu": 0, "gc": 0}
    original = pre_spend._scan_pre_spend_value

    def collecting(*args: Any, **kwargs: Any) -> Any:
        clock["cpu"] += 100_000_000
        clock["gc"] += 100_000_000
        return original(*args, **kwargs)

    monkeypatch.setattr(pre_spend, "_scan_pre_spend_value", collecting)
    service = PreSpendInspectionService(
        thread_time_ns=lambda: clock["cpu"], gc_time_ns=lambda: clock["gc"]
    )

    result = service.preview({"input_tokens": 1000, "max_output_tokens": 1000})

    assert result.decision == PreSpendDecision.ALLOW
    assert result.limited is False


def test_a_real_collection_is_counted_against_the_collecting_thread() -> None:
    import gc

    before = pre_spend._gc_thread_time_ns()
    garbage = [[index] for index in range(200_000)]
    gc.collect()
    del garbage

    assert pre_spend._gc_thread_time_ns() > before
