# User Journey Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tick each box in this file in the same commit that completes the step.

**Goal:** Every surface Pitwall exposes — 1,326 discovered on 2026-09-23 — is bound to an executed behavioral test or an explicit, reviewed exception, and the journey harness walks every product path a user can take, not only the 27 registry-broker journeys.

**Architecture:** Reuse the release-acceptance machinery merged from `feat/release-acceptance` (`tools/release_acceptance`: `discovery` → reviewed bindings → `test_index` → `matrix` → `evidence`) as the single source of the denominator and the binding ledger. New journeys follow the existing J23–J25 pattern: an in-process `-m release` pytest module under `tests/release/` plus a one-function wrapper in `scripts/release/run-user-journeys.sh`. Surface families too large for hand-written journeys (every MCP tool, REST operation, CLI command, and configuration key) get generated parametrized tests whose parameter list is the discovery report itself, so a new surface without a test fails the build.

**Tech Stack:** Python 3.14.7, pytest (`release`, `integration` markers), Textual Pilot, the `mcp` stdio client, FastAPI `TestClient`, Agent Routing `unittest`, vitest for the Node packages, bash for the harness.

**Spec:** [`docs/evidence/2026-09-22-intent-vs-implementation-review.md`](../../evidence/2026-09-22-intent-vs-implementation-review.md) finding R13, and the release acceptance matrix specification (`docs/superpowers/specs/2026-09-20-release-acceptance-matrix.md`, moved there by remediation Task 16). Runs after Phase 4 of [`2026-09-23-review-remediation.md`](2026-09-23-review-remediation.md).

## Global Constraints

- Hermetic: no provider credentials, no paid calls, no network beyond loopback. Provider behavior comes from `tests/fakes/` and loopback fake servers. The live lanes belong to remediation Phase 5.
- A journey asserts observable effects (state files, database rows, HTTP bodies, process exit, files written), never only an exit code. "Help prints" counts only for the `--help` surface itself.
- No new test framework, service, or runtime dependency. Test-only dependencies already in `[project.optional-dependencies].dev` may be used.
- The denominator is the discovery report. A surface leaves the denominator only through a reviewed entry in `release_acceptance/discovery-review.json` naming why it is not a user surface.
- Commit after every task with `git commit -s` and the session trailers. Work on local `main`.
- Journey IDs continue from J27. The harness's filter usage message lists every in-process journey that needs no database.

## Denominator (discovery on `integration/2026-09-22`, 2026-09-23)

| Domain | Kind | Surfaces |
| --- | --- | ---: |
| cli_arguments | cli | 442 |
| tui | tui | 240 |
| config | config | 233 |
| rest_mcp | rest | 111 |
| rest_mcp | mcp | 79 |
| ops | install / service / ops | 44 / 15 / 14 |
| cli_dispatch | cli | 43 |
| node | plugin / gateway / cli | 20 / 14 / 13 |
| routing | shim / provider / plugin | 14 / 13 / 3 |
| routing_contracts | provider / mcp / plugin | 13 / 9 / 6 |
| **Total** | | **1,326** |

Discovery also reports 82 unresolved issues: `dynamic_default` 14, `unresolved_parser_binding` 13, `dynamic_compose_expression` 9, `deferred-scope` 6, `dynamic-event-registration` 4, `scope` 4, `dynamic_command_name` 3, and one each of `aliased_constructors`, `compose_include`, `config_files`, `confirmation`, `confirmation-policy-declaration-only`, plus the remainder listed in the report.

## Review Focus

- A new REST route, MCP tool, CLI command, or configuration key added without a test must fail the build, not silently widen the gap (Task 1 and the generated families pin it).
- A personal `serve` interrupted after the pod exists must leave no running fake pod and a `failed` state record (Task 4 step 1 pins it).
- A destructive console action reached by keyboard must not run without the typed confirmation its tier declares (Task 3 pins it).
- An MCP tool that spends must be exercised in dry-run and refuse a real spend without budget, over the real stdio transport (Task 7 pins it).
- A configuration key given an invalid value must fail with a named error rather than fall back to a default (Task 10 pins it).

---

### Task 1: Freeze the denominator

**Files:**
- Create: `tests/release_acceptance/test_denominator.py`
- Create: `release_acceptance/denominator.json`

- [x] **Step 1: Write the test**

