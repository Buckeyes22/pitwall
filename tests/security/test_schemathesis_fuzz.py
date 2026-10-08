"""S7: schemathesis fuzz of the whole API — no server errors on any input.

Generates schema-derived and deliberately malformed requests from the live
``app.openapi()`` and drives the in-process ASGI app, asserting that no route
ever returns a 5xx — i.e. fuzzed/garbage input is always handled (4xx), never
crashes a handler. Two passes run: every operation without the admin secret
(secret-gated routes stay fuzzed on their 401 path), and every secret-gated
``/v1/admin/*`` operation with it, so those handlers run too.

Handlers run against hermetic doubles that answer like the real dependencies:
the asyncpg double echoes inserts, finds a stored provider or capability for
half of all keys, and refuses NUL / lone-surrogate parameters as Postgres does;
the real RunPod control-plane service runs over an in-memory backend. A 5xx
that surfaces here is therefore a genuine input-handling bug, not a double.

Scope notes:
  * Only the ``not_a_server_error`` check is asserted. The default
    ``response_schema_conformance`` check is intentionally separate from this
    security property. Contract conformance is covered by API schema tests; this
    lane proves every generated request terminates without a server error.

schemathesis 4.x API: ``schemathesis.openapi.from_asgi`` + a *synchronous*
``@schema.parametrize()`` test calling ``case.call_and_validate()``.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import os
import re
import sys
import warnings
import zlib
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import not_a_server_error
from schemathesis.python import asgi as schemathesis_asgi
from starlette_testclient import TestClient

import pitwall.api.app as _pitwall_api_app
from pitwall.api.admin import emergency
from pitwall.api.admin.kill_switch import KillReport, NoOpNetworkSever
from pitwall.db.repository import _capability_from_row, _provider_from_row
from pitwall.onboarding import OnboardingError, RunPodOnboardingService
from pitwall.resolver import CapabilityNotFoundError
from pitwall.routing.production import ProductionRoutingService
from pitwall.runpod_control_plane import EndpointGpuRequest, RunPodControlPlaneService
from pitwall.runpod_files import VolumeFileResult, VolumeFileService
from pitwall.runpod_market import RunpodMarketService
from tests.conftest import TEST_NOW, make_capability_row, make_provider_row
from tests.runpod_control_plane.backend_test_support import RecordingBackend
from tests.security.conftest import ADMIN_SECRET


class _ClosingTestClient(TestClient):
    """Close the second lifespan memory channel omitted by starlette-testclient 0.4.1."""

    def __exit__(self, *args: Any) -> None:
        stream_receive = self.stream_receive
        try:
            super().__exit__(*args)
        finally:
            # The upstream client closes stream_send in wait_shutdown but leaves
            # this independent channel open until cyclic GC. Close both halves
            # synchronously after the lifespan task has stopped.
            stream_receive.send_stream.close()
            stream_receive.receive_stream.close()


# Schemathesis up to 4.10 builds its ASGI transport on starlette-testclient, so
# swap in the closing subclass there. Later releases ship their own
# ``ASGIClient`` with a shared lifespan portal; its requests transport counts
# pooled connections through ``HTTPAdapter.poolmanager``, which the
# starlette-testclient adapter never initialises, so keep the upstream client.
if not hasattr(schemathesis_asgi, "ASGIClient"):
    schemathesis_asgi.get_client = _ClosingTestClient

pytestmark = [
    pytest.mark.security,
    pytest.mark.fuzz,
    # Schemathesis 4.10/httpx leaves its per-example AnyIO memory transport
    # streams for cyclic GC on Python 3.13.  Scope the upstream warning waiver
    # to this harness; project ResourceWarnings remain errors everywhere else.
    pytest.mark.filterwarnings("ignore:Unclosed <MemoryObject.*Stream.*:ResourceWarning"),
]


_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+[\w.]+\s*\(([^)]*)\)\s*VALUES\s*\((.*?)\)\s*(?:ON\s+CONFLICT|RETURNING)",
    re.IGNORECASE | re.DOTALL,
)
_PARAM_RE = re.compile(r"^\$(\d+)(::\w+)?$")
_LOOKUP_RE = re.compile(r"\s*SELECT \* FROM ([\w.]+) WHERE (id|name) = \$1\s*$")
_UPDATE_RE = re.compile(r"\s*UPDATE ([\w.]+) SET .* RETURNING \*\s*$", re.DOTALL)
_SEEDED_TABLES: dict[str, tuple[Callable[..., dict[str, Any]], Callable[[Any], object]]] = {
    "pitwall.providers": (make_provider_row, _provider_from_row),
    "pitwall.capabilities": (make_capability_row, _capability_from_row),
}


def _seeded_key_exists(key: object) -> bool:
    # Half of all generated ids/names "exist", decided by a stable hash, so the
    # fuzz reaches both the 404 branch and the post-lookup handler logic (patch
    # validation, enable/disable, audit) without any shared state between cases.
    return isinstance(key, str) and zlib.crc32(key.encode("utf-8", "surrogatepass")) % 2 == 0


def _inserted_row(sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
    """Echo ``INSERT ... (cols) VALUES ($n, ...) RETURNING *`` as the stored row."""

    match = _INSERT_RE.search(sql)
    if match is None or "RETURNING" not in sql.upper():
        return None
    columns = [column.strip() for column in match.group(1).split(",")]
    values = [value.strip() for value in match.group(2).split(",")]
    row: dict[str, Any] = {"id": 1, "created_at": TEST_NOW, "updated_at": TEST_NOW}
    for column, value in zip(columns, values, strict=False):
        param = _PARAM_RE.match(value)
        if param is not None and int(param.group(1)) <= len(args):
            row[column] = args[int(param.group(1)) - 1]
    return row


def _reject_like_postgres(value: object) -> None:
    """Fail the way asyncpg and Postgres fail on values text and jsonb cannot hold."""

    if isinstance(value, str):
        if "\x00" in value:
            raise asyncpg.exceptions.CharacterNotInRepertoireError(
                'invalid byte sequence for encoding "UTF8": 0x00'
            )
        value.encode("utf-8")  # lone surrogates raise UnicodeEncodeError, as in asyncpg
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_like_postgres(key)
            _reject_like_postgres(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_like_postgres(item)


class _FuzzConnection:
    """asyncpg connection double that answers the SQL the handlers issue.

    ``INSERT ... RETURNING`` echoes the inserted row (Postgres always returns
    one), provider and capability lookups find a seeded row for half of all
    keys, and everything else is an empty result, so a 5xx is never an artefact
    of the double. Parameters Postgres would refuse (NUL bytes, lone surrogates)
    raise the same errors a real connection raises.
    """

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        _reject_like_postgres(args)
        inserted = _inserted_row(sql, args)
        if inserted is not None:
            return inserted
        lookup = _LOOKUP_RE.match(sql)
        if lookup is not None and lookup.group(1) in _SEEDED_TABLES:
            if not args or not _seeded_key_exists(args[0]):
                return None
            factory, from_row = _SEEDED_TABLES[lookup.group(1)]
            row = factory(**{lookup.group(2): args[0]})
            try:
                from_row(row)
            except ValueError:
                return None  # the domain model refuses this key, so no stored row can carry it
            return row
        update = _UPDATE_RE.match(sql)
        if update is not None and update.group(1) in _SEEDED_TABLES:
            return _SEEDED_TABLES[update.group(1)][0]()
        return None

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        _reject_like_postgres(args)
        return []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        _reject_like_postgres(args)
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        _reject_like_postgres(args)
        return "SELECT 1"

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        yield

    def transaction(self, **_kwargs: Any) -> Any:
        return self._transaction()


class _FuzzPool(_FuzzConnection):
    """asyncpg pool double; the pool-level query helpers share the connection logic."""

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[_FuzzConnection]:
        yield _FuzzConnection()

    def acquire(self, **_kwargs: Any) -> Any:
        return self._acquire()


class _FuzzRunPodBackend(RecordingBackend):
    """In-memory RunPod account; GPU resolution accepts whatever types were asked for."""

    async def resolve_endpoint_gpu_types(
        self, gpu_type_ids: list[str], *, gpu_count: int
    ) -> EndpointGpuRequest:
        return EndpointGpuRequest(pools=["ADA_24"], excluded_type_ids=[], count=gpu_count)


class _FakeKillSwitch:
    """Stands in for CloudKillSwitch: never severs Tailscale or terminates pods."""

    def __init__(self, sever: Any, *, terminate_compute: bool = True) -> None:
        self._terminate_compute = terminate_compute

    async def activate(self, reason: str) -> KillReport:
        return KillReport(
            triggered_at=dt.datetime.now(dt.UTC),
            reason=reason,
            tailscale_acl_updated=False,
            devices_removed=0,
            pods_terminated=0,
            total_duration_ms=0,
            errors=[],
        )


@pytest.fixture(autouse=True, scope="module")
def _api_modules_the_app_was_built_from() -> Iterator[None]:
    """Pin the pitwall.api modules the fuzz app was built from.

    Other test modules purge ``pitwall.api*`` from sys.modules and re-import it,
    and a handler's lazy import (``create_routed_lease`` importing
    ``pitwall.api.routes.routing``) would then raise exception classes from a
    fresh module copy that this app's exception handlers do not match, turning a
    mapped 404 into a false 500.
    """
    with patch.dict(sys.modules):
        for name in [name for name in sys.modules if name.startswith("pitwall.api")]:
            del sys.modules[name]
        sys.modules.update(_API_MODULES)
        yield


@pytest.fixture(autouse=True)
def _hermetic_kill_switch() -> Iterator[None]:
    """Keep POST /v1/admin/kill-switch off the global pool, Tailscale, and RunPod.

    ``run_kill`` resolves its pool through ``pitwall.db.get_pool`` (not
    ``app.state.pool``) and builds the real ``CloudKillSwitch``, so the route has
    no app-state seam; patch those three module globals for the fuzz only.
    """

    async def _fuzz_pool() -> _FuzzPool:
        return _FuzzPool()

    with (
        patch.object(emergency, "get_pool", _fuzz_pool),
        patch.object(emergency, "CloudKillSwitch", _FakeKillSwitch),
        patch.object(emergency, "_network_sever_from_env", NoOpNetworkSever),
    ):
        yield


@pytest.fixture(autouse=True)
def _fresh_runpod_account() -> None:
    """Every fuzz test starts from the same in-memory RunPod account.

    The app is built once per module; without this, a fuzzed DELETE in one test leaves
    the account without its template, and whichever create test the worker runs next
    answers 502 when it reads the new template back.
    """
    _reset_runpod_account(_FUZZ_APP)


def _build_isolated_fuzz_app() -> Any:
    # Load a PRIVATE copy of app.py as a throwaway module, yielding a fresh,
    # fully isolated FastAPI instance. This shares nothing mutable with the
    # global app or sys.modules['pitwall.api.*'], so the fuzz can attach a fake
    # pool without leaking into other tests AND without a module-level purge that
    # would rebind sys.modules out from under other test files' top-level imports
    # (which previously made the kill-switch tests issue real calls). The route
    # functions still come from the shared, already-imported pitwall.api.routes.*
    # modules; only the app object + its state are private.
    spec = importlib.util.spec_from_file_location(
        "_pitwall_fuzz_app_private", _pitwall_api_app.__file__
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # The admin secret is configured so the authenticated pass reaches every
    # secret-gated handler while the public pass still proves the missing- and
    # wrong-secret 401 path. The inbound rate limiter is off: its 120/60s default
    # answered 429 to every request after the first 120, so the fuzz never reached
    # most handlers.
    with patch.dict(
        os.environ,
        {
            "PITWALL_API_TOKEN": "",
            "PITWALL_ADMIN_SECRET": ADMIN_SECRET,
            "PITWALL_INBOUND_RATE_LIMIT": "off",
        },
    ):
        spec.loader.exec_module(mod)
    mod.app.state.pool = _FuzzPool()
    mod.app.state.runpod_api_key = "test-key"
    mod.app.state.redis = _FakeRedis()
    _attach_hermetic_provider_services(mod.app)
    return mod.app


def _reset_runpod_account(app: Any) -> None:
    """Give the app a fresh in-memory RunPod account, so no fuzz test sees another's deletions."""
    app.state.runpod_control_plane_service = RunPodControlPlaneService(
        backend=_FuzzRunPodBackend(),
        audit_pool=app.state.pool,
        actor="rest:admin",
        environ={},
    )


