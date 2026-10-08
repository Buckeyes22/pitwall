# Subscription Usage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show usage for every coding subscription on a machine in one command, let the orchestrating model read it before choosing a route, and serve it so the desk meter keeps working once the separate submeter repository is retired.

**Architecture:** A new `usage` package in Agent Routing holds one read-only reader per plan, all returning the same row. Accounts come from login directories and from routes that name a second login directory. One command prints the rows; a serve mode samples them on a schedule and answers the desk meter's existing payload. The broker is not touched.

**Tech Stack:** Python 3.14.7, standard library only (`urllib`, `http.server`, `threading`, `json`), `unittest`, `uv`. The firmware is unchanged C++ for PlatformIO.

**Spec:** `docs/superpowers/specs/2026-09-28-subscription-usage-design.md`. This plan covers stages 1 and 2. Stage 3, the dashboard, gets its own design pass first.

## Global Constraints

- Agent Routing runtime code is standard library only. No new dependency in `packages/agent-routing/pyproject.toml`.
- Python 3.14.7. Run Agent Routing commands from `packages/agent-routing` with `uv run --frozen`. Never bare `python`.
- Readers only read. No reader refreshes a token, writes a credential file, or sends anything but one GET.
- A token or key value is never printed, logged, cached, or served. Failure text is a status code plus fixed wording, never a response body.
- Status rules: `warn` at 80 percent or more, `limit` at 100 percent or more or when the source reports it. `error`, `stale`, and `unknown` mean no information.
- The routing skill's reserve is 10 percent: avoid an account at or above 90 percent when another is below it.
- Reader intervals: `claude` 300 seconds, every other plan 60 seconds.
- `usage serve`: default `127.0.0.1:8848`, default interval 45 seconds, minimum 30, token from `PITWALL_AGENT_ROUTING_USAGE_TOKEN`, required on any address other than loopback.
- Desk meter payload: at most 7 rows, account tags of 3 characters, the five statuses `ok`, `warn`, `limit`, `error`, `stale`, and `Content-Length` on every response.
- Account labels are 1 to 16 letters, digits, or hyphens.
- Every commit is signed off (`git commit -s`) and ends with the session's attribution trailers.
- Documentation changes ship with the behaviour they describe. No committed text contains a home directory path, a machine name, or a LAN address.
- The link checker reads code fences in this plan. Document text embedded here writes a link's target in curly brackets, as `[text]{target}`; use round brackets in the real file.
- A test line that sets a fake key carries `# pragma: allowlist secret`, as shown in the test code below.
- The database test suites share one Postgres database. Never run two of them at once.

## Review Focus

Inputs the spec implies but does not spell out, most likely first. Each has its test in the task named.

1. A credential file that is valid JSON but the wrong shape (`[]`, `null`, an empty token): the row is `error` and names the file; no traceback. Tested in Task 6 (`test_a_credentials_file_of_the_wrong_shape_is_a_read_error`), Task 4 (`test_a_store_that_is_not_an_object_is_ignored`), and Task 10 (`test_a_reader_that_trips_on_an_odd_shape_is_an_unexpected_response`).
2. A cache file cut off mid-write or holding the wrong types: it is read as no cache. Tested in Task 5 (`test_a_damaged_file_reads_as_no_cache`) and Task 10 (`test_a_damaged_cache_file_is_read_as_no_cache`).
3. A reset time already in the past: the desk meter's minutes are 0, never negative. Tested in Task 14 (`test_minutes_are_computed_from_the_reset_instant_and_never_negative`).
4. Two account labels that match once cut to three characters: the tags become `1`, `2`. Tested in Task 14 (`test_account_tags`).
5. A refresh that raises while `usage serve` is running, such as a routes file made unreadable: the server keeps the rows it has. Tested in Task 15 (`test_a_refresh_that_raises_keeps_the_rows_already_held`).

A vendor percentage above 100 is kept as reported and is a `limit`; tested in Task 2.

## File Structure

New, under `packages/agent-routing/runtime/model_routing/usage/`:

| File | Responsibility |
|---|---|
| `rows.py` | `Row`, `Window`, `Reading`, the status rules, rounding, instants, the JSON fetch, the table |
| `accounts.py` | Which plans and accounts exist on this machine, and which routes reach them |
| `cache.py` | One file per account: last good row, last failure, last attempt |
| `claude.py`, `codex.py`, `glm.py`, `minimax.py`, `model_studio.py` | One reader each |
| `__init__.py` | The reader registry and `collect()` |
| `serve.py` | Stage 2: sampling, the two payloads, the token rule, the HTTP server |

Changed:

| File | Change |
|---|---|
| `packages/agent-routing/runtime/model_routing/routes.py` | The `account` field: `ACCOUNT_LABEL`, `_ENTRY_KEYS`, `validate_routes`, `add_route` |
| `packages/agent-routing/runtime/model_routing/cli.py` | `routes add --account`; `usage`; `usage serve` (`build_parser`, `main`, `_usage`, `_usage_serve`) |
| `packages/agent-routing/runtime/model_routing/resources/schemas/routes.schema.json` | The `account` property |
| `packages/agent-routing/plugins/pitwall/hooks/dag-tripwire.py` | The second-account exception |
| The three `plugins/*/skills/subagent-model-routing/SKILL.md` files | The usage rules and each host's exception |
| `tests/release/test_cli_all_commands_journey.py`, `tests/release/cli_fixtures.json`, `scripts/release/run-user-journeys.sh`, `release_acceptance/*.json` | Surface counts and fixtures |

New outside Agent Routing: `examples/desk-meter/` (firmware), `qa/concepts/subscription-usage.md`.

---

### Task 1: Branch, worktree, and a green baseline

**Files:**
- Move: `docs/superpowers/specs/2026-09-28-subscription-usage-design.md` and this plan into the new worktree

**Interfaces:**
- Consumes: the tip of `feat/model-studio` (`f59e971`).
- Produces: the worktree `../pitwall-usage` on branch `feat/subscription-usage`. Every later task runs there.

- [x] **Step 1: Create the worktree**

Run from `the repository root`:

```bash
git worktree add ../pitwall-usage -b feat/subscription-usage feat/model-studio
mv docs/superpowers/specs/2026-09-28-subscription-usage-design.md ../pitwall-usage/docs/superpowers/specs/
mv docs/superpowers/plans/2026-09-28-subscription-usage.md ../pitwall-usage/docs/superpowers/plans/
git status --short
```

Expected: `git status --short` prints nothing: the original checkout is clean again.

- [x] **Step 2: Install both projects**

Run from `the repository root`:

```bash
cd ../pitwall-usage
uv sync --frozen --extra dev --python 3.14.7
npm ci --ignore-scripts --prefix packages/pi-workbench
(cd packages/agent-routing && uv sync --frozen --group dev --python 3.14.7)
```

Expected: each command exits 0.

- [x] **Step 3: Confirm the baseline is green**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest discover -s tests 2>&1 | tail -3
```

Expected: the last line is `OK` (skips are allowed).

- [x] **Step 4: Commit the spec and plan**

Run from the root of `../pitwall-usage`:

```bash
git add docs/superpowers/specs/2026-09-28-subscription-usage-design.md docs/superpowers/plans/2026-09-28-subscription-usage.md
git commit -s -m "docs(spec): subscription usage design and implementation plan"
```

---

### Task 2: Rows and status rules

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/usage/__init__.py` (empty for now)
- Create: `packages/agent-routing/runtime/model_routing/usage/rows.py`
- Test: `packages/agent-routing/tests/test_usage_rows.py`

**Interfaces:**
- Consumes: nothing.
- Produces, all in `model_routing.usage.rows`:
  - `Window(name: str, used_pct: int | None, resets_at: str | None)` with `to_dict()`
  - `Reading(tier: str, windows: tuple[Window, ...], detail: str = "", limit_reached: bool = False)`
  - `Row(plan, account, routes: tuple[str, ...], label, tier, windows, status, detail, observed_at)` with `to_dict()` and `Row.from_dict(value)`
  - `ReadError`, `Unmeasured` (exceptions), `Opener` (type), `STATUSES`, `DETAIL_MAX`
  - `percent(value) -> int | None`, `derive_status(windows, *, limit_reached=False) -> str`
  - `iso_utc(moment) -> str`, `parse_instant(value) -> datetime`, `from_epoch(seconds) -> str`
  - `fetch_json(outbound, opener, *, timeout=15.0) -> Any`
  - `render_table(rows, *, now) -> str`

- [x] **Step 1: Write the failing test**

Create `packages/agent-routing/tests/test_usage_rows.py`:

```python
"""Usage rows: status rules, rounding, instants, the JSON fetch, and the table."""

from __future__ import annotations

from datetime import datetime, timezone
import io
from pathlib import Path
import sys
import unittest
from urllib import error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.usage import rows  # noqa: E402

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class StatusTests(unittest.TestCase):
    def test_thresholds(self) -> None:
        def status(*values: int | None) -> str:
            return rows.derive_status([rows.Window("5h", value, None) for value in values])

        self.assertEqual("ok", status(0, 79))
        self.assertEqual("warn", status(80, 10))
        self.assertEqual("warn", status(None, 99))
        self.assertEqual("limit", status(100, 0))
        self.assertEqual("ok", status(None, None))

    def test_a_reported_limit_wins_over_the_numbers(self) -> None:
        windows = [rows.Window("5h", 10, None)]
        self.assertEqual("limit", rows.derive_status(windows, limit_reached=True))

    def test_a_vendor_value_above_one_hundred_is_kept_and_is_a_limit(self) -> None:
        self.assertEqual(104, rows.percent(104.2))
        self.assertEqual("limit", rows.derive_status([rows.Window("7d", 104, None)]))


class PercentTests(unittest.TestCase):
    def test_rounds_half_up_and_refuses_non_numbers(self) -> None:
        self.assertEqual(1, rows.percent(0.5))
        self.assertEqual(42, rows.percent(41.5))
        self.assertEqual(0, rows.percent(0))
        for value in (None, "41", True, float("nan"), float("inf")):
            self.assertIsNone(rows.percent(value))


class InstantTests(unittest.TestCase):
    def test_instants_are_normalised_to_utc(self) -> None:
        self.assertEqual(
            "2026-09-28T14:20:00Z", rows.iso_utc(rows.parse_instant("2026-09-28T16:20:00+02:00"))
        )
        self.assertEqual(
            "2026-09-28T14:20:00Z", rows.iso_utc(rows.parse_instant("2026-09-28T14:20:00Z"))
        )
        self.assertEqual("2026-09-28T12:00:00Z", rows.from_epoch(NOW.timestamp()))


class RowTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        row = rows.Row(
            "claude",
            "work",
            ("claude-work",),
            "Claude",
            "Max 20x",
            (rows.Window("5h", 41, "2026-09-28T14:20:00Z"),),
            "ok",
            "",
            "2026-09-28T12:00:00Z",
        )
        self.assertEqual(row, rows.Row.from_dict(row.to_dict()))

    def test_an_unknown_status_is_refused(self) -> None:
        value = rows.Row("glm", "", (), "GLM", "", (), "ok", "", "2026-09-28T12:00:00Z").to_dict()
        value["status"] = "fine"
        with self.assertRaises(ValueError):
            rows.Row.from_dict(value)


class FetchTests(unittest.TestCase):
    def test_json_is_returned(self) -> None:
        self.assertEqual(
            {"a": 1}, rows.fetch_json(object(), lambda *_a, **_k: _Response(b'{"a": 1}'))
        )  # type: ignore[arg-type]

    def test_failures_carry_a_code_and_never_a_body(self) -> None:
        def refuse(*_a: object, **_k: object) -> object:
            raise error.HTTPError(
                "https://example.invalid",
                401,
                "Unauthorized",
                None,
                io.BytesIO(b"echoed-credential"),
            )  # type: ignore[arg-type]

        with self.assertRaises(rows.ReadError) as caught:
            rows.fetch_json(object(), refuse)  # type: ignore[arg-type]
        self.assertEqual("HTTP 401", str(caught.exception))

        def unreachable(*_a: object, **_k: object) -> object:
            raise error.URLError("refused")

        with self.assertRaises(rows.ReadError) as caught:
            rows.fetch_json(object(), unreachable)  # type: ignore[arg-type]
        self.assertEqual("network failure", str(caught.exception))

        with self.assertRaises(rows.ReadError) as caught:
            rows.fetch_json(object(), lambda *_a, **_k: _Response(b"<html>"))  # type: ignore[arg-type]
        self.assertEqual("unexpected response", str(caught.exception))


class TableTests(unittest.TestCase):
    def test_rows_render_with_reset_times_and_routes(self) -> None:
        table = rows.render_table(
            [
                rows.Row(
                    "claude",
                    "work",
                    (),
                    "Claude",
                    "Max 20x",
                    (
                        rows.Window("5h", 92, "2026-09-28T14:20:00Z"),
                        rows.Window("7d", 63, "2026-10-01T09:00:00Z"),
                    ),
                    "warn",
                    "",
                    "2026-09-28T12:00:00Z",
                ),
                rows.Row(
                    "claude",
                    "home",
                    ("claude-home",),
                    "Claude",
                    "Max 5x",
                    (rows.Window("5h", 4, None),),
                    "ok",
                    "",
                    "2026-09-28T12:00:00Z",
                ),
                rows.Row(
                    "model-studio",
                    "",
                    (),
                    "Model Studio",
                    "Pro",
                    (),
                    "unknown",
                    "renews 2026-10-21",
                    "2026-09-28T12:00:00Z",
                ),
            ],
            now=NOW,
        )
        lines = table.splitlines()
        self.assertIn("5h 92% resets 14:20", lines[1])
        self.assertIn("7d 63% resets Thu 09:00", lines[1])
        self.assertTrue(lines[1].rstrip().endswith("(default)"))
        self.assertTrue(lines[2].rstrip().endswith("route claude-home"))
        self.assertIn("renews 2026-10-21", lines[3])

    def test_no_rows(self) -> None:
        self.assertEqual("no subscriptions found on this machine\n", rows.render_table([], now=NOW))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_rows 2>&1 | tail -3
```

Expected: an error ending `ModuleNotFoundError: No module named 'model_routing.usage'`.

- [x] **Step 3: Write the module**

Create `packages/agent-routing/runtime/model_routing/usage/__init__.py` holding one line:

```python
"""Subscription usage: one row per plan and account (stdlib only)."""
```

Create `packages/agent-routing/runtime/model_routing/usage/rows.py`:

```python
"""Usage rows: the shape every reader reduces to, and the status rules (stdlib only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from typing import Any, Callable, Mapping, Sequence
from urllib import error, request

STATUSES = ("ok", "warn", "limit", "error", "stale", "unknown")
WARN_AT = 80
LIMIT_AT = 100
DETAIL_MAX = 120
RESPONSE_CAP = 1024 * 1024
Opener = Callable[..., Any]


class ReadError(Exception):
    """A read failed. The message is fixed wording plus a status code, never a response body."""


class Unmeasured(Exception):
    """The plan is configured but its usage cannot be measured."""


@dataclass(frozen=True, slots=True)
class Window:
    name: str
    used_pct: int | None
    resets_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "used_pct": self.used_pct, "resets_at": self.resets_at}


@dataclass(frozen=True, slots=True)
class Reading:
    tier: str
    windows: tuple[Window, ...]
    detail: str = ""
    limit_reached: bool = False


@dataclass(frozen=True, slots=True)
class Row:
    plan: str
    account: str
    routes: tuple[str, ...]
    label: str
    tier: str
    windows: tuple[Window, ...]
    status: str
    detail: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "account": self.account,
            "routes": list(self.routes),
            "label": self.label,
            "tier": self.tier,
            "windows": [window.to_dict() for window in self.windows],
            "status": self.status,
            "detail": self.detail,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Row:
        windows = tuple(
            Window(str(item["name"]), item.get("used_pct"), item.get("resets_at"))
            for item in value.get("windows", [])
        )
        status = str(value["status"])
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        return cls(
            plan=str(value["plan"]),
            account=str(value.get("account", "")),
            routes=tuple(str(name) for name in value.get("routes", [])),
            label=str(value["label"]),
            tier=str(value.get("tier", "")),
            windows=windows,
            status=status,
            detail=str(value.get("detail", "")),
            observed_at=str(value["observed_at"]),
        )


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def from_epoch(seconds: float) -> str:
    return iso_utc(datetime.fromtimestamp(seconds, tz=timezone.utc))


def percent(value: Any) -> int | None:
    """Round half up to a whole percent; anything that is not a finite number is None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return int(math.floor(value + 0.5))


def derive_status(windows: Sequence[Window], *, limit_reached: bool = False) -> str:
    worst = max((window.used_pct for window in windows if window.used_pct is not None), default=0)
    if limit_reached or worst >= LIMIT_AT:
        return "limit"
    if worst >= WARN_AT:
        return "warn"
    return "ok"


def fetch_json(outbound: request.Request, opener: Opener, *, timeout: float = 15.0) -> Any:
    try:
        with opener(outbound, timeout=timeout) as response:
            body = response.read(RESPONSE_CAP)
    except error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ReadError(f"HTTP {code}") from None
    except error.URLError, OSError:
        raise ReadError("network failure") from None
    try:
        return json.loads(body.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        raise ReadError("unexpected response") from None


def _when(resets_at: str | None, now: datetime) -> str:
    if resets_at is None:
        return ""
    try:
        moment = parse_instant(resets_at)
    except ValueError:
        return ""
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if 0 <= (moment - now).days < 7:
        return moment.strftime("%a %H:%M")
    return moment.strftime("%Y-%m-%d")


def _window_text(window: Window, now: datetime) -> str:
    used = "—" if window.used_pct is None else f"{window.used_pct}%"
    when = _when(window.resets_at, now)
    verb = "renews" if window.name == "30d" else "resets"
    return f"{window.name} {used}" + (f" {verb} {when}" if when else "")


def render_table(rows: Sequence[Row], *, now: datetime) -> str:
    if not rows:
        return "no subscriptions found on this machine\n"
    header = ("plan", "account", "tier", "usage (times are UTC)", "status", "reached by")
    cells = [
        (
            row.plan,
            row.account or "-",
            row.tier or "-",
            "   ".join(_window_text(window, now) for window in row.windows) or row.detail or "-",
            row.status,
            ("route " + ", ".join(row.routes)) if row.routes else "(default)",
        )
        for row in rows
    ]
    widths = [
        max(len(header[index]), *(len(cell[index]) for cell in cells))
        for index in range(len(header))
    ]
    lines = ["  ".join(text.ljust(widths[index]) for index, text in enumerate(header)).rstrip()]
    lines.extend(
        "  ".join(text.ljust(widths[index]) for index, text in enumerate(cell)).rstrip()
        for cell in cells
    )
    return "\n".join(lines) + "\n"
```

- [x] **Step 4: Run the test**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_rows 2>&1 | tail -3
```

Expected: `Ran 11 tests` and `OK`.

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/usage packages/agent-routing/tests/test_usage_rows.py
git commit -s -m "feat(agent-routing): usage rows and status rules"
```

---

### Task 3: The `account` field on a route

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/routes.py` (`SEATS` constants block, `_ENTRY_KEYS`, `validate_routes`, `add_route`)
- Modify: `packages/agent-routing/runtime/model_routing/cli.py` (`build_parser` `routes_add` arguments, `_routes_add`)
- Modify: `packages/agent-routing/runtime/model_routing/resources/schemas/routes.schema.json`
- Modify: `packages/agent-routing/docs/routes.md`
- Create: `packages/agent-routing/tests/fixtures/routes/positive/second-account.json`, `packages/agent-routing/tests/fixtures/routes/negative/account-label-too-long.json`
- Test: `packages/agent-routing/tests/test_routes_account.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `routes.ACCOUNT_LABEL` (compiled pattern); route entries may carry `"account": "<label>"`; `routes.add_route(..., account: str | None = None)`; `routes add --account LABEL`.

- [x] **Step 1: Write the failing test**

Create `packages/agent-routing/tests/test_routes_account.py`:

```python
"""The optional ``account`` field on a route entry."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import cli, routes  # noqa: E402
from model_routing.registry import load_registry  # noqa: E402


def document(entry: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
        "models": {"second": entry},
    }


class AccountFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def test_a_label_is_kept(self) -> None:
        entry = {
            "model": "sonnet",
            "harness": "claude",
            "env": {"CLAUDE_CONFIG_DIR": "/srv/logins/home"},
            "account": "home",
        }
        normalized = routes.validate_routes(document(entry), registry=self.registry)
        self.assertEqual("home", normalized["models"]["second"]["account"])

    def test_bad_labels_are_refused(self) -> None:
        for label in ("", "has space", "a-label-longer-than-sixteen", "under_score", 7):
            with self.subTest(label=label):
                with self.assertRaises(routes.RoutesError) as caught:
                    routes.validate_routes(
                        document({"model": "sonnet", "harness": "claude", "account": label}),
                        registry=self.registry,
                    )
                self.assertIn(
                    "models.second.account must be 1 to 16 letters, digits, or hyphens",
                    str(caught.exception),
                )

    def test_add_route_writes_the_label(self) -> None:
        updated = routes.add_route(
            routes.empty_routes(),
            "second",
            model="sonnet",
            harness="claude",
            env={"CLAUDE_CONFIG_DIR": "/srv/logins/home"},
            account="home",
        )
        self.assertEqual("home", updated["models"]["second"]["account"])


class AccountCliTests(unittest.TestCase):
    def test_routes_add_accepts_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "routes.json"
            env = {
                "HOME": directory,
                "SUBAGENT_MODEL_ROUTING_ROUTES": str(target),
                "PATH": os.environ.get("PATH", ""),
            }
            err = io.StringIO()
            with (
                mock.patch.dict(os.environ, env, clear=True),
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = cli.main(
                    [
                        "routes",
                        "add",
                        "claude-home",
                        "--model",
                        "sonnet",
                        "--harness",
                        "claude",
                        "--env",
                        "CLAUDE_CONFIG_DIR=/srv/logins/home",  # pragma: allowlist secret
                        "--account",
                        "home",
                    ]
                )
            self.assertEqual(0, code, err.getvalue())
            entry = json.loads(target.read_text(encoding="utf-8"))["models"]["claude-home"]
            self.assertEqual("home", entry["account"])
            self.assertEqual({"CLAUDE_CONFIG_DIR": "/srv/logins/home"}, entry["env"])


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_routes_account 2>&1 | tail -4
```

Expected: `FAILED`, with errors naming `models.second has unknown fields: account` and `add_route() got an unexpected keyword argument 'account'`.

- [x] **Step 3: Edit `routes.py`**

In `packages/agent-routing/runtime/model_routing/routes.py`, make five edits.

Edit 1. Find:

```python
SEATS = ("default-author", "critical", "review", "burst", "throughput", "local", "gateway")
```

Replace with:

```python
ACCOUNT_LABEL = re.compile(r"[A-Za-z0-9-]{1,16}")
SEATS = ("default-author", "critical", "review", "burst", "throughput", "local", "gateway")
```

Edit 2. Find:

```python
_ENTRY_KEYS = {
    "model",
    "harness",
    "endpoint",
    "args",
    "env",
    "limits",
    "seat",
    "workspace",
    "taskMode",
    "expiresAt",
    "origin",
    "autoServe",
    "effort",
}
```

Replace with:

```python
_ENTRY_KEYS = {
    "model",
    "harness",
    "endpoint",
    "args",
    "env",
    "limits",
    "seat",
    "workspace",
    "taskMode",
    "expiresAt",
    "origin",
    "autoServe",
    "effort",
    "account",
}
```

Edit 3. Find:

```python
                normalized[key] = entry[key]
        endpoint_object = resolved_endpoint(normalized)
```

Replace with:

```python
                normalized[key] = entry[key]
        if "account" in entry:
            _require(
                isinstance(entry["account"], str) and ACCOUNT_LABEL.fullmatch(entry["account"]) is not None,
                f"{field}.account must be 1 to 16 letters, digits, or hyphens",
            )
            normalized["account"] = entry["account"]
        endpoint_object = resolved_endpoint(normalized)
```

Edit 4. Find:

```python
    auto_serve: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of ``data`` with ``name`` set
```

Replace with:

```python
    auto_serve: Mapping[str, Any] | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Return a copy of ``data`` with ``name`` set
```

Edit 5. Find:

```python
    for key, value in (("seat", seat), ("workspace", workspace), ("taskMode", task_mode)):
```

Replace with:

```python
    for key, value in (("seat", seat), ("workspace", workspace), ("taskMode", task_mode), ("account", account)):
```

- [x] **Step 4: Edit `cli.py`**

In `packages/agent-routing/runtime/model_routing/cli.py`, make two edits.

Edit 1, in `build_parser`. Find:

```python
    routes_add.add_argument("--seat", choices=SEATS)
```

