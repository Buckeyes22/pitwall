# Frontier Agent Integration — First Wave (Proposals 4, 7, 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "clone Pitwall and point an agent at it" work end to end: a `pitwall doctor` readiness report (proposal 5), a `pitwall mcp install|uninstall` registrar for Claude Code, Codex, and OpenCode (proposal 4), and committed agent-facing install docs (proposal 7).

**Architecture:** Two new root-project modules carry the logic: `src/pitwall/doctor.py` builds a structured readiness report from injectable probes, and `src/pitwall/mcp_install.py` renders and applies per-harness MCP registrations. Thin CLI wrappers expose them as `pitwall doctor` and `pitwall mcp install|uninstall`; the doctor report is also served as the `pitwall_doctor` MCP tool. Docs under `docs/agents/` embed registrar output verbatim, and a test keeps them identical.

**Tech Stack:** Python 3.14, the root uv project (`uv run`), argparse CLI with `pitwall.cli_output.Output`, asyncpg, `redis.asyncio`, httpx, FastMCP, pytest (`make test-fast`).

**Spec:** `docs/research/2026-09-10-frontier-agent-integration.md`, proposals 4, 5, and 7, plus its "Sequencing recommendation" (first wave = 4 → 7 → 5). Read it alongside this plan.

## Global Constraints

- Run Python only through `uv run` or `.venv/bin/python`; never bare `python`. Setup: `uv sync --frozen --extra dev --python 3.14.7`.
- Every task's gate is `uv run ruff check .` and `uv run mypy --strict src/` (the CI commands) plus the task's tests.
- Unit tests stay hermetic: no network, no real Postgres/Redis, no writes outside `tmp_path`. Real service checks go behind injectable probes. `tests/_hermetic_env.py` sets `DATABASE_URL`, so tests pass an explicit `environ` mapping instead of trusting `os.environ`.
- Never print, log, or write a secret value. Checks report variable names, never values; registrations reference environment variables by name, never embed them.
- Fail closed: an unparseable harness config aborts that harness with no write; the doctor never reports `ok` for a check it could not run.
- `doctor` makes no paid call. The canary is a dry-run inference by construction and runs only when asked.
- Keep the root project and `packages/agent-routing` independent: no imports across them.
- Work on branch `feat/agent-install-first-wave` off `main`. Commit after every task with `git commit -s`. Do not push.
- Every behavior change lands with its test in the same task. The only intended changes to existing assertions are named in the task that makes them.

## Decisions recorded for this batch

1. **Public agent docs live in `docs/agents/`**, with a README pointer (operator choice, 2026-09-11). The root `AGENTS.md` stays local-only and untouched, because it already occupies that path and is excluded through `.git/info/exclude`.
2. **Three harnesses:** Claude Code, Codex, and OpenCode, the three named in proposal 4.
3. **Server identity:** the registration is named `pitwall`. Its command is the `pitwall-mcp` console script beside the running interpreter when it exists, else `<python> -m pitwall.mcp`, so a uv-managed install produces a config that survives shell changes (proposal 4 design note).
4. **Secrets by reference only.** `pitwall-mcp` refuses to boot without `RUNPOD_API_KEY`, `DATABASE_URL`, and `REDIS_URL` (`config.py::_REQUIRED_ENV_BY_SERVICE["mcp"]`). Registrations forward those names plus `PITWALL_CONFIG_FILE` using each harness's own reference syntax: Claude Code `${VAR:-}`, Codex `env_vars`, OpenCode `{env:VAR}`. When a variable is missing from the harness environment, the server's own fail-closed boot names it.
5. **Claude Code user scope goes through its CLI.** `claude mcp add-json --scope user` and `claude mcp remove --scope user` are used because Claude Code rewrites `~/.claude.json` while it runs. Project scope writes `.mcp.json` directly.
6. **Format preservation.** Codex registrations are a managed TOML block, so every foreign byte is untouched and uninstall restores the original bytes exactly. JSON files (`.mcp.json`, `opencode.json`) are re-serialized with 2-space indentation: foreign entries are preserved by value, not by byte, because the standard library has no format-preserving JSON editor. Every modifying write first copies the file to `<name>.bak.<UTC timestamp>`. Proposal 4 said "byte-for-byte"; this narrows that to "by value" for JSON only.
7. **Doctor modes follow `pitwall.personal.backend.select_backend`**: `registry` when `DATABASE_URL` is set, else `personal`. Each mode runs only its own checks, so the personal path is not buried in database failures (proposal 5 design note).
8. **The MCP tool count grows from 78 to 79** with `pitwall_doctor`. The pinned counts in code, tests, and docs are updated in Task 3.
9. **The canary covers embedding capabilities only.** It sends the README quickstart's dry-run shape, `{"capability": <name>, "texts": [...], "dry_run": true}`. Other capability classes report `skip` with the reason.
10. **Codex registrations are user scope only** (added 2026-09-11 from the live check in Task 11): codex-cli 0.153.4 ignores `[mcp_servers]` in a project's `.codex/config.toml`, so project scope is refused rather than written.

## File Map

| File | Responsibility |
| --- | --- |
| `src/pitwall/doctor.py` *(new)* | report model, personal and registry checks, default probes, rendering |
| `src/pitwall/cli_doctor.py` *(new)* | `pitwall doctor` argument parsing, output, exit codes |
| `src/pitwall/mcp/tools/doctor.py` *(new)* | `pitwall_doctor` MCP tool |
| `src/pitwall/mcp_install.py` *(new)* | registration paths, renderers, install/uninstall plans, apply, detection |
| `src/pitwall/cli.py` | `doctor` dispatch (`main` :159); `mcp install|uninstall` subcommands (`_parse_mcp_serve_args` :2577, `cmd_mcp_serve` :2601); usage text (`_usage` :731) |
| `src/pitwall/mcp/registry.py` | register `pitwall_doctor`; counts 78 → 79 (:120, :360) |
| `tests/cli/test_doctor.py`, `tests/cli/test_mcp_install.py`, `tests/mcp/test_doctor_tool.py`, `tests/docs/test_agent_docs.py` *(new)* | tests |
| `tests/mcp/test_registry_health.py`, `tests/mcp/test_registry.py`, `tests/mcp/test_gateway_tools.py`, `tests/cli/test_cli_dispatch.py` | count and usage assertions |
| `docs/agents/README.md`, `install.md`, `claude-code.md`, `codex.md`, `opencode.md` *(new)* | public agent guide |
| `README.md`, `CHANGELOG.md`, `docs/sdlc/18-cli.md`, `docs/sdlc/03-mcp-server.md`, `docs/support-matrix.md`, `docs/operator/user-journey-catalog.md` | pointers, command docs, counts |
| `docs/research/2026-09-10-frontier-agent-integration.md` | status line after the wave lands |

## Execution order and lanes

| Lane | Tasks | Exclusive files |
| --- | --- | --- |
| doctor | 1 → 2 → 3 | `doctor.py`, `cli_doctor.py`, `mcp/tools/doctor.py`, `mcp/registry.py`, the `doctor` dispatch branch and all `_usage` edits in `cli.py`, doctor tests, MCP count tests, `docs/sdlc/03-mcp-server.md`, `docs/support-matrix.md`, `docs/operator/user-journey-catalog.md` |
| registrar | 4 → 5 | `mcp_install.py`, the `mcp` group parser and command in `cli.py` (`_parse_mcp_serve_args`, `cmd_mcp_serve`, new `_cmd_mcp_install`), registrar tests |
| docs | 6 → 7 | `docs/agents/*`, `README.md`, `CHANGELOG.md`, `docs/sdlc/18-cli.md`, `tests/docs/test_agent_docs.py`, the research doc |

The doctor and registrar lanes run at the same time, each on its own lane branch (`aiw-doctor`, `aiw-registrar`) in its own worktree under `/tmp`. Both edit `cli.py` but touch disjoint functions: the doctor lane owns `main` and `_usage`, and the registrar lane owns the `mcp` group functions. The docs lane (`aiw-docs`) starts after both merge, because its docs embed the output of both. Lane prompts start from `~/.claude/templates/lane-prompt.md`. A lane lands by rebasing onto `feat/agent-install-first-wave` and fast-forwarding it with `git fetch . aiw-<lane>:feat/agent-install-first-wave`, which refuses anything but a fast-forward. Never land a lane with a path-unscoped patch.

## Task 0: Baseline

- [x] **Step 1: Branch and measure**

```bash
cd ~/git/pitwall && git branch feat/agent-install-first-wave main
git worktree add /tmp/aiw-doctor -b aiw-doctor feat/agent-install-first-wave
git worktree add /tmp/aiw-registrar -b aiw-registrar feat/agent-install-first-wave
cd /tmp/aiw-doctor && uv sync --frozen --extra dev --python 3.14.7 >/dev/null
uv run pytest -q -m "not integration and not slow" -p no:randomly 2>&1 | tail -3
uv run ruff check . && uv run mypy --strict src/ 2>&1 | tail -1
```

Expected: the pytest tail ends with `passed` and no failures; record the count. ruff prints `All checks passed!`; mypy prints `Success: no issues found in N source files`. These are the CI commands (`.github/workflows/ci.yml:42`, `:125`).

---

## Task 1: Doctor core — report model, checks, default probes (proposal 5)

**Lane:** doctor (worktree `/tmp/aiw-doctor`).

**Files:**
- Create: `src/pitwall/doctor.py`, `tests/cli/test_doctor.py`