```python
"""The discovered surface set is pinned; any change is a reviewed update."""

from __future__ import annotations

import collections
import json
from pathlib import Path

from tools.release_acceptance import discovery

ROOT = Path(__file__).resolve().parents[2]
PIN = ROOT / "release_acceptance" / "denominator.json"


def _counts() -> dict[str, int]:
    report = discovery.build_report(ROOT)
    counts = collections.Counter(
        f"{row['origin']['domain']}:{row['kind']}" for row in report["surfaces"]
    )
    return dict(sorted(counts.items()))


def test_surface_counts_match_the_reviewed_pin() -> None:
    pinned = json.loads(PIN.read_text(encoding="utf-8"))
    assert _counts() == pinned["counts"], (
        "surfaces changed: bind the new surfaces to tests (this plan's families) and "
        "update release_acceptance/denominator.json with a reason"
    )
```

`discovery.build_report(root)` (`tools/release_acceptance/discovery.py:586`) is the function `main()` uses to build the report.

- [x] **Step 2: Write the pin**

Run: `uv run --frozen python -c "import json,collections; from pathlib import Path; from tools.release_acceptance import discovery as d; r=d.build_report(Path('.')); c=collections.Counter(f\"{s['origin']['domain']}:{s['kind']}\" for s in r['surfaces']); Path('release_acceptance/denominator.json').write_text(json.dumps({'reason':'baseline 2026-09-23','counts':dict(sorted(c.items()))},indent=2)+'\n')"`

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest -q tests/release_acceptance/test_denominator.py`
Expected: passed.

```bash
git add tests/release_acceptance/test_denominator.py release_acceptance/denominator.json
git commit -s -m "test(release-acceptance): pin the discovered surface denominator"
```

### Task 2: Resolve the 82 discovery issues

Each issue is resolved in source (make the surface statically discoverable) or in `release_acceptance/discovery-review.json` (a reviewed statement of what the dynamic construct produces). Never both silently.

**Files:**
- Modify: the source files named by each issue's `source` field
- Modify: `release_acceptance/discovery-review.json`
- Test: `tests/release_acceptance/test_discovery.py`

- [x] **Step 1: List them**

Run: `uv run --frozen python -m tools.release_acceptance.discovery --root . --output /tmp/disc.json && uv run --frozen python -c "import json; [print(i.get('code') or i.get('topic'), i.get('source'), (i.get('reason') or i.get('message',''))[:120]) for i in json.load(open('/tmp/disc.json'))['issues']]" > /tmp/disc-issues.txt; wc -l /tmp/disc-issues.txt`
Expected: `82`.

- [x] **Step 2: Resolve by code**

- `unresolved_parser_binding` (13), `dynamic_command_name` (3), `aliased_constructors` (1): rewrite the parser construction at each `source` line with literal, same-scope `add_parser("name")` / `add_argument("--flag")` calls, as remediation already did for `mcp install`. Run `uv run --frozen pytest -q tests/cli` after each file.
- `dynamic_default` (14): for each, add a review entry stating the default's source expression and its value domain (for example `os.environ.get("PITWALL_MCP_TRANSPORT", "stdio")` → `{"stdio"}`), and add a CLI test that runs the command with and without the environment variable and asserts the effective value in `--json` output.
- `dynamic_compose_expression` (9), `compose_include` (1): add review entries giving the resolved value from `docker compose -f <file> config` for the canonical environment, and extend `tests/test_compose_budget_env.py` (or the compose test that owns that file) to assert the rendered value.
- `dynamic-event-registration` (4): add review entries naming the event and handler, and add a test that fires each event and asserts the handler's effect.
- `deferred-scope` (6), `scope` (4), `config_files` (1), `confirmation` (1): these are the areas this plan covers with Tasks 3–11; each gets a review entry pointing at the task and test node that covers it.
- `confirmation-policy-declaration-only` (1): closed by Task 3.
- Every remaining code in `/tmp/disc-issues.txt` gets a review entry or a source fix by the same rule.

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen python -m tools.release_acceptance.discovery --root . --output /tmp/disc.json && uv run --frozen python -c "import json; d=json.load(open('/tmp/disc.json')); print(d['status'], len(d['issues']))" && uv run --frozen pytest -q tests/release_acceptance tests/cli`
Expected: `complete 0`; passed. Update `release_acceptance/denominator.json` if resolution added surfaces, with `reason` naming this task.

```bash
git add -A src tests release_acceptance
git commit -s -m "fix(release-acceptance): resolve every discovery issue in source or by reviewed record"
```

### Task 3: Console confirmation tiers are enforced

Discovery found `src/pitwall/tui/confirmation.py::_ACTION_TIERS` has no caller: `confirm_tier_for_action` and `ConfirmTier` are never used, so the declared tiers do not govern any action. The support matrix and `docs/sdlc/18-cli.md` state that every console mutation sits behind a preview and an exact typed confirmation.