Replace with:

```python
    routes_add.add_argument("--seat", choices=SEATS)
    routes_add.add_argument("--account", metavar="LABEL")
```

Edit 2, in `_routes_add`. Find:

```python
            auto_serve=auto_serve,
        )
        path = save_routes(
```

Replace with:

```python
            auto_serve=auto_serve,
            account=args.account,
        )
        path = save_routes(
```

- [x] **Step 5: Edit the schema and add the fixtures**

In `packages/agent-routing/runtime/model_routing/resources/schemas/routes.schema.json`. Find:

```json
          "taskMode": { "enum": ["read", "write"] },
```

Replace with:

```json
          "taskMode": { "enum": ["read", "write"] },
          "account": { "type": "string", "pattern": "^[A-Za-z0-9-]{1,16}$" },
```

Create `packages/agent-routing/tests/fixtures/routes/positive/second-account.json`:

```json
{
  "schemaVersion": 1,
  "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
  "models": {
    "claude-home": {
      "model": "sonnet",
      "harness": "claude",
      "env": {"CLAUDE_CONFIG_DIR": "/srv/logins/claude-home"},
      "account": "home"
    }
  }
}
```

Create `packages/agent-routing/tests/fixtures/routes/negative/account-label-too-long.json`:

```json
{
  "schemaVersion": 1,
  "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
  "models": {
    "claude-home": {
      "model": "sonnet",
      "harness": "claude",
      "account": "a-label-longer-than-sixteen"
    }
  }
}
```

- [x] **Step 6: Document the field**

In `packages/agent-routing/docs/routes.md`, make two edits.

Edit 1, in the `Entry fields:` sentence. Find:

```markdown
`env` (non-secret extra variables), `limits`, `seat` (
```

Replace with:

```markdown
`env` (non-secret extra variables), `limits`, `account` (a label of 1 to 16 letters, digits, or hyphens for the subscription account the route reaches; see `docs/usage.md`), `seat` (
```

Edit 2, in the Commands table. Find:

```markdown
[--env KEY=VALUE]… [--limits context=N,output=N]` / `routes remove <name>`
```

Replace with:

```markdown
[--env KEY=VALUE]… [--limits context=N,output=N] [--account LABEL]` / `routes remove <name>`
```

- [x] **Step 7: Run the tests**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_routes_account tests.test_routes 2>&1 | tail -3
uv run --frozen python tools/validate_json_schemas.py
```

Expected: `OK`, then `all schemas and representative runtime documents are valid`.

- [x] **Step 8: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/routes.py packages/agent-routing/runtime/model_routing/cli.py packages/agent-routing/runtime/model_routing/resources/schemas/routes.schema.json packages/agent-routing/docs/routes.md packages/agent-routing/tests/fixtures/routes packages/agent-routing/tests/test_routes_account.py
git commit -s -m "feat(agent-routing): an optional account label on a route"
```

---

### Task 4: Accounts

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/usage/accounts.py`
- Test: `packages/agent-routing/tests/test_usage_accounts.py`

**Interfaces:**
- Consumes: `routes.entry_harness`, `routes.resolved_endpoint`, `routes.EndpointReference`, `model_studio.KIND`, `model_studio.is_token_plan`; route entries with `account` (Task 3).
- Produces, in `model_routing.usage.accounts`:
  - `Account(plan: str, label: str, routes: tuple[str, ...] = (), login_dir: Path | None = None, endpoints: tuple[tuple[str, Mapping], ...] = (), problem: str = "")`
  - `discover(routes_config, registry, env, home, *, installed: Callable[[str], bool]) -> list[Account]`
  - `api_key(plan, env, home) -> str | None`, `opencode_key(env, home, entry) -> str | None`
  - `derived_label(plan, directory) -> str`, `expand(value, home) -> Path`
  - `PLAN_ORDER`, `LABELS`, `LOGIN`, `KEYED`, `HARNESS_ONLY`

- [x] **Step 1: Write the failing test**

Create `packages/agent-routing/tests/test_usage_accounts.py`:

```python
"""Accounts are found from login directories, keys, routes, and installed harnesses."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import routes  # noqa: E402
from model_routing.registry import load_registry  # noqa: E402
from model_routing.usage import accounts  # noqa: E402


class AccountDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {"HOME": str(self.home)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def login(self, directory: Path, name: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text("{}", encoding="utf-8")
        return directory

    def config(
        self, models: dict[str, object], endpoints: dict[str, object] | None = None
    ) -> dict[str, object]:
        document = {
            "schemaVersion": 1,
            "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
            "endpoints": endpoints or {},
            "models": models,
        }
        return routes.validate_routes(document, registry=self.registry)

    def discover(
        self, config: dict[str, object], installed: tuple[str, ...] = ()
    ) -> list[accounts.Account]:
        return accounts.discover(
            config,
            self.registry,
            self.env,
            self.home,
            installed=lambda harness: harness in installed,
        )

    def test_nothing_configured_gives_no_accounts(self) -> None:
        self.assertEqual([], self.discover(self.config({})))

    def test_one_login_has_no_label(self) -> None:
        self.login(self.home / ".claude", ".credentials.json")
        found = self.discover(
            self.config({"labelled": {"model": "sonnet", "harness": "claude", "account": "work"}})
        )
        self.assertEqual([accounts.Account("claude", "", (), self.home / ".claude")], found)

    def test_a_route_declares_a_second_login(self) -> None:
        self.login(self.home / ".claude", ".credentials.json")
        second = self.login(self.home / "logins" / ".claude-home", ".credentials.json")
        config = self.config(
            {
                "claude-home": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(second)},
                },
                "claude-home-opus": {
                    "model": "opus",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(second)},
                },
                "claude-work": {"model": "sonnet", "harness": "claude", "account": "work"},
            }
        )
        found = self.discover(config)
        self.assertEqual(
            [
                accounts.Account("claude", "work", (), self.home / ".claude"),
                accounts.Account("claude", "home", ("claude-home", "claude-home-opus"), second),
            ],
            found,
        )

    def test_an_explicit_label_beats_the_directory_name(self) -> None:
        second = self.login(self.home / "logins" / "codex-b", "auth.json")
        config = self.config(
            {
                "spare": {
                    "model": "gpt-5.6-sol",
                    "harness": "codex",
                    "env": {"CODEX_HOME": str(second)},
                    "account": "spare",
                }
            }
        )
        self.assertEqual(
            [accounts.Account("codex", "spare", ("spare",), second)], self.discover(config)
        )

    def test_a_home_relative_directory_is_expanded(self) -> None:
        second = self.login(self.home / "logins" / "second", ".credentials.json")
        config = self.config(
            {
                "two": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": "~/logins/second"},
                }
            }
        )
        self.assertEqual(
            [accounts.Account("claude", "second", ("two",), second)], self.discover(config)
        )

    def test_a_missing_or_relative_login_directory_is_a_problem_that_names_the_route(self) -> None:
        config = self.config(
            {
                "gone": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(self.home / "nowhere")},
                },
                "loose": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": "logins/relative"},
                },
            }
        )
        problems = {account.label: account.problem for account in self.discover(config)}
        self.assertEqual(
            "route gone names a login directory with no login in it", problems["nowhere"]
        )
        self.assertEqual(
            "route loose names a login directory that is not an absolute path", problems["relative"]
        )

    def test_labels_never_collide(self) -> None:
        first = self.login(self.home / "a" / "claude", ".credentials.json")
        second = self.login(self.home / "b" / "claude", ".credentials.json")
        config = self.config(
            {
                "one": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(first)},
                },
                "two": {
                    "model": "sonnet",
                    "harness": "claude",
                    "env": {"CLAUDE_CONFIG_DIR": str(second)},
                },
            }
        )
        self.assertEqual(["2", "2-2"], [account.label for account in self.discover(config)])

    def test_derived_labels(self) -> None:
        for name, expected in (
            (".claude-work", "work"),
            (".claude", "2"),
            ("codex_b", "b"),
            ("my login!", "my-login"),
            ("x" * 40, "x" * 16),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    expected,
                    accounts.derived_label(
                        "claude" if "claude" in name else "codex", Path("/srv") / name
                    ),
                )

    def test_keyed_plans_come_from_the_environment_or_the_opencode_store(self) -> None:
        self.assertEqual([], self.discover(self.config({})))
        store = self.home / ".local" / "share" / "opencode"
        store.mkdir(parents=True)
        (store / "auth.json").write_text(
            json.dumps(
                {
                    "zai-coding-plan": {"type": "api", "key": "k1"},
                    "opencode-go": {"type": "api", "key": "k2"},
                }
            ),
            encoding="utf-8",
        )
        self.env["MINIMAX_API_KEY"] = "k3"  # pragma: allowlist secret
        self.assertEqual(
            ["glm", "minimax", "opencode-go"],
            [account.plan for account in self.discover(self.config({}))],
        )

    def test_a_store_that_is_not_an_object_is_ignored(self) -> None:
        store = self.home / ".local" / "share" / "opencode"
        store.mkdir(parents=True)
        (store / "auth.json").write_text("[]", encoding="utf-8")
        self.assertEqual([], self.discover(self.config({})))

    def test_plans_without_a_reader_need_their_harness(self) -> None:
        found = self.discover(self.config({}), installed=("kimi", "agy"))
        self.assertEqual(["kimi", "gemini"], [account.plan for account in found])

    def test_model_studio_token_plan_endpoints_make_one_account(self) -> None:
        endpoint = {
            "kind": "model-studio",
            "plan": "token-plan-personal",
            "tier": "pro",
            "apiKeyEnv": "MODEL_STUDIO_API_KEY",  # pragma: allowlist secret
            "renewsOn": "2026-09-21",
        }
        payg = {
            "kind": "model-studio",
            "plan": "pay-as-you-go",
            "region": "ap-southeast-1",
            "workspace": "ws-1",
            "apiKeyEnv": "MODEL_STUDIO_PAYG_KEY",  # pragma: allowlist secret
        }
        config = self.config(
            {"flash": {"model": "qwen3.8-flash", "endpoint": "ms", "harness": "opencode"}},
            endpoints={"ms": endpoint, "payg": payg},
        )
        found = self.discover(config)
        self.assertEqual(1, len(found))
        self.assertEqual(
            ("model-studio", "", ("flash",)), (found[0].plan, found[0].label, found[0].routes)
        )
        self.assertEqual(["ms"], [name for name, _endpoint in found[0].endpoints])


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_accounts 2>&1 | tail -3
```

Expected: an error ending `ImportError: cannot import name 'accounts' from 'model_routing.usage'`.

- [x] **Step 3: Write the module**

Create `packages/agent-routing/runtime/model_routing/usage/accounts.py`:

```python
"""Accounts: a plan plus the login or key its usage is read with (stdlib only)."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .. import model_studio as catalog
from ..routes import EndpointReference, entry_harness, resolved_endpoint

PLAN_ORDER = (
    "claude",
    "codex",
    "glm",
    "minimax",
    "model-studio",
    "kimi",
    "grok",
    "muse",
    "gemini",
    "opencode-go",
)
LABELS = {
    "claude": "Claude",
    "codex": "Codex",
    "glm": "GLM",
    "minimax": "MiniMax",
    "model-studio": "Model Studio",
    "kimi": "Kimi",
    "grok": "Grok",
    "muse": "Muse",
    "gemini": "Gemini",
    "opencode-go": "OpenCode Go",
}
# plan -> (harness, login directory variable, default directory name, credential file)
LOGIN = {
    "claude": ("claude", "CLAUDE_CONFIG_DIR", ".claude", ".credentials.json"),
    "codex": ("codex", "CODEX_HOME", ".codex", "auth.json"),
}
# plan -> (key variable, entry in OpenCode's auth store)
KEYED = {
    "glm": ("GLM_API_KEY", "zai-coding-plan"),
    "minimax": ("MINIMAX_API_KEY", "minimax-coding-plan"),
}
# plan -> harness whose installation makes the plan present
HARNESS_ONLY = {"kimi": "kimi", "grok": "grok", "muse": "muse", "gemini": "agy"}
OPENCODE_GO_ENTRY = "opencode-go"
_UNSAFE = re.compile(r"[^A-Za-z0-9-]+")


@dataclass(frozen=True, slots=True)
class Account:
    plan: str
    label: str
    routes: tuple[str, ...] = ()
    login_dir: Path | None = None
    endpoints: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    problem: str = ""


def expand(value: str, home: Path) -> Path:
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value)


def derived_label(plan: str, directory: Path) -> str:
    name = directory.name.lstrip(".")
    if name.lower().startswith(plan):
        name = name[len(plan) :]
    return _UNSAFE.sub("-", name).strip("-")[:16].strip("-") or "2"