def _attach_hermetic_provider_services(app: Any) -> None:
    """Keep schema-generated provider reads inside deterministic service doubles."""

    # The real control-plane service runs (id validation, pre-spend inspection,
    # dry-run, idempotency, audit, error mapping) over an in-memory RunPod backend.
    _reset_runpod_account(app)

    files = MagicMock(spec=VolumeFileService)
    file_results = {
        "list_objects": VolumeFileResult(operation="list", status="completed"),
        "read_object_chunk": VolumeFileResult(operation="download", status="completed"),
        "read_pod_logs": VolumeFileResult(operation="logs", status="completed"),
        "upload_bytes": VolumeFileResult(operation="upload", status="dry_run"),
        "delete_object": VolumeFileResult(operation="delete", status="dry_run"),
    }
    for name, result in file_results.items():
        setattr(files, name, AsyncMock(return_value=result))
    app.state.volume_file_service = files

    market = MagicMock(spec=RunpodMarketService)
    snapshot = MagicMock()
    snapshot.to_serializable_dict.return_value = {
        "observed_at": "2026-09-01T00:00:00Z",
        "source": "hermetic-fuzz",
        "gpu_types": [],
        "data_centers": [],
        "balance_usd": None,
        "billing_support": {},
    }
    market.read = AsyncMock(return_value=snapshot)
    app.state.runpod_market_service = market

    onboarding = RunPodOnboardingService(
        state=MagicMock(),
        resources=MagicMock(),
        discovery=MagicMock(),
        environ={},
    )
    onboarding.execute = AsyncMock(  # type: ignore[method-assign]  # reason: bounded failure fake
        side_effect=OnboardingError("guardrail_rejected", "fuzz request rejected")
    )
    app.state.runpod_onboarding_service = onboarding

    routing = MagicMock(spec=ProductionRoutingService)
    for name in (
        "preview",
        "preview_prepared",
        "plan_execution",
        "plan_prepared_execution",
        "execute_sync",
        "execute_sync_prepared",
        "capability_class",
        "submit_job",
        "submit_job_prepared",
    ):
        setattr(routing, name, AsyncMock(side_effect=CapabilityNotFoundError("fuzz.capability")))
    for name in ("get_job", "cancel_job", "job_events", "job_result"):
        setattr(routing, name, AsyncMock(side_effect=LookupError("fuzz workload")))
    app.state.production_routing_service = routing