**Files:**
- Modify: `src/pitwall/tui/confirmation.py`, the screens that own mutating actions (`src/pitwall/tui/models.py`, `personal.py`, `providers.py`, `resources.py`, `operations.py`)
- Test: `tests/tui/test_confirmation_tiers.py` (create)

- [x] **Step 1: Write the failing test**

```python
"""Every mutating console action goes through the tier its policy declares."""

from __future__ import annotations

import pytest

from pitwall.tui import confirmation
from pitwall.tui.app import PitwallApp  # use the console App class name in src/pitwall/tui/app.py


@pytest.mark.parametrize("action", sorted(confirmation._ACTION_TIERS))
async def test_action_requires_its_declared_confirmation(action: str) -> None:
    tier = confirmation.confirm_tier_for_action(action)
    app = PitwallApp(sources=confirmation.test_sources_for(action))
    async with app.run_test() as pilot:
        await confirmation.trigger_for_test(pilot, action)
        assert confirmation.last_executed(app) is None, f"{action} ran before confirmation"
        await confirmation.confirm_for_test(pilot, tier, wrong=True)
        assert confirmation.last_executed(app) is None, f"{action} accepted a wrong confirmation"
        await confirmation.confirm_for_test(pilot, tier, wrong=False)
        assert confirmation.last_executed(app) == action
```

Implement `test_sources_for`, `trigger_for_test`, `confirm_for_test`, and `last_executed` as test helpers in `tests/tui/_confirmation_helpers.py` (not in `src`), using each screen's existing injectable source protocol and a recording fake; import them from there instead of from `confirmation`.

- [x] **Step 2: Run to verify it fails**

Run: `uv run --frozen pytest -q tests/tui/test_confirmation_tiers.py`
Expected: FAIL for actions whose screen does not consult the tier.

- [x] **Step 3: Route every mutating action through `confirm_tier_for_action`**

In each screen's action handler, replace the local confirmation choice with `tier = confirm_tier_for_action("<action name>")` and push the modal that tier selects (exact type-to-confirm for `destructive`/`spend`, yes/no for lower tiers). Keep the no-business-logic guard (`tests/tui/test_no_business_logic_guard.py`) green: the tier lookup is presentation policy, the mutation still goes through the source protocol.

- [x] **Step 4: Verify and commit**

Run: `uv run --frozen pytest -q tests/tui`
Expected: passed.

```bash
git add src/pitwall/tui tests/tui
git commit -s -m "fix(tui): every mutating console action uses its declared confirmation tier"
```

### Task 4: Personal serving journeys (J28–J31)

Shared fakes move out of `tests/personal/test_service.py` into `tests/fakes/personal.py` (`FakeRunPod`, `FakeRoutes`, `FakeClock`, `FakeCatalogue`, `plan_result`) so journeys and unit tests share them. The journeys drive the real CLI verbs through `pitwall.cli_personal`, replacing only `_service_or_exit` with a factory that builds `PersonalServeService` over the fakes and a temporary `XDG_STATE_HOME`.

**Files:**
- Create: `tests/fakes/personal.py`
- Modify: `tests/personal/test_service.py` (import the fakes from `tests/fakes/personal.py`)
- Create: `tests/release/test_personal_journeys.py`
- Modify: `scripts/release/run-user-journeys.sh` (`j28`–`j31`, filter list, `main`)
- Modify: `docs/operator/user-journey-catalog.md`

- [x] **Step 1: Write the journeys**