**Interfaces:**
- Consumes (existing): `pitwall.personal.backend.select_backend(environ)`; `pitwall.runpod_credentials.resolve_runpod_api_key(environ, config_path)` and `runpodctl_config_path(environ)`; `pitwall.personal.keys.read_endpoint_key(root)` and `ENDPOINT_KEY_ENV`; `pitwall.personal.state.StateStore(root).load()`; `pitwall.config.check_domain_config(service, settings=...)`, `load_settings_from_env()`, `resolve_config_file(environ)`, `format_settings_load_error(exc)`, `ConfigFileError`; `pitwall.migrations.discover_migrations()`; `pitwall.finops.burn_rate.read_configured_burn_rate(pool, now=..., window_days=...)`.
- Produces: `Status = Literal["ok", "warn", "fail", "skip"]`; `@dataclass(frozen=True) DoctorCheck(id, phase, status, detail, next_step=None)` with `to_dict()`; `@dataclass(frozen=True) DoctorReport(mode, version, checks)` with `status`, `exit_code(*, strict=False) -> int`, and `to_dict()` (`schema_version`, `mode`, `version`, `status`, `summary`, `checks`); `@dataclass(frozen=True) DatabaseFacts`; `@dataclass(frozen=True) BurnFacts(budget_usd, spend_to_date_usd, forecast_total_usd)`; `class ProbeError(RuntimeError)`; `@dataclass(frozen=True) Probes(database, redis, api_health, canary)`; `default_probes() -> Probes`; `async def run_doctor(*, environ, settings=None, probes=None, api_url=None, api_token=None, canary=None, timeout_s=5.0) -> DoctorReport`.

Check catalogue (ids are the contract; the docs in Task 6 list them):

| Mode | id | phase | ok when | otherwise |
| --- | --- | --- | --- | --- |
| both | `install.python` | install | interpreter is 3.14 | `fail`: "Pitwall supports Python 3.14 only" |
| both | `config.file` | config | no TOML file (`skip`), or it loads | `fail` with `format_settings_load_error` text |
| personal | `personal.runpod_credential` | config | credential found in env or runpodctl config | `fail`, next `export RUNPOD_API_KEY, or run runpodctl doctor` |
| personal | `personal.endpoint_key` | config | key file exists and `PITWALL_ENDPOINT_KEY` is exported | missing file: `fail`, next `pitwall setup`; not exported: `warn`, next `open a new shell or export PITWALL_ENDPOINT_KEY` |
| personal | `personal.routing_cli` | install | `pitwall-agent-routing` on `PATH` | `warn`, next `packages/agent-routing/scripts/install.sh` |
| personal | `personal.leases` | services | state file loads (detail: count) | `warn`: state file unreadable |
| personal | `registry.mode` | registry | always `skip`: "DATABASE_URL is not set (personal mode)" | — |
| registry | `config.runtime` | config | `check_domain_config("api")` has no errors | errors: `fail` (issue codes only), next `pitwall config check`; warnings only: `warn` |
| registry | `db.connect` | services | probe connects | `fail` (error class only); dependent checks `skip` |
| registry | `db.migrations` | install | none pending | `fail` listing up to 5 pending versions, next `pitwall db migrate` |
| registry | `registry.capabilities` | registry | ≥ 1 enabled | `warn`, next `pitwall init`; schema absent: `skip` |
| registry | `registry.providers` | registry | ≥ 1 enabled and healthy | none enabled: `warn`, next `pitwall init`; none healthy: `warn`, next `pitwall set-provider-health <provider-id> healthy` |
| registry | `redis.connect` | services | ping succeeds | `fail` (error class only) |
| registry | `spend.budget` | spend | monthly budget > 0 and per-request cap ≤ budget | budget ≤ 0: `fail`; cap > budget: `warn` |
| registry | `spend.kill_switch` | spend | no `pitwall.kill_log` row | `fail`: "kill switch engaged; new spend is refused", next `docs/operator/incident-response.md` |
| registry | `spend.burn_rate` | spend | forecast ≤ budget | spend ≥ budget: `fail`; forecast > budget: `warn`; no data: `skip` |
| registry | `api.health` | services | `GET /v1/health` returns 200 with `ok: true` | unreachable: `warn`, next `uv run pitwall-api` (optional for CLI use); unhealthy: `fail` |
| registry | `canary.dry_run` | canary | only with `canary=<name>`: dry-run inference returns `result.dry_run == true` | API not ok, or capability not an enabled embedding capability: `skip`; any other reply: `fail` |

- [x] **Step 1: Write the failing tests** (`tests/cli/test_doctor.py`)