def opencode_key(env: Mapping[str, str], home: Path, entry: str) -> str | None:
    base = Path(env["XDG_DATA_HOME"]) if env.get("XDG_DATA_HOME") else home / ".local" / "share"
    try:
        document = json.loads((base / "opencode" / "auth.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    record = document.get(entry) if isinstance(document, dict) else None
    key = record.get("key") if isinstance(record, dict) else None
    return key if isinstance(key, str) and key else None


def api_key(plan: str, env: Mapping[str, str], home: Path) -> str | None:
    variable, entry = KEYED[plan]
    return env.get(variable) or opencode_key(env, home, entry)


def _unique(label: str, used: set[str]) -> str:
    candidate, index = label, 2
    while candidate in used:
        suffix = f"-{index}"
        candidate = label[: 16 - len(suffix)] + suffix
        index += 1
    used.add(candidate)
    return candidate


def _login_accounts(
    plan: str,
    routes_config: Mapping[str, Any],
    registry: Mapping[str, Any],
    env: Mapping[str, str],
    home: Path,
) -> list[Account]:
    harness, variable, default_name, credential = LOGIN[plan]
    default_dir = expand(env[variable], home) if env.get(variable) else home / default_name
    default_label = ""
    default_routes: list[str] = []
    groups: dict[Path, dict[str, Any]] = {}
    models = routes_config.get("models", {})
    for name in sorted(models):
        entry = models[name]
        if entry_harness(entry, routes_config["defaults"], registry) != harness:
            continue
        value = entry.get("env", {}).get(variable)
        label = entry.get("account")
        directory = expand(value, home) if value else default_dir
        if directory == default_dir:
            if value:
                default_routes.append(name)
            if label and not default_label:
                default_label = str(label)
            continue
        group = groups.setdefault(directory, {"routes": [], "label": None})
        group["routes"].append(name)
        if label and group["label"] is None:
            group["label"] = str(label)
    found: list[Account] = []
    used: set[str] = set()
    if (default_dir / credential).is_file():
        label = _unique(default_label, used) if groups and default_label else ""
        found.append(Account(plan, label, tuple(default_routes), default_dir))
    seconds: list[Account] = []
    for directory, group in groups.items():
        routes = tuple(group["routes"])
        problem = ""
        if not directory.is_absolute():
            problem = f"route {routes[0]} names a login directory that is not an absolute path"
        elif not (directory / credential).is_file():
            problem = f"route {routes[0]} names a login directory with no login in it"
        label = _unique(group["label"] or derived_label(plan, directory), used)
        seconds.append(Account(plan, label, routes, directory, problem=problem))
    return found + sorted(seconds, key=lambda account: account.label)


def _token_plan_account(routes_config: Mapping[str, Any]) -> Account | None:
    endpoints: dict[str, Mapping[str, Any]] = {}
    routes: list[str] = []
    models = routes_config.get("models", {})
    for name in sorted(models):
        entry = models[name]
        endpoint = resolved_endpoint(entry)
        if endpoint is None or endpoint.get("kind") != catalog.KIND:
            continue
        if not catalog.is_token_plan(str(endpoint["plan"])):
            continue
        raw = entry.get("endpoint")
        endpoints.setdefault(str(raw) if isinstance(raw, EndpointReference) else name, endpoint)
        routes.append(name)
    for name in sorted(routes_config.get("endpoints", {})):
        endpoint = routes_config["endpoints"][name]
        if endpoint.get("kind") == catalog.KIND and catalog.is_token_plan(str(endpoint["plan"])):
            endpoints.setdefault(name, endpoint)
    if not endpoints:
        return None
    return Account("model-studio", "", tuple(routes), endpoints=tuple(sorted(endpoints.items())))


def discover(
    routes_config: Mapping[str, Any],
    registry: Mapping[str, Any],
    env: Mapping[str, str],
    home: Path,
    *,
    installed: Callable[[str], bool],
) -> list[Account]:
    found: list[Account] = []
    for plan in PLAN_ORDER:
        if plan in LOGIN:
            found.extend(_login_accounts(plan, routes_config, registry, env, home))
        elif plan in KEYED:
            if api_key(plan, env, home):
                found.append(Account(plan, ""))
        elif plan == "model-studio":
            account = _token_plan_account(routes_config)
            if account is not None:
                found.append(account)
        elif plan in HARNESS_ONLY:
            if installed(HARNESS_ONLY[plan]):
                found.append(Account(plan, ""))
        elif opencode_key(env, home, OPENCODE_GO_ENTRY):
            found.append(Account(plan, ""))
    return found
```

- [x] **Step 4: Run the test**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_accounts 2>&1 | tail -3
```

Expected: `Ran 12 tests` and `OK`.

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/usage/accounts.py packages/agent-routing/tests/test_usage_accounts.py
git commit -s -m "feat(agent-routing): find subscription accounts from logins, keys, and routes"
```

---

### Task 5: The cache

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/usage/cache.py`
- Test: `packages/agent-routing/tests/test_usage_cache.py`

**Interfaces:**
- Consumes: `run_store.atomic_write_json`, `run_store.state_root`.
- Produces, in `model_routing.usage.cache`:
  - `path_for(env, plan, account) -> Path`
  - `load(env, plan, account) -> dict | None` returning `{"attemptedAt": float, "row": dict | None, "error": str | None}`
  - `due(entry, plan, now_epoch) -> bool`
  - `store(env, plan, account, now_epoch, *, row, error) -> dict`
  - `INTERVALS`, `DEFAULT_INTERVAL`

- [x] **Step 1: Write the failing test**

Create `packages/agent-routing/tests/test_usage_cache.py`:

```python
"""The per-account cache: intervals, private files, and unreadable content."""

from __future__ import annotations

from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.usage import cache  # noqa: E402

ROW = {
    "plan": "glm",
    "account": "",
    "routes": [],
    "label": "GLM",
    "tier": "Pro",
    "windows": [],
    "status": "ok",
    "detail": "",
    "observed_at": "2026-09-28T12:00:00Z",
}


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"HOME": self.tmp.name, "XDG_STATE_HOME": str(Path(self.tmp.name) / "state")}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_paths_carry_the_account(self) -> None:
        base = Path(self.tmp.name) / "state" / "subagent-model-routing" / "usage"
        self.assertEqual(base / "claude.json", cache.path_for(self.env, "claude", ""))
        self.assertEqual(base / "claude-home.json", cache.path_for(self.env, "claude", "home"))

    def test_round_trip_and_private_modes(self) -> None:
        self.assertIsNone(cache.load(self.env, "glm", ""))
        cache.store(self.env, "glm", "", 1000.0, row=ROW, error=None)
        self.assertEqual(
            {"attemptedAt": 1000.0, "row": ROW, "error": None}, cache.load(self.env, "glm", "")
        )
        path = cache.path_for(self.env, "glm", "")
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))

    def test_intervals(self) -> None:
        self.assertTrue(cache.due(None, "glm", 0.0))
        entry = {"attemptedAt": 1000.0, "row": None, "error": None}
        self.assertFalse(cache.due(entry, "glm", 1059.9))
        self.assertTrue(cache.due(entry, "glm", 1060.0))
        self.assertFalse(cache.due(entry, "claude", 1299.9))
        self.assertTrue(cache.due(entry, "claude", 1300.0))

    def test_a_damaged_file_reads_as_no_cache(self) -> None:
        path = cache.path_for(self.env, "glm", "")
        path.parent.mkdir(parents=True)
        for content in (
            "",
            '{"attemptedAt": 1000.0, "row": {"pl',
            "[]",
            '{"attemptedAt": "soon"}',
            '{"attemptedAt": true}',
        ):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                self.assertIsNone(cache.load(self.env, "glm", ""))

    def test_odd_fields_are_dropped_not_trusted(self) -> None:
        path = cache.path_for(self.env, "glm", "")
        path.parent.mkdir(parents=True)
        path.write_text('{"attemptedAt": 5, "row": "text", "error": 7}', encoding="utf-8")
        self.assertEqual(
            {"attemptedAt": 5.0, "row": None, "error": None}, cache.load(self.env, "glm", "")
        )


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_cache 2>&1 | tail -3
```

Expected: an error ending `ImportError: cannot import name 'cache' from 'model_routing.usage'`.

- [x] **Step 3: Write the module**

Create `packages/agent-routing/runtime/model_routing/usage/cache.py`:

```python
"""One file per account: the last good row, the last failure, and the last attempt (stdlib only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..run_store import atomic_write_json, state_root

DEFAULT_INTERVAL = 60.0
INTERVALS = {"claude": 300.0}


def path_for(env: Mapping[str, str], plan: str, account: str) -> Path:
    name = f"{plan}-{account}" if account else plan
    return state_root(env) / "usage" / f"{name}.json"


def load(env: Mapping[str, str], plan: str, account: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path_for(env, plan, account).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(value, dict):
        return None
    attempted = value.get("attemptedAt")
    if isinstance(attempted, bool) or not isinstance(attempted, (int, float)):
        return None
    row = value.get("row")
    error = value.get("error")
    return {
        "attemptedAt": float(attempted),
        "row": row if isinstance(row, dict) else None,
        "error": error if isinstance(error, str) and error else None,
    }


def due(entry: Mapping[str, Any] | None, plan: str, now_epoch: float) -> bool:
    if entry is None:
        return True
    return now_epoch - float(entry["attemptedAt"]) >= INTERVALS.get(plan, DEFAULT_INTERVAL)


def store(
    env: Mapping[str, str],
    plan: str,
    account: str,
    now_epoch: float,
    *,
    row: Mapping[str, Any] | None,
    error: str | None,
) -> dict[str, Any]:
    entry = {
        "attemptedAt": now_epoch,
        "row": dict(row) if row is not None else None,
        "error": error,
    }
    atomic_write_json(path_for(env, plan, account), entry)
    return entry
```

- [x] **Step 4: Run the test**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_cache 2>&1 | tail -3
```

Expected: `Ran 5 tests` and `OK`.

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/usage/cache.py packages/agent-routing/tests/test_usage_cache.py
git commit -s -m "feat(agent-routing): per-account usage cache with minimum intervals"
```

---

### Task 6: The Claude reader

**Files:**
- Create: `packages/agent-routing/tests/usage_test_support.py`
- Create: `packages/agent-routing/runtime/model_routing/usage/claude.py`
- Test: `packages/agent-routing/tests/test_usage_claude.py`

**Interfaces:**
- Consumes: `rows` (Task 2), `accounts.Account` (Task 4), `endpoint_discovery.ENDPOINT_USER_AGENT`.
- Produces: `model_routing.usage.claude.read`, `read(account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener) -> Reading`; raises `ReadError` on any failure.
- Produces for later tests, in `tests/usage_test_support.py`: `FakeOpener(answers)` with `.calls` and `.urls`; `Response`; `jwt(payload)`; `claude_login(directory, *, expires_at_ms, tier=..., kind=...)`; `codex_login(directory, *, exp, account_id="acct-1", plan="pro")`; `ReaderCase` (sets `self.home`, `self.env`); `NOW`, `EPOCH`.

- [x] **Step 1: Write the test support and the failing test**

Create `packages/agent-routing/tests/usage_test_support.py`:

```python
"""Shared fakes for the usage tests: a recording opener and credential builders."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest
from urllib import error, request

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
EPOCH = NOW.timestamp()


class Response(io.BytesIO):
    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class FakeOpener:
    """Answers by URL substring and records every request it is handed."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = dict(answers or {})
        self.calls: list[request.Request] = []

    def __call__(self, outbound: request.Request, timeout: float = 0.0) -> Response:
        self.calls.append(outbound)
        for fragment, answer in self.answers.items():
            if fragment in outbound.full_url:
                if isinstance(answer, int):
                    raise error.HTTPError(
                        outbound.full_url,
                        answer,
                        "refused",
                        None,
                        io.BytesIO(b"body-that-must-not-leak"),
                    )  # type: ignore[arg-type]
                if isinstance(answer, Exception):
                    raise answer
                if isinstance(answer, bytes):
                    return Response(answer)
                return Response(json.dumps(answer).encode("utf-8"))
        raise error.URLError(f"no fake answer for {outbound.full_url}")

    @property
    def urls(self) -> list[str]:
        return [call.full_url for call in self.calls]


def jwt(payload: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii").rstrip("=")
        )

    return f"{part({'alg': 'none'})}.{part(payload)}.signature"


def claude_login(
    directory: Path,
    *,
    expires_at_ms: float,
    tier: str = "default_claude_max_20x",
    kind: str = "max",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "claude-access",
                    "refreshToken": "claude-refresh",
                    "expiresAt": expires_at_ms,
                    "subscriptionType": kind,
                    "rateLimitTier": tier,
                }
            }
        ),
        encoding="utf-8",
    )
    return directory


def codex_login(
    directory: Path, *, exp: float, account_id: str | None = "acct-1", plan: str = "pro"
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tokens: dict[str, Any] = {
        "access_token": jwt({"exp": exp}),
        "refresh_token": "codex-refresh",
        "id_token": jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct-from-id-token",
                    "chatgpt_plan_type": plan,
                }
            }
        ),
    }
    if account_id is not None:
        tokens["account_id"] = account_id
    (directory / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": tokens}), encoding="utf-8"
    )
    return directory


class ReaderCase(unittest.TestCase):
    """A throwaway home and state directory for one reader test."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {"HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")}

    def tearDown(self) -> None:
        self.tmp.cleanup()
```

Create `packages/agent-routing/tests/test_usage_claude.py`:

```python
"""The Claude reader against recorded response shapes."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "tests"))

from model_routing.usage import claude  # noqa: E402
from model_routing.usage.accounts import Account  # noqa: E402
from model_routing.usage.rows import ReadError, Window  # noqa: E402
from usage_test_support import EPOCH, NOW, FakeOpener, ReaderCase, claude_login  # noqa: E402


class ClaudeReaderTests(ReaderCase):
    def account(self, *, expires_in: float = 3600.0, **kwargs: str) -> Account:
        return Account(
            "claude",
            "",
            (),
            claude_login(
                self.home / ".claude", expires_at_ms=(EPOCH + expires_in) * 1000, **kwargs
            ),
        )

    def test_windows_tier_and_headers(self) -> None:
        opener = FakeOpener(
            {
                "api.anthropic.com/api/oauth/usage": {
                    "five_hour": {"utilization": 41.5, "resets_at": "2026-09-28T14:20:00+00:00"},
                    "seven_day": {"utilization": 63, "resets_at": "2026-10-01T09:00:00Z"},
                    "seven_day_opus": {"utilization": 12.4},
                    "seven_day_sonnet": None,
                }
            }
        )
        reading = claude.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual(
            (Window("5h", 42, "2026-09-28T14:20:00Z"), Window("7d", 63, "2026-10-01T09:00:00Z")),
            reading.windows,
        )
        self.assertEqual("Max 20x", reading.tier)
        self.assertEqual("opus 7d 12%", reading.detail)
        sent = opener.calls[0]
        self.assertEqual("GET", sent.get_method())
        self.assertEqual("Bearer claude-access", sent.get_header("Authorization"))
        self.assertEqual("oauth-2025-04-20", sent.get_header("Anthropic-beta"))

    def test_tiers(self) -> None:
        for tier, kind, expected in (
            ("default_claude_max_5x", "max", "Max 5x"),
            ("", "max", "Max"),
            ("", "pro", "Pro"),
            ("", "team", "team"),
        ):
            with self.subTest(expected=expected):
                opener = FakeOpener({"oauth/usage": {}})
                self.assertEqual(
                    expected,
                    claude.read(
                        self.account(tier=tier, kind=kind), self.env, self.home, NOW, opener
                    ).tier,
                )

    def test_an_expired_login_makes_no_request(self) -> None:
        opener = FakeOpener({"oauth/usage": {}})
        with self.assertRaises(ReadError) as caught:
            claude.read(self.account(expires_in=-1.0), self.env, self.home, NOW, opener)
        self.assertIn("login expired", str(caught.exception))
        self.assertEqual([], opener.calls)

    def test_a_credentials_file_of_the_wrong_shape_is_a_read_error(self) -> None:
        directory = self.home / ".claude"
        directory.mkdir()
        for content in (
            "",
            "not json",
            "[]",
            "null",
            '{"claudeAiOauth": []}',
            '{"claudeAiOauth": {"accessToken": ""}}',
        ):
            with self.subTest(content=content):
                (directory / ".credentials.json").write_text(content, encoding="utf-8")
                with self.assertRaises(ReadError) as caught:
                    claude.read(
                        Account("claude", "", (), directory), self.env, self.home, NOW, FakeOpener()
                    )
                self.assertIn(".credentials.json", str(caught.exception))
                self.assertNotIn("claude-access", str(caught.exception))

    def test_refusals_carry_only_the_status_code(self) -> None:
        with self.assertRaises(ReadError) as caught:
            claude.read(self.account(), self.env, self.home, NOW, FakeOpener({"oauth/usage": 429}))
        self.assertEqual("HTTP 429", str(caught.exception))

    def test_a_response_that_is_not_an_object_is_unexpected(self) -> None:
        with self.assertRaises(ReadError) as caught:
            claude.read(
                self.account(), self.env, self.home, NOW, FakeOpener({"oauth/usage": [1, 2]})
            )
        self.assertEqual("unexpected response", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_claude 2>&1 | tail -3
```

Expected: an error ending `ImportError: cannot import name 'claude' from 'model_routing.usage'`.

- [x] **Step 3: Write the reader**

Create `packages/agent-routing/runtime/model_routing/usage/claude.py`:

```python
"""Claude subscription usage, read with the login the CLI already holds (stdlib only)."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping
from urllib import request

from ..endpoint_discovery import ENDPOINT_USER_AGENT
from .accounts import Account
from .rows import Opener, ReadError, Reading, Window, fetch_json, iso_utc, parse_instant, percent

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA = "oauth-2025-04-20"
CREDENTIALS = ".credentials.json"
EXPIRED = "login expired; send work to this account once to refresh it"


def _tier(oauth: Mapping[str, Any]) -> str:
    rate = str(oauth.get("rateLimitTier") or "")
    kind = str(oauth.get("subscriptionType") or "")
    if "20x" in rate:
        return "Max 20x"
    if "5x" in rate:
        return "Max 5x"
    return {"max": "Max", "pro": "Pro"}.get(kind, kind)


def _window(name: str, raw: Any) -> Window:
    if not isinstance(raw, dict):
        return Window(name, None, None)
    resets = raw.get("resets_at")
    try:
        instant = iso_utc(parse_instant(resets)) if isinstance(resets, str) else None
    except ValueError:
        instant = None
    return Window(name, percent(raw.get("utilization")), instant)


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    assert account.login_dir is not None
    try:
        document = json.loads((account.login_dir / CREDENTIALS).read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise ReadError(f"cannot read {CREDENTIALS}") from None
    oauth = document.get("claudeAiOauth", document) if isinstance(document, dict) else None
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(oauth, dict) or not isinstance(token, str) or not token:
        raise ReadError(f"no access token in {CREDENTIALS}")
    expires = oauth.get("expiresAt")
    if (
        isinstance(expires, (int, float))
        and not isinstance(expires, bool)
        and expires / 1000 <= now.timestamp()
    ):
        raise ReadError(EXPIRED)
    outbound = request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA,
            "Accept": "application/json",
            "User-Agent": ENDPOINT_USER_AGENT,
        },
        method="GET",
    )
    payload = fetch_json(outbound, opener)
    if not isinstance(payload, dict):
        raise ReadError("unexpected response")
    extras = []
    for key, name in (("seven_day_opus", "opus 7d"), ("seven_day_sonnet", "sonnet 7d")):
        raw = payload.get(key)
        value = percent(raw.get("utilization")) if isinstance(raw, dict) else None
        if value is not None:
            extras.append(f"{name} {value}%")
    return Reading(
        tier=_tier(oauth),
        windows=(_window("5h", payload.get("five_hour")), _window("7d", payload.get("seven_day"))),
        detail=", ".join(extras),
    )
```

- [x] **Step 4: Run the test**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -W error::ResourceWarning -m unittest tests.test_usage_claude 2>&1 | tail -3
```

Expected: `Ran 6 tests` and `OK`.

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/usage/claude.py packages/agent-routing/tests/usage_test_support.py packages/agent-routing/tests/test_usage_claude.py
git commit -s -m "feat(agent-routing): read Claude subscription usage"
```

---

### Task 7: The Codex reader

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/usage/codex.py`
- Test: `packages/agent-routing/tests/test_usage_codex.py`

**Interfaces:**
- Consumes: `rows` (Task 2), `accounts.Account` (Task 4), `usage_test_support` (Task 6).
- Produces: `model_routing.usage.codex.read`, `read(account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener) -> Reading`; raises `ReadError` on any failure; `codex.claims(token) -> dict`.

- [x] **Step 1: Write the failing test**

Create `packages/agent-routing/tests/test_usage_codex.py`:

```python
"""The Codex reader against recorded response shapes."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "tests"))

from model_routing.usage import codex  # noqa: E402
from model_routing.usage.accounts import Account  # noqa: E402
from model_routing.usage.rows import ReadError, Window  # noqa: E402
from usage_test_support import EPOCH, NOW, FakeOpener, ReaderCase, codex_login  # noqa: E402


class CodexReaderTests(ReaderCase):
    def account(self, *, exp_in: float = 3600.0, account_id: str | None = "acct-1") -> Account:
        return Account(
            "codex",
            "",
            (),
            codex_login(self.home / ".codex", exp=EPOCH + exp_in, account_id=account_id),
        )

    def payload(
        self,
        primary: dict[str, object] | None,
        secondary: dict[str, object] | None,
        **extra: object,
    ) -> dict[str, object]:
        return {
            "plan_type": "pro",
            "rate_limit": {
                "allowed": True,
                "limit_reached": False,
                "primary_window": primary,
                "secondary_window": secondary,
                **extra,
            },
        }

    def test_windows_are_chosen_by_duration_not_position(self) -> None:
        week = {"used_percent": 40, "limit_window_seconds": 604800, "reset_at": EPOCH + 86400}
        five = {"used_percent": 8.4, "limit_window_seconds": 18000, "reset_at": EPOCH + 3600}
        for primary, secondary in ((five, week), (week, five)):
            with self.subTest(first=primary["limit_window_seconds"]):
                opener = FakeOpener({"wham/usage": self.payload(primary, secondary)})
                reading = codex.read(self.account(), self.env, self.home, NOW, opener)
                self.assertEqual(
                    (
                        Window("5h", 8, "2026-09-28T13:00:00Z"),
                        Window("7d", 40, "2026-09-29T12:00:00Z"),
                    ),
                    reading.windows,
                )
                self.assertEqual("Pro", reading.tier)

    def test_position_is_the_fallback_when_durations_are_missing(self) -> None:
        opener = FakeOpener({"wham/usage": self.payload({"used_percent": 5}, {"used_percent": 50})})
        reading = codex.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual((Window("5h", 5, None), Window("7d", 50, None)), reading.windows)

    def test_one_window_only(self) -> None:
        opener = FakeOpener(
            {"wham/usage": self.payload({"used_percent": 5, "limit_window_seconds": 604800}, None)}
        )
        reading = codex.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual((Window("5h", None, None), Window("7d", 5, None)), reading.windows)

    def test_a_reported_limit_is_carried(self) -> None:
        opener = FakeOpener(
            {
                "wham/usage": self.payload(
                    {"used_percent": 99, "limit_window_seconds": 18000}, None, limit_reached=True
                )
            }
        )
        self.assertTrue(codex.read(self.account(), self.env, self.home, NOW, opener).limit_reached)

    def test_the_account_id_header(self) -> None:
        opener = FakeOpener({"wham/usage": self.payload(None, None)})
        codex.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual("acct-1", opener.calls[0].get_header("Chatgpt-account-id"))
        opener = FakeOpener({"wham/usage": self.payload(None, None)})
        codex.read(self.account(account_id=None), self.env, self.home, NOW, opener)
        self.assertEqual("acct-from-id-token", opener.calls[0].get_header("Chatgpt-account-id"))

    def test_an_expired_or_unreadable_token_makes_no_request(self) -> None:
        opener = FakeOpener({"wham/usage": self.payload(None, None)})
        with self.assertRaises(ReadError) as caught:
            codex.read(self.account(exp_in=60.0), self.env, self.home, NOW, opener)
        self.assertIn("login expired", str(caught.exception))
        (self.home / ".codex" / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "not-a-jwt"}}), encoding="utf-8"
        )
        with self.assertRaises(ReadError):
            codex.read(
                Account("codex", "", (), self.home / ".codex"), self.env, self.home, NOW, opener
            )
        self.assertEqual([], opener.calls)

    def test_a_response_without_rate_limits_is_unexpected(self) -> None:
        with self.assertRaises(ReadError) as caught:
            codex.read(
                self.account(),
                self.env,
                self.home,
                NOW,
                FakeOpener({"wham/usage": {"plan_type": "pro"}}),
            )
        self.assertEqual("unexpected response", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_codex 2>&1 | tail -3
```

Expected: an error ending `ImportError: cannot import name 'codex' from 'model_routing.usage'`.

- [x] **Step 3: Write the reader**

Create `packages/agent-routing/runtime/model_routing/usage/codex.py`:

```python
"""Codex (ChatGPT plan) usage, read with the login the CLI already holds (stdlib only)."""

from __future__ import annotations

import base64
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping
from urllib import request

from ..endpoint_discovery import ENDPOINT_USER_AGENT
from .accounts import Account
from .rows import Opener, ReadError, Reading, Window, fetch_json, from_epoch, percent

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
AUTH_FILE = "auth.json"
AUTH_CLAIM = "https://api.openai.com/auth"
EXPIRY_SKEW = 120
FIVE_HOURS_MAX = 10 * 3600
WEEK_MIN = 7 * 24 * 3600
EXPIRED = "login expired; send work to this account once to refresh it"


def claims(token: Any) -> dict[str, Any]:
    """The payload of a JWT, or an empty object. The signature is not checked; nothing is trusted from it."""
    if not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        value = json.loads(base64.urlsafe_b64decode(segment.encode("ascii")))
    except ValueError, UnicodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _seconds(window: Mapping[str, Any]) -> float | None:
    value = window.get("limit_window_seconds")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _window(name: str, raw: Mapping[str, Any] | None) -> Window:
    if raw is None:
        return Window(name, None, None)
    reset = raw.get("reset_at")
    instant = (
        from_epoch(reset)
        if isinstance(reset, (int, float)) and not isinstance(reset, bool)
        else None
    )
    return Window(name, percent(raw.get("used_percent")), instant)


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    assert account.login_dir is not None
    try:
        document = json.loads((account.login_dir / AUTH_FILE).read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise ReadError(f"cannot read {AUTH_FILE}") from None
    tokens = document.get("tokens") if isinstance(document, dict) else None
    access = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(tokens, dict) or not isinstance(access, str) or not access:
        raise ReadError(f"no access token in {AUTH_FILE}")
    expires = claims(access).get("exp")
    if (
        isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or now.timestamp() >= expires - EXPIRY_SKEW
    ):
        raise ReadError(EXPIRED)
    identity = claims(tokens.get("id_token")).get(AUTH_CLAIM)
    identity = identity if isinstance(identity, dict) else {}
    account_id = tokens.get("account_id") or identity.get("chatgpt_account_id")
    headers = {
        "Authorization": f"Bearer {access}",
        "Accept": "application/json",
        "User-Agent": ENDPOINT_USER_AGENT,
    }
    if isinstance(account_id, str) and account_id:
        headers["ChatGPT-Account-Id"] = account_id
    payload = fetch_json(request.Request(USAGE_URL, headers=headers, method="GET"), opener)
    limits = payload.get("rate_limit") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        raise ReadError("unexpected response")
    primary = (
        limits.get("primary_window") if isinstance(limits.get("primary_window"), dict) else None
    )
    secondary = (
        limits.get("secondary_window") if isinstance(limits.get("secondary_window"), dict) else None
    )
    candidates = [window for window in (primary, secondary) if window is not None]
    five = next((w for w in candidates if (_seconds(w) or float("inf")) <= FIVE_HOURS_MAX), None)
    week = next((w for w in candidates if (_seconds(w) or 0.0) >= WEEK_MIN), None)
    if five is None and primary is not None and primary is not week:
        five = primary
    if week is None and secondary is not None and secondary is not five:
        week = secondary
    plan = payload.get("plan_type") or identity.get("chatgpt_plan_type") or ""
    return Reading(
        tier=str(plan).capitalize(),
        windows=(_window("5h", five), _window("7d", week)),
        limit_reached=limits.get("limit_reached") is True,
    )
```

- [x] **Step 4: Run the test**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -W error::ResourceWarning -m unittest tests.test_usage_codex 2>&1 | tail -3
```

Expected: `Ran 7 tests` and `OK`.

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/usage/codex.py packages/agent-routing/tests/test_usage_codex.py
git commit -s -m "feat(agent-routing): read Codex subscription usage"
```

---

### Task 8: The GLM and MiniMax readers

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/usage/glm.py`, `packages/agent-routing/runtime/model_routing/usage/minimax.py`
- Test: `packages/agent-routing/tests/test_usage_keyed.py`

**Interfaces:**
- Consumes: `rows` (Task 2), `accounts.Account` and `accounts.api_key` (Task 4), `usage_test_support` (Task 6).
- Produces: `model_routing.usage.glm.read` and `model_routing.usage.minimax.read`, each `read(account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener) -> Reading`; raises `ReadError` on any failure.

- [x] **Step 1: Write the failing test**

Create `packages/agent-routing/tests/test_usage_keyed.py`:

```python
"""The GLM and MiniMax readers against recorded response shapes."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "tests"))

from model_routing.usage import glm, minimax  # noqa: E402
from model_routing.usage.accounts import Account  # noqa: E402
from model_routing.usage.rows import ReadError, Window  # noqa: E402
from usage_test_support import EPOCH, NOW, FakeOpener, ReaderCase  # noqa: E402


class GlmReaderTests(ReaderCase):
    def setUp(self) -> None:
        super().setUp()
        self.env["GLM_API_KEY"] = "glm-key"  # pragma: allowlist secret
        self.account = Account("glm", "")

    def limits(self, *entries: dict[str, object]) -> dict[str, object]:
        return {"code": 200, "data": {"level": "pro", "limits": list(entries)}}

    def test_codes_identify_the_windows_even_when_reset_order_disagrees(self) -> None:
        five = {
            "type": "TOKENS_LIMIT",
            "unit": 3,
            "number": 5,
            "percentage": 7,
            "nextResetTime": (EPOCH + 4 * 3600) * 1000,
        }
        week = {
            "type": "TOKENS_LIMIT",
            "unit": 6,
            "number": 1,
            "percentage": 31,
            "nextResetTime": (EPOCH + 3600) * 1000,
        }
        tools = {"type": "TIME_LIMIT", "percentage": 16}
        reading = glm.read(
            self.account,
            self.env,
            self.home,
            NOW,
            FakeOpener({"quota/limit": self.limits(week, tools, five)}),
        )
        self.assertEqual(
            (Window("5h", 7, "2026-09-28T16:00:00Z"), Window("7d", 31, "2026-09-28T13:00:00Z")),
            reading.windows,
        )
        self.assertEqual(("Pro", "tools 16%"), (reading.tier, reading.detail))

    def test_reset_order_is_the_fallback_without_codes(self) -> None:
        later = {"type": "TOKENS_LIMIT", "percentage": 31, "nextResetTime": (EPOCH + 86400) * 1000}
        sooner = {"type": "TOKENS_LIMIT", "percentage": 7, "nextResetTime": (EPOCH + 3600) * 1000}
        reading = glm.read(
            self.account,
            self.env,
            self.home,
            NOW,
            FakeOpener({"quota/limit": self.limits(later, sooner)}),
        )
        self.assertEqual([7, 31], [window.used_pct for window in reading.windows])

    def test_the_key_is_sent_and_never_reported(self) -> None:
        opener = FakeOpener({"quota/limit": 401})
        with self.assertRaises(ReadError) as caught:
            glm.read(self.account, self.env, self.home, NOW, opener)
        self.assertEqual("HTTP 401", str(caught.exception))
        self.assertEqual("Bearer glm-key", opener.calls[0].get_header("Authorization"))

    def test_an_unexpected_shape(self) -> None:
        for payload in ({"data": {"limits": "none"}}, {"data": None}, []):
            with self.subTest(payload=payload):
                with self.assertRaises(ReadError) as caught:
                    glm.read(
                        self.account, self.env, self.home, NOW, FakeOpener({"quota/limit": payload})
                    )
                self.assertEqual("unexpected response", str(caught.exception))


class MiniMaxReaderTests(ReaderCase):
    def setUp(self) -> None:
        super().setUp()
        self.env["MINIMAX_API_KEY"] = "mm-key"  # pragma: allowlist secret
        self.account = Account("minimax", "")

    def test_counts_become_percentages_for_the_coding_model(self) -> None:
        payload = {
            "model_remains": [
                {
                    "model_name": "Hailuo-02",
                    "current_interval_total_count": 10,
                    "current_interval_usage_count": 9,
                },
                {
                    "model_name": "MiniMax-M3",
                    "remains_time": 3600_000,
                    "current_interval_total_count": 1200,
                    "current_interval_usage_count": 300,
                    "current_weekly_total_count": 8000,
                    "current_weekly_usage_count": 4000,
                    "weekly_remains_time": 86_400_000,
                },
            ]
        }
        opener = FakeOpener({"api.minimax.io/v1/token_plan/remains": payload})
        reading = minimax.read(self.account, self.env, self.home, NOW, opener)
        self.assertEqual(
            (Window("5h", 25, "2026-09-28T13:00:00Z"), Window("7d", 50, "2026-09-29T12:00:00Z")),
            reading.windows,
        )
        self.assertEqual("5h requests 300/1200, 7d requests 4000/8000", reading.detail)

    def test_a_total_of_zero_gives_no_percentage(self) -> None:
        payload = {
            "model_remains": [
                {
                    "model_name": "MiniMax-M3",
                    "current_interval_total_count": 0,
                    "current_interval_usage_count": 0,
                }
            ]
        }
        reading = minimax.read(
            self.account, self.env, self.home, NOW, FakeOpener({"token_plan/remains": payload})
        )
        self.assertEqual([None, None], [window.used_pct for window in reading.windows])

    def test_a_vendor_status_is_reported_by_code_only(self) -> None:
        payload = {"base_resp": {"status_code": 2049, "status_msg": "invalid api key mm-key"}}
        with self.assertRaises(ReadError) as caught:
            minimax.read(
                self.account, self.env, self.home, NOW, FakeOpener({"token_plan/remains": payload})
            )
        self.assertEqual("vendor status 2049", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_keyed 2>&1 | tail -3
```

Expected: an error ending `ImportError: cannot import name 'glm' from 'model_routing.usage'`.

- [x] **Step 3: Write the readers**

Create `packages/agent-routing/runtime/model_routing/usage/glm.py`:

```python
"""GLM Coding Plan usage from the Z.ai quota endpoint (stdlib only)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib import request

from ..endpoint_discovery import ENDPOINT_USER_AGENT
from .accounts import Account, api_key
from .rows import Opener, ReadError, Reading, Window, fetch_json, from_epoch, percent

QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
QUOTA_URL_ENV = "GLM_QUOTA_URL"
FIVE_HOUR_CODES = (3, 5)
WEEKLY_CODES = (6, 1)


def _window(name: str, raw: Mapping[str, Any] | None) -> Window:
    if raw is None:
        return Window(name, None, None)
    reset = raw.get("nextResetTime")
    instant = (
        from_epoch(reset / 1000)
        if isinstance(reset, (int, float)) and not isinstance(reset, bool)
        else None
    )
    return Window(name, percent(raw.get("percentage")), instant)


def _coded(
    windows: Sequence[Mapping[str, Any]], codes: tuple[int, int]
) -> Mapping[str, Any] | None:
    return next((w for w in windows if (w.get("unit"), w.get("number")) == codes), None)


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    key = api_key("glm", env, home)
    if not key:
        raise ReadError("no GLM key available")
    outbound = request.Request(
        env.get(QUOTA_URL_ENV) or QUOTA_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": ENDPOINT_USER_AGENT,
        },
        method="GET",
    )
    payload = fetch_json(outbound, opener)
    data = payload.get("data") if isinstance(payload, dict) else None
    limits = data.get("limits") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(limits, list):
        raise ReadError("unexpected response")
    entries = [item for item in limits if isinstance(item, dict)]
    tokens = [item for item in entries if item.get("type") == "TOKENS_LIMIT"]
    # The unit and number codes identify the windows. Reset order is only a fallback: shortly before
    # the weekly boundary the weekly window resets first, and reset order would swap the two.
    by_reset = sorted(tokens, key=lambda item: item.get("nextResetTime") or 0)
    five = _coded(tokens, FIVE_HOUR_CODES) or (by_reset[0] if by_reset else None)
    week = _coded(tokens, WEEKLY_CODES) or next(
        (item for item in by_reset if item is not five), None
    )
    tools = next((item for item in entries if item.get("type") == "TIME_LIMIT"), None)
    tools_pct = percent(tools.get("percentage")) if tools is not None else None
    level = data.get("level")
    return Reading(
        tier=str(level).capitalize() if isinstance(level, str) and level else "Coding Plan",
        windows=(_window("5h", five), _window("7d", week)),
        detail=f"tools {tools_pct}%" if tools_pct is not None else "",
    )
```

Create `packages/agent-routing/runtime/model_routing/usage/minimax.py`:

```python
"""MiniMax Coding Plan usage; the quota is a request count (stdlib only)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Any, Mapping
from urllib import request

from ..endpoint_discovery import ENDPOINT_USER_AGENT
from .accounts import Account, api_key
from .rows import Opener, ReadError, Reading, Window, fetch_json, iso_utc, percent

# The coding-plan key is valid on the .io host only.
BASE_URL = "https://api.minimax.io"
BASE_URL_ENV = "MINIMAX_BASE"
PATH = "/v1/token_plan/remains"
_CODING_MODEL = re.compile(r"^MiniMax-M", re.IGNORECASE)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _window(name: str, used: Any, total: Any, remaining_ms: Any, now: datetime) -> Window:
    used_n, total_n, remaining = _number(used), _number(total), _number(remaining_ms)
    pct = (
        percent(used_n / total_n * 100)
        if used_n is not None and total_n is not None and total_n > 0
        else None
    )
    instant = (
        iso_utc(now + timedelta(milliseconds=remaining))
        if remaining is not None and remaining >= 0
        else None
    )
    return Window(name, pct, instant)


def _count(used: Any, total: Any) -> str:
    used_n, total_n = _number(used), _number(total)
    return f"{int(used_n)}/{int(total_n)}" if used_n is not None and total_n is not None else ""


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    key = api_key("minimax", env, home)
    if not key:
        raise ReadError("no MiniMax key available")
    outbound = request.Request(
        (env.get(BASE_URL_ENV) or BASE_URL).rstrip("/") + PATH,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": ENDPOINT_USER_AGENT,
        },
        method="GET",
    )
    payload = fetch_json(outbound, opener)
    if not isinstance(payload, dict):
        raise ReadError("unexpected response")
    models = payload.get("model_remains")
    if not isinstance(models, list) or not models:
        base = payload.get("base_resp")
        code = base.get("status_code") if isinstance(base, dict) else None
        raise ReadError(
            f"vendor status {code}" if isinstance(code, int) and code else "unexpected response"
        )
    entries = [item for item in models if isinstance(item, dict)]
    if not entries:
        raise ReadError("unexpected response")
    chosen = next(
        (item for item in entries if _CODING_MODEL.match(str(item.get("model_name") or ""))),
        entries[0],
    )
    five = _count(
        chosen.get("current_interval_usage_count"), chosen.get("current_interval_total_count")
    )
    week = _count(
        chosen.get("current_weekly_usage_count"), chosen.get("current_weekly_total_count")
    )
    detail = ", ".join(
        text
        for text in (f"5h requests {five}" if five else "", f"7d requests {week}" if week else "")
        if text
    )
    return Reading(
        tier="Coding Plan",
        windows=(
            _window(
                "5h",
                chosen.get("current_interval_usage_count"),
                chosen.get("current_interval_total_count"),
                chosen.get("remains_time"),
                now,
            ),
            _window(
                "7d",
                chosen.get("current_weekly_usage_count"),
                chosen.get("current_weekly_total_count"),
                chosen.get("weekly_remains_time"),
                now,
            ),
        ),
        detail=detail,
    )
```

- [x] **Step 4: Run the test**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -W error::ResourceWarning -m unittest tests.test_usage_keyed 2>&1 | tail -3
```

Expected: `Ran 7 tests` and `OK`.

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/usage/glm.py packages/agent-routing/runtime/model_routing/usage/minimax.py packages/agent-routing/tests/test_usage_keyed.py
git commit -s -m "feat(agent-routing): read GLM and MiniMax plan usage"
```

---

### Task 9: The Model Studio reader

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/usage/model_studio.py`
- Test: `packages/agent-routing/tests/test_usage_model_studio.py`

**Interfaces:**
- Consumes: `model_studio_openapi.get_subscription_stats(env, *, now, opener)`, `endpoint_slots.read_lockout(env, endpoint, *, now)`, `model_studio.credits_window(renews_on, now)`; `rows` (Task 2); `accounts.Account.endpoints` (Task 4).
- Produces: `model_routing.usage.model_studio.read`, `read(account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener) -> Reading`; raises `ReadError` on any failure; raises `Unmeasured` when no AccessKey pair is configured.

- [x] **Step 1: Write the failing test**

Create `packages/agent-routing/tests/test_usage_model_studio.py`:

```python
"""The Model Studio reader: statistics, the lockout, and the unmeasured case."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "tests"))

from model_routing import endpoint_slots  # noqa: E402
from model_routing.usage import model_studio  # noqa: E402
from model_routing.usage.accounts import Account  # noqa: E402
from model_routing.usage.rows import ReadError, Unmeasured, Window  # noqa: E402
from usage_test_support import EPOCH, NOW, FakeOpener, ReaderCase  # noqa: E402


class ModelStudioReaderTests(ReaderCase):
    ENDPOINT = {
        "kind": "model-studio",
        "plan": "token-plan-personal",
        "tier": "pro",
        "renewsOn": "2026-09-21",
    }

    def account(self, endpoint: dict[str, object] | None = None) -> Account:
        return Account(
            "model-studio", "", ("flash",), endpoints=(("ms", endpoint or self.ENDPOINT),)
        )

    def test_without_an_access_key_usage_is_unmeasured_and_names_the_renewal(self) -> None:
        opener = FakeOpener()
        with self.assertRaises(Unmeasured) as caught:
            model_studio.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual("usage needs an AccessKey pair; renews 2026-10-21", str(caught.exception))
        self.assertEqual([], opener.calls)

    def test_without_a_renewal_date(self) -> None:
        endpoint = {"kind": "model-studio", "plan": "token-plan-personal", "tier": "pro"}
        with self.assertRaises(Unmeasured) as caught:
            model_studio.read(self.account(endpoint), self.env, self.home, NOW, FakeOpener())
        self.assertEqual("usage needs an AccessKey pair", str(caught.exception))

    def test_stats_become_a_thirty_day_window(self) -> None:
        self.env.update(
            {
                "ALIBABA_CLOUD_ACCESS_KEY_ID": "TESTAKID",
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "test-value",  # pragma: allowlist secret
            }
        )
        reset_ms = int((EPOCH + 10 * 86400) * 1000)
        payload = {
            "Data": {
                "Items": [
                    {
                        "SeatCredits": 180000,
                        "SeatRemainingCredits": 45000,
                        "SeatRefreshTime": reset_ms,
                    }
                ]
            }
        }
        reading = model_studio.read(
            self.account(),
            self.env,
            self.home,
            NOW,
            FakeOpener({"tokenplan/subscription/stats": payload}),
        )
        self.assertEqual((Window("30d", 75, "2026-10-08T12:00:00Z"),), reading.windows)
        self.assertEqual(
            ("Pro", "45000 of 180000 Credits remaining"), (reading.tier, reading.detail)
        )

    def test_a_local_lockout_is_a_limit_without_a_request(self) -> None:
        endpoint_slots.write_lockout(self.env, "ms", datetime(2026, 10, 21, tzinfo=timezone.utc))
        opener = FakeOpener()
        reading = model_studio.read(self.account(), self.env, self.home, NOW, opener)
        self.assertEqual((Window("30d", 100, "2026-10-21T00:00:00Z"),), reading.windows)
        self.assertTrue(reading.limit_reached)
        self.assertEqual([], opener.calls)

    def test_a_refused_stats_read(self) -> None:
        self.env.update(
            {
                "ALIBABA_CLOUD_ACCESS_KEY_ID": "TESTAKID",
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "test-value",  # pragma: allowlist secret
            }
        )
        with self.assertRaises(ReadError) as caught:
            model_studio.read(
                self.account(), self.env, self.home, NOW, FakeOpener({"subscription/stats": 403})
            )
        self.assertEqual("HTTP 403", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_model_studio 2>&1 | tail -3
```

Expected: an error ending `ImportError: cannot import name 'model_studio' from 'model_routing.usage'`.

- [x] **Step 3: Write the reader**

Create `packages/agent-routing/runtime/model_routing/usage/model_studio.py`:

```python
"""Model Studio Token Plan usage: the existing stats read and the local lockout (stdlib only)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping
from urllib import error

from .. import endpoint_slots, model_studio as catalog, model_studio_openapi
from .accounts import Account
from .rows import Opener, ReadError, Reading, Unmeasured, Window, iso_utc, parse_instant, percent


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    tier = next(
        (
            str(endpoint["tier"]).capitalize()
            for _name, endpoint in account.endpoints
            if endpoint.get("tier")
        ),
        "",
    )
    for name, _endpoint in account.endpoints:
        until = endpoint_slots.read_lockout(env, name, now=now)
        if until is not None:
            return Reading(
                tier=tier,
                windows=(Window("30d", 100, iso_utc(parse_instant(until))),),
                detail="Credits exhausted",
                limit_reached=True,
            )
    try:
        stats = model_studio_openapi.get_subscription_stats(env, now=now, opener=opener)
    except error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ReadError(f"HTTP {code}") from None
    except error.URLError, OSError:
        raise ReadError("network failure") from None
    except ValueError, KeyError, TypeError:
        raise ReadError("unexpected response") from None
    if stats is None:
        renews = next(
            (
                str(endpoint["renewsOn"])
                for _name, endpoint in account.endpoints
                if endpoint.get("renewsOn")
            ),
            None,
        )
        if renews is None:
            raise Unmeasured("usage needs an AccessKey pair")
        renewal = catalog.credits_window(renews, now)[1].date().isoformat()
        raise Unmeasured(f"usage needs an AccessKey pair; renews {renewal}")
    used = (
        percent((stats.total_credits - stats.remaining_credits) / stats.total_credits * 100)
        if stats.total_credits > 0
        else None
    )
    return Reading(
        tier=tier,
        windows=(Window("30d", used, iso_utc(stats.reset_at)),),
        detail=f"{stats.remaining_credits:.0f} of {stats.total_credits:.0f} Credits remaining",
    )
```

- [x] **Step 4: Run the test**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -W error::ResourceWarning -m unittest tests.test_usage_model_studio 2>&1 | tail -3
```

Expected: `Ran 5 tests` and `OK`.

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/usage/model_studio.py packages/agent-routing/tests/test_usage_model_studio.py
git commit -s -m "feat(agent-routing): read Model Studio Token Plan usage"
```

---

### Task 10: `collect()` and the `usage` command

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/usage/__init__.py` (replace the one-line file)
- Modify: `packages/agent-routing/runtime/model_routing/cli.py` (imports, `build_parser`, `main`, new `_usage`)
- Test: `packages/agent-routing/tests/test_usage_collect.py`, `packages/agent-routing/tests/test_usage_cli.py`, `packages/agent-routing/tests/test_usage_live.py`

**Interfaces:**
- Consumes: everything from Tasks 2 and 4 to 9; `cli._routes_context() -> (registry, config, home)`; `provider_setup.resolve_provider_binary`, `provider_setup.environment_with_user_bins`.
- Produces:
  - `usage.collect(env, *, registry, routes_config, home, now=None, opener=urlopen, installed=None, readers=None) -> list[Row]`
  - `usage.READERS`, and re-exports `usage.Row`, `usage.Account`, `usage.iso_utc`, `usage.render_table`, `usage.rows`
  - The command `pitwall-agent-routing usage [--json]`, exit 0 when it ran and 2 on a usage error or unreadable routes file

- [x] **Step 1: Write the failing tests**

Create `packages/agent-routing/tests/test_usage_collect.py`:

```python
"""collect(): rows from accounts, the cache between reads, and failures that stay informative."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "tests"))

from model_routing import routes, usage  # noqa: E402
from model_routing.registry import load_registry  # noqa: E402
from model_routing.usage import cache  # noqa: E402
from usage_test_support import FakeOpener, claude_login, codex_login  # noqa: E402

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
CLAUDE_OK = {
    "five_hour": {"utilization": 92, "resets_at": "2026-09-28T14:20:00Z"},
    "seven_day": {"utilization": 63, "resets_at": "2026-10-01T09:00:00Z"},
}
REFRESH_ADDRESSES = (
    "oauth/token",
    "auth.openai.com",
    "platform.claude.com",
    "console.anthropic.com",
)


class CollectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {"HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")}
        self.config = routes.validate_routes(routes.empty_routes(), registry=self.registry)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def collect(
        self,
        opener: FakeOpener,
        *,
        now: datetime = NOW,
        installed: tuple[str, ...] = (),
        config: dict[str, object] | None = None,
    ) -> list[usage.Row]:
        return usage.collect(
            self.env,
            registry=self.registry,
            routes_config=self.config if config is None else config,
            home=self.home,
            now=now,
            opener=opener,
            installed=lambda harness: harness in installed,
        )

    def test_nothing_configured(self) -> None:
        self.assertEqual([], self.collect(FakeOpener()))

    def test_a_reading_becomes_a_row_with_a_status(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 3600) * 1000)
        (row,) = self.collect(FakeOpener({"oauth/usage": CLAUDE_OK}))
        self.assertEqual(
            ("claude", "", (), "Claude", "Max 20x", "warn", "2026-09-28T12:00:00Z"),
            (row.plan, row.account, row.routes, row.label, row.tier, row.status, row.observed_at),
        )
        self.assertEqual([92, 63], [window.used_pct for window in row.windows])

    def test_plans_without_a_reader_are_unknown(self) -> None:
        rows = self.collect(FakeOpener(), installed=("kimi", "grok"))
        self.assertEqual(
            [("kimi", "unknown", "no usage source"), ("grok", "unknown", "no usage source")],
            [(row.plan, row.status, row.detail) for row in rows],
        )

    def test_inside_the_interval_the_cached_row_is_returned_without_a_request(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        first = self.collect(FakeOpener({"oauth/usage": CLAUDE_OK}))
        opener = FakeOpener({"oauth/usage": 500})
        again = self.collect(opener, now=NOW + timedelta(seconds=299))
        self.assertEqual(first, again)
        self.assertEqual([], opener.calls)
        self.collect(opener, now=NOW + timedelta(seconds=300))
        self.assertEqual(1, len(opener.calls))

    def test_a_failure_after_a_good_read_is_stale_with_the_old_numbers(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        self.collect(FakeOpener({"oauth/usage": CLAUDE_OK}))
        later = NOW + timedelta(seconds=300)
        (row,) = self.collect(FakeOpener({"oauth/usage": 429}), now=later)
        self.assertEqual(("stale", "HTTP 429"), (row.status, row.detail))
        self.assertEqual([92, 63], [window.used_pct for window in row.windows])
        self.assertEqual("2026-09-28T12:00:00Z", row.observed_at)
        # still stale, and still the old numbers, when served from the cache
        (cached,) = self.collect(FakeOpener(), now=later + timedelta(seconds=10))
        self.assertEqual(row, cached)

    def test_a_failure_with_no_earlier_row_is_an_error(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        (row,) = self.collect(FakeOpener({"oauth/usage": 401}))
        self.assertEqual(("error", "HTTP 401", ()), (row.status, row.detail, row.windows))

    def test_a_failing_source_is_throttled_too(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        self.collect(FakeOpener({"oauth/usage": 500}))
        opener = FakeOpener({"oauth/usage": CLAUDE_OK})
        (row,) = self.collect(opener, now=NOW + timedelta(seconds=30))
        self.assertEqual("error", row.status)
        self.assertEqual([], opener.calls)

    def test_the_attempt_is_recorded_before_the_request(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        seen: list[object] = []

        class Watching(FakeOpener):
            def __call__(inner, outbound, timeout=0.0):  # type: ignore[no-untyped-def,override]
                seen.append(cache.load(self.env, "claude", ""))
                return super().__call__(outbound, timeout)

        self.collect(Watching({"oauth/usage": CLAUDE_OK}))
        self.assertEqual(
            [{"attemptedAt": NOW.timestamp(), "row": None, "error": "read did not finish"}], seen
        )

    def test_an_expired_idle_account_is_stale_and_says_so(self) -> None:
        second = claude_login(
            self.home / "logins" / ".claude-home", expires_at_ms=(NOW.timestamp() + 3600) * 1000
        )
        document = routes.empty_routes()
        document["models"] = {
            "claude-home": {
                "model": "sonnet",
                "harness": "claude",
                "env": {"CLAUDE_CONFIG_DIR": str(second)},
            }
        }
        config = routes.validate_routes(document, registry=self.registry)
        self.collect(FakeOpener({"oauth/usage": CLAUDE_OK}), config=config)
        (row,) = self.collect(FakeOpener(), now=NOW + timedelta(hours=2), config=config)
        self.assertEqual(("home", ("claude-home",), "stale"), (row.account, row.routes, row.status))
        self.assertIn("login expired", row.detail)

    def test_a_route_with_a_missing_login_directory_is_an_error_that_names_it(self) -> None:
        document = routes.empty_routes()
        document["models"] = {
            "gone": {
                "model": "sonnet",
                "harness": "claude",
                "env": {"CLAUDE_CONFIG_DIR": str(self.home / "nowhere")},
            }
        }
        opener = FakeOpener()
        (row,) = self.collect(
            opener, config=routes.validate_routes(document, registry=self.registry)
        )
        self.assertEqual(
            ("error", "route gone names a login directory with no login in it"),
            (row.status, row.detail),
        )
        self.assertEqual([], opener.calls)

    def test_a_reader_that_trips_on_an_odd_shape_is_an_unexpected_response(self) -> None:
        self.env["GLM_API_KEY"] = "glm-key"  # pragma: allowlist secret

        def trips(*_args: object) -> usage.rows.Reading:
            raise KeyError("limits")

        rows = usage.collect(
            self.env,
            registry=self.registry,
            routes_config=self.config,
            home=self.home,
            now=NOW,
            opener=FakeOpener(),
            installed=lambda _harness: False,
            readers={"glm": trips},
        )
        self.assertEqual(
            [("glm", "error", "unexpected response")],
            [(row.plan, row.status, row.detail) for row in rows],
        )

    def test_a_damaged_cache_file_is_read_as_no_cache(self) -> None:
        self.env["GLM_API_KEY"] = "glm-key"  # pragma: allowlist secret
        path = cache.path_for(self.env, "glm", "")
        path.parent.mkdir(parents=True)
        path.write_text('{"attemptedAt": 17', encoding="utf-8")
        opener = FakeOpener({"quota/limit": {"data": {"level": "pro", "limits": []}}})
        (row,) = self.collect(opener)
        self.assertEqual("ok", row.status)
        self.assertEqual(1, len(opener.calls))

    def test_no_reader_asks_for_a_token_refresh_or_writes_a_credential_file(self) -> None:
        claude_dir = claude_login(
            self.home / ".claude", expires_at_ms=(NOW.timestamp() + 86400) * 1000
        )
        codex_dir = codex_login(self.home / ".codex", exp=NOW.timestamp() + 86400)
        self.env.update(
            {"GLM_API_KEY": "glm-key", "MINIMAX_API_KEY": "mm-key"}  # pragma: allowlist secret
        )
        before = {
            path: path.read_bytes()
            for path in (claude_dir / ".credentials.json", codex_dir / "auth.json")
        }
        for answers in (
            {},
            {"oauth/usage": 401, "wham/usage": 401, "quota/limit": 401, "token_plan/remains": 401},
        ):
            opener = FakeOpener(answers)
            self.collect(opener, now=NOW + timedelta(hours=len(answers)))
            self.assertEqual(4, len(opener.calls))
            for call in opener.calls:
                self.assertEqual("GET", call.get_method())
                self.assertIsNone(call.data)
                for address in REFRESH_ADDRESSES:
                    self.assertNotIn(address, call.full_url)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_cached_files_hold_no_credential(self) -> None:
        claude_login(self.home / ".claude", expires_at_ms=(NOW.timestamp() + 7200) * 1000)
        self.env["GLM_API_KEY"] = "glm-key"  # pragma: allowlist secret
        self.collect(FakeOpener({"oauth/usage": CLAUDE_OK, "quota/limit": 403}))
        stored = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.home / "state" / "subagent-model-routing" / "usage").iterdir()
        )
        for value in ("claude-access", "claude-refresh", "glm-key", "body-that-must-not-leak"):
            self.assertNotIn(value, stored)
        self.assertEqual(
            {"claude.json", "glm.json"},
            {
                path.name
                for path in (self.home / "state" / "subagent-model-routing" / "usage").iterdir()
            },
        )
        json.loads(
            (self.home / "state" / "subagent-model-routing" / "usage" / "glm.json").read_text(
                encoding="utf-8"
            )
        )


if __name__ == "__main__":
    unittest.main()
```

Create `packages/agent-routing/tests/test_usage_cli.py`:

```python
"""The ``usage`` command: both output forms and the exit codes."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import cli, usage  # noqa: E402
from model_routing.usage.rows import Row, Window  # noqa: E402

ROWS = [
    Row(
        "claude",
        "",
        (),
        "Claude",
        "Max 20x",
        (Window("5h", 41, None), Window("7d", 63, None)),
        "ok",
        "",
        "2026-09-28T12:00:00Z",
    ),
    Row("kimi", "", (), "Kimi", "", (), "unknown", "no usage source", "2026-09-28T12:00:00Z"),
]


class UsageCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            "HOME": self.tmp.name,
            "XDG_STATE_HOME": str(Path(self.tmp.name) / "state"),
            "XDG_CONFIG_HOME": str(Path(self.tmp.name) / "config"),
            "PATH": "/usr/bin:/bin",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *argv: str, rows: list[Row] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, self.env, clear=True))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            if rows is not None:
                stack.enter_context(mock.patch.object(usage, "collect", return_value=rows))
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_json(self) -> None:
        code, out, err = self.run_cli("usage", "--json", rows=ROWS)
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual(["observed_at", "plans"], sorted(payload))
        self.assertEqual([row.to_dict() for row in ROWS], payload["plans"])

    def test_table(self) -> None:
        code, out, _err = self.run_cli("usage", rows=ROWS)
        self.assertEqual(0, code)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("plan"))
        self.assertIn("5h 41%", lines[1])
        self.assertIn("unknown", lines[2])

    def test_rows_that_failed_still_exit_zero(self) -> None:
        failed = [Row("glm", "", (), "GLM", "", (), "error", "HTTP 401", "2026-09-28T12:00:00Z")]
        self.assertEqual(0, self.run_cli("usage", "--json", rows=failed)[0])

    def test_an_empty_machine_really_runs_and_prints_no_plans(self) -> None:
        code, out, err = self.run_cli("usage", "--json")
        self.assertEqual(0, code, err)
        self.assertEqual([], json.loads(out)["plans"])

    def test_an_unknown_flag_is_a_usage_error(self) -> None:
        code, _out, err = self.run_cli("usage", "--nope")
        self.assertEqual(2, code)
        self.assertIn("usage", err.lower())

    def test_an_unreadable_routes_file_exits_two(self) -> None:
        target = Path(self.tmp.name) / "config" / "subagent-model-routing" / "routes.json"
        target.parent.mkdir(parents=True)
        target.write_text("{not json", encoding="utf-8")
        code, _out, err = self.run_cli("usage")
        self.assertEqual(2, code)
        self.assertIn("routes.json", err)