```python
"""Hermetic personal-serving journeys J28-J31: no database, fake RunPod, real CLI verbs."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from pitwall import cli_personal
from pitwall.personal.state import StateStore
from tests.fakes.personal import FakeRunPod, FakeRoutes, build_service

pytestmark = [pytest.mark.release]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "journey-placeholder")
    return tmp_path


@pytest.fixture
def fakes(home: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeRunPod, FakeRoutes]:
    runpod, routes = FakeRunPod(), FakeRoutes()
    monkeypatch.setattr(
        cli_personal,
        "_service_or_exit",
        lambda: build_service(home / "state" / "pitwall", runpod, routes),
    )
    return runpod, routes


def test_j28_first_run_setup_is_idempotent_and_owner_only(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_personal.cmd_setup(["--yes"]) == 0
    key = home / "state" / "pitwall" / "endpoint.key"
    first = key.read_text()
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert cli_personal.cmd_setup(["--yes"]) == 0
    assert key.read_text() == first
    assert "personal (local state file)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["--max-usd-per-hour", "0.01"], "price_over_cap"),
        (["--gpu-class", "NVIDIA GeForce RTX 3060"], "does_not_fit"),
        (["--route", "taken"], "route_exists"),
    ],
)
def test_j29_serve_refusals_create_no_pod(
    fakes: tuple[FakeRunPod, FakeRoutes],
    args: list[str],
    code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runpod, routes = fakes
    routes.existing.add("taken")
    base = [
        "--model",
        "ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        "--gpu-class",
        "NVIDIA GeForce RTX 3090",
        "--ttl-minutes",
        "15",
        "--max-usd-per-hour",
        "1.00",
        "--route",
        "ornith",
        "--json",
    ]
    argv = base + args
    assert cli_personal.cmd_serve(argv) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == code
    assert runpod.created == []


def test_j30_serve_status_stop_lifecycle(
    home: Path, fakes: tuple[FakeRunPod, FakeRoutes], capsys: pytest.CaptureFixture[str]
) -> None:
    runpod, routes = fakes
    argv = [
        "--model",
        "ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        "--gpu-class",
        "NVIDIA GeForce RTX 3090",
        "--ttl-minutes",
        "15",
        "--max-usd-per-hour",
        "1.00",
        "--route",
        "ornith",
        "--json",
    ]
    assert cli_personal.cmd_serve(argv) == 0
    served = json.loads(capsys.readouterr().out)
    assert served["route"] == "ornith" and served["state"] == "ready"
    [created] = runpod.created
    assert "pods/$RUNPOD_POD_ID/action" in created["docker_start_cmd"][0]
    assert os.environ.get("RUNPOD_API_KEY") not in json.dumps(served)
    assert routes.attached == ["ornith"]

    assert cli_personal.cmd_status(["--json"]) == 0
    [row] = json.loads(capsys.readouterr().out)["leases"]
    assert row["route"] == "ornith" and row["state"] == "ready"

    assert cli_personal.cmd_stop(["ornith", "--json"]) == 0
    assert runpod.terminated == [created["pod_id"]]
    assert routes.removed == ["ornith"]
    [record] = StateStore(home / "state" / "pitwall").read()
    assert record.state == "stopped"


def test_j31_status_reconciles_gone_and_overdue_pods(
    home: Path, fakes: tuple[FakeRunPod, FakeRoutes], capsys: pytest.CaptureFixture[str]
) -> None:
    runpod, routes = fakes
    for route in ("gone", "late"):
        assert (
            cli_personal.cmd_serve(
                [
                    "--model",
                    "ornith-ai/Ornith-1.5-35B-A3B-GGUF",
                    "--gpu-class",
                    "NVIDIA GeForce RTX 3090",
                    "--ttl-minutes",
                    "15",
                    "--max-usd-per-hour",
                    "1.00",
                    "--route",
                    route,
                    "--json",
                ]
            )
            == 0
        )
    capsys.readouterr()
    runpod.vanish(route="gone")
    runpod.clock.advance(minutes=16)
    assert cli_personal.cmd_status(["--json"]) == 0
    states = {
        r["route"]: (r["state"], r.get("failure"))
        for r in json.loads(capsys.readouterr().out)["leases"]
    }
    assert states["gone"] == ("gone", None)
    assert states["late"] == ("stopped", "terminated_late")
```

`build_service(state_dir, runpod, routes)` constructs `PersonalServeService` exactly as `tests/personal/test_service.py::service_factory` does today (fake catalogue with the Ornith dossier, a price snapshot pricing RTX 3090 at $0.44/h and RTX 3060 as not fitting, a fake clock shared with `runpod.clock`). Give `FakeRunPod` the `vanish(route=...)` and `clock` members and `FakeRoutes` the `existing`, `attached`, and `removed` members if they do not already exist. Match the JSON keys to what `cmd_serve`/`cmd_status` actually print (read `src/pitwall/cli_personal.py`); the assertions must check the state, not only presence.

- [x] **Step 2: Wire the harness**

Add to `scripts/release/run-user-journeys.sh`, beside `j27`:

```bash
# ------------------------------------------------- J28-J31 personal serving (no database)
j28_31() {
    local id=J28-J31 ok=1
    check ${id} "personal setup, refusals, lifecycle, and reconciliation" \
        env DATABASE_URL='' REDIS_URL='' uv run --frozen pytest -q -m release \
        tests/release/test_personal_journeys.py -p no:randomly || ok=0
    [ ${ok} -eq 1 ] && journey_pass "${id} personal serving"
}
```