```python
"""pitwall doctor: readiness checks per mode, with fake probes (plan Task 1)."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pitwall.config import PitwallSettings
from pitwall.doctor import (
    BurnFacts,
    DatabaseFacts,
    DoctorReport,
    ProbeError,
    Probes,
    _database_probe,
    run_doctor,
)
from pitwall.migrations import discover_migrations
from pitwall.personal.keys import ensure_endpoint_key

ALL_VERSIONS = frozenset(m.version for m in discover_migrations())


def _probes(
    *,
    facts: DatabaseFacts | None = None,
    db_error: bool = False,
    redis_error: bool = False,
    api: dict[str, Any] | None = None,
    api_error: bool = False,
    canary: dict[str, Any] | None = None,
) -> Probes:
    async def database(url: str, timeout: float) -> DatabaseFacts:
        if db_error:
            raise ProbeError("cannot connect to Postgres (ConnectionRefusedError)")
        return facts or DatabaseFacts(
            applied_versions=ALL_VERSIONS,
            schema_ready=True,
            enabled_capabilities=1,
            embedding_capabilities=("embedding.demo",),
            enabled_providers=1,
            healthy_providers=1,
            burn=BurnFacts(Decimal("50"), Decimal("1"), Decimal("5")),
        )

    async def redis(url: str, timeout: float) -> None:
        if redis_error:
            raise ProbeError("cannot reach Redis (ConnectionError)")

    async def api_health(url: str, token: str | None, timeout: float) -> dict[str, Any]:
        if api_error:
            raise ProbeError("API not reachable at http://127.0.0.1:8080 (ConnectError)")
        return api or {"status_code": 200, "body": {"ok": True}}

    async def dry_run(
        url: str, token: str | None, capability: str, timeout: float
    ) -> dict[str, Any]:
        return canary or {"status_code": 200, "body": {"result": {"dry_run": True}}}

    return Probes(database=database, redis=redis, api_health=api_health, canary=dry_run)


def _registry_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://pitwall:hunter2@127.0.0.1:5444/pitwall_test",
        "REDIS_URL": "redis://127.0.0.1:6380/0",
        "RUNPOD_API_KEY": "rk-secret-value",
        "HOME": "/nonexistent-home",
        "PATH": "/usr/bin:/bin",
    }


def _by_id(report: DoctorReport) -> dict[str, str]:
    return {check.id: check.status for check in report.checks}


def _settings(**overrides: Any) -> PitwallSettings:
    values: dict[str, Any] = {
        "runpod_api_key": "rk-secret-value",
        "database_url": "postgresql://x",
        "redis_url": "redis://x",
    }
    values.update(overrides)
    return PitwallSettings(**values)


@pytest.fixture
def personal_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    routing = bin_dir / "pitwall-agent-routing"
    routing.write_text("#!/bin/sh\n", encoding="utf-8")
    routing.chmod(0o755)
    state = tmp_path / "state"
    ensure_endpoint_key(state / "pitwall")
    return {
        "HOME": str(tmp_path),
        "PATH": str(bin_dir),
        "XDG_STATE_HOME": str(state),
        "RUNPOD_API_KEY": "rk-secret-value",
        "PITWALL_ENDPOINT_KEY": "ek-secret-value",
    }


async def test_personal_mode_all_green(personal_env: dict[str, str]) -> None:
    report = await run_doctor(environ=personal_env, probes=_probes())
    assert report.mode == "personal"
    assert report.status == "ok"
    assert _by_id(report) == {
        "install.python": "ok",
        "config.file": "skip",
        "personal.runpod_credential": "ok",
        "personal.endpoint_key": "ok",
        "personal.routing_cli": "ok",
        "personal.leases": "ok",
        "registry.mode": "skip",
    }
    assert report.exit_code() == 0


async def test_personal_mode_names_the_fixes_and_never_the_secrets(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / "state")}
    report = await run_doctor(environ=env, probes=_probes())
    checks = {check.id: check for check in report.checks}
    assert checks["personal.runpod_credential"].status == "fail"
    assert checks["personal.endpoint_key"].status == "fail"
    assert checks["personal.endpoint_key"].next_step == "pitwall setup"
    assert checks["personal.routing_cli"].status == "warn"
    assert report.exit_code() == 1


async def test_registry_mode_all_green() -> None:
    report = await run_doctor(environ=_registry_env(), settings=_settings(), probes=_probes())
    assert report.mode == "registry"
    assert _by_id(report) == {
        "install.python": "ok",
        "config.file": "skip",
        "config.runtime": "ok",
        "db.connect": "ok",
        "db.migrations": "ok",
        "registry.capabilities": "ok",
        "registry.providers": "ok",
        "redis.connect": "ok",
        "spend.budget": "ok",
        "spend.kill_switch": "ok",
        "spend.burn_rate": "ok",
        "api.health": "ok",
    }
    assert report.status == "ok"


async def test_unreachable_database_skips_its_dependents() -> None:
    report = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(db_error=True)
    )
    ids = _by_id(report)
    assert ids["db.connect"] == "fail"
    for dependent in (
        "db.migrations",
        "registry.capabilities",
        "registry.providers",
        "spend.kill_switch",
        "spend.burn_rate",
    ):
        assert ids[dependent] == "skip", dependent
    assert report.exit_code() == 1


async def test_pending_migrations_kill_switch_and_breach_fail() -> None:
    facts = DatabaseFacts(
        applied_versions=ALL_VERSIONS - {sorted(ALL_VERSIONS)[-1]},
        schema_ready=True,
        enabled_capabilities=1,
        enabled_providers=1,
        healthy_providers=0,
        kill_switch_engaged=True,
        burn=BurnFacts(Decimal("50"), Decimal("60"), Decimal("80")),
    )
    report = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(facts=facts)
    )
    checks = {check.id: check for check in report.checks}
    assert checks["db.migrations"].status == "fail"
    assert sorted(ALL_VERSIONS)[-1] in checks["db.migrations"].detail
    assert checks["db.migrations"].next_step == "pitwall db migrate"
    assert checks["registry.providers"].status == "warn"
    assert checks["spend.kill_switch"].status == "fail"
    assert checks["spend.burn_rate"].status == "fail"


async def test_budget_and_cap_rules() -> None:
    zero = await run_doctor(
        environ=_registry_env(),
        settings=_settings(pitwall_monthly_budget_usd=Decimal("0")),
        probes=_probes(),
    )
    assert _by_id(zero)["spend.budget"] == "fail"
    inverted = await run_doctor(
        environ=_registry_env(),
        settings=_settings(
            pitwall_monthly_budget_usd=Decimal("10"), pitwall_per_request_max_usd=Decimal("20")
        ),
        probes=_probes(),
    )
    assert _by_id(inverted)["spend.budget"] == "warn"


async def test_api_down_is_a_warning_that_strict_mode_fails() -> None:
    report = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(api_error=True)
    )
    assert _by_id(report)["api.health"] == "warn"
    assert (report.exit_code(), report.exit_code(strict=True)) == (0, 1)


async def test_canary_runs_only_for_enabled_embedding_capabilities() -> None:
    env = _registry_env()
    ok = await run_doctor(
        environ=env, settings=_settings(), probes=_probes(), canary="embedding.demo"
    )
    assert _by_id(ok)["canary.dry_run"] == "ok"
    other = await run_doctor(environ=env, settings=_settings(), probes=_probes(), canary="llm.chat")
    assert _by_id(other)["canary.dry_run"] == "skip"
    down = await run_doctor(
        environ=env, settings=_settings(), probes=_probes(api_error=True), canary="embedding.demo"
    )
    assert _by_id(down)["canary.dry_run"] == "skip"
    bad = await run_doctor(
        environ=env,
        settings=_settings(),
        probes=_probes(canary={"status_code": 422, "body": {}}),
        canary="embedding.demo",
    )
    assert _by_id(bad)["canary.dry_run"] == "fail"
    assert "canary.dry_run" not in _by_id(
        await run_doctor(environ=env, settings=_settings(), probes=_probes())
    )


async def test_report_json_never_contains_secret_values(personal_env: dict[str, str]) -> None:
    registry = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(db_error=True)
    )
    personal = await run_doctor(environ=personal_env, probes=_probes())
    for report in (registry, personal):
        text = json.dumps(report.to_dict())
        for secret in ("hunter2", "rk-secret-value", "ek-secret-value"):
            assert secret not in text
    payload = registry.to_dict()
    assert payload["schema_version"] == 1
    assert set(payload["summary"]) == {"ok", "warn", "fail", "skip"}


async def test_default_database_probe_reports_class_names_only() -> None:
    with pytest.raises(ProbeError) as caught:
        await _database_probe("postgresql://pitwall:hunter2@127.0.0.1:1/nope", 0.5)
    assert "hunter2" not in str(caught.value)
    assert "Postgres" in str(caught.value)


def test_python_check_matches_the_supported_minor() -> None:
    assert sys.version_info[:2] == (3, 14)
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `uv run pytest -q tests/cli/test_doctor.py 2>&1 | tail -5`
Expected: FAIL with `ModuleNotFoundError: No module named 'pitwall.doctor'`.

- [x] **Step 3: Implement `src/pitwall/doctor.py`**

```python
"""Installation readiness report for ``pitwall doctor`` and the ``pitwall_doctor`` MCP tool.

Answers "what works, what is missing, what next" for one Pitwall installation.
It never makes a paid call and never prints secret values: checks name
variables, not their contents. Real services are reached through
:class:`Probes`, which tests replace.
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pitwall.config import PitwallSettings

Status = Literal["ok", "warn", "fail", "skip"]
Mode = Literal["personal", "registry"]
SCHEMA_VERSION = 1
DEFAULT_API_URL = "http://127.0.0.1:8080"
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
class DoctorReport:
    mode: Mode
    version: str
    checks: tuple[DoctorCheck, ...]

    @property
    def status(self) -> Status:
        statuses = {check.status for check in self.checks}
        if "fail" in statuses:
            return "fail"
        return "warn" if "warn" in statuses else "ok"

    def exit_code(self, *, strict: bool = False) -> int:
        if self.status == "fail" or (strict and self.status == "warn"):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        summary = {name: 0 for name in ("ok", "warn", "fail", "skip")}
        for check in self.checks:
            summary[check.status] += 1
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": self.mode,
            "version": self.version,
            "status": self.status,
            "summary": summary,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class BurnFacts:
    budget_usd: Decimal
    spend_to_date_usd: Decimal
    forecast_total_usd: Decimal | None


@dataclass(frozen=True)
class DatabaseFacts:
    applied_versions: frozenset[str]
    schema_ready: bool
    enabled_capabilities: int = 0
    embedding_capabilities: tuple[str, ...] = ()
    enabled_providers: int = 0
    healthy_providers: int = 0
    kill_switch_engaged: bool = False
    burn: BurnFacts | None = None


class ProbeError(RuntimeError):
    """A probe could not reach its service; the message never contains credentials."""


@dataclass(frozen=True)
class Probes:
    database: Callable[[str, float], Awaitable[DatabaseFacts]]
    redis: Callable[[str, float], Awaitable[None]]
    api_health: Callable[[str, str | None, float], Awaitable[dict[str, Any]]]
    canary: Callable[[str, str | None, str, float], Awaitable[dict[str, Any]]]
```

Default probes (same file). Every failure becomes a `ProbeError` naming only the exception class; the DSN, password, and tokens never reach a message:

```python
async def _database_probe(url: str, timeout: float) -> DatabaseFacts:
    import asyncpg

    from pitwall.finops.burn_rate import read_configured_burn_rate

    try:
        pool = await asyncpg.create_pool(
            dsn=url, min_size=1, max_size=1, timeout=timeout, statement_cache_size=0
        )
    except Exception as exc:  # reason: connect failures are reported by class name only
        raise ProbeError(f"cannot connect to Postgres ({type(exc).__name__})") from None
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
                    "SELECT count(*) FILTER (WHERE enabled) AS enabled, "
                    "count(*) FILTER (WHERE enabled AND health_status = 'healthy') AS healthy "
                    "FROM pitwall.providers"
                )
                engaged = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pitwall.kill_log)")
            except missing_schema:
                return DatabaseFacts(applied_versions=applied, schema_ready=False)
        burn: BurnFacts | None
        try:
            read = await read_configured_burn_rate(pool, now=dt.datetime.now(dt.UTC))
            burn = BurnFacts(read.budget_usd, read.spend_to_date_usd, read.forecast_total_usd)
        except (
            Exception
        ):  # reason: burn-rate data is optional; its absence is a skip, not a failure
            burn = None
        return DatabaseFacts(
            applied_versions=applied,
            schema_ready=True,
            enabled_capabilities=int(capabilities),
            embedding_capabilities=tuple(str(row["name"]) for row in embedding),
            enabled_providers=int(providers["enabled"]),
            healthy_providers=int(providers["healthy"]),
            kill_switch_engaged=bool(engaged),
            burn=burn,
        )
    except ProbeError:
        raise
    except Exception as exc:  # reason: query failures are reported by class name only
        raise ProbeError(f"Postgres query failed ({type(exc).__name__})") from None
    finally:
        await pool.close()


async def _redis_probe(url: str, timeout: float) -> None:
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(url, socket_connect_timeout=timeout, socket_timeout=timeout)
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
```

The checks and `run_doctor` (same file):

```python
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
    if shutil.which("pitwall-agent-routing", path=environ.get("PATH")):
        checks.append(
            _check("personal.routing_cli", "install", "ok", "pitwall-agent-routing is on PATH")
        )
    else:
        checks.append(
            _check(
                "personal.routing_cli",
                "install",
                "warn",
                "pitwall-agent-routing is not on PATH; routes cannot be attached",
                "packages/agent-routing/scripts/install.sh",
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
        _check("registry.mode", "registry", "skip", "DATABASE_URL is not set (personal mode)")
    )
    return checks
```

`run_doctor` assembles the checks in catalogue order:

```python
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
    from pydantic import ValidationError

    from pitwall.config import (
        ConfigFileError,
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
    except (ConfigFileError, ValidationError, ValueError) as exc:
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
    else:
        checks.append(
            _check("config.file", "config", "ok", f"loaded {config_path}")
            if config_path is not None
            else _check("config.file", "config", "skip", "no pitwall.toml in use")
        )

    mode: Mode = select_backend(environ)
    if mode == "personal":
        return DoctorReport("personal", _version(), tuple(checks + _personal_checks(environ)))
    checks.extend(
        await _registry_checks(
            environ, loaded, active, api_url, api_token, canary, timeout_s, check_domain_config
        )
    )
    return DoctorReport("registry", _version(), tuple(checks))
```

`_registry_checks` implements the registry rows of the catalogue in order: `config.runtime` (from `check_domain_config("api", settings=loaded)`, reporting `issue.code` values only; `skip` when settings failed to load); `db.connect` via `active.database(environ["DATABASE_URL"], timeout_s)`, turning a `ProbeError` into `fail` and every `_DEPENDENTS_OF_DB` id into `skip` with detail "database unreachable"; `db.migrations` from `[m.version for m in discover_migrations() if m.version not in facts.applied_versions]`; `registry.capabilities` and `registry.providers` (`skip` with "schema not migrated" when `facts.schema_ready` is false); `redis.connect` via `active.redis(environ["REDIS_URL"], timeout_s)` (`fail` when `REDIS_URL` is empty); `spend.budget` from `loaded.pitwall_monthly_budget_usd` and `loaded.pitwall_per_request_max_usd` (detail `monthly budget $X, per-request cap $Y`); `spend.kill_switch`; `spend.burn_rate` (`fail` when `spend_to_date_usd >= budget_usd`, `warn` when `forecast_total_usd > budget_usd`, detail with both amounts); `api.health` against `api_url or environ.get("PITWALL_API_URL") or DEFAULT_API_URL` with `api_token or environ.get("PITWALL_API_TOKEN")`, printing the URL through `_display_url`; and `canary.dry_run` only when `canary` is given. Money is formatted as `f"${value:.2f}"`.

- [x] **Step 4: Run the tests**

Run: `uv run pytest -q tests/cli/test_doctor.py 2>&1 | tail -3`
Expected: `11 passed`.

- [x] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict src/ 2>&1 | tail -1`
Expected: `All checks passed!` and `Success: no issues found in N source files`.

```bash
git add src/pitwall/doctor.py tests/cli/test_doctor.py
git commit -s -m "feat(doctor): installation readiness report with personal and registry checks"
```

## Task 2: `pitwall doctor` command

**Lane:** doctor.

**Files:**
- Create: `src/pitwall/cli_doctor.py`
- Modify: `src/pitwall/cli.py` (`main` :159 — add the `doctor` branch after `setup`; `_usage` :731 — add `doctor` to the group list after `stop`, plus a `pitwall doctor` line and two `pitwall mcp install|uninstall` lines for Task 5)
- Modify: `tests/cli/test_doctor.py` (append CLI tests)

**Interfaces:**
- Consumes: `run_doctor`, `DoctorReport` (Task 1); `pitwall.cli_output.Output`, `add_json_argument`, `json_mode`.
- Produces: `cmd_doctor(argv: list[str]) -> int` for `pitwall doctor [--json] [--strict] [--api-url URL] [--canary CAPABILITY] [--timeout SECONDS]`. Exit 0 when nothing failed, 1 when any check failed, or when `--strict` and any check warned, 2 for argument errors (argparse). `--json` prints exactly `DoctorReport.to_dict()`.

- [x] **Step 1: Write the failing tests** (append to `tests/cli/test_doctor.py`)

```python
from pitwall import cli
from pitwall.doctor import DoctorCheck


def _fixed_report(status: str) -> DoctorReport:
    return DoctorReport(
        "registry", "0.0.0", (DoctorCheck("api.health", "services", status, "detail", "next"),)
    )


@pytest.mark.parametrize(
    ("status", "argv", "code"),
    [
        ("ok", ["doctor"], 0),
        ("warn", ["doctor"], 0),
        ("warn", ["doctor", "--strict"], 1),
        ("fail", ["doctor"], 1),
    ],
)
def test_cli_exit_codes(
    monkeypatch: pytest.MonkeyPatch, status: str, argv: list[str], code: int
) -> None:
    async def fake(**_kwargs: Any) -> DoctorReport:
        return _fixed_report(status)

    monkeypatch.setattr("pitwall.cli_doctor.run_doctor", fake)
    assert cli.main(argv) == code


def test_cli_json_is_the_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> DoctorReport:
        seen.update(kwargs)
        return _fixed_report("ok")

    monkeypatch.setattr("pitwall.cli_doctor.run_doctor", fake)
    assert (
        cli.main(
            ["doctor", "--json", "--canary", "embedding.demo", "--api-url", "http://127.0.0.1:9"]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == _fixed_report("ok").to_dict()
    assert (seen["canary"], seen["api_url"]) == ("embedding.demo", "http://127.0.0.1:9")


def test_cli_table_names_every_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake(**_kwargs: Any) -> DoctorReport:
        return _fixed_report("warn")

    monkeypatch.setattr("pitwall.cli_doctor.run_doctor", fake)
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "api.health" in out and "warn" in out and "mode registry" in out


def test_usage_lists_doctor_and_mcp_install(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["--help"])
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("Usage: pitwall {setup|serve|status|stop|doctor|")
    assert "pitwall doctor" in out
    assert "pitwall mcp install" in out and "pitwall mcp uninstall" in out
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `uv run pytest -q tests/cli/test_doctor.py 2>&1 | tail -5`
Expected: FAIL — `Unknown command group: doctor` (exit 1 where 0 is expected) and a `ModuleNotFoundError` for `pitwall.cli_doctor` in the monkeypatch.

- [x] **Step 3: Implement**

```python
"""``pitwall doctor``: print the installation readiness report."""

from __future__ import annotations

import argparse
import asyncio
import os

from pitwall.cli_output import Output, add_json_argument, json_mode
from pitwall.doctor import DEFAULT_TIMEOUT_S, run_doctor


def _parse_doctor_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall doctor",
        description="Check install, configuration, services, and spend controls; exit 1 on any failure.",
    )
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument(
        "--api-url", help="API base URL (default: PITWALL_API_URL or http://127.0.0.1:8080)."
    )
    parser.add_argument(
        "--canary",
        metavar="CAPABILITY",
        help="Also send a dry-run inference to this embedding capability.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="Per-probe timeout in seconds (default: 5).",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


def cmd_doctor(argv: list[str]) -> int:
    args = _parse_doctor_args(argv)
    out = Output(json_mode(args))
    report = asyncio.run(
        run_doctor(
            environ=os.environ, api_url=args.api_url, canary=args.canary, timeout_s=args.timeout
        )
    )
    if out.json_mode:
        out.set_json(report.to_dict())
    else:
        rows = [[c.phase, c.id, c.status, c.detail, c.next_step or ""] for c in report.checks]
        out.print_table("Pitwall doctor", ["phase", "check", "status", "detail", "next step"], rows)
        summary = report.to_dict()["summary"]
        counts = ", ".join(f"{summary[name]} {name}" for name in ("ok", "warn", "fail", "skip"))
        out.print(
            f"doctor: {report.status} ({counts}), mode {report.mode}, pitwall {report.version}"
        )
    out.emit()
    return report.exit_code(strict=args.strict)
```

In `cli.main`, after the `setup` branch: `if group == "doctor": from pitwall.cli_doctor import cmd_doctor; return cmd_doctor(rest)`. In `_usage`, change the group list to begin `{setup|serve|status|stop|doctor|models|…` and add, next to the `pitwall mcp serve` line:

```text
  pitwall doctor              Check install, config, services, and spend controls
  pitwall mcp install         Register pitwall-mcp with Claude Code, Codex, or OpenCode
  pitwall mcp uninstall       Remove those registrations
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest -q tests/cli/test_doctor.py tests/cli/test_cli_dispatch.py tests/cli/test_gateway_cli.py 2>&1 | tail -3`
Expected: all pass (`test_usage_lists_personal_verbs_first` still sees the `setup|serve|status|stop|` prefix).

- [x] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict src/ 2>&1 | tail -1`
Expected: `All checks passed!` and `Success: no issues found in N source files`.

```bash
git add src/pitwall/cli_doctor.py src/pitwall/cli.py tests/cli/test_doctor.py
git commit -s -m "feat(cli): pitwall doctor with --json, --strict, and an opt-in dry-run canary"
```

## Task 3: `pitwall_doctor` MCP tool (78 → 79 tools)

**Lane:** doctor.

**Files:**
- Create: `src/pitwall/mcp/tools/doctor.py`, `tests/mcp/test_doctor_tool.py`
- Modify: `src/pitwall/mcp/registry.py` (`TOOL_NAMES` :88–118, the `== 78` asserts at :120 and :360, a `ToolSpec` entry)
- Modify: the count assertions in `tests/mcp/test_registry_health.py` (:14, :15, :44, :56), `tests/mcp/test_registry.py` (:12, :13), `tests/mcp/test_gateway_tools.py` (:68–71: assert 79 and rename the test to `test_registry_has_79_tools_including_gateway`)
- Modify: `docs/sdlc/03-mcp-server.md` (:5, :17, :19, :50, and a `pitwall_doctor` row in its tool catalogue), `docs/support-matrix.md` (:11), `README.md` (:58), `docs/operator/user-journey-catalog.md` (:33, J10)

**Interfaces:**
- Produces: `async def pitwall_doctor(canary: str | None = None) -> dict[str, Any]`, returning `run_doctor(environ=os.environ, canary=canary)` as `to_dict()`, the same schema as `pitwall doctor --json`. The MCP server always runs in registry mode, because it refuses to boot without `DATABASE_URL`.

- [x] **Step 1: Write the failing tests** (`tests/mcp/test_doctor_tool.py`)

```python
"""pitwall_doctor MCP tool (plan Task 3)."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.doctor import DoctorCheck, DoctorReport


