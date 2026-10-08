from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pitwall.routing.cooldown import CooldownState, CooldownStateMachine
from pitwall.routing.saturation import SaturationDetector

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self) -> None:
        self.members: dict[str, dict[str, float]] = {}

    async def zadd(self, key: str, values: dict[str, float]) -> int:
        bucket = self.members.setdefault(key, {})
        before = len(bucket)
        bucket.update(values)
        return len(bucket) - before

    async def zremrangebyscore(self, key: str, minimum: float, maximum: float) -> int:
        bucket = self.members.setdefault(key, {})
        removed = [name for name, score in bucket.items() if minimum <= score <= maximum]
        for name in removed:
            del bucket[name]
        return len(removed)

    async def zcard(self, key: str) -> int:
        return len(self.members.setdefault(key, {}))

    async def zrange(
        self,
        key: str,
        start: int,
        stop: int,
        *,
        withscores: bool,
    ) -> list[tuple[str, float]]:
        assert start == 0
        assert stop == -1
        assert withscores
        return sorted(self.members.setdefault(key, {}).items(), key=lambda item: item[1])

    async def expire(self, key: str, seconds: int) -> bool:
        del key, seconds
        return True


def test_non_failure_classifications_never_trip_cooldown() -> None:
    machine = CooldownStateMachine()
    initial = CooldownState(consecutive_failures=2, health_status="healthy")

    warming = machine.record_request_outcome(initial, classification="warming", now=NOW)
    misconfigured = machine.record_request_outcome(initial, classification="misconfigured", now=NOW)

    assert warming.consecutive_failures == 2
    assert warming.health_status == "warming"
    assert warming.cooldown_until is None
    assert misconfigured.consecutive_failures == 2
    assert misconfigured.health_status == "misconfigured"
    assert misconfigured.cooldown_until is None


def test_success_resets_failures_and_failure_counts() -> None:
    machine = CooldownStateMachine()
    state = CooldownState(consecutive_failures=2, health_status="healthy")

    failed = machine.record_request_outcome(state, classification="failure", now=NOW)
    recovered = machine.record_request_outcome(
        failed, classification="healthy", now=NOW + timedelta(seconds=1)
    )

    assert failed.health_status == "unhealthy"
    assert recovered == CooldownState(health_status="healthy")


@pytest.mark.anyio
async def test_fixed_cadence_4xx_storm_emits_named_signal() -> None:
    detector = SaturationDetector(FakeRedis(), window_s=60, threshold=4)
    signal = None
    for offset in (0, 10, 20, 30):
        signal = await detector.observe_4xx(
            provider_id="prov-selfhosted",
            token_fingerprint="sha256:0123456789ab",
            capability_name="llm.local",
            now=NOW + timedelta(seconds=offset),
        )

    assert signal is not None
    assert signal.provider_id == "prov-selfhosted"
    assert signal.token_fingerprint == "sha256:0123456789ab"
    assert signal.capability_name == "llm.local"
    assert signal.count == 4
    assert signal.window_s == 60
    assert signal.observed_at == NOW + timedelta(seconds=30)
    assert signal.rate_per_s == pytest.approx(4 / 60)


@pytest.mark.anyio
async def test_same_count_with_unsteady_gaps_does_not_emit() -> None:
    detector = SaturationDetector(FakeRedis(), window_s=60, threshold=4)
    signal = None
    for offset in (0, 1, 2, 40):
        signal = await detector.observe_4xx(
            provider_id="prov-selfhosted",
            token_fingerprint="sha256:0123456789ab",
            capability_name="llm.local",
            now=NOW + timedelta(seconds=offset),
        )

    assert signal is None


@pytest.mark.anyio
async def test_capabilities_use_separate_saturation_keys() -> None:
    redis = FakeRedis()
    detector = SaturationDetector(redis, window_s=60, threshold=4)

    for capability_name in ("llm.local", "llm.tools"):
        for offset in (0, 10, 20):
            signal = await detector.observe_4xx(
                provider_id="prov-selfhosted",
                token_fingerprint="sha256:0123456789ab",
                capability_name=capability_name,
                now=NOW + timedelta(seconds=offset),
            )
            assert signal is None

    assert set(redis.members) == {
        "pitwall:saturation:prov-selfhosted:sha256:0123456789ab:llm.local",
        "pitwall:saturation:prov-selfhosted:sha256:0123456789ab:llm.tools",
    }