if __name__ == "__main__":
    unittest.main()
```

Create `packages/agent-routing/tests/test_usage_live.py`:

```python
"""Opt-in live reads, one per reader. Skipped unless PITWALL_AGENT_ROUTING_USAGE_LIVE=1.

Each check makes one read-only request with the credential already on this machine and
prints only the status and the percentages.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import routes, usage  # noqa: E402
from model_routing.registry import load_registry  # noqa: E402

LIVE = os.environ.get("PITWALL_AGENT_ROUTING_USAGE_LIVE") == "1"
MEASURED = ("claude", "codex", "glm", "minimax", "model-studio")


@unittest.skipUnless(LIVE, "set PITWALL_AGENT_ROUTING_USAGE_LIVE=1 to read live usage")
class LiveUsageTests(unittest.TestCase):
    def test_every_configured_reader_answers(self) -> None:
        registry = load_registry()
        home = Path(os.environ["HOME"])
        with tempfile.TemporaryDirectory() as state:
            env = {**os.environ, "XDG_STATE_HOME": state}
            env.pop("SUBAGENT_MODEL_ROUTING_STATE_HOME", None)
            rows = usage.collect(
                env,
                registry=registry,
                routes_config=routes.load_routes(env, registry=registry),
                home=home,
            )
        measured = [row for row in rows if row.plan in MEASURED]
        self.assertTrue(measured, "no plan with a reader is configured on this machine")
        for row in measured:
            print(
                f"{row.plan} {row.account or '-'}: {row.status} {[window.used_pct for window in row.windows]} {row.detail}"
            )
            self.assertIn(
                row.status, ("ok", "warn", "limit", "unknown"), f"{row.plan}: {row.detail}"
            )


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run them to see them fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_collect tests.test_usage_cli 2>&1 | tail -3
```

Expected: `FAILED`, with errors ending `AttributeError: module 'model_routing.usage' has no attribute 'collect'`.

- [x] **Step 3: Write `collect()`**

Replace `packages/agent-routing/runtime/model_routing/usage/__init__.py` with:

```python
"""Subscription usage: one row per plan and account (stdlib only)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import request