async def test_tool_returns_the_cli_report_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.mcp.tools.doctor import pitwall_doctor

    seen: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> DoctorReport:
        seen.update(kwargs)
        return DoctorReport(
            "registry", "0.0.0", (DoctorCheck("db.connect", "services", "ok", "connected"),)
        )

    monkeypatch.setattr("pitwall.mcp.tools.doctor.run_doctor", fake)
    result = await pitwall_doctor(canary="embedding.demo")
    assert result["status"] == "ok"
    assert result["checks"][0]["id"] == "db.connect"
    assert seen["canary"] == "embedding.demo"


def test_tool_is_registered() -> None:
    from pitwall.mcp.registry import TOOL_NAMES, TOOL_REGISTRY

    assert "pitwall_doctor" in TOOL_NAMES
    assert len(TOOL_NAMES) == len(TOOL_REGISTRY) == 79
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `uv run pytest -q tests/mcp/test_doctor_tool.py 2>&1 | tail -5`
Expected: FAIL with `ModuleNotFoundError: No module named 'pitwall.mcp.tools.doctor'`.

- [x] **Step 3: Implement.** Create the tool:

```python
"""Installation readiness for MCP clients; the same report as ``pitwall doctor --json``."""

from __future__ import annotations

import os
from typing import Any

from pitwall.doctor import run_doctor


async def pitwall_doctor(canary: str | None = None) -> dict[str, Any]:
    """Return Pitwall's readiness report: install, config, services, and spend controls.

    Args:
        canary: Optional enabled embedding capability to exercise with a dry-run inference.

    Returns:
        ``schema_version``, ``mode``, ``version``, overall ``status`` (ok/warn/fail), a per-status
        ``summary``, and ``checks`` (each with id, phase, status, detail, next_step). Makes no paid
        call and never includes secret values.
    """
    report = await run_doctor(environ=os.environ, canary=canary)
    return report.to_dict()
```

