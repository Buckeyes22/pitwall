# Retained Backlog Review Fixes Implementation Plan

**Status:** complete; merged as `e6fbffb` (merge of `review-fixes/integration`). Checkboxes were not maintained during execution; the commit history and tests are the record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every finding in the 2026-09-02 independent review so the PR #30 and PR #32 work is deterministic, correct on real providers, and documented before release.

**Architecture:** Fix the test-isolation flake first so every later task is verifiable. Then repair the cost persistence contract at the producer and with a normalizing migration, fix the four volume-file service defects at the S3 boundary and the REST error boundary, harden the four Textual screens with the current-task guard pattern already used by two of them, and finish with documentation and repository hygiene. Every task is a small, independently testable change with a red-green cycle.

**Tech Stack:** Python 3.14, FastAPI, pydantic v2, asyncpg, Textual, pytest with anyio and pytest-randomly, uv, PostgreSQL migrations under `db/migrations/`.

**Spec:** `docs/evidence/2026-09-02-retained-backlog-independent-review.md`

## Global Constraints

- Run every command through `uv run --frozen`; never `pip install`.
- Tests are hermetic: no provider egress, no real credentials, no paid calls. `tests/conftest.py` fails on DNS.
- Error surfaces must not reflect provider or credential detail. Existing tests assert that a "credential canary" string never appears in rendered text. Keep that property in every message you change.
- Migrations are append-only. The next file is `db/migrations/0032_*.sql`. Every migration gets a contract test under `tests/db/`.
- Decimal is authoritative for money. Never introduce `float` in cost code.
- Commit after every task with a DCO sign-off: `git commit -s`.
- Do not change `.github/workflows/` except where a task says so.
- Do not run `/code-review` at `high` effort or any multi-agent workflow during this plan; verify with the commands in each task.

## File Map

| File | Responsibility in this plan |
| --- | --- |
| `tests/test_staging_store.py` | Task 1: restore `boto3`/`botocore` module state after the import-isolation test |
| `src/pitwall/routing/production.py` | Task 2: emit the canonical `CostQuote` shape from `RouteBudgetQuote` |
| `db/migrations/0032_normalize_route_plan_cost_quotes.sql` | Task 3: rewrite already-persisted route-plan quotes into the canonical shape |
| `src/pitwall/cost/budget_gate.py`, `src/pitwall/audit/capability.py`, `src/pitwall/tui/operations.py` | Task 4: one shared month-to-date spend expression |
| `src/pitwall/runpod_files.py` | Tasks 5, 8, 10, 11: object lookup, upload inspection limits, hard-link fallback, log parsing |
| `src/pitwall/runpod_client/mounts.py` | Task 6: map S3 416 and 404 to typed outcomes |
| `src/pitwall/api/routes/volume_files.py`, `src/pitwall/api/app.py` | Task 7: app-level `VolumeFileError` handler |
| `src/pitwall/tui/volume_files.py`, `src/pitwall/tui/onboarding.py` | Tasks 9, 15: current-task guards |
| `src/pitwall/tui/providers.py`, `src/pitwall/tui/app.py` | Tasks 12, 13, 14: armed column, hotkey, failure text |
| `src/pitwall/tui/resources.py` | Tasks 16, 17: failure type names, stale-since label |
| `src/pitwall/tui/routing_jobs.py` | Task 18: unknown capability maps to `route_not_found` |
| `src/pitwall/tui/operations.py` | Tasks 19, 20, 21: preview input retention, `limit` label, capacity label |
| `src/pitwall/tui/leases.py` | Task 22: pod id remains visible and searchable |
| `src/pitwall/mcp/tools/cost.py` | Task 23: explicit `limit` validation |
| `src/pitwall/models/prices.py` | Task 24: cache the market-backed snapshot |
| `CHANGELOG.md`, `docs/` | Tasks 25, 26: release notes and behaviour-change documentation |
| local worktrees and branches | Task 27: cleanup |

---

## Phase A: Deterministic test suite

### Task 1: Restore boto module state in the staging-store import test

**Files:**
- Modify: `tests/test_staging_store.py:109-137`

**Interfaces:**
- Consumes: nothing.
- Produces: an order-independent hermetic suite. Later tasks assume `uv run --frozen pytest -m "not integration and not slow and not live"` is green regardless of seed.

- [ ] **Step 1: Reproduce the failure deterministically**

Run:

```bash
uv run --frozen pytest -p no:randomly -q \
  tests/test_staging_store.py \
  "tests/runpod_client/test_mounts.py::test_s3_get_object"
```

Expected: `1 failed, 6 passed` with `AttributeError: module 'botocore' has no attribute 'exceptions'`.

- [ ] **Step 2: Rewrite the test to snapshot and restore every boto module**

Replace the body of `test_r2_staging_cleanup_import_does_not_import_boto3` with:

```python
def test_r2_staging_cleanup_import_does_not_import_boto3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boto_prefixes = ("boto3", "botocore")
    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if name.split(".", 1)[0] in boto_prefixes
        or name in {"pitwall.api.admin.r2_staging_cleanup", "pitwall.r2_staging_cleanup"}
    }
    for name in saved_modules:
        sys.modules.pop(name, None)

    real_import = builtins.__import__

    def blocked_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name.split(".", 1)[0] in boto_prefixes:
            raise ModuleNotFoundError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    try:
        module = importlib.import_module("pitwall.api.admin.r2_staging_cleanup")
        assert module.DEFAULT_R2_DEBUG_LOG_PREFIX == "debug-logs/"
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
        for name in [n for n in sys.modules if n.split(".", 1)[0] in boto_prefixes]:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)

    # Self-check: a later test must be able to import boto3 normally.
    reloaded = importlib.import_module("boto3")
    assert hasattr(importlib.import_module("botocore"), "exceptions")
    assert reloaded.__name__ == "boto3"
```

- [ ] **Step 3: Verify the deterministic reproduction now passes**

Run the Step 1 command. Expected: `7 passed`.

- [ ] **Step 4: Run the suite under three seeds**

```bash
for seed in 1 12345 987654; do
  uv run --frozen pytest -q -m "not integration and not slow and not live" \
    -p randomly --randomly-seed=$seed | tail -1
done
```

Expected: each run ends with `0 failed` (the summary line shows only `passed`, `skipped`, `deselected`).

- [ ] **Step 5: Commit**

```bash
git add tests/test_staging_store.py
git commit -s -m "test: restore boto module state after r2 staging import isolation"
```

---

## Phase B: Cost persistence contract

### Task 2: Emit the canonical CostQuote shape from RouteBudgetQuote

**Files:**
- Modify: `src/pitwall/routing/production.py:355-389`
- Test: `tests/routing/test_production_routing.py`
- Test: `tests/cost/test_cost_read_models.py`

**Interfaces:**
- Consumes: `CostComponent` from `pitwall.cost.estimator` (fields `name, unit, rate, ceiling_rate, estimated_count, ceiling_count, estimate, ceiling`, all `Decimal`; `model_dump(mode="json")` returns the persisted component dict with key `count` for `estimated_count`).
- Consumes: `WorkloadCostRead.from_persisted(...)` from `pitwall.cost.read_models`, which requires exactly the keys `model, components, estimate, ceiling, confidence, provenance, currency, assumptions`.
- Produces: `RouteBudgetQuote.to_serializable_dict()` that round-trips through `WorkloadCostRead.from_persisted`. Task 3's migration writes the same shape.

- [ ] **Step 1: Write the failing round-trip test**

Append to `tests/routing/test_production_routing.py`:

```python
def test_route_budget_quote_round_trips_through_persisted_cost_read() -> None:
    from pitwall.cost.read_models import WorkloadCostRead
    from pitwall.routing.production import RouteBudgetQuote

    plan = _plan(
        [
            _provider("prov-a", ProviderAdapterId.RUNPOD, priority=1, latency_ms=100),
            _provider("prov-b", ProviderAdapterId.RUNPOD, priority=2, latency_ms=200),
        ]
    )
    quote = RouteBudgetQuote(plan, fallback_spend=True)

    serialized = quote.to_serializable_dict()

    assert set(serialized) == {
        "model",
        "components",
        "estimate",
        "ceiling",
        "confidence",
        "provenance",
        "currency",
        "assumptions",
    }
    read = WorkloadCostRead.from_persisted(
        cost_estimate_usd=quote.estimate(),
        cost_ceiling_usd=quote.upper_bound(),
        cost_quote=serialized,
        cost_actual_usd=None,
    )
    assert read.estimate == quote.estimate()
    assert read.ceiling == quote.upper_bound()
    assert [component.name for component in read.components] == [
        "provider_attempt_1",
        "provider_attempt_2",
    ]
    assert any("plan_" in item for item in read.assumptions)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --frozen pytest tests/routing/test_production_routing.py::test_route_budget_quote_round_trips_through_persisted_cost_read -q`

Expected: FAIL with `ValueError: cost_quote must contain exactly the structured quote fields; missing=[], unknown=['plan_id']`.

- [ ] **Step 3: Replace `to_serializable_dict` in `RouteBudgetQuote`**

In `src/pitwall/routing/production.py`, add `CostComponent` to the existing import from `pitwall.cost.estimator` (grep the file for `from pitwall.cost.estimator import` and extend it), then replace the method:

```python
    def to_serializable_dict(self) -> dict[str, object]:
        """Return the one persisted quote shape read back by WorkloadCostRead.

        The plan id is persisted separately in ``workloads.route_plan_id``
        (migration 0031); it appears here only as a human-readable assumption.
        """
        components = [
            CostComponent(
                name=f"provider_attempt_{candidate.rank}",
                unit="attempt",
                rate=candidate.quote.estimate(),
                ceiling_rate=candidate.quote.upper_bound(),
                estimated_count=Decimal("1"),
                ceiling_count=Decimal("1"),
                estimate=candidate.quote.estimate(),
                ceiling=candidate.quote.upper_bound(),
            ).model_dump(mode="json")
            for candidate in self.plan.attempts
        ]
        spend_assumption = (
            "all synchronous fallback attempts may incur their provider ceiling"
            if self.fallback_spend
            else "asynchronous submission stops after the first ambiguous provider response"
        )
        return {
            "model": "route_plan",
            "components": components,
            "estimate": _decimal_text(self.estimate()),
            "ceiling": _decimal_text(self.upper_bound()),
            "confidence": "bounded",
            "provenance": "production_route_plan",
            "currency": "USD",
            "assumptions": [spend_assumption, f"route plan {self.plan.plan_id}"],
        }
```

Note: `CostComponent.__post_init__` requires six-decimal USD quantum for `estimate` and `ceiling`. Provider quotes already satisfy that; if the test fails with "component amounts must use the six-decimal USD quantum", wrap the four money values in `_usd(...)` imported from `pitwall.cost.estimator`.

- [ ] **Step 4: Run the new test and the existing routing and cost suites**

Run: `uv run --frozen pytest tests/routing/test_production_routing.py tests/cost/test_cost_read_models.py tests/cost/test_budget_gate.py tests/api/test_jobs*.py -q`

Expected: all pass. If a routing test asserted the old `plan_id` key inside `to_serializable_dict()`, update it to assert the assumption string instead; nothing in `src/` reads that key.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/routing/production.py tests/routing/test_production_routing.py
git commit -s -m "fix(cost): persist route plan quotes in the canonical CostQuote shape"
```

### Task 3: Normalize already-persisted route-plan quotes

**Files:**
- Create: `db/migrations/0032_normalize_route_plan_cost_quotes.sql`
- Create: `tests/db/test_normalize_route_plan_cost_quotes_migration.py`

**Interfaces:**
- Consumes: the shape defined in Task 2.
- Produces: no persisted `cost_quote` with `model = 'route_plan'` carries a `plan_id` key or non-canonical components.

- [ ] **Step 1: Write the migration contract test**

Create `tests/db/test_normalize_route_plan_cost_quotes_migration.py`:

```python
"""Route-plan quote normalization is the next append-only migration."""

from __future__ import annotations

from pathlib import Path

from pitwall.migrations import discover_migrations

_ROOT = Path(__file__).parents[2]
_MIGRATION = _ROOT / "db/migrations/0032_normalize_route_plan_cost_quotes.sql"


def test_normalize_migration_is_next_append_only_record() -> None:
    records = discover_migrations(_ROOT / "db/migrations")

    assert records[-1].filename == _MIGRATION.name
    assert records[-1].version == "0032_normalize_route_plan_cost_quotes"


def test_normalize_migration_rewrites_only_route_plan_quotes() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")

    assert "cost_quote ->> 'model' = 'route_plan'" in sql
    assert "cost_quote ? 'plan_id'" in sql
    assert "cost_quote - 'plan_id'" in sql
    assert "'unit', 'attempt'" in sql
    assert "'ceiling_count', '1'" in sql
    assert "jsonb_array_elements(cost_quote -> 'components')" in sql
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --frozen pytest tests/db/test_normalize_route_plan_cost_quotes_migration.py -q`

Expected: FAIL because the file does not exist.

- [ ] **Step 3: Write the migration**

Create `db/migrations/0032_normalize_route_plan_cost_quotes.sql`:

```sql
-- COST-01 follow-up: route-plan admission quotes persisted before the
-- producer fix carried a top-level plan_id and attempt components without
-- the canonical unit/rate/count fields. WorkloadCostRead rejects that shape.
-- Rewrite those rows into the canonical CostQuote shape. The plan id is
-- already persisted in workloads.route_plan_id (migration 0031).

UPDATE pitwall.workloads
SET cost_quote = (cost_quote - 'plan_id')
  || jsonb_build_object(
       'components',
       COALESCE(
         (
           SELECT jsonb_agg(
                    jsonb_build_object(
                      'name', component ->> 'name',
                      'unit', 'attempt',
                      'rate', component ->> 'estimate',
                      'ceiling_rate', component ->> 'ceiling',
                      'count', '1',
                      'ceiling_count', '1',
                      'estimate', component ->> 'estimate',
                      'ceiling', component ->> 'ceiling'
                    )
                  )
           FROM jsonb_array_elements(cost_quote -> 'components') AS component
         ),
         '[]'::jsonb
       ),
       'assumptions',
       COALESCE(cost_quote -> 'assumptions', '[]'::jsonb)
         || to_jsonb(ARRAY['route plan ' || (cost_quote ->> 'plan_id')])
     )
WHERE cost_quote IS NOT NULL
  AND cost_quote ->> 'model' = 'route_plan'
  AND cost_quote ? 'plan_id';
```

- [ ] **Step 4: Run the contract test and, if the integration stack is up, the migration runner**

Run: `uv run --frozen pytest tests/db/test_normalize_route_plan_cost_quotes_migration.py -q`
Expected: PASS.

If `make up` is available (PostgreSQL on port 5444), also run:

```bash
make up
PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test \
  uv run --frozen pytest -m integration tests/db -q -p no:randomly
```

Expected: PASS, including whichever test applies all migrations end to end.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0032_normalize_route_plan_cost_quotes.sql tests/db/test_normalize_route_plan_cost_quotes_migration.py
git commit -s -m "feat(db): normalize persisted route-plan cost quotes"
```

### Task 4: One month-to-date spend expression for gate, audit, and TUI

**Files:**
- Modify: `src/pitwall/cost/budget_gate.py:143-149` and `:183-186`
- Modify: `src/pitwall/audit/capability.py:576-590`
- Modify: `src/pitwall/tui/operations.py:774-783`
- Test: `tests/cost/test_budget_gate.py`
- Test: `tests/audit/` (locate the file that tests `read_month_to_date_spend_usd`; if none, create `tests/audit/test_month_to_date_spend.py`)

**Interfaces:**
- Produces: `MONTH_TO_DATE_SPEND_SQL: str` exported from `pitwall.cost.budget_gate`, the single SELECT used everywhere month-to-date spend is summed.

- [ ] **Step 1: Write the failing test**

Add to `tests/cost/test_budget_gate.py`:

```python
def test_month_to_date_spend_sql_is_shared_with_audit() -> None:
    from pitwall.audit import capability
    from pitwall.cost.budget_gate import MONTH_TO_DATE_SPEND_SQL

    assert (
        "COALESCE(cost_actual_usd, cost_ceiling_usd, cost_estimate_usd)" in MONTH_TO_DATE_SPEND_SQL
    )
    assert capability.MONTH_TO_DATE_SPEND_SQL is MONTH_TO_DATE_SPEND_SQL
```

And a behavioural test with a recording pool:

```python
async def test_audit_month_to_date_spend_uses_ceiling_when_actual_missing() -> None:
    from decimal import Decimal

    from pitwall.audit.capability import read_month_to_date_spend_usd
    from pitwall.cost.budget_gate import MONTH_TO_DATE_SPEND_SQL

    class _Conn:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def fetchrow(self, query: str, *args: object) -> dict[str, Decimal]:
            self.queries.append(query)
            return {"s": Decimal("12.5")}

    class _Acquire:
        def __init__(self, conn: _Conn) -> None:
            self._conn = conn

        async def __aenter__(self) -> _Conn:
            return self._conn

        async def __aexit__(self, *exc: object) -> None:
            return None

    class _Pool:
        def __init__(self) -> None:
            self.conn = _Conn()

        def acquire(self) -> _Acquire:
            return _Acquire(self.conn)

    pool = _Pool()
    spend = await read_month_to_date_spend_usd(pool)

    assert spend == Decimal("12.5")
    assert pool.conn.queries == [MONTH_TO_DATE_SPEND_SQL]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/cost/test_budget_gate.py -q -k month_to_date`
Expected: FAIL with `ImportError: cannot import name 'MONTH_TO_DATE_SPEND_SQL'`.

- [ ] **Step 3: Add the constant and use it in the gate**

In `src/pitwall/cost/budget_gate.py`, near the other module constants add:

```python
MONTH_TO_DATE_SPEND_SQL = (
    "SELECT COALESCE(SUM(COALESCE(cost_actual_usd, cost_ceiling_usd, cost_estimate_usd)), 0) AS s"
    " FROM pitwall.workloads"
    " WHERE submitted_at >= date_trunc('month', now() AT TIME ZONE 'UTC')"
)
```