Add `J28-J31` to the in-process filter list at the top (usage line and both `!=` guards) and call `j28_31` from the main sequence. Add four rows to `docs/operator/user-journey-catalog.md` (persona: Personal user).

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest -q -m release tests/release/test_personal_journeys.py -p no:randomly && bash scripts/release/run-user-journeys.sh J28-J31`
Expected: passed; harness `1 passed, 0 failed`.

```bash
git add tests/fakes/personal.py tests/personal/test_service.py tests/release/test_personal_journeys.py scripts/release/run-user-journeys.sh docs/operator/user-journey-catalog.md
git commit -s -m "test(journeys): J28-J31 personal setup, refusals, lifecycle, and reconciliation"
```

### Task 5: Free-tier gateway journey (J32)

Real Node gateway (`packages/gateway/dist/shim.js`, built by the journey), a route table with two routes pointing at a loopback fake OpenAI upstream, the broker's `openai_gateway` adapter, and the production router.

**Files:**
- Create: `tests/release/test_gateway_journey.py`
- Modify: `scripts/release/run-user-journeys.sh` (`j32`), `docs/operator/user-journey-catalog.md`

- [x] **Step 1: Write the journey**

The test (a) runs `npm ci --silent && npm run -s build` in `packages/gateway` once per session (skip-free: fail if Node 22 is missing, since the gateway is a shipped component); (b) starts a `http.server`-based fake upstream on an ephemeral loopback port that records requests and returns an OpenAI chat body with `usage`, and returns 429 with `retry-after: 30` for model `m-quota`; (c) writes a route table with `gw-a` → model `m-a` (keyless) and `gw-q` → model `m-quota`; (d) starts the gateway through `pitwall.personal.gateway.GatewaySupervisor` with `PITWALL_GATEWAY_ROUTES` and a random `PITWALL_GATEWAY_TOKEN`, waiting on `/health`; (e) calls `GatewayProvider().infer(...)` for a provider record named `gw-a` and asserts the fake upstream saw model `m-a` and no `Authorization`; (f) calls for `gw-q` and asserts `QuotaExhausted` with `reset_at` about 30 s ahead; (g) stops the supervisor and asserts the process is gone and the state file cleared.

- [x] **Step 2: Harness wrapper `j32`, catalogue row (persona: Personal user), verify with `bash scripts/release/run-user-journeys.sh J32`, commit.**

Expected: `1 passed, 0 failed`.

```bash
git commit -s -m "test(journeys): J32 supervised gateway routes providers to their own upstreams"
```

### Task 6: Claude-native `/v1/messages` journey (J33)

**Files:**
- Create: `tests/release/test_messages_journey.py`; modify the harness and catalogue.

- [x] **Step 1: Write the journey**

With the API app from `tests.conftest._import_app` and a seeded registry (the J23 seeding helpers), a `coding.chat` capability whose provider is an `openai_gateway` record pointing at a respx-mocked loopback URL: (a) non-streaming `POST /v1/messages` with a system prompt and one user turn returns an Anthropic `message` with `content[0].type == "text"` and `usage.input_tokens`; (b) the same request with `stream: true` yields `message_start`, at least one `content_block_delta`, and `message_stop` events in order; (c) a request carrying `tools` and an upstream `tool_calls` response yields a `tool_use` block, and a follow-up user turn with a `tool_result` block reaches the upstream as an OpenAI `tool` message with the same id; (d) a `gw/` model id is rewritten to the provider's `gateway.model_id` in both stream and non-stream (remediation R6 regression).

- [x] **Step 2: Wrapper `j33`, catalogue row (persona: Agent client), verify, commit.**

### Task 7: Every MCP tool over real stdio (J34)

Generated from `TOOL_REGISTRY` (79 tools). Each tool gets a valid-input case and an invalid-input case through the real `pitwall.mcp` stdio server.

**Files:**
- Create: `tests/release/mcp_tool_fixtures.json` (one valid argument object and one invalid argument object per tool name, plus the expected success marker path, e.g. `"status"` for `pitwall_health`)
- Create: `tests/release/test_mcp_all_tools_journey.py`
- Modify: harness and catalogue

- [x] **Step 1: Write the generated test**

```python
"""J34: every registered MCP tool, over stdio, with valid and invalid input."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from pitwall.mcp.registry import TOOL_REGISTRY

pytestmark = [pytest.mark.release, pytest.mark.integration]

FIXTURES = json.loads((Path(__file__).parent / "mcp_tool_fixtures.json").read_text())


def test_every_tool_has_fixtures() -> None:
    assert sorted(FIXTURES) == sorted(spec.name for spec in TOOL_REGISTRY)


@pytest.mark.parametrize("name", sorted(spec.name for spec in TOOL_REGISTRY))
async def test_tool_over_stdio(name: str, journey_env: dict[str, str]) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "pitwall.mcp"], env=journey_env
    )
    fixture = FIXTURES[name]
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        ok = await session.call_tool(name, fixture["valid"])
        assert not ok.isError, (name, ok.content)
        payload = ok.structuredContent or json.loads(ok.content[0].text)
        for key in fixture["expect_keys"]:
            assert key in payload, (name, key)
        bad = await session.call_tool(name, fixture["invalid"])
        assert bad.isError
        assert json.dumps(fixture["invalid"]) not in str(bad.content)
```

`journey_env` is a fixture in `tests/release/conftest.py` returning the environment of a seeded disposable database (reuse the J07/J10 seeding: `pitwall db migrate`, `pitwall init --non-interactive`, admin secret, placeholder RunPod key). Tools that spend use `dry_run: true` in `valid`; RunPod resource tools use their preview path; tools that need a live provider return their documented unavailable code, and `expect_keys` names that code's field — the fixture file records which.

- [x] **Step 2: Author the fixture file**

For each of the 79 names, derive `valid` from the tool's input schema (`session.list_tools()` prints them) and the matching REST request in `docs/sdlc/03-mcp-server.md`; derive `invalid` by violating one required field's type. `expect_keys` are the top-level keys the tool's docstring promises.

- [x] **Step 3: Wrapper `j34` (database journey), catalogue row, verify, commit.**

Run: `DATABASE_URL=... REDIS_URL=... bash scripts/release/run-user-journeys.sh` (J34 runs in the database section).
Expected: 80 cases passed (79 tools plus the fixture-coverage case).

### Task 8: Every REST operation with a valid, authorized fixture (J35)

Generated from the live OpenAPI document. Fuzzing (Schemathesis) already proves "no unexpected 5xx"; this proves each operation's success path and its authorization boundary.

**Files:**
- Create: `tests/release/rest_operation_fixtures.json` (per `METHOD path`: path params, query, body, required scope, expected status, expected body keys, optional `setup` step name)
- Create: `tests/release/test_rest_all_operations_journey.py`

- [x] **Step 1: Write the generated test**

```python
"""J35: every OpenAPI operation succeeds with a valid fixture and enforces its scope."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.release, pytest.mark.integration]

