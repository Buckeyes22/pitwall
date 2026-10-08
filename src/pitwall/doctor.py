"""Installation readiness report for ``pitwall doctor`` and the ``pitwall_doctor`` MCP tool.

Answers "what works, what is missing, what next" for one Pitwall installation.
It never makes a paid call and never prints secret values: checks name
variables, not their contents. Real services are reached through
:class:`Probes`, which tests replace.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pitwall.cli.base_url import default_base_url
from pitwall.config import PitwallSettings
from pitwall.personal.routes import DEFAULT_ROUTING_CLI, routing_available

Status = Literal["ok", "warn", "fail", "skip"]
Mode = Literal["personal", "registry"]
SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_S = 5.0
CANARY_TEXT = "pitwall doctor canary"
_DEPENDENTS_OF_DB = (
    "db.migrations",
    "registry.capabilities",
    "registry.providers",
    "spend.kill_switch",
    "spend.burn_rate",
)


@dataclass(frozen=True)
class DoctorCheck:
    id: str
    phase: str
    status: Status
    detail: str
    next_step: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "phase": self.phase,
            "status": self.status,
            "detail": self.detail,
            "next_step": self.next_step,
        }


@dataclass(frozen=True)
class DoctorSection:
    """One named group of checks in the unified report (broker, agents, gateway, workbench)."""

    name: str
    checks: tuple[DoctorCheck, ...]

    @property
    def status(self) -> Status:
        statuses = {check.status for check in self.checks}
        if "fail" in statuses:
            return "fail"
        return "warn" if "warn" in statuses else "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
        }


_SECTIONS: dict[str, Callable[[], DoctorSection]] = {}


def register_doctor_section(name: str, check: Callable[[], DoctorSection]) -> None:
    """Add a section to ``pitwall doctor``. Registering a name twice is a programming error."""
    if name in _SECTIONS and _SECTIONS[name] is not check:
        raise ValueError(f"doctor section {name!r} is already registered")
    _SECTIONS[name] = check


def registered_sections() -> tuple[str, ...]:
    return tuple(_SECTIONS)


def run_registered_sections() -> tuple[DoctorSection, ...]:
    """Run every registered section; one that raises reports a failed check, not a crash."""
    sections: list[DoctorSection] = []
    for name, check in _SECTIONS.items():
        try:
            sections.append(check())
        except Exception as exc:  # reason: one broken section must not hide the others' results
            failure = DoctorCheck(
                f"{name}.section",
                name,
                "fail",
                f"the {name} checks did not run ({type(exc).__name__})",
                "fix the section's setup, then rerun pitwall doctor",
            )
            sections.append(DoctorSection(name, (failure,)))
    return tuple(sections)


_AGENT_STATUS: dict[str, Status] = {"PASS": "ok", "WARN": "warn", "FAIL": "fail", "SKIP": "skip"}


def agents_section() -> DoctorSection:
    """Agent Routing installation readiness, from the ``pitwall agents doctor`` checks."""
    import os

    from pitwall.agents.doctor import run_doctor as run_agents_doctor

    report = run_agents_doctor(None, os.environ, installation_only=True)
    checks = tuple(
        DoctorCheck(
            f"agents.{check['id']}",
            str(check["category"]),
            _AGENT_STATUS.get(str(check["status"]).upper(), "fail"),
            str(check["summary"]),
            check.get("remediation"),
        )
        for check in report["checks"]
    )
    return DoctorSection("agents", checks)


register_doctor_section("agents", agents_section)


def gateway_section() -> DoctorSection:
    """Free-tier gateway readiness, from the ``pitwall gateway doctor`` checks."""
    from pitwall.cli.gateway import gateway_section as run_gateway_section

    return run_gateway_section()


register_doctor_section("gateway", gateway_section)


def workbench_section() -> DoctorSection:
    """Pinned Pi toolchain and runtime readiness for the workbench (optional tooling)."""
    from pitwall.workbench.doctor import workbench_section as run_workbench_section

    return run_workbench_section()


register_doctor_section("workbench", workbench_section)


@dataclass(frozen=True)
class DoctorReport:
    mode: Mode
    version: str
    checks: tuple[DoctorCheck, ...]
    sections: tuple[DoctorSection, ...] = ()

    @property
    def all_checks(self) -> tuple[DoctorCheck, ...]:
        """The broker checks followed by every section's checks."""
        return (*self.checks, *(check for section in self.sections for check in section.checks))

    @property
    def status(self) -> Status:
        statuses = {check.status for check in self.all_checks}
        if "fail" in statuses:
            return "fail"
        return "warn" if "warn" in statuses else "ok"

    def exit_code(self, *, strict: bool = False) -> int:
        if self.status == "fail" or (strict and self.status == "warn"):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        summary = dict.fromkeys(("ok", "warn", "fail", "skip"), 0)
        for check in self.all_checks:
            summary[check.status] += 1
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "mode": self.mode,
            "version": self.version,
            "status": self.status,
            "summary": summary,
            "checks": [check.to_dict() for check in self.checks],
        }
        if self.sections:
            payload["sections"] = [
                DoctorSection("broker", self.checks).to_dict(),
                *(section.to_dict() for section in self.sections),
            ]
        return payload