from ..provider_setup import environment_with_user_bins, resolve_provider_binary
from . import accounts, cache, claude, codex, glm, minimax, model_studio, rows
from .accounts import Account
from .rows import (
    DETAIL_MAX,
    Opener,
    ReadError,
    Reading,
    Row,
    Unmeasured,
    derive_status,
    iso_utc,
    render_table,
)

__all__ = ["Account", "READERS", "Row", "collect", "iso_utc", "render_table", "rows"]

Reader = Callable[[Account, Mapping[str, str], Path, datetime, Opener], Reading]
READERS: dict[str, Reader] = {
    "claude": claude.read,
    "codex": codex.read,
    "glm": glm.read,
    "minimax": minimax.read,
    "model-studio": model_studio.read,
}
NO_SOURCE = "no usage source"


def _row(account: Account, moment: datetime, *, status: str, detail: str) -> Row:
    return Row(
        plan=account.plan,
        account=account.label,
        routes=account.routes,
        label=accounts.LABELS[account.plan],
        tier="",
        windows=(),
        status=status,
        detail=detail[:DETAIL_MAX],
        observed_at=iso_utc(moment),
    )


def _from_cache(account: Account, entry: Mapping[str, Any], moment: datetime) -> Row:
    failure = entry.get("error")
    stored = entry.get("row")
    if isinstance(stored, dict):
        try:
            row = replace(Row.from_dict(stored), account=account.label, routes=account.routes)
        except KeyError, TypeError, ValueError:
            row = None
        if row is not None:
            return (
                replace(row, status="stale", detail=str(failure)[:DETAIL_MAX]) if failure else row
            )
    return _row(account, moment, status="error", detail=str(failure or "no reading yet"))


def _read(
    account: Account,
    reader: Reader,
    env: Mapping[str, str],
    home: Path,
    moment: datetime,
    opener: Opener,
) -> Row:
    if account.problem:
        return _row(account, moment, status="error", detail=account.problem)
    epoch = moment.timestamp()
    entry = cache.load(env, account.plan, account.label)
    if entry is not None and not cache.due(entry, account.plan, epoch):
        return _from_cache(account, entry, moment)
    last_good = entry["row"] if entry is not None else None
    # The attempt is recorded before the request so a failing source is throttled too.
    cache.store(env, account.plan, account.label, epoch, row=last_good, error="read did not finish")
    try:
        reading = reader(account, env, home, moment, opener)
    except Unmeasured as exc:
        row = _row(account, moment, status="unknown", detail=str(exc))
        cache.store(env, account.plan, account.label, epoch, row=row.to_dict(), error=None)
        return row
    except ReadError as exc:
        failed = cache.store(env, account.plan, account.label, epoch, row=last_good, error=str(exc))
        return _from_cache(account, failed, moment)
    except AttributeError, IndexError, KeyError, TypeError, ValueError:
        failed = cache.store(
            env, account.plan, account.label, epoch, row=last_good, error="unexpected response"
        )
        return _from_cache(account, failed, moment)
    row = Row(
        plan=account.plan,
        account=account.label,
        routes=account.routes,
        label=accounts.LABELS[account.plan],
        tier=reading.tier,
        windows=reading.windows,
        status=derive_status(reading.windows, limit_reached=reading.limit_reached),
        detail=reading.detail[:DETAIL_MAX],
        observed_at=iso_utc(moment),
    )
    cache.store(env, account.plan, account.label, epoch, row=row.to_dict(), error=None)
    return row


def collect(
    env: Mapping[str, str],
    *,
    registry: Mapping[str, Any],
    routes_config: Mapping[str, Any],
    home: Path,
    now: datetime | None = None,
    opener: Opener = request.urlopen,
    installed: Callable[[str], bool] | None = None,
    readers: Mapping[str, Reader] | None = None,
) -> list[Row]:
    moment = now or datetime.now(timezone.utc)
    table = READERS if readers is None else readers
    if installed is None:
        detection = environment_with_user_bins(env, home)

        def installed(harness: str) -> bool:
            return resolve_provider_binary(harness, detection, home) is not None

    rows: list[Row] = []
    for account in accounts.discover(routes_config, registry, env, home, installed=installed):
        reader = table.get(account.plan)
        if reader is None:
            rows.append(_row(account, moment, status="unknown", detail=NO_SOURCE))
        else:
            rows.append(_read(account, reader, env, home, moment, opener))
    return rows
```

- [x] **Step 4: Add the command**

In `packages/agent-routing/runtime/model_routing/cli.py`, make five edits.

Edit 1, the imports. Find:

```python
import argparse
import json
```

Replace with:

```python
import argparse
from datetime import datetime, timezone
import json
```

Edit 2, the imports. Find:

```python
from . import model_studio
```

Replace with:

```python
from . import model_studio, usage
```

Edit 3, `build_parser`. Find:

```python
    inbox_parser = subparsers.add_parser("inbox")
```

Replace with:

```python
    usage_parser = subparsers.add_parser("usage")
    usage_parser.add_argument("--json", action="store_true", dest="json_output")
    inbox_parser = subparsers.add_parser("inbox")
```

Edit 4, `main`. Find:

```python
    if args.command == "harnesses":
        return _harnesses(args.json_output)
```

Replace with:

```python
    if args.command == "harnesses":
        return _harnesses(args.json_output)
    if args.command == "usage":
        return _usage(args.json_output)