def test_isolated_fuzz_app_loads_when_process_token_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITWALL_API_TOKEN", "dummy-token")
    assert _build_isolated_fuzz_app().openapi()["openapi"]


class _FakeRedis:
    async def ping(self) -> bool:
        return True


with warnings.catch_warnings():
    # FastAPI derives op-ids from function names → one benign duplicate warning.
    warnings.simplefilter("ignore")
    _FUZZ_APP = _build_isolated_fuzz_app()
    schema = schemathesis.openapi.from_asgi("/openapi.json", _FUZZ_APP)

# Captured after the app is built, so every module its routes import is included.
_API_MODULES = {
    name: module for name, module in sys.modules.items() if name.startswith("pitwall.api")
}


# Several public params carry a NUL-byte reject pattern (^[^\x00]+$, the
# null-byte hardening), so Hypothesis filters out a fair fraction of generated
# strings for some operations (notably POST /v1/inference). That legitimately
# trips the `filter_too_much` health check on unlucky seeds — flaky, and it fired
# in coverage-combined though the dedicated security-fuzz job passed. The filtering
# is expected and harmless (the property is just "no 5xx"), so suppress that one
# health check to make the gate deterministic.
@schema.parametrize()
@settings(suppress_health_check=[HealthCheck.filter_too_much], derandomize=True)
def test_public_api_no_server_errors(case: Any) -> None:
    # No X-Pitwall-Secret is sent (schemathesis may generate a random one from the
    # AdminSecret scheme), so secret-gated /v1/admin/* operations stay fuzzed on
    # their 401 path here; the authenticated pass below reaches their handlers.
    case.call_and_validate(checks=[not_a_server_error])