In `registry.py`, import it, add `"pitwall_doctor"` to `TOOL_NAMES`, add `ToolSpec(name="pitwall_doctor", description="Return the installation readiness report (install, config, services, spend controls) with a next step for every problem. Read-only; the optional canary is a dry-run.", handler=pitwall_doctor)` beside `pitwall_health`, and change both asserts to 79. Update the listed tests and docs to 79, and add the tool to the catalogue in `docs/sdlc/03-mcp-server.md`.

- [x] **Step 4: Run the tests**

Run: `uv run pytest -q tests/mcp tests/cli/test_doctor.py 2>&1 | tail -3`
Expected: all pass, including `tests/mcp/test_doc_count_sync.py` (the SDLC doc and support matrix now say 79).

- [x] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict src/ 2>&1 | tail -1 && uv run python tools/ci/check_markdown_links.py | tail -1`
Expected: `All checks passed!`, `Success: no issues found in N source files`, `markdown links passed: N files (internal)`.

```bash
git add src/pitwall/mcp/tools/doctor.py src/pitwall/mcp/registry.py tests/mcp README.md docs/sdlc/03-mcp-server.md \
  docs/support-matrix.md docs/operator/user-journey-catalog.md
git commit -s -m "feat(mcp): pitwall_doctor tool; the server now exposes 79 tools"
```

## Task 4: MCP registrar module (proposal 4)

**Lane:** registrar (worktree `/tmp/aiw-registrar`, branch `aiw-registrar`; run `uv sync --frozen --extra dev --python 3.14.7` there first).

**Files:**
- Create: `src/pitwall/mcp_install.py`, `tests/cli/test_mcp_install.py`

**Interfaces:**
- Produces: `SERVER_NAME = "pitwall"`; `HARNESSES = ("claude-code", "codex", "opencode")`; `FORWARDED_ENV = ("RUNPOD_API_KEY", "DATABASE_URL", "REDIS_URL", "PITWALL_CONFIG_FILE")`; `Scope = Literal["user", "project"]`; `class McpInstallError(RuntimeError)`; `@dataclass(frozen=True) InstallPlan(harness, scope, path, before, after, commands=())` with `changed`; `server_command(executable: str | None = None) -> list[str]`; `config_path(harness, scope, *, environ, home, project_root) -> Path`; `render_entry(harness, command) -> dict[str, Any] | str`; `render_snippet(harness, scope, command) -> str` (the exact text the docs show; Task 6 tests the docs against it); `plan_registration(harness, scope, *, environ, home, project_root, command, remove=False, force=False) -> InstallPlan`; `apply_plan(plan, *, run=subprocess.run) -> Path | None` (returns the backup path); `detect_harnesses(*, environ, home, project_root) -> list[str]`; `VERIFY_HINT: dict[tuple[str, str], str]`.

Registration table (Decisions 3–6):

| Harness | Scope | File | How | Entry |
| --- | --- | --- | --- | --- |
| Claude Code | user | `${CLAUDE_CONFIG_DIR:-~}/.claude.json` (read only) | `claude mcp add-json --scope user pitwall <json>`; removal `claude mcp remove --scope user pitwall` | `{"type": "stdio", "command": CMD, "args": ARGS, "env": {V: "${V:-}"}}` |
| Claude Code | project | `<project>/.mcp.json` → `mcpServers` | direct JSON merge | same entry |
| Codex | user | `${CODEX_HOME:-~/.codex}/config.toml` | managed block | `[mcp_servers.pitwall]`, `command`, `args`, `env_vars = [FORWARDED_ENV…]` |
| Codex | project | none | refused with "rerun with --scope user" (Decision 10, Task 11) | none |
| OpenCode | user | `${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json` → `mcp` | direct JSON merge (strict JSON; JSONC is refused, as `packages/agent-routing/runtime/model_routing/providers/opencode.py` also refuses it) | `{"type": "local", "command": [CMD, *ARGS], "enabled": true, "environment": {V: "{env:V}"}}` |
| OpenCode | project | `<project>/opencode.json` → `mcp` | same | same |

An existing `pitwall` entry is "ours" when its command's basename is `pitwall-mcp` or its argv contains `pitwall.mcp`. A foreign entry makes the plan raise `McpInstallError("… already has a 'pitwall' server that pitwall did not write; rerun with --force to replace it")`; `--force` replaces JSON entries, but never an unmanaged Codex table, because replacing arbitrary TOML safely needs a TOML writer the project does not have.

- [x] **Step 1: Write the failing tests** (`tests/cli/test_mcp_install.py`)

```python
"""pitwall mcp install: per-harness registration plans (plan Task 4)."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from pitwall.mcp_install import (
    FORWARDED_ENV,
    McpInstallError,
    apply_plan,
    config_path,
    detect_harnesses,
    plan_registration,
    render_snippet,
    server_command,
)

CMD = ["/opt/pitwall/.venv/bin/pitwall-mcp"]


@pytest.fixture
def where(tmp_path: Path) -> dict[str, Any]:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    return {
        "environ": {"HOME": str(home), "PATH": str(tmp_path / "bin")},
        "home": home,
        "project_root": project,
    }


def _plan(where: dict[str, Any], harness: str, scope: str = "user", **kwargs: Any):  # type: ignore[no-untyped-def]
    return plan_registration(harness, scope, command=CMD, **where, **kwargs)  # type: ignore[arg-type]


def test_codex_block_round_trips_foreign_bytes(where: dict[str, Any]) -> None:
    path = config_path("codex", "user", **where)
    path.parent.mkdir(parents=True)
    original = 'model = "gpt-5.6-sol"\n\n[mcp_servers.docs]\ncommand = "docs"\n'
    path.write_text(original, encoding="utf-8")
    backup = apply_plan(_plan(where, "codex"))
    assert backup is not None and backup.read_text(encoding="utf-8") == original
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["mcp_servers"]["pitwall"] == {
        "command": CMD[0],
        "args": [],
        "env_vars": list(FORWARDED_ENV),
    }
    assert data["mcp_servers"]["docs"] == {"command": "docs"}
    assert not _plan(where, "codex").changed
    apply_plan(_plan(where, "codex", remove=True))
    assert path.read_text(encoding="utf-8") == original


def test_codex_refuses_an_unmanaged_pitwall_table(where: dict[str, Any]) -> None:
    path = config_path("codex", "user", **where)
    path.parent.mkdir(parents=True)
    path.write_text('[mcp_servers.pitwall]\ncommand = "other"\n', encoding="utf-8")
    with pytest.raises(McpInstallError, match="outside the pitwall-managed block"):
        _plan(where, "codex", force=True)


@pytest.mark.parametrize(
    ("harness", "scope", "section"),
    [
        ("opencode", "user", "mcp"),
        ("opencode", "project", "mcp"),
        ("claude-code", "project", "mcpServers"),
    ],
)
def test_json_entries_merge_by_value_and_uninstall(
    where: dict[str, Any], harness: str, scope: str, section: str
) -> None:
    path = config_path(harness, scope, **where)  # type: ignore[arg-type]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({section: {"docs": {"command": "docs"}}, "theme": "dark"}), encoding="utf-8"
    )
    apply_plan(_plan(where, harness, scope))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["theme"] == "dark" and data[section]["docs"] == {"command": "docs"}
    entry = data[section]["pitwall"]
    if harness == "opencode":
        assert entry == {
            "type": "local",
            "command": CMD,
            "enabled": True,
            "environment": {v: "{env:" + v + "}" for v in FORWARDED_ENV},
        }
    else:
        assert entry == {
            "type": "stdio",
            "command": CMD[0],
            "args": [],
            "env": {v: "${" + v + ":-}" for v in FORWARDED_ENV},
        }
    assert not _plan(where, harness, scope).changed
    apply_plan(_plan(where, harness, scope, remove=True))
    after = json.loads(path.read_text(encoding="utf-8"))
    assert "pitwall" not in after[section] and after[section]["docs"] == {"command": "docs"}


def test_foreign_entry_needs_force_and_jsonc_is_refused(where: dict[str, Any]) -> None:
    path = config_path("opencode", "user", **where)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"mcp": {"pitwall": {"type": "local", "command": ["someone-else"]}}}),
        encoding="utf-8",
    )
    with pytest.raises(McpInstallError, match="rerun with --force"):
        _plan(where, "opencode")
    assert _plan(where, "opencode", force=True).changed
    path.write_text("{\n  // a comment\n}\n", encoding="utf-8")
    with pytest.raises(McpInstallError, match="not strict JSON"):
        _plan(where, "opencode")


def test_claude_user_scope_uses_the_claude_cli(where: dict[str, Any]) -> None:
    plan = _plan(where, "claude-code")
    assert plan.before == plan.after
    (command,) = plan.commands
    assert list(command[:6]) == ["claude", "mcp", "add-json", "--scope", "user", "pitwall"]
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    apply_plan(plan, run=fake_run)
    assert calls == [list(command)]
    path = config_path("claude-code", "user", **where)
    path.write_text(
        json.dumps({"mcpServers": {"pitwall": json.loads(command[6])}}), encoding="utf-8"
    )
    assert not _plan(where, "claude-code").changed
    removal = _plan(where, "claude-code", remove=True)
    assert removal.commands == (("claude", "mcp", "remove", "--scope", "user", "pitwall"),)


def test_failed_harness_command_raises_with_its_stderr(where: dict[str, Any]) -> None:
    def failing(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "not logged in")

    with pytest.raises(McpInstallError, match="not logged in"):
        apply_plan(_plan(where, "claude-code"), run=failing)


def test_new_user_files_are_private(where: dict[str, Any]) -> None:
    plan = _plan(where, "opencode")
    apply_plan(plan)
    assert plan.path.stat().st_mode & 0o777 == 0o600


def test_server_command_prefers_the_sibling_console_script(tmp_path: Path) -> None:
    python = tmp_path / "python3"
    python.write_text("", encoding="utf-8")
    assert server_command(str(python)) == [str(python), "-m", "pitwall.mcp"]
    script = tmp_path / "pitwall-mcp"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    assert server_command(str(python)) == [str(script)]


def test_detection_uses_binaries_or_configs(where: dict[str, Any], tmp_path: Path) -> None:
    assert detect_harnesses(**where) == []
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    opencode = config_path("opencode", "user", **where)
    opencode.parent.mkdir(parents=True)
    opencode.write_text("{}", encoding="utf-8")
    assert detect_harnesses(**where) == ["codex", "opencode"]


def test_snippets_match_the_registrations(where: dict[str, Any]) -> None:
    snippet = render_snippet("opencode", "project", CMD)
    assert json.loads(snippet) == {
        "mcp": {
            "pitwall": json.loads(
                json.dumps(
                    {
                        "type": "local",
                        "command": CMD,
                        "enabled": True,
                        "environment": {v: "{env:" + v + "}" for v in FORWARDED_ENV},
                    }
                )
            )
        }
    }
    assert render_snippet("claude-code", "user", CMD).startswith(
        "claude mcp add-json --scope user pitwall '"
    )
    assert "[mcp_servers.pitwall]" in render_snippet("codex", "user", CMD)
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `uv run pytest -q tests/cli/test_mcp_install.py 2>&1 | tail -5`
Expected: FAIL with `ModuleNotFoundError: No module named 'pitwall.mcp_install'`.

- [x] **Step 3: Implement `src/pitwall/mcp_install.py`**

```python
"""Register the Pitwall MCP server with coding-agent harnesses (``pitwall mcp install``).

Every registration is named ``pitwall``, launches ``pitwall-mcp`` over stdio,
and forwards the server's required variables by reference, never by value.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

SERVER_NAME = "pitwall"
HARNESSES = ("claude-code", "codex", "opencode")
FORWARDED_ENV = ("RUNPOD_API_KEY", "DATABASE_URL", "REDIS_URL", "PITWALL_CONFIG_FILE")
Scope = Literal["user", "project"]
_BEGIN = "# >>> pitwall mcp (managed by `pitwall mcp install`; edits here are overwritten)"
_END = "# <<< pitwall mcp"
_BINARY = {"claude-code": "claude", "codex": "codex", "opencode": "opencode"}
VERIFY_HINT: dict[tuple[str, str], str] = {
    ("claude-code", "user"): "claude mcp list",
    (
        "claude-code",
        "project",
    ): "restart Claude Code in this project and approve the pitwall server",
    ("codex", "user"): "codex mcp list",
    ("codex", "project"): "codex mcp list, run from this project",
    ("opencode", "user"): "opencode mcp list",
    ("opencode", "project"): "opencode mcp list, run from this project",
}


class McpInstallError(RuntimeError):
    """A harness config cannot be changed safely; nothing was written for that harness."""


@dataclass(frozen=True)
class InstallPlan:
    harness: str
    scope: Scope
    path: Path
    before: str
    after: str
    commands: tuple[tuple[str, ...], ...] = ()

    @property
    def changed(self) -> bool:
        return self.before != self.after or bool(self.commands)


def server_command(executable: str | None = None) -> list[str]:
    """``pitwall-mcp`` beside the interpreter when present, else ``<python> -m pitwall.mcp``."""
    python = Path(executable or sys.executable)
    script = python.with_name("pitwall-mcp")
    if script.is_file() and os.access(script, os.X_OK):
        return [str(script)]
    return [str(python), "-m", "pitwall.mcp"]


def config_path(
    harness: str, scope: Scope, *, environ: Mapping[str, str], home: Path, project_root: Path
) -> Path:
    if harness == "claude-code":
        if scope == "project":
            return project_root / ".mcp.json"
        root = environ.get("CLAUDE_CONFIG_DIR")
        return (Path(root).expanduser() if root else home) / ".claude.json"
    if harness == "codex":
        if scope == "project":
            return project_root / ".codex" / "config.toml"
        codex_home = environ.get("CODEX_HOME")
        return (Path(codex_home).expanduser() if codex_home else home / ".codex") / "config.toml"
    if harness == "opencode":
        if scope == "project":
            return project_root / "opencode.json"
        config_home = environ.get("XDG_CONFIG_HOME")
        return (
            (Path(config_home).expanduser() if config_home else home / ".config")
            / "opencode"
            / "opencode.json"
        )
    raise McpInstallError(f"unknown harness {harness!r}; expected one of {', '.join(HARNESSES)}")


def render_entry(harness: str, command: Sequence[str]) -> dict[str, Any] | str:
    if harness == "claude-code":
        return {
            "type": "stdio",
            "command": command[0],
            "args": list(command[1:]),
            "env": {name: "${" + name + ":-}" for name in FORWARDED_ENV},
        }
    if harness == "opencode":
        return {
            "type": "local",
            "command": list(command),
            "enabled": True,
            "environment": {name: "{env:" + name + "}" for name in FORWARDED_ENV},
        }
    if harness == "codex":
        return (
            "\n".join(
                [
                    _BEGIN,
                    f"[mcp_servers.{SERVER_NAME}]",
                    f"command = {json.dumps(command[0])}",
                    "args = [" + ", ".join(json.dumps(arg) for arg in command[1:]) + "]",
                    "env_vars = [" + ", ".join(json.dumps(name) for name in FORWARDED_ENV) + "]",
                    _END,
                ]
            )
            + "\n"
        )
    raise McpInstallError(f"unknown harness {harness!r}; expected one of {', '.join(HARNESSES)}")


def render_snippet(harness: str, scope: Scope, command: Sequence[str]) -> str:
    """The exact text ``docs/agents`` shows for one harness and scope."""
    entry = render_entry(harness, command)
    if harness == "claude-code" and scope == "user":
        return f"claude mcp add-json --scope user {SERVER_NAME} {shlex.quote(json.dumps(entry))}"
    if harness == "codex":
        assert isinstance(entry, str)
        return entry.rstrip("\n")
    section = "mcpServers" if harness == "claude-code" else "mcp"
    return json.dumps({section: {SERVER_NAME: entry}}, indent=2)


def _is_ours(entry: Any) -> bool:
    if not isinstance(entry, Mapping):
        return False
    command = entry.get("command")
    args: list[Any] = list(entry.get("args") or [])
    if isinstance(command, list):
        command, args = (command[0] if command else ""), command[1:]
    return Path(str(command)).name == "pitwall-mcp" or "pitwall.mcp" in [str(arg) for arg in args]
```

Planning (same file). `_read_json(path)` returns `(before, data)`, raising `McpInstallError(f"{path} is not strict JSON ({exc.msg} at line {exc.lineno}); fix or convert it, then rerun")` on a parse error and requiring a top-level object. `_plan_json(harness, scope, path, section, entry, *, remove, force)` reads the section (must be an object), raises the foreign-entry error unless `force`, then removes, keeps (when equal), or sets the entry; a new OpenCode file starts with `"$schema": "https://opencode.ai/config.json"`; `after = json.dumps(data, indent=2) + "\n"` only when something changed, else `after = before`. The Codex planner and the Claude user-scope planner:

```python
def _strip_block(text: str) -> str:
    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip("\n") == _BEGIN)
        end = next(i for i in range(start, len(lines)) if lines[i].rstrip("\n") == _END)
    except StopIteration:
        return text
    if start > 0 and lines[start - 1].strip() == "":
        start -= 1
    return "".join(lines[:start] + lines[end + 1 :])