Replace both inline query strings at lines 147 and 184 with `MONTH_TO_DATE_SPEND_SQL`. Add the name to `__all__` if the module defines one.

- [ ] **Step 4: Use it in the audit and the TUI**

In `src/pitwall/audit/capability.py` replace the body of `read_month_to_date_spend_usd` with:

```python
    async with pool.acquire() as conn:
        row = await conn.fetchrow(MONTH_TO_DATE_SPEND_SQL)
    return _required_decimal(_row_get(row, "s", Decimal("0")), "month_to_date_spend_usd")
```

and add `from pitwall.cost.budget_gate import MONTH_TO_DATE_SPEND_SQL` to the imports. Update the docstring to say the query is the gate's own expression.

In `src/pitwall/tui/operations.py:778` change `COALESCE(cost_actual_usd, cost_estimate_usd) AS cost_usd` to `COALESCE(cost_actual_usd, cost_ceiling_usd, cost_estimate_usd) AS cost_usd`.

- [ ] **Step 5: Run the tests**

Run: `uv run --frozen pytest tests/cost/test_budget_gate.py tests/audit tests/tui/test_operations_screen.py -q`
Expected: PASS. If an audit test asserted the old `SUM(cost_estimate_usd)` text, update it to the constant.

- [ ] **Step 6: Commit**

```bash
git add src/pitwall/cost/budget_gate.py src/pitwall/audit/capability.py src/pitwall/tui/operations.py tests/cost/test_budget_gate.py
git commit -s -m "fix(cost): share one month-to-date spend expression across gate, audit, and TUI"
```

---

## Phase C: Volume-file service

### Task 5: Missing-key lookup returns None instead of a provider error

**Files:**
- Modify: `src/pitwall/runpod_files.py:1489-1509`
- Test: `tests/test_runpod_files.py`

**Interfaces:**
- Consumes: `FakeStore.list_objects`, which sorts keys and returns `VolumeObjectPage(objects, truncated)` exactly like the real store.
- Produces: `_find_object` returns the exact entry or `None`; it never raises for a missing key.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_runpod_files.py`:

```python
async def test_download_of_missing_key_with_many_prefixed_siblings_is_not_found(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    store.objects = {
        "ckpt/step1": b"a",
        "ckpt/step2": b"b",
        "ckpt/step3": b"c",
    }
    service, _, _ = _service(store)

    with pytest.raises(VolumeFileNotFound):
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="ckpt/step",
            local_path="step.bin",
            root=tmp_path,
        )
```

Use the same keyword names `download_to_path` already takes in neighbouring tests; adjust `local_path` and `root` if the existing tests spell them differently.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/test_runpod_files.py -q -k many_prefixed_siblings`
Expected: FAIL with `VolumeFileProviderError` raised instead of `VolumeFileNotFound`.

- [ ] **Step 3: Fix `_find_object`**

S3 lists keys in binary order, and a key is always the first entry among keys that share it as a prefix. Replace the method:

```python
    async def _find_object(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
    ) -> VolumeObject | None:
        page = await self._provider_call(
            "object lookup",
            lambda: self._object_store.list_objects(
                volume_id,
                data_center_id,
                prefix=key,
                max_items=1,
            ),
        )
        # The exact key sorts first among every key that starts with it, so a
        # single-item prefix listing is a complete existence check.
        first = page.objects[0] if page.objects else None
        if first is not None and first.key == key:
            return first
        return None
```

- [ ] **Step 4: Run the volume-file suites**

Run: `uv run --frozen pytest tests/test_runpod_files.py tests/api/test_volume_file_routes.py -q`
Expected: PASS. If a test asserted the `("list", ..., 2)` call record, change the expected `max_items` to `1`.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/runpod_files.py tests/test_runpod_files.py
git commit -s -m "fix(volume-files): exact-key lookup never reports a missing key as a provider error"
```

### Task 6: Map S3 416 and 404 on range reads to empty chunk and not-found

**Files:**
- Modify: `src/pitwall/runpod_client/mounts.py:455-497` and the error classes near `:131`
- Modify: `src/pitwall/runpod_files.py:1588-1601` (`_provider_call`)
- Test: `tests/runpod_client/test_mounts.py`
- Test: `tests/test_runpod_files.py` (`FakeStore.get_object_range`)

**Interfaces:**
- Produces: `S3ObjectNotFound(RunPodError)` in `pitwall.runpod_client.mounts`.
- Produces: `NetworkVolumeClient.get_object_range` returns `b""` for a satisfiable-but-empty range (HTTP 416) and raises `S3ObjectNotFound` for a missing key.
- Produces: `VolumeFileService._provider_call` re-raises `S3ObjectNotFound` as `VolumeFileNotFound`.

- [ ] **Step 1: Write the failing client tests**

Add to `tests/runpod_client/test_mounts.py`, following the file's existing fake-boto pattern (look at `test_s3_get_object` for how the fake client is installed):

```python
def _client_error(code: str, status: int) -> Exception:
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "GetObject",
    )


async def test_s3_range_read_past_end_returns_empty_bytes(fake_boto_client) -> None:
    fake_boto_client.get_object_error = _client_error("InvalidRange", 416)
    client = _make_client(fake_boto_client)  # reuse the helper the file already uses

    data = await client.get_object_range("vol", "US-KS-2", "empty.txt", offset=0, max_bytes=16)

    assert data == b""


async def test_s3_range_read_missing_key_raises_not_found(fake_boto_client) -> None:
    from pitwall.runpod_client.mounts import S3ObjectNotFound

    fake_boto_client.get_object_error = _client_error("NoSuchKey", 404)
    client = _make_client(fake_boto_client)

    with pytest.raises(S3ObjectNotFound):
        await client.get_object_range("vol", "US-KS-2", "missing", offset=0, max_bytes=16)
```

If the existing fake client has no `get_object_error` hook, add one attribute that, when set, is raised from its `get_object`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/runpod_client/test_mounts.py -q -k "past_end or missing_key"`
Expected: FAIL (`ImportError` for `S3ObjectNotFound`, or the raw `ClientError` propagating).

- [ ] **Step 3: Add the error class and the mapping in `mounts.py`**

Below `S3ObjectPreconditionFailed` add:

```python
class S3ObjectNotFound(RunPodError):
    """The requested S3 object key does not exist."""
```

Inside `get_object_range._get_range`, wrap the `client.get_object(...)` call:

```python
            try:
                response = client.get_object(
                    Bucket=volume_id,
                    Key=key,
                    Range=f"bytes={offset}-{offset + max_bytes - 1}",
                )
            except Exception as exc:  # reason: optional botocore errors need lazy type inspection
                from botocore.exceptions import ClientError

                if isinstance(exc, ClientError):
                    metadata = exc.response.get("ResponseMetadata", {})
                    status = metadata.get("HTTPStatusCode")
                    code = exc.response.get("Error", {}).get("Code")
                    if status == 416 or code in {"InvalidRange", "416"}:
                        return b""
                    if status == 404 or code in {"NoSuchKey", "404"}:
                        raise S3ObjectNotFound("S3 object does not exist") from exc
                raise
```

Add `S3ObjectNotFound` to the module's `__all__`.

- [ ] **Step 4: Map it in the service boundary**

In `src/pitwall/runpod_files.py`, import `S3ObjectNotFound` alongside the existing `NetworkVolumeClient` import from `pitwall.runpod_client.mounts`, and add to `_provider_call` before the generic `except Exception`:

```python
        except S3ObjectNotFound as exc:
            raise VolumeFileNotFound("object does not exist") from exc
```

- [ ] **Step 5: Make the test fake mirror real S3**

In `tests/test_runpod_files.py` change `FakeStore.get_object_range` so it raises for a missing key and returns empty bytes past the end:

```python
        if key not in self.objects:
            from pitwall.runpod_client.mounts import S3ObjectNotFound

            raise S3ObjectNotFound("missing")
        value = self.objects[key]
        if self.short_read:
            return b""
        if offset >= len(value):
            return b""
        return value[offset : offset + max_bytes]
```

Then add a service-level test:

```python
async def test_read_chunk_of_missing_key_is_not_found() -> None:
    service, _, _ = _service()

    with pytest.raises(VolumeFileNotFound):
        await service.read_object_chunk(
            volume_id="vol-1", data_center_id="US-KS-2", object_key="absent.bin"
        )


async def test_read_chunk_past_end_is_an_empty_completed_chunk() -> None:
    store = FakeStore()
    store.objects = {"tiny.bin": b"abc"}
    service, _, _ = _service(store)

    result = await service.read_object_chunk(
        volume_id="vol-1", data_center_id="US-KS-2", object_key="tiny.bin", offset=3
    )

    assert result.status == "completed"
    assert result.bytes_transferred == 0
```

- [ ] **Step 6: Run all three suites**

Run: `uv run --frozen pytest tests/runpod_client/test_mounts.py tests/test_runpod_files.py tests/api/test_volume_file_routes.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pitwall/runpod_client/mounts.py src/pitwall/runpod_files.py tests/runpod_client/test_mounts.py tests/test_runpod_files.py
git commit -s -m "fix(volume-files): map S3 416 and 404 on range reads to empty chunk and not-found"
```

### Task 7: REST returns the documented 503 when S3 credentials are not configured

