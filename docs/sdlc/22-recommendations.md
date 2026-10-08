# Recommendations Engine

## 1. Purpose & Scope

The recommendations engine aggregates three signal planes into prioritized,
actionable operator guidance:

- **Scorecards** — cost×latency×quality metrics per (capability, provider)
- **Burn-rate forecasting** — budget runway and spend-trend projections
- **Reservation planning** — on-demand vs reserved/warm-pool cost evaluations

The engine is **pure, deterministic, and read-only**.  It never mutates state or
auto-applies changes.  Output is a sorted list of :class:`Recommendation`
objects ordered by priority (most urgent first).  An operator, reconciler, or
future "Broker Copilot" MCP agent consumes these recommendations and decides
whether to act.

---

## 2. Components

### `src/pitwall/recommendations/engine.py`

**Responsibility:** Convert signal snapshots into ranked recommendations.

**Key classes/functions:**

```python
@dataclass(frozen=True, slots=True)
class DimensionScore:
    capability_id: str
    provider_id: str
    dimension: str
    score: Decimal
    benchmark: Decimal
    message: str = ""
```

One dimension's score against its benchmark for a (capability, provider) pair, both clamped to 0..1.  It is not `observability.scorecards.EntityScorecard`, the normalized cost, latency, and quality scorecard of one pair; the two types have different fields.  A gap of ``score`` below
``benchmark`` that exceeds the engine threshold triggers a
``switch_to_better_provider`` recommendation.

```python
class RecommendationCategory(StrEnum):
    BUDGET = "budget"
    CAPACITY = "capacity"
    SCORECARD = "scorecard"
```

High-level bucket used for filtering and UI grouping.

```python
@dataclass(frozen=True, slots=True)
class Recommendation:
    action: str
    category: RecommendationCategory
    target_provider_id: str | None
    target_capability_id: str | None
    rationale: str
    estimated_impact_usd: Decimal
    confidence: Decimal
    source_signals: tuple[str, ...]
    priority: int
```

One actionable item.  ``priority`` is an integer where lower values mean higher
urgency.  ``estimated_impact_usd`` may be zero for operational
recommendations (e.g. a scorecard switch) and non-zero for financial
recommendations (e.g. reservation savings).

```python
@dataclass(frozen=True, slots=True)
class RecommendationEngine:
    runway_critical_days: Decimal = Decimal("3")
    runway_warning_days: Decimal = Decimal("7")
    scorecard_threshold: Decimal = Decimal("0.15")

    def recommend(
        self,
        *,
        scorecards: Sequence[DimensionScore] = (),
        burn_rate: BurnRateForecast | None = None,
        reservation: ReservationRecommendation | None = None,
    ) -> list[Recommendation]: ...
```

The core engine.  ``recommend`` is deterministic given the inputs and returns
results sorted by ``(priority, 1 - confidence, action)``.

**Signal → recommendation mapping**

| Signal source | Condition | Action | Priority |
|---|---|---|---|
| Burn rate — runway | ≤ 3 days | reduce_spend_or_increase_budget | 2 |
| Burn rate — runway | ≤ 7 days | review_spend_trend | 6 |
| Burn rate — trend | increasing | investigate_spend_acceleration | 8 |
| Reservation — action | blocked | review_provider_pool | 3 |
| Reservation — action | reserve | reserve_capacity | 4 |
| Scorecard — any dimension | gap > 0.15 below benchmark | switch_to_better_provider | 12 |

---

### `src/pitwall/recommendations/__init__.py`

Re-exports ``Recommendation``, ``RecommendationCategory``,
``RecommendationEngine``, and ``DimensionScore``.  This is the package public
API.

---

## 3. Public Interfaces

### From `pitwall.recommendations`

```python
engine = RecommendationEngine(
    runway_critical_days=Decimal("3"),
    runway_warning_days=Decimal("7"),
    scorecard_threshold=Decimal("0.15"),
)

recs = engine.recommend(
    scorecards=[
        DimensionScore(
            capability_id="cap_llm",
            provider_id="prov_runpod_a",
            dimension="cost",
            score=Decimal("0.4"),
            benchmark=Decimal("0.8"),
            message="Per-second rate is 2x benchmark",
        )
    ],
    burn_rate=BurnRateForecast(...),
    reservation=ReservationRecommendation(...),
)

# recs is sorted by priority ascending
for rec in recs:
    print(rec.priority, rec.action, rec.rationale)
```

---

## 4. Configuration

The engine is configured at instantiation time; there are no env vars or
settings files.

| Parameter | Default | Description |
|---|---|---|
| ``runway_critical_days`` | ``3`` | Burn-rate runway below which a critical budget recommendation is emitted |
| ``runway_warning_days`` | ``7`` | Burn-rate runway below which a warning budget recommendation is emitted |
| ``scorecard_threshold`` | ``0.15`` | Minimum gap (benchmark − score) required to emit a scorecard recommendation |

---

## 5. Failure Modes & Error Types

- **Validation errors** — ``DimensionScore``, ``Recommendation``, and
  ``RecommendationEngine`` validate inputs in ``__post_init__`` and raise
  ``ValueError`` on malformed data (empty ids, negative priorities,
  out-of-range confidence, etc.).
- **Determinism** — The engine contains no randomness, no I/O, and no
  time-based logic.  Identical inputs always produce identical outputs.
- **Empty signals** — Calling ``recommend()`` with no arguments returns an
  empty list.

---

## 6. Testing

| Test file | What it covers |
|---|---|
| ``tests/unit/recommendations/test_recommendations.py`` | Hermetic unit tests for all three signal planes, validation, cross-signal integration, determinism, sorting, and ``to_dict`` roundtrips.  Includes two hypothesis property tests: scorecard-gap monotonicity and burn-rate crash-freedom. |

---

## 7. Dependencies

### Internal imports

| Module | What is used |
|---|---|
| ``pitwall.finops.burn_rate`` | ``BurnRateForecast`` |
| ``pitwall.finops.reservations`` | ``ReservationRecommendation`` |

### External libraries

| Library | Used by | Purpose |
|---|---|---|
| ``hypothesis`` | tests | Property-based determinism and monotonicity tests |