FIXTURES = json.loads((Path(__file__).parent / "rest_operation_fixtures.json").read_text())


def _operations(app) -> list[str]:
    spec = app.openapi()
    return sorted(
        f"{method.upper()} {path}"
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    )


def test_every_operation_has_a_fixture(journey_app) -> None:
    assert sorted(FIXTURES) == _operations(journey_app)


@pytest.mark.parametrize("operation", sorted(FIXTURES))
def test_operation(operation: str, journey_client, scoped_token) -> None:
    method, path = operation.split(" ", 1)
    fx = FIXTURES[operation]
    url = path.format(**fx.get("path_params", {}))
    if fx.get("setup"):
        journey_client.setup(fx["setup"])
    headers = {"Authorization": f"Bearer {scoped_token(fx['scope'])}"}
    if fx.get("admin"):
        headers["X-Pitwall-Secret"] = journey_client.admin_secret
    ok = journey_client.request(
        method, url, params=fx.get("query"), json=fx.get("body"), headers=headers
    )
    assert ok.status_code == fx["status"], (operation, ok.text[:300])
    body = ok.json() if ok.headers.get("content-type", "").startswith("application/json") else {}
    for key in fx.get("expect_keys", []):
        assert key in body, (operation, key)
    if fx["scope"] != "none":
        denied = journey_client.request(
            method,
            url,
            params=fx.get("query"),
            json=fx.get("body"),
            headers={
                "Authorization": f"Bearer {scoped_token('read' if fx['scope'] != 'read' else 'spend')}"
            },
        )
        assert denied.status_code in {401, 403}, operation