**Files:**
- Modify: `src/pitwall/api/routes/volume_files.py`
- Modify: `src/pitwall/api/app.py:545-553`
- Test: `tests/api/test_volume_file_routes.py`

**Interfaces:**
- Produces: `install_volume_file_error_handler(app: FastAPI) -> None` in `pitwall.api.routes.volume_files`, registering a `VolumeFileError` handler that returns `exc.status_code` and `exc.to_response_body()`.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_volume_file_routes.py`:

```python
async def test_unconfigured_service_returns_documented_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.api.routes.volume_files import install_volume_file_error_handler

    app = FastAPI()
    app.state.pool = object()  # a pool exists, but S3 credentials do not
    for name in ("RUNPOD_S3_ACCESS_KEY", "RUNPOD_S3_SECRET_KEY", "RUNPOD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    app.include_router(volume_file_router)
    install_volume_file_error_handler(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/v1/volumes/vol-1/objects", params={"data_center_id": "US-KS-2"}
        )

    assert response.status_code == 503
    assert response.json() == {"error": "volume_file_not_configured"}
```

If the env var names differ, read `S3CredentialReferences` in `src/pitwall/runpod_files.py` and use its defaults.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/api/test_volume_file_routes.py -q -k unconfigured`
Expected: FAIL with `ImportError` for `install_volume_file_error_handler`.

- [ ] **Step 3: Add the installer and register it in the app**

In `src/pitwall/api/routes/volume_files.py` after `volume_file_router` is defined:

```python
def install_volume_file_error_handler(app: FastAPI) -> None:
    """Serialize VolumeFileError raised outside a route body (dependencies)."""

    async def _handler(_request: Request, exc: VolumeFileError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())

    app.add_exception_handler(VolumeFileError, _handler)  # type: ignore[arg-type]
```

Add `from fastapi import FastAPI` to the imports if missing. In `src/pitwall/api/app.py`, next to the other `@app.exception_handler` registrations, add:

```python
install_volume_file_error_handler(app)
```

with the corresponding import from `pitwall.api.routes.volume_files`.

- [ ] **Step 4: Run the route tests and the OpenAPI check**

Run: `uv run --frozen pytest tests/api/test_volume_file_routes.py tests/api/test_openapi_contract.py -q && make openapi-check`
Expected: PASS; the OpenAPI baseline is unchanged because handlers do not alter the schema.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/api/routes/volume_files.py src/pitwall/api/app.py tests/api/test_volume_file_routes.py
git commit -s -m "fix(api): return the documented 503 when volume-file credentials are not configured"
```

### Task 8: Upload inspection limits follow the transfer limit

**Files:**
- Modify: `src/pitwall/runpod_files.py:700-720` (constructor) and `:795-825` (`upload_bytes`)
- Test: `tests/test_runpod_files.py`

**Interfaces:**
- Consumes: `PreSpendInspectionService(mode=..., limits=PreSpendInspectionLimits(...))` and `get_settings().pitwall_pre_spend_mode`.
- Produces: `VolumeFileService` builds its default inspection service with `max_input_bytes = limits.max_transfer_bytes + 65536` and `timeout_ms = 5000`, and runs upload inspection in a worker thread. Callers that inject `inspection_service` keep full control and the shared guardrail counters.

Decision recorded here: uploads inspected by the default service are no longer counted in the process-wide guardrail status counters. That status describes request-path guardrails; volume uploads have their own audit journal. If the maintainer prefers the shared counters, the alternative is a per-call `limits` parameter on `PreSpendInspectionService.inspect`, which is a larger change.

- [ ] **Step 1: Write the failing test**

```python
async def test_upload_of_text_between_inspection_and_transfer_limits_succeeds() -> None:
    service, store, _ = _service()
    body = ("line of ordinary text\n" * 20000).encode("utf-8")  # about 440 KiB
    assert 262_144 < len(body) < 4 * 1024 * 1024

    result = await service.upload_bytes(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="notes.txt",
        body=body,
        idempotency_key="big-text-1",
    )

    assert result.status == "completed"
    assert store.objects["notes.txt"] == body
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/test_runpod_files.py -q -k between_inspection_and_transfer`
Expected: FAIL with `VolumeFilePreSpendRejected`.

- [ ] **Step 3: Build a sized default inspection service**

In `VolumeFileService.__init__` replace
`self._inspection_service = inspection_service or get_pre_spend_inspection_service()` with:

```python
self._inspection_service = inspection_service or _default_upload_inspection_service(self._limits)
```

and add the module-level helper (import `PreSpendInspectionLimits` and `PreSpendPolicyMode` from `pitwall.security.pre_spend`):

```python
_UPLOAD_INSPECTION_METADATA_BYTES = 65_536
_UPLOAD_INSPECTION_TIMEOUT_MS = 5_000


def _default_upload_inspection_service(limits: VolumeFileLimits) -> PreSpendInspectionService:
    """Size the default inspector so every transfer within limits can be scanned."""
    from pitwall.config import get_settings

    return PreSpendInspectionService(
        mode=PreSpendPolicyMode(get_settings().pitwall_pre_spend_mode),
        limits=PreSpendInspectionLimits(
            max_input_bytes=limits.max_transfer_bytes + _UPLOAD_INSPECTION_METADATA_BYTES,
            timeout_ms=_UPLOAD_INSPECTION_TIMEOUT_MS,
        ),
    )
```

- [ ] **Step 4: Run inspection off the event loop for uploads**

In `upload_bytes`, change the `self._inspect_request({...}, dry_run=dry_run)` call to:

```python
        await asyncio.to_thread(
            self._inspect_request,
            {
                "volume_id": volume_id,
                "data_center_id": data_center_id,
                "object_key": key,
                "content": inspected_content,
                "overwrite": overwrite,
                "expected_sha256": expected,
                "idempotency_key": idempotency_key,
            },
            dry_run=dry_run,
        )
```

`asyncio.to_thread` accepts keyword arguments and forwards them.

- [ ] **Step 5: Run the suite**

Run: `uv run --frozen pytest tests/test_runpod_files.py tests/test_runpod_files_no_egress.py tests/mcp/test_guardrail_tools.py -q`
Expected: PASS. `test_upload_guardrail_rejects_sensitive_or_opaque_content_before_provider` still passes because it injects its own `PreSpendInspectionService()`.

- [ ] **Step 6: Commit**

```bash
git add src/pitwall/runpod_files.py tests/test_runpod_files.py
git commit -s -m "fix(volume-files): size upload inspection to the transfer limit and scan off the loop"
```

### Task 9: Second volume-file action keeps its own cancellation

**Files:**
- Modify: `src/pitwall/tui/volume_files.py:623-652`
- Test: `tests/tui/test_volume_files_panel.py`

**Interfaces:**
- Produces: `_show_cancelled` and `_finish` return early unless called from the currently active task, mirroring `onboarding.py:313`.

- [ ] **Step 1: Write the failing test**

Add to `tests/tui/test_volume_files_panel.py`:

```python
async def test_starting_a_second_action_keeps_its_cancel_button_and_message() -> None:
    class SlowSource(StaticVolumeFilesOperationsSource):
        async def list_objects(self, **kwargs: object) -> VolumeFileResult:
            del kwargs
            await asyncio.sleep(60)
            return _listing()

        async def read_pod_logs(self, **kwargs: object) -> VolumeFileResult:
            del kwargs
            await asyncio.sleep(60)
            return _logs()

    panel = VolumeFilesOperationsPanel(SlowSource())
    app = PanelApp(panel)
    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-pod-id", Input).value = "pod-1"
        panel.query_one("#volume-files-list", Button).press()
        await pilot.pause()
        panel.query_one("#volume-files-logs", Button).press()
        await pilot.pause()
        await pilot.pause()

        cancel = panel.query_one("#volume-files-cancel", Button)
        result = str(panel.query_one("#volume-files-result", Static).content)
        assert cancel.disabled is False
        assert "Operation cancelled" not in result

        cancel.press()
        await pilot.pause()
        assert "Operation cancelled" in str(panel.query_one("#volume-files-result", Static).content)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_volume_files_panel.py -q -k second_action`
Expected: FAIL on `cancel.disabled is False` or on the cancelled text appearing early.

- [ ] **Step 3: Add the guard**

In `src/pitwall/tui/volume_files.py`:

```python
def _is_current_operation(self) -> bool:
    return self._active_task is asyncio.current_task()


def _show_cancelled(self) -> None:
    if not self._is_current_operation():
        return
    message = "Operation cancelled before a result was returned."
    if self._active_mutation:
        message = (
            "Mutation cancellation is ambiguous: the write may have completed. "
            "Inspect provider/local state and durable audit before retrying the retained "
            "preview, which preserves its original idempotency key where supported."
        )
    self.query_one("#volume-files-result", Static).update(message)
    self._finish()


def _finish(self) -> None:
    if not self._is_current_operation():
        return
    self._active_task = None
    self._cancel_event = None
    self._active_mutation = False
    self.query_one("#volume-files-cancel", Button).disabled = True
```

Apply the same early return at the top of `_show_safe_error`.

- [ ] **Step 4: Run the panel suite**

Run: `uv run --frozen pytest tests/tui/test_volume_files_panel.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/tui/volume_files.py tests/tui/test_volume_files_panel.py
git commit -s -m "fix(tui): volume-file actions only finish their own task"
```

### Task 10: Non-overwrite download works without hard links

**Files:**
- Modify: `src/pitwall/runpod_files.py:1999-2016`
- Test: `tests/test_runpod_files.py`

**Interfaces:**
- Produces: `_publish_no_overwrite` falls back to a stat-then-replace when `os.link` is unsupported, and treats a failed temp unlink after a successful publish as a logged warning rather than an error.

- [ ] **Step 1: Write the failing tests**

```python
async def test_download_publishes_without_hard_link_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import os

    def no_link(*args: object, **kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "linkat not permitted")

    monkeypatch.setattr(os, "link", no_link)
    store = FakeStore()
    store.objects = {"model.bin": b"weights"}
    service, _, _ = _service(store)

    result = await service.download_to_path(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="model.bin",
        local_path="model.bin",
        root=tmp_path,
    )

    assert result.status == "completed"
    assert (tmp_path / "model.bin").read_bytes() == b"weights"


async def test_download_without_hard_links_still_requires_overwrite_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import os

    monkeypatch.setattr(
        os, "link", lambda *a, **k: (_ for _ in ()).throw(PermissionError(errno.EPERM, "no"))
    )
    (tmp_path / "model.bin").write_bytes(b"old")
    store = FakeStore()
    store.objects = {"model.bin": b"weights"}
    service, _, _ = _service(store)

    with pytest.raises(VolumeFileConfirmationRequired):
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="model.bin",
            local_path="model.bin",
            root=tmp_path,
        )
    assert (tmp_path / "model.bin").read_bytes() == b"old"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/test_runpod_files.py -q -k hard_link`
Expected: the first test FAILS with `VolumeFileValidationError`.

- [ ] **Step 3: Implement the fallback**

Replace `_publish_no_overwrite`:

```python
def _publish_no_overwrite(temp_name: str, target: _SafeLocalTarget) -> None:
    try:
        os.link(
            temp_name,
            target.name,
            src_dir_fd=target.parent_fd,
            dst_dir_fd=target.parent_fd,
            follow_symlinks=False,
        )
    except FileExistsError as exc:
        raise VolumeFileConfirmationRequired(
            "destination overwrite requires explicit confirmation"
        ) from exc
    except OSError as exc:
        if exc.errno not in {
            errno.EPERM,
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
            errno.EXDEV,
            errno.EMLINK,
        }:
            raise VolumeFileValidationError("local download destination is unavailable") from exc
        # Filesystem without hard links: check-then-replace is the best
        # available no-overwrite publish. The window between the stat and the
        # replace is documented in docs/operator/runpod-volume-files.md.
        try:
            os.stat(target.name, dir_fd=target.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            _replace_temp(temp_name, target)
            return
        except OSError as stat_exc:
            raise VolumeFileValidationError(
                "local download destination is unavailable"
            ) from stat_exc
        raise VolumeFileConfirmationRequired(
            "destination overwrite requires explicit confirmation"
        ) from exc
    try:
        os.unlink(temp_name, dir_fd=target.parent_fd)
    except OSError:
        log.warning("published download left a temporary file behind")
```

Add `import errno` at the top of the module. `_replace_temp` already exists directly above and removes the temp name as part of `os.replace`.

- [ ] **Step 4: Document the fallback**

In `docs/operator/runpod-volume-files.md`, under the download section, add one paragraph: on filesystems without hard-link support (exFAT, some CIFS and FUSE mounts) the non-overwrite publish checks for an existing file and then moves the temporary file into place; an existing file still requires overwrite confirmation, but a file created by another process between the check and the move is replaced.

- [ ] **Step 5: Run the suite and the docs check**

Run: `uv run --frozen pytest tests/test_runpod_files.py -q && make docs-check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pitwall/runpod_files.py tests/test_runpod_files.py docs/operator/runpod-volume-files.md
git commit -s -m "fix(volume-files): publish downloads on filesystems without hard links"
```

### Task 11: Log parsing keeps empty-but-present values

**Files:**
- Modify: `src/pitwall/runpod_files.py:2054-2090`
- Test: `tests/test_runpod_files.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_log_lines_keeps_blank_message_and_empty_logs_list() -> None:
    from pitwall.runpod_files import _parse_log_lines

    blank, _ = _parse_log_lines(
        '[{"timestamp": "2026-09-01T00:00:00Z", "message": ""}]', max_lines=10
    )
    assert blank[0].text == ""
    assert blank[0].timestamp == "2026-09-01T00:00:00Z"

    empty, truncated = _parse_log_lines('{"logs": []}', max_lines=10)
    assert empty == ()
    assert truncated is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/test_runpod_files.py -q -k blank_message`
Expected: FAIL; the blank line renders as JSON and the empty list yields one bogus line.

- [ ] **Step 3: Replace the `or` chains**

Add a helper near `_log_line`:

```python
_ABSENT = object()


def _first_present(mapping: Mapping[str, object], keys: tuple[str, ...]) -> object:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return _ABSENT
```

In `_parse_log_lines` replace `candidate = parsed.get("logs") or parsed.get("data")` with `candidate = _first_present(parsed, ("logs", "data"))` and keep the following `isinstance` branches unchanged (`_ABSENT` matches none of them, so it falls to the redacted-text branch as before).

In `_log_line` replace the two lookups:

```python
        timestamp_value = _first_present(item, ("timestamp", "time", "createdAt"))
        message_value = _first_present(item, ("message", "log", "text"))
        timestamp = (
            redact_text(str(timestamp_value))
            if timestamp_value is not _ABSENT and timestamp_value is not None
            else None
        )
        message = (
            str(message_value)
            if message_value is not _ABSENT and message_value is not None
            else json.dumps(item, sort_keys=True)
        )
```

- [ ] **Step 4: Run the suite**

Run: `uv run --frozen pytest tests/test_runpod_files.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/runpod_files.py tests/test_runpod_files.py
git commit -s -m "fix(volume-files): treat empty log values as present"
```

---

## Phase D: TUI screens

### Task 12: Providers screen derives the armed state from the descriptor

**Files:**
- Modify: `src/pitwall/tui/providers.py:456-492`
- Test: `tests/tui/test_providers_screen.py`

**Interfaces:**
- Consumes: `ProviderDescriptor.config: Mapping[str, Any]` (redacted, non-secret keys retained).
- Produces: `_armed_state(config: Mapping[str, object]) -> tuple[bool, str | None]` used by both `_provider_entry_from_record` and `_provider_entry_from_descriptor`.

- [ ] **Step 1: Write the failing test**

```python
def test_descriptor_entry_reads_armed_state_from_config() -> None:
    from dataclasses import replace

    from pitwall.tui.providers import _provider_entry_from_descriptor
    from tests.providers._provider_operations import descriptor

    armed = replace(
        descriptor(),
        config={
            "cost": {"kind": "per_second", "price_per_hour": "1.250000"},
            "active_pod_id": "pod-serve-1",
            "active_lease_id": "lease-1",
        },
    )

    entry = _provider_entry_from_descriptor(armed)

    assert entry.armed is True
    assert entry.active_pod_id == "pod-serve-1"
    assert _provider_entry_from_descriptor(descriptor()).armed is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_providers_screen.py -q -k armed_state_from_config`
Expected: FAIL on `entry.armed is True`.

- [ ] **Step 3: Extract the helper and use it in both mappers**

```python
def _armed_state(config: Mapping[str, object]) -> tuple[bool, str | None]:
    active_pod_value = config.get("active_pod_id")
    active_pod_id = active_pod_value.strip() if isinstance(active_pod_value, str) else ""
    armed = bool(active_pod_id) and bool(config.get("active_lease_id"))
    return armed, active_pod_id or None
```

In `_provider_entry_from_record` replace the three lines that compute `active_pod_value`, `active_pod_id`, and `armed` with `armed, active_pod_id = _armed_state(config)`. In `_provider_entry_from_descriptor` replace `armed=False, active_pod_id=None` with:

```python
    armed, active_pod_id = _armed_state(descriptor.config)
    return ProviderEntry(
        ...
        armed=armed,
        active_pod_id=active_pod_id,
        ...
    )
```

- [ ] **Step 4: Run the providers suite**

Run: `uv run --frozen pytest tests/tui/test_providers_screen.py tests/tui/test_no_business_logic_guard.py -q`
Expected: PASS. The no-business-logic guard permits presentation mapping; if it flags the helper, move `_armed_state` into `src/pitwall/providers/service.py` as a method on `ProviderDescriptor` named `armed_state()` and call that instead.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/tui/providers.py tests/tui/test_providers_screen.py
git commit -s -m "fix(tui): providers screen shows the persisted armed state again"
```

### Task 13: Move the availability hotkey off the global Operations key

**Files:**
- Modify: `src/pitwall/tui/providers.py:231-235`
- Test: `tests/tui/test_providers_screen.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_operations_hotkey_still_works_on_providers_screen() -> None:
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(_providers_snapshot()),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert app.screen.name == "operations"
```

Check how other tests in the file identify the current screen (`app.screen.name` or an `isinstance` check) and use the same idiom.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_providers_screen.py -q -k operations_hotkey`
Expected: FAIL; the screen is still Providers.

- [ ] **Step 3: Rebind availability to `v`**

Change `Binding("a", "availability", "Availability/pricing")` to `Binding("v", "availability", "Availability/pricing")`. Update the two existing tests that press `"a"` to read availability (around lines 347 and 378 of the test file) to press `"v"`. Update the key list in `docs/operator/` wherever the Providers screen hotkeys are documented (grep for `Availability/pricing`).

- [ ] **Step 4: Run the suite and docs check**

Run: `uv run --frozen pytest tests/tui/test_providers_screen.py tests/tui/test_help_overlay.py -q && make docs-check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/tui/providers.py tests/tui/test_providers_screen.py docs/operator
git commit -s -m "fix(tui): availability hotkey no longer shadows Operations"
```

### Task 14: Providers refresh failure shows the exception class

**Files:**
- Modify: `src/pitwall/tui/providers.py:404-411`
- Test: `tests/tui/test_providers_screen.py`

Decision: the screen deliberately hides exception text so credentials never render. Showing only the exception class name keeps that property while telling the operator whether the failure was a connection error or a validation error.

- [ ] **Step 1: Write the failing test**

```python
async def test_providers_refresh_failure_names_the_error_class_only() -> None:
    canary = "refresh-credential-canary"

    class BrokenSource(StaticProvidersSource):
        async def load_providers(self) -> ProvidersSnapshot:
            raise ConnectionError(canary)

    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=BrokenSource(_providers_snapshot()),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        rendered = str(app.screen.query_one("#providers-error", Static).content)

    assert rendered == "Providers unavailable (ConnectionError)."
    assert canary not in rendered
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_providers_screen.py -q -k names_the_error_class`
Expected: FAIL; rendered is `Providers unavailable.`.

- [ ] **Step 3: Change the message**

```python
        except Exception as exc:  # reason: service/database details can contain sensitive configuration
            log.warning("providers refresh failed (%s)", type(exc).__name__)
            self.query_one("#providers-error", Static).update(
                f"Providers unavailable ({type(exc).__name__})."
            )
            return
```

Add `log = logging.getLogger(__name__)` and `import logging` if the module has none.

- [ ] **Step 4: Run and commit**

Run: `uv run --frozen pytest tests/tui/test_providers_screen.py -q`
Expected: PASS.

```bash
git add src/pitwall/tui/providers.py tests/tui/test_providers_screen.py
git commit -s -m "fix(tui): providers refresh failure names the error class"
```

### Task 15: Onboarding cancellation only writes over its own output

**Files:**
- Modify: `src/pitwall/tui/onboarding.py:227-233`
- Test: `tests/tui/test_onboarding_panel.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_starting_status_while_plan_runs_does_not_show_cancelled_text() -> None:
    wait = asyncio.Event()
    slow_source = StaticOnboardingSource(await _results(), wait=wait)
    panel = RunPodOnboardingPanel(slow_source)
    app = PanelApp(panel)
    async with app.run_test(size=(160, 45)) as pilot:
        _set_request(panel)
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        panel.query_one("#onboarding-status", Button).press()
        await pilot.pause()
        await pilot.pause()

        rendered = str(panel.query_one("#onboarding-result", Static).content).lower()
        assert "cancelled" not in rendered
        assert panel.query_one("#onboarding-cancel", Button).disabled is False
        wait.set()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_onboarding_panel.py -q -k status_while_plan`
Expected: FAIL on the `"cancelled" not in rendered` assertion.

- [ ] **Step 3: Guard the update**

```python
        except asyncio.CancelledError:
            if self._active_task is asyncio.current_task():
                self.query_one("#onboarding-result", Static).update(
                    "Onboarding action cancelled. Inspect status before resuming; "
                    "a confirmed write may already have completed."
                )
            self._finish()
            return
```

- [ ] **Step 4: Run and commit**

Run: `uv run --frozen pytest tests/tui/test_onboarding_panel.py -q`
Expected: PASS.

```bash
git add src/pitwall/tui/onboarding.py tests/tui/test_onboarding_panel.py
git commit -s -m "fix(tui): cancelled onboarding task does not overwrite a newer task's output"
```

### Task 16: Resources screen names the failure class per section

**Files:**
- Modify: `src/pitwall/tui/resources.py:237-247`, `:375-425`, `:797-801`
- Test: `tests/tui/test_resources_screen.py`

**Interfaces:**
- Produces: `ResourcesSnapshot.unavailable_errors: tuple[str, ...] = ()`, parallel to `unavailable_sections`, holding exception class names only.

- [ ] **Step 1: Write the failing test**

Extend `test_runpod_resources_source_maps_only_shared_service_results` after the existing stale assertions:

```python
    assert stale.unavailable_errors == ("RuntimeError",)
    assert "token-not-shown" not in " ".join(stale.unavailable_errors)
```

And add a rendering test:

```python
async def test_resources_error_line_names_failed_section_and_class() -> None:
    from dataclasses import replace

    degraded = replace(
        _resources_snapshot(),
        unavailable_sections=("endpoints",),
        unavailable_errors=("RuntimeError",),
    )
    market_service = RunpodMarketService(None, None, None)
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=StaticResourcesSource(degraded),
        runpod_market_source=StaticRunpodMarketSource(await market_service.read()),
    )
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        rendered = str(app.screen.query_one("#resources-error", Static).content)

    assert rendered == "Provider error · unavailable: endpoints (RuntimeError)"
