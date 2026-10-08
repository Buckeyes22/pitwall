# Broker Agent Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six broker gaps that forced agents to ask another session for help: unexplained budget rejections, previews that skip the budget check, budget limits that need a restart, MCP connections that die with the container, pod views without an address, and a deployed broker that predates the pod-log fix.

**Architecture:** Budget limits move from process-start environment values to a single audited database row that every budget reader resolves at use time (the environment values stay as defaults). The MCP safe boundary passes the budget error's server-computed fields through. Raw-pod previews evaluate the same budget rule as the real request. A stdio relay keeps the harness connected across container restarts. The pod view gains the pod's public address. A redeploy ships all of it and proves each gap closed on the live broker.

**Tech Stack:** Python 3.14.7, asyncpg, FastAPI, the MCP Python SDK (stdio), pytest (+ `pg_pool` integration fixture), Docker Compose (`pitwall-services`).

**Design:** approved by the maintainer on 2026-09-26. Budget-limit changes during execution go through the audited `pitwall_budget_set` surface. Evidence: two `budget_rejected` results without a reason after a passing preview, a lost broker connection on a cap-change restart, a missing SSH address, and `volume_file_not_configured` from `pitwall_pod_logs`. A separate request asked for an SSH address and a 240-minute TTL. Live state at planning time: monthly budget $5.00, month-to-date spend $4.82, per-request cap $10.00.

## Global Constraints

- Python only through `uv run` (root project). Never bare `python`.
- Work in `$HOME/git/pitwall-gaps` on `feat/broker-agent-gaps` (based on `f332554` (historical, private repository), which contains migration 0035). The next migration is `0036`.
- `pitwall.mcp.tools.*` must not import `pitwall.cost.budget_gate`, `pitwall.cost.sync_gate`, `pitwall.cost.estimator`, `pitwall.runpod_client.*`, or routing internals (`tests/mcp/test_no_business_logic_guard.py`). MCP tools call service functions.
- Error payloads may carry only server-computed values. Request text never crosses the MCP boundary.
- Every budget-limit change requires a non-empty reason and writes a `config_audit` row (`action="budget_limits.set"`, `entity_type="budget_limits"`, `entity_id="global"`).
- Budget changes never need a restart: every enforcement and display path reads the effective limits at use time.
- DB suites share `pitwall_test` on 127.0.0.1:5444 (`pitwall-integ` stack): never `make up`/`make down`; never run two DB-backed suites at once. Integration command:
  `PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/0 uv run --frozen pytest -m "integration and not live" <paths> 2>&1 | tail -15`
- Pipe test output through `| tail -40`. One commit per task. Never weaken a test; tests that pin a value this plan changes (tool count 79, the bare `budget_rejected` payload, latest migration 0035, surface denominators) are updated to the new value.
- No IP address in committed text except documentation ranges (`203.0.113.0/24`).

## Review Focus

1. A budget error's `snapshot` must never carry request-controlled text: only the five known keys with decimal-string values pass (Task 2 `test_budget_detail_drops_unknown_and_non_decimal_fields`).
2. Raising the monthly budget at runtime must also move the reconciler's budget-breach threshold; otherwise an armed kill switch fires at the old limit (Task 5 `test_breach_uses_the_runtime_monthly_budget`).
3. A preview and the apply that follows must agree for the same TTL and rate (Task 7 `test_preview_verdict_matches_apply_outcome`, integration).
4. Two concurrent limit changes and an admission must serialize on the budget advisory lock (Task 3 `set_limits` takes `PITWALL_BUDGET_LOCK_KEY`; integration test).
5. A request in flight when the server dies gets exactly one error response, and the replayed `initialize` response is never forwarded to the harness (Task 8 `test_relay_replays_initialize_and_fails_in_flight_request`).

## File structure

- `db/migrations/0036_budget_limits.sql` — singleton `pitwall.budget_limits` row.
- `src/pitwall/cost/budget_limits.py` — `BudgetLimits`, `read_limits`, `effective_limits`, `set_limits`, `budget_status`.
- `src/pitwall/cost/budget_gate.py` — enforce effective limits under the lock; `evaluate()` / `BudgetVerdict`.
- `src/pitwall/mcp/safe_boundary.py` — budget detail passthrough.
- `src/pitwall/runpod_control_plane.py` — `PodResource.public_ip`, `.port_mappings`.
- `src/pitwall/api/leases/launch.py` — `preview_raw_pod_lease_budget`.
- `src/pitwall/mcp/tools/runpod_resources.py` — preview carries `budget`.
- `src/pitwall/mcp/tools/budget.py` — `pitwall_budget_status`, `pitwall_budget_set`.
- `src/pitwall/api/routes/budget.py` — `GET/PUT /v1/admin/budget`.
- `src/pitwall/cli_budget.py` — `pitwall budget show|set`.
- `src/pitwall/mcp/relay.py` — stdio relay; `pitwall mcp relay -- CMD…`.
- Modified readers: `routing/production.py`, `cost/sub_budgets.py`, `cost/billing_read.py`, `finops/burn_rate.py`, `reconciler/__init__.py`, `audit/capability.py`.

---

### Task 1: The pod view carries the pod's public address

**Files:**
- Modify: `src/pitwall/runpod_control_plane.py` — `PodResource` (line 330), `_pod_resource` (line 1699)
- Test: `tests/test_runpod_pod_connection.py` (create)

**Interfaces:**
- Produces: `PodResource.public_ip: str | None`, `PodResource.port_mappings: dict[str, int]` (private port as string → public port). Every surface that dumps `PodResource` (`pitwall_runpod_get_pod`, `pitwall_runpod_list_pods`, `/v1/admin/runpod/pods…`) now includes them.

- [x] **Step 1: Write the failing tests**

```python
"""The raw-pod view carries the pod's public address so an agent can reach its own pod."""

from __future__ import annotations

from pitwall.runpod_control_plane import _pod_resource


def test_rest_pod_exposes_public_ip_and_port_mappings() -> None:
    pod = _pod_resource(
        {
            "id": "0xzp9kwv4wtgb4",
            "name": "p",
            "desiredStatus": "RUNNING",
            "publicIp": "203.0.113.7",
            "portMappings": {"22": 22060},
        }
    )
    assert (pod.public_ip, pod.port_mappings) == ("203.0.113.7", {"22": 22060})
    assert pod.model_dump(mode="json")["port_mappings"] == {"22": 22060}


def test_graphql_runtime_public_ports_are_used_when_rest_fields_are_absent() -> None:
    pod = _pod_resource(
        {
            "id": "abc123def456gh",
            "desiredStatus": "RUNNING",
            "runtime": {
                "ports": [
                    {
                        "ip": "203.0.113.8",
                        "isIpPublic": True,
                        "privatePort": 22,
                        "publicPort": 40022,
                        "type": "tcp",
                    },
                    {
                        "ip": "10.0.0.2",
                        "isIpPublic": False,
                        "privatePort": 8000,
                        "publicPort": 8000,
                        "type": "http",
                    },
                ]
            },
        }
    )
    assert (pod.public_ip, pod.port_mappings) == ("203.0.113.8", {"22": 40022})


def test_malformed_mappings_are_ignored_not_raised() -> None:
    pod = _pod_resource(
        {
            "id": "abc123def456gh",
            "desiredStatus": "RUNNING",
            "portMappings": {"ssh": "x", "22": None, "8888": 30888},
        }
    )
    assert pod.port_mappings == {"8888": 30888}


def test_pod_without_network_fields_is_unchanged() -> None:
    pod = _pod_resource({"id": "abc123def456gh", "desiredStatus": "EXITED"})
    assert (pod.public_ip, pod.port_mappings) == (None, {})
```

If `_provider_resource_id` rejects `abc123def456gh`, use an id of the shape the existing tests in `tests/mcp/test_runpod_resources.py` use.

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_runpod_pod_connection.py -q 2>&1 | tail -5`
Expected: FAIL — `AttributeError: 'PodResource' object has no attribute 'public_ip'`.

- [x] **Step 3: Implement**

Add to `PodResource` (after `uptime_seconds`):

```python
    public_ip: str | None = None
    port_mappings: dict[str, int] = Field(default_factory=dict)
```

(import `Field` from pydantic if the module does not already). Add above `_pod_resource`:

```python
def _pod_connection(value: Mapping[str, Any]) -> tuple[str | None, dict[str, int]]:
    """The caller's own pod address: REST publicIp/portMappings, else GraphQL runtime.ports."""
    mappings: dict[str, int] = {}
    raw = value.get("portMappings")
    if isinstance(raw, Mapping):
        for private, public in raw.items():
            port = _optional_int(public)
            if port is not None and str(private).isdigit():
                mappings[str(private)] = port
    public_ip = _optional_string(value.get("publicIp"))
    for entry in _mapping(value.get("runtime")).get("ports") or []:
        if not isinstance(entry, Mapping) or entry.get("isIpPublic") is not True:
            continue
        private = _optional_int(entry.get("privatePort"))
        public = _optional_int(entry.get("publicPort"))
        if private is not None and public is not None:
            mappings.setdefault(str(private), public)
        public_ip = public_ip or _optional_string(entry.get("ip"))
    return public_ip, mappings
```

and in `_pod_resource` compute `public_ip, port_mappings = _pod_connection(value)` and pass both to `PodResource(...)`. If `_optional_int` raises on non-numeric strings instead of returning `None`, wrap the conversion so malformed values are skipped (the third test pins that).

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_runpod_pod_connection.py tests/mcp/test_runpod_resources.py tests/mcp/test_resource_response_acceptance.py -q 2>&1 | tail -3`
Expected: all pass. If a response-shape acceptance test pins the exact pod keys, update it to include `public_ip` and `port_mappings`.

- [x] **Step 5: Commit**

```bash
git add src/pitwall/runpod_control_plane.py tests/test_runpod_pod_connection.py tests/mcp
git commit -m "feat(runpod): the pod view carries its public IP and port mappings"
```

---

### Task 2: Budget rejections explain themselves across the MCP boundary

**Files:**
- Modify: `src/pitwall/mcp/safe_boundary.py` — `_stable_error_payload` (line 16)
- Test: `tests/mcp/test_safe_boundary_codes.py` (update `test_budget_rejection_keeps_its_code`, add tests)

**Interfaces:**
- Produces: MCP error payloads for `budget_rejected`, `sub_budget_rejected`, `budget_exhausted` of the form `{"error": code, "reason": "monthly_budget"|"per_request_cap", "snapshot": {five decimal strings}, "remedy": str}`. All other errors stay `{"error": code}`.

- [x] **Step 1: Write the failing tests** (replace `test_budget_rejection_keeps_its_code`; add the rest)