def _plan_codex(scope: Scope, path: Path, command: Sequence[str], *, remove: bool) -> InstallPlan:
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    base = _strip_block(before)
    try:
        parsed = tomllib.loads(base)
    except tomllib.TOMLDecodeError as exc:
        raise McpInstallError(f"{path} is not valid TOML ({exc}); fix it, then rerun") from exc
    servers = parsed.get("mcp_servers", {})
    if isinstance(servers, dict) and SERVER_NAME in servers:
        raise McpInstallError(
            f"{path} defines [mcp_servers.{SERVER_NAME}] outside the pitwall-managed block; "
            "remove it by hand, then rerun"
        )
    if remove:
        after = base
    else:
        block = render_entry("codex", command)
        assert isinstance(block, str)
        after = (base.rstrip("\n") + "\n\n" if base.strip() else "") + block
    tomllib.loads(after)  # the rendered file must still parse
    return InstallPlan("codex", scope, path, before, after)


def _plan_claude_user(
    path: Path, command: Sequence[str], *, remove: bool, force: bool
) -> InstallPlan:
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    existing: Any = None
    if before.strip():
        _before, data = _read_json(path)
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            existing = servers.get(SERVER_NAME)
    if existing is not None and not _is_ours(existing) and not force:
        raise McpInstallError(
            f"{path} already has a {SERVER_NAME!r} server that pitwall did not write; rerun with --force to replace it"
        )
    removal = ("claude", "mcp", "remove", "--scope", "user", SERVER_NAME)
    entry = render_entry("claude-code", command)
    commands: tuple[tuple[str, ...], ...]
    if remove:
        commands = (removal,) if existing is not None else ()
    elif existing == entry:
        commands = ()
    else:
        add = ("claude", "mcp", "add-json", "--scope", "user", SERVER_NAME, json.dumps(entry))
        commands = (removal, add) if existing is not None else (add,)
    return InstallPlan("claude-code", "user", path, before, before, commands)


def plan_registration(
    harness: str,
    scope: Scope,
    *,
    environ: Mapping[str, str],
    home: Path,
    project_root: Path,
    command: Sequence[str],
    remove: bool = False,
    force: bool = False,
) -> InstallPlan:
    path = config_path(harness, scope, environ=environ, home=home, project_root=project_root)
    if harness == "claude-code" and scope == "user":
        return _plan_claude_user(path, command, remove=remove, force=force)
    if harness == "codex":
        return _plan_codex(scope, path, command, remove=remove)
    section = "mcpServers" if harness == "claude-code" else "mcp"
    entry = render_entry(harness, command)
    assert isinstance(entry, dict)
    return _plan_json(harness, scope, path, section, entry, remove=remove, force=force)