```

This mirrors the app construction in `test_pitwall_app_switches_to_resources_screen` in the same file.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_resources_screen.py -q -k "shared_service_results or names_failed_section"`
Expected: FAIL with `AttributeError: unavailable_errors`.

- [ ] **Step 3: Record and render the class names**

Add the field to `ResourcesSnapshot` after `unavailable_sections`:

```python
    unavailable_errors: tuple[str, ...] = ()
```

In `RunPodResourcesSource.load_resources` after computing `unavailable`:

```python
errors = tuple(type(value).__name__ for value in results if isinstance(value, BaseException))
for name, value in zip(_SECTION_NAMES, results, strict=True):
    if isinstance(value, BaseException):
        log.warning("resources section %s unavailable (%s)", name, type(value).__name__)
```

Pass `unavailable_errors=errors` into the `ResourcesSnapshot(...)` constructor. In `_render_snapshot` replace the error line:

```python
if snapshot.unavailable_sections:
    errors = snapshot.unavailable_errors or ("unknown",) * len(snapshot.unavailable_sections)
    labelled = ", ".join(
        f"{name} ({error})"
        for name, error in zip(snapshot.unavailable_sections, errors, strict=False)
    )
    self.query_one("#resources-error", Static).update("Provider error · unavailable: " + labelled)
```