```python
from mcp.shared.exceptions import McpError

from pitwall.mcp.error_adapter import adapt_error

_SNAPSHOT = BudgetSnapshot(
    monthly_budget_usd=Decimal("5"),
    per_request_max_usd=Decimal("10"),
    mtd_spend_usd=Decimal("4.817201"),
    estimate_usd=Decimal("1.96"),
    budget_remaining_usd=Decimal("0.182799"),
)
_EXPECTED = {
    "error": "budget_rejected",
    "reason": "monthly_budget",
    "snapshot": {
        "monthly_budget_usd": "5",
        "per_request_max_usd": "10",
        "mtd_spend_usd": "4.817201",
        "estimate_usd": "1.96",
        "budget_remaining_usd": "0.182799",
    },
    "remedy": "raise the limit with pitwall_budget_set (a reason is required), or lower ttl_minutes or max_cost_per_hour",
}


def test_budget_rejection_keeps_its_code_and_explains_itself() -> None:
    assert _stable_error_payload(_wrapped(BudgetRejected("monthly_budget", _SNAPSHOT))) == _EXPECTED


def test_adapted_budget_rejection_keeps_its_explanation() -> None:
    adapted = adapt_error(BudgetRejected("monthly_budget", _SNAPSHOT))
    assert _stable_error_payload(_wrapped(adapted)) == _EXPECTED


def test_budget_detail_drops_unknown_and_non_decimal_fields() -> None:
    error = McpError(
        adapt_error(BudgetRejected("monthly_budget", _SNAPSHOT)).error.model_copy(
            update={
                "data": {
                    "error": "budget_rejected",
                    "reason": "request text",
                    "snapshot": {"estimate_usd": "1; drop", "monthly_budget_usd": "5", "note": "x"},
                }
            }
        )
    )
    assert _stable_error_payload(_wrapped(error)) == {
        "error": "budget_rejected",
        "snapshot": {"monthly_budget_usd": "5"},
        "remedy": _EXPECTED["remedy"],
    }


def test_non_budget_errors_still_carry_only_their_code() -> None:
    assert _stable_error_payload(_wrapped(CapabilityNotFound("secret-canary.capability"))) == {
        "error": "capability_not_found"
    }
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/mcp/test_safe_boundary_codes.py -q 2>&1 | tail -5`
Expected: FAIL — payload is `{'error': 'budget_rejected'}`.

- [x] **Step 3: Implement** in `safe_boundary.py`

```python
import re
from collections.abc import Mapping

# Budget refusals carry Pitwall's own budget state so an agent can act without asking a
# person which rule fired. Only these server-computed fields cross the boundary.
_BUDGET_ERRORS = frozenset({"budget_rejected", "sub_budget_rejected", "budget_exhausted"})
_BUDGET_REASONS = frozenset({"monthly_budget", "per_request_cap"})
_SNAPSHOT_KEYS = (
    "monthly_budget_usd",
    "per_request_max_usd",
    "mtd_spend_usd",
    "estimate_usd",
    "budget_remaining_usd",
)
_DECIMAL = re.compile(r"-?\d+(?:\.\d+)?")
BUDGET_REMEDY = (
    "raise the limit with pitwall_budget_set (a reason is required), "
    "or lower ttl_minutes or max_cost_per_hour"
)


def _budget_detail(data: Mapping[str, Any]) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    if data.get("reason") in _BUDGET_REASONS:
        detail["reason"] = data["reason"]
    snapshot = data.get("snapshot")
    if isinstance(snapshot, Mapping):
        values = {
            key: str(snapshot[key])
            for key in _SNAPSHOT_KEYS
            if key in snapshot and _DECIMAL.fullmatch(str(snapshot[key]))
        }
        if values:
            detail["snapshot"] = values
    if detail:
        detail["remedy"] = BUDGET_REMEDY
    return detail
```

Change `_stable_error_payload`'s return type to `dict[str, Any]` and its two code paths:

```python
    if isinstance(cause, McpError) and isinstance(cause.error.data, dict):
        error = cause.error.data.get("error")
        if isinstance(error, str) and error:
            payload: dict[str, Any] = {"error": error}
            if error in _BUDGET_ERRORS:
                payload.update(_budget_detail(cause.error.data))
            return payload
    class_code = getattr(type(cause), "error_code", None) if cause is not None else None
    if isinstance(class_code, str) and class_code:
        payload = {"error": class_code}
        to_body = getattr(type(cause), "to_response_body", None)
        if class_code in _BUDGET_ERRORS and callable(to_body):
            payload.update(_budget_detail(to_body(cause)))
        return payload
```

(`to_response_body` is looked up on the class, like `error_code`, so an instance attribute cannot inject data.)

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/mcp -q 2>&1 | tail -3`
Expected: `0 failed`. Other tests that pin a bare `{"error": "budget_rejected"}` MCP payload are updated to the explained payload.

- [x] **Step 5: Commit**

```bash
git add src/pitwall/mcp/safe_boundary.py tests/mcp
git commit -m "fix(mcp): budget rejections carry their reason, figures, and remedy"
```

---

### Task 3: Runtime budget limits (migration 0036 and the limits service)

**Files:**
- Create: `db/migrations/0036_budget_limits.sql`
- Create: `src/pitwall/cost/budget_limits.py`
- Modify: `tests/db/test_model_studio_migration.py` (0035 is no longer the head)
- Test: `tests/cost/test_budget_limits.py`, `tests/integration/test_budget_limits_integration.py`

**Interfaces:**
- Produces:
  - `BUDGET_LIMITS_SQL: str`
  - `@dataclass(frozen=True, slots=True) BudgetLimits(monthly_budget_usd: Decimal, per_request_max_usd: Decimal, source: Literal["runtime","environment"], updated_at: datetime | None = None, updated_by: str | None = None, reason: str | None = None)` with `to_dict() -> dict[str, str | None]`
  - `class BudgetLimitsError(ValueError)` (`error_code = "invalid_budget_limits"`, `status_code = 422`)
  - `async read_limits(conn, *, default_monthly: Decimal, default_per_request: Decimal) -> BudgetLimits`
  - `environment_defaults() -> tuple[Decimal, Decimal]`
  - `async effective_limits(pool, *, default_monthly: Decimal | None = None, default_per_request: Decimal | None = None) -> BudgetLimits`
  - `async set_limits(pool, *, monthly_budget_usd: Decimal | None, per_request_max_usd: Decimal | None, reason: str, actor: str) -> BudgetLimits`
  - `async budget_status(pool) -> dict[str, Any]` (limits + `mtd_spend_usd` + `budget_remaining_usd`, all strings)

- [x] **Step 1: Write the migration**

```sql
-- Runtime budget limits: one audited row that overrides PITWALL_MONTHLY_BUDGET_USD and
-- PITWALL_PER_REQUEST_MAX_USD without a restart. No row means the environment values apply.

CREATE TABLE pitwall.budget_limits (
  id                   SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  monthly_budget_usd   NUMERIC(14, 6) NOT NULL CHECK (monthly_budget_usd > 0),
  per_request_max_usd  NUMERIC(14, 6) NOT NULL CHECK (per_request_max_usd > 0),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by           TEXT NOT NULL CHECK (length(btrim(updated_by)) > 0),
  reason               TEXT NOT NULL CHECK (length(btrim(reason)) > 0)
);
```

In `tests/db/test_model_studio_migration.py`, change `test_0035_is_the_latest_migration_and_widens_every_check` to assert `"0035_model_studio"` is in the discovered versions (keep its other assertions), mirroring the earlier 0034 change.

- [x] **Step 2: Write the failing unit tests** (`tests/cost/test_budget_limits.py`)

```python
"""Runtime budget limits: resolution, validation, and status (no database)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.cost.budget_limits import BudgetLimits, BudgetLimitsError, read_limits, set_limits


class _Conn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        assert "pitwall.budget_limits" in sql
        return self.row


@pytest.mark.anyio
async def test_no_row_means_the_environment_defaults_apply() -> None:
    limits = await read_limits(
        _Conn(None), default_monthly=Decimal("5"), default_per_request=Decimal("10")
    )
    assert limits == BudgetLimits(Decimal("5"), Decimal("10"), "environment")


@pytest.mark.anyio
async def test_a_runtime_row_overrides_the_defaults() -> None:
    at = dt.datetime(2026, 9, 26, 18, 0, tzinfo=dt.UTC)
    row = {
        "monthly_budget_usd": Decimal("50"),
        "per_request_max_usd": Decimal("10"),
        "updated_at": at,
        "updated_by": "mcp",
        "reason": "4x4090 batch",
    }
    limits = await read_limits(
        _Conn(row), default_monthly=Decimal("5"), default_per_request=Decimal("10")
    )
    assert (limits.monthly_budget_usd, limits.source, limits.reason) == (
        Decimal("50"),
        "runtime",
        "4x4090 batch",
    )
    assert limits.to_dict()["monthly_budget_usd"] == "50"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("monthly", "per_request", "reason", "message"),
    [
        (None, None, "why", "at least one"),
        (Decimal("0"), None, "why", "positive"),
        (None, Decimal("-1"), "why", "positive"),
        (Decimal("50"), None, "   ", "reason"),
    ],
)
async def test_invalid_changes_are_refused_before_any_write(
    monthly: Any, per_request: Any, reason: str, message: str
) -> None:
    class _NoPool:
        def acquire(self) -> Any:
            raise AssertionError("no database access for an invalid change")

    with pytest.raises(BudgetLimitsError, match=message):
        await set_limits(
            _NoPool(),
            monthly_budget_usd=monthly,
            per_request_max_usd=per_request,
            reason=reason,
            actor="test",
        )
```

- [x] **Step 3: Write the failing integration tests** (`tests/integration/test_budget_limits_integration.py`)

```python
"""Runtime budget limits against real Postgres: persistence, audit, and lock ordering."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pitwall.cost.budget_limits import budget_status, effective_limits, set_limits

pytestmark = pytest.mark.integration


async def test_set_limits_persists_audits_and_keeps_the_other_value(pg_pool) -> None:  # type: ignore[no-untyped-def]
    before = await effective_limits(
        pg_pool, default_monthly=Decimal("5"), default_per_request=Decimal("10")
    )
    assert before.source == "environment"
    after = await set_limits(
        pg_pool,
        monthly_budget_usd=Decimal("50"),
        per_request_max_usd=None,
        reason="4x4090 batch",
        actor="mcp",
    )
    assert (after.monthly_budget_usd, after.per_request_max_usd, after.source) == (
        Decimal("50"),
        Decimal("10"),
        "runtime",
    )
    again = await effective_limits(
        pg_pool, default_monthly=Decimal("5"), default_per_request=Decimal("10")
    )
    assert again.monthly_budget_usd == Decimal("50")
    async with pg_pool.acquire() as conn:
        audit = await conn.fetchrow(
            "SELECT actor, action, entity_id, change_reason, old_value, new_value FROM pitwall.config_audit"
            " WHERE entity_type = 'budget_limits' ORDER BY id DESC LIMIT 1"
        )
    assert (audit["actor"], audit["action"], audit["entity_id"], audit["change_reason"]) == (
        "mcp",
        "budget_limits.set",
        "global",
        "4x4090 batch",
    )
    assert (
        audit["old_value"]["monthly_budget_usd"] == "5"
        and audit["new_value"]["monthly_budget_usd"] == "50"
    )