@dataclass(frozen=True)
class BurnFacts:
    budget_usd: Decimal
    spend_to_date_usd: Decimal
    forecast_total_usd: Decimal | None
    # The budget gate's own month-to-date figure (``MONTH_TO_DATE_SPEND_SQL``). The burn rate
    # reads the daily rollup, which can miss spend; the gate's figure is what admission enforces.
    gate_spend_usd: Decimal | None = None
    stale: bool = False
    data_sufficiency: str = "sufficient"


@dataclass(frozen=True)
class DatabaseFacts:
    applied_versions: frozenset[str]
    schema_ready: bool
    enabled_capabilities: int = 0
    embedding_capabilities: tuple[str, ...] = ()
    enabled_providers: int = 0
    healthy_providers: int = 0
    # Serve providers with no active lease; excluded from enabled_providers.
    disarmed_providers: int = 0
    kill_switch_engaged: bool = False
    burn: BurnFacts | None = None
    # Class name of the exception that stopped the burn-rate read, when one did.
    burn_error: str | None = None


class ProbeError(RuntimeError):
    """A probe could not reach its service; the message never contains credentials."""


@dataclass(frozen=True)
class Probes:
    database: Callable[[str, float], Awaitable[DatabaseFacts]]
    redis: Callable[[str, float], Awaitable[None]]
    api_health: Callable[[str, str | None, float], Awaitable[dict[str, Any]]]
    canary: Callable[[str, str | None, str, float], Awaitable[dict[str, Any]]]