Add a module logger if none exists.

- [ ] **Step 4: Run and commit**

Run: `uv run --frozen pytest tests/tui/test_resources_screen.py -q`
Expected: PASS.

```bash
git add src/pitwall/tui/resources.py tests/tui/test_resources_screen.py
git commit -s -m "fix(tui): resources screen names the failing section's error class"
```

### Task 17: Retained rows carry a stale-since time

**Files:**
- Modify: `src/pitwall/tui/resources.py:237-247`, `:375-425`, and the `refreshed_label` property
- Test: `tests/tui/test_resources_screen.py`

**Interfaces:**
- Produces: `ResourcesSnapshot.stale_since: dt.datetime | None = None`, the refresh time of the oldest retained data; `refreshed_label` renders it when set.

- [ ] **Step 1: Write the failing test**

Extend the same source test:

```python
    assert stale.stale_since == snapshot.refreshed_at
    again = await source.load_resources()
    assert again.stale_since == snapshot.refreshed_at  # carried forward, not reset
```

For the second call the `now` callable must advance; if `_NOW` is a constant, construct the source with an incrementing `now` in this test:

```python
clock = [_NOW]


def now() -> dt.datetime:
    clock[0] = clock[0] + dt.timedelta(minutes=5)
    return clock[0]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_resources_screen.py -q -k shared_service_results`
Expected: FAIL with `AttributeError: stale_since`.

- [ ] **Step 3: Implement**

Add to `ResourcesSnapshot`:

```python
    stale_since: dt.datetime | None = None
```

In `load_resources`, after `stale` is computed:

```python
        stale_since = None
        if stale and previous is not None:
            stale_since = previous.stale_since or previous.refreshed_at
```

and pass `stale_since=stale_since`. In the `refreshed_label` property (grep `def refreshed_label`), append the stale marker:

```python
        label = self.refreshed_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if self.stale_since is not None:
            label += f" (retained data from {self.stale_since.strftime('%H:%M:%S UTC')})"
        return label
```

Keep whatever format the property already uses for the first part; only append the parenthetical.

- [ ] **Step 4: Run and commit**

Run: `uv run --frozen pytest tests/tui/test_resources_screen.py -q`
Expected: PASS.

```bash
git add src/pitwall/tui/resources.py tests/tui/test_resources_screen.py
git commit -s -m "fix(tui): label retained resource rows with their original refresh time"
```

### Task 18: Unknown capability maps to route_not_found