async def test_budget_status_reports_limits_and_spend(pg_pool, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "5")
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "10")
    status = await budget_status(pg_pool)
    assert status["monthly_budget_usd"] == "5" and status["source"] == "environment"
    assert status["mtd_spend_usd"] == "0" and status["budget_remaining_usd"] == "5"
```

If `config_audit` orders by a column other than `id`, order by its timestamp column instead (read `db/migrations` for the table). If `environment_defaults()` reads cached settings, clear the settings cache in the second test (`get_settings.cache_clear()`) after setting the environment.

- [x] **Step 4: Run to verify failure**

Run: `uv run pytest tests/cost/test_budget_limits.py -q 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'pitwall.cost.budget_limits'`.

- [x] **Step 5: Implement** (`src/pitwall/cost/budget_limits.py`)

```python
"""Runtime budget limits: one audited database row that overrides the environment defaults.

Every budget reader resolves limits through this module at use time, so a change takes
effect on the next admission without restarting any process.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

BUDGET_LIMITS_SQL = (
    "SELECT monthly_budget_usd, per_request_max_usd, updated_at, updated_by, reason"
    " FROM pitwall.budget_limits WHERE id = 1"
)
_UPSERT_SQL = """
    INSERT INTO pitwall.budget_limits (id, monthly_budget_usd, per_request_max_usd, updated_at, updated_by, reason)
    VALUES (1, $1, $2, now(), $3, $4)
    ON CONFLICT (id) DO UPDATE SET
        monthly_budget_usd = EXCLUDED.monthly_budget_usd,
        per_request_max_usd = EXCLUDED.per_request_max_usd,
        updated_at = EXCLUDED.updated_at,
        updated_by = EXCLUDED.updated_by,
        reason = EXCLUDED.reason
    RETURNING monthly_budget_usd, per_request_max_usd, updated_at, updated_by, reason
"""


class BudgetLimitsError(ValueError):
    error_code = "invalid_budget_limits"
    status_code = 422


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    monthly_budget_usd: Decimal
    per_request_max_usd: Decimal
    source: Literal["runtime", "environment"]
    updated_at: dt.datetime | None = None
    updated_by: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "monthly_budget_usd": _text(self.monthly_budget_usd),
            "per_request_max_usd": _text(self.per_request_max_usd),
            "source": self.source,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
            "reason": self.reason,
        }


def _text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _from_row(row: Any) -> BudgetLimits:
    return BudgetLimits(
        monthly_budget_usd=Decimal(str(row["monthly_budget_usd"])),
        per_request_max_usd=Decimal(str(row["per_request_max_usd"])),
        source="runtime",
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
        reason=row["reason"],
    )


async def read_limits(
    conn: Any, *, default_monthly: Decimal, default_per_request: Decimal
) -> BudgetLimits:
    row = await conn.fetchrow(BUDGET_LIMITS_SQL)
    if row is None:
        return BudgetLimits(default_monthly, default_per_request, "environment")
    return _from_row(row)


def environment_defaults() -> tuple[Decimal, Decimal]:
    from pitwall.config import get_settings

    settings = get_settings()
    return Decimal(str(settings.pitwall_monthly_budget_usd)), Decimal(
        str(settings.pitwall_per_request_max_usd)
    )


async def effective_limits(
    pool: Any,
    *,
    default_monthly: Decimal | None = None,
    default_per_request: Decimal | None = None,
) -> BudgetLimits:
    if default_monthly is None or default_per_request is None:
        env_monthly, env_per_request = environment_defaults()
        default_monthly = env_monthly if default_monthly is None else default_monthly
        default_per_request = (
            env_per_request if default_per_request is None else default_per_request
        )
    async with pool.acquire() as conn:
        return await read_limits(
            conn, default_monthly=default_monthly, default_per_request=default_per_request
        )


def _positive(value: Decimal | str | int | float, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BudgetLimitsError(f"{name} must be a positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise BudgetLimitsError(f"{name} must be a positive decimal")
    return parsed


async def set_limits(
    pool: Any,
    *,
    monthly_budget_usd: Decimal | str | None,
    per_request_max_usd: Decimal | str | None,
    reason: str,
    actor: str,
) -> BudgetLimits:
    if monthly_budget_usd is None and per_request_max_usd is None:
        raise BudgetLimitsError("set at least one of monthly_budget_usd or per_request_max_usd")
    monthly = (
        None if monthly_budget_usd is None else _positive(monthly_budget_usd, "monthly_budget_usd")
    )
    per_request = (
        None
        if per_request_max_usd is None
        else _positive(per_request_max_usd, "per_request_max_usd")
    )
    reason = (reason or "").strip()
    if not reason:
        raise BudgetLimitsError("a reason is required for every budget change")
    from pitwall.cost.budget_gate import PITWALL_BUDGET_LOCK_KEY
    from pitwall.db.repository import insert_audit

    env_monthly, env_per_request = environment_defaults()
    async with pool.acquire() as conn, conn.transaction():
        # Same lock as admission: a change and an admission never interleave.
        await conn.execute("SELECT pg_advisory_xact_lock($1)", PITWALL_BUDGET_LOCK_KEY)
        current = await read_limits(
            conn, default_monthly=env_monthly, default_per_request=env_per_request
        )
        row = await conn.fetchrow(
            _UPSERT_SQL,
            monthly if monthly is not None else current.monthly_budget_usd,
            per_request if per_request is not None else current.per_request_max_usd,
            actor,
            reason,
        )
        updated = _from_row(row)
        await insert_audit(
            pool,
            actor=actor,
            action="budget_limits.set",
            entity_type="budget_limits",
            entity_id="global",
            old_value=dict(current.to_dict()),
            new_value=dict(updated.to_dict()),
            change_reason=reason,
            conn=conn,
        )
    return updated


async def budget_status(pool: Any) -> dict[str, Any]:
    from pitwall.cost.budget_gate import MONTH_TO_DATE_SPEND_SQL

    env_monthly, env_per_request = environment_defaults()
    async with pool.acquire() as conn:
        limits = await read_limits(
            conn, default_monthly=env_monthly, default_per_request=env_per_request
        )
        row = await conn.fetchrow(MONTH_TO_DATE_SPEND_SQL)
    spend = Decimal(str(row["s"])) if row is not None else Decimal("0")
    remaining = max(limits.monthly_budget_usd - spend, Decimal("0"))
    return {
        **limits.to_dict(),
        "mtd_spend_usd": _text(spend),
        "budget_remaining_usd": _text(remaining),
    }
```

`_text` prints `Decimal("5.000000")` as `"5"`; the tests pin that form.

- [x] **Step 6: Run tests**

```bash
uv run pytest tests/cost/test_budget_limits.py tests/db -q -m "not integration and not slow and not live" 2>&1 | tail -3
PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/0 uv run --frozen pytest -m "integration and not live" tests/integration/test_budget_limits_integration.py tests/db 2>&1 | tail -5
```

Expected: unit `0 failed`; integration `0 failed` with both new tests run (not skipped).

- [x] **Step 7: Commit**

```bash
git add db/migrations/0036_budget_limits.sql src/pitwall/cost/budget_limits.py tests/cost/test_budget_limits.py tests/integration/test_budget_limits_integration.py tests/db/test_model_studio_migration.py
git commit -m "feat(budget): runtime budget limits in one audited row (migration 0036)"
```

---

### Task 4: The budget gate enforces the effective limits and can evaluate without admitting

**Files:**
- Modify: `src/pitwall/cost/budget_gate.py` — `_check_available_on_connection` (line 173), `try_launch_admission` (lines 243–252 removed), `_snapshot` (line 317); add `effective_limits`, `evaluate`, `BudgetVerdict`
- Test: `tests/integration/test_budget_gate_runtime_limits.py` (create); update fakes in `tests/cost/test_budget_gate.py` only if they fail on the new limits query

**Interfaces:**
- Consumes: Task 3 `read_limits`, `BudgetLimits`.
- Produces:
  - `BudgetGate.effective_limits(conn: Any | None = None) -> BudgetLimits` (async)
  - `BudgetGate.evaluate(estimate_usd: BudgetEstimateInput) -> BudgetVerdict` (async, read-only, no lock, no write)
  - `@dataclass(frozen=True) BudgetVerdict(admitted: bool, reason: BudgetRejectionReason | None, snapshot: BudgetSnapshot)` with `to_dict() -> dict[str, Any]` = `{"admitted", "reason", "snapshot": {…strings}}`
  - Constructor `monthly_budget_usd` / `per_request_max_usd` remain as the defaults a missing row falls back to.

- [x] **Step 1: Write the failing integration tests**

```python
"""The budget gate enforces runtime limits at admission time, without a restart."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pitwall.cost.budget_gate import BudgetGate, BudgetRejected
from pitwall.cost.budget_limits import set_limits

pytestmark = pytest.mark.integration


async def test_raising_the_monthly_budget_admits_what_the_environment_limit_rejected(
    pg_pool,
) -> None:  # type: ignore[no-untyped-def]
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("5"), per_request_max_usd=Decimal("10"))
    with pytest.raises(BudgetRejected) as rejected:
        await gate.try_launch(
            capability_id="runpod_direct", provider_id="runpod_direct", estimate_usd=Decimal("6")
        )
    assert rejected.value.reason == "monthly_budget"
    await set_limits(
        pg_pool,
        monthly_budget_usd=Decimal("50"),
        per_request_max_usd=None,
        reason="test",
        actor="test",
    )
    workload_id = await gate.try_launch(
        capability_id="runpod_direct", provider_id="runpod_direct", estimate_usd=Decimal("6")
    )
    assert workload_id.startswith("wkl_")


async def test_lowering_the_per_request_cap_takes_effect_on_the_same_gate(pg_pool) -> None:  # type: ignore[no-untyped-def]
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("50"), per_request_max_usd=Decimal("10"))
    await set_limits(
        pg_pool,
        monthly_budget_usd=None,
        per_request_max_usd=Decimal("1"),
        reason="test",
        actor="test",
    )
    with pytest.raises(BudgetRejected) as rejected:
        await gate.try_launch(
            capability_id="runpod_direct", provider_id="runpod_direct", estimate_usd=Decimal("2")
        )
    assert rejected.value.reason == "per_request_cap"
    assert rejected.value.snapshot.per_request_max_usd == Decimal("1")


async def test_evaluate_matches_admission_and_writes_nothing(pg_pool) -> None:  # type: ignore[no-untyped-def]
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("5"), per_request_max_usd=Decimal("10"))
    verdict = await gate.evaluate(Decimal("6"))
    assert (verdict.admitted, verdict.reason) == (False, "monthly_budget")
    assert verdict.to_dict()["snapshot"]["budget_remaining_usd"] == "5"
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM pitwall.workloads") == 0
    ok = await gate.evaluate(Decimal("1"))
    assert (ok.admitted, ok.reason) == (True, None)