```

Applying and detecting:

```python
Runner = Callable[..., subprocess.CompletedProcess[str]]


def apply_plan(plan: InstallPlan, *, run: Runner = subprocess.run) -> Path | None:
    """Write the new file atomically (backing up an existing one), then run harness commands."""
    backup: Path | None = None
    if plan.before != plan.after:
        if plan.path.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            backup = plan.path.with_name(f"{plan.path.name}.bak.{stamp}")
            shutil.copy2(plan.path, backup)
            mode = plan.path.stat().st_mode & 0o777
        else:
            mode = 0o600 if plan.scope == "user" else 0o644
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{plan.path.name}.", dir=plan.path.parent)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(plan.after)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, plan.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    for command in plan.commands:
        completed = run(list(command), check=False, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise McpInstallError(
                f"`{' '.join(command[:6])}` exited {completed.returncode}: {completed.stderr.strip()[:300]}"
            )
    return backup


def detect_harnesses(*, environ: Mapping[str, str], home: Path, project_root: Path) -> list[str]:
    """Harnesses with a binary on PATH or an existing user config; filesystem only."""
    found: list[str] = []
    for harness in HARNESSES:
        user_config = config_path(
            harness, "user", environ=environ, home=home, project_root=project_root
        )
        if shutil.which(_BINARY[harness], path=environ.get("PATH")) or user_config.exists():
            found.append(harness)
    return found
```

- [x] **Step 4: Run the tests**

Run: `uv run pytest -q tests/cli/test_mcp_install.py 2>&1 | tail -3`
Expected: all pass.

- [x] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict src/ 2>&1 | tail -1`
Expected: `All checks passed!` and `Success: no issues found in N source files`.

```bash
git add src/pitwall/mcp_install.py tests/cli/test_mcp_install.py
git commit -s -m "feat(mcp): registration plans for Claude Code, Codex, and OpenCode"
```

## Task 5: `pitwall mcp install|uninstall`

**Lane:** registrar.

**Files:**
- Create: `src/pitwall/cli_mcp_install.py`
- Modify: `src/pitwall/cli.py` (`_parse_mcp_serve_args` :2577 — add `install` and `uninstall` subparsers; `cmd_mcp_serve` :2601 — parse first, and route `install`/`uninstall` to `cli_mcp_install.cmd_mcp_install` before the `pitwall.mcp` import and `ensure_runtime_env()`, so registering never requires the server's runtime variables)
- Modify: `tests/cli/test_mcp_install.py` (append CLI tests)

**Interfaces:**
- `pitwall mcp install [HARNESS ...] [--scope user|project] [--project-root PATH] [--dry-run] [--force] [--json]` and `pitwall mcp uninstall` (same flags). No harness arguments means the detected harnesses; none detected prints `no supported harness found (claude-code, codex, opencode); name one explicitly` and exits 1. Each harness prints `== <harness> (<scope>): <path>`, a unified diff and any commands (or `already registered` / `not registered`), then `wrote <path> (backup <path>)` and `verify: <hint>` unless `--dry-run`. A harness that fails prints `error: <message>`, and the rest continue. Exit 0 when every harness succeeded, else 1. `--json` prints `{"results": [{"harness", "scope", "path", "changed", "dry_run", "backup", "commands", "verify", "error"}]}`. When `DATABASE_URL` is unset in the current shell, install also prints one note: `pitwall-mcp needs DATABASE_URL, REDIS_URL, and RUNPOD_API_KEY where the harness runs; pitwall doctor checks them`.
- `--project-root` defaults to the current directory. `--scope` defaults to `user`.

- [x] **Step 1: Write the failing tests** (append to `tests/cli/test_mcp_install.py`)

```python
from pitwall import cli


def _cli_env(monkeypatch: pytest.MonkeyPatch, where: dict[str, Any]) -> None:
    monkeypatch.setenv("HOME", str(where["home"]))
    monkeypatch.setenv("PATH", where["environ"]["PATH"])
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("pitwall.cli_mcp_install.server_command", lambda: CMD)


def test_cli_dry_run_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    assert cli.main(["mcp", "install", "codex", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[mcp_servers.pitwall]" in out
    assert not config_path("codex", "user", **where).exists()


def test_cli_install_uninstall_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    assert (
        cli.main(
            [
                "mcp",
                "install",
                "opencode",
                "--scope",
                "project",
                "--project-root",
                str(where["project_root"]),
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert (result["harness"], result["changed"], result["error"]) == ("opencode", True, None)
    assert result["verify"] == "opencode mcp list, run from this project"
    assert (
        cli.main(
            [
                "mcp",
                "uninstall",
                "opencode",
                "--scope",
                "project",
                "--project-root",
                str(where["project_root"]),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    data = json.loads((where["project_root"] / "opencode.json").read_text(encoding="utf-8"))
    assert "pitwall" not in data["mcp"]


def test_cli_reports_a_failing_harness_and_continues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    bad = config_path("opencode", "user", **where)
    bad.parent.mkdir(parents=True)
    bad.write_text("{ not json", encoding="utf-8")
    assert cli.main(["mcp", "install", "opencode", "codex"]) == 1
    out = capsys.readouterr()
    assert "error:" in out.out + out.err
    assert config_path("codex", "user", **where).exists()


def test_cli_without_harnesses_detected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    assert cli.main(["mcp", "install"]) == 1
    assert "no supported harness found" in capsys.readouterr().err


def test_install_does_not_need_the_server_runtime(
    monkeypatch: pytest.MonkeyPatch, where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)

    def explode() -> None:
        raise AssertionError("install must not validate the MCP runtime")

    monkeypatch.setattr("pitwall.mcp.ensure_runtime_env", explode)
    assert cli.main(["mcp", "install", "codex", "--dry-run"]) == 0
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `uv run pytest -q tests/cli/test_mcp_install.py 2>&1 | tail -5`
Expected: FAIL — argparse rejects `install` (`invalid choice: 'install'`, exit 2).

- [x] **Step 3: Implement.** In `_parse_mcp_serve_args`, add two subparsers sharing one helper that adds `harnesses` (`nargs="*"`, `choices=HARNESSES`), `--scope` (`choices=("user", "project")`, default `user`), `--project-root` (default `.`), `--dry-run`, `--force`, and `--json`. In `cmd_mcp_serve`, call `_parse_mcp_serve_args(argv)` first; when `args.command in {"install", "uninstall"}`, `from pitwall.cli_mcp_install import cmd_mcp_install` and `return cmd_mcp_install(args)`; otherwise keep today's serve path unchanged. `cli_mcp_install.cmd_mcp_install(args) -> int` implements the behavior in the Interfaces block. It imports `server_command`, `detect_harnesses`, `plan_registration`, `apply_plan`, and `VERIFY_HINT` at module level from `pitwall.mcp_install` (the tests patch `pitwall.cli_mcp_install.server_command`), takes `home` from `Path(os.environ.get("HOME") or Path.home())`, uses `difflib.unified_diff` for the diff, writing `error:` lines through `Output.print_error` (stderr) and collecting JSON results with `Output.set_json`.

- [x] **Step 4: Run the tests, including the existing `mcp serve` coverage**

Run: `uv run pytest -q tests/cli/test_mcp_install.py tests/cli/test_cli_dispatch.py -k "mcp or install" 2>&1 | tail -3`
Expected: all pass (the three `mcp serve` tests at `tests/cli/test_cli_dispatch.py:1150-1210` still pass).

- [x] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict src/ 2>&1 | tail -1`
Expected: `All checks passed!` and `Success: no issues found in N source files`.

```bash
git add src/pitwall/cli_mcp_install.py src/pitwall/cli.py tests/cli/test_mcp_install.py
git commit -s -m "feat(cli): pitwall mcp install and uninstall for Claude Code, Codex, and OpenCode"
```

## Task 6: Agent-facing install docs (proposal 7)

**Lane:** docs (after the doctor and registrar lanes land; worktree `/tmp/aiw-docs` on branch `aiw-docs`, created from the updated `feat/agent-install-first-wave`).

**Files:**
- Create: `docs/agents/README.md`, `docs/agents/install.md`, `docs/agents/claude-code.md`, `docs/agents/codex.md`, `docs/agents/opencode.md`, `tests/docs/__init__.py`, `tests/docs/test_agent_docs.py`
- Modify: `README.md` (new "Install with an AI agent" section after Quick Start), `docs/sdlc/18-cli.md` (components for `cli_doctor.py`, `cli_mcp_install.py`, `doctor.py`, `mcp_install.py`; command-inventory and exit-code rows for `doctor`, `mcp install`, `mcp uninstall`), `CHANGELOG.md` (`[Unreleased]` → `Added`)

**Content:**
- `docs/agents/README.md`: who the guide is for; how to use it ("point your agent at `docs/agents/install.md`"); the page list; the public/local split (this directory is the public guide; a checkout may also hold a local, uncommitted `AGENTS.md`).
- `docs/agents/install.md`, the walkthrough an agent follows, with a verification step after each stage:
  1. Prerequisites and toolchain: Python 3.14.7 through `uv`, Docker for the registry path, and `uv sync --frozen --extra dev --python 3.14.7`.
  2. Choose a mode: personal (only a RunPod credential; no database) or registry (Postgres, Redis, API). The mode is decided by `DATABASE_URL`, exactly as `pitwall doctor` reports.
  3. Personal path: `uv run pitwall setup`, then `uv run pitwall doctor` until it reports no `fail`.
  4. Registry path: `docker compose -f docker-compose.testinfra.yml up -d --wait`, the exports from the README quickstart, `uv run pitwall db migrate`, `uv run pitwall init --non-interactive`, `uv run pitwall-api` in a second terminal, then `uv run pitwall doctor --canary embedding.demo`.
  5. Register the MCP server: `uv run pitwall mcp install --dry-run`, review, then `uv run pitwall mcp install`, then the printed `verify:` command.
  6. What an agent must not do: never run paid operations without the user's explicit authorization; never print or commit credentials.
  7. The doctor check catalogue from Task 1 (id, meaning, fix), so an agent can act on any report line.
- `docs/agents/claude-code.md`, `codex.md`, `opencode.md`: the install and uninstall commands for each scope, the exact entry each writes, where the file lives, how the harness is expected to pass the four variables, and how to verify inside the harness. Each entry is shown in a fenced block directly after the marker `<!-- pitwall-mcp-install: <harness> <scope> -->`, rendered with the placeholder command `/path/to/pitwall-mcp`.
- `README.md`: a short "Install with an AI agent" section that names `docs/agents/install.md`, `pitwall doctor`, and `pitwall mcp install`.
- `CHANGELOG.md` `Added`: one line each for `pitwall doctor` (with the `pitwall_doctor` MCP tool), `pitwall mcp install|uninstall`, and `docs/agents/`.

- [x] **Step 1: Write the failing docs test** (`tests/docs/test_agent_docs.py`)

```python
"""docs/agents shows exactly what the registrar writes and what doctor checks (plan Task 6)."""

from __future__ import annotations

import re
from pathlib import Path

from pitwall.mcp_install import HARNESSES, render_snippet

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "docs" / "agents"
PLACEHOLDER = ["/path/to/pitwall-mcp"]
MARKER = re.compile(
    r"<!-- pitwall-mcp-install: (?P<harness>[a-z-]+) (?P<scope>user|project) -->\n```[a-z]*\n(?P<body>.*?)\n```",
    re.S,
)
DOCTOR_IDS = (
    "install.python",
    "config.file",
    "personal.runpod_credential",
    "personal.endpoint_key",
    "personal.routing_cli",
    "personal.leases",
    "registry.mode",
    "config.runtime",
    "db.connect",
    "db.migrations",
    "registry.capabilities",
    "registry.providers",
    "redis.connect",
    "spend.budget",
    "spend.kill_switch",
    "spend.burn_rate",
    "api.health",
    "canary.dry_run",
)


def _blocks() -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for page in AGENTS.glob("*.md"):
        for match in MARKER.finditer(page.read_text(encoding="utf-8")):
            found[(match["harness"], match["scope"])] = match["body"]
    return found


def test_every_harness_and_scope_is_documented_verbatim() -> None:
    blocks = _blocks()
    for harness in HARNESSES:
        for scope in ("user", "project"):
            assert blocks.get((harness, scope)) == render_snippet(harness, scope, PLACEHOLDER), (
                harness,
                scope,
            )  # type: ignore[arg-type]


def test_install_walkthrough_covers_every_doctor_check_and_the_verify_steps() -> None:
    text = (AGENTS / "install.md").read_text(encoding="utf-8")
    for check_id in DOCTOR_IDS:
        assert f"`{check_id}`" in text, check_id
    for command in (
        "pitwall doctor",
        "pitwall mcp install --dry-run",
        "pitwall db migrate",
        "pitwall setup",
    ):
        assert command in text, command


def test_readme_points_agents_at_the_guide() -> None:
    assert "docs/agents/install.md" in (ROOT / "README.md").read_text(encoding="utf-8")
```

- [x] **Step 2: Run it and watch it fail**

Run: `uv run pytest -q tests/docs/test_agent_docs.py 2>&1 | tail -5`
Expected: FAIL — `docs/agents/install.md` does not exist.

- [x] **Step 3: Write the pages** as specified above. Generate each marked block by running `uv run python -c 'from pitwall.mcp_install import render_snippet; print(render_snippet("codex", "user", ["/path/to/pitwall-mcp"]))'` (and likewise for each harness and scope) and pasting the output verbatim.

- [x] **Step 4: Validate**

Run: `uv run pytest -q tests/docs/test_agent_docs.py 2>&1 | tail -3 && uv run python tools/ci/check_markdown_links.py | tail -1`
Expected: `3 passed`; `markdown links passed: N files (internal)`.

- [x] **Step 5: Commit**

```bash
git add docs/agents tests/docs README.md docs/sdlc/18-cli.md CHANGELOG.md
git commit -s -m "docs(agents): public install guide for coding agents with verbatim MCP registrations"
```

## Task 7: Close-out

**Lane:** docs.

**Files:**
- Modify: `docs/research/2026-09-10-frontier-agent-integration.md` (Status line), this plan (tick every box)

- [x] **Step 1: Record status.** In the research doc's Status line, add that proposals 4, 5, and 7 are implemented on `feat/agent-install-first-wave` under this plan, and that the other eight remain unselected.
- [x] **Step 2: Full validation from a clean tree**

```bash
git status --short                                   # expect no output
uv run pytest -q -m "not integration and not slow" -p no:randomly 2>&1 | tail -3
uv run ruff check . && uv run mypy --strict src/ 2>&1 | tail -1
uv run python tools/ci/check_markdown_links.py | tail -1
uv run pitwall doctor; echo "doctor exit=$?"
uv run pitwall mcp install --dry-run; echo "install exit=$?"
```

Expected: clean status; the suite passes with at least the Task 0 count plus the new tests; ruff and mypy clean; links pass. `pitwall doctor` prints the table for this machine's mode and exits 0 or 1 according to its own checks (quote the output). The dry run prints a plan for each detected harness and writes nothing (`git status` and the harness config timestamps unchanged).

- [x] **Step 3: Live registration check (operator-run; writes to a throwaway project only)**

```bash
PROJ="$(mktemp -d)" && uv run pitwall mcp install claude-code codex opencode --scope project --project-root "$PROJ"
cat "$PROJ/.mcp.json" "$PROJ/.codex/config.toml" "$PROJ/opencode.json"
(cd "$PROJ" && opencode mcp list; codex mcp list)
uv run pitwall mcp uninstall claude-code codex opencode --scope project --project-root "$PROJ"
```

Expected: three files with the `pitwall` entry; `opencode mcp list` and `codex mcp list` show `pitwall` (its status reflects whether the registry variables are set in this shell); uninstall leaves `opencode.json` and `.mcp.json` with an empty server map and the Codex file with no managed block. User-scope installs change your real harness configs and stay your call.

- [x] **Step 4: Commit** (`git commit -s -m "docs: frontier agent integration first wave implemented"`). Merging to `main` and pushing wait for the operator.

---

## Review and live-check fixes (2026-09-11)

The three Sonnet lanes reported DONE. Review against the repo and a live check against the real harness CLIs found five defects. Each was fixed with a test that fails on the lane's code and passes on the fix.

### Task 8: Install output never echoes a harness config

- [x] `pitwall mcp install` printed a unified diff of the harness config. Re-indenting compact JSON made the diff span the whole file, so another server's `apiKey` reached the output, and with it an agent transcript. It now prints only the `pitwall` entry (`render_snippet`) and shell-quoted commands. Test: `tests/cli/test_mcp_install.py::test_cli_output_never_echoes_foreign_config_values`. Commit `737cdc5`.

### Task 9: Bound doctor's Postgres queries

- [x] The probe pool's connect timeout did not cover queries; `command_timeout=timeout` bounds them. Commit `da4df59`.

### Task 10: Readable, copyable output at 80 columns

- [x] Agents read CLI output through 80-column pipes. The doctor table ellipsized check ids, and Rich wrapped the Codex `env_vars` array into invalid TOML and broke the `claude mcp add-json` command. `Output.print` gained `soft_wrap`; `doctor` prints `[status] check.id: detail` lines with `next:` steps. Tests: `test_cli_lines_keep_full_ids_and_next_steps_at_80_columns`, `test_cli_snippets_print_unwrapped_at_80_columns`. Commit `a44d166`.

### Task 11: Codex user scope only; full error lines

- [x] Live check (Task 7 Step 3): project `.mcp.json` shows in `claude mcp list` as "Pending approval"; project `opencode.json` shows in `opencode mcp list`; a project `.codex/config.toml` is ignored by `codex mcp list`, even with the project trusted. `SCOPES` now records each harness's loadable scopes and Codex project scope fails with "rerun with --scope user". The boxed error panel cut that remedy off at 80 columns, so errors print as full stderr lines. User scope, verified with redirected config homes and placeholder registry variables: Claude Code `Connected`, OpenCode `connected`, Codex lists the entry with its forwarded variable names. Tests: `test_codex_is_user_scope_only`, `test_cli_codex_project_scope_fails_cleanly`. Commit `b88df29`.

### Task 12: Accurate `pitwall setup` description

- [x] `docs/agents/install.md` said setup "offers to attach the Claude Code plugin"; it offers to export the endpoint key from the shell profile and reports whether `pitwall-agent-routing` is on `PATH`.

---

## Spec coverage map

| Research-doc requirement | Task |
| --- | --- |
| P4: detect harnesses; explicit harness list; `--project`/`--user` scope | 4, 5 |
| P4: merge by key, preserve foreign entries, refuse conflicting `pitwall` entries unless `--force`, backups, exact-inverse uninstall | 4 (Decision 6 narrows JSON to value preservation) |
| P4: fail closed on unparseable configs; per-harness errors reported; filesystem-only detection | 4, 5 |
| P4: server command resolved from the running environment | 4 |
| P4: print the harness-native verify step | 4, 5 |
| P5: install, services, registry, spend, and dry-run phases; `{id, status, detail, next_step}` | 1 |
| P5: personal vs registry as a first-class axis | 1 |
| P5: `--json`, `--strict`, non-zero exit on failure | 2 |
| P5: exposed as an MCP tool | 3 |
| P5: bounded, offline, no paid calls; canary dry-run only | 1, 2 |
| P7: committed agent docs; public/local split | 6 (Decision 1) |
| P7: snippets identical to registrar output; docs-check green | 6 |
| Sequencing: first wave 4 → 7 → 5 | this plan |