async def _database_probe(url: str, timeout: float) -> DatabaseFacts:
    import asyncpg

    from pitwall.cost.budget_gate import month_to_date_spend
    from pitwall.finops.burn_rate import read_configured_burn_rate

    try:
        pool = await asyncpg.create_pool(
            dsn=url,
            min_size=1,
            max_size=1,
            timeout=timeout,
            command_timeout=timeout,
            statement_cache_size=0,
        )
    except Exception as exc:  # reason: connect failures are reported by class name only
        raise ProbeError(f"cannot connect to Postgres ({type(exc).__name__})") from None
    if pool is None:
        raise ProbeError("cannot connect to Postgres (PoolCreationFailed)")
    missing_schema = (
        asyncpg.exceptions.UndefinedTableError,
        asyncpg.exceptions.InvalidSchemaNameError,
    )
    try:
        async with pool.acquire() as conn:
            try:
                rows = await conn.fetch("SELECT version FROM pitwall.schema_migrations")
            except missing_schema:
                return DatabaseFacts(applied_versions=frozenset(), schema_ready=False)
            applied = frozenset(str(row["version"]) for row in rows)
            try:
                capabilities = await conn.fetchval(
                    "SELECT count(*) FROM pitwall.capabilities WHERE enabled"
                )
                embedding = await conn.fetch(
                    "SELECT name FROM pitwall.capabilities WHERE enabled AND class = 'embedding' ORDER BY name"
                )
                providers = await conn.fetchrow(
                    "SELECT count(*) FILTER (WHERE enabled AND health_status <> 'disarmed')"
                    " AS enabled, "
                    "count(*) FILTER (WHERE enabled AND health_status = 'healthy') AS healthy, "
                    "count(*) FILTER (WHERE enabled AND health_status = 'disarmed') AS disarmed "
                    "FROM pitwall.providers"
                )
                engaged = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pitwall.kill_log)")
                gate_spend = await month_to_date_spend(conn)
            except missing_schema:
                return DatabaseFacts(applied_versions=applied, schema_ready=False)
        burn: BurnFacts | None
        burn_error: str | None = None
        try:
            read = await read_configured_burn_rate(pool, now=dt.datetime.now(dt.UTC))
            burn = BurnFacts(
                read.budget_usd,
                read.spend_to_date_usd,
                read.forecast_total_usd,
                gate_spend_usd=gate_spend,
                stale=read.stale,
                data_sufficiency=str(read.data_sufficiency),
            )
        except Exception as exc:  # reason: reported by class name as a warning, never swallowed
            burn = None
            burn_error = type(exc).__name__
        return DatabaseFacts(
            applied_versions=applied,
            schema_ready=True,
            enabled_capabilities=int(capabilities),
            embedding_capabilities=tuple(str(row["name"]) for row in embedding),
            enabled_providers=int(providers["enabled"]),
            healthy_providers=int(providers["healthy"]),
            disarmed_providers=int(providers["disarmed"]),
            kill_switch_engaged=bool(engaged),
            burn=burn,
            burn_error=burn_error,
        )
    except ProbeError:
        raise
    except Exception as exc:  # reason: query failures are reported by class name only
        raise ProbeError(f"Postgres query failed ({type(exc).__name__})") from None
    finally:
        await pool.close()


async def _redis_probe(url: str, timeout: float) -> None:
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]  # reason: redis.from_url is untyped in redis-py
        url, socket_connect_timeout=timeout, socket_timeout=timeout
    )
    try:
        await client.ping()
    except Exception as exc:  # reason: connect failures are reported by class name only
        raise ProbeError(f"cannot reach Redis ({type(exc).__name__})") from None
    finally:
        await client.aclose()