```

(`to_dict()["snapshot"]` values use `BudgetSnapshot.to_serializable_dict()`; if that prints `5` as `"5.000000"` or `"5"`, assert the value it produces for `Decimal("5") - Decimal("0")` — compute the expected string with `str(Decimal("5"))`.)

- [x] **Step 2: Run to verify failure**

Run the integration command from Global Constraints on `tests/integration/test_budget_gate_runtime_limits.py`.
Expected: FAIL — the second admission is still rejected, and `evaluate` is missing.

- [x] **Step 3: Implement** in `budget_gate.py`

Add `from pitwall.cost.budget_limits import BudgetLimits, read_limits` to the imports. Add methods and the verdict type:

```python
@dataclass(frozen=True)
class BudgetVerdict:
    """What admission would decide right now, computed without the lock or any write."""

    admitted: bool
    reason: BudgetRejectionReason | None
    snapshot: BudgetSnapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason": self.reason,
            "snapshot": self.snapshot.to_serializable_dict(),
        }
```

```python
async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
    if conn is not None:
        return await read_limits(
            conn,
            default_monthly=self.monthly_budget_usd,
            default_per_request=self.per_request_max_usd,
        )
    async with self.pool.acquire() as owned:
        return await read_limits(
            owned,
            default_monthly=self.monthly_budget_usd,
            default_per_request=self.per_request_max_usd,
        )


async def evaluate(self, estimate_usd: BudgetEstimateInput) -> BudgetVerdict:
    estimate = _admission_cost(estimate_usd).ceiling_usd
    async with self.pool.acquire() as conn:
        limits = await self.effective_limits(conn)
        spend = _decimal_from_row(await conn.fetchrow(MONTH_TO_DATE_SPEND_SQL), "s")
    reason: BudgetRejectionReason | None = None
    if estimate > limits.per_request_max_usd:
        reason = "per_request_cap"
    elif spend + estimate > limits.monthly_budget_usd:
        reason = "monthly_budget"
    snapshot = self._snapshot(limits=limits, mtd_spend_usd=spend, estimate_usd=estimate)
    return BudgetVerdict(admitted=reason is None, reason=reason, snapshot=snapshot)
```

Replace `_check_available_on_connection` so both checks use the effective limits:

```python
    async def _check_available_on_connection(self, conn: Any, estimate: Decimal) -> None:
        limits = await self.effective_limits(conn)
        if estimate > limits.per_request_max_usd:
            log.warning("per-request cap exceeded: %.6f > %.6f", estimate, limits.per_request_max_usd)
            raise BudgetRejected(
                "per_request_cap",
                self._snapshot(limits=limits, mtd_spend_usd=Decimal("0"), estimate_usd=estimate),
            )
        row = await conn.fetchrow(MONTH_TO_DATE_SPEND_SQL)
        spend = _decimal_from_row(row, "s")
        if spend + estimate > limits.monthly_budget_usd:
            log.warning(
                "monthly budget would exceed under advisory lock: %.6f + %.6f > %.6f",
                spend,
                estimate,
                limits.monthly_budget_usd,
            )
            raise BudgetRejected(
                "monthly_budget",
                self._snapshot(limits=limits, mtd_spend_usd=spend, estimate_usd=estimate),
            )
```

Delete the pre-lock per-request check in `try_launch_admission` (lines 243–252): it used the constructor limits and would refuse a request a runtime raise allows. The check inside the lock (`check_available(estimate, _conn=conn)`) still rejects over-cap requests for keyless admissions, before any insert. Change `_snapshot` to take the limits:

```python
def _snapshot(
    self, *, limits: BudgetLimits, mtd_spend_usd: Decimal, estimate_usd: Decimal
) -> BudgetSnapshot:
    remaining = limits.monthly_budget_usd - mtd_spend_usd
    return BudgetSnapshot(
        monthly_budget_usd=limits.monthly_budget_usd,
        per_request_max_usd=limits.per_request_max_usd,
        mtd_spend_usd=mtd_spend_usd,
        estimate_usd=estimate_usd,
        budget_remaining_usd=remaining if remaining > 0 else Decimal("0"),
    )
```

Add `"BudgetVerdict"` to `__all__`.

- [x] **Step 4: Run tests and repair test fakes**

```bash
uv run pytest tests/cost tests/routing tests/mcp tests/api -q -m "not integration and not live" 2>&1 | tail -5
```

A test fake connection that raises on the unknown `pitwall.budget_limits` query is updated to answer `BUDGET_LIMITS_SQL` with `None` (no runtime row), keeping every assertion unchanged. A test that expected a per-request rejection without any connection being acquired is updated to expect the same rejection reason after the lock (the refusal and its reason are unchanged). Then run the integration command on `tests/integration/test_budget_gate_runtime_limits.py tests/integration` and expect `0 failed`.

- [x] **Step 5: Commit**

```bash
git add src/pitwall/cost/budget_gate.py tests/integration/test_budget_gate_runtime_limits.py tests/cost tests/routing tests/mcp tests/api
git commit -m "feat(budget): the gate enforces runtime limits under its lock and can evaluate without admitting"
```

---

### Task 5: Every other budget reader uses the effective limits

**Files:**
- Modify: `src/pitwall/routing/production.py` — `_available_budget_limit` (line 1551), `_budget_rejection` (line 1597)
- Modify: `src/pitwall/cost/sub_budgets.py` — add `effective_limits` next to `monthly_budget_usd` (line 228)
- Modify: `src/pitwall/cost/billing_read.py` — line 136
- Modify: `src/pitwall/finops/burn_rate.py` — `read_configured_burn_rate` (line 486)
- Modify: `src/pitwall/reconciler/__init__.py` — `_budget_breach_escalation` (line 1775)
- Modify: `src/pitwall/audit/capability.py` — `_resolve_budget_state` (line 511)
- Test: `tests/cost/test_effective_limit_readers.py` (create)

**Interfaces:**
- Consumes: Task 4 `BudgetGate.effective_limits`; Task 3 `effective_limits(pool, …)`.
- Produces: no new names beyond `SubBudgetGate.effective_limits(conn=None)` (delegates to the wrapped gate). `doctor.py` keeps checking the environment values (a static configuration check that runs without a database); its output line gains ` (runtime limits, when set, override these)`.

- [x] **Step 1: Write the failing tests**

```python
"""Budget readers outside the gate use the runtime limits, not the environment values."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from pitwall.cost.budget_limits import BudgetLimits

RUNTIME = BudgetLimits(Decimal("50"), Decimal("12"), "runtime")


class _Gate:
    monthly_budget_usd = Decimal("5")
    per_request_max_usd = Decimal("10")

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("4.82")

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        return RUNTIME


@pytest.mark.anyio
async def test_routing_headroom_uses_runtime_limits() -> None:
    from pitwall.routing.production import ProductionRoutingService

    service = ProductionRoutingService.__new__(ProductionRoutingService)
    service._budget = _Gate()  # type: ignore[attr-defined]
    assert await service._available_budget_limit() == Decimal("12")
    rejection = await service._budget_rejection(Decimal("13"))
    assert (rejection.reason, rejection.snapshot.monthly_budget_usd) == (
        "per_request_cap",
        Decimal("50"),
    )


@pytest.mark.anyio
async def test_billing_reconciliation_uses_runtime_monthly_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.cost import billing_read

    class _Billing:
        client_balance_usd = Decimal("48.48")
        current_spend_per_hr_usd = Decimal("0")
        spend_limit_usd = Decimal("80")
        under_balance = False

    async def fake_snapshot(_client: Any) -> Any:
        return _Billing()

    monkeypatch.setattr(billing_read, "read_billing_snapshot", fake_snapshot)
    result = await billing_read.reconcile_with_budget(object(), _Gate())  # type: ignore[arg-type]
    assert result.pitwall_monthly_budget_usd == Decimal("50")
```

Append to `tests/integration/test_budget_breach_escalation.py` (it already defines `RecordingKillSwitch`, `_seed_spend`, and `_ctx`):

```python
async def test_breach_uses_the_runtime_monthly_budget(pg_pool: Any) -> None:
    from pitwall.cost.budget_limits import set_limits

    async with pg_pool.acquire() as conn:
        await _seed_spend(conn, "5.00")
    await set_limits(
        pg_pool,
        monthly_budget_usd=Decimal("50"),
        per_request_max_usd=None,
        reason="raise",
        actor="test",
    )
    kill_switch = RecordingKillSwitch(pg_pool)
    await _budget_breach_escalation(_ctx(pg_pool, kill_switch, mode="armed", budget="1.00"))
    assert (
        kill_switch.reasons == []
    )  # $5 spent is under the runtime $50 budget, although over the $1 setting
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cost/test_effective_limit_readers.py -q 2>&1 | tail -5` and the integration command on `tests/integration/test_budget_breach_escalation.py`
Expected: FAIL — headroom `Decimal('0.18')` and monthly budget `Decimal('5')` (unit); the breach test fails because the kill switch fires at the $1 setting (integration).

- [x] **Step 3: Implement**

`routing/production.py`:

```python
    async def _available_budget_limit(self) -> Decimal:
        limits = await self._budget.effective_limits()
        spent = await self._budget.current_mtd_spend()
        monthly_remaining = limits.monthly_budget_usd - spent
        if monthly_remaining <= 0:
            return Decimal("0")
        return min(limits.per_request_max_usd, monthly_remaining)
```

and in `_budget_rejection`, read `limits = await self._budget.effective_limits()` first and use `limits.per_request_max_usd` / `limits.monthly_budget_usd` in place of the `self._budget.*` attributes (four places, lines 1600–1610).

`cost/sub_budgets.py` (beside the `monthly_budget_usd` property):

```python
    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        """Delegate to the wrapped gate so runtime limits apply through sub-budgets too."""
        return await self._budget_gate.effective_limits(conn)
```

`cost/billing_read.py` line 136: `monthly_budget = (await budget_gate.effective_limits()).monthly_budget_usd`.

`finops/burn_rate.py` `read_configured_burn_rate`:

```python
from pitwall.cost.budget_limits import effective_limits

limits = await effective_limits(pool, default_monthly=configured_monthly_budget_usd())
return await read_burn_rate(
    pool, budget_usd=limits.monthly_budget_usd, now=now, window_days=window_days
)
```

`reconciler/__init__.py` `_budget_breach_escalation`: after constructing `gate`, replace `budget = settings.pitwall_monthly_budget_usd` usage with `budget = (await gate.effective_limits()).monthly_budget_usd` (keep constructing the gate with the settings values as defaults).

`audit/capability.py` `_resolve_budget_state`: when `self._pool is not None`, resolve `limits = await effective_limits(self._pool, default_monthly=monthly_budget, default_per_request=per_request_cap)` and use its two values in the returned `BudgetState`.

`doctor.py` line 629–630 context: append the note to the line that prints the two values.

- [x] **Step 4: Run tests**

```bash
uv run pytest tests/cost tests/routing tests/reconciler tests/finops tests/audit -q -m "not integration and not live" 2>&1 | tail -3
```

and the integration command on `tests/integration/test_budget_breach_escalation.py`. Expected: `0 failed` for both (fakes updated only as Task 4 Step 4 allows: add an `effective_limits` coroutine returning environment-sourced limits built from the fake's own attributes).

- [x] **Step 5: Commit**

```bash
git add src/pitwall/routing/production.py src/pitwall/cost/sub_budgets.py tests/integration/test_budget_breach_escalation.py src/pitwall/cost/billing_read.py src/pitwall/finops/burn_rate.py src/pitwall/reconciler/__init__.py src/pitwall/audit/capability.py src/pitwall/doctor.py tests
git commit -m "feat(budget): routing, reconciliation, burn rate, and breach checks read runtime limits"
```

---

### Task 6: Surfaces to read and change limits (CLI, admin REST, MCP)

**Files:**
- Create: `src/pitwall/cli_budget.py`; modify `src/pitwall/cli.py` (group dispatch near line 245, help text near line 791)
- Create: `src/pitwall/api/routes/budget.py`; modify `src/pitwall/api/app.py` (`include_router`, line 472–494 block)
- Create: `src/pitwall/mcp/tools/budget.py`; modify `src/pitwall/mcp/registry.py` (import, `TOOL_NAMES`, `assert len(TOOL_NAMES) == 81`, two `ToolSpec`s)
- Test: `tests/test_cli_budget.py`, `tests/api/test_budget_admin.py`, `tests/mcp/test_budget_tools.py`

**Interfaces:**
- Consumes: Task 3 `budget_status`, `set_limits`, `BudgetLimitsError`.
- Produces:
  - `pitwall budget show [--json]`; `pitwall budget set [--monthly USD] [--per-request USD] --reason TEXT [--json]` (exit 2 on `BudgetLimitsError`)
  - `GET /v1/admin/budget` → `budget_status`; `PUT /v1/admin/budget` body `{"monthly_budget_usd"?: str, "per_request_max_usd"?: str, "reason": str}` → `{"limits": …, "status": …}`; 422 `{"error":"invalid_budget_limits","detail": str}`; both behind the existing admin-secret middleware (`/v1/admin/*`)
  - MCP `pitwall_budget_status() -> dict`, `pitwall_budget_set(reason: str, monthly_budget_usd: str | None = None, per_request_max_usd: str | None = None) -> dict` (actor `"mcp"`)

- [x] **Step 1: Write the failing tests**

`tests/mcp/test_budget_tools.py`:

```python
"""MCP budget tools delegate to the budget-limits service and surface its refusals."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from mcp.shared.exceptions import McpError