```

`journey_app`, `journey_client` (a `TestClient` over the app with a seeded database, exposing `setup(name)` for named preconditions like `lease_exists` and `admin_secret`), and `scoped_token(scope)` (tokens configured through `PITWALL_API_SCOPED_TOKENS`) go in `tests/release/conftest.py`. Health routes use `"scope": "none"`.

- [x] **Step 2: Author the fixture file** for all operations (102 before remediation Task 13's baseline refresh; use the regenerated count), using `docs/sdlc/02-api-rest.md` for bodies and scopes. Operations that call a provider use their `dry_run` or preview form, or a respx-mocked provider set up by a named `setup` step.

- [x] **Step 3: Wrapper `j35`, catalogue row, verify, commit.**

### Task 9: Every CLI command and argument (J36)

Generated from discovery's `cli_dispatch` (43 commands) and `cli_arguments` (442 surfaces) rows for `pitwall` and `pitwall-agent-routing`.

**Files:**
- Create: `tests/release/cli_fixtures.json` (per command path: a representative valid argv, the expected exit code, the JSON keys for `--json` commands, and whether it needs the database)
- Create: `tests/release/test_cli_all_commands_journey.py`

- [x] **Step 1: Write the generated test**

For every discovered command path: `--help` exits 0 and names every argument surface discovery attributes to that parser (this is where the 442 argument rows are bound: each argument's flag must appear in its parser's help and be accepted by a parse-only call); the representative argv runs in a subprocess (`uv run --frozen pitwall …` / `.venv/bin/pitwall-agent-routing …`) with a temporary `HOME`, exits as the fixture says, and for `--json` commands parses stdout and asserts the fixture's keys; an unknown flag exits 2 with usage on stderr. Destructive commands run with their refusal path (no `--force`/`--confirm`) and assert the refusal message and no state change.

- [x] **Step 2: Author `cli_fixtures.json`** from `docs/sdlc/18-cli.md` and `packages/agent-routing/README.md`, one entry per discovered command path; `test_every_command_has_a_fixture` compares against discovery.

- [x] **Step 3: Wrapper `j36`, catalogue row, verify, commit.**

### Task 10: Every configuration key (J37)

Generated from discovery's `config` rows (233).

**Files:**
- Create: `tests/release/config_fixtures.json` (per key: a valid value, an invalid value, and the effect probe: a settings attribute name and expected parsed value)
- Create: `tests/release/test_config_keys_journey.py`

- [x] **Step 1: Write the generated test**

For each key: set the valid value in the environment and assert `pitwall.config.get_settings()` (cache cleared) exposes the parsed value at the fixture's attribute; set the invalid value and assert startup validation fails with an error naming the key (`pitwall config check --json` exits non-zero and its error names the key); for keys that also load from `pitwall.toml`, assert the TOML value applies and the environment overrides it. Keys discovery marks as compose-only are asserted through `docker compose config` output instead.

- [x] **Step 2: Author the fixture file** from `.env.example` and `docs/sdlc/16-core-config.md`; `test_every_key_has_a_fixture` compares against discovery.

- [x] **Step 3: Wrapper `j37`, catalogue row, verify, commit.**

### Task 11: All ten console views (J38)

Discovery lists 240 TUI surfaces (views, bindings, actions, columns). The existing J09 proves boot only.

**Files:**
- Create: `tests/release/test_console_views_journey.py`
- Create: `tests/release/tui_fixtures.py` (injectable source fakes producing empty, loaded, and failing states per view)

- [x] **Step 1: Write the journey**

For each of the ten views (Overview, Providers, Leases, Models, Serve, Pods, Routes, Cost, Resources, Operations) and each of three source states (empty, loaded, failing): mount the App with the fake sources under Pilot at 100×30 and 160×45, switch to the view by its key binding, and assert the view's table/row text for loaded, the empty-state text for empty, and the named error class for failing (the Resources and Providers failure contracts from the 2026-09-02 fixes). Then for every binding discovery attributes to the view, press it and assert its observable effect (focus, filter, palette, help overlay, or a confirmation modal, which Task 3 covers to completion). Bind each discovered TUI surface to the parametrized case id that exercises it.

- [x] **Step 2: Wrapper `j38` (in-process, no database), catalogue row, verify, commit.**

### Task 12: Agent Routing journeys (J39–J41)

Discovery lists 206 Agent Routing surfaces (151 CLI, 26 provider, 14 shim, 9 MCP, 6 plugin).

**Files:**
- Create: `packages/agent-routing/tests/test_journeys.py`
- Modify: harness (`j39`–`j41` run the component's `unittest` modules), catalogue

- [x] **Step 1: J39 routes and doctor.** In a temporary `HOME`: `routes add` an endpoint route to a loopback fake OpenAI server, `routes list --json` shows it, `routes probe` succeeds, `doctor --json` reports the route healthy, `routes remove` deletes it and `routes list` no longer shows it; a second `routes add` with the same name fails with the documented error.
- [x] **Step 2: J40 shims.** For each of the 14 shims in `scripts/`, put a fake harness binary on `PATH` that records its argv and prints a known reply; run the shim with a prompt file; assert the last line is `SHIM-DONE exit=0`, the recorded argv matches the adapter's documented shape (model flag, prompt path, effort flag where supported), and a `SHIM-RESULT` receipt parses with `scripts/parse-shim-result.py`. Then make the fake exit 3 and assert `SHIM-DONE exit=3`.
- [x] **Step 3: J41 channel and workflows.** Wrap the existing scripted exits (`test_channel_tier1_integration`, `test_channel_steer_integration`, `test_workflow_channel_e2e`) and add: a workflow with a Pi task validates and runs against a fake `pi` binary; a workflow naming a non-workflow provider (e.g. `muse`) fails validation with "does not support workflow execution"; `inbox`, `answer`, `steer`, and `runs stop` each change the mailbox as documented.
- [x] **Step 4: Verify and commit.**

Run: `cd packages/agent-routing && HOME=$(mktemp -d) .venv/bin/python -m unittest tests.test_journeys -v 2>&1 | tail -3`
Expected: `OK`.

### Task 13: Operations journeys (J42–J44)

- [x] **Step 1: J42 backup and restore that actually runs.** `tests/integration/test_backup_restore_drill.py` skips locally when the host `pg_dump` major version differs from the server's. Make the drill run `pg_dump`/`pg_restore` inside the test Postgres container (`docker compose -f docker-compose.testinfra.yml exec -T postgres pg_dump …`) when the host client does not match, so the drill never skips on a machine that has the test stack. Assert per-table row counts and a checksum of `pitwall.workloads` survive restore into a fresh database.
- [x] **Step 2: J43 upgrade.** Create a database at the migration set of the last release tag (`git show v0.1.0a2:db/migrations`), seed it through that version's schema, then run `pitwall db migrate` from the working tree and assert every later migration applies once, `db status` reports none pending, and seeded rows are intact.
- [x] **Step 3: J44 installed artifacts.** Build the wheel and sdist (`uv build`), install each into a clean virtual environment outside the checkout, and assert: `pitwall --help`, `pitwall-api --help`, `pitwall-mcp` handshake, `pitwall models list --json` (packaged dossiers), `pitwall gateway status --json` reading the packaged catalog and route table, and `pitwall db migrate` against the test database using packaged migrations.
- [x] **Step 4: Wrappers, catalogue rows, verify, commit.**

### Task 14: Bind every surface and gate on the matrix

**Files:**
- Modify: `release_acceptance/reviewed-bindings*.json`
- Modify: `scripts/release/run-user-journeys.sh` (final `matrix` step)
- Create: `tests/release_acceptance/test_matrix_complete.py`

- [x] **Step 1: Bind**

For every discovered surface, add a binding to the matching `reviewed-bindings-*.json` file naming the exact test node id from Tasks 3–13 (or an earlier existing test that asserts that surface's oracle), with `review_state: "bound"`. Generated families bind by parametrize id (for example `tests/release/test_mcp_all_tools_journey.py::test_tool_over_stdio[pitwall_health]`).

- [x] **Step 2: Gate**

```python
def test_every_surface_has_a_bound_executed_node(tmp_path: Path) -> None:
    out = tmp_path / "matrix"
    matrix.assemble(
        ROOT,
        output_dir=out,
        collection_report_path=Path(os.environ["PITWALL_JOURNEY_COLLECTION"]),
    )
    gaps = json.loads((out / "gap-ledger.json").read_text())
    assert gaps["entries"] == [], gaps["entries"][:20]
    rows = [json.loads(line) for line in (out / "acceptance.v1.jsonl").read_text().splitlines()]
    assert rows and all(row["status"] in {"pass", "approved_exception"} for row in rows)