**Files:**
- Modify: `src/pitwall/tui/routing_jobs.py:436-456`
- Test: `tests/tui/test_routing_jobs_panel.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_unknown_capability_maps_to_route_not_found() -> None:
    from pitwall.resolver.exceptions import CapabilityNotFoundError

    class MissingCapabilityService(_FakeRoutingService):
        async def preview(self, **kwargs: Any) -> Any:
            raise CapabilityNotFoundError("llm.nope")

    async def factory() -> MissingCapabilityService:
        return MissingCapabilityService()

    source = ProductionRoutingJobsSource(factory)
    plan = RoutingJobCommand(
        action=RoutingJobAction.PLAN, capability_id="llm.nope", payload={"messages": []}
    )
    with pytest.raises(RoutingJobsError, match="route_not_found"):
        await source.execute_routing_job(plan)
```

Match the `preview` signature used by `_FakeRoutingService` at line 133 of the test file.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_routing_jobs_panel.py -q -k unknown_capability`
Expected: FAIL with `routing_operation_failed`.

- [ ] **Step 3: Add the clause**

Import `from pitwall.resolver.exceptions import CapabilityNotFoundError` and insert before `except LookupError:`:

```python
        except CapabilityNotFoundError:
            raise RoutingJobsError("route_not_found") from None
```

- [ ] **Step 4: Run and commit**

Run: `uv run --frozen pytest tests/tui/test_routing_jobs_panel.py -q`
Expected: PASS.

```bash
git add src/pitwall/tui/routing_jobs.py tests/tui/test_routing_jobs_panel.py
git commit -s -m "fix(tui): unknown capability reports route_not_found"
```

### Task 19: Guardrail preview keeps the input until the preview succeeds

**Files:**
- Modify: `src/pitwall/tui/operations.py:601-625`
- Test: `tests/tui/test_operations_screen.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_invalid_guardrail_preview_keeps_the_typed_payload() -> None:
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        operations_source=StaticOperationsSource(_operations_snapshot()),
    )
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#guardrail-preview-input", Input).value = '{"messages": [}'
        screen.query_one("#guardrail-preview-button", Button).press()
        await pilot.pause()

        assert screen.query_one("#guardrail-preview-input", Input).value == '{"messages": [}'
        assert "unavailable" in str(screen.query_one("#guardrail-preview-result", Static).content)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_operations_screen.py -q -k keeps_the_typed_payload`
Expected: FAIL; the input is empty.

- [ ] **Step 3: Move the clear**

Delete `preview_input.value = ""` from its current position and insert it immediately after the successful `result = self._source.preview_guardrail(payload)` line, before the `findings = ...` rendering.

- [ ] **Step 4: Run and commit**

Run: `uv run --frozen pytest tests/tui/test_operations_screen.py -q`
Expected: PASS.

```bash
git add src/pitwall/tui/operations.py tests/tui/test_operations_screen.py
git commit -s -m "fix(tui): keep the guardrail preview payload when validation fails"
```

### Task 20: Limited decision without a reason renders a stable label

**Files:**
- Modify: `src/pitwall/tui/operations.py:399`
- Test: `tests/tui/test_operations_screen.py`

- [ ] **Step 1: Write the failing test**

The line is built by the `guardrail_last_decision_summary` property of the operations snapshot. Next to `test_guardrail_last_decision_summary_is_complete_and_non_disclosing` add:

```python
def test_limited_decision_without_reason_renders_unspecified() -> None:
    service = PreSpendInspectionService(now_factory=lambda: _NOW)
    service.inspect({"token": "sk-abcdefghijklmnop12345678"})
    status = service.status()
    assert status.last_decision is not None
    limited_status = replace(
        status,
        last_decision=replace(status.last_decision, limited=True, limit_reason=None),
    )
    snapshot = replace(_operations_snapshot(), guardrails=limited_status)

    summary = snapshot.guardrail_last_decision_summary

    assert "limit unspecified" in summary
    assert "limit None" not in summary
```

`replace` is `dataclasses.replace`, already imported by the file. If the status or decision record is not a dataclass, build the record with its constructor using the field list at `src/pitwall/security/pre_spend.py:196-210`.

- [ ] **Step 2: Run to verify failure**

Expected: FAIL with `limit None` in the output.

- [ ] **Step 3: Fix the expression**

```python
        limit = (last.limit_reason or "unspecified") if last.limited else "none"
```

- [ ] **Step 4: Run and commit**

```bash
git add src/pitwall/tui/operations.py tests/tui/test_operations_screen.py
git commit -s -m "fix(tui): stable label for limited guardrail decisions without a reason"
```

### Task 21: Relabel the capacity figure to what it now counts

**Files:**
- Modify: `src/pitwall/tui/operations.py:227`, `:258`, `:883`, `:891`, `:905`
- Test: `tests/tui/test_operations_screen.py:158`

`ProductionRoutePlan` no longer exposes `capacity_decisions`, so the old count cannot be restored. Rename the field so the label is truthful.

- [ ] **Step 1: Update the test expectation**

Change the `capacity_decision_count=0` keyword at line 158 to `capacity_dropped_count=0`, and wherever the summary string is asserted change `| capacity 0` to `| capacity dropped 0`.

- [ ] **Step 2: Run to verify failure**

Expected: FAIL with `TypeError: unexpected keyword argument`.

- [ ] **Step 3: Rename**

Rename `capacity_decision_count` to `capacity_dropped_count` in the `RoutingSummary` dataclass and its three constructors, and change the summary segment to `f" | capacity dropped {self.capacity_dropped_count}"`.

- [ ] **Step 4: Run and commit**

Run: `uv run --frozen pytest tests/tui/test_operations_screen.py -q`

```bash
git add src/pitwall/tui/operations.py tests/tui/test_operations_screen.py
git commit -s -m "fix(tui): label the routing summary capacity figure as dropped candidates"
```

### Task 22: Leases keep the pod id visible and searchable

**Files:**
- Modify: `src/pitwall/tui/leases.py:60-66`, `:280-305`
- Test: `tests/tui/test_leases_screen.py`

**Interfaces:**
- Produces: `LeaseDisplayRow.resource_label` property: `external_resource_id` alone when it equals `pod_id` or is unset, otherwise `"{external_resource_id} · pod {pod_id}"`.

- [ ] **Step 1: Write the failing test**

```python
def test_resource_label_shows_both_ids_when_they_differ() -> None:
    row = _row(pod_id="pod-9f2")
    both = LeaseDisplayRow(**{**row.__dict__, "external_resource_id": "ep-abc123"})

    assert both.resource_label == "ep-abc123 · pod pod-9f2"
    assert row.resource_label == "pod-9f2"
```

If `LeaseDisplayRow` uses `slots=True`, build the second row with `dataclasses.replace(row, external_resource_id="ep-abc123")`.

And a filter test mirroring `test_leases_screen_renders_no_matching_state_for_nonempty_filtered_snapshot`:

```python
async def test_leases_filter_matches_pod_id_when_external_resource_id_is_set() -> None:
    row = replace(_row(pod_id="pod-9f2"), external_resource_id="ep-abc123")
    app = PitwallApp(
        overview_source=_overview_source(), leases_source=StaticLeasesSource(_snapshot(row))
    )

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("l")
        app.screen.apply_filter("pod-9f2")
        await pilot.pause()

        assert str(app.screen.query_one("#leases-summary", Static).content) == "1 active pod leases"
        assert "No matching" not in str(app.screen.query_one("#leases-empty", Static).content)
```

Import `replace` from `dataclasses` if the file does not already.

- [ ] **Step 2: Run to verify failure**

Expected: FAIL with `AttributeError: resource_label`.

- [ ] **Step 3: Implement**

Add to `LeaseDisplayRow`:

```python
    @property
    def resource_label(self) -> str:
        if not self.external_resource_id or self.external_resource_id == self.pod_id:
            return self.pod_id
        return f"{self.external_resource_id} · pod {self.pod_id}"
```

In the render method, add `row.pod_id` to the haystack tuple and replace both `row.resource_id` uses (haystack and `add_row`) with `row.resource_label`.

- [ ] **Step 4: Run and commit**

Run: `uv run --frozen pytest tests/tui/test_leases_screen.py -q`

```bash
git add src/pitwall/tui/leases.py tests/tui/test_leases_screen.py
git commit -s -m "fix(tui): leases keep the pod id visible and searchable"
```

---

## Phase E: MCP and pricing

### Task 23: MCP recent-workloads validates limit before touching the pool

**Files:**
- Modify: `src/pitwall/mcp/tools/cost.py:63-104`
- Test: `tests/mcp/test_cost_tools.py`

- [ ] **Step 1: Write the failing test**

Inside `TestPitwallRecentWorkloads`:

```python
    async def test_rejects_limit_outside_documented_range_before_pool(self) -> None:
        with patch("pitwall.mcp.tools.cost.get_pool") as mock_get_pool:
            with pytest.raises(ValueError, match="limit must be between 1 and 100"):
                await pitwall_recent_workloads(limit=250)
            with pytest.raises(ValueError, match="limit must be between 1 and 100"):
                await pitwall_recent_workloads(limit=0)
            mock_get_pool.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL because `get_pool` was called (the error is raised later, inside the read model).