from pitwall.cost.budget_limits import BudgetLimits, BudgetLimitsError
from pitwall.mcp.tools import budget as tools


@pytest.mark.anyio
async def test_status_returns_the_service_view(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_status(pool: Any) -> dict[str, Any]:
        return {"monthly_budget_usd": "5", "source": "environment", "mtd_spend_usd": "4.817201"}

    monkeypatch.setattr(tools, "get_pool", lambda: _async(object()))
    monkeypatch.setattr(tools, "budget_status", fake_status)
    assert (await tools.pitwall_budget_status())["monthly_budget_usd"] == "5"


@pytest.mark.anyio
async def test_set_passes_values_reason_and_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_set(pool: Any, **kwargs: Any) -> BudgetLimits:
        seen.update(kwargs)
        return BudgetLimits(
            Decimal("50"),
            Decimal("10"),
            "runtime",
            reason=kwargs["reason"],
            updated_by=kwargs["actor"],
        )

    async def fake_status(pool: Any) -> dict[str, Any]:
        return {"monthly_budget_usd": "50"}

    monkeypatch.setattr(tools, "get_pool", lambda: _async(object()))
    monkeypatch.setattr(tools, "set_limits", fake_set)
    monkeypatch.setattr(tools, "budget_status", fake_status)
    result = await tools.pitwall_budget_set(reason="4x4090 batch", monthly_budget_usd="50")
    assert seen == {
        "monthly_budget_usd": "50",
        "per_request_max_usd": None,
        "reason": "4x4090 batch",
        "actor": "mcp",
    }
    assert (
        result["limits"]["monthly_budget_usd"] == "50"
        and result["status"]["monthly_budget_usd"] == "50"
    )


@pytest.mark.anyio
async def test_invalid_change_is_a_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_set(pool: Any, **kwargs: Any) -> BudgetLimits:
        raise BudgetLimitsError("a reason is required for every budget change")

    monkeypatch.setattr(tools, "get_pool", lambda: _async(object()))
    monkeypatch.setattr(tools, "set_limits", fake_set)
    with pytest.raises(McpError) as caught:
        await tools.pitwall_budget_set(reason=" ", monthly_budget_usd="50")
    assert caught.value.error.data["error"] == "invalid_budget_limits"


async def _async(value: Any) -> Any:
    return value
```

`tests/api/test_budget_admin.py` (same app builder as `tests/api/test_admin_auth_matrix.py`); also add `("PUT", "/v1/admin/budget")` to that file's `_ADMIN_ROUTES`:

```python
"""Admin REST for runtime budget limits."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest

from pitwall.cost.budget_limits import BudgetLimits, BudgetLimitsError
from tests.api._contract_helpers import build_app

pytestmark = pytest.mark.anyio
_SECRET = "test-admin-secret"
_HEADERS = {"X-Pitwall-Secret": _SECRET}


async def _client(mod: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://test")


async def test_get_requires_the_admin_secret(clear_app_module) -> None:  # type: ignore[no-untyped-def]
    mod = build_app(secret=_SECRET)
    async with await _client(mod) as client:
        assert (await client.get("/v1/admin/budget")).status_code == 401


async def test_get_and_put_delegate_to_the_service(
    clear_app_module, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    mod = build_app(secret=_SECRET)
    from pitwall.api.routes import budget as route

    seen: dict[str, Any] = {}

    async def fake_status(pool: Any) -> dict[str, Any]:
        return {"monthly_budget_usd": "50", "source": "runtime"}

    async def fake_set(pool: Any, **kwargs: Any) -> BudgetLimits:
        seen.update(kwargs)
        return BudgetLimits(Decimal("50"), Decimal("10"), "runtime")

    monkeypatch.setattr(route, "budget_status", fake_status)
    monkeypatch.setattr(route, "set_limits", fake_set)
    async with await _client(mod) as client:
        got = await client.get("/v1/admin/budget", headers=_HEADERS)
        put = await client.put(
            "/v1/admin/budget",
            headers=_HEADERS,
            json={"monthly_budget_usd": "50", "reason": "batch"},
        )
    assert got.status_code == 200 and got.json()["monthly_budget_usd"] == "50"
    assert put.status_code == 200 and put.json()["limits"]["monthly_budget_usd"] == "50"
    assert seen == {
        "monthly_budget_usd": "50",
        "per_request_max_usd": None,
        "reason": "batch",
        "actor": "api:admin",
    }


async def test_invalid_change_is_422(clear_app_module, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    mod = build_app(secret=_SECRET)
    from pitwall.api.routes import budget as route

    async def fake_set(pool: Any, **kwargs: Any) -> BudgetLimits:
        raise BudgetLimitsError("monthly_budget_usd must be a positive decimal")

    monkeypatch.setattr(route, "set_limits", fake_set)
    async with await _client(mod) as client:
        put = await client.put(
            "/v1/admin/budget", headers=_HEADERS, json={"monthly_budget_usd": "0", "reason": "x"}
        )
    assert put.status_code == 422
    assert put.json() == {
        "error": "invalid_budget_limits",
        "detail": "monthly_budget_usd must be a positive decimal",
    }
```

`tests/test_cli_budget.py`:

```python
"""pitwall budget show|set call the budget-limits service."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from pitwall import cli_budget
from pitwall.cost.budget_limits import BudgetLimits


def test_set_requires_a_reason(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli_budget.cmd_budget(["set", "--monthly", "50"])
    assert exited.value.code == 2


def test_set_calls_the_service_and_prints_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    async def fake_pool() -> Any:
        return object()

    async def fake_set(pool: Any, **kwargs: Any) -> BudgetLimits:
        seen.update(kwargs)
        return BudgetLimits(
            Decimal("50"),
            Decimal("10"),
            "runtime",
            reason=kwargs["reason"],
            updated_by=kwargs["actor"],
        )

    async def fake_status(pool: Any) -> dict[str, Any]:
        return {
            "monthly_budget_usd": "50",
            "per_request_max_usd": "10",
            "source": "runtime",
            "mtd_spend_usd": "4.817201",
            "budget_remaining_usd": "45.182799",
        }

    monkeypatch.setattr(cli_budget, "get_pool", fake_pool)
    monkeypatch.setattr(cli_budget, "set_limits", fake_set)
    monkeypatch.setattr(cli_budget, "budget_status", fake_status)
    assert cli_budget.cmd_budget(["set", "--monthly", "50", "--reason", "batch", "--json"]) == 0
    assert seen == {
        "monthly_budget_usd": "50",
        "per_request_max_usd": None,
        "reason": "batch",
        "actor": "cli",
    }
    assert json.loads(capsys.readouterr().out)["monthly_budget_usd"] == "50"
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/mcp/test_budget_tools.py tests/test_cli_budget.py tests/api/test_budget_admin.py -q 2>&1 | tail -3`
Expected: `ModuleNotFoundError`s.

- [x] **Step 3: Implement**

`src/pitwall/mcp/tools/budget.py`:

```python
"""Budget limit tools: read the effective limits and change them without a restart.

Every change needs a reason and is written to config_audit by the service layer.
"""

from __future__ import annotations

from typing import Any

from pitwall.cost.budget_limits import BudgetLimitsError, budget_status, set_limits
from pitwall.db import get_pool
from pitwall.mcp.error_adapter import adapt_error


async def pitwall_budget_status() -> dict[str, Any]:
    return await budget_status(await get_pool())


async def pitwall_budget_set(
    reason: str,
    monthly_budget_usd: str | None = None,
    per_request_max_usd: str | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    try:
        limits = await set_limits(
            pool,
            monthly_budget_usd=monthly_budget_usd,
            per_request_max_usd=per_request_max_usd,
            reason=reason,
            actor="mcp",
        )
    except BudgetLimitsError as exc:
        raise adapt_error(exc) from exc
    return {"limits": limits.to_dict(), "status": await budget_status(pool)}
```

Register `"invalid_budget_limits": VALIDATION` in `mcp/error_codes.py` `_CODE_MAP`. In `registry.py` import both handlers, add both names to `TOOL_NAMES`, change the assert to `81`, and add:

```python
(
    ToolSpec(
        name="pitwall_budget_status",
        description="Return the effective monthly budget and per-request cap (runtime or environment), month-to-date spend, and remaining budget.",
        handler=pitwall_budget_status,
    ),
)
(
    ToolSpec(
        name="pitwall_budget_set",
        description=(
            "Change the monthly budget and/or per-request cap without a restart. A reason is required; "
            "the change is audited. Use it when a budget_rejected error names the limit you need raised."
        ),
        handler=pitwall_budget_set,
    ),
)
```

`src/pitwall/api/routes/budget.py`:

```python
"""Admin REST for runtime budget limits (behind the /v1/admin secret)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from pitwall.cost.budget_limits import BudgetLimitsError, budget_status, set_limits

router = APIRouter(tags=["budget"])


class BudgetLimitsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    monthly_budget_usd: str | None = None
    per_request_max_usd: str | None = None


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    return pool


@router.get("/v1/admin/budget")
async def get_budget(request: Request) -> dict[str, Any]:
    return await budget_status(_pool(request))


@router.put("/v1/admin/budget", response_model=None)
async def put_budget(request: Request, update: BudgetLimitsUpdate) -> dict[str, Any] | JSONResponse:
    pool = _pool(request)
    try:
        limits = await set_limits(
            pool,
            monthly_budget_usd=update.monthly_budget_usd,
            per_request_max_usd=update.per_request_max_usd,
            reason=update.reason,
            actor="api:admin",
        )
    except BudgetLimitsError as exc:
        return JSONResponse(
            status_code=exc.status_code, content={"error": exc.error_code, "detail": str(exc)}
        )
    return {"limits": limits.to_dict(), "status": await budget_status(pool)}
```

In `app.py` add `from pitwall.api.routes.budget import router as budget_router` with the other route imports and `app.include_router(budget_router)` after `app.include_router(cost_router)`.

`src/pitwall/cli_budget.py`:

```python
"""pitwall budget: show and change runtime budget limits without a restart."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from pitwall.cli_output import Output, add_json_argument, json_mode
from pitwall.cost.budget_limits import BudgetLimitsError, budget_status, set_limits
from pitwall.db import get_pool


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall budget", description="Show or change the runtime budget limits."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    show = subcommands.add_parser(
        "show", help="Show effective limits, month-to-date spend, and remaining budget."
    )
    add_json_argument(show)
    change = subcommands.add_parser(
        "set", help="Change the monthly budget and/or per-request cap (audited)."
    )
    change.add_argument("--monthly", dest="monthly", help="Monthly budget in USD")
    change.add_argument("--per-request", dest="per_request", help="Per-request cap in USD")
    change.add_argument(
        "--reason", required=True, help="Why the limit changes (recorded in config_audit)"
    )
    add_json_argument(change)
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    pool = await get_pool()
    if args.command == "set":
        await set_limits(
            pool,
            monthly_budget_usd=args.monthly,
            per_request_max_usd=args.per_request,
            reason=args.reason,
            actor="cli",
        )
    return await budget_status(pool)


def cmd_budget(argv: list[str]) -> int:
    args = _parse_args(argv)
    output = Output(json_mode(args))
    try:
        status = asyncio.run(_run(args))
    except BudgetLimitsError as exc:
        output.print(f"pitwall budget: {exc}")
        output.emit()
        return 2
    if output.json_mode:
        output.set_json(status)
    else:
        output.print(
            f"Budget ({status['source']}): monthly {status['monthly_budget_usd']} USD, per-request cap "
            f"{status['per_request_max_usd']} USD, spent {status['mtd_spend_usd']} USD, remaining {status['budget_remaining_usd']} USD"
        )
    output.emit()
    return 0


__all__ = ["cmd_budget"]
```

In `cli.py` add `from pitwall.cli_budget import cmd_budget`, `if group == "budget": return cmd_budget(rest)` beside the `cost` branch, and a help line `"  pitwall budget show|set     Show or change budget limits without a restart"`.

- [x] **Step 4: Run tests**

```bash
uv run pytest tests/mcp tests/api tests/test_cli_budget.py -q -m "not integration and not live" 2>&1 | tail -3
```

Expected: `0 failed`. Tests that pin the tool count or the registry name set are updated to 81 with the two new names.

- [x] **Step 5: Commit**

```bash
git add src/pitwall/cli_budget.py src/pitwall/cli.py src/pitwall/api/routes/budget.py tests/api/test_admin_auth_matrix.py src/pitwall/api/app.py src/pitwall/mcp/tools/budget.py src/pitwall/mcp/registry.py src/pitwall/mcp/error_codes.py tests
git commit -m "feat(budget): show and change limits from the CLI, admin API, and MCP"
```

---

### Task 7: Raw-pod previews report the budget verdict

**Files:**
- Modify: `src/pitwall/api/leases/launch.py` — add `preview_raw_pod_lease_budget` after `admit_raw_pod_lease` (line 1060)
- Modify: `src/pitwall/mcp/tools/runpod_resources.py` — preview branch of `pitwall_runpod_create_pod` (line 147)
- Test: `tests/mcp/test_runpod_create_pod_lease.py` (add), `tests/integration/test_raw_pod_preview_budget.py` (create)

**Interfaces:**
- Consumes: Task 4 `BudgetGate.evaluate`, `BudgetVerdict.to_dict`.
- Produces: `async preview_raw_pod_lease_budget(pool, *, ttl_minutes: int, max_cost_per_hour: Decimal | None, budget_gate: Any | None = None) -> dict[str, Any]`; the preview result gains `"budget": {"admitted": bool, "reason": str | None, "snapshot": {…}, "estimate_basis": "ttl_minutes x max_cost_per_hour"}` (or `"ttl_minutes x 0.50 USD/hour default"` without a rate), plus `"remedy"` when not admitted.

- [x] **Step 1: Write the failing tests**

Unit (append to `tests/mcp/test_runpod_create_pod_lease.py`, reusing its `StubServiceWithTracking` and `PodCreateRequest` construction):

```python
async def test_preview_carries_the_budget_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    service = StubServiceWithTracking()

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    async def fake_pool() -> Any:
        return object()

    async def fake_preview(pool: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"ttl_minutes": 240, "max_cost_per_hour": Decimal("0.49")}
        return {
            "admitted": False,
            "reason": "monthly_budget",
            "snapshot": {"budget_remaining_usd": "0.182799"},
        }

    monkeypatch.setattr(runpod_resources, "_service", fake_service)
    monkeypatch.setattr(runpod_resources, "get_pool", fake_pool)
    monkeypatch.setattr(runpod_resources, "preview_raw_pod_lease_budget", fake_preview)
    request = PodCreateRequest(
        intent="preview",
        idempotency_key="mcp-preview-budget-1",
        name="p",
        image="example/image:1",
        gpu_type_ids=["NVIDIA A40"],
        ttl_minutes=240,
        max_cost_per_hour=Decimal("0.49"),
    )
    result = await runpod_resources.pitwall_runpod_create_pod(request)
    assert result["budget"]["admitted"] is False
    assert result["budget"]["reason"] == "monthly_budget"
    assert "pitwall_budget_set" in result["budget"]["remedy"]
```

Integration (`tests/integration/test_raw_pod_preview_budget.py`):

```python
"""A raw-pod preview and the admission after it agree."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pitwall.api.leases.launch import admit_raw_pod_lease, preview_raw_pod_lease_budget
from pitwall.cost.budget_gate import BudgetGate, BudgetRejected

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(("ttl", "admitted"), [(150, True), (1000, False)])
async def test_preview_verdict_matches_apply_outcome(pg_pool, ttl: int, admitted: bool) -> None:  # type: ignore[no-untyped-def]
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("5"), per_request_max_usd=Decimal("10"))
    preview = await preview_raw_pod_lease_budget(
        pg_pool, ttl_minutes=ttl, max_cost_per_hour=Decimal("0.49"), budget_gate=gate
    )
    assert preview["admitted"] is admitted
    if admitted:
        admission = await admit_raw_pod_lease(
            pg_pool,
            ttl_minutes=ttl,
            max_cost_per_hour=Decimal("0.49"),
            idempotency_key=f"agree-{ttl}",
            budget_gate=gate,
        )
        assert admission.is_new
    else:
        with pytest.raises(BudgetRejected):
            await admit_raw_pod_lease(
                pg_pool,
                ttl_minutes=ttl,
                max_cost_per_hour=Decimal("0.49"),
                idempotency_key=f"agree-{ttl}",
                budget_gate=gate,
            )
```

(150 minutes at $0.49/h = $1.225 fits $5; 1000 minutes = $8.17 exceeds the $5 monthly budget and fits the $10 cap, so it is refused as `monthly_budget`.)

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/mcp/test_runpod_create_pod_lease.py -q -k budget_verdict 2>&1 | tail -3`
Expected: FAIL — `AttributeError: … has no attribute 'preview_raw_pod_lease_budget'`.

- [x] **Step 3: Implement**

`launch.py`:

```python
async def preview_raw_pod_lease_budget(
    pool: Any,
    *,
    ttl_minutes: int,
    max_cost_per_hour: Decimal | None,
    budget_gate: Any | None = None,
) -> dict[str, Any]:
    """What the raw-pod admission would decide for this TTL and rate, without reserving budget."""
    estimate = estimate_raw_pod_lease_cost(ttl_minutes, max_cost_per_hour)
    gate = budget_gate if budget_gate is not None else BudgetGate(pool)
    verdict = (await gate.evaluate(estimate)).to_dict()
    verdict["estimate_basis"] = (
        "ttl_minutes x max_cost_per_hour"
        if max_cost_per_hour is not None
        else "ttl_minutes x 0.50 USD/hour default"
    )
    return verdict
```

`runpod_resources.py`: import `preview_raw_pod_lease_budget` from `pitwall.api.leases.launch` (the same module `admit_raw_pod_lease` comes from) and `BUDGET_REMEDY` from `pitwall.mcp.safe_boundary`; replace the preview branch:

```python
    if request.dry_run:
        result = _dump(await _call(lambda service: service.create_pod(request), mutation=False))
        budget = await preview_raw_pod_lease_budget(
            await get_pool(), ttl_minutes=ttl, max_cost_per_hour=request.max_cost_per_hour
        )
        if not budget.get("admitted"):
            budget["remedy"] = BUDGET_REMEDY
        result["budget"] = budget
        return result
```

- [x] **Step 4: Run tests**

```bash
uv run pytest tests/mcp -q 2>&1 | tail -3
```

then the integration command on `tests/integration/test_raw_pod_preview_budget.py`. Expected: `0 failed` for both. The no-business-logic guard still passes (the tool imports from `pitwall.api.leases.launch`, not `pitwall.cost.budget_gate`).

- [x] **Step 5: Commit**

```bash
git add src/pitwall/api/leases/launch.py src/pitwall/mcp/tools/runpod_resources.py tests/mcp/test_runpod_create_pod_lease.py tests/integration/test_raw_pod_preview_budget.py
git commit -m "feat(runpod): raw-pod previews report the budget verdict the apply will get"
```

---

### Task 8: A restart-tolerant MCP relay

**Files:**
- Create: `src/pitwall/mcp/relay.py`
- Modify: `src/pitwall/cli.py` — `_parse_mcp_serve_args` (line 2640, add a `relay` subparser), `cmd_mcp_serve` (line 2718, dispatch), help text (line 791)
- Test: `tests/mcp/test_relay.py`

**Interfaces:**
- Produces: `pitwall mcp relay -- COMMAND [ARGS…]`; `run_relay(command: Sequence[str]) -> int`; `python -m pitwall.mcp.relay -- COMMAND…`; error code `-32004` with `data.error` `mcp_server_restarted` (request was in flight when the server died) or `mcp_server_unavailable` (server not ready within `PITWALL_MCP_RELAY_WAIT_SECONDS`, default 30).

- [x] **Step 1: Write the failing tests** (`tests/mcp/test_relay.py`)

```python
"""The relay keeps the harness connected across server restarts."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

FAKE_SERVER = r"""
import json, os, sys
count_file = sys.argv[1]
n = (int(open(count_file).read()) if os.path.exists(count_file) else 0) + 1
open(count_file, "w").write(str(n))
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "crash":
        os._exit(3)
    if "id" in msg and "method" in msg:
        result = {"generation": n, "method": msg["method"]}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
"""


class _Client:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc

    async def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def recv(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=20)
        return json.loads(line)


async def _relay(tmp_path: Path, *server: str, wait: str = "10") -> _Client:
    env = {**os.environ, "PITWALL_MCP_RELAY_WAIT_SECONDS": wait}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pitwall.mcp.relay",
        "--",
        *server,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=env,
    )
    return _Client(proc)


@pytest.mark.anyio
async def test_relay_replays_initialize_and_fails_in_flight_request(tmp_path: Path) -> None:
    server = tmp_path / "server.py"
    server.write_text(FAKE_SERVER)
    count = tmp_path / "count"
    client = await _relay(tmp_path, sys.executable, str(server), str(count))
    await client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert (await client.recv())["result"] == {"generation": 1, "method": "initialize"}
    await client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    await client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert (await client.recv())["result"]["generation"] == 1
    await client.send({"jsonrpc": "2.0", "id": 3, "method": "crash"})
    failed = await client.recv()
    assert failed["id"] == 3 and failed["error"]["data"] == {"error": "mcp_server_restarted"}
    await client.send({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    after = await client.recv()
    assert (
        after["id"] == 4 and after["result"]["generation"] == 2
    )  # replayed initialize was not forwarded
    assert count.read_text() == "2"
    assert client.proc.stdin is not None
    client.proc.stdin.close()
    assert await asyncio.wait_for(client.proc.wait(), timeout=10) == 0


@pytest.mark.anyio
async def test_unstartable_server_answers_requests_with_unavailable(tmp_path: Path) -> None:
    client = await _relay(tmp_path, str(tmp_path / "missing-binary"), wait="0.5")
    await client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    failed = await client.recv()
    assert failed["id"] == 1 and failed["error"]["data"] == {"error": "mcp_server_unavailable"}
    assert client.proc.stdin is not None
    client.proc.stdin.close()
    assert await asyncio.wait_for(client.proc.wait(), timeout=10) == 0
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/mcp/test_relay.py -q 2>&1 | tail -5`
Expected: FAIL — `No module named pitwall.mcp.relay` (the relay process exits and `recv` times out or reads EOF).

- [x] **Step 3: Implement** (`src/pitwall/mcp/relay.py`)

```python
"""Restart-tolerant stdio relay for the Pitwall MCP server.

Harnesses start an MCP server once and do not reconnect when it dies. The broker's server
runs inside the API container (``docker exec … pitwall mcp serve``), so every container
restart used to end every agent's broker connection. The relay stays up for the harness,
restarts the server command when it exits, replays the MCP initialize handshake, and answers
requests that were in flight with a retryable error.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

ERROR_CODE = -32004
_REPLAY_ID = "pitwall-relay-replay"
_LIMIT = 64 * 1024 * 1024


def _key(request_id: Any) -> str:
    return json.dumps(request_id, sort_keys=True)


class Relay:
    def __init__(
        self,
        command: Sequence[str],
        *,
        write: Callable[[bytes], None],
        wait_seconds: float = 30.0,
        backoff: tuple[float, float] = (0.5, 10.0),
        log: Callable[[str], None] = lambda message: print(message, file=sys.stderr, flush=True),
    ) -> None:
        self._command = list(command)
        self._write = write
        self._wait_seconds = wait_seconds
        self._backoff = backoff
        self._log = log
        self._child: asyncio.subprocess.Process | None = None
        self._ready = asyncio.Event()
        self._closing = False
        self._init: dict[str, Any] | None = None
        self._initialized: dict[str, Any] | None = None
        self._pending: dict[str, Any] = {}
        self._replay: asyncio.Future[dict[str, Any]] | None = None

    def _emit(self, message: dict[str, Any]) -> None:
        self._write((json.dumps(message, separators=(",", ":")) + "\n").encode())

    def _fail(self, request_id: Any, error: str, message: str) -> None:
        self._emit(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": ERROR_CODE, "message": message, "data": {"error": error}},
            }
        )

    async def run(self, client: asyncio.StreamReader) -> int:
        supervisor = asyncio.create_task(self._supervise())
        try:
            while line := await client.readline():
                await self._from_client(line)
        finally:
            self._closing = True
            await self._stop_child()
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
        return 0

    async def _from_client(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self._log("pitwall mcp relay: dropped a non-JSON line from the client")
            return
        if not isinstance(message, dict):
            return
        method = message.get("method")
        if method == "initialize" and self._init is None:
            self._init = message
        elif method == "notifications/initialized":
            self._initialized = message
        is_request = method is not None and "id" in message
        if not self._ready.is_set():
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=self._wait_seconds)
            except TimeoutError:
                if is_request:
                    self._fail(
                        message["id"],
                        "mcp_server_unavailable",
                        "the Pitwall MCP server is not running; retry shortly",
                    )
                return
        child = self._child
        if child is None or child.stdin is None:
            if is_request:
                self._fail(
                    message["id"],
                    "mcp_server_unavailable",
                    "the Pitwall MCP server is not running; retry shortly",
                )
            return
        if is_request:
            self._pending[_key(message["id"])] = message["id"]
        child.stdin.write(line if line.endswith(b"\n") else line + b"\n")
        await child.stdin.drain()

    async def _from_child(self, child: asyncio.subprocess.Process) -> None:
        assert child.stdout is not None
        while line := await child.stdout.readline():
            try:
                message = json.loads(line)
            except ValueError:
                message = None
            if (
                isinstance(message, dict)
                and "method" not in message
                and message.get("id") == _REPLAY_ID
            ):
                if self._replay is not None and not self._replay.done():
                    self._replay.set_result(message)
                continue
            if isinstance(message, dict) and "method" not in message and "id" in message:
                self._pending.pop(_key(message["id"]), None)
            self._write(line if line.endswith(b"\n") else line + b"\n")

    async def _start(self) -> asyncio.subprocess.Process:
        child = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=_LIMIT,
        )
        return child

    async def _replay_handshake(self, child: asyncio.subprocess.Process) -> None:
        assert child.stdin is not None and self._init is not None
        self._replay = asyncio.get_running_loop().create_future()
        child.stdin.write((json.dumps({**self._init, "id": _REPLAY_ID}) + "\n").encode())
        await child.stdin.drain()
        await asyncio.wait_for(self._replay, timeout=self._wait_seconds)
        if self._initialized is not None:
            child.stdin.write((json.dumps(self._initialized) + "\n").encode())
            await child.stdin.drain()

    async def _supervise(self) -> None:
        loop = asyncio.get_running_loop()
        delay = self._backoff[0]
        while not self._closing:
            try:
                child = await self._start()
            except OSError as exc:
                self._log(f"pitwall mcp relay: cannot start the server: {exc}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._backoff[1])
                continue
            started = loop.time()
            self._child = child
            reader = asyncio.create_task(self._from_child(child))
            try:
                if self._init is not None:
                    await self._replay_handshake(child)
                self._ready.set()
                code = await child.wait()
            except TimeoutError, ConnectionError, BrokenPipeError:
                if child.returncode is None:
                    child.kill()
                code = await child.wait()
            finally:
                self._ready.clear()
                self._child = None
            await asyncio.gather(reader, return_exceptions=True)
            for request_id in list(self._pending.values()):
                self._fail(
                    request_id,
                    "mcp_server_restarted",
                    "the Pitwall MCP server restarted; retry the request",
                )
            self._pending.clear()
            if self._closing:
                return
            self._log(f"pitwall mcp relay: server exited with status {code}; restarting")
            if loop.time() - started > 30:
                delay = self._backoff[0]
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._backoff[1])

    async def _stop_child(self) -> None:
        child = self._child
        if child is None or child.returncode is not None:
            return
        if child.stdin is not None:
            child.stdin.close()
        try:
            await asyncio.wait_for(child.wait(), timeout=5)
        except TimeoutError:
            child.kill()
            await child.wait()


async def _main(command: Sequence[str]) -> int:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=_LIMIT)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    out = sys.stdout.buffer

    def write(data: bytes) -> None:
        out.write(data)
        out.flush()

    wait = float(os.environ.get("PITWALL_MCP_RELAY_WAIT_SECONDS", "30"))
    return await Relay(command, write=write, wait_seconds=wait).run(reader)


def run_relay(command: Sequence[str]) -> int:
    args = list(command)
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        print("usage: pitwall mcp relay -- COMMAND [ARGS...]", file=sys.stderr)
        return 64
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(run_relay(sys.argv[1:]))
```

In `cli.py` `_parse_mcp_serve_args`, add after the `serve` parser:

```python
    relay = subcommands.add_parser(
        "relay",
        help="Relay stdio to an MCP server command and restart it when it exits.",
        description="Keep a harness connected across broker restarts: pitwall mcp relay -- docker exec -i CONTAINER pitwall mcp serve",
    )
    relay.add_argument("server_command", nargs=argparse.REMAINDER, help="Server command after --")
```

and at the top of `cmd_mcp_serve`:

```python
    if args.command == "relay":
        from pitwall.mcp.relay import run_relay

        return run_relay(args.server_command)
```

Add the help line `"  pitwall mcp relay -- CMD     Keep MCP clients connected across broker restarts"`. Register `"mcp_server_restarted"` and `"mcp_server_unavailable"` as `UPSTREAM` in `mcp/error_codes.py` if its tests require every emitted code to be listed.

- [x] **Step 4: Run tests**

```bash
uv run pytest tests/mcp/test_relay.py -q -p no:randomly 2>&1 | tail -3
uv run pytest tests/mcp tests/test_cli*.py -q 2>&1 | tail -3
```

Expected: relay tests `2 passed`; suites `0 failed`.

- [x] **Step 5: Commit**

```bash
git add src/pitwall/mcp/relay.py src/pitwall/cli.py src/pitwall/mcp/error_codes.py tests/mcp/test_relay.py
git commit -m "feat(mcp): a relay that keeps harnesses connected across broker restarts"
```

---

### Task 9: Documentation, API baseline, release acceptance, full gates

**Files:**
- Create: `docs/operator/budget-limits.md`
- Modify: `docs/operator/model-studio.md` only if it references budget configuration; the MCP setup doc that shows the `docker exec` registration (`grep -rln "mcp serve" docs`) gains the relay form
- Modify: `CHANGELOG.md` (Unreleased), `docs/api/openapi-baseline.json` (regenerated), `release_acceptance/denominator.json` and `release_acceptance/reviewed-bindings.json` (through `bind_surfaces`), `.secrets.baseline` (only if the scan flags test values)

- [x] **Step 1: Write `docs/operator/budget-limits.md`**: the two limits and their environment defaults; the runtime row and that no restart is needed; `pitwall budget show|set`, `GET/PUT /v1/admin/budget` (with a curl example using `$PITWALL_ADMIN_SECRET` by name), MCP `pitwall_budget_status` / `pitwall_budget_set`; that every change needs a reason and is in `config_audit` (`pitwall audit` / `pitwall_audit_log` to review); what a `budget_rejected` payload contains and how to read `reason`; that raw-pod previews report `budget.admitted`; the reconciler breach check follows the runtime monthly budget.
- [x] **Step 2: Document the relay** in the MCP setup doc: `claude mcp add pitwall_broker -s user -- pitwall mcp relay -- docker exec -i <api-container> pitwall mcp serve --transport stdio`, what `mcp_server_restarted` / `mcp_server_unavailable` mean (retry), and `PITWALL_MCP_RELAY_WAIT_SECONDS`.
- [x] **Step 3: Changelog** (Unreleased): budget rejections explained over MCP; runtime budget limits (migration 0036) with CLI/admin API/MCP; raw-pod previews report the budget verdict; the pod view carries `public_ip` and `port_mappings`; `pitwall mcp relay`.
- [x] **Step 4: Regenerate and run the gates**

```bash
uv run python tools/ci/export_openapi.py --output docs/api/openapi-baseline.json && make openapi-check 2>&1 | tail -2
uv run python -m tools.release_acceptance.bind_surfaces 2>&1 | tail -5
uv run pytest tests/release_acceptance -q 2>&1 | tail -3
make docs-check 2>&1 | tail -2
git ls-files -z | xargs -0 -r -n 200 uv run python tools/guards/repo_text_policy.py; echo "policy exit=$?"
uv run python tools/security/check_secrets.py 2>&1 | tail -1
uv run ruff check src tests 2>&1 | tail -1 && uv run mypy src 2>&1 | tail -1
make test-fast 2>&1 | tail -3
```

then the integration command on `tests/integration tests/db`. Expected: the OpenAPI diff adds `/v1/admin/budget` (GET, PUT) and the two `PodResource` fields only; `bind_surfaces` reports 0 unmapped (new surfaces: `pitwall budget show|set` and arguments, `pitwall mcp relay`, two MCP tools, two REST operations, migration 0036); if `test_denominator` fails, update `release_acceptance/denominator.json` with the new counts and a reason naming these surfaces; every other command passes with `0 failed`.

- [x] **Step 5: Commit**

```bash
git add docs CHANGELOG.md release_acceptance .secrets.baseline
git commit -m "docs(budget): runtime limits, budget errors, previews, relay; release acceptance bindings"
```

---

### Task 10: Deploy to the local broker and prove each gap closed live

**Files:**
- Create: `docs/evidence/2026-09-26-broker-agent-gaps-live.md`
- Operator files changed (backed up first, never committed): `~/.local/bin/pitwall-services`, `~/.config/pitwall/deployment/durable-gateway.compose.override.yml`, `~/.local/bin/pitwall` symlink, the user-scope MCP registration `pitwall_broker`

The redeploy restarts the API container, which ends every current `docker exec` MCP session once. Before Step 3, message the connected clients that the broker restarts in about a minute, that SSH to leased pods is unaffected, and that lease TTLs keep running in the reconciler.

- [x] **Step 1: Merge the latest `feat/model-studio`** (the Live goal may have added commits): `git merge --no-edit feat/model-studio`, rerun `make test-fast 2>&1 | tail -3` → no failures.
- [x] **Step 2: Stage the release**

```bash
REL=~/.local/share/pitwall/releases/20260926-broker-gaps
git archive --format=tar HEAD | (mkdir -p "$REL" && tar -x -C "$REL")
(cd "$REL" && uv sync --frozen --python 3.14.7 >/tmp/broker-gaps-sync.log 2>&1 && echo synced)
cp ~/.local/bin/pitwall-services ~/.local/bin/pitwall-services.bak.20260926-broker-gaps
cp ~/.config/pitwall/deployment/durable-gateway.compose.override.yml ~/.config/pitwall/deployment/durable-gateway.compose.override.yml.bak.20260926-broker-gaps
sed -i 's#PITWALL_IMAGE_TAG=clean-20260920#PITWALL_IMAGE_TAG=broker-gaps-20260926#; s#releases/20260920-clean#releases/20260926-broker-gaps#' ~/.local/bin/pitwall-services
sed -i 's#releases/20260920-clean#releases/20260926-broker-gaps#' ~/.config/pitwall/deployment/durable-gateway.compose.override.yml
pitwall-services config >/dev/null && echo compose-ok
```

Expected: `synced`, `compose-ok`. The project name stays `pitwall`, so the database volume and data carry over.
- [x] **Step 3: Build and roll**

```bash
pitwall-services build 2>&1 | tail -3
pitwall-services up -d 2>&1 | tail -5
pitwall-services ps --format '{{.Service}} {{.Status}}'
docker exec pitwall-api-1 pitwall db status 2>&1 | tail -3
```

Expected: all services `healthy`; migrations through `0036_budget_limits` applied (use the migrate subcommand the CLI provides if `db status` is named differently).
- [x] **Step 4: Switch the host CLI and the MCP registration to the relay**

```bash
ln -sfn ~/.local/share/pitwall/releases/20260926-broker-gaps/.venv/bin/pitwall ~/.local/bin/pitwall
claude mcp remove pitwall_broker -s user
claude mcp add pitwall_broker -s user -- $HOME/.local/bin/pitwall mcp relay -- docker exec -i pitwall-api-1 pitwall mcp serve --transport stdio
claude mcp get pitwall_broker
```

- [x] **Step 5: Prove each gap closed** with a scripted MCP client through the relay (a short `uv run python` script using the `mcp` client library over stdio against the registered command), recording each result in the evidence doc:
  1. `pitwall_budget_status` → current limits (`source: environment`, monthly `5`), spend, remaining.
  2. `pitwall_runpod_create_pod` with `intent: preview`, TTL 1000 minutes, `max_cost_per_hour: 0.49` → `budget.admitted: false`, `reason: monthly_budget`, `remedy` present. No pod is created by a preview.
  3. The same request with `intent: apply` → error payload with `reason`, `snapshot`, `remedy`; confirm via `pitwall_runpod_list_pods` that no pod was created.
  4. `pitwall_runpod_get_pod` on a running raw pod (if one is still leased, else skip with the reason) → `public_ip` and `port_mappings["22"]` present.
  5. `pitwall_pod_logs` on that pod → log lines, not `volume_file_not_configured`.
  6. Relay: call `pitwall_health`; `docker restart pitwall-api-1`; call `pitwall_health` again → the first call after the restart succeeds after the relay replays the handshake (or returns `mcp_server_unavailable`/`mcp_server_restarted` once and succeeds on retry); the client never has to reconnect.
  7. Budget change round trip: do **not** change the limit here unless a requesting agent asks (the requester supplies the amount). Instead show `pitwall budget show` and that `pitwall budget set --monthly <current> --reason "verify audited no-op"` writes a `config_audit` row, then read it back with `pitwall audit` (or `pitwall_audit_log`).
- [x] **Step 6: Tell the clients**: message the connected clients that the broker is back, that `pitwall_budget_set` now changes limits without a restart (reason required, audited), that budget errors and previews explain themselves, and that `pitwall_runpod_get_pod` includes `public_ip` / `port_mappings`. Their harness still needs one `/mcp` reconnect (or a new session) to pick up the relay registration.
- [x] **Step 7: Commit the evidence**

```bash
git ls-files -z | xargs -0 -r -n 200 uv run python tools/guards/repo_text_policy.py; echo "policy exit=$?"
git add docs/evidence/2026-09-26-broker-agent-gaps-live.md
git commit -m "docs(evidence): broker agent gaps closed on the live local broker"
```

The evidence doc records commands, exit codes, and response shapes; public IPs appear only as `203.0.113.x` placeholders, and no secret, token, or account id appears.

## Continuation: review and live-check corrections (2026-09-26)

Tasks 1–8 were implemented by the GLM lane; Claude reviewed them, finished Task 9, and ran Task 10.

C1. Migration 0036 admitted a test-only `test` actor into `config_audit`; tests now use `cli` and the
schema admits only `api:admin` and `cli` beyond the existing actors (`e3d19fb`).

C2. Burn rate needs only the monthly budget, so it reads `effective_monthly_budget` instead of a
resolver that also required a per-request default; `PITWALL_MCP_RELAY_WAIT_SECONDS` falls back to 30
with a warning instead of crashing the relay and is bound as a config surface; unit, journey, and
conftest fakes answer the runtime-limits query with no row; the admin-secret gate test raises the
inbound rate limit (`90ceb6a`).

C3. Pre-existing release-tier failures fixed on the way: the tier tests look for the release marker
among the registered markers, and the sdist policy admits the five gateway files the wheel
force-includes (`90ceb6a`). The Model Studio branch was also missing `ruff format`; fixed there
(`f59e971`) and merged.

C4. The live pod view showed no address: the broker's RunPod client returns `runtime.ports` as
`{ip, private, public, type}`, a shape Task 1's tests did not cover. Fixed and redeployed (`038b377`);
evidence in `docs/evidence/2026-09-26-broker-agent-gaps-live.md` (`c587632`).

C5. Terminating a raw pod early (through `pitwall_runpod_terminate_pod` or at RunPod) left its lease
in `creating` and its reservation counted until TTL expiry; live, three terminated farm pods kept new
admissions refused. The reconciler now closes an unexpired `runpod_direct` lease whose pod is absent
or terminated (teardown reason `operator`, recorded `pod_absent`) and closes its workload at the
accrued cost (`754b086`); the CGNAT range is built from its integer form to satisfy the text policy
(`53e8658`). Live: both farm leases closed as `stopped`/`pod_absent` on the first tick after deploy.

C6. An unexplained single release-acceptance failure was traced, not waved off: reproduced under
40 CPU hogs plus fsync disk load with `--randomly-seed=202`, it is
`test_timeout_closes_pipes_when_descendant_escapes_owned_group` (and its sibling
`test_timeout_terminates_owned_process_group_and_retains_evidence` shares the race). Both gave the
recorded program a 0.1 s timeout, so on a loaded machine the timeout fired before the program had
started its escaped child or printed, and the test measured its own start-up race. The program now
signals readiness before it prints, the timeout is 3 s, and the test asserts its precondition;
under the same load seeds 202/303/404 pass and the two tests pass 10 of 10.