```

`PITWALL_JOURNEY_COLLECTION` points at the collection receipt the harness writes after running every journey with `--junitxml=$OUT/journeys.xml` (`tools/release_acceptance/pytest_collection.py` produces it; `result_adapters.match_vitest_junit` and `match_unittest_report` feed the Node and Agent Routing results). Match the gap-ledger and row field names to the files `assemble` writes (see `release_acceptance/MATRIX.md`).

- [x] **Step 3: Harness final step**

After all journeys, the harness runs the matrix assembly into `${JOURNEY_OUT:-/tmp/pitwall-journeys}/matrix` and fails when the gate fails, printing the counts: surfaces, bound, passed, exceptions.

- [x] **Step 4: Verify and commit**

Run: `DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test REDIS_URL=redis://127.0.0.1:6380/0 bash scripts/release/run-user-journeys.sh | tail -5`
Expected: every journey `PASS`, and `matrix: 1326+ surfaces, 0 unbound, 0 failing`.

```bash
git add release_acceptance scripts/release/run-user-journeys.sh tests/release_acceptance/test_matrix_complete.py
git commit -s -m "test(journeys): every discovered surface is bound to an executed test and the harness gates on it"
```

### Task 15: Documentation

- [x] **Step 1:** Rewrite `docs/operator/user-journey-catalog.md` around personas (Evaluator, Operator, Personal user, API client, Agent client, Contributor, Agent Routing user) with every journey J01–J44, its oracle, and its harness filter.
- [x] **Step 2:** In `docs/sdlc/17-testing-strategy.md`, add the journey and matrix lane: how the denominator is discovered, how a new surface is bound, and the command that proves coverage.
- [x] **Step 3:** `make docs-check`, commit.