```

Edit 5, a new function above `_routes_list`. Find:

```python
def _routes_list(json_output: bool) -> int:
```

Replace with:

```python
def _usage(json_output: bool) -> int:
    try:
        registry, config, home = _routes_context()
    except (RoutesError, RegistryError) as exc:
        print(f"pitwall-agent-routing: {exc}", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    rows = usage.collect(os.environ, registry=registry, routes_config=config, home=home, now=now)
    if json_output:
        print(json.dumps({"observed_at": usage.iso_utc(now), "plans": [row.to_dict() for row in rows]}, indent=2, sort_keys=True))
    else:
        sys.stdout.write(usage.render_table(rows, now=now))
    return 0


def _routes_list(json_output: bool) -> int:
```

- [x] **Step 5: Run the tests**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -W error::ResourceWarning -m unittest tests.test_usage_collect tests.test_usage_cli tests.test_usage_live 2>&1 | tail -3
```

Expected: `Ran 21 tests` and `OK (skipped=1)`.

- [x] **Step 6: Run the command**

Run from `packages/agent-routing`:

```bash
uv run --frozen pitwall-agent-routing usage --json | head -5
```

Expected: JSON beginning with `"observed_at"` and a `"plans"` list holding one row per subscription on this machine.

- [x] **Step 7: Run the opt-in live check**

Run from `packages/agent-routing`:

```bash
PITWALL_AGENT_ROUTING_USAGE_LIVE=1 uv run --frozen python -m unittest tests.test_usage_live -v 2>&1 | tail -12
```

Expected: one printed line per plan that has a reader, and `OK`. Every status is `ok`, `warn`, `limit`, or `unknown`. A plan that prints `error` is a finding: read its detail, fix the reader or its test, and run this step again before committing.

- [x] **Step 8: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/usage/__init__.py packages/agent-routing/runtime/model_routing/cli.py packages/agent-routing/tests/test_usage_collect.py packages/agent-routing/tests/test_usage_cli.py packages/agent-routing/tests/test_usage_live.py
git commit -s -m "feat(agent-routing): the usage command"
```

---

### Task 11: The host boundary exception

**Files:**
- Modify: `packages/agent-routing/plugins/pitwall/hooks/dag-tripwire.py` (new `routes_file`, `second_account_route`, `native_claude_route`; the two checks in `main`; the `ROUTE BOUNDARY` message)
- Modify: `packages/agent-routing/tests/test_claude_tripwires.py` (`hook_env`, `run_hook`, two new tests in `RouteShimTripwireTests`)

**Interfaces:**
- Consumes: a saved route whose `env` sets `CLAUDE_CONFIG_DIR` (Task 3 documents it; `env` already exists).
- Produces: `route-shim.sh <name>` passes the `ROUTE BOUNDARY` check when `<name>` is a saved route that sets `CLAUDE_CONFIG_DIR`. Every other Claude spec is blocked as before. The workflow runner is unchanged.

The hook is a standalone script. It imports nothing from the runtime and runs no command, so it reads `routes.json` itself and answers "not a second account" on anything it cannot read.

- [x] **Step 1: Write the failing tests**

In `packages/agent-routing/tests/test_claude_tripwires.py`, make four edits.

Edit 1, in `hook_env`, so hook tests never read the developer's own routes. Find:

```python
    env["XDG_STATE_HOME"] = str(Path(_HOOK_HOME) / "state")
    env.pop("SUBAGENT_MODEL_ROUTING_STATE_HOME", None)
```

Replace with:

```python
    env["XDG_STATE_HOME"] = str(Path(_HOOK_HOME) / "state")
    env["XDG_CONFIG_HOME"] = str(Path(_HOOK_HOME) / "config")
    env.pop("SUBAGENT_MODEL_ROUTING_STATE_HOME", None)
    env.pop("SUBAGENT_MODEL_ROUTING_ROUTES", None)
```

Edit 2, the `run_hook` signature. Find:

```python
    *,
    stop_hook_active: bool = False,
) -> dict[str, object] | None:
    with tempfile.NamedTemporaryFile(
```

Replace with:

```python
    *,
    stop_hook_active: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, object] | None:
    with tempfile.NamedTemporaryFile(
```

Edit 3, in `run_hook`. Find:

```python
            [sys.executable, str(hook)],
            env=hook_env(),
            input=json.dumps(
                {
                    "stop_hook_active": stop_hook_active,
```

Replace with:

```python
            [sys.executable, str(hook)],
            env={**hook_env(), **(env or {})},
            input=json.dumps(
                {
                    "stop_hook_active": stop_hook_active,
```

Edit 4. Add these two methods to the end of `RouteShimTripwireTests`, directly above `class LaunchGuardTests`, with one blank line before them and two after:

```python
def test_a_saved_claude_route_with_its_own_login_directory_passes_the_boundary(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        routes_file = Path(directory) / "routes.json"
        routes_file.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "models": {
                        "claude-home": {
                            "model": "sonnet",
                            "harness": "claude",
                            "env": {"CLAUDE_CONFIG_DIR": "/srv/logins/claude-home"},
                        },
                        "claude-plain": {"model": "sonnet", "harness": "claude", "env": {}},
                        "claude-blank": {
                            "model": "sonnet",
                            "harness": "claude",
                            "env": {"CLAUDE_CONFIG_DIR": "  "},
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        env = {"SUBAGENT_MODEL_ROUTING_ROUTES": str(routes_file)}
        for spec, blocked in (
            ("claude-home", False),
            ("claude-home@claude", False),
            ("claude-plain", True),
            ("claude-blank", True),
            ("claude-missing", True),
            ("sonnet", True),
        ):
            with self.subTest(spec=spec):
                response = run_hook(
                    DAG_TRIPWIRE,
                    [user_entry(DAG_COMMAND), bash_entry(f"scripts/route-shim.sh {spec} p.md")],
                    env=env,
                )
                reason = str(response["reason"]) if response else ""
                self.assertEqual(blocked, "ROUTE BOUNDARY" in reason, reason)
        agent = agent_entry("pitwall:route-shim")
        agent["message"]["content"][0]["input"]["prompt"] = (  # type: ignore[index]
            "Run verbatim: ~/.claude/scripts/route-shim.sh claude-home /tmp/x.md"
        )
        response = run_hook(DAG_TRIPWIRE, [user_entry(DAG_COMMAND), agent], env=env)
        self.assertNotIn("ROUTE BOUNDARY", str(response["reason"]) if response else "")


def test_an_unreadable_routes_file_leaves_the_boundary_in_force(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        routes_file = Path(directory) / "routes.json"
        for content in (
            "",
            "{not json",
            "[]",
            '{"models": []}',
            '{"models": {"claude-home": {"env": "text"}}}',
        ):
            with self.subTest(content=content):
                routes_file.write_text(content, encoding="utf-8")
                response = run_hook(
                    DAG_TRIPWIRE,
                    [user_entry(DAG_COMMAND), bash_entry("scripts/route-shim.sh claude-home p.md")],
                    env={"SUBAGENT_MODEL_ROUTING_ROUTES": str(routes_file)},
                )
                self.assertIn("ROUTE BOUNDARY", str(response["reason"]) if response else "")
```

- [x] **Step 2: Run them to see them fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_claude_tripwires.RouteShimTripwireTests 2>&1 | tail -4
```

Expected: `FAILED`: `claude-home` is still blocked.

- [x] **Step 3: Edit the hook**

In `packages/agent-routing/plugins/pitwall/hooks/dag-tripwire.py`, make three edits.

Edit 1, add three functions above `route_specs`. Find:

```python
def route_specs(cmd):
```

Replace with:

```python
def routes_file():
    configured = os.environ.get("SUBAGENT_MODEL_ROUTING_ROUTES")
    if configured:
        return os.path.expanduser(configured)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "subagent-model-routing", "routes.json")


def second_account_route(spec):
    """True when the spec names a saved route that sets its own Claude login directory.

    Such a route reaches a second Claude account, so it is not native work. Anything
    unreadable or unexpected answers False and the boundary applies as before.
    """
    name = str(spec).split("@", 1)[0]
    try:
        with open(routes_file(), encoding="utf-8") as handle:
            value = json.load(handle)["models"][name]["env"]["CLAUDE_CONFIG_DIR"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return isinstance(value, str) and bool(value.strip())


def native_claude_route(spec):
    return bool(CLAUDE_ROUTE_SPEC_RE.match(spec)) and not second_account_route(spec)


def route_specs(cmd):
```

Edit 2, in `main`. This text appears twice, once for `Agent` calls and once for `Bash` calls; change both. Find:

```python
                    if CLAUDE_ROUTE_SPEC_RE.match(spec):
                        claude_route = spec
```

Replace with:

```python
                    if native_claude_route(spec):
                        claude_route = spec
```

Edit 3, the message. Find:

```text
            "stay native in this host (Agent/Workflow); route-shim is for codex, kimi, grok, qwen, and "
            "opencode routes only. Re-dispatch the Claude work natively and continue."
```

Replace with:

```text
            "stay native in this host (Agent/Workflow); route-shim is for codex, kimi, grok, qwen, and "
            "opencode routes, and for a saved Claude route that sets CLAUDE_CONFIG_DIR to reach a second "
            "account. Re-dispatch the Claude work natively and continue."
```

- [x] **Step 4: Run the tests**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_claude_tripwires 2>&1 | tail -3
```

Expected: `Ran 51 tests` and `OK`.

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/plugins/pitwall/hooks/dag-tripwire.py packages/agent-routing/tests/test_claude_tripwires.py
git commit -s -m "feat(agent-routing): let a second-account Claude route through the host boundary"
```

---

### Task 12: Skill rules and documentation

**Files:**
- Modify: `packages/agent-routing/plugins/pitwall/skills/subagent-model-routing/SKILL.md`, `packages/agent-routing/plugins/pitwall-codex/skills/subagent-model-routing/SKILL.md`, `packages/agent-routing/plugins/pitwall-copilot/skills/subagent-model-routing/SKILL.md`
- Create: `packages/agent-routing/docs/usage.md`, `qa/concepts/subscription-usage.md`
- Modify: `packages/agent-routing/docs/routes.md`, `packages/agent-routing/README.md`, `packages/agent-routing/CHANGELOG.md`, `docs/sdlc/23-agent-routing.md`, `qa/concepts/README.md`
- Test: `packages/agent-routing/tests/test_usage_skill_rules.py`

**Interfaces:**
- Consumes: the `usage` command (Task 10), the `account` field (Task 3), the boundary exception (Task 11).
- Produces: documentation and skill text only.

- [x] **Step 1: Write the failing test**

Create `packages/agent-routing/tests/test_usage_skill_rules.py`:

```python
"""Every host's routing skill teaches the usage rules, and each native host names its exception."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    "claude": ROOT / "plugins" / "pitwall" / "skills" / "subagent-model-routing" / "SKILL.md",
    "codex": ROOT / "plugins" / "pitwall-codex" / "skills" / "subagent-model-routing" / "SKILL.md",
    "copilot": ROOT
    / "plugins"
    / "pitwall-copilot"
    / "skills"
    / "subagent-model-routing"
    / "SKILL.md",
}
RULES = (
    "### Subscription usage",
    "run `pitwall-agent-routing usage --json`",
    "Do not choose an account whose status is `limit`.",
    "at or above 90 percent",
    "10 percent reserve",
    "use the route of the account with the most room",
    "Treat `error`, `stale`, and `unknown` as no information.",
    "say so to the user and continue",
)


class UsageSkillRuleTests(unittest.TestCase):
    def test_every_host_carries_the_same_rules(self) -> None:
        for host, path in SKILLS.items():
            text = path.read_text(encoding="utf-8")
            for rule in RULES:
                with self.subTest(host=host, rule=rule):
                    self.assertEqual(1, text.count(rule))

    def test_the_rules_come_before_the_self_hosted_section(self) -> None:
        for host, path in SKILLS.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(host=host):
                self.assertLess(
                    text.index("### Subscription usage"),
                    text.index("### Self-hosted through Pitwall"),
                )

    def test_each_native_host_names_its_second_account_exception(self) -> None:
        claude = SKILLS["claude"].read_text(encoding="utf-8")
        self.assertIn("a saved route whose `env` sets `CLAUDE_CONFIG_DIR`", claude)
        self.assertIn("Claude work stays native", claude)
        codex = SKILLS["codex"].read_text(encoding="utf-8")
        self.assertIn("a saved route whose `env` sets `CODEX_HOME`", codex)
        self.assertIn("keep GPT work inline", codex)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_skill_rules 2>&1 | tail -3
```

Expected: `FAILED`: no skill carries the rules yet.

- [x] **Step 3: Add the rules to all three skills**

In each of the three `SKILL.md` files, insert this section directly above the line `### Self-hosted through Pitwall`, followed by one blank line:

```markdown
### Subscription usage

1. Before choosing a subscription-backed route, run `pitwall-agent-routing usage --json`. Each row is one plan and account: `windows` holds percent used and the reset time, `status` is `ok`, `warn`, `limit`, `error`, `stale`, or `unknown`, and `routes` names the routes that reach that account (empty means the harness default).
2. Do not choose an account whose status is `limit`.
3. Avoid an account with any window at or above 90 percent when another suitable one is below it. This keeps a 10 percent reserve.
4. When a plan has more than one account, use the route of the account with the most room.
5. Treat `error`, `stale`, and `unknown` as no information. They are not a reason to avoid an account.
6. When this host's own account is at `warn` or `limit` and no other account of that plan is configured, say so to the user and continue.

Details: `docs/usage.md`.
```

Then, in the Claude Code skill only. Find:

```markdown
Never pass a Claude model spec (`sonnet`, `opus`, `fable`, `haiku`, `claude-*`) to route-shim from this host; Claude work stays native.
```

Replace with:

```markdown
Never pass a Claude model spec (`sonnet`, `opus`, `fable`, `haiku`, `claude-*`) to route-shim from this host; Claude work stays native. The one exception is a saved route whose `env` sets `CLAUDE_CONFIG_DIR`: it reaches a second Claude account, and route-shim may dispatch it.
```

And in the Codex skill only. Find:

```markdown
Do not resolve a route to the codex harness from Codex; keep GPT work inline.
```

Replace with:

```markdown
Do not resolve a route to the codex harness from Codex; keep GPT work inline. The one exception is a saved route whose `env` sets `CODEX_HOME`: it reaches a second Codex account, and route-shim may dispatch it.
```

- [x] **Step 4: Write `docs/usage.md`**

Create `packages/agent-routing/docs/usage.md`:

````markdown
# Subscription usage

`pitwall-agent-routing usage` shows how much of each coding subscription on this machine has been
used, and which route reaches each account. The routing skill reads the same rows before it
chooses where to send work.

## The command

```bash
pitwall-agent-routing usage          # a table
pitwall-agent-routing usage --json   # {"observed_at": ..., "plans": [row, ...]}
```

The exit code is 0 whenever the command ran. Each row carries its own status. A bad flag or an
unreadable `routes.json` exits 2.

## A row

| Field | Meaning |
|---|---|
| `plan` | `claude`, `codex`, `glm`, `minimax`, `model-studio`, `kimi`, `grok`, `muse`, `gemini`, or `opencode-go` |
| `account` | Short label for the account; empty when the plan has one account |
| `routes` | Routes that send work to this account; empty means the harness default |
| `label`, `tier` | Display name, and the plan tier when the source reports one |
| `windows` | `{name, used_pct, resets_at}` for `5h`, `7d`, or `30d`; times are UTC |
| `status` | `ok`, `warn` (80 percent or more), `limit` (100 percent or a reported limit), `error`, `stale`, or `unknown` |
| `detail` | The reason for `error`, `stale`, or `unknown`, or an extra figure |
| `observed_at` | When the row was read |

`error`, `stale`, and `unknown` mean there is no information. They never mean the plan is used up.
`stale` keeps the last good numbers.

## Where each plan is read from

| Plan | Read from | Present when |
|---|---|---|
| `claude` | Anthropic's usage endpoint, with the login in the Claude Code login directory | the login directory holds `.credentials.json` |
| `codex` | The ChatGPT usage endpoint, with the login in the Codex login directory | the login directory holds `auth.json` |
| `glm` | The Z.ai quota endpoint | `GLM_API_KEY` is set, or OpenCode's auth store has `zai-coding-plan` |
| `minimax` | The MiniMax plan endpoint | `MINIMAX_API_KEY` is set, or OpenCode's auth store has `minimax-coding-plan` |
| `model-studio` | The Token Plan statistics read and the local exhaustion lockout | a Token Plan endpoint is in `routes.json` |
| `kimi`, `grok`, `muse`, `gemini` | Nothing; the row is `unknown` | the harness is installed |
| `opencode-go` | Nothing; the row is `unknown` | OpenCode's auth store has `opencode-go` |

Every read is one GET with the credential already on the machine. Nothing refreshes a token,
writes a credential file, or prints a credential. Model Studio shows percentages only when an
Alibaba Cloud AccessKey pair is configured; without one the row is `unknown` and names the renewal
date.

## A second account

An account is a plan plus a login directory. Declare a second one with a route:

```bash
CLAUDE_CONFIG_DIR="$HOME/.claude-home" claude   # sign in once with the second account

pitwall-agent-routing routes add claude-home --model sonnet --harness claude \
  --env CLAUDE_CONFIG_DIR="$HOME/.claude-home" --account home
```

Codex works the same way with `CODEX_HOME`. Use an absolute path. `--account` is a label of 1 to
16 letters, digits, or hyphens; without it the label comes from the directory name. A route for
the same harness with `--account` and no login directory labels the default account.

The row for the second account lists `claude-home` under `routes`, so a reader sees both that the
account has room and how to reach it. An account that has not been used for a while has an expired
login; its row is `stale` and says so. Sending work to it once makes its CLI sign in again.

GLM and MiniMax have one account each: `routes.json` refuses variable names that look like
secrets, so a second key cannot be declared in a route.

## How often a plan is read

Each account has a file under the state directory, `usage/<plan>[-<account>].json`, holding the
last good row. Claude is read at most once in 300 seconds and every other plan once in 60; inside
that time the stored row is returned. A failed read is throttled the same way.
````

- [x] **Step 5: Update the routes document**

In `packages/agent-routing/docs/routes.md`, make two edits.

Edit 1, a new row in the Commands table. Find:

```markdown
| `pitwall-agent-routing harnesses [--json]` | installed path, version, kind, endpoint delivery, effort control, and route count for every harness |
```

Replace with:

```markdown
| `pitwall-agent-routing harnesses [--json]` | installed path, version, kind, endpoint delivery, effort control, and route count for every harness |
| `pitwall-agent-routing usage [--json]` | one row per subscription plan and account: percent used per window, reset time, status, and the routes that reach it; see `docs/usage.md` |
```

Edit 2, in Host boundaries. Find:

```markdown
reports `nativeTo` so any host can apply the same rule.
```

Replace with:

```markdown
reports `nativeTo` so any host can apply the same rule. One exception: a saved route whose `env` sets `CLAUDE_CONFIG_DIR` (or `CODEX_HOME` on a Codex host) reaches a second account of that plan, so it is not native work. The Claude Code tripwire reads `routes.json` and lets such a route through; the workflow runner still rejects Claude tasks from a Claude host.
```

- [x] **Step 6: Update the README, changelog, SDLC chapter, and QA packet**

In `packages/agent-routing/README.md`. Find:

```markdown
- **[Pitwall handoff]{docs/pitwall.md}** — serve a leased model as an expiry-aware route profile.
```

Replace with:

```markdown
- **[Pitwall handoff]{docs/pitwall.md}** — serve a leased model as an expiry-aware route profile.
- **[Subscription usage]{docs/usage.md}** — how much of each coding subscription is used, and how a second account is declared.
```

In `packages/agent-routing/CHANGELOG.md`, directly under `## [Unreleased]` and its `### Added` heading, add:

```markdown
- `pitwall-agent-routing usage [--json]`: one row per subscription plan and account, with
  percent used per window, reset time, and status. Claude, Codex, GLM, MiniMax, and Model
  Studio are read; every read is one GET with the credential already on the machine, and
  nothing refreshes a token.
- Route entries accept `account`, and `routes add` accepts `--account`. A route whose `env`
  sets `CLAUDE_CONFIG_DIR` or `CODEX_HOME` declares a second account of that plan.
- The routing skills read usage before choosing a route and keep a 10 percent reserve.
- The Claude Code host boundary lets a saved Claude route through when it sets
  `CLAUDE_CONFIG_DIR`.
```

In `docs/sdlc/23-agent-routing.md`, insert this section directly above `## Pi endpoint configuration ownership`. Each `path:line` below is filled from the command in the next step, run against the code as committed:

```markdown
## Subscription usage

`pitwall-agent-routing usage` reports one row per subscription plan and account
(`packages/agent-routing/runtime/model_routing/usage/__init__.py`, `collect`). A row holds percent
used per window, the reset instant, and one of six statuses; `error`, `stale`, and `unknown` mean
no information (`packages/agent-routing/runtime/model_routing/usage/rows.py`, `derive_status`).

Readers are read-only. Each makes one GET with the credential already on the machine, never
refreshes a token, and reports a failure as a status code plus fixed wording
(`packages/agent-routing/runtime/model_routing/usage/rows.py`, `fetch_json`). An account is a plan
plus a login directory; a route whose `env` sets `CLAUDE_CONFIG_DIR` or `CODEX_HOME` declares a
second one (`packages/agent-routing/runtime/model_routing/usage/accounts.py`, `_login_accounts`).
The last good row, the last failure, and the last attempt are kept per account under the state
directory, and the attempt is recorded before the request so a failing source is throttled too
(`packages/agent-routing/runtime/model_routing/usage/cache.py`, `store`).

The Claude Code host boundary reads `routes.json` and lets a saved Claude route through when it
sets `CLAUDE_CONFIG_DIR` (`packages/agent-routing/plugins/pitwall/hooks/dag-tripwire.py`,
`second_account_route`).
```

In the same file. Find:

```markdown
- [Routes]{../../packages/agent-routing/docs/routes.md}
```

Replace with:

```markdown
- [Routes]{../../packages/agent-routing/docs/routes.md}
- [Subscription usage]{../../packages/agent-routing/docs/usage.md}
```

Create `qa/concepts/subscription-usage.md`:

````markdown
# Subscription usage

## In one sentence

`pitwall-agent-routing usage` lists each coding subscription on the machine with how much of it
has been used and when it resets.

## Why it matters when testing Pitwall

The routing skill reads these rows before it picks a model, so a wrong status sends work to a
plan that is used up, or away from one that is fine. A row that could not be read must say
`error`, `stale`, or `unknown`; it must never look exhausted.

## Try it

```bash
pitwall-agent-routing usage --json
```

Expected: JSON with `observed_at` and `plans`. On a machine with no subscriptions `plans` is `[]`
and the exit code is still 0.

## Common confusions

- `limit` means the plan is used up. `error` means the read failed. They are different.
- `stale` shows old numbers on purpose, with the reason in `detail`.
- The command never signs in, refreshes a login, or prints a key.

## Check yourself

1. A row shows `status: unknown`. Is that plan used up?

<details><summary>Answer</summary>

No. `unknown` means usage cannot be measured for that plan. It carries no information about how
much is left.

</details>

## Go deeper

- [Subscription usage]{../../packages/agent-routing/docs/usage.md}
- [Agent Routing]{../../docs/sdlc/23-agent-routing.md}
````

In `qa/concepts/README.md`. Find:

```markdown
- [MCP and agent clients]{mcp-and-agent-clients.md}
```

Replace with:

```markdown
- [MCP and agent clients]{mcp-and-agent-clients.md}
- [Subscription usage]{subscription-usage.md}
```

- [x] **Step 7: Put the line numbers into the SDLC section**

Run from `the repository root`:

```bash
grep -n 'def collect\|def derive_status\|def fetch_json\|def _login_accounts\|def store\|def second_account_route' packages/agent-routing/runtime/model_routing/usage/*.py packages/agent-routing/plugins/pitwall/hooks/dag-tripwire.py
```

Expected: six lines, one per function. In the section just added, write each citation as `path:line` using the number printed, replacing the `path`, `function` pair.

- [x] **Step 8: Run the tests and the document gates**

Run from `the repository root`:

```bash
(cd packages/agent-routing && uv run --frozen python -m unittest tests.test_usage_skill_rules tests.test_parity tests.test_plugin_identity 2>&1 | tail -3 && uv run --frozen python tools/validate_plugins.py)
make docs-check 2>&1 | tail -2
uv run python tools/guards/repo_text_policy.py packages/agent-routing/docs/usage.md packages/agent-routing/docs/routes.md packages/agent-routing/README.md packages/agent-routing/CHANGELOG.md docs/sdlc/23-agent-routing.md qa/concepts/subscription-usage.md qa/concepts/README.md; echo "policy exit=$?"
```

Expected: `OK`; `all plugin structures and host-native boundaries are valid`; `markdown links passed`; `policy exit=0`.

- [x] **Step 9: Commit**

Run from the repository root:

```bash
git add packages/agent-routing/plugins packages/agent-routing/docs/usage.md packages/agent-routing/docs/routes.md packages/agent-routing/README.md packages/agent-routing/CHANGELOG.md packages/agent-routing/tests/test_usage_skill_rules.py docs/sdlc/23-agent-routing.md qa/concepts/subscription-usage.md qa/concepts/README.md
git commit -s -m "docs(agent-routing): usage rules in the routing skills, the usage guide, and the QA card"
```

---

### Task 13: Release acceptance and gates for stage 1

**Files:**
- Modify: `tests/release/test_cli_all_commands_journey.py:233` (`test_every_argument_surface_is_registered_and_documented`)
- Modify: `tests/release/cli_fixtures.json`
- Modify: `scripts/release/run-user-journeys.sh:675` (`j36`)
- Modify: `release_acceptance/denominator.json`, `release_acceptance/reviewed-bindings.json`, `.secrets.baseline`

**Interfaces:**
- Consumes: the `usage` command and `--account` (Tasks 3 and 10).
- Produces: release pins that match the code.

The base branch is already behind. The CLI journey test pins 444 argument surfaces where discovery finds 455, and `routes add-model-studio-endpoint` has no fixture. This task puts both right and then adds this work's three surfaces, for 458.

- [x] **Step 1: Count the surfaces**

Run from `the repository root`:

```bash
uv run python -c "from pathlib import Path; from tools.release_acceptance import cli_arguments; print(len(cli_arguments.discover_with_issues(Path('.'))['surfaces']))"
```

Expected: `458`.

- [x] **Step 2: Pin the count**

In `tests/release/test_cli_all_commands_journey.py`. Find:

```python
    assert len(surfaces) == 444
```

Replace with:

```python
    assert len(surfaces) == 458
```

In `scripts/release/run-user-journeys.sh`. Find:

```bash
check ${id} "all CLI command paths and 444 argument surfaces through the entry points" \\
```

Replace with:

```bash
check ${id} "all CLI command paths and 458 argument surfaces through the entry points" \\
```

- [x] **Step 3: Add the fixtures**

In `tests/release/cli_fixtures.json`, add these two entries, keeping the file's key order alphabetical:

```json
  "pitwall-agent-routing|routes add-model-studio-endpoint": {"argv": ["routes", "add-model-studio-endpoint", "j36-ms", "--plan", "token-plan-personal", "--tier", "pro"], "database": false, "exit": 0, "stdout_contains": "saved j36-ms"},
  "pitwall-agent-routing|usage": {"argv": ["usage", "--json"], "database": false, "exit": 0, "json_keys": ["observed_at", "plans"]},
```

- [x] **Step 4: Confirm what the Model Studio command prints**

Run from `packages/agent-routing`:

```bash
T=$(mktemp -d) && HOME=$T XDG_CONFIG_HOME=$T/config uv run --frozen pitwall-agent-routing routes add-model-studio-endpoint j36-ms --plan token-plan-personal --tier pro; echo "exit=$?"; rm -rf $T
```

Expected: a line beginning `saved j36-ms` and `exit=0`. If the line differs, put its first two words in the fixture's `stdout_contains`.

- [x] **Step 5: Update the denominator**

In `release_acceptance/denominator.json`, set `"cli_arguments:cli"` to `458`, set `"total"` to `1285`, and replace `"reason"` with:

```json
  "reason": "2026-09-28: `pitwall-agent-routing usage` adds a subcommand and its --json argument, and `routes add` gains --account: three cli_argument surfaces bound through the CLI journey; previously 1282 after `routes add-model-studio-endpoint` added eleven",
```

- [x] **Step 6: Regenerate the bindings and the secret baseline**

Run from `the repository root`:

```bash
uv run --frozen python -m tools.release_acceptance.bind_surfaces 2>&1 | tail -5
make sec-baseline 2>&1 | tail -3
uv run python tools/security/check_secrets.py; echo "secrets exit=$?"
```

Expected: `bind_surfaces` names no unmapped surface; `secret scan passed` and `secrets exit=0`. Audit every finding `make sec-baseline` adds: each must be a fake value in a test or fixture.

- [x] **Step 7: Run the release-acceptance tests and the CLI journey**

Run from `the repository root`:

```bash
uv run pytest tests/release_acceptance -q 2>&1 | tail -3
make up
eval "$(grep -E '^export (DATABASE_URL|REDIS_URL)=' README.md)"
PITWALL_JOURNEY_HARNESS=1 RUNPOD_API_KEY=local-dry-run-key PITWALL_ADMIN_SECRET=journey-admin-secret uv run --frozen pytest -q -m release tests/release/test_cli_all_commands_journey.py -p no:randomly 2>&1 | tail -5
```

Expected: both end with `passed` and no `failed`. The `eval` line takes the test database and Redis addresses from the README quick start.

- [x] **Step 8: Run the full gates**

Run from `the repository root`:

```bash
(cd packages/agent-routing && uv run --frozen ruff check runtime tests tools scripts/pitwall-agent-routing && uv run --frozen mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing && uv run --frozen python tools/validate_json_schemas.py && uv run --frozen python tools/validate_plugins.py && uv run --frozen python tools/validate_registry.py && uv run --frozen python tools/check_generated.py && uv run --frozen python -m unittest discover -s tests 2>&1 | tail -3)
make test-fast 2>&1 | tail -3
make test-int 2>&1 | tail -3
make down
```

Expected: every command exits 0; the Agent Routing suite ends `OK`; `make test-fast` and `make test-int` end with `passed` and no `failed`. Run `make test-int` only after the CLI journey has finished: they share one database.

- [x] **Step 9: Commit**

Run from the repository root:

```bash
git add tests/release/test_cli_all_commands_journey.py tests/release/cli_fixtures.json scripts/release/run-user-journeys.sh release_acceptance .secrets.baseline
git commit -s -m "chore(release): bind the usage surfaces and repair the CLI journey pins"
```

---

### Task 14: Sampling and the two payloads

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/usage/serve.py`
- Create: `packages/agent-routing/tests/fixtures/usage/submeter-usage.json`
- Test: `packages/agent-routing/tests/test_usage_serve.py`

**Interfaces:**
- Consumes: `rows.Row`, `rows.iso_utc`, `rows.parse_instant` (Task 2); `accounts.LABELS` in the test (Task 4).
- Produces, in `model_routing.usage.serve`:
  - `row_key(row) -> str`
  - `Sampler()` with `observe(rows, now_epoch)`, `rate(row, window, now_epoch) -> float | None`, `eta_minutes(row, window, now_epoch) -> int | None`, `history(row, window="5h") -> list[int]`
  - `legacy_payload(rows, sampler, now) -> dict` and `plans_payload(rows, sampler, now) -> dict`

The fixture is a payload recorded from the submeter aggregator's own code. The contract test holds the new payload to its field names, field order, and value types.

- [x] **Step 1: Add the recorded payload**

Create `packages/agent-routing/tests/fixtures/usage/submeter-usage.json`:

```json
{
 "updated": 1790576734,
 "providers": [
  {
   "name": "claude",
   "label": "Claude",
   "tier": "Max 20x",
   "s_pct": 42,
   "s_reset_min": 120,
   "w_pct": 24,
   "w_reset_min": 5000,
   "status": "ok",
   "extra": {},
   "s_rate": null,
   "w_rate": null,
   "s_eta_min": null,
   "w_eta_min": null,
   "s_hist": [
    42
   ]
  },
  {
   "name": "codex",
   "label": "Codex",
   "tier": "Pro",
   "s_pct": 35,
   "s_reset_min": 120,
   "w_pct": 10,
   "w_reset_min": 5000,
   "status": "ok",
   "extra": {},
   "s_rate": null,
   "w_rate": null,
   "s_eta_min": null,
   "w_eta_min": null,
   "s_hist": [
    35
   ]
  },
  {
   "name": "glm",
   "label": "GLM",
   "tier": "Coding Plan",
   "s_pct": 21,
   "s_reset_min": 120,
   "w_pct": 42,
   "w_reset_min": 5000,
   "status": "ok",
   "extra": {},
   "s_rate": null,
   "w_rate": null,
   "s_eta_min": null,
   "w_eta_min": null,
   "s_hist": [
    21
   ]
  }
 ]
}
```

- [x] **Step 2: Write the failing test**

Create `packages/agent-routing/tests/test_usage_serve.py`:

```python
"""The serve mode: sampling, the desk meter contract, the token rule, and the HTTP surface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.usage import accounts, serve  # noqa: E402
from model_routing.usage.rows import Row, Window  # noqa: E402

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
RECORDED = json.loads(
    (ROOT / "tests" / "fixtures" / "usage" / "submeter-usage.json").read_text(encoding="utf-8")
)


def row(
    plan: str,
    *,
    account: str = "",
    five: int | None = 10,
    week: int | None = 20,
    status: str = "ok",
    detail: str = "",
    tier: str = "Pro",
    long_name: str = "7d",
) -> Row:
    windows = (
        Window("5h", five, "2026-09-28T14:00:00Z"),
        Window(long_name, week, "2026-10-01T12:00:00Z"),
    )
    return Row(
        plan,
        account,
        (),
        accounts.LABELS.get(plan, plan),
        tier,
        windows,
        status,
        detail,
        "2026-09-28T12:00:00Z",
    )


class SamplerTests(unittest.TestCase):
    def test_no_rate_until_a_baseline_is_five_minutes_old(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=10)], 0.0)
        self.assertIsNone(sampler.rate(row("claude", five=12), "5h", 299.0))
        self.assertEqual(0.4, sampler.rate(row("claude", five=12), "5h", 300.0))

    def test_a_baseline_older_than_ten_minutes_is_not_used(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=10)], 0.0)
        self.assertIsNone(sampler.rate(row("claude", five=40), "5h", 601.0))

    def test_the_newest_aged_sample_is_the_baseline(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=10)], 0.0)
        sampler.observe([row("claude", five=20)], 200.0)
        self.assertEqual(2.0, sampler.rate(row("claude", five=30), "5h", 500.0))

    def test_a_window_reset_gives_no_rate(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=99)], 0.0)
        self.assertIsNone(sampler.rate(row("claude", five=2), "5h", 300.0))

    def test_time_to_full_needs_a_rate_above_the_floor(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=50, week=50)], 0.0)
        current = row("claude", five=60, week=50)
        self.assertEqual(20, sampler.eta_minutes(current, "5h", 300.0))
        self.assertIsNone(sampler.eta_minutes(current, "7d", 300.0))

    def test_failed_rows_add_no_samples_and_carry_no_rate(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=10)], 0.0)
        sampler.observe([row("claude", five=10, status="stale")], 300.0)
        self.assertEqual([10], sampler.history(row("claude")))
        self.assertIsNone(sampler.rate(row("claude", five=30, status="stale"), "5h", 300.0))
        self.assertIsNone(sampler.eta_minutes(row("claude", five=30, status="stale"), "5h", 300.0))

    def test_history_keeps_one_point_per_five_minutes_and_the_newest_twenty_four(self) -> None:
        sampler = serve.Sampler()
        for index in range(60):
            sampler.observe([row("claude", five=index)], index * 150.0)
        self.assertEqual(list(range(12, 60, 2)), sampler.history(row("claude")))

    def test_accounts_of_one_plan_keep_separate_series(self) -> None:
        sampler = serve.Sampler()
        sampler.observe(
            [row("claude", account="work", five=10), row("claude", account="home", five=70)], 0.0
        )
        self.assertEqual([10], sampler.history(row("claude", account="work")))
        self.assertEqual([70], sampler.history(row("claude", account="home")))


class LegacyPayloadTests(unittest.TestCase):
    def payload(
        self, rows: list[Row], sampler: serve.Sampler | None = None, now: datetime = NOW
    ) -> dict[str, object]:
        return serve.legacy_payload(rows, sampler or serve.Sampler(), now)

    def test_the_shape_matches_a_payload_recorded_from_submeter(self) -> None:
        sampler = serve.Sampler()
        rows = [
            row("claude", five=42, week=24, tier="Max 20x"),
            row("codex", five=35, week=10),
            row("glm", five=21, week=42, tier="Coding Plan"),
        ]
        sampler.observe(rows, NOW.timestamp())
        ours = self.payload(rows, sampler)
        self.assertEqual(list(RECORDED), list(ours))
        self.assertIsInstance(ours["updated"], int)
        for recorded, provider in zip(RECORDED["providers"], ours["providers"]):  # type: ignore[arg-type]
            self.assertEqual(list(recorded), list(provider))
            for key, value in recorded.items():
                if value is not None:
                    self.assertIs(type(value), type(provider[key]), key)
            for key in (
                "name",
                "label",
                "tier",
                "s_pct",
                "w_pct",
                "status",
                "extra",
                "s_rate",
                "w_rate",
                "s_eta_min",
                "w_eta_min",
                "s_hist",
            ):
                self.assertEqual(recorded[key], provider[key], key)

    def test_minutes_are_computed_from_the_reset_instant_and_never_negative(self) -> None:
        (provider,) = self.payload([row("claude")])["providers"]  # type: ignore[misc]
        self.assertEqual((120, 4320), (provider["s_reset_min"], provider["w_reset_min"]))
        (late,) = self.payload([row("claude")], now=NOW + timedelta(days=30))["providers"]  # type: ignore[misc]
        self.assertEqual((0, 0), (late["s_reset_min"], late["w_reset_min"]))

    def test_the_thirty_day_window_stands_in_for_the_long_window(self) -> None:
        (provider,) = self.payload(
            [
                Row(
                    "model-studio",
                    "",
                    (),
                    "Model Studio",
                    "Pro",
                    (Window("30d", 75, "2026-10-08T12:00:00Z"),),
                    "ok",
                    "",
                    "2026-09-28T12:00:00Z",
                )
            ]
        )["providers"]  # type: ignore[misc]
        self.assertEqual(
            (None, None, 75, 14400),
            (
                provider["s_pct"],
                provider["s_reset_min"],
                provider["w_pct"],
                provider["w_reset_min"],
            ),
        )

    def test_unknown_rows_are_left_out_and_seven_rows_is_the_limit(self) -> None:
        rows = [row("kimi", status="unknown")] + [row(f"plan{index}") for index in range(9)]
        providers = self.payload(rows)["providers"]
        self.assertEqual(
            [f"plan{index}" for index in range(7)], [provider["name"] for provider in providers]
        )  # type: ignore[union-attr]

    def test_account_tags(self) -> None:
        def tags(*accounts: str) -> list[object]:
            providers = self.payload([row("claude", account=account) for account in accounts])[
                "providers"
            ]
            return [(provider["name"], provider.get("account")) for provider in providers]  # type: ignore[union-attr]

        self.assertEqual([("claude", None)], tags(""))
        self.assertEqual([("claude-work", "wor"), ("claude-home", "hom")], tags("work", "home"))
        self.assertEqual([("claude", "1"), ("claude-home", "hom")], tags("", "home"))
        self.assertEqual([("claude-work1", "1"), ("claude-work2", "2")], tags("work1", "work2"))

    def test_detail_is_cut_and_keyed_by_kind(self) -> None:
        (failed,) = self.payload(
            [row("glm", status="error", detail="x" * 200, five=None, week=None)]
        )["providers"]  # type: ignore[misc]
        self.assertEqual({"error": "x" * 80}, failed["extra"])
        (noted,) = self.payload([row("glm", detail="tools 16%")])["providers"]  # type: ignore[misc]
        self.assertEqual({"note": "tools 16%"}, noted["extra"])


class PlansPayloadTests(unittest.TestCase):
    def test_rows_gain_rate_time_to_full_and_history(self) -> None:
        sampler = serve.Sampler()
        sampler.observe([row("claude", five=50)], NOW.timestamp() - 300)
        payload = serve.plans_payload([row("claude", five=60)], sampler, NOW)
        self.assertEqual("2026-09-28T12:00:00Z", payload["observed_at"])
        five, week = payload["plans"][0]["windows"]
        self.assertEqual((2.0, 20, [50]), (five["rate"], five["eta_min"], five["history"]))
        self.assertEqual((0.0, None, []), (week["rate"], week["eta_min"], week["history"]))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 3: Run it to see it fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_serve 2>&1 | tail -3
```

Expected: an error ending `ImportError: cannot import name 'serve' from 'model_routing.usage'`.

- [x] **Step 4: Write the module**

Create `packages/agent-routing/runtime/model_routing/usage/serve.py`:

```python
"""``usage serve``: scheduled sampling, burn rate, and the desk meter contract (stdlib only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .rows import Row, iso_utc, parse_instant

RATE_WINDOW = 300.0
MAX_BASELINE = 600.0
SAMPLES_MAX = 40
HISTORY_INTERVAL = 300.0
HISTORY_MAX = 24
RATE_FLOOR = 0.05
LEGACY_ROWS = 7
LEGACY_ACCOUNT = 3
LEGACY_DETAIL = 80
LEGACY_STATUSES = ("ok", "warn", "limit", "error", "stale")
MEASURED = ("ok", "warn", "limit")


def row_key(row: Row) -> str:
    return f"{row.plan}-{row.account}" if row.account else row.plan


def _used(row: Row, window: str) -> int | None:
    return next((item.used_pct for item in row.windows if item.name == window), None)


@dataclass(frozen=True, slots=True)
class Sample:
    at: float
    values: Mapping[str, int | None]


class Sampler:
    """Readings over time, kept in memory. A restart starts the history again."""

    def __init__(self) -> None:
        self._samples: dict[str, list[Sample]] = {}
        self._history: dict[str, list[Sample]] = {}

    def observe(self, rows: Sequence[Row], now_epoch: float) -> None:
        for row in rows:
            if row.status not in MEASURED:
                continue
            sample = Sample(now_epoch, {item.name: item.used_pct for item in row.windows})
            samples = self._samples.setdefault(row_key(row), [])
            samples.append(sample)
            del samples[:-SAMPLES_MAX]
            history = self._history.setdefault(row_key(row), [])
            if not history or now_epoch - history[-1].at >= HISTORY_INTERVAL:
                history.append(sample)
                del history[:-HISTORY_MAX]

    def rate(self, row: Row, window: str, now_epoch: float) -> float | None:
        """Percent-points per minute against a baseline 5 to 10 minutes old, or None."""
        current = _used(row, window)
        if row.status not in MEASURED or current is None:
            return None
        aged = [
            s
            for s in self._samples.get(row_key(row), [])
            if RATE_WINDOW <= now_epoch - s.at <= MAX_BASELINE
        ]
        if not aged:
            return None
        baseline = aged[-1]
        earlier = baseline.values.get(window)
        if earlier is None:
            return None
        value = round((current - earlier) / ((now_epoch - baseline.at) / 60.0), 2)
        return value if value >= 0 else None

    def eta_minutes(self, row: Row, window: str, now_epoch: float) -> int | None:
        rate = self.rate(row, window, now_epoch)
        current = _used(row, window)
        if rate is None or current is None or rate <= RATE_FLOOR:
            return None
        return max(0, int(round((100 - current) / rate)))

    def history(self, row: Row, window: str = "5h") -> list[int]:
        values = (sample.values.get(window) for sample in self._history.get(row_key(row), []))
        return [value for value in values if value is not None]


def _minutes_until(resets_at: str | None, now: datetime) -> int | None:
    if resets_at is None:
        return None
    try:
        moment = parse_instant(resets_at)
    except ValueError:
        return None
    return max(0, int(round((moment - now).total_seconds() / 60.0)))


def _tags(rows: Sequence[Row]) -> dict[str, str]:
    """Account tags for the desk meter: three characters, never empty or repeated within a plan."""
    tags: dict[str, str] = {}
    plans: dict[str, list[Row]] = {}
    for row in rows:
        plans.setdefault(row.plan, []).append(row)
    for group in plans.values():
        if len(group) == 1:
            continue
        cut = [(row.account[:LEGACY_ACCOUNT] or "1") for row in group]
        if len(set(cut)) < len(cut):
            cut = [str(index + 1) for index in range(len(group))]
        for row, tag in zip(group, cut):
            tags[row_key(row)] = tag
    return tags


def legacy_payload(rows: Sequence[Row], sampler: Sampler, now: datetime) -> dict[str, Any]:
    """The payload the desk meter firmware reads. Field names and types are frozen."""
    epoch = now.timestamp()
    shown = [row for row in rows if row.status in LEGACY_STATUSES][:LEGACY_ROWS]
    tags = _tags(shown)
    providers: list[dict[str, Any]] = []
    for row in shown:
        short = next((item for item in row.windows if item.name == "5h"), None)
        long_name = "7d" if any(item.name == "7d" for item in row.windows) else "30d"
        long = next((item for item in row.windows if item.name == long_name), None)
        provider: dict[str, Any] = {"name": row_key(row), "label": row.label}
        if row_key(row) in tags:
            provider["account"] = tags[row_key(row)]
        extra: dict[str, Any] = {}
        if row.detail:
            extra["error" if row.status in ("error", "stale") else "note"] = row.detail[
                :LEGACY_DETAIL
            ]
        provider.update(
            {
                "tier": row.tier,
                "s_pct": short.used_pct if short else None,
                "s_reset_min": _minutes_until(short.resets_at, now) if short else None,
                "w_pct": long.used_pct if long else None,
                "w_reset_min": _minutes_until(long.resets_at, now) if long else None,
                "status": row.status,
                "extra": extra,
                "s_rate": sampler.rate(row, "5h", epoch),
                "w_rate": sampler.rate(row, long_name, epoch),
                "s_eta_min": sampler.eta_minutes(row, "5h", epoch),
                "w_eta_min": sampler.eta_minutes(row, long_name, epoch),
                "s_hist": sampler.history(row),
            }
        )
        providers.append(provider)
    return {"updated": int(epoch), "providers": providers}


def plans_payload(rows: Sequence[Row], sampler: Sampler, now: datetime) -> dict[str, Any]:
    epoch = now.timestamp()
    plans: list[dict[str, Any]] = []
    for row in rows:
        value = row.to_dict()
        for window in value["windows"]:
            window["rate"] = sampler.rate(row, window["name"], epoch)
            window["eta_min"] = sampler.eta_minutes(row, window["name"], epoch)
            window["history"] = (
                sampler.history(row, window["name"]) if window["name"] == "5h" else []
            )
        plans.append(value)
    return {"observed_at": iso_utc(now), "plans": plans}
```

- [x] **Step 5: Run the test**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_serve 2>&1 | tail -3
```

Expected: `Ran 15 tests` and `OK`.

- [x] **Step 6: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/usage/serve.py packages/agent-routing/tests/fixtures/usage packages/agent-routing/tests/test_usage_serve.py
git commit -s -m "feat(agent-routing): burn rate, history, and the desk meter payload"
```

---

### Task 15: The server and `usage serve`

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/usage/serve.py` (imports and constants; new `UsageState`, `refresh_once`, `resolve_token`, `make_server`, `run`)
- Modify: `packages/agent-routing/runtime/model_routing/cli.py` (imports, `build_parser`, `main`, new `_usage_serve`)
- Modify: `packages/agent-routing/tests/test_usage_serve.py`, `packages/agent-routing/tests/test_usage_cli.py`
- Modify: `packages/agent-routing/docs/usage.md`, `packages/agent-routing/docs/routes.md`, `packages/agent-routing/CHANGELOG.md`, `docs/sdlc/23-agent-routing.md`

**Interfaces:**
- Consumes: `Sampler`, `legacy_payload`, `plans_payload` (Task 14); `usage.collect` and `cli._routes_context` (Task 10); `errors.EX_CONFIG` (78).
- Produces, in `model_routing.usage.serve`:
  - `ServeConfigError`, `TOKEN_ENV`, `MIN_INTERVAL`, `LOOPBACK`
  - `UsageState()` with `update(rows, now)`, `legacy(now)`, `plans(now)`
  - `refresh_once(state, collect_rows, clock) -> bool`
  - `resolve_token(host, env) -> str | None`
  - `make_server(host, port, token, state, clock) -> ThreadingHTTPServer`
  - `run(host, port, interval, env, collect_rows, *, clock=...) -> None`
- Produces the command `pitwall-agent-routing usage serve [--host H] [--port P] [--interval SECONDS]`: exit 78 when refused, 1 when the port cannot be bound, 0 on interrupt.

- [x] **Step 1: Write the failing tests**

In `packages/agent-routing/tests/test_usage_serve.py`, add two imports below `from datetime import datetime, timedelta, timezone`:

```python
import http.client
```

and below `import sys`:

```python
import threading
```

Then add these classes directly above `if __name__ == "__main__":`:

```python
class TokenRuleTests(unittest.TestCase):
    def test_loopback_needs_no_token(self) -> None:
        for host in ("127.0.0.1", "::1", "localhost"):
            self.assertIsNone(serve.resolve_token(host, {}))
        self.assertEqual("t", serve.resolve_token("127.0.0.1", {serve.TOKEN_ENV: " t "}))

    def test_any_other_address_needs_one(self) -> None:
        for host in ("0.0.0.0", "192.0.2.10", "::"):
            with self.assertRaises(serve.ServeConfigError) as caught:
                serve.resolve_token(host, {serve.TOKEN_ENV: "  "})
            self.assertIn(serve.TOKEN_ENV, str(caught.exception))
        self.assertEqual("t", serve.resolve_token("0.0.0.0", {serve.TOKEN_ENV: "t"}))

    def test_run_refuses_before_binding(self) -> None:
        with self.assertRaises(serve.ServeConfigError):
            serve.run("0.0.0.0", 0, 45.0, {}, lambda _now: [])
        with self.assertRaises(serve.ServeConfigError) as caught:
            serve.run("127.0.0.1", 0, 29.0, {}, lambda _now: [])
        self.assertIn("at least 30 seconds", str(caught.exception))


class RefreshTests(unittest.TestCase):
    def test_a_refresh_that_raises_keeps_the_rows_already_held(self) -> None:
        state = serve.UsageState()
        self.assertTrue(
            serve.refresh_once(state, lambda _now: [row("claude", five=33)], lambda: NOW)
        )

        def broken(_now: datetime) -> list[Row]:
            raise RuntimeError("routes file vanished")

        self.assertFalse(serve.refresh_once(state, broken, lambda: NOW))
        self.assertEqual(33, state.legacy(NOW)["providers"][0]["s_pct"])


class HttpTests(unittest.TestCase):
    def serve(self, token: str | None) -> tuple[str, int]:
        state = serve.UsageState()
        state.update([row("claude", five=41, week=63)], NOW)
        server = serve.make_server("127.0.0.1", 0, token, state, lambda: NOW)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "127.0.0.1", server.server_address[1]

    def get(
        self, address: tuple[str, int], path: str, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(*address, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return (
                response.status,
                {key.lower(): value for key, value in response.getheaders()},
                response.read(),
            )
        finally:
            connection.close()

    def test_every_response_has_a_length_and_is_never_chunked(self) -> None:
        address = self.serve("desk-token")
        auth = {"Authorization": "Bearer desk-token"}
        for path, headers, expected in (
            ("/health", {}, 200),
            ("/usage", auth, 200),
            ("/plans", auth, 200),
            ("/usage", {}, 401),
            ("/nope", auth, 404),
        ):
            with self.subTest(path=path, expected=expected):
                status, sent, body = self.get(address, path, headers)
                self.assertEqual(expected, status)
                self.assertEqual(str(len(body)), sent["content-length"])
                self.assertNotIn("transfer-encoding", sent)
                json.loads(body)

    def test_the_token_is_required_and_compared_whole(self) -> None:
        address = self.serve("desk-token")
        for offered in (
            "",
            "Bearer",
            "Bearer desk-toke",
            "Bearer desk-token-more",
            "desk-token",
            "bearer desk-token",
        ):
            with self.subTest(offered=offered):
                self.assertEqual(401, self.get(address, "/plans", {"Authorization": offered})[0])
        self.assertEqual(
            200, self.get(address, "/plans", {"Authorization": "Bearer desk-token"})[0]
        )

    def test_health_is_open_and_says_nothing_else(self) -> None:
        status, _sent, body = self.get(self.serve("desk-token"), "/health")
        self.assertEqual((200, {"ok": True}), (status, json.loads(body)))

    def test_without_a_token_loopback_is_open(self) -> None:
        status, _sent, body = self.get(self.serve(None), "/usage?x=1")
        self.assertEqual(200, status)
        self.assertEqual(41, json.loads(body)["providers"][0]["s_pct"])
```

In `packages/agent-routing/tests/test_usage_cli.py`, add this class directly above `if __name__ == "__main__":`:

```python
class UsageServeCliTests(unittest.TestCase):
    def run_cli(self, *argv: str, env: dict[str, str] | None = None) -> tuple[int, str]:
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            base = {
                "HOME": directory,
                "XDG_STATE_HOME": str(Path(directory) / "state"),
                "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                "PATH": "/usr/bin:/bin",
            }
            with (
                mock.patch.dict(os.environ, {**base, **(env or {})}, clear=True),
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                try:
                    code = cli.main(list(argv))
                except SystemExit as exc:
                    code = int(exc.code or 0)
        return code, err.getvalue()

    def test_a_wide_bind_without_a_token_is_a_configuration_error(self) -> None:
        code, err = self.run_cli("usage", "serve", "--host", "0.0.0.0", "--port", "0")
        self.assertEqual(78, code)
        self.assertIn("PITWALL_AGENT_ROUTING_USAGE_TOKEN", err)

    def test_an_interval_under_thirty_seconds_is_refused(self) -> None:
        code, err = self.run_cli("usage", "serve", "--port", "0", "--interval", "5")
        self.assertEqual(78, code)
        self.assertIn("at least 30 seconds", err)

    def test_a_port_that_cannot_be_bound_exits_one(self) -> None:
        with mock.patch.object(
            cli.usage_serve, "make_server", side_effect=OSError(98, "Address already in use")
        ):
            code, err = self.run_cli("usage", "serve", "--port", "0")
        self.assertEqual(1, code)
        self.assertIn("cannot serve on 127.0.0.1:0", err)

    def test_the_server_is_started_with_the_arguments_and_an_interrupt_exits_zero(self) -> None:
        with mock.patch.object(cli.usage_serve, "run", side_effect=KeyboardInterrupt) as run:
            code, _err = self.run_cli(
                "usage", "serve", "--host", "::1", "--port", "9001", "--interval", "60"
            )
        self.assertEqual(0, code)
        self.assertEqual(("::1", 9001, 60.0), run.call_args.args[:3])
```

- [x] **Step 2: Run them to see them fail**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -m unittest tests.test_usage_serve tests.test_usage_cli 2>&1 | tail -3
```

Expected: `FAILED`, with errors naming `resolve_token` and `UsageState`.

- [x] **Step 3: Extend the module**

In `packages/agent-routing/runtime/model_routing/usage/serve.py`, make three edits.

Edit 1, the imports and first constants. Find:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .rows import Row, iso_utc, parse_instant

RATE_WINDOW = 300.0
```

Replace with:

```python
from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from .rows import Row, iso_utc, parse_instant

TOKEN_ENV = "PITWALL_AGENT_ROUTING_USAGE_TOKEN"
LOOPBACK = ("127.0.0.1", "::1", "localhost")
MIN_INTERVAL = 30.0
RATE_WINDOW = 300.0
```

Edit 2, below the constants. Find:

```python
MEASURED = ("ok", "warn", "limit")


def row_key
```

Replace with:

```python
MEASURED = ("ok", "warn", "limit")
CollectRows = Callable[[datetime], Sequence[Row]]
Clock = Callable[[], datetime]


class ServeConfigError(Exception):
    """The serve mode was asked to start in a way it refuses."""


def row_key
```

Edit 3. Add this to the end of the file:

```python
class UsageState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[Row] = []
        self._sampler = Sampler()

    def update(self, rows: Sequence[Row], now: datetime) -> None:
        with self._lock:
            self._sampler.observe(rows, now.timestamp())
            self._rows = list(rows)

    def legacy(self, now: datetime) -> dict[str, Any]:
        with self._lock:
            return legacy_payload(self._rows, self._sampler, now)

    def plans(self, now: datetime) -> dict[str, Any]:
        with self._lock:
            return plans_payload(self._rows, self._sampler, now)


def refresh_once(state: UsageState, collect_rows: CollectRows, clock: Clock) -> bool:
    """One refresh. A failure keeps the rows already held and reports False."""
    now = clock()
    try:
        rows = collect_rows(now)
    except Exception:  # the loop must outlive any one bad refresh
        return False
    state.update(rows, now)
    return True


def resolve_token(host: str, env: Mapping[str, str]) -> str | None:
    token = env.get(TOKEN_ENV, "").strip()
    if host not in LOOPBACK and not token:
        raise ServeConfigError(f"binding {host} needs a bearer token; set {TOKEN_ENV}")
    return token or None


class _IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def make_server(
    host: str, port: int, token: str | None, state: UsageState, clock: Clock
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                self._send(200, {"ok": True})
            elif path in ("/usage", "/plans"):
                if not self._authorized():
                    self._send(401, {"error": "unauthorized"})
                elif path == "/usage":
                    self._send(200, state.legacy(clock()))
                else:
                    self._send(200, state.plans(clock()))
            else:
                self._send(404, {"error": "not_found"})

        def _authorized(self) -> bool:
            if token is None:
                return True
            offered = self.headers.get("Authorization", "")
            return hmac.compare_digest(offered.encode("utf-8"), f"Bearer {token}".encode("utf-8"))

        def _send(self, status: int, value: Mapping[str, Any]) -> None:
            # Always a whole body with its length: the desk meter stream-parses and cannot read chunks.
            payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_: object) -> None:
            return None

    server_class = _IPv6Server if ":" in host else ThreadingHTTPServer
    return server_class((host, port), Handler)


def run(
    host: str,
    port: int,
    interval: float,
    env: Mapping[str, str],
    collect_rows: CollectRows,
    *,
    clock: Clock = lambda: datetime.now(timezone.utc),
) -> None:
    """Serve until interrupted. Raises ServeConfigError before binding when refused."""
    if interval < MIN_INTERVAL:
        raise ServeConfigError(f"--interval must be at least {MIN_INTERVAL:.0f} seconds")
    token = resolve_token(host, env)
    state = UsageState()
    refresh_once(state, collect_rows, clock)
    server = make_server(host, port, token, state, clock)
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            refresh_once(state, collect_rows, clock)

    worker = threading.Thread(target=loop, name="usage-refresh", daemon=True)
    worker.start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
```

- [x] **Step 4: Add the subcommand**

In `packages/agent-routing/runtime/model_routing/cli.py`, make four edits.

Edit 1, the imports. Find:

```python
from . import model_studio, usage
```

Replace with:

```python
from . import model_studio, usage
from .usage import serve as usage_serve
```

Edit 2, `build_parser`. Find:

```python
    usage_parser.add_argument("--json", action="store_true", dest="json_output")
```

Replace with:

```python
    usage_parser.add_argument("--json", action="store_true", dest="json_output")
    usage_commands = usage_parser.add_subparsers(dest="usage_command", required=False)
    usage_serve_parser = usage_commands.add_parser("serve")
    usage_serve_parser.add_argument("--host", default="127.0.0.1")
    usage_serve_parser.add_argument("--port", type=int, default=8848)
    usage_serve_parser.add_argument("--interval", type=float, default=45.0, metavar="SECONDS")
```

Edit 3, `main`. Find:

```python
    if args.command == "usage":
        return _usage(args.json_output)
```

Replace with:

```python
    if args.command == "usage":
        if args.usage_command == "serve":
            return _usage_serve(args.host, args.port, args.interval)
        return _usage(args.json_output)
```

Edit 4, a new function above `_routes_list`. Find:

```python
def _routes_list(json_output: bool) -> int:
```

Replace with:

```python
def _usage_serve(host: str, port: int, interval: float) -> int:
    def collect_rows(now: datetime) -> list[usage.Row]:
        # Routes are read again on every refresh, so a route added later shows up without a restart.
        registry, config, home = _routes_context()
        return usage.collect(os.environ, registry=registry, routes_config=config, home=home, now=now)

    try:
        usage_serve.run(host, port, interval, os.environ, collect_rows)
    except usage_serve.ServeConfigError as exc:
        print(f"pitwall-agent-routing: {exc}", file=sys.stderr)
        return EX_CONFIG
    except OSError as exc:
        print(f"pitwall-agent-routing: cannot serve on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


def _routes_list(json_output: bool) -> int:
```

- [x] **Step 5: Run the tests**

Run from `packages/agent-routing`:

```bash
uv run --frozen python -W error::ResourceWarning -m unittest tests.test_usage_serve tests.test_usage_cli 2>&1 | tail -3
```

Expected: `Ran 33 tests` and `OK`.

- [x] **Step 6: Run the server**

Run from `packages/agent-routing`:

```bash
T=$(mktemp -d); (HOME=$T XDG_STATE_HOME=$T/state XDG_CONFIG_HOME=$T/config timeout 8 uv run --frozen pitwall-agent-routing usage serve --port 18849 &) ; sleep 3
curl -s -i http://127.0.0.1:18849/usage | tr -d '\r' | grep -iE '^(HTTP|content-length|transfer-encoding)'
curl -s http://127.0.0.1:18849/health; echo; rm -rf $T
```

Expected: `HTTP/1.0 200 OK`, a `Content-Length` line, no `Transfer-Encoding` line, and `{"ok":true}`.

- [x] **Step 7: Document the serve mode**

Add this to the end of `packages/agent-routing/docs/usage.md`:

````markdown
## Serving usage

```bash
pitwall-agent-routing usage serve [--host 127.0.0.1] [--port 8848] [--interval 45]
```

| Path | Auth | Returns |
|---|---|---|
| `GET /usage` | Bearer | The desk meter payload |
| `GET /plans` | Bearer | The rows, with burn rate, time to full, and history per window |
| `GET /health` | None | `{"ok": true}` |

- The bearer token is read from `PITWALL_AGENT_ROUTING_USAGE_TOKEN`. Any address other than
  loopback needs it; without it the command exits 78.
- `--interval` is in seconds and must be at least 30.
- Burn rate is percent-points per minute against a reading 5 to 10 minutes old. A window that
  reset, or a row that is not fresh, has no rate.
- History is one point per 5 minutes, the newest 24, held in memory.
- Every response carries `Content-Length` and is never chunked.

`GET /usage` carries at most 7 rows, leaves out `unknown` rows, and cuts account labels to 3
characters, because that is what the desk meter firmware holds. The firmware is in
`examples/desk-meter/`.

A user service keeps it running:

```ini
[Unit]
Description=Pitwall Agent Routing subscription usage
After=network-online.target

[Service]
EnvironmentFile=%h/.config/subagent-model-routing/usage.env
ExecStart=%h/.local/bin/pitwall-agent-routing usage serve --host 0.0.0.0 --port 8848
Restart=on-failure

[Install]
WantedBy=default.target
```

`usage.env` holds the `PITWALL_AGENT_ROUTING_USAGE_TOKEN` line and any plan keys, with mode 600.
````

In `packages/agent-routing/docs/routes.md`, in the Commands table. Find:

```markdown
reset time, status, and the routes that reach it; see `docs/usage.md` |
```

Replace with:

```markdown
reset time, status, and the routes that reach it; see `docs/usage.md` |
| `pitwall-agent-routing usage serve [--host H] [--port 8848] [--interval 45]` | sample usage on a schedule and answer `GET /usage` (the desk meter payload), `GET /plans`, and `GET /health`; any address other than loopback needs `PITWALL_AGENT_ROUTING_USAGE_TOKEN` |
```

In `packages/agent-routing/CHANGELOG.md`, under `## [Unreleased]`, `### Added`, add:

```markdown
- `pitwall-agent-routing usage serve`: samples usage on a schedule, computes burn rate, time
  to full, and history, and answers the desk meter payload at `GET /usage` with
  `Content-Length` on every response. It binds to loopback by default and requires
  `PITWALL_AGENT_ROUTING_USAGE_TOKEN` on any other address.
```

In `docs/sdlc/23-agent-routing.md`, add this paragraph to the end of the `## Subscription usage` section, with each line number taken from the command in the next step:

```markdown
`pitwall-agent-routing usage serve` samples on a schedule and serves the rows. It refuses an
address other than loopback without a bearer token, and an interval under 30 seconds
(`packages/agent-routing/runtime/model_routing/usage/serve.py`, `resolve_token` and `run`). Every
response carries `Content-Length`, because the desk meter cannot read a chunked body
(`packages/agent-routing/runtime/model_routing/usage/serve.py`, `make_server`). `GET /usage`
keeps the payload the desk meter firmware reads: at most 7 rows, 3-character account tags, and
five statuses (`packages/agent-routing/runtime/model_routing/usage/serve.py`, `legacy_payload`).
```

- [x] **Step 8: Put the line numbers into the SDLC paragraph**

Run from `the repository root`:

```bash
grep -n 'def resolve_token\|^def run\|def make_server\|def legacy_payload' packages/agent-routing/runtime/model_routing/usage/serve.py
```

Expected: four lines. Write each citation in the new paragraph as `path:line` using the number printed.

- [x] **Step 9: Run the document gates**

Run from `the repository root`:

```bash
make docs-check 2>&1 | tail -2
uv run python tools/guards/repo_text_policy.py packages/agent-routing/docs/usage.md packages/agent-routing/docs/routes.md packages/agent-routing/CHANGELOG.md docs/sdlc/23-agent-routing.md; echo "policy exit=$?"
```

Expected: `markdown links passed` and `policy exit=0`.

- [x] **Step 10: Commit**

Run from the repository root:

```bash
git add packages/agent-routing/runtime/model_routing/usage/serve.py packages/agent-routing/runtime/model_routing/cli.py packages/agent-routing/tests/test_usage_serve.py packages/agent-routing/tests/test_usage_cli.py packages/agent-routing/docs/usage.md packages/agent-routing/docs/routes.md packages/agent-routing/CHANGELOG.md docs/sdlc/23-agent-routing.md
git commit -s -m "feat(agent-routing): usage serve"
```

---

### Task 16: Move the firmware to `examples/desk-meter`

**Files:**
- Create: `examples/desk-meter/platformio.ini`, `examples/desk-meter/src/main.cpp`, `examples/desk-meter/src/meter.cpp`, `examples/desk-meter/src/touch_test.cpp`, `examples/desk-meter/src/ledsweep.cpp`, `examples/desk-meter/src/secrets.example.h` (copied from the submeter checkout)
- Create: `examples/desk-meter/README.md`, `examples/desk-meter/.gitignore`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `GET /usage` from `usage serve` (Task 15).
- Produces: the firmware in this repository. The four `.cpp` files and `platformio.ini` are byte-identical to the submeter checkout. Only `secrets.example.h` changes, because it names machines.

`SUBMETER` below is the path of the submeter checkout on the machine doing the work.

- [x] **Step 1: Copy the firmware**

Run from `the repository root`:

```bash
export SUBMETER=<path to the submeter checkout>
mkdir -p examples/desk-meter/src
cp "$SUBMETER/firmware/platformio.ini" examples/desk-meter/
cp "$SUBMETER"/firmware/src/main.cpp "$SUBMETER"/firmware/src/meter.cpp "$SUBMETER"/firmware/src/touch_test.cpp "$SUBMETER"/firmware/src/ledsweep.cpp "$SUBMETER"/firmware/src/secrets.example.h examples/desk-meter/src/
for f in platformio.ini src/main.cpp src/meter.cpp src/touch_test.cpp src/ledsweep.cpp; do cmp "$SUBMETER/firmware/$f" "examples/desk-meter/$f" && echo "same $f"; done
```

Expected: five lines beginning `same`.

- [x] **Step 2: Replace the secrets example**

Replace `examples/desk-meter/src/secrets.example.h` with:

```cpp
// Copy this to src/secrets.h (gitignored) and fill in. secrets.h is NEVER committed.
#pragma once

#define WIFI_SSID        "your-2.4GHz-ssid"   // ESP32 classic = 2.4 GHz only
#define WIFI_PASS        "your-wifi-password"

// Address of the machine running `pitwall-agent-routing usage serve`. Give it a fixed address.
#define AGGREGATOR_URL   "http://192.0.2.10:8848/usage"

// The value of PITWALL_AGENT_ROUTING_USAGE_TOKEN on that machine.
#define AGGREGATOR_BEARER ""
```

- [x] **Step 3: Add the ignore file and the guide**

Create `examples/desk-meter/.gitignore`:

```text
src/secrets.h
.pio/
```

Create `examples/desk-meter/README.md`:

````markdown
# Desk meter

Firmware for a 4-inch ESP32 touch display that shows subscription usage. It polls
`pitwall-agent-routing usage serve` over WiFi and draws one strip per plan.

This is an example. It is not built or tested in CI. Comments in the sources that cite
"HANDOFF" sections refer to the design notes of the project this firmware came from.

## Board (verified)

| Attribute | Value |
|---|---|
| Board family | ESP32-3248S035 ("Cheap Yellow Display"), 4.0" variant |
| MCU | ESP32-WROOM-32E: classic LX6, no PSRAM, 4 MB flash |
| Display | ST7796S 320x480 on HSPI, 40 MHz |
| Touch | XPT2046 resistive, sharing the display HSPI bus (SCLK 14 / MOSI 13 / MISO 12); only CS 33 and IRQ 36 are separate. Calibration `{263, 3547, 293, 3586, 4}` at `setRotation(0)`, set in `src/meter.cpp` and `src/touch_test.cpp` |
| Backlight | GPIO 27 (21 is the 2.8" ILI9341 variant) |
| USB bridge | CH340, which appears as `/dev/ttyUSB0` |
| RGB LED | red GPIO 4 does not work on this model; green 16 and blue 17 are active-low. `LED_ENABLED = false` in `src/meter.cpp` |

Do not add a second SPI bus for touch and do not add a `TOUCH_*` pin block. `platformio.ini`
passes only `-DTOUCH_CS=33` because the display library's touch driver shares the display bus.

## Prerequisites

- A USB-C data cable. A board that powers on but never shows a serial port usually has a
  charge-only cable.
- PlatformIO Core: `uv tool install platformio --with pip`.
- Membership of the `dialout` group for USB serial access.

## Find the port

```bash
pio device list
ls /dev/ttyUSB* /dev/ttyACM*
```

Expect `/dev/ttyUSB0`. The CH340 driver is in the kernel.

## Environments

| Env | Source | Proves |
|---|---|---|
| `display` | `src/main.cpp` | the panel: cycles red, green, blue and prints `ST7796 OK 320x480` |
| `touch` | `src/touch_test.cpp` | calibrated touch: prints coordinates and draws a dot under the stylus |
| `meter` | `src/meter.cpp` | the app: WiFi, poll `/usage` every 45 s, draw the strips |
| `ledsweep` | `src/ledsweep.cpp` | maps RGB LED pins; only needed for a different board model |

```bash
cd examples/desk-meter
pio run -e display -t upload --upload-port /dev/ttyUSB0
pio run -e touch   -t upload --upload-port /dev/ttyUSB0
pio run -e meter   -t upload --upload-port /dev/ttyUSB0
```

Run `display` and `touch` first on a new unit.

## Configure `secrets.h` before flashing `meter`

`src/meter.cpp` includes `secrets.h`, so the `meter` environment does not compile without it. The
file is ignored by git and must never be committed.

```bash
cd examples/desk-meter
cp src/secrets.example.h src/secrets.h
```

| Macro | Value |
|---|---|
| `WIFI_SSID` | a 2.4 GHz network; this ESP32 cannot join 5 GHz |
| `WIFI_PASS` | its password |
| `AGGREGATOR_URL` | `http://<address of the machine running usage serve>:8848/usage` |
| `AGGREGATOR_BEARER` | the value of `PITWALL_AGENT_ROUTING_USAGE_TOKEN` on that machine |

WiFi and the address are compiled in. Changing either means editing `src/secrets.h` and flashing
again. Give the serving machine a fixed address; if its address changes the meter shows STALE.

The serving machine must listen on an address the meter can reach, which needs the token:

```bash
PITWALL_AGENT_ROUTING_USAGE_TOKEN=<token> pitwall-agent-routing usage serve --host 0.0.0.0
```

## First flash of the meter

```bash
pio run -e meter -t upload --upload-port /dev/ttyUSB0
pio device monitor -b 115200
```

If the upload cannot connect, hold **BOOT**, tap **RESET**, release RESET, release BOOT, and run
the upload again. If uploads fail part-way, lower the speed:
`pio run -e meter -t upload -t upload_speed=460800 --upload-port /dev/ttyUSB0`.

## If the screen stays black

| Symptom | Cause | Fix |
|---|---|---|
| Whole screen dark | wrong `TFT_BL` | try 27, then 21 |
| Backlight on, no image | wrong `TFT_DC` or `TFT_CS`, or `USE_HSPI_PORT` missing | check the pins in `platformio.ini` |
| Colours inverted | wrong inversion flag | add `-DTFT_INVERSION_ON` or `-DTFT_INVERSION_OFF` to `build_flags` |
| Noisy image | SPI too fast | lower `-DSPI_FREQUENCY` to `27000000` |
| Nothing uploads | cable or port | cable first, then `--upload-port` |

## What a healthy boot looks like

1. The screen shows `WiFi...` with the network name.
2. Serial prints `WiFi OK <ip>`. On failure the screen shows `WiFi FAILED` in red.
3. The overview draws: an `AI USAGE` header, one strip per plan with 5-hour and 7-day bars, and a
   clock. Two accounts of one plan share a split strip, each half tagged with its account.

Two behaviours are by design:

- The clock is set from the payload's `updated` field on every poll. There is no NTP.
- The device restarts once a day to clear heap fragmentation.

`usage GET <code>` on serial is a poll that did not return 200. `json err: <reason>` is a payload
that did not parse. Both leave the last good data on screen, marked STALE.

## Limits the payload respects

The firmware holds 7 rows, 3-character account tags, and five statuses. `GET /usage` sends no more
than that. See `packages/agent-routing/docs/usage.md`.
````

- [x] **Step 4: Record it in the changelog**

In `CHANGELOG.md`, under `## [Unreleased]`, `### Added`, add:

```markdown
- `examples/desk-meter`: firmware for an ESP32 touch display that shows subscription usage from
  `pitwall-agent-routing usage serve`. It is an example and is not built in CI.
```

- [x] **Step 5: Build the firmware**

Run from `the repository root`:

```bash
cd examples/desk-meter && cp src/secrets.example.h src/secrets.h && pio run -e meter 2>&1 | tail -3; rm -f src/secrets.h
```

Expected: a line containing `SUCCESS`. The build needs PlatformIO (`uv tool install platformio --with pip`) and downloads the two libraries on first run.

- [x] **Step 6: Run the gates**

Run from `the repository root`:

```bash
uv run python tools/guards/repo_text_policy.py $(git ls-files --others --exclude-standard examples/desk-meter) CHANGELOG.md; echo "policy exit=$?"
make docs-check 2>&1 | tail -2
uv run python tools/security/check_secrets.py; echo "secrets exit=$?"
git status --short examples/desk-meter | grep -c secrets.h$ ; git status --short examples/desk-meter | grep -c '\.pio'
```

Expected: `policy exit=0`; `markdown links passed`; `secrets exit=0`; then `0` and `0`: neither `secrets.h` nor `.pio` is offered to git.

- [x] **Step 7: Commit**

Run from the repository root:

```bash
git add examples/desk-meter CHANGELOG.md
git commit -s -m "feat(examples): the desk meter firmware"
```

---

### Task 17: Release acceptance and gates for stage 2

**Files:**
- Modify: `tests/release/test_cli_all_commands_journey.py:233`, `tests/release/cli_fixtures.json`, `scripts/release/run-user-journeys.sh:675`
- Modify: `release_acceptance/denominator.json`, `release_acceptance/reviewed-bindings.json`, `.secrets.baseline`

**Interfaces:**
- Consumes: `usage serve` (Task 15).
- Produces: release pins that match the code: 463 CLI argument surfaces, 1290 in total.

- [x] **Step 1: Count the surfaces**

Run from `the repository root`:

```bash
uv run python -c "from pathlib import Path; from tools.release_acceptance import cli_arguments; print(len(cli_arguments.discover_with_issues(Path('.'))['surfaces']))"
```

Expected: `463`.

- [x] **Step 2: Pin the count**

In `tests/release/test_cli_all_commands_journey.py`. Find:

```python
    assert len(surfaces) == 458
```

Replace with:

```python
    assert len(surfaces) == 463
```

In `scripts/release/run-user-journeys.sh`. Find:

```bash
check ${id} "all CLI command paths and 458 argument surfaces through the entry points" \\
```

Replace with:

```bash
check ${id} "all CLI command paths and 463 argument surfaces through the entry points" \\
```

- [x] **Step 3: Add the fixture**

In `tests/release/cli_fixtures.json`, add this entry directly after `pitwall-agent-routing|usage`:

```json
  "pitwall-agent-routing|usage serve": {"argv": ["usage", "serve", "--host", "127.0.0.1", "--port", "0"], "database": false, "daemon": true},
```

- [x] **Step 4: Update the denominator**

In `release_acceptance/denominator.json`, set `"cli_arguments:cli"` to `463`, set `"total"` to `1290`, and replace `"reason"` with:

```json
  "reason": "2026-09-28: `pitwall-agent-routing usage serve` adds a subparser group, the subcommand, and three arguments: five cli_argument surfaces bound through the CLI journey; previously 1285 after `usage` and `routes add --account` added three",
```

- [x] **Step 5: Regenerate the bindings and the secret baseline**

Run from `the repository root`:

```bash
uv run --frozen python -m tools.release_acceptance.bind_surfaces 2>&1 | tail -5
make sec-baseline 2>&1 | tail -3
uv run python tools/security/check_secrets.py; echo "secrets exit=$?"
```

Expected: `bind_surfaces` names no unmapped surface; `secret scan passed` and `secrets exit=0`.

- [x] **Step 6: Run the release-acceptance tests and the CLI journey**

Run from `the repository root`:

```bash
uv run pytest tests/release_acceptance -q 2>&1 | tail -3
make up
eval "$(grep -E '^export (DATABASE_URL|REDIS_URL)=' README.md)"
PITWALL_JOURNEY_HARNESS=1 RUNPOD_API_KEY=local-dry-run-key PITWALL_ADMIN_SECRET=journey-admin-secret uv run --frozen pytest -q -m release tests/release/test_cli_all_commands_journey.py -p no:randomly 2>&1 | tail -5
```

Expected: both end with `passed` and no `failed`. The `eval` line takes the test database and Redis addresses from the README quick start.

- [x] **Step 7: Run the full gates**

Run from `the repository root`:

```bash
(cd packages/agent-routing && uv run --frozen ruff check runtime tests tools scripts/pitwall-agent-routing && uv run --frozen mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing && uv run --frozen python tools/validate_json_schemas.py && uv run --frozen python tools/validate_plugins.py && uv run --frozen python tools/validate_registry.py && uv run --frozen python tools/check_generated.py && uv run --frozen python -m unittest discover -s tests 2>&1 | tail -3)
uv run ruff check src tests && uv run mypy src 2>&1 | tail -2
make test-fast 2>&1 | tail -3
make test-int 2>&1 | tail -3
make docs-check 2>&1 | tail -2
make down
```

Expected: every command exits 0; the Agent Routing suite ends `OK`; the pytest runs end with `passed` and no `failed`.

- [x] **Step 8: Commit**

Run from the repository root:

```bash
git add tests/release/test_cli_all_commands_journey.py tests/release/cli_fixtures.json scripts/release/run-user-journeys.sh release_acceptance .secrets.baseline
git commit -s -m "chore(release): bind the usage serve surfaces"
```

---

### Task 18: Retire submeter

**Files:**
- No file in this repository changes.

**Interfaces:**
- Consumes: a release that contains Tasks 1 to 17, installed on the machine whose logins are read.
- Produces: the desk meter reading from `usage serve`; the old service stopped; the submeter repository archived.

The maintainer runs this task. It flashes a physical device, stops services on another machine, and archives a repository, so none of it is done unattended. Three values are set first:

- `SUBMETER`: the path of the submeter checkout.
- `SUBMETER_HOST`: the ssh alias of the machine that runs the old aggregator.
- `SUBMETER_REPO`: the repository's `owner/name` on the Forgejo instance.

- [ ] **Step 1: Commit the uncommitted work in the submeter checkout**

Run from `any directory`:

```bash
git -C "$SUBMETER" status --short | wc -l
git -C "$SUBMETER" add -A && git -C "$SUBMETER" commit -m "docs: bring the documents up to date; keep the account tag on failed rows"
git -C "$SUBMETER" push origin main
git -C "$SUBMETER" status --short | wc -l
```

Expected: a count above 0, then the commit and push, then `0`.

- [ ] **Step 2: Run the serve mode on the machine whose logins it reads**

Run from `any directory`:

```bash
mkdir -p ~/.config/subagent-model-routing ~/.config/systemd/user
umask 077; printf 'PITWALL_AGENT_ROUTING_USAGE_TOKEN=%s\n' "$(openssl rand -hex 24)" > ~/.config/subagent-model-routing/usage.env
cat > ~/.config/systemd/user/pitwall-usage.service <<'UNIT'
[Unit]
Description=Pitwall Agent Routing subscription usage
After=network-online.target

[Service]
EnvironmentFile=%h/.config/subagent-model-routing/usage.env
ExecStart=%h/.local/bin/pitwall-agent-routing usage serve --host 0.0.0.0 --port 8848
Restart=on-failure

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload && systemctl --user enable --now pitwall-usage.service
systemctl --user is-active pitwall-usage.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8848/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8848/usage
```

Expected: `active`, `200`, and `401`: the service is up and `/usage` refuses a request without the token.

- [ ] **Step 3: Flash the desk meter with the new address and token**

Run from `the repository root`:

```bash
cd examples/desk-meter && cp src/secrets.example.h src/secrets.h
"${EDITOR:-nano}" src/secrets.h   # set WIFI_SSID, WIFI_PASS, AGGREGATOR_URL (this machine's address), AGGREGATOR_BEARER (the token in usage.env)
pio run -e meter -t upload --upload-port /dev/ttyUSB0 2>&1 | tail -3
pio device monitor -b 115200
```

Expected: `SUCCESS`, then `WiFi OK` on serial, and the screen shows one strip per plan with no `STALE` in the header.

- [ ] **Step 4: Stop and remove the old services**

Run from `any directory`:

```bash
ssh "$SUBMETER_HOST" 'sudo systemctl disable --now submeter-aggregator.service && systemctl --user disable --now submeter-claude-keepfresh.timer && sudo rm /etc/systemd/system/submeter-aggregator.service && rm ~/.config/systemd/user/submeter-claude-keepfresh.service ~/.config/systemd/user/submeter-claude-keepfresh.timer ~/bin/submeter-claude-refresh.mjs && sudo systemctl daemon-reload && systemctl --user daemon-reload'
ssh "$SUBMETER_HOST" 'systemctl list-unit-files "submeter*" --no-legend; systemctl --user list-unit-files "submeter*" --no-legend' | wc -l
```

Expected: `0`: no submeter unit remains. The desk meter still shows live rows.

- [ ] **Step 5: Archive the repository**

Run from `any directory`:

```bash
fj repo edit "$SUBMETER_REPO" --archived true
fj repo view "$SUBMETER_REPO" | grep -i archived
```

Expected: a line showing the repository is archived.

---

## Self-Review

| Spec section | Task |
|---|---|
| 4.1 Row, status rules | 2 |
| 4.2 Accounts | 3, 4 |
| 4.3 Readers | 6, 7, 8, 9; plans without a reader in 4 and 10 |
| 4.4 Cache | 5, 10 |
| 4.5 Command | 10 |
| 4.6 Routing skill | 12 |
| 4.7 Host boundary exception | 11, 12 |
| 4.8 Layout | 2 to 10, 14 |
| 5.1 Serve command, 5.3 Endpoints | 15 |
| 5.2 Burn rate and history, 5.4 Desk meter contract | 14 |
| 5.5 Firmware | 16 |
| 5.6 Retiring submeter | 18 |
| 7 Error handling | 4, 6 to 10, 15 |
| 8 Security | 10 (`test_no_reader_asks_for_a_token_refresh_or_writes_a_credential_file`, `test_cached_files_hold_no_credential`), 15 |
| 9 Testing | every task; the live check in 10 |
| 10 Documentation and pinned lists | 3, 12, 13, 15, 16, 17 |
| 12 Branching | 1 |

---

## Continuation: corrections from orchestrated execution

Recorded while the plan was carried out by parallel lanes on 2026-09-28.

### C1. A scaffold commit comes before the lanes

Two files are committed first so lanes in the same wave do not depend on each other:
`packages/agent-routing/runtime/model_routing/usage/__init__.py` (the one-line file from Task 2
Step 3) and `packages/agent-routing/tests/usage_test_support.py` (from Task 6 Step 1). Their
content is the plan's. Tasks 2 and 6 no longer create them.

### C2. Lanes edit files and the orchestrator commits

The routing skill forbids a dispatched model from running `git add` or `git commit`, and an
isolated dispatch captures uncommitted work as a patch. Each task's "Commit" step is run by the
orchestrator after it has applied and verified the lane's patch.

### C3. The `space-bunny` route was out of quota

The `space-bunny` route points at `opencode-go/space-bunny-free`. On 2026-09-28 OpenCode logged
`Go usage limit exceeded` for it and hung with no output until the supervisor's timeout. Lanes
are dispatched with the route spec `opencode/space-bunny-free@opencode`, the same model through
OpenCode's other provider, which answered the pilot in 6 seconds.

### C4. The machine's inotify watch limit was raised

65,422 of 65,536 inotify watches were in use, most by an OpenCode session in another project, and
a new OpenCode run could not watch its worktree. The runtime limit was raised with
`sudo sysctl fs.inotify.max_user_watches=524288`. It is not persisted and is undone with
`sudo sysctl fs.inotify.max_user_watches=65536`.

### C5. The secret baseline is not regenerated with `make sec-baseline`

Tasks 13 and 17 name `make sec-baseline` for the secret scan. That target regenerates the bandit
baseline. The secret scan is `tools/security/check_secrets.py`, and it fails on any fingerprint
that is not in `.secrets.baseline`. The base branch carries one such finding:
`tests/providers/test_model_studio_openapi.py` line 33, the literal test value passed as
`access_key_secret`, exposed when the format commit moved it onto its own line. It is fixed the
way the Model Studio plan prescribes, with `# pragma: allowlist secret`. The same value appears
again on line 48 of that file and gets the same comment. The new tests in this plan already carry
it.

`.secrets.baseline` still changes in Tasks 13 and 17, for a different reason. Each binding in
`release_acceptance/reviewed-bindings.json` records the SHA-256 of its test file, and the scan
reads that as a high-entropy string. Editing `tests/release/test_cli_all_commands_journey.py`
changes its hash, so the baseline is regenerated with the canonical scan in
`tools/security/check_secrets.py`, and the new finding is marked reviewed only after it is shown
to be the hash of that test file.

### C6. Live check result (Task 10 Step 7)

Run on 2026-09-28 with `PITWALL_AGENT_ROUTING_USAGE_LIVE=1`: `Ran 1 test`, `OK`. Every configured
reader answered `ok`: Claude, Codex, GLM, and MiniMax. Codex returned one window, of 604,800
seconds, as its primary window and no secondary window; the reader placed it as the 7-day window
because it chooses by duration. MiniMax reported a total of zero requests, so its percentages are
empty, as Task 8 specifies. No Model Studio row appeared because `routes.json` on this machine
holds no Token Plan endpoint.

### C7. Source citations in the SDLC chapter

The docs lane wrote the six new citations in `docs/sdlc/23-agent-routing.md` without their closing
backtick. The orchestrator repaired them while merging and checked each line number against the
code. Two existing citations in the same chapter were also put right: the `cli.py` range that this
branch's edits moved, and the two `providers/pi.py` citations, which already pointed at a blank
line and a decorator on the base branch and now name `_managed_provider_fields` and
`endpoint_sync_status`. Task 15 moves the `cli.py` range again, and it is corrected again there.

### C8. Task 13 as run

- The Model Studio command prints `saved endpoint j36-ms to <path>`, so the fixture's
  `stdout_contains` is `saved endpoint j36-ms`, not `saved j36-ms`.
- `make up` and `make down` were not run. The test Postgres and Redis containers were already up
  and other worktrees use them; stopping them would break those sessions.
- The integration suite ran only after the CLI journey had finished, because they share one
  database.

### C9. Task 17 as run

The same corrections as C5 and C8 apply: the secret baseline was regenerated with the canonical
scan and its one new finding audited as the hash of the CLI journey test; `make up` and
`make down` were not run; the CLI journey and the integration suite ran one after the other.

### C10. Task 18 is left for the maintainer

Tasks 1 to 17 were carried out by the orchestrator and the lanes. Task 18 flashes the desk meter,
stops services on the old host, and archives the submeter repository, so its steps stay unticked
until the maintainer runs them. The old host has one system unit, `submeter-aggregator.service`,
and one user timer, `submeter-claude-keepfresh.timer`, with its service and script; this was
confirmed with a read-only listing on 2026-09-28.

### C11. Two gate failures in Task 17, both caused by how the gates were run

- The CLI journey failed `test_no_command_writes_into_the_checkout` once. The only difference in
  the checkout was this plan file, which the orchestrator edited while the journey was running.
  Rerun with the checkout left alone: `151 passed`.
- `make test-fast` failed `tests/test_lease_state_transitions.py` once with
  `duplicate key value violates unique constraint "pg_namespace_nspname_index"`. The hermetic
  setup in `tests/_hermetic_env.py` points at the shared test database, and that test rebuilds
  the `pitwall` schema there. It ran while the CLI journey and the integration suite were using
  the same database. Run alone it passes, and the whole suite rerun alone gave `6524 passed`.
  `make test-fast` belongs with the suites that must never run side by side.
- The canonical secret scan writes the baseline's absolute path into its `is_baseline_file`
  filter. The commit for Task 13 carried that path. It was put back to `.secrets.baseline` in the
  commit for Task 17, and the text policy now passes on every file this branch changed.

### C12. Corrections found while merging with the review remediation stack

- The merge added one command surface, `pitwall mcp relay`. The CLI journey fixture for it uses
  the refusal path (`exit` 64, `usage: pitwall mcp relay -- COMMAND`), because the harness adds a
  flag after `--`. The CLI surface count is 472 and the denominator total is 1311.
- `tests.test_workflow_channel_e2e` failed once on a busy machine. An ask can be answered before
  the supervisor sees the child's exit 75, and the run was then recorded as failed. Commit
  `b6fa8f75` pauses in that case. It was too loose: a child that wrote an ask past the per-run
  cap also paused. Commit `4839d034` keeps that case a failure. No test was changed; two tests
  were added in `packages/agent-routing/tests/test_pause_contract.py`.
- The format step in CI, `uv run ruff format --check .`, formats Python code blocks in Markdown
  at the root line length of 100. The code blocks in this plan were laid out for Agent Routing's
  longer lines, so the step failed on this file. The blocks are now formatted. Only the layout
  changed: every changed block parses to the same syntax tree as before. The files under
  `packages/agent-routing` were not touched and remain the source.
- Formatting moved five fixture values onto lines of their own, away from their
  `# pragma: allowlist secret` comments. The comments now sit on those lines.
- The two message fragments in Task 11 Step 3 are labelled `text`. The formatter read the first
  string of each as a docstring and removed its trailing space, which is part of the message.