def _display_url(url: str) -> str:
    """Scheme, host, and port only, so userinfo never reaches output."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return f"{parts.scheme}://{host}{f':{parts.port}' if parts.port else ''}"


async def _api_request(
    method: str,
    url: str,
    token: str | None,
    timeout: float,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=headers, json=json_body)
    except httpx.HTTPError as exc:
        raise ProbeError(
            f"API not reachable at {_display_url(url)} ({type(exc).__name__})"
        ) from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {"status_code": response.status_code, "body": body if isinstance(body, dict) else {}}


async def _api_health_probe(base_url: str, token: str | None, timeout: float) -> dict[str, Any]:
    return await _api_request("GET", f"{base_url.rstrip('/')}/v1/health", token, timeout)


async def _canary_probe(
    base_url: str, token: str | None, capability: str, timeout: float
) -> dict[str, Any]:
    payload = {"capability": capability, "texts": [CANARY_TEXT], "dry_run": True}
    return await _api_request(
        "POST", f"{base_url.rstrip('/')}/v1/inference", token, timeout, payload
    )


def default_probes() -> Probes:
    return Probes(
        database=_database_probe,
        redis=_redis_probe,
        api_health=_api_health_probe,
        canary=_canary_probe,
    )


def _version() -> str:
    try:
        return version("pitwall")
    except PackageNotFoundError:
        from pitwall import __version__

        return __version__


def _check(
    id_: str, phase: str, status: Status, detail: str, next_step: str | None = None
) -> DoctorCheck:
    return DoctorCheck(id_, phase, status, detail, next_step)


def _python_check() -> DoctorCheck:
    found = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.version_info[:2] == (3, 14):
        return _check("install.python", "install", "ok", f"Python {found}")
    return _check(
        "install.python",
        "install",
        "fail",
        f"Pitwall supports Python 3.14 only; this is {found}",
        "uv sync --frozen --extra dev --python 3.14.7",
    )


def _personal_state_root(environ: Mapping[str, str]) -> Path:
    base = environ.get("XDG_STATE_HOME", "").strip()
    home = Path(environ.get("HOME") or Path.home())
    return (Path(base) if base else home / ".local" / "state") / "pitwall"


def _personal_checks(environ: Mapping[str, str]) -> list[DoctorCheck]:
    from pitwall.personal.keys import ENDPOINT_KEY_ENV, read_endpoint_key
    from pitwall.personal.state import StateStore
    from pitwall.runpod_credentials import resolve_runpod_api_key, runpodctl_config_path

    checks: list[DoctorCheck] = []
    key, source = resolve_runpod_api_key(environ, runpodctl_config_path(environ))
    del key  # never retained or printed
    checks.append(
        _check("personal.runpod_credential", "config", "ok", f"RunPod credential found ({source})")
        if source != "none"
        else _check(
            "personal.runpod_credential",
            "config",
            "fail",
            "no RunPod credential found",
            "export RUNPOD_API_KEY, or run runpodctl doctor",
        )
    )
    root = _personal_state_root(environ)
    if read_endpoint_key(root) is None:
        checks.append(
            _check(
                "personal.endpoint_key",
                "config",
                "fail",
                f"no endpoint key under {root}",
                "pitwall setup",
            )
        )
    elif not environ.get(ENDPOINT_KEY_ENV):
        checks.append(
            _check(
                "personal.endpoint_key",
                "config",
                "warn",
                f"endpoint key exists but {ENDPOINT_KEY_ENV} is not exported in this shell",
                f"open a new shell or export {ENDPOINT_KEY_ENV}",
            )
        )
    else:
        checks.append(
            _check("personal.endpoint_key", "config", "ok", "endpoint key present and exported")
        )
    routing_cli = environ.get("PITWALL_ROUTING_CLI", "").strip() or DEFAULT_ROUTING_CLI
    if routing_available(routing_cli, environ):
        checks.append(
            _check("personal.routing_cli", "install", "ok", f"`{routing_cli}` is available")
        )
    else:
        checks.append(
            _check(
                "personal.routing_cli",
                "install",
                "warn",
                f"`{routing_cli}` is not on PATH; routes cannot be attached",
                "unset PITWALL_ROUTING_CLI to use the built-in `pitwall agents`",
            )
        )
    try:
        leases = StateStore(root).load()
    except Exception as exc:  # reason: a corrupt state file is reported, not raised
        checks.append(
            _check(
                "personal.leases",
                "services",
                "warn",
                f"state file unreadable ({type(exc).__name__})",
            )
        )
    else:
        checks.append(
            _check("personal.leases", "services", "ok", f"{len(leases)} lease(s) recorded")
        )
    checks.append(
        _check(
            "registry.mode",
            "registry",
            "skip",
            '[personal] backend is not "registry" in pitwall.toml (personal mode)',
        )
    )
    return checks


async def run_doctor(
    *,
    environ: Mapping[str, str],
    settings: PitwallSettings | None = None,
    probes: Probes | None = None,
    api_url: str | None = None,
    api_token: str | None = None,
    canary: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> DoctorReport:
    from pitwall.config import (
        check_domain_config,
        format_settings_load_error,
        load_settings_from_env,
        resolve_config_file,
    )
    from pitwall.personal.backend import select_backend

    active = probes or default_probes()
    checks: list[DoctorCheck] = [_python_check()]
    config_path = resolve_config_file(dict(environ))
    loaded: PitwallSettings | None = settings
    try:
        if loaded is None:
            loaded = load_settings_from_env()
    except ValueError as exc:  # ConfigFileError, including settings that fail validation
        checks.append(
            _check(
                "config.file",
                "config",
                "fail",
                format_settings_load_error(exc),
                "fix the config file, then rerun pitwall doctor",
            )
        )
        loaded = None
        config_failed = True
    else:
        config_failed = False

    mode: Mode = "personal"
    try:
        mode = select_backend(environ)
    except ValueError as exc:  # an unparseable file or a bad `[personal] backend`
        if not config_failed:
            checks.append(
                _check(
                    "config.file",
                    "config",
                    "fail",
                    format_settings_load_error(exc),
                    "fix the config file, then rerun pitwall doctor",
                )
            )
            config_failed = True
        loaded = None
    if not config_failed:
        checks.append(
            _check("config.file", "config", "ok", f"loaded {config_path}")
            if config_path is not None
            else _check("config.file", "config", "skip", "no pitwall.toml in use")
        )

    if mode == "personal":
        return DoctorReport("personal", _version(), tuple(checks + _personal_checks(environ)))
    checks.extend(
        await _registry_checks(
            environ, loaded, active, api_url, api_token, canary, timeout_s, check_domain_config
        )
    )
    return DoctorReport("registry", _version(), tuple(checks))


def _money(value: Decimal) -> str:
    return f"${value:.2f}"


async def _registry_checks(
    environ: Mapping[str, str],
    loaded: PitwallSettings | None,
    probes: Probes,
    api_url: str | None,
    api_token: str | None,
    canary: str | None,
    timeout_s: float,
    check_domain_config: Callable[..., Any],
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []

    if loaded is None:
        checks.append(_check("config.runtime", "config", "skip", "config failed to load"))
    else:
        result = check_domain_config("api", settings=loaded)
        if result.errors:
            codes = ", ".join(issue.code for issue in result.errors)
            checks.append(_check("config.runtime", "config", "fail", codes, "pitwall config check"))
        elif result.warnings:
            codes = ", ".join(issue.code for issue in result.warnings)
            checks.append(_check("config.runtime", "config", "warn", codes))
        else:
            checks.append(_check("config.runtime", "config", "ok", "no configuration issues"))

    # The loaded settings already merge the environment with pitwall.toml.
    database_url = loaded.database_url if loaded is not None else environ.get("DATABASE_URL", "")
    facts: DatabaseFacts | None = None
    db_ok = False
    try:
        facts = await probes.database(database_url, timeout_s)
    except ProbeError as exc:
        checks.append(_check("db.connect", "services", "fail", str(exc)))
    else:
        db_ok = True
        checks.append(_check("db.connect", "services", "ok", "connected"))

    if db_ok:
        assert facts is not None
        _append_migration_check(checks, facts)
        _append_capability_and_provider_checks(checks, facts)
    else:
        checks.append(_check("db.migrations", "install", "skip", "database unreachable"))
        checks.append(_check("registry.capabilities", "registry", "skip", "database unreachable"))
        checks.append(_check("registry.providers", "registry", "skip", "database unreachable"))

    redis_url = loaded.redis_url if loaded is not None else environ.get("REDIS_URL", "")
    if not redis_url:
        checks.append(_check("redis.connect", "services", "fail", "REDIS_URL is not set"))
    else:
        try:
            await probes.redis(redis_url, timeout_s)
        except ProbeError as exc:
            checks.append(_check("redis.connect", "services", "fail", str(exc)))
        else:
            checks.append(_check("redis.connect", "services", "ok", "connected"))

    checks.append(_budget_check(loaded))

    if db_ok:
        assert facts is not None
        _append_kill_switch_and_burn_checks(checks, facts)
    else:
        checks.append(_check("spend.kill_switch", "spend", "skip", "database unreachable"))
        checks.append(_check("spend.burn_rate", "spend", "skip", "database unreachable"))

    resolved_api_url = api_url or default_base_url(environ)
    resolved_api_token = api_token or environ.get("PITWALL_API_TOKEN")
    api_ok = False
    try:
        response = await probes.api_health(resolved_api_url, resolved_api_token, timeout_s)
    except ProbeError as exc:
        checks.append(_check("api.health", "services", "warn", str(exc), "uv run pitwall-api"))
    else:
        body = response.get("body") if isinstance(response, dict) else {}
        status_code = response.get("status_code") if isinstance(response, dict) else None
        display_url = _display_url(resolved_api_url)
        if status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
            api_ok = True
            checks.append(_check("api.health", "services", "ok", f"{display_url} is healthy"))
        else:
            checks.append(
                _check(
                    "api.health",
                    "services",
                    "fail",
                    f"{display_url} responded but is not healthy (status {status_code})",
                )
            )

    if canary is not None:
        embedding_capabilities = facts.embedding_capabilities if facts is not None else ()
        if not api_ok or canary not in embedding_capabilities:
            reason = (
                "API is not healthy"
                if not api_ok
                else f"{canary} is not an enabled embedding capability"
            )
            checks.append(_check("canary.dry_run", "canary", "skip", reason))
        else:
            try:
                response = await probes.canary(
                    resolved_api_url, resolved_api_token, canary, timeout_s
                )
            except ProbeError as exc:
                checks.append(_check("canary.dry_run", "canary", "fail", str(exc)))
            else:
                body = response.get("body") if isinstance(response, dict) else {}
                result = body.get("result") if isinstance(body, dict) else None
                if isinstance(result, dict) and result.get("dry_run") is True:
                    checks.append(
                        _check("canary.dry_run", "canary", "ok", f"{canary} dry-run succeeded")
                    )
                else:
                    checks.append(
                        _check(
                            "canary.dry_run",
                            "canary",
                            "fail",
                            f"{canary} dry-run returned an unexpected reply",
                        )
                    )

    return checks


def _append_migration_check(checks: list[DoctorCheck], facts: DatabaseFacts) -> None:
    from pitwall.migrations import discover_migrations

    pending = sorted(
        m.version for m in discover_migrations() if m.version not in facts.applied_versions
    )
    if pending:
        shown = ", ".join(pending[:5])
        checks.append(
            _check(
                "db.migrations",
                "install",
                "fail",
                f"pending migrations: {shown}",
                "pitwall db migrate",
            )
        )
    else:
        checks.append(_check("db.migrations", "install", "ok", "all migrations applied"))


def _append_capability_and_provider_checks(checks: list[DoctorCheck], facts: DatabaseFacts) -> None:
    if not facts.schema_ready:
        checks.append(_check("registry.capabilities", "registry", "skip", "schema not migrated"))
        checks.append(_check("registry.providers", "registry", "skip", "schema not migrated"))
        return
    if facts.enabled_capabilities >= 1:
        checks.append(
            _check(
                "registry.capabilities",
                "registry",
                "ok",
                f"{facts.enabled_capabilities} capability(ies) enabled",
            )
        )
    else:
        checks.append(
            _check(
                "registry.capabilities",
                "registry",
                "warn",
                "no capabilities enabled",
                "pitwall init",
            )
        )
    # A disarmed serve provider is idle until a serve lease arms it; it is not failing, and
    # forcing it healthy would route to a terminated pod.
    disarmed = f" ({facts.disarmed_providers} disarmed)" if facts.disarmed_providers else ""
    if facts.enabled_providers < 1 and facts.disarmed_providers:
        checks.append(
            _check(
                "registry.providers",
                "registry",
                "ok",
                f"no armed providers; {facts.disarmed_providers} disarmed until a serve lease"
                " arms them",
            )
        )
    elif facts.enabled_providers < 1:
        checks.append(
            _check("registry.providers", "registry", "warn", "no providers enabled", "pitwall init")
        )
    elif facts.healthy_providers < 1:
        checks.append(
            _check(
                "registry.providers",
                "registry",
                "warn",
                f"{facts.enabled_providers} enabled but none healthy{disarmed}",
                "pitwall set-provider-health <provider-id> healthy",
            )
        )
    else:
        checks.append(
            _check(
                "registry.providers",
                "registry",
                "ok",
                f"{facts.healthy_providers}/{facts.enabled_providers} enabled providers healthy"
                f"{disarmed}",
            )
        )


def _budget_check(loaded: PitwallSettings | None) -> DoctorCheck:
    if loaded is None:
        return _check("spend.budget", "spend", "skip", "config failed to load")
    budget = loaded.pitwall_monthly_budget_usd
    cap = loaded.pitwall_per_request_max_usd
    detail = f"monthly budget {_money(budget)}, per-request cap {_money(cap)} (runtime limits, when set, override these)"
    if budget <= 0:
        return _check("spend.budget", "spend", "fail", detail)
    if cap > budget:
        return _check("spend.budget", "spend", "warn", detail)
    return _check("spend.budget", "spend", "ok", detail)


def _append_kill_switch_and_burn_checks(checks: list[DoctorCheck], facts: DatabaseFacts) -> None:
    if not facts.schema_ready:
        checks.append(_check("spend.kill_switch", "spend", "skip", "schema not migrated"))
        checks.append(_check("spend.burn_rate", "spend", "skip", "schema not migrated"))
        return

    if facts.kill_switch_engaged:
        checks.append(
            _check(
                "spend.kill_switch",
                "spend",
                "fail",
                "kill switch engaged; new spend is refused",
                "docs/operator/incident-response.md",
            )
        )
    else:
        checks.append(_check("spend.kill_switch", "spend", "ok", "kill switch not engaged"))

    burn = facts.burn
    if burn is None:
        if facts.burn_error is not None:
            checks.append(
                _check(
                    "spend.burn_rate",
                    "spend",
                    "warn",
                    f"burn-rate read failed ({facts.burn_error})",
                )
            )
        else:
            checks.append(_check("spend.burn_rate", "spend", "skip", "no burn-rate data"))
        return
    # The gate's figure is authoritative: it is what admission enforces. The rollup may lag
    # (stale) or miss spend it cannot attribute; both are warnings, never a green light.
    enforced = burn.gate_spend_usd if burn.gate_spend_usd is not None else burn.spend_to_date_usd
    detail = (
        f"spend to date {_money(enforced)} (budget gate), "
        f"forecast {_money(burn.forecast_total_usd) if burn.forecast_total_usd is not None else 'n/a'}, "
        f"budget {_money(burn.budget_usd)}"
    )
    problems: list[str] = []
    if burn.gate_spend_usd is not None:
        gap = abs(burn.gate_spend_usd - burn.spend_to_date_usd)
        if gap > Decimal("0.01") and gap > burn.gate_spend_usd * Decimal("0.01"):
            problems.append(
                f"daily rollup reports {_money(burn.spend_to_date_usd)} against the gate's "
                f"{_money(burn.gate_spend_usd)}"
            )
    if burn.stale:
        problems.append("rollup data is stale")
    if burn.data_sufficiency != "sufficient":
        problems.append(f"burn-rate data is {burn.data_sufficiency}")
    if problems:
        detail = f"{detail}; " + "; ".join(problems)
    if enforced >= burn.budget_usd:
        checks.append(_check("spend.burn_rate", "spend", "fail", detail))
    elif problems or (
        burn.forecast_total_usd is not None and burn.forecast_total_usd > burn.budget_usd
    ):
        checks.append(_check("spend.burn_rate", "spend", "warn", detail))
    else:
        checks.append(_check("spend.burn_rate", "spend", "ok", detail))