- [ ] **Step 3: Validate up front**

At the top of `pitwall_recent_workloads`, before `pool = await get_pool()`:

```python
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
```

Update the docstring `limit:` line to `Maximum number of workloads to return, 1 to 100 (default 20).`

- [ ] **Step 4: Run the MCP suite and schema checks**

Run: `uv run --frozen pytest tests/mcp -q`
Expected: PASS. If a generated-schema snapshot test compares tool descriptions, regenerate it with the command that test names in its failure message.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/mcp/tools/cost.py tests/mcp/test_cost_tools.py
git commit -s -m "fix(mcp): validate recent-workloads limit before acquiring the pool"
```

### Task 24: Cache the market-backed price snapshot

**Files:**
- Modify: `src/pitwall/models/prices.py:81-141`
- Test: `tests/models/test_prices.py` (create if absent)

**Interfaces:**
- Produces: `_cached_snapshot(cloud) -> GpuPriceSnapshot | None` and `_store_snapshot(cloud, snapshot) -> None` helpers used by both `gpu_price_snapshot` and the market path of `load_gpu_price_snapshot`.

- [ ] **Step 1: Write the failing test**

```python
async def test_market_backed_snapshot_is_cached_for_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.models import prices
    from pitwall.models.prices import load_gpu_price_snapshot, reset_price_cache

    reads = 0

    from pitwall.runpod_market import RunpodMarketService

    static_read = await RunpodMarketService(None, None, None).read()

    class FakeMarket:
        async def read(self, *, force_refresh: bool = False):
            nonlocal reads
            reads += 1
            return static_read

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "pitwall.runpod_market.build_configured_runpod_market_service", lambda: FakeMarket()
    )
    reset_price_cache()

    first = await load_gpu_price_snapshot(cloud="secure")
    second = await load_gpu_price_snapshot(cloud="secure")

    assert reads == 1
    assert first == second
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL with `reads == 2`.

- [ ] **Step 3: Extract the cache helpers and use them**

```python
def _cached_snapshot(cloud: Cloud) -> GpuPriceSnapshot | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cloud)
        if cached is not None and now - cached.monotonic_at <= _CACHE_TTL_SECONDS:
            return cached.snapshot
    return None


def _store_snapshot(cloud: Cloud, snapshot: GpuPriceSnapshot) -> None:
    with _CACHE_LOCK:
        _CACHE[cloud] = _CachedSnapshot(monotonic_at=time.monotonic(), snapshot=snapshot)
```

Rewrite `gpu_price_snapshot` to call `_cached_snapshot` first and `_store_snapshot` at the end (behaviour unchanged). In `load_gpu_price_snapshot`, wrap the `settings is None` branch:

```python
    if settings is None:
        cached = _cached_snapshot(cloud)
        if cached is not None:
            return cached
        from pitwall.runpod_market import build_configured_runpod_market_service

        owned_market = build_configured_runpod_market_service()
        try:
            snapshot = gpu_price_snapshot_from_market(await owned_market.read(), cloud=cloud)
        finally:
            await owned_market.aclose()
        _store_snapshot(cloud, snapshot)
        return snapshot
```

- [ ] **Step 4: Run and commit**

Run: `uv run --frozen pytest tests/models tests/tui/test_hardware_fit_screen.py tests/runpod_client/test_warm_volume.py -q`

```bash
git add src/pitwall/models/prices.py tests/models
git commit -s -m "fix(prices): cache the market-backed snapshot for CLI and TUI callers"
```

---

## Phase F: Documentation and hygiene

### Task 25: Changelog entry for the retained backlog and these fixes

**Files:**
- Modify: `CHANGELOG.md` under `## [Unreleased]`

- [ ] **Step 1: Add the entries**

Under `### Added`, prepend:

```markdown
- Provider-neutral runtime contracts, static adapter registry, and credential references
  (migrations `0028` and `0030`), with structured Decimal cost quotes, ceilings, and
  reconciliation provenance (migration `0029`) and persisted production route plans
  (migration `0031`).
- RunPod catalogue, balance, billing, Pod/endpoint/template/volume/registry administration,
  bounded volume-object and Pod-log operations with a durable mutation journal, and a
  resumable plan/apply/status/resume/rollback onboarding workflow across REST, MCP, CLI, and TUI.
- Vast.ai and Lambda Cloud compute adapters, a Together sync-inference adapter, and exact
  OpenAI fallback chains behind deterministic routing with async job lifecycle and result paging.
- Decimal burn-rate forecasts with atomic owner-token alert reservations, and bounded pre-spend
  guardrail status and preview surfaces.
- 52 REST operations, 49 MCP tools, and the `cost`, `burn-rate`, `guardrails`, `routing`,
  `runpod`, `runpod-onboard`, `provider-ops`, and `volume-files` CLI groups.
```

Add a `### Changed` section (create it if absent) with:

```markdown
- Per-token admission ceilings now use the larger of the estimated token count and the UTF-8
  byte count of the input, and energy-priced capabilities are admitted at their full
  execution-timeout ceiling. Both raise the conservative ceiling used by `per_request_max_usd`.
- The Cost screen renders against the configured monthly budget setting (default `50.0`)
  instead of requiring `PITWALL_MONTHLY_BUDGET_USD`; a zero budget shows an immediate projected
  breach.
- The routing summary's capacity figure now counts candidates dropped for capacity.
- Notification delivery failures report `notification_delivery_failed` and log only the
  exception class, so no transport detail can leak.
```

Add a `### Fixed` section with one bullet per Task 1 through Task 24, one line each, phrased from the commit subjects.

- [ ] **Step 2: Verify and commit**

Run: `make docs-check && uv run --frozen python tools/guards/repo_text_policy.py CHANGELOG.md`

```bash
git add CHANGELOG.md
git commit -s -m "docs: changelog for the retained backlog and review fixes"
```

### Task 26: Document the remaining behaviour differences

**Files:**
- Modify: `docs/operator/` cost documentation (grep for `burn-rate` or `PITWALL_MONTHLY_BUDGET_USD` to find the page)
- Modify: `docs/evidence/2026-09-02-retained-backlog-independent-review.md` (append a "Disposition" section)

- [ ] **Step 1: Operator note on cost sources**

Add a short subsection stating that runway and what-if figures read `cost_daily` rollups while chargeback sums `workloads`, so the Cost screen can show different totals until the rollup job runs, and name the command that runs the rollup (grep `cost_daily` under `src/pitwall/cli.py` and `src/pitwall/reconciler` for its name).

- [ ] **Step 2: Disposition section in the review doc**

Append:

```markdown
## 7. Disposition (2026-09-xx)

| Item | Disposition |
| --- | --- |
| Section 2 flake | Fixed in Task 1 of `docs/superpowers/plans/2026-09-02-retained-backlog-review-fixes.md` |
| Defects 1 through 21 | Fixed in Tasks 2 through 24 of the same plan |
| Section 4 behaviour changes | Retained and documented in `CHANGELOG.md` and the operator cost page |
| Section 5 hygiene | Completed in Tasks 25 and 27 |
```

Fill in the date when the plan completes.

- [ ] **Step 3: Verify and commit**

Run: `make docs-check`

```bash
git add docs
git commit -s -m "docs: record cost-source differences and review disposition"
```

### Task 27: Remove leftover worktrees, branches, and local artefacts

**Files:** none in the repository.

All 15 worktrees are clean and every branch's content is merged. The branches show as unmerged only because the PRs were squash-merged.

- [ ] **Step 1: Confirm every worktree is clean**

```bash
for wt in $(git worktree list --porcelain | awk '/^worktree /{print $2}' | grep -v "^$(pwd)$"); do
  echo "$(git -C "$wt" status --short | wc -l) $wt"
done
```

Expected: every line starts with `0`. Stop and ask if any does not.

- [ ] **Step 2: Remove worktrees and branches**

```bash
for wt in $(git worktree list --porcelain | awk '/^worktree /{print $2}' | grep -v "^$(pwd)$"); do
  git worktree remove --force "$wt"
done
git worktree prune
for b in $(git branch --format='%(refname:short)' | grep -v '^main$'); do
  git branch -D "$b"
done
git branch
```

Expected: only `main` remains.

- [ ] **Step 3: Remove ignored local artefacts**

```bash
rm -rf artifacts mutants
git status --short
```

Expected: `git status` is clean (both directories are git-ignored).

---

## Final verification

Run in this order and record the output in the PR description:

```bash
uv run --frozen ruff check . && uv run --frozen ruff format --check .
uv run --frozen mypy src
for seed in 1 12345 987654; do
  uv run --frozen pytest -q -m "not integration and not slow and not live" -p randomly --randomly-seed=$seed | tail -1
done
make openapi-check
make docs-check
make ci-tools
git ls-files -z | xargs -0 -r -n 200 uv run --frozen python tools/guards/repo_text_policy.py
```

Expected: ruff and mypy clean; three seeds each end with `0 failed`; OpenAPI baseline unchanged; docs, workflow, and text policy checks pass.

Open one PR per phase (A+B, C, D, E+F) or one PR for all phases if the maintainer prefers; each phase is independently green.