# Every operation behind AdminSecretMiddleware. RunPod GET/HEAD reads under
# /v1/admin/runpod/ bypass the secret by design (read scope only), so the public
# pass above already reaches their handlers; they are not fuzzed twice.
admin_schema = schema.include(path_regex=r"^/v1/admin(/|$)").exclude(
    method="GET", path_regex=r"^/v1/admin/runpod/"
)


@admin_schema.parametrize()
@settings(suppress_health_check=[HealthCheck.filter_too_much], derandomize=True)
def test_admin_api_no_server_errors_authenticated(case: Any) -> None:
    response = case.call_and_validate(
        headers={"X-Pitwall-Secret": ADMIN_SECRET}, checks=[not_a_server_error]
    )
    # Schemathesis' coverage phase deliberately drops the AdminSecret header on
    # some cases; any other 401 means the correct secret was rejected and the
    # case never reached its handler.
    if response.request.headers.get("X-Pitwall-Secret") == ADMIN_SECRET:
        assert response.status_code != 401, response.text


def test_template_create_after_an_earlier_fuzz_deletion_is_not_a_server_error() -> None:
    # A fuzzed DELETE marks the in-memory account's template gone; a later create then
    # cannot read its template back and answers 502. Each fuzz test gets a fresh account.
    app = _build_isolated_fuzz_app()
    app.state.runpod_control_plane_service._backend.template_present = False
    _reset_runpod_account(app)
    body: dict[str, Any] = {
        "args": [],
        "disk_gb": 50,
        "env": {},
        "idempotency_key": "00000000",
        "image": "0",
        "intent": "apply",
        "name": "0",
        "ports": [],
        "registry_auth_id": None,
        "serverless": False,
        "volume_gb": 0,
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/admin/runpod/templates", json=body, headers={"X-Pitwall-Secret": ADMIN_SECRET}
        )
    assert response.status_code < 500, response.text
