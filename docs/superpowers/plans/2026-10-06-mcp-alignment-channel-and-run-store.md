# MCP 2026-07-28 Alignment, Channel Coverage, and Run-Store Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a current MCP implementation and truthful run records in the public release. Both Pitwall MCP servers serve protocol `2026-07-28` alongside the legacy handshake. Every installed harness with an MCP client can be a managed child on the orchestrator channel. Codex and Copilot parents are taught the managed path. The fourteen run-store defects are fixed.

**Architecture:**
- **Broker.** Moves from MCP Python SDK 1.28 (`FastMCP`) to SDK 2.3 (`MCPServer`), whose runner serves both protocol eras; `server/discover`, `resultType`, cache hints, and `subscriptions/listen` come from the SDK. Pitwall keeps one error boundary (`safe_boundary`) and one tool-metadata table (`tool_metadata.py`). Tasks 2-7.
- **Channel protocol.** The standard-library channel server gains a modern code path beside its handshake path, plus annotations, argument validation, rate limiting, and a two-era doctor probe. Tasks 8-9.
- **Relay and CLI.** The narrow fixes their findings name. Tasks 10-11.
- **Channel coverage.** Registration gains Grok's TOML block, Antigravity's and Muse's JSON maps, and a stdlib YAML entry module for Hermes and goose; `CHANNEL_HARNESSES` grows from 8 to 13. The Codex and Copilot routing skills adopt the managed path. Tasks 12-15.
- **Run store.** One terminal-state set and one pid helper module, a recorded supervisor pid so dead standalone runs are reconciled, and one targeted fix per remaining defect. Tasks 16-30.
- **Release.** One documentation, changelog, and full CI-parity task (31) and one live-proof and workstation-cleanup task (32).

**Tech Stack:** Python 3.14.7, `uv`, `mcp>=2.3.0,<3` (with `mcp-types==2.3.0`), pydantic 2, pytest with anyio, the standard-library JSON-RPC loop for the channel, `unittest` for the agents tests.

**Specs** (executors read both):
- [`docs/evidence/2026-10-06-mcp-2026-07-28-gap-analysis.md`](../../evidence/2026-10-06-mcp-2026-07-28-gap-analysis.md): findings **F01-F18**. Protocol source: <https://modelcontextprotocol.io/specification/2026-07-28/>. SDK 2 facts were read from the published `mcp-2.3.0` and `mcp-types-2.3.0` wheels.
- [`docs/superpowers/specs/2026-10-06-channel-coverage-findings.md`](../specs/2026-10-06-channel-coverage-findings.md): Part 1 channel coverage, whose recommendations are labelled **C1-C5** below, and Part 2 run-store findings 1-14, labelled **R1-R14**.

## Global Constraints

- **Branch.** Everything lands on `feat/zcode-harness` (it already holds the ZCode commit `836c4fba`) as one integration branch and one pull request. Never commit to `main`.
- **Python and dependencies.** Python 3.14 only. Run Python through `uv run` or `.venv/bin/python`, never bare `python`. Install with `uv sync --frozen --extra dev`. One `pyproject.toml`, one `uv.lock`. The MCP pin becomes exactly `"mcp>=2.3.0,<3"`.
- **Import weight.** `pitwall.agents` (the channel server, `mcp_server.py`, `mcp_tools.py`, and the new `yaml_channel.py` and `pids.py`) stays standard-library only: no `mcp`, `pydantic`, `fastapi`, `uvicorn`, `asyncpg`, `redis`, `arq`, `textual`, `runpod`, `prometheus_client`, or PyYAML (`tests/test_startup_imports.py`, `tests/agents/test_dispatch_import_weight.py`).
- **Import direction.** `pitwall.agents` never imports `pitwall.mcp`, `pitwall.db`, broker services, or repositories (`tests/test_agents_import_direction.py`).
- **Non-reflection.** Broker error payloads never include request values or exception text; names from Pitwall's own schemas are allowed. The channel never echoes exception text.
- **Transports.** Both servers stay stdio only.
- **Protocol versions.** Channel modern `("2026-07-28",)`; channel legacy `("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")`, preference order unchanged; broker versions from `mcp_types.version`.
- **Error codes.** Pitwall-defined numeric codes move to `-31000`…`-31010`. Standard codes stay `-32700`, `-32600`, `-32601`, `-32602`, `-32603`; protocol codes `-32021` and `-32022`.
- **Channel role rule.** `server_role` keeps classifying an unexpanded `${VAR}` dispatch id as `misconfigured` (`tests/agents/test_mcp_server.py::RoleTests`).
- **Ask support.** Stays opt-in for direct shim callers (`dispatch.py` `RoutingOptions.ask_support = False`); downstream batch callers parse shim output (C3).
- **Run records.** `result.json` keeps its exact field set (`src/pitwall/agents/result.py:validate_result`). New run facts go in `run.json`, a separate artifact, events, or the ledger row. The only new environment variable is `PITWALL_AGENTS_MANAGED_LAUNCH`, set by `managed_channel.start_dispatch` and dropped from the harness environment by `dispatch.py`.
- **Style and policy.** Except clauses use the repo's unparenthesized style (`except OSError, ValueError:`). Every broad `except` and every `type: ignore` carries `# reason:` (`tools/guards/python_policy.py`).
- **Tests.** Hermetic: no live credentials, paid providers, or user routing state. Tests and policy gates are never weakened to pass. The database suites (`make test-int`, the journey runner, `tests/release/test_mcp_all_tools_journey.py`) share `pitwall_test:5444`; run them one after another, never in parallel.
- **Workstation data.** The run store, launch logs, prompts, worktrees, and the Docker stack change only in Task 32, step by step, after the operator approves each step.
- **Commits.** Every commit is signed off (`git commit -s`) and ends with the session's `Co-Authored-By` and `Claude-Session` trailers.

## Review Focus

1. **A legacy client still works after the SDK and channel upgrades.** A harness on `2025-06-18` must still get tools from both servers. Tests: `test_legacy_initialize_negotiates_handshake_version` (Task 2) and `test_channel_legacy_handshake_unchanged` (Task 8).
2. **A modern request reaches the broker through the relay with no `initialize` ever sent**, and gets `resultType`. Test: `test_relay_forwards_modern_request_without_initialize` (Task 10).
3. **A long but legitimate burst of tool calls is served, and a runaway loop is limited.** The release journey sends 162 calls in one session and must pass; a burst beyond the bucket gets `rate_limited`. Tests: `tests/release/test_mcp_all_tools_journey.py` and `test_burst_beyond_bucket_is_rate_limited` (Task 4), `test_channel_rate_limits_calls` (Task 9).
4. **A tool raises an `MCPError` carrying a Pitwall code.** SDK 2 passes `MCPError` through unchanged, so the client must still see only an `isError` result with the string code. Test: `test_mcp_error_from_tool_becomes_tool_result` (Task 4).
5. **A live standalone run that prints nothing for a long time** must never be recorded as abandoned. Test: `test_live_recent_paused_and_managed_runs_are_never_abandoned` (Task 18).

## Task groups and parallel execution

Two tasks can run at the same time only when they edit no file in common and neither needs the
other's result. The groups below come from comparing every task's **Files** list and commit paths.
Tasks inside a group share files, so they run one after another; no file is shared across groups.

| Group | Tasks, in order | Why they are serial | Must wait for |
|---|---|---|---|
| G1 Broker on SDK 2 | 2 → 3 → 4 → 5 → 6 → 7 | `src/pitwall/mcp/registry.py`, `safe_boundary.py`, `tool_metadata.py`, `__init__.py`, and `docs/sdlc/03-mcp-server.md` | Task 1 |
| G2 Run-store core | 16 → 18 → 19 → 22 → 23 → 20 → 25 → 27, then 26 → 28 | `src/pitwall/agents/dispatch.py` and `managed_channel.py` (plus `run_store.py`, `channel.py`, `cli.py`, `migrate.py`); 18, 19, and 23 need Task 16's shared states and pid helpers, 19 needs 18 | Task 1; Task 26 also waits for Task 21 (`process.py`) |
| G3 Channel protocol and CLI | 8 → 9, then 11 | 8 and 9 share `src/pitwall/agents/mcp_server.py`; 11 (`src/pitwall/cli/mcp.py`) is independent and rides in this lane to stay within five lanes | Task 1 |
| G4 Channel coverage | 12 → 13 → 14 → 15 | 12-14 share `mcp_registration.py`, `capability_inventory.py`, and their test; 15 (the Codex and Copilot skills) is independent and rides here | Task 1 |
| G5 Independent fixes | 17 → 21 → 24 → 29 | no shared files; grouped into one lane only to respect the five-lane limit | Task 1 |
| G6 After the first merge | 10, 30 | 10's modern relay test needs the SDK 2 broker (Task 2); 30 regenerates the registry and skill files that Tasks 24 and 15 edit | Wave 1 merged |
| Release | 31 → 32 | the whole branch | everything |

### Waves

| Wave | Lanes running at once | Tasks |
|---|---|---|
| 0 | none (on `feat/zcode-harness`) | 1 |
| 1 | 5 | G1: 2-7 · G2: 16, 18, 19, 22, 23, 20, 25, 27 · G3: 8, 9, 11 · G4: 12-15 · G5: 17, 21, 24, 29 |
| merge 1 | none | merge the five lanes into `feat/zcode-harness`, `uv sync --frozen --extra dev` (the branch now has SDK 2), then `make test-fast 2>&1 \| tail -5` must report no failures before wave 2 starts |
| 2 | 3 | G2 continued: 26, 28 · G6: 10 · G6: 30 |
| merge 2 | none | merge the three lanes, `make test-fast 2>&1 \| tail -5` with no failures |
| 3 | none (on `feat/zcode-harness`) | 31, then 32 |

The longest chain is G2 (ten tasks across waves 1 and 2); G1 is shorter but holds the heaviest task (2,
the SDK migration). The plan never needs more than five lanes at once.

### Lane rules

- Each lane works in its own worktree branched from `feat/zcode-harness` after Task 1, starts with `uv sync --frozen --extra dev`, and follows `~/.claude/templates/lane-prompt.md`: its tasks' **Files** lists are its exclusive ownership, and every other lane's files are its do-not-touch list. A lane runs `lane-checkpoint <lane>` before any step expected to take more than ten minutes.
- Lanes in wave 1 run on SDK 1.28 except G1; only G1's tasks import the broker SDK, so the others are unaffected until merge 1.
- A lane may run a test file another lane edits (for example G2 runs `tests/agents/test_shim_contract.py`, which G5's Task 24 edits); it never edits it. Merge 1 and Task 31 run the whole suite against the merged code.
- Database suites (`make test-int`, the journey runner, `tests/release/test_mcp_all_tools_journey.py`) run in one lane at a time: G1's Task 4 runs the MCP journey in wave 1, and no other wave-1 lane runs a database suite.
- Merges rebase each lane onto `feat/zcode-harness` and fast-forward, so every commit keeps its DCO sign-off and the history stays linear. The lanes touch disjoint files, so the rebases do not conflict.

---

### Task 1: Open the integration branch with the specs, this plan, and the pending SDLC fix

**Files:**
- Commit: `docs/sdlc/03-mcp-server.md` (the pending budget-tools and tool-count correction), `docs/evidence/2026-10-06-mcp-2026-07-28-gap-analysis.md`, `docs/superpowers/specs/2026-10-06-channel-coverage-findings.md`, and this plan

- [ ] **Step 1: Confirm the branch and the pending doc fix's counts**

Run: `git branch --show-current && uv run pytest tests/mcp/test_doc_count_sync.py tests/mcp/test_tool_count.py -q 2>&1 | tail -3 && uv run python tools/ci/check_markdown_links.py 2>&1 | tail -1`
Expected: `feat/zcode-harness`, all passed, then `markdown links passed`.

- [ ] **Step 2: Commit**

```bash
git add docs/sdlc/03-mcp-server.md docs/evidence/2026-10-06-mcp-2026-07-28-gap-analysis.md docs/superpowers/specs/2026-10-06-channel-coverage-findings.md docs/superpowers/plans/2026-10-06-mcp-alignment-channel-and-run-store.md
git commit -s -m "docs: MCP 2026-07-28 gap analysis, channel and run-store findings, merged plan; budget tools in the MCP SDLC doc"
```

---

### Task 2: Move the broker to MCP SDK 2 and serve both protocol eras (F01)

**Files:**
- Modify: `pyproject.toml:31`, `uv.lock` (regenerated)
- Modify: `src/pitwall/mcp/__init__.py` (whole file)
- Modify: `src/pitwall/mcp/registry.py` (`register_all`, module docstring)
- Modify: `src/pitwall/mcp/safe_boundary.py` (imports, `_stable_error_payload`, `_error_result`, `_invalid_arguments_result`, `install_safe_call_boundary`)
- Modify: `src/pitwall/mcp/error_adapter.py` (imports, `adapt_error`)
- Modify: `src/pitwall/mcp/tools/onboarding.py`, `src/pitwall/mcp/tools/provider_operations.py`, `src/pitwall/mcp/tools/routing.py` (`McpError(ErrorData(...))` construction sites)
- Modify: `tools/release_acceptance/inventory.py:306` (`_mcp_schema_metadata`)
- Modify tests that import SDK 1 names:
  - `tests/cost/test_budget_not_configured.py`
  - `tests/mcp/conftest.py`
  - `tests/mcp/test_budget_tools.py`
  - `tests/mcp/test_production_routing_tools.py`
  - `tests/mcp/test_provider_operations_tools.py`
  - `tests/mcp/test_registry_health.py`
  - `tests/mcp/test_registry.py`
  - `tests/mcp/test_resource_response_acceptance.py`
  - `tests/mcp/test_resource_stdio_acceptance.py`
  - `tests/mcp/test_runpod_create_pod_lease.py`
  - `tests/mcp/test_runpod_resource_gating.py`
  - `tests/mcp/test_runpod_resources.py`
  - `tests/mcp/test_safe_boundary_codes.py`
  - `tests/mcp/test_stdio_transport.py`
  - `tests/mcp/test_tool_contract.py`
  - `tests/mcp/test_volume_file_tools.py`
  - `tests/onboarding/test_surfaces.py`
  - `tests/release/test_mcp_all_tools_journey.py`
- Create: `tests/mcp/raw_stdio.py`, `tests/mcp/test_protocol_eras.py`

**Interfaces:**
- Produces:
  - `pitwall.mcp.mcp: mcp.server.mcpserver.MCPServer`
  - `pitwall.mcp.registry.register_all(server: MCPServer) -> None`
  - `pitwall.mcp.safe_boundary.install_safe_call_boundary(server: MCPServer) -> None`, which registers `tools/call` on `server._lowlevel_server`
  - `tests.mcp.raw_stdio.RawStdio`, with:
    - `RawStdio.start(env: dict[str, str] | None = None) -> RawStdio`
    - `.request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]`
    - `.notify(method: str, params: dict[str, Any] | None = None) -> None`
    - `.close() -> None`
    - `MODERN_META: dict[str, Any]`

- [ ] **Step 1: Write the raw stdio helper.** Era tests must control the exact wire bytes, so they don't go through an SDK client.

```python
# tests/mcp/raw_stdio.py
"""Line-level stdio client for MCP era tests: exact JSON-RPC bytes, no SDK client."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
BROKER_ENV = {
    "PITWALL_MCP_TRANSPORT": "stdio",
    "RUNPOD_API_KEY": "test-key",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "REDIS_URL": "redis://localhost:6379/0",
}
MODERN_META: dict[str, Any] = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "pitwall-tests", "version": "0"},
}


class RawStdio:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self._next_id = 0

    @classmethod
    def start(cls, env: dict[str, str] | None = None) -> RawStdio:
        process = subprocess.Popen(
            [sys.executable, "-m", "pitwall.mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**BROKER_ENV, **(env or {}), "PATH": "/usr/bin:/bin"},
            cwd=ROOT,
        )
        return cls(process)

    def _write(self, message: dict[str, Any]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write((json.dumps(message) + "\n").encode())
        self._process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)
        assert self._process.stdout is not None
        while True:
            line = self._process.stdout.readline()
            assert line, "server closed stdout before answering"
            reply: dict[str, Any] = json.loads(line)
            if reply.get("id") == request_id:
                return reply

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=HANG_GUARD_SECS)
        finally:
            if self._process.poll() is None:
                self._process.kill()
                self._process.wait()
            if self._process.stdout is not None:
                self._process.stdout.close()
```

- [ ] **Step 2: Write the failing era tests**

```python
# tests/mcp/test_protocol_eras.py
"""The broker serves both MCP eras: 2026-07-28 stateless and the legacy handshake (F01)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.mcp.raw_stdio import MODERN_META, RawStdio


@pytest.fixture()
def broker() -> Iterator[RawStdio]:
    client = RawStdio.start()
    try:
        yield client
    finally:
        client.close()


def test_server_discover_advertises_modern_version(broker: RawStdio) -> None:
    reply = broker.request("server/discover", {"_meta": MODERN_META})
    result = reply["result"]
    assert result["resultType"] == "complete"
    assert "2026-07-28" in result["supportedVersions"]
    assert "tools" in result["capabilities"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "pitwall"
    assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
    assert result["cacheScope"] in {"public", "private"}


def test_modern_tools_list_without_initialize(broker: RawStdio) -> None:
    reply = broker.request("tools/list", {"_meta": MODERN_META})
    result = reply["result"]
    assert result["resultType"] == "complete"
    assert any(tool["name"] == "pitwall_health" for tool in result["tools"])
    assert result["ttlMs"] >= 0 and result["cacheScope"] in {"public", "private"}


def test_modern_unsupported_version_is_rejected(broker: RawStdio) -> None:
    meta = {**MODERN_META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
    reply = broker.request("tools/list", {"_meta": meta})
    assert reply["error"]["code"] == -32022
    assert "2026-07-28" in reply["error"]["data"]["supported"]
    assert reply["error"]["data"]["requested"] == "1900-01-01"


def test_modern_missing_client_capabilities_is_invalid_params(broker: RawStdio) -> None:
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
    reply = broker.request("tools/list", {"_meta": meta})
    assert reply["error"]["code"] == -32602


def test_legacy_initialize_negotiates_handshake_version(broker: RawStdio) -> None:
    reply = broker.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "legacy", "version": "0"},
        },
    )
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    broker.notify("notifications/initialized")
    listed = broker.request("tools/list")
    assert any(tool["name"] == "pitwall_health" for tool in listed["result"]["tools"])
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/mcp/test_protocol_eras.py -q -p no:randomly 2>&1 | tail -15`
Expected: FAIL. `server/discover` returns `-32601`, and the modern `tools/list` tests fail on a missing `resultType` or on a legacy rejection.

- [ ] **Step 4: Change the pin and relock**

In `pyproject.toml`, replace `"mcp>=1.28.1,<2",` with `"mcp>=2.3.0,<3",`. Then run:

```bash
uv lock --upgrade-package mcp
uv sync --frozen --extra dev
.venv/bin/python -c "import importlib.metadata as m; print(m.version('mcp'), m.version('mcp-types'))"
```

Expected: `2.3.0 2.3.0`. If `uv lock` reports a resolver conflict on `starlette`, the conflict is with `fastapi`. Resolve it with `uv lock --upgrade-package fastapi --upgrade-package starlette`. SDK 2 needs `starlette>=0.48.0` on Python 3.14, and the current lock has `starlette 1.3.1`, which satisfies it.

- [ ] **Step 5: Port the server bootstrap**

```python
# src/pitwall/mcp/__init__.py
"""Pitwall MCP server — local stdio entrypoint on the MCP Python SDK 2 ``MCPServer``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mcp.server.mcpserver import MCPServer

from pitwall.config import require_runtime_env
from pitwall.mcp.registry import register_all
from pitwall.mcp.tools.runpod_market import close_runpod_market_service
from pitwall.security.redaction import configure_logging_redaction

configure_logging_redaction()


def _distribution_version() -> str:
    try:
        return version("pitwall")
    except PackageNotFoundError:  # reason: a source checkout without metadata reports unknown
        return "0+unknown"


@asynccontextmanager
async def _mcp_lifespan(_server: MCPServer[Any]) -> AsyncIterator[dict[str, object]]:
    try:
        yield {}
    finally:
        await close_runpod_market_service()


mcp: MCPServer[Any] = MCPServer(
    "pitwall",
    version=_distribution_version(),
    lifespan=_mcp_lifespan,
)


def ensure_runtime_env() -> None:
    """Validate required runtime env for the MCP service.

    Called from the serve entry points only, never at import, so test
    collection and ``import pitwall.mcp`` stay hermetic (no SystemExit at
    import time).
    """
    require_runtime_env("mcp")


register_all(mcp)


__all__ = ["ensure_runtime_env", "mcp"]
```

- [ ] **Step 6: Port registration.** In `src/pitwall/mcp/registry.py`, change the module docstring's usage block to `from mcp.server.mcpserver import MCPServer` / `mcp = MCPServer("pitwall")`. Replace `register_all` with:

```python
def register_all(server: Any) -> None:
    """Register every tool in ``TOOL_REGISTRY`` with an SDK 2 ``MCPServer``."""
    from pitwall.mcp.safe_boundary import install_safe_call_boundary

    for spec in TOOL_REGISTRY:
        server.add_tool(spec.handler, name=spec.name, description=spec.description)
    install_safe_call_boundary(server)
```

- [ ] **Step 7: Port the error adapter.** In `src/pitwall/mcp/error_adapter.py`:
  - Replace `from mcp.shared.exceptions import McpError` and `from mcp.types import ErrorData` with `from mcp.shared.exceptions import MCPError`.
  - Change `adapt_error`'s return annotation to `MCPError`.
  - Change its construction to `return MCPError(code=mcp_code, message=message, data=data)`, keeping the existing `mcp_code`, `message`, and `data` computation.
  - In the docstring, replace every `McpError` with `MCPError`.

  Apply the same construction change at each `McpError(ErrorData(code=..., message=..., data=...))` site in `src/pitwall/mcp/tools/onboarding.py`, `provider_operations.py`, and `routing.py`. List them with:

```bash
grep -n "McpError\|ErrorData" src/pitwall/mcp/tools/onboarding.py src/pitwall/mcp/tools/provider_operations.py src/pitwall/mcp/tools/routing.py
```

  Each becomes `MCPError(code=<same>, message=<same>, data=<same>)`, imported from `mcp.shared.exceptions`.

- [ ] **Step 8: Port the safe boundary.** SDK 2 still turns tool exceptions into `ToolError` / `UnexpectedToolError` (with `__cause__` set to the original). But its own handler returns `str(exc)` as text, which would echo exception text. The boundary therefore replaces the `tools/call` handler and calls `MCPServer.call_tool` directly. Replace the imports and `install_safe_call_boundary` in `src/pitwall/mcp/safe_boundary.py`. `_budget_detail`, `BUDGET_REMEDY`, and the constants stay as they are.

```python
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolRequestParams, CallToolResult, TextContent


def _stable_error_payload(exc: Exception) -> dict[str, Any]:
    """Return only a stable error code, never exception or request text."""
    cause = exc.__cause__ if isinstance(exc, ToolError) else exc
    if isinstance(cause, MCPError) and isinstance(cause.error.data, dict):
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
    if (
        isinstance(exc, ToolError)
        and cause is not None
        and cause.__class__.__module__.startswith(("pydantic", "pydantic_core"))
    ):
        return {"error": "invalid_tool_arguments"}
    return {"error": "tool_execution_failed"}


def _payload_result(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, sort_keys=True))],
        structured_content=payload,
        is_error=True,
    )


def _error_result(exc: Exception) -> CallToolResult:
    return _payload_result(_stable_error_payload(exc))


def _invalid_arguments_result() -> CallToolResult:
    return _payload_result({"error": "invalid_tool_arguments"})


def install_safe_call_boundary(server: Any) -> None:
    """Replace the SDK 2 ``tools/call`` handler with one that never reflects request text."""
    if getattr(server, _BOUNDARY_MARKER, False):
        return

    declared: dict[str, frozenset[str]] = {}
    for tool in server._tool_manager.list_tools():
        tool.parameters["additionalProperties"] = False
        declared[tool.name] = frozenset(tool.parameters.get("properties", {}))

    async def safe_call_tool(ctx: Any, params: CallToolRequestParams) -> Any:
        arguments = params.arguments or {}
        allowed = declared.get(params.name)
        if allowed is not None and not set(arguments) <= allowed:
            return _invalid_arguments_result()
        context = Context(
            request_context=ctx,
            mcp_server=server,
            input_params=params,
            subscriptions=server._subscriptions,
        )
        try:
            return await server.call_tool(params.name, arguments, context)
        except Exception as exc:  # reason: MCP transport must fail closed without reflection.
            return _error_result(exc)

    server._lowlevel_server.add_request_handler("tools/call", CallToolRequestParams, safe_call_tool)
    setattr(server, _BOUNDARY_MARKER, True)
```

  Remove the now-unused imports `Awaitable`, `Callable`, `Sequence`, `cast`, and `ContentBlock` if ruff reports them.

- [ ] **Step 9: Port the release inventory.** In `tools/release_acceptance/inventory.py`, `_mcp_schema_metadata`, replace `from mcp.server.fastmcp.tools import Tool` with `from mcp.server.mcpserver.tools import Tool`. `Tool.from_function(handler).parameters` keeps its name in SDK 2 (`mcp/server/mcpserver/tools/base.py:42`). In the docstring, replace "FastMCP" with "MCPServer".

- [ ] **Step 10: Port the tests.** Apply these mechanical renames in every test file listed under **Files**:

| SDK 1 | SDK 2 |
|---|---|
| `from mcp.server.fastmcp import FastMCP` | `from mcp.server.mcpserver import MCPServer` (then `FastMCP(` becomes `MCPServer(`) |
| `from mcp.server.fastmcp.exceptions import ToolError` | `from mcp.server.mcpserver.exceptions import ToolError` |
| `from mcp.shared.exceptions import McpError` | `from mcp.shared.exceptions import MCPError` (then `McpError` becomes `MCPError`) |
| `result.isError` / `result.structuredContent` / `result.serverInfo` / `result.protocolVersion` | `result.is_error` / `result.structured_content` / `result.server_info` / `result.protocol_version` |
| `server._mcp_server` | `server._lowlevel_server` |
| calling the SDK 1 `tools/call` entry in `mcp._mcp_server.request_handlers` with a `CallToolRequest` | `await safe_call_tool_for(mcp, name, args)` (helper below), which calls `mcp._lowlevel_server.get_request_handler("tools/call").handler(None, params)` |

  Add this helper to `tests/mcp/conftest.py` for tests that invoke the boundary in process:

```python
async def safe_call_tool_for(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Invoke the installed tools/call handler the way the SDK runner does, without a transport."""
    from mcp.types import CallToolRequestParams

    entry = server._lowlevel_server.get_request_handler("tools/call")
    assert entry is not None
    return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))
```

  `Context(request_context=None, ...)` is valid for tools that never touch `ctx.request_context`. No Pitwall tool takes a `Context` parameter: `grep -rn "Context" src/pitwall/mcp/tools` returns nothing.

  Then find any remaining SDK 1 spelling:

```bash
grep -rnE "fastmcp|McpError|\.isError|\.structuredContent|\.serverInfo|\.protocolVersion|_mcp_server" src tests tools | grep -v "^tests/mcp/raw_stdio.py"
```

  Expected: no output.

- [ ] **Step 11: Run the era tests and the MCP suites**

Run: `uv run pytest tests/mcp tests/onboarding/test_surfaces.py tests/cost/test_budget_not_configured.py -q -n auto 2>&1 | tail -15`
Expected: all pass, including the 5 tests in `test_protocol_eras.py`.

Run: `uv run pytest tests/test_startup_imports.py tests/agents/test_dispatch_import_weight.py tests/test_agents_import_direction.py -q 2>&1 | tail -5`
Expected: all pass.

Run: `uv run mypy --strict src/ 2>&1 | tail -3 && uv run ruff check . && uv run ruff format --check .`
Expected: `Success: no issues found`, then two clean runs.

- [ ] **Step 12: Commit**

```bash
git add pyproject.toml uv.lock src/pitwall/mcp tools/release_acceptance/inventory.py tests/mcp tests/onboarding/test_surfaces.py tests/cost/test_budget_not_configured.py tests/release/test_mcp_all_tools_journey.py
git commit -s -m "feat(mcp): broker on MCP SDK 2, serving 2026-07-28 and the legacy handshake"
```

---

### Task 3: Move Pitwall error codes out of the JSON-RPC reserved range and correct the docs (F11)

**Files:**
- Modify: `src/pitwall/mcp/error_codes.py` (constants, module docstring, comments)
- Modify: `src/pitwall/mcp/error_adapter.py` (`PITWALL_ERROR_CODE_BASE`, module docstring)
- Modify: `tests/mcp/test_error_codes.py`, plus every test asserting a numeric `-3200x` Pitwall code. Find them with `grep -rnE "\-3200[0-5]" tests` (today: `tests/mcp/test_runpod_resource_gating.py:254,400`, `tests/mcp/test_volume_file_tools.py:282`, `tests/mcp/test_runpod_create_pod_lease.py:184,653`).
- Modify: `docs/sdlc/03-mcp-server.md`: the `pitwall.mcp.error_adapter` and `pitwall.mcp.error_codes` component sections, §3.4's "refusal surfaces as `-32002`" sentences, and §6 "Error adapter".

**Interfaces:**
- Produces: `error_codes.AUTHZ = -31001`, `BUDGET = -31002`, `VALIDATION = -31003`, `UPSTREAM = -31004`, `CONFLICT = -31005`, `error_adapter.PITWALL_ERROR_CODE_BASE = -31000`.

- [ ] **Step 1: Write the failing test.** Append to `tests/mcp/test_error_codes.py`:

```python
def test_pitwall_codes_sit_outside_the_jsonrpc_reserved_range() -> None:
    from pitwall.mcp import error_codes
    from pitwall.mcp.error_adapter import PITWALL_ERROR_CODE_BASE

    codes = {
        PITWALL_ERROR_CODE_BASE,
        error_codes.AUTHZ,
        error_codes.BUDGET,
        error_codes.VALIDATION,
        error_codes.UPSTREAM,
        error_codes.CONFLICT,
    }
    assert codes == {-31000, -31001, -31002, -31003, -31004, -31005}
    assert all(not (-32768 <= code <= -32000) for code in codes)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/mcp/test_error_codes.py -q 2>&1 | tail -5`
Expected: FAIL on the set comparison.

- [ ] **Step 3: Implement.**
  - In `error_codes.py`, set `AUTHZ = -31001`, `BUDGET = -31002`, `VALIDATION = -31003`, `UPSTREAM = -31004`, `CONFLICT = -31005`.
  - Replace the comment `# Class codes — must stay in the ``-32000`` JSON-RPC reserved range.` with:
    ```text
    # Class codes — application-defined, outside the JSON-RPC reserved range
    # (-32768..-32000), per the MCP 2026-07-28 error-code allocation policy.
    ```
  - Rewrite the docstring's "Class codes" list with the new numbers, and add: "These numbers classify errors inside Pitwall; ``safe_boundary`` delivers every tool failure to MCP clients as an ``isError`` result carrying only the string ``error`` code."
  - In `error_adapter.py`, set `PITWALL_ERROR_CODE_BASE = -31000`, and replace the docstring sentence "MCP integer codes are in the ``-32000`` range (JSON-RPC reserved)." with "Pitwall integer codes are application-defined (``-31000`` to ``-31005``), outside the JSON-RPC reserved range."
  - In each listed test, change `-32001`…`-32005` to `-31001`…`-31005`, and `-32000` (Pitwall fallback only) to `-31000`.

- [ ] **Step 4: Correct the SDLC doc.** In `docs/sdlc/03-mcp-server.md`:
  - **error_codes table.** Replace the class table's first column with `-31001`…`-31005` and `-31000`.
  - **error_adapter section.** Add a sentence: "Clients never receive these integers: `safe_boundary` returns every tool failure as `isError: true` with `{"error": "<code>"}`. The class number is used inside Pitwall and in tests only."
  - **§3.4.** Replace the two occurrences of "refusal surfaces as `-32002`" / "answers `-32002 budget_exhausted`" with "is refused with the `budget_exhausted` error code".
  - **§6 Error adapter.** Replace the paragraph with: "`adapt_error()` converts every `PitwallApiError` subclass to an `MCPError` with the class code from `error_codes` (fallback `-31000`) and `data` from `to_response_body()`. `safe_boundary` then reduces it to the stable string code in `data["error"]`."

- [ ] **Step 5: Run the affected tests and the doc gate**

Run: `uv run pytest tests/mcp -q -n auto 2>&1 | tail -5 && make docs-check 2>&1 | tail -1`
Expected: all pass, then `markdown links passed`.

- [ ] **Step 6: Commit**

```bash
git add src/pitwall/mcp/error_codes.py src/pitwall/mcp/error_adapter.py tests/mcp docs/sdlc/03-mcp-server.md
git commit -s -m "fix(mcp): allocate Pitwall error classes outside the JSON-RPC reserved range"
```

---

### Task 4: Make the boundary return protocol errors, actionable argument feedback, and rate limits (F03, F08, F09)

**Files:**
- Modify: `src/pitwall/mcp/safe_boundary.py` (`install_safe_call_boundary`, new `_RateLimiter`, new `_validation_fields`)
- Create: `tests/mcp/test_safe_boundary_protocol.py`
- Modify: `tests/release/test_mcp_all_tools_journey.py` (`test_tool_over_stdio`: the invalid-argument assertion), and any other test under `tests/` that pins `{"error": "invalid_tool_arguments"}` exactly
- Modify: `docs/sdlc/03-mcp-server.md` §6 "Global error boundary" paragraph

**Interfaces:**
- Consumes: `install_safe_call_boundary` from Task 2, and `safe_call_tool_for` from `tests/mcp/conftest.py` (Task 2).
- Produces:
  - Unknown tool: raises `MCPError(code=-32602, message="Unknown tool", data={"error": "unknown_tool"})`.
  - Invalid arguments: payload `{"error": "invalid_tool_arguments", "fields": [<declared names>]}` (validation failure), or `{"error": "invalid_tool_arguments", "allowed": [<declared names>]}` (undeclared argument).
  - Rate limit: payload `{"error": "rate_limited", "retry_after_s": <float>}`.
  - `safe_boundary.RATE_LIMIT_PER_SECOND = 20.0` and `RATE_LIMIT_BURST = 200`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mcp/test_safe_boundary_protocol.py
"""Boundary protocol behavior: unknown tool, argument feedback, rate limits (F03, F08, F09)."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError

from pitwall.mcp import safe_boundary
from pitwall.mcp.safe_boundary import install_safe_call_boundary
from tests.mcp.conftest import safe_call_tool_for

pytestmark = pytest.mark.anyio


def _server() -> MCPServer[Any]:
    server: MCPServer[Any] = MCPServer("t")

    def fit(model: str, ttl_minutes: int = 120) -> dict[str, Any]:
        return {"model": model, "ttl_minutes": ttl_minutes}

    def boom() -> dict[str, Any]:
        raise MCPError(code=-31002, message="spent", data={"error": "budget_exhausted"})

    server.add_tool(fit, name="fit", description="fit")
    server.add_tool(boom, name="boom", description="boom")
    install_safe_call_boundary(server)
    return server


async def test_unknown_tool_is_a_jsonrpc_invalid_params_error() -> None:
    with pytest.raises(MCPError) as raised:
        await safe_call_tool_for(_server(), "no_such_tool", {})
    assert raised.value.error.code == -32602
    assert raised.value.error.data == {"error": "unknown_tool"}
    assert "no_such_tool" not in raised.value.error.message


async def test_validation_failure_names_declared_fields_without_values() -> None:
    result = await safe_call_tool_for(_server(), "fit", {"model": {"nested": "SECRET"}})
    assert result.is_error is True
    assert result.structured_content == {"error": "invalid_tool_arguments", "fields": ["model"]}
    assert "SECRET" not in result.content[0].text


async def test_undeclared_argument_lists_allowed_names_only() -> None:
    result = await safe_call_tool_for(_server(), "fit", {"model": "m", "evil key": 1})
    assert result.structured_content == {
        "error": "invalid_tool_arguments",
        "allowed": ["model", "ttl_minutes"],
    }
    assert "evil key" not in result.content[0].text


async def test_mcp_error_from_tool_becomes_tool_result() -> None:
    result = await safe_call_tool_for(_server(), "boom", {})
    assert result.is_error is True
    assert result.structured_content == {"error": "budget_exhausted"}


async def test_burst_beyond_bucket_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_boundary, "RATE_LIMIT_BURST", 3)
    monkeypatch.setattr(safe_boundary, "RATE_LIMIT_PER_SECOND", 0.001)
    server = _server()
    for _ in range(3):
        ok = await safe_call_tool_for(server, "fit", {"model": "m"})
        assert ok.is_error is False
    limited = await safe_call_tool_for(server, "fit", {"model": "m"})
    assert limited.is_error is True
    assert limited.structured_content["error"] == "rate_limited"
    assert limited.structured_content["retry_after_s"] > 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/mcp/test_safe_boundary_protocol.py -q 2>&1 | tail -8`
Expected: 4 FAIL. Only `test_mcp_error_from_tool_becomes_tool_result` passes, because Task 2 already handles it.

- [ ] **Step 3: Implement.** Add these to `src/pitwall/mcp/safe_boundary.py`, and rewrite `safe_call_tool` inside `install_safe_call_boundary`:

```python
import time

from mcp.types import INVALID_PARAMS

# One stdio broker serves one client. The burst covers a legitimate sweep (the release
# journey sends 162 calls in one session); the refill rate caps a runaway loop.
RATE_LIMIT_PER_SECOND = 20.0
RATE_LIMIT_BURST = 200


class _RateLimiter:
    """One token bucket per server process: MCP 2026-07-28 tools security requires rate limits."""

    def __init__(self) -> None:
        self._tokens = float(RATE_LIMIT_BURST)
        self._stamp = time.monotonic()

    def acquire(self) -> float:
        """Return 0.0 when a call may proceed, else the seconds until a token is available."""
        now = time.monotonic()
        self._tokens = min(
            float(RATE_LIMIT_BURST), self._tokens + (now - self._stamp) * RATE_LIMIT_PER_SECOND
        )
        self._stamp = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        return round((1.0 - self._tokens) / RATE_LIMIT_PER_SECOND, 3)


def _validation_fields(exc: Exception, declared: frozenset[str]) -> list[str] | None:
    """Declared parameter names a pydantic validation failure points at; never values."""
    cause = exc.__cause__ if isinstance(exc, ToolError) else None
    errors = getattr(cause, "errors", None)
    if cause is None or not cause.__class__.__module__.startswith(("pydantic", "pydantic_core")):
        return None
    if not callable(errors):
        return None
    names = {str(error["loc"][0]) for error in errors() if error.get("loc")}
    return sorted(names & declared)
```

```text
    limiter = _RateLimiter()

    async def safe_call_tool(ctx: Any, params: CallToolRequestParams) -> Any:
        allowed = declared.get(params.name)
        if allowed is None:
            raise MCPError(code=INVALID_PARAMS, message="Unknown tool", data={"error": "unknown_tool"})
        arguments = params.arguments or {}
        if not set(arguments) <= allowed:
            return _payload_result({"error": "invalid_tool_arguments", "allowed": sorted(allowed)})
        wait = limiter.acquire()
        if wait:
            return _payload_result({"error": "rate_limited", "retry_after_s": wait})
        context = Context(
            request_context=ctx,
            mcp_server=server,
            input_params=params,
            subscriptions=server._subscriptions,
        )
        try:
            return await server.call_tool(params.name, arguments, context)
        except Exception as exc:  # reason: MCP transport must fail closed without reflection.
            fields = _validation_fields(exc, allowed)
            if fields is not None:
                return _payload_result({"error": "invalid_tool_arguments", "fields": fields})
            return _error_result(exc)
```

  Delete `_invalid_arguments_result`, which is now unused.

- [ ] **Step 4: Update the doc.** In `docs/sdlc/03-mcp-server.md` §6, replace the "Global error boundary" paragraph's last two sentences with:

  > An unknown tool name is a JSON-RPC error `-32602` with `data: {"error": "unknown_tool"}`. An undeclared argument returns `{"error": "invalid_tool_arguments", "allowed": [...]}`, and a schema-validation failure returns `{"error": "invalid_tool_arguments", "fields": [...]}`. Both lists contain declared parameter names only, never caller values. Calls beyond a per-process token bucket (20 per second, burst 200) return `{"error": "rate_limited", "retry_after_s": N}`.

- [ ] **Step 5: Update every exact-payload assertion, then run the MCP suites and the release journey**

List the assertions that pin the old payload across the whole test tree: `grep -rn 'invalid_tool_arguments"}' tests/`. Update each to the new shape. In `tests/release/test_mcp_all_tools_journey.py:test_tool_over_stdio`, replace `assert _payload(invalid) == {"error": "invalid_tool_arguments"}, name` with:

```text
    rejected = _payload(invalid)
    assert rejected["error"] == "invalid_tool_arguments", name
    # Feedback names Pitwall's own declared parameters, never the caller's values.
    assert set(rejected) <= {"error", "allowed", "fields"}, name
```

Run: `uv run pytest tests/mcp -q -n auto 2>&1 | tail -5`
Expected: all pass.

Run (database suite; never in parallel with another): `make up && uv run pytest tests/release/test_mcp_all_tools_journey.py -q -p no:randomly 2>&1 | tail -5`
Expected: all passed. Its 162 calls in one session stay inside the burst of 200, so no call is `rate_limited`.

- [ ] **Step 6: Commit**

```bash
git add src/pitwall/mcp/safe_boundary.py tests/mcp tests/release/test_mcp_all_tools_journey.py docs/sdlc/03-mcp-server.md
git commit -s -m "fix(mcp): unknown tool is -32602, argument errors name fields, tool calls are rate limited"
```

---

### Task 5: Give every broker tool a title and annotations (F05, F06 titles)

**Files:**
- Create: `src/pitwall/mcp/tool_metadata.py`
- Modify: `src/pitwall/mcp/registry.py` (`register_all`)
- Create: `tests/mcp/test_tool_metadata.py`
- Modify: `docs/sdlc/03-mcp-server.md` §3 (one new paragraph after the inventory table)

**Interfaces:**
- Produces:
  - `pitwall.mcp.tool_metadata.ToolMetadata` (frozen dataclass: `title: str`, `annotations: ToolAnnotations`)
  - `TOOL_METADATA: dict[str, ToolMetadata]`
  - `metadata_for(name: str) -> ToolMetadata`, which raises `KeyError` for an unknown name

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_tool_metadata.py
"""Every broker tool carries a title and honest annotations (F05, F06)."""

from __future__ import annotations

import pytest

from pitwall.mcp.registry import TOOL_NAMES
from pitwall.mcp.tool_metadata import TOOL_METADATA

pytestmark = pytest.mark.anyio

READ_PREFIXES = ("list", "get", "describe", "status", "read", "health", "doctor", "preview")


def test_metadata_covers_exactly_the_registry() -> None:
    assert set(TOOL_METADATA) == set(TOOL_NAMES)


def test_names_that_only_read_are_read_only() -> None:
    for name, meta in TOOL_METADATA.items():
        verb = name.removeprefix("pitwall_").removeprefix("runpod_").removeprefix("provider_ops_")
        if verb.startswith(READ_PREFIXES):
            assert meta.annotations.read_only_hint is True, name


def test_deletes_and_terminations_are_destructive() -> None:
    for name, meta in TOOL_METADATA.items():
        if any(word in name for word in ("delete", "terminate", "stop_lease", "cancel_job")):
            assert meta.annotations.read_only_hint is False, name
            assert meta.annotations.destructive_hint is True, name


def test_every_hint_is_explicit() -> None:
    for name, meta in TOOL_METADATA.items():
        a = meta.annotations
        assert None not in (a.read_only_hint, a.idempotent_hint, a.open_world_hint), name
        if a.read_only_hint is False:
            assert a.destructive_hint is not None, name
        assert meta.title and meta.title == meta.annotations.title, name


async def test_listed_tools_carry_title_and_annotations() -> None:
    from pitwall.mcp import mcp

    for tool in await mcp.list_tools():
        assert tool.title == TOOL_METADATA[tool.name].title
        assert tool.annotations == TOOL_METADATA[tool.name].annotations
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/mcp/test_tool_metadata.py -q 2>&1 | tail -5`
Expected: FAIL with `ModuleNotFoundError: pitwall.mcp.tool_metadata`.

- [ ] **Step 3: Create the metadata table.** Classification rules:
  - **Read-only:** no writes anywhere.
  - **Open world:** the tool reaches RunPod or another provider over the network.
  - **Destructive:** the tool removes, stops, replaces, or overwrites something.
  - **Idempotent:** a repeat with the same arguments has no further effect. This is true where an idempotency key is required, or where the operation converges to the same state.

```python
# src/pitwall/mcp/tool_metadata.py
"""Display titles, behavior annotations, and parameter descriptions for every broker tool.

One table, keyed by tool name, so the 81 handlers and their six feature-local spec
types stay unchanged. ``registry.register_all`` applies it; tests pin full coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.types import ToolAnnotations


@dataclass(frozen=True, slots=True)
class ToolMetadata:
    title: str
    annotations: ToolAnnotations


def _meta(
    title: str,
    *,
    read_only: bool,
    destructive: bool = False,
    idempotent: bool,
    open_world: bool,
) -> ToolMetadata:
    return ToolMetadata(
        title=title,
        annotations=ToolAnnotations(
            title=title,
            read_only_hint=read_only,
            destructive_hint=None if read_only else destructive,
            idempotent_hint=idempotent,
            open_world_hint=open_world,
        ),
    )


def _read(title: str, *, open_world: bool = False) -> ToolMetadata:
    return _meta(title, read_only=True, idempotent=True, open_world=open_world)


TOOL_METADATA: dict[str, ToolMetadata] = {
    # Discovery and catalogue reads (database or local files only).
    "pitwall_list_capabilities": _read("List capabilities"),
    "pitwall_describe_capability": _read("Describe a capability"),
    "pitwall_list_providers": _read("List providers"),
    "pitwall_get_provider_health": _read("Get provider health"),
    "pitwall_models_list": _read("List catalogue models"),
    "pitwall_models_fit": _read("Fit a model to GPUs"),
    "pitwall_cost_summary": _read("Summarize cost"),
    "pitwall_recent_workloads": _read("List recent workloads"),
    "pitwall_burn_rate": _read("Forecast budget burn rate"),
    "pitwall_budget_status": _read("Get budget status"),
    "pitwall_guardrail_status": _read("Get guardrail status"),
    "pitwall_guardrail_preview": _read("Preview a guardrail decision"),
    "pitwall_audit_log": _read("Read the audit log"),
    "pitwall_copilot_propose": _read("Propose a GitOps change"),
    "pitwall_health": _read("Check broker health"),
    "pitwall_doctor": _read("Run installation doctor", open_world=True),
    "pitwall_gateway_catalog_read": _read("Read the gateway catalog"),
    "pitwall_quota_list": _read("List provider quotas"),
    "pitwall_get_job_status": _read("Get job status"),
    "pitwall_get_job_result": _read("Get job result"),
    "pitwall_get_job_events": _read("Get job events"),
    "pitwall_get_lease": _read("Get a lease"),
    "pitwall_preview_route": _read("Preview a route"),
    "pitwall_provider_ops_list_descriptors": _read("List provider descriptors"),
    "pitwall_provider_ops_describe": _read("Describe a provider"),
    "pitwall_provider_ops_availability": _read("Probe provider availability", open_world=True),
    "pitwall_provider_ops_health": _read("Read provider health", open_world=True),
    "pitwall_runpod_catalogue": _read("Read the RunPod catalogue", open_world=True),
    "pitwall_runpod_onboarding_plan": _read("Plan RunPod onboarding", open_world=True),
    "pitwall_runpod_onboarding_status": _read("Get RunPod onboarding status", open_world=True),
    "pitwall_runpod_onboarding_rollback": _read("Get RunPod rollback guidance", open_world=True),
    "pitwall_runpod_list_pods": _read("List RunPod pods", open_world=True),
    "pitwall_runpod_get_pod": _read("Get a RunPod pod", open_world=True),
    "pitwall_runpod_list_endpoints": _read("List RunPod endpoints", open_world=True),
    "pitwall_runpod_get_endpoint": _read("Get a RunPod endpoint", open_world=True),
    "pitwall_runpod_list_templates": _read("List RunPod templates", open_world=True),
    "pitwall_runpod_get_template": _read("Get a RunPod template", open_world=True),
    "pitwall_runpod_list_volumes": _read("List RunPod volumes", open_world=True),
    "pitwall_runpod_get_volume": _read("Get a RunPod volume", open_world=True),
    "pitwall_runpod_list_registry_auths": _read("List registry auths", open_world=True),
    "pitwall_runpod_get_registry_auth": _read("Get a registry auth", open_world=True),
    "pitwall_runpod_list_hub_templates": _read("List Hub templates", open_world=True),
    "pitwall_runpod_get_hub_template": _read("Get a Hub template", open_world=True),
    "pitwall_runpod_search_hub_templates": _read("Search Hub templates", open_world=True),
    "pitwall_volume_list_objects": _read("List volume objects", open_world=True),
    "pitwall_volume_read_chunk": _read("Read a volume object chunk", open_world=True),
    "pitwall_pod_logs": _read("Read pod logs", open_world=True),
    # Spend-creating work (additive, provider-facing; optional idempotency key -> not idempotent).
    "pitwall_submit_inference": _meta(
        "Submit inference", read_only=False, idempotent=False, open_world=True
    ),
    "pitwall_submit_job": _meta("Submit a job", read_only=False, idempotent=False, open_world=True),
    "pitwall_lease_pod": _meta("Lease a pod", read_only=False, idempotent=False, open_world=True),
    "pitwall_serve_model": _meta(
        "Serve a model", read_only=False, idempotent=False, open_world=True
    ),
    "pitwall_renew_lease": _meta(
        "Renew a lease", read_only=False, idempotent=False, open_world=False
    ),
    # Stops and cancellations.
    "pitwall_cancel_job": _meta(
        "Cancel a job", read_only=False, destructive=True, idempotent=True, open_world=True
    ),
    "pitwall_stop_lease": _meta(
        "Stop a lease", read_only=False, destructive=True, idempotent=True, open_world=True
    ),
    # Registry and budget administration (database only unless noted).
    "pitwall_budget_set": _meta(
        "Set budget limits", read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "pitwall_create_capability": _meta(
        "Create a capability", read_only=False, idempotent=False, open_world=False
    ),
    "pitwall_update_capability": _meta(
        "Update a capability", read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "pitwall_create_provider": _meta(
        "Create a provider", read_only=False, idempotent=False, open_world=False
    ),
    "pitwall_update_provider": _meta(
        "Update a provider", read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "pitwall_disable_provider": _meta(
        "Disable a provider", read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "pitwall_hibernate_provider": _meta(
        "Hibernate a provider", read_only=False, destructive=True, idempotent=True, open_world=True
    ),
    # RunPod onboarding (confirmed plan id makes apply/resume idempotent and additive).
    "pitwall_runpod_onboarding_apply": _meta(
        "Apply RunPod onboarding", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_onboarding_resume": _meta(
        "Resume RunPod onboarding", read_only=False, idempotent=True, open_world=True
    ),
    # Raw RunPod resources (idempotency_key is required on every mutation).
    "pitwall_runpod_create_pod": _meta(
        "Create a RunPod pod", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_update_pod": _meta(
        "Update a RunPod pod", read_only=False, destructive=True, idempotent=True, open_world=True
    ),
    "pitwall_runpod_action_pod": _meta(
        "Start, stop, restart, or reset a pod",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_terminate_pod": _meta(
        "Terminate a RunPod pod",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_create_endpoint": _meta(
        "Create a RunPod endpoint", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_update_endpoint": _meta(
        "Update a RunPod endpoint",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_delete_endpoint": _meta(
        "Delete a RunPod endpoint",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_create_template": _meta(
        "Create a RunPod template", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_update_template": _meta(
        "Update a RunPod template",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_delete_template": _meta(
        "Delete a RunPod template",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_create_volume": _meta(
        "Create a RunPod volume", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_grow_volume": _meta(
        "Grow a RunPod volume", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_delete_volume": _meta(
        "Delete a RunPod volume",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_create_registry_auth": _meta(
        "Create a registry auth", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_replace_registry_auth": _meta(
        "Replace a registry auth",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_delete_registry_auth": _meta(
        "Delete a registry auth",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    # Network-volume file mutations (intent, idempotency key, and confirmation required).
    "pitwall_volume_upload_object": _meta(
        "Upload a volume object",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_volume_delete_object": _meta(
        "Delete a volume object",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
}


def metadata_for(name: str) -> ToolMetadata:
    return TOOL_METADATA[name]


__all__ = ["TOOL_METADATA", "ToolMetadata", "metadata_for"]
```

  The table has 81 entries. Confirm the count with `test_metadata_covers_exactly_the_registry`.

- [ ] **Step 4: Apply it at registration.** In `registry.register_all`:

```text
    from pitwall.mcp.tool_metadata import metadata_for

    for spec in TOOL_REGISTRY:
        meta = metadata_for(spec.name)
        server.add_tool(
            spec.handler,
            name=spec.name,
            title=meta.title,
            description=spec.description,
            annotations=meta.annotations,
        )
```

- [ ] **Step 5: Document it.** After the §3 inventory table in `docs/sdlc/03-mcp-server.md`, add:

  > Every tool's display `title` and its `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) come from `pitwall.mcp.tool_metadata.TOOL_METADATA`. A tool is open-world when it reaches RunPod or another provider, and idempotent when an idempotency key or a converging operation makes a repeat harmless. `tests/mcp/test_tool_metadata.py` requires an entry for every registered name.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/mcp/test_tool_metadata.py tests/mcp/test_tool_count.py tests/mcp/test_doc_count_sync.py -q 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/pitwall/mcp/tool_metadata.py src/pitwall/mcp/registry.py tests/mcp/test_tool_metadata.py docs/sdlc/03-mcp-server.md
git commit -s -m "feat(mcp): titles and behavior annotations for every broker tool"
```

---

### Task 6: Describe every broker tool parameter (F06 parameters)

**Files:**
- Modify: `src/pitwall/mcp/tool_metadata.py` (add `PARAMETER_DESCRIPTIONS`, `PARAMETER_OVERRIDES`, `describe_parameter`)
- Modify: `src/pitwall/mcp/registry.py` (`register_all`: apply descriptions to `tool.parameters`)
- Create: `tests/mcp/test_tool_parameter_descriptions.py`

**Interfaces:**
- Consumes: `TOOL_METADATA` (Task 5).
- Produces: `describe_parameter(tool: str, parameter: str) -> str`, which raises `KeyError` when neither a per-tool override nor a shared description exists.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_tool_parameter_descriptions.py
"""Every broker tool parameter has a model-facing description (F06)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def test_every_parameter_is_described() -> None:
    from pitwall.mcp import mcp

    missing = [
        f"{tool.name}.{name}"
        for tool in await mcp.list_tools()
        for name, schema in tool.input_schema.get("properties", {}).items()
        if not str(schema.get("description", "")).strip()
    ]
    assert missing == []


def test_handler_annotated_descriptions_are_not_overwritten() -> None:
    from pitwall.mcp.tool_metadata import PARAMETER_OVERRIDES

    assert "ttl_minutes" not in PARAMETER_OVERRIDES.get("pitwall_serve_model", {})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/mcp/test_tool_parameter_descriptions.py -q 2>&1 | tail -5`
Expected: FAIL. `missing` lists 177 entries.

- [ ] **Step 3: Add the tables.** Append to `src/pitwall/mcp/tool_metadata.py`, and add the two names to `__all__`. Shared descriptions apply wherever a parameter name means the same thing. Overrides cover the names whose meaning differs by tool.

```python
PARAMETER_DESCRIPTIONS: dict[str, str] = {
    "action": "Audit action to filter by, for example 'create' or 'update'.",
    "adapter_id": "Provider adapter identifier; 'runpod' unless another adapter is installed.",
    "capability_class": "Capability class filter or value, for example 'embedding' or 'chat'.",
    "capability_id": "Capability ULID as returned by pitwall_list_capabilities.",
    "cloud_type": "RunPod cloud type: 'SECURE' or 'COMMUNITY'.",
    "cold_start_p50_ms": "Observed median cold start in milliseconds.",
    "cold_start_p95_ms": "Observed 95th-percentile cold start in milliseconds.",
    "config": "Provider-specific configuration object validated against the provider schema.",
    "confirm_delete": "Must be true to delete; protects against accidental removal.",
    "confirm_overwrite": "Must be true to replace an existing object at object_key.",
    "consecutive_failures": "Recorded consecutive failure count for the provider.",
    "content_base64": "Object bytes, base64-encoded; bounded by the volume-file upload limit.",
    "cooldown_trips": "Recorded number of cooldown trips for the provider.",
    "cost_mode": "Cost mode: 'per_request', 'per_second', or 'free'.",
    "credential_ref": "Name of the environment variable holding the provider credential; never the value.",
    "data_center_id": "RunPod data center id that hosts the network volume, for example 'EU-RO-1'.",
    "description": "Human-readable capability description.",
    "dry_run": "When true, validate and return the plan without creating, spending, or writing.",
    "enabled": "Filter by, or set, the enabled flag.",
    "enabled_only": "When true, list only enabled providers.",
    "engine": "Serving engine: 'vllm', 'llama.cpp', or 'sglang'.",
    "entity_id": "Audited entity id to filter by.",
    "entity_type": "Audited entity type to filter by, for example 'capability' or 'provider'.",
    "expected_sha256": "Optional lowercase hex SHA-256 the uploaded bytes must match.",
    "extends_minutes": "Minutes to add to the lease expiry.",
    "force_refresh": "When true, make one live RunPod read instead of serving the cache.",
    "health_status": "Provider health status to record: 'healthy', 'degraded', or 'unhealthy'.",
    "idempotency_key": "Caller-chosen key; a repeat with the same key replays the first result.",
    "input": "Job input object passed to the capability.",
    "input_schema": "JSON Schema object for the capability's input.",
    "lease_id": "Lease ULID as returned by pitwall_lease_pod or pitwall_serve_model.",
    "max_bytes": "Upper bound on bytes returned in this call.",
    "max_items": "Upper bound on objects returned in this page.",
    "max_lines": "Upper bound on log lines returned.",
    "monthly_budget_usd": "New monthly budget cap in USD, as a decimal string, for example '75.00'.",
    "object_key": "Object key (path) inside the network volume.",
    "output_schema": "JSON Schema object for the capability's output.",
    "per_request_max_usd": "New per-request cost cap in USD, as a decimal string.",
    "pod_id": "RunPod pod id.",
    "prefix": "Object key prefix to list under; empty lists from the volume root.",
    "priority": "Routing priority; lower numbers are tried first.",
    "provider_enabled": "Desired enabled state for the referenced provider.",
    "provider_id": "Provider ULID as returned by pitwall_list_providers.",
    "provider_patch": "Explicit provider field changes to propose.",
    "provider_priority": "Desired routing priority for the referenced provider.",
    "provider_ref": "Provider name or ULID the proposal applies to.",
    "provider_type": "Provider type filter or value, for example 'serverless' or 'pod_lease'.",
    "query": "Search text matched against Hub template names and descriptions.",
    "recent_error_rate": "Recorded recent error rate between 0 and 1.",
    "region": "Provider region or data center label.",
    "resource_id": "RunPod resource id of the pod, endpoint, template, volume, or registry auth.",
    "routable_only": "When true, return only catalog rows the gateway can route to.",
    "runpod_endpoint_id": "RunPod serverless endpoint id backing the provider.",
    "runpod_template_id": "RunPod template id backing the provider.",
    "scorecards": "Optional provider scorecard snapshots used to derive recommendations.",
    "since": "Inclusive ISO 8601 start date or timestamp (UTC).",
    "state": "Workload state filter, for example 'completed' or 'failed'.",
    "tos": "Terms-of-service verdict filter, for example 'allowed' or 'avoid'.",
    "until": "Exclusive ISO 8601 end date or timestamp (UTC).",
    "version": "Capability version string, for example '1.0.0'.",
    "volume_id": "RunPod network volume id.",
    "webhook_url": "Optional HTTPS URL notified when the job finishes; must pass the webhook allowlist.",
    "window_days": "Number of trailing UTC days the forecast averages over.",
    "workload_id": "Workload ULID returned by a submit call.",
}

PARAMETER_OVERRIDES: dict[str, dict[str, str]] = {
    "pitwall_audit_log": {"limit": "Maximum audit entries to return."},
    "pitwall_recent_workloads": {"limit": "Maximum workloads to return."},
    "pitwall_provider_ops_list_descriptors": {"limit": "Maximum descriptors to return (1-100)."},
    "pitwall_provider_ops_availability": {"limit": "Maximum availability rows to return (1-100)."},
    "pitwall_runpod_list_hub_templates": {
        "limit": "Maximum templates to return.",
        "offset": "Number of templates to skip before the first one returned.",
    },
    "pitwall_runpod_search_hub_templates": {"limit": "Maximum matching templates to return."},
    "pitwall_get_job_events": {"limit": "Maximum lifecycle events to return in this page."},
    "pitwall_volume_read_chunk": {"offset": "Byte offset to start reading from."},
    "pitwall_describe_capability": {"name": "Capability name or ULID."},
    "pitwall_create_capability": {"name": "Unique capability name."},
    "pitwall_update_capability": {"name": "New capability name."},
    "pitwall_create_provider": {"name": "Unique provider name."},
    "pitwall_update_provider": {"name": "New provider name."},
    "pitwall_copilot_propose": {
        "intent": "Operator intent: 'enable', 'disable', 'priority', or 'patch'."
    },
    "pitwall_volume_upload_object": {"intent": "Must be the literal 'upload'."},
    "pitwall_volume_delete_object": {"intent": "Must be the literal 'delete'."},
    "pitwall_stop_lease": {"reason": "Optional reason recorded with the teardown."},
    "pitwall_budget_set": {"reason": "Required reason recorded in the audit trail."},
    "pitwall_submit_inference": {"payload": "Inference request body for the capability."},
    "pitwall_guardrail_preview": {"payload": "Any JSON value to scan for secrets and PII."},
    "pitwall_preview_route": {
        "payload": "Optional request body used for size and guardrail planning.",
        "operation": "Routing operation: 'sync_inference', 'async_inference', or 'compute'.",
    },
    "pitwall_models_fit": {
        "inventory": "Optional local GPU inventory; when given, fit against it instead of RunPod.",
        "context": "Context length in tokens; required when the variant's context is unverified.",
    },
    "pitwall_doctor": {
        "canary": "Optional enabled embedding capability to exercise with a dry-run inference."
    },
    **{
        name: {
            "request": "Request object; see this tool's input schema for its fields and the intent."
        }
        for name in (
            "pitwall_runpod_onboarding_plan",
            "pitwall_runpod_onboarding_apply",
            "pitwall_runpod_onboarding_status",
            "pitwall_runpod_onboarding_resume",
            "pitwall_runpod_onboarding_rollback",
            "pitwall_runpod_create_pod",
            "pitwall_runpod_update_pod",
            "pitwall_runpod_action_pod",
            "pitwall_runpod_terminate_pod",
            "pitwall_runpod_create_endpoint",
            "pitwall_runpod_update_endpoint",
            "pitwall_runpod_delete_endpoint",
            "pitwall_runpod_create_template",
            "pitwall_runpod_update_template",
            "pitwall_runpod_delete_template",
            "pitwall_runpod_create_volume",
            "pitwall_runpod_grow_volume",
            "pitwall_runpod_delete_volume",
            "pitwall_runpod_create_registry_auth",
            "pitwall_runpod_replace_registry_auth",
            "pitwall_runpod_delete_registry_auth",
        )
    },
}


def describe_parameter(tool: str, parameter: str) -> str:
    override = PARAMETER_OVERRIDES.get(tool, {}).get(parameter)
    return override if override is not None else PARAMETER_DESCRIPTIONS[parameter]
```

- [ ] **Step 4: Apply the descriptions.** At the end of the loop body in `registry.register_all`, add the following. A description already written on the handler's `Annotated[..., Field(description=...)]` wins: the 30 already described keep theirs.

```text
        registered = server._tool_manager.get_tool(spec.name)
        for parameter, schema in registered.parameters.get("properties", {}).items():
            if not str(schema.get("description", "")).strip():
                schema["description"] = describe_parameter(spec.name, parameter)
```

  Import `describe_parameter` next to `metadata_for`.

- [ ] **Step 5: Run the tests and the release inventory**

Run: `uv run pytest tests/mcp/test_tool_parameter_descriptions.py tests/mcp/test_tool_contract.py -q 2>&1 | tail -5`
Expected: all pass. A `KeyError` names any parameter missing from both tables; add it to `PARAMETER_DESCRIPTIONS` with its real meaning from the handler.

- [ ] **Step 6: Commit**

```bash
git add src/pitwall/mcp/tool_metadata.py src/pitwall/mcp/registry.py tests/mcp/test_tool_parameter_descriptions.py
git commit -s -m "feat(mcp): describe every broker tool parameter"
```

---

### Task 7: Broker identity: instructions, cache hints, honest capabilities, no empty output schema (F07, F10, F17)

**Files:**
- Modify: `src/pitwall/mcp/__init__.py` (`MCPServer(...)` arguments)
- Modify: `src/pitwall/mcp/registry.py` (new `strip_unused_features(server)` and `install_list_tools_filter(server)`, called from `register_all`)
- Create: `tests/mcp/test_server_identity.py`
- Modify: `docs/sdlc/03-mcp-server.md` §1, §2 (package init, `__main__`), §4, §5

**Interfaces:**
- Produces: `pitwall.mcp.INSTRUCTIONS: str`, `registry.GENERIC_OUTPUT_SCHEMA_KEYS = {"type", "additionalProperties", "title"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mcp/test_server_identity.py
"""Discovery identity, cache hints, honest capabilities, no empty output schemas (F07, F10, F17)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.mcp.raw_stdio import MODERN_META, RawStdio


@pytest.fixture()
def broker() -> Iterator[RawStdio]:
    client = RawStdio.start()
    try:
        yield client
    finally:
        client.close()


def test_discover_has_instructions_and_only_tools(broker: RawStdio) -> None:
    result = broker.request("server/discover", {"_meta": MODERN_META})["result"]
    assert "catalogue" in result["instructions"]
    assert set(result["capabilities"]) - {"experimental", "extensions"} == {"tools"}
    assert result["cacheScope"] == "public" and result["ttlMs"] == 3_600_000


def test_tools_list_is_cacheable_for_an_hour(broker: RawStdio) -> None:
    result = broker.request("tools/list", {"_meta": MODERN_META})["result"]
    assert result["ttlMs"] == 3_600_000 and result["cacheScope"] == "public"


def test_no_tool_advertises_the_generic_object_output_schema(broker: RawStdio) -> None:
    tools = broker.request("tools/list", {"_meta": MODERN_META})["result"]["tools"]
    for tool in tools:
        schema = tool.get("outputSchema")
        assert schema is None or set(schema) - {"type", "additionalProperties", "title"}, tool[
            "name"
        ]


def test_prompts_list_is_not_served(broker: RawStdio) -> None:
    reply = broker.request("prompts/list", {"_meta": MODERN_META})
    assert reply["error"]["code"] == -32601


def test_structured_content_survives_without_output_schema(broker: RawStdio) -> None:
    reply = broker.request(
        "tools/call", {"_meta": MODERN_META, "name": "pitwall_models_list", "arguments": {}}
    )
    assert reply["result"]["isError"] is False
    assert isinstance(reply["result"]["structuredContent"]["models"], list)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/mcp/test_server_identity.py -q -p no:randomly 2>&1 | tail -8`
Expected: FAIL on instructions, capabilities, ttl, output schema, and prompts.

- [ ] **Step 3: Add instructions and cache hints.** In `src/pitwall/mcp/__init__.py`:

```python
from mcp.server.caching import CacheHint

INSTRUCTIONS = (
    "Pitwall brokers GPU inference, pod leases, and RunPod resources under a monthly budget. "
    "Use the catalogue first: pitwall_models_list, then pitwall_models_fit, then "
    "pitwall_serve_model with the fitted variant. Mutating RunPod tools take intent='preview' "
    "before intent='apply' and require an idempotency_key; repeat a call with the same key to "
    "replay it safely. Spend is gated by the budget: on budget_rejected or budget_exhausted read "
    "the remedy field, and change limits only with pitwall_budget_set and a reason. Errors are "
    "returned as {'error': '<code>'} tool results; invalid_tool_arguments lists the parameter "
    "names to fix."
)

_HOUR_PUBLIC = CacheHint(ttl_ms=3_600_000, scope="public")

mcp: MCPServer[Any] = MCPServer(
    "pitwall",
    version=_distribution_version(),
    instructions=INSTRUCTIONS,
    lifespan=_mcp_lifespan,
    cache_hints={"server/discover": _HOUR_PUBLIC, "tools/list": _HOUR_PUBLIC},
)
```

  Add `"INSTRUCTIONS"` to `__all__`.

- [ ] **Step 4: Remove unserved features and the generic output schema.** In `registry.py`, define the following and call both at the end of `register_all`, after `install_safe_call_boundary(server)`:

```python
GENERIC_OUTPUT_SCHEMA_KEYS = frozenset({"type", "additionalProperties", "title"})
_UNSERVED_METHODS = (
    "prompts/list",
    "prompts/get",
    "resources/list",
    "resources/read",
    "resources/templates/list",
    "resources/subscribe",
    "resources/unsubscribe",
    "completion/complete",
    "logging/setLevel",
)


def strip_unused_features(server: Any) -> None:
    """Drop prompt/resource handlers: SDK 2 advertises a capability for every registered handler."""
    handlers = server._lowlevel_server._request_handlers
    for method in _UNSERVED_METHODS:
        handlers.pop(method, None)


def install_list_tools_filter(server: Any) -> None:
    """Omit outputSchema when it is the generic dict schema that conveys no structure."""
    from mcp.types import PaginatedRequestParams

    original = server._handle_list_tools

    async def list_tools(ctx: Any, params: PaginatedRequestParams | None) -> Any:
        result = await original(ctx, params)
        for tool in result.tools:
            schema = tool.output_schema
            if isinstance(schema, dict) and set(schema) <= GENERIC_OUTPUT_SCHEMA_KEYS:
                tool.output_schema = None
        return result

    server._lowlevel_server.add_request_handler("tools/list", PaginatedRequestParams, list_tools)
```

  Structured content is unaffected, because SDK 2 converts a handler's `dict` return independently of the listing (`Tool.run(convert_result=True)`). `test_structured_content_survives_without_output_schema` pins this.

- [ ] **Step 5: Document it.** In `docs/sdlc/03-mcp-server.md`:
  - **§1:** replace "standalone FastMCP-based server" with "server on the MCP Python SDK 2 `MCPServer`, serving protocol `2026-07-28` (stateless, `server/discover`) and the legacy `initialize` handshake (2024-11-05 to 2025-11-25) from one process".
  - **§2 package init:** list `INSTRUCTIONS`, the one-hour public cache hints for `server/discover` and `tools/list`, and that prompt and resource handlers are removed so discovery advertises `tools` only.
  - **§4:** add `INSTRUCTIONS` and `install_list_tools_filter`.
  - **§5:** add the line "Protocol versions: from `mcp_types.version` (`MODERN_PROTOCOL_VERSIONS`, `HANDSHAKE_PROTOCOL_VERSIONS`)".
  - **§8 External dependencies:** change `mcp.server.fastmcp.FastMCP` to `mcp.server.mcpserver.MCPServer (mcp>=2.3.0,<3)`.

- [ ] **Step 6: Run the tests and the doc gate**

Run: `uv run pytest tests/mcp -q -n auto 2>&1 | tail -5 && make docs-check 2>&1 | tail -1`
Expected: all pass, then `markdown links passed`.

- [ ] **Step 7: Commit**

```bash
git add src/pitwall/mcp/__init__.py src/pitwall/mcp/registry.py tests/mcp/test_server_identity.py docs/sdlc/03-mcp-server.md
git commit -s -m "feat(mcp): broker instructions, cache hints, tools-only capabilities, no empty output schema"
```

---

### Task 8: Serve both protocol eras from the channel server (F02, F13)

**Files:**
- Modify: `src/pitwall/agents/mcp_server.py`:
  - module constants
  - `ChannelServer.serve`
  - `ChannelServer._dispatch`
  - new methods: `_modern_envelope`, `_complete`, `_discover`, `_listen`, `_server_info`
  - `_result`
  - `_start_call` result shaping
- Create: `tests/agents/test_mcp_protocol_eras.py`
- Modify: `docs/agents/orchestrator-channel.md` § "Tier 1: the MCP server"

**Interfaces:**
- Consumes: `tests.agents.mcp_test_client.McpTestClient` (`send`, `wait`, `request`, `initialize`, `call`, `close`, `notifications`).
- Produces:
  - `mcp_server.MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)`
  - `mcp_server.LEGACY_PROTOCOL_VERSIONS` (renamed from `SUPPORTED_PROTOCOL_VERSIONS`, same tuple)
  - `UNSUPPORTED_PROTOCOL_VERSION = -32022`
  - `CACHE_TTL_MS = 3_600_000`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_mcp_protocol_eras.py
"""The channel serves 2026-07-28 statelessly and the legacy handshake (F02, F13)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.agents.mcp_test_client import McpTestClient

META: dict[str, Any] = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "0"},
}


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[McpTestClient]:
    env = {k: v for k, v in os.environ.items() if k != "PITWALL_AGENTS_CHANNEL_DISPATCH_ID"}
    env["PITWALL_AGENTS_STATE_HOME"] = str(tmp_path)
    c = McpTestClient(env)
    try:
        yield c
    finally:
        c.close()


def test_channel_discover(client: McpTestClient) -> None:
    result = client.request("server/discover", {"_meta": META})["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == ["2026-07-28"]
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "pitwall-channel"
    assert result["ttlMs"] == 3_600_000 and result["cacheScope"] == "public"
    assert result["instructions"]


def test_channel_modern_tools_list_and_call_without_initialize(client: McpTestClient) -> None:
    listed = client.request("tools/list", {"_meta": META})["result"]
    assert listed["resultType"] == "complete"
    assert listed["ttlMs"] == 3_600_000 and listed["cacheScope"] == "public"
    assert {t["name"] for t in listed["tools"]} >= {"inbox", "dispatch_and_wait"}
    called = client.call("inbox", {}, meta=META)["result"]
    assert called["resultType"] == "complete" and called["isError"] is False
    assert called["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "pitwall-channel"


def test_channel_unsupported_version_is_rejected(client: McpTestClient) -> None:
    meta = {**META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
    error = client.request("tools/list", {"_meta": meta})["error"]
    assert error["code"] == -32022
    assert error["data"] == {"supported": ["2026-07-28"], "requested": "1900-01-01"}


def test_channel_modern_request_without_capabilities_is_invalid(client: McpTestClient) -> None:
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
    assert client.request("tools/list", {"_meta": meta})["error"]["code"] == -32602


def test_channel_legacy_handshake_unchanged(client: McpTestClient) -> None:
    reply = client.initialize("2025-06-18")["result"]
    assert reply["protocolVersion"] == "2025-06-18"
    assert "resultType" not in reply
    listed = client.request("tools/list")["result"]
    assert "resultType" not in listed and "ttlMs" not in listed


def test_channel_listen_acknowledges_an_empty_filter_and_closes_on_cancel(
    client: McpTestClient,
) -> None:
    listen_id = client.send(
        "subscriptions/listen", {"_meta": META, "notifications": {"toolsListChanged": True}}
    )
    client.wait_for_notification("notifications/subscriptions/acknowledged")
    ack = next(
        n for n in client.notifications if n["method"] == "notifications/subscriptions/acknowledged"
    )
    assert ack["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == listen_id
    assert ack["params"]["notifications"] == {}
    client.send("notifications/cancelled", {"requestId": listen_id}, notify=True)
    assert client.request("tools/list", {"_meta": META})["result"]["resultType"] == "complete"


def test_channel_null_id_is_invalid_request(client: McpTestClient) -> None:
    assert client.process.stdin is not None
    client.process.stdin.write(
        (json.dumps({"jsonrpc": "2.0", "id": None, "method": "tools/list"}) + "\n").encode()
    )
    client.process.stdin.flush()
    # McpTestClient._read files every response carrying an "id" key under that id, None included.
    assert client.wait(None)["error"]["code"] == -32600
```

  Add one method to `tests/agents/mcp_test_client.py`:

```text
    def wait_for_notification(self, method: str, timeout: float = HANG_GUARD_SECS) -> None:
        with self._cond:
            if not self._cond.wait_for(
                lambda: any(n.get("method") == method for n in self.notifications), timeout=timeout
            ):
                raise TimeoutError(f"no {method} notification")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_mcp_protocol_eras.py -q -p no:randomly 2>&1 | tail -10`
Expected: FAIL. `server/discover` returns `-32601`, modern results lack `resultType`, and the null-id request times out.

- [ ] **Step 3: Implement the modern path.** In `src/pitwall/agents/mcp_server.py`:

```python
LEGACY_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)
UNSUPPORTED_PROTOCOL_VERSION = -32022
CACHE_TTL_MS = 3_600_000
INSTRUCTIONS = "Orchestrator channel for Pitwall Agent Routing dispatches."
_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
_SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
_SUBSCRIPTION_KEY = "io.modelcontextprotocol/subscriptionId"
```

  Replace every use of `SUPPORTED_PROTOCOL_VERSIONS` with `LEGACY_PROTOCOL_VERSIONS`. Add these methods to `ChannelServer`, and keep `self._listens: dict[Any, threading.Event] = {}` (set in `__init__`):

```text
    def _server_info(self) -> dict[str, Any]:
        return {"name": SERVER_NAME, "version": _version(self.env)}

    def _modern_envelope(self, request_id: Any, params: dict[str, Any]) -> bool | None:
        """True: a valid modern request. False: rejected (error sent). None: no modern _meta."""
        meta = params.get("_meta")
        if not isinstance(meta, dict) or _VERSION_KEY not in meta:
            return None
        requested = meta.get(_VERSION_KEY)
        if requested not in MODERN_PROTOCOL_VERSIONS:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": UNSUPPORTED_PROTOCOL_VERSION,
                        "message": "Unsupported protocol version",
                        "data": {"supported": list(MODERN_PROTOCOL_VERSIONS), "requested": requested},
                    },
                }
            )
            return False
        if not isinstance(meta.get(_CAPABILITIES_KEY), dict):
            self._error(request_id, INVALID_PARAMS, "missing io.modelcontextprotocol/clientCapabilities")
            return False
        return True

    def _complete(self, result: dict[str, Any]) -> dict[str, Any]:
        meta = dict(result.get("_meta") or {})
        meta[_SERVER_INFO_KEY] = self._server_info()
        return {**result, "resultType": "complete", "_meta": meta}

    def _discover(self, request_id: Any) -> None:
        self._result(
            request_id,
            self._complete(
                {
                    "supportedVersions": list(MODERN_PROTOCOL_VERSIONS),
                    "capabilities": {"tools": {"listChanged": False}},
                    "instructions": INSTRUCTIONS,
                    "ttlMs": CACHE_TTL_MS,
                    "cacheScope": "public",
                }
            ),
        )

    def _listen(self, request_id: Any) -> None:
        """Acknowledge with the honored subset (none: the tool list never changes) and hold open."""
        closed = threading.Event()
        with self._inflight_lock:
            self._listens[request_id] = closed
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/subscriptions/acknowledged",
                "params": {"_meta": {_SUBSCRIPTION_KEY: request_id}, "notifications": {}},
            }
        )
```

  In `_dispatch`, replace the `request_id is None` early return and the method routing with:

```text
        if method == "notifications/cancelled":
            with self._inflight_lock:
                event = self._inflight.get(params.get("requestId"))
                self._listens.pop(params.get("requestId"), None)
            if event is not None:
                event.set()
            return
        if "id" in message and request_id is None:
            self._error(None, INVALID_REQUEST, "request id must not be null")
            return
        if request_id is None:
            return  # other notifications, including notifications/initialized
        modern = self._modern_envelope(request_id, params)
        if modern is False:
            return
        if method == "server/discover":
            self._discover(request_id)
            return
        if method == "subscriptions/listen" and modern:
            self._listen(request_id)
            return
        if method == "initialize":
            ...  # existing handshake body, using LEGACY_PROTOCOL_VERSIONS; unchanged otherwise
            return
        if method == "ping":
            self._result(request_id, {})
            return
        if method == "tools/list":
            listing: dict[str, Any] = {
                "tools": [definition for definition, _ in self._visible().values()]
            }
            if modern:
                listing = self._complete({**listing, "ttlMs": CACHE_TTL_MS, "cacheScope": "public"})
            self._result(request_id, listing)
            return
        if method == "tools/call":
            self._start_call(request_id, params, modern=bool(modern))
            return
        self._error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")
```

  In `_start_call(self, request_id, params, *, modern: bool = False)`, wrap each `result` dict passed to `self._result` with `self._complete(...)` when `modern` is true: the success, `ToolError`, and internal-error branches.

  In `serve()`, after the read loop ends, send a graceful completion for each open listen before joining the workers:

```text
        with self._inflight_lock:
            listens = list(self._listens)
            self._listens.clear()
        for listen_id in listens:
            self._result(
                listen_id, {"resultType": "complete", "_meta": {_SUBSCRIPTION_KEY: listen_id}}
            )
```

- [ ] **Step 4: Document it.** In `docs/agents/orchestrator-channel.md` § "Tier 1: the MCP server", after "The server never opens a socket (stdio only)…", add:

  > It serves MCP `2026-07-28` statelessly: every request carrying `io.modelcontextprotocol/protocolVersion` in `_meta` gets `resultType` and `serverInfo`, and `server/discover` and `subscriptions/listen` are served. An unknown version gets `-32022` with the supported list. It also serves the legacy `initialize` handshake (2024-11-05 to 2025-11-25), so existing harness registrations keep working.

- [ ] **Step 5: Run the channel suites**

Run: `uv run pytest tests/agents/test_mcp_protocol_eras.py tests/agents/test_mcp_server.py tests/agents/test_mcp_subagent_tools.py tests/agents/test_mcp_orchestrator_tools.py tests/agents/test_mcp_event_channel.py tests/test_startup_imports.py tests/agents/test_dispatch_import_weight.py -q -n auto 2>&1 | tail -6`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/pitwall/agents/mcp_server.py tests/agents/test_mcp_protocol_eras.py tests/agents/mcp_test_client.py docs/agents/orchestrator-channel.md
git commit -s -m "feat(agents): channel MCP server serves 2026-07-28 and the legacy handshake"
```

---

### Task 9: Channel tool surface, plus a doctor probe for both eras (F04, F05 channel, F09 channel, F16, F18)

**Files:**
- Modify: `src/pitwall/agents/mcp_tools.py`:
  - the nine tool dicts: `ASK_TOOL`, `READ_STEERING_TOOL`, `ACK_STEER_TOOL`, `DISPATCH_AND_WAIT_TOOL`, `WAIT_DISPATCH_TOOL`, `ANSWER_AND_WAIT_TOOL`, `STEER_AND_WAIT_TOOL`, `INBOX_TOOL`, `ANSWER_ASK_TOOL`
- Modify: `src/pitwall/agents/mcp_server.py`:
  - `_start_call` (argument check, rate limit, internal error text)
  - new `_TokenBucket`
- Modify: `src/pitwall/agents/doctor.py`: `_probe_channel_server` gains a modern pass; `_channel_checks` compares both
- Create: `tests/agents/test_mcp_tool_surface.py`, `tests/agents/test_doctor_channel_probe.py`

**Interfaces:**
- Produces:
  - `mcp_server.CALLS_PER_SECOND = 5.0`, `CALL_BURST = 20`
  - `doctor._probe_channel_server(env, dispatch_id, *, modern: bool = False) -> list[str] | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_mcp_tool_surface.py
"""Channel tool annotations, argument validation, rate limits, sanitized errors (F04, F05, F09, F16)."""

from __future__ import annotations

import io
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pitwall.agents import mcp_server
from pitwall.agents.mcp_tools import (
    ACK_STEER_TOOL,
    ANSWER_AND_WAIT_TOOL,
    ANSWER_ASK_TOOL,
    ASK_TOOL,
    DISPATCH_AND_WAIT_TOOL,
    INBOX_TOOL,
    READ_STEERING_TOOL,
    STEER_AND_WAIT_TOOL,
    WAIT_DISPATCH_TOOL,
)
from tests.agents.mcp_test_client import McpTestClient

ALL_TOOLS = (
    ASK_TOOL,
    READ_STEERING_TOOL,
    ACK_STEER_TOOL,
    DISPATCH_AND_WAIT_TOOL,
    WAIT_DISPATCH_TOOL,
    ANSWER_AND_WAIT_TOOL,
    STEER_AND_WAIT_TOOL,
    INBOX_TOOL,
    ANSWER_ASK_TOOL,
)


def test_every_channel_tool_is_annotated() -> None:
    for tool in ALL_TOOLS:
        hints = tool["annotations"]
        assert set(hints) >= {"readOnlyHint", "idempotentHint", "openWorldHint"}, tool["name"]
        if hints["readOnlyHint"] is False:
            assert "destructiveHint" in hints, tool["name"]
    assert INBOX_TOOL["annotations"]["readOnlyHint"] is True
    assert READ_STEERING_TOOL["annotations"]["readOnlyHint"] is True
    assert WAIT_DISPATCH_TOOL["annotations"]["readOnlyHint"] is True
    assert DISPATCH_AND_WAIT_TOOL["annotations"]["openWorldHint"] is True
    assert DISPATCH_AND_WAIT_TOOL["annotations"]["destructiveHint"] is True


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[McpTestClient]:
    env = {k: v for k, v in os.environ.items() if k != "PITWALL_AGENTS_CHANNEL_DISPATCH_ID"}
    env["PITWALL_AGENTS_STATE_HOME"] = str(tmp_path)
    c = McpTestClient(env)
    c.initialize()
    try:
        yield c
    finally:
        c.close()


def test_channel_rejects_undeclared_arguments(client: McpTestClient) -> None:
    result = client.call("inbox", {"bogus": 1})["result"]
    assert result["isError"] is True
    assert "dispatch_id" in result["content"][0]["text"]
    assert "bogus" not in result["content"][0]["text"]


def test_internal_errors_do_not_echo_exception_text() -> None:
    out = io.BytesIO()
    server = mcp_server.ChannelServer({}, io.BytesIO(), out)

    def broken(_args: dict[str, Any], _cancel: threading.Event, _progress: Any) -> dict[str, Any]:
        raise RuntimeError("SECRET-PATH /home/x")

    server.register({"name": "inbox", "inputSchema": {"type": "object", "properties": {}}}, broken)
    server._start_call(1, {"name": "inbox", "arguments": {}})
    for worker in server._workers:
        worker.join(timeout=5)
    reply = json.loads(out.getvalue().splitlines()[-1])
    assert reply["result"]["isError"] is True
    assert "SECRET" not in reply["result"]["content"][0]["text"]


def test_channel_rate_limits_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "CALL_BURST", 2)
    monkeypatch.setattr(mcp_server, "CALLS_PER_SECOND", 0.001)
    out = io.BytesIO()
    server = mcp_server.ChannelServer({}, io.BytesIO(), out)
    server.register(
        {"name": "inbox", "inputSchema": {"type": "object", "properties": {}}},
        lambda _a, _c, _p: {"ok": True},
    )
    for request_id in (1, 2, 3):
        server._start_call(request_id, {"name": "inbox", "arguments": {}})
        for worker in server._workers:
            worker.join(timeout=5)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["result"]["isError"] for r in replies] == [False, False, True]
    assert "rate limited" in replies[2]["result"]["content"][0]["text"]
```

```python
# tests/agents/test_doctor_channel_probe.py
"""Doctor proves the channel's modern era as well as the handshake (F18)."""

from __future__ import annotations

import os

from pitwall.agents.doctor import _probe_channel_server


def test_probe_lists_orchestrator_tools_in_both_eras() -> None:
    env = {k: v for k, v in os.environ.items() if k != "PITWALL_AGENTS_CHANNEL_DISPATCH_ID"}
    legacy = _probe_channel_server(env, None)
    modern = _probe_channel_server(env, None, modern=True)
    assert legacy is not None and modern is not None
    assert set(legacy) == set(modern) >= {"inbox", "dispatch_and_wait"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_mcp_tool_surface.py tests/agents/test_doctor_channel_probe.py -q 2>&1 | tail -8`
Expected: FAIL. The tool dicts have no `annotations` key, `bogus` is accepted, the internal error echoes its text, no rate limit applies, and `_probe_channel_server` has no `modern` keyword.

- [ ] **Step 3: Annotate the tools.** Add an `"annotations"` key to each tool dict in `mcp_tools.py`. Use the tool's existing `title` as `annotations.title`.

| Tool | readOnlyHint | destructiveHint | idempotentHint | openWorldHint |
|---|---|---|---|---|
| `ask_orchestrator` | false | false | false | false |
| `read_steering` | true | (omit) | true | false |
| `ack_steer` | false | false | true | false |
| `inbox` | true | (omit) | true | false |
| `answer_ask` | false | false | true | false |
| `dispatch_and_wait` | false | true | false | true |
| `wait_dispatch` | true | (omit) | true | false |
| `answer_and_wait` | false | false | true | true |
| `steer_and_wait` | false | false | false | true |

Example for `INBOX_TOOL`:

```text
    "annotations": {
        "title": "Channel inbox",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
```

  `dispatch_and_wait` is destructive and open-world because it launches an external-model harness that may edit the workspace. `answer_ask` is idempotent because a matching repeat is accepted, and the answer is an exclusive write (`orchestrator-channel.md`, "Broker endpoints").

- [ ] **Step 4: Validate arguments, rate limit, and sanitize in `_start_call`.** In `mcp_server.py`:

```python
CALLS_PER_SECOND = 5.0
CALL_BURST = 20


class _TokenBucket:
    def __init__(self) -> None:
        self._tokens = float(CALL_BURST)
        self._stamp = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                float(CALL_BURST), self._tokens + (now - self._stamp) * CALLS_PER_SECOND
            )
            self._stamp = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False
```

  Add `import time`. Create `self._bucket = _TokenBucket()` in `ChannelServer.__init__`. In `_start_call`, after resolving `entry` and `arguments`, and before creating the cancel event:

```text
        declared = entry[0].get("inputSchema", {}).get("properties", {})
        undeclared = set(arguments) - set(declared)
        if undeclared:
            allowed = ", ".join(sorted(declared)) or "no arguments"
            self._result(
                request_id,
                self._shape(
                    {
                        "content": [{"type": "text", "text": f"unknown argument; allowed: {allowed}"}],
                        "isError": True,
                    },
                    modern,
                ),
            )
            return
        if not self._bucket.take():
            self._result(
                request_id,
                self._shape(
                    {"content": [{"type": "text", "text": "rate limited; retry shortly"}], "isError": True},
                    modern,
                ),
            )
            return
```

  Here `self._shape(result, modern)` returns `self._complete(result) if modern else result`. Add it next to `_complete`, and use it for the three existing result branches from Task 8. In the generic `except Exception` branch, replace `f"internal error: {exc}"` with `"internal error; see the server's stderr log"`. Keep `traceback.print_exc(file=sys.stderr)`.

- [ ] **Step 5: Probe both eras in doctor.** In `doctor._probe_channel_server(env, dispatch_id, *, modern: bool = False)`:
  - Leave the legacy branch exactly as it is.
  - When `modern` is true, skip `initialize` and `notifications/initialized` and send:

```text
            meta = {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "doctor", "version": "0"},
            }
            send({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": meta}})
            discovered = receive(1)
            if discovered is None or "2026-07-28" not in discovered.get("result", {}).get(
                "supportedVersions", []
            ):
                return None
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": meta}})
```

    Then continue into the existing `reply = receive(2)` and tool-name extraction.
  - In `_channel_checks`, compute `orchestrator_tools_modern = _probe_channel_server(env, None, modern=True)` and `subagent_tools_modern = _probe_channel_server(env, "00000000-0000-4000-8000-000000000000", modern=True)`. Require both pairs to match the existing expected sets for `handshake_ok`.
  - Change the PASS summary to "pitwall-channel answers the 2026-07-28 discover/tools path and the legacy handshake with the orchestrator and subagent tools".

- [ ] **Step 6: Run the channel and doctor suites**

Run: `uv run pytest tests/agents -q -n auto -k "mcp or doctor" 2>&1 | tail -6 && uv run python -m pitwall.agents doctor --installation-only --json >/dev/null && echo doctor-ok`
Expected: all pass, then `doctor-ok`.

- [ ] **Step 7: Commit**

```bash
git add src/pitwall/agents/mcp_tools.py src/pitwall/agents/mcp_server.py src/pitwall/agents/doctor.py tests/agents/test_mcp_tool_surface.py tests/agents/test_doctor_channel_probe.py
git commit -s -m "fix(agents): channel tools annotated, arguments validated, calls rate limited, doctor probes both eras"
```

---

### Task 10: Relay drops non-MCP output and uses an application error code (F12, F15)

**Files:**
- Modify: `src/pitwall/mcp/relay.py`: `ERROR_CODE`, `Relay._fail` (`data.retryable`), `Relay._from_child`
- Modify: `tests/mcp/test_relay.py`: new tests, plus the fake server gains a `"junk"` method

**Interfaces:**
- Produces: `relay.ERROR_CODE = -31010`. The `_fail` payload gains `data.retryable = True`.

- [ ] **Step 1: Write the failing tests.** In `tests/mcp/test_relay.py`, extend `FAKE_SERVER` inside its loop, before the `"id" in msg` branch:

```text
    if msg.get("method") == "junk":
        print("not json", flush=True)
```

  Then append:

```python
async def test_relay_drops_non_json_child_output(tmp_path: Path) -> None:
    count = tmp_path / "count"
    client = await _relay(tmp_path, sys.executable, "-c", FAKE_SERVER, str(count))
    await client.send({"jsonrpc": "2.0", "id": 1, "method": "junk"})
    reply = await client.recv()
    assert reply == {"jsonrpc": "2.0", "id": 1, "result": {"generation": 1, "method": "junk"}}


async def test_relay_errors_use_the_application_code(tmp_path: Path) -> None:
    client = await _relay(tmp_path, str(tmp_path / "missing-binary"), wait="0.2")
    await client.send({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    reply = await client.recv()
    assert reply["error"]["code"] == -31010
    assert reply["error"]["data"] == {"error": "mcp_server_unavailable", "retryable": True}


async def test_relay_forwards_modern_request_without_initialize(tmp_path: Path) -> None:
    from tests.mcp.raw_stdio import BROKER_ENV, MODERN_META

    env = {**os.environ, **BROKER_ENV, "PITWALL_MCP_RELAY_WAIT_SECONDS": "30"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pitwall.mcp.relay",
        "--",
        sys.executable,
        "-m",
        "pitwall.mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=env,
    )
    client = _Client(proc)
    await client.send(
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": MODERN_META}}
    )
    reply = await client.recv()
    assert reply["result"]["resultType"] == "complete"
    assert "2026-07-28" in reply["result"]["supportedVersions"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/mcp/test_relay.py -q -p no:randomly 2>&1 | tail -8`
Expected:
- `test_relay_drops_non_json_child_output` fails: `recv` gets `not json` and raises `JSONDecodeError`.
- The code test fails: `-32004 != -31010`.
- The modern pass-through test passes, because Task 2 is in place. Keep it as the regression pin for Review Focus item 1.

- [ ] **Step 3: Implement.** In `relay.py`:
  - Set `ERROR_CODE = -31010  # application-defined; outside the JSON-RPC reserved range (MCP 2026-07-28)`.
  - In `_fail`, set `"data": {"error": error, "retryable": True}`.
  - In `_from_child`, replace the `except ValueError: message = None` branch so that non-JSON lines are logged and dropped:

```text
            try:
                message = json.loads(line)
            except ValueError:
                self._log("pitwall mcp relay: dropped a non-JSON line from the server")
                continue
```

- [ ] **Step 4: Run the relay suite**

Run: `uv run pytest tests/mcp/test_relay.py -q 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/mcp/relay.py tests/mcp/test_relay.py
git commit -s -m "fix(mcp): relay drops non-JSON server output and uses an application error code"
```

---

### Task 11: `serve broker --json` keeps stdout for the protocol (F14)

**Files:**
- Modify: `src/pitwall/cli/mcp.py`: `cmd_mcp` broker branch
- Modify: `tests/cli/test_mcp_serve.py`: new test
- Modify: `docs/sdlc/18-cli.md:722`

**Interfaces:**
- Produces: `pitwall mcp serve broker --json` writes `{"transport": "stdio"}` to **stderr**. Stdout carries only MCP messages.

- [ ] **Step 1: Write the failing test.** Append to `tests/cli/test_mcp_serve.py`:

```python
def test_serve_broker_json_never_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    from pitwall import mcp

    with patch("pitwall.mcp.ensure_runtime_env"), patch.object(mcp.mcp, "run"):
        assert cli.main(["mcp", "serve", "broker", "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert '"transport": "stdio"' in captured.err
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/cli/test_mcp_serve.py -q 2>&1 | tail -5`
Expected: FAIL, because `captured.out` contains the JSON.

- [ ] **Step 3: Implement.** In `cmd_mcp`, replace the `out = Output(...)` … `sys.stdout.flush()` block with:

```text
    from pitwall.mcp import ensure_runtime_env, mcp

    transport = args.transport
    ensure_runtime_env()
    if _json_mode(args):
        # stdout belongs to the MCP stdio transport (2026-07-28 stdio binding: nothing else
        # may be written there), so the machine-readable start record goes to stderr.
        print(json.dumps({"transport": transport}, indent=2), file=sys.stderr, flush=True)
    mcp.run(transport=transport)
    return 0
```

  Add `import json`, and drop the now-unused `Output` import if ruff flags it.

- [ ] **Step 4: Update the doc.** Replace `docs/sdlc/18-cli.md:722` with:

  > - `pitwall mcp serve broker` calls `ensure_runtime_env()` from `pitwall.mcp` (validates the required env vars for the MCP runtime) and then `mcp.run(transport="stdio")`. With `--json` it writes `{"transport": "stdio"}` to stderr, because stdout carries only MCP messages.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/cli/test_mcp_serve.py -q 2>&1 | tail -3 && make docs-check 2>&1 | tail -1`
Expected: all pass, then `markdown links passed`.

- [ ] **Step 6: Commit**

```bash
git add src/pitwall/cli/mcp.py tests/cli/test_mcp_serve.py docs/sdlc/18-cli.md
git commit -s -m "fix(cli): serve broker --json writes its start record to stderr"
```

---

### Task 12: Grok registration (TOML managed block) (C1)

**Files:**
- Modify: `src/pitwall/agents/capability_inventory.py` (`CHANNEL_HARNESSES`, `channel_config_path`)
- Modify: `src/pitwall/agents/mcp_registration.py` (`render_entry`, `_plan_codex` → `_plan_toml`, `plan_registration`, `current_entry`)
- Test: `tests/agents/test_mcp_registration.py`

**Interfaces:**
- Produces: `mcp_registration._TOML_HARNESSES = ("codex", "grok")`; `_plan_toml(harness: str, path: Path, command: str, *, remove: bool) -> SyncPlan`; `channel_config_path("grok", env, home) == home / ".grok" / "config.toml"`.

- [ ] **Step 1: Write the failing tests** (add to `RegistrationTests`)

```text
    def test_grok_block_is_valid_toml_idempotent_and_removable(self) -> None:
        config = self.home / ".grok" / "config.toml"
        config.parent.mkdir()
        config.write_text('[mcp_servers.docs]\ncommand = "docs"\nargs = []\n', encoding="utf-8")
        original = config.read_text(encoding="utf-8")
        plan = self._apply("grok")
        self.assertEqual(config, plan.path)
        entry = tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]["pitwall-channel"]
        self.assertEqual({"command": CMD, "args": ARGS, "enabled": True}, entry)
        self.assertTrue(mcp_channel_registered("grok", self.env, self.home))
        self.assertFalse(plan_registration("grok", self.env, self.home, command=CMD).changed)
        self._apply("grok", remove=True)
        self.assertEqual(original, config.read_text(encoding="utf-8"))
        self.assertFalse(mcp_channel_registered("grok", self.env, self.home))

    def test_grok_reformatted_block_resyncs_to_one_table(self) -> None:
        config = self.home / ".grok" / "config.toml"
        config.parent.mkdir()
        self._apply("grok")
        text = config.read_text(encoding="utf-8")
        # `grok mcp add` rewrites arrays one item per line inside our markers.
        config.write_text(
            text.replace('args = ["mcp", "serve", "channel"]', 'args = [\n    "mcp",\n    "serve",\n    "channel",\n]'),
            encoding="utf-8",
        )
        self._apply_command("grok", "/new/pitwall")
        rewritten = config.read_text(encoding="utf-8")
        self.assertEqual(1, rewritten.count("[mcp_servers.pitwall-channel]"))
        entry = tomllib.loads(rewritten)["mcp_servers"]["pitwall-channel"]
        self.assertEqual("/new/pitwall", entry["command"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/agents/test_mcp_registration.py -q -p no:randomly -k grok 2>&1 | tail -5`
Expected: 2 failed, each with `KeyError: 'grok'` raised by `plan_registration`.

- [ ] **Step 3: Implement**

In `capability_inventory.py`, extend the tuple and the path lookup:

```python
CHANNEL_HARNESSES = (
    "claude",
    "codex",
    "copilot",
    "opencode",
    "kimi",
    "cline",
    "qwen",
    "zcode",
    "grok",
)
```

```text
    if harness_id == "grok":
        # `grok mcp add --scope user` writes this file.
        return home / ".grok" / "config.toml"
```

In `mcp_registration.py`, add below `_JSON_SECTION`:

```python
_TOML_HARNESSES = ("codex", "grok")
```

Add a branch to `render_entry`, before the `codex` branch:

```text
    if harness == "grok":
        # Grok starts stdio servers with the parent environment (verified with an
        # env-recording server through `grok mcp doctor`), so no env table is needed.
        return (
            "\n".join(
                [
                    _BEGIN,
                    f"[mcp_servers.{CHANNEL_SERVER_NAME}]",
                    f"command = {json.dumps(command)}",
                    "args = " + json.dumps(list(CHANNEL_ARGS)),
                    "enabled = true",
                    _END,
                ]
            )
            + "\n"
        )
```

Rename `_plan_codex(path, command, *, remove)` to `_plan_toml(harness, path, command, *, remove)`. Inside it, replace `render_entry("codex", command)` with `render_entry(harness, command)` and `SyncPlan("codex", ...)` with `SyncPlan(harness, ...)`. In `plan_registration` replace the codex branch with:

```text
    if harness in _TOML_HARNESSES:
        return _plan_toml(harness, channel_config_path(harness, env, home), command, remove=remove)
```

In `current_entry` replace `if harness == "codex":` with `if harness in _TOML_HARNESSES:`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_mcp_registration.py tests/agents/test_migrate.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/capability_inventory.py src/pitwall/agents/mcp_registration.py tests/agents/test_mcp_registration.py
git commit -s -m "feat(agents): register pitwall-channel for Grok"
```

---

### Task 13: Antigravity and Muse registration (JSON) (C1)

**Files:**
- Modify: `src/pitwall/agents/capability_inventory.py` (`CHANNEL_HARNESSES`, `channel_config_path`)
- Modify: `src/pitwall/agents/mcp_registration.py` (`_JSON_SECTION`, `render_entry`, `_plan_json`)
- Test: `tests/agents/test_mcp_registration.py`

**Interfaces:**
- Consumes: Task 12's `CHANNEL_HARNESSES` tuple.
- Produces: `channel_config_path("agy", ...) == home / ".gemini" / "config" / "mcp_config.json"`; `channel_config_path("muse", ...) == ${XDG_CONFIG_HOME:-~/.config}/muse/settings.json`.

- [ ] **Step 1: Write the failing tests**

```text
    def test_agy_and_muse_entries(self) -> None:
        expected = {
            "agy": (
                self.home / ".gemini" / "config" / "mcp_config.json",
                {"command": CMD, "args": ARGS, "disabled": False},
            ),
            "muse": (
                self.home / ".config" / "muse" / "settings.json",
                {
                    "command": CMD,
                    "args": ARGS,
                    "env_vars": list(FORWARDED_ENV),
                    "startup_timeout_sec": 30,
                },
            ),
        }
        for harness, (path, entry) in expected.items():
            with self.subTest(harness=harness):
                plan = self._apply(harness)
                self.assertEqual(path, plan.path)
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(entry, data["mcpServers"]["pitwall-channel"])
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
                self.assertTrue(mcp_channel_registered(harness, self.env, self.home))
                self.assertFalse(plan_registration(harness, self.env, self.home, command=CMD).changed)
                self._apply(harness, remove=True)
                self.assertFalse(mcp_channel_registered(harness, self.env, self.home))

    def test_muse_settings_gain_schema_version_and_keep_keys(self) -> None:
        fresh = self._apply("muse")
        self.assertEqual(1, json.loads(fresh.path.read_text(encoding="utf-8"))["schema_version"])
        fresh.path.write_text('{"model": "muse-spark-1.3", "tui": {"theme": "dark"}}', encoding="utf-8")
        self._apply("muse")
        data = json.loads(fresh.path.read_text(encoding="utf-8"))
        self.assertEqual(
            (1, "muse-spark-1.3", {"theme": "dark"}),
            (data["schema_version"], data["model"], data["tui"]),
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/agents/test_mcp_registration.py -q -p no:randomly -k "agy or muse" 2>&1 | tail -5`
Expected: 2 failed with `KeyError: 'agy'` / `KeyError: 'muse'`.

- [ ] **Step 3: Implement**

`capability_inventory.py`: append `"agy", "muse"` to `CHANNEL_HARNESSES`, and add to `channel_config_path`:

```text
    if harness_id == "agy":
        # `agy mcp add` writes user-scope servers here.
        return home / ".gemini" / "config" / "mcp_config.json"
    if harness_id == "muse":
        return _path("${XDG_CONFIG_HOME:-~/.config}/muse/settings.json", env, home)
```

`mcp_registration.py`: add `"agy": "mcpServers"` and `"muse": "mcpServers"` to `_JSON_SECTION`, and add to `render_entry`:

```text
    if harness == "agy":
        # The shape `agy mcp add` writes. No env block: an unexpanded `${VAR}` would mark the
        # server misconfigured, and Task 32 proves the parent environment reaches the server.
        return {"command": command, "args": list(CHANNEL_ARGS), "disabled": False}
    if harness == "muse":
        # Muse forwards named parent variables through env_vars, as Codex does.
        return {
            "command": command,
            "args": list(CHANNEL_ARGS),
            "env_vars": list(FORWARDED_ENV),
            "startup_timeout_sec": 30,
        }
```

In `_plan_json`, in the registration (not removal) branch, before `_set_section(...)`:

```text
        if harness == "muse":
            # Muse rejects settings without a schema version.
            data.setdefault("schema_version", 1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_mcp_registration.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/capability_inventory.py src/pitwall/agents/mcp_registration.py tests/agents/test_mcp_registration.py
git commit -s -m "feat(agents): register pitwall-channel for Antigravity and Muse"
```

---

### Task 14: Hermes and goose registration (YAML managed line) (C1)

**Files:**
- Create: `src/pitwall/agents/yaml_channel.py`
- Modify: `src/pitwall/agents/capability_inventory.py` (`CHANNEL_HARNESSES`, new `CHANNEL_YAML_SECTION`, `channel_config_path`, `mcp_channel_registered`)
- Modify: `src/pitwall/agents/mcp_registration.py` (`render_entry`, new `_plan_yaml` and `_with_command`, `plan_registration`, `current_entry`)
- Test: `tests/agents/test_mcp_registration.py`

**Interfaces:**
- Produces: `yaml_channel.read_entry(text: str, section: str, name: str) -> dict[str, Any] | None`; `yaml_channel.plan_text(before: str, section: str, name: str, entry: Mapping[str, Any] | None, is_ours: Callable[[Mapping[str, Any]], bool]) -> str`; `yaml_channel.YamlChannelError(ValueError)`; `capability_inventory.CHANNEL_YAML_SECTION = {"hermes": "mcp_servers", "goose": "extensions"}`. `current_entry("goose", ...)` returns the entry with `command` copied from goose's `cmd`, so `doctor._channel_checks` and `managed_channel._registered_channel_argv` work unchanged.

- [ ] **Step 1: Write the failing tests**

Add `current_entry` to the test module's `pitwall.agents.mcp_registration` import, then:

```text
    def test_hermes_entry_is_one_managed_line_under_mcp_servers(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        original = (
            "model:\n  default: glm-5.2\n\n"
            "mcp_servers:\n  docs:\n    command: docs\n    args: []\n\n"
            'providers:\n  # managed by pitwall\n  local:\n    base_url: "http://127.0.0.1:8000/v1"\n'
        )
        config.write_text(original, encoding="utf-8")
        self._apply("hermes")
        text = config.read_text(encoding="utf-8")
        self.assertIn('  # managed by pitwall\n  pitwall-channel: {"command": ', text)
        self.assertLess(text.index("pitwall-channel"), text.index("providers:"))
        self.assertEqual(
            {
                "command": CMD,
                "args": ARGS,
                "env": {v: "${" + v + "}" for v in FORWARDED_ENV},
                "timeout": 3660,
            },
            current_entry("hermes", self.env, self.home),
        )
        self.assertTrue(mcp_channel_registered("hermes", self.env, self.home))
        self.assertFalse(plan_registration("hermes", self.env, self.home, command=CMD).changed)
        self._apply("hermes", remove=True)
        self.assertEqual(original, config.read_text(encoding="utf-8"))
        self.assertFalse(mcp_channel_registered("hermes", self.env, self.home))

    def test_goose_entry_and_block_style_rewrite(self) -> None:
        plan = self._apply("goose")
        self.assertEqual(self.home / ".config" / "goose" / "config.yaml", plan.path)
        entry = current_entry("goose", self.env, self.home)
        assert entry is not None
        self.assertEqual(
            ("stdio", CMD, CMD, ARGS, [], 3660),
            (entry["type"], entry["cmd"], entry["command"], entry["args"], entry["env_keys"], entry["timeout"]),
        )
        # `goose configure` rewrites config.yaml in block style and drops comments.
        plan.path.write_text(
            "extensions:\n  pitwall-channel:\n    enabled: true\n    type: stdio\n"
            f"    cmd: {CMD}\n    args:\n    - mcp\n    - serve\n    - channel\n"
            "    timeout: 3660\nGOOSE_MODEL: x\n",
            encoding="utf-8",
        )
        self.assertTrue(mcp_channel_registered("goose", self.env, self.home))
        self._apply_command("goose", "/new/pitwall")
        rewritten = current_entry("goose", self.env, self.home)
        assert rewritten is not None
        self.assertEqual("/new/pitwall", rewritten["cmd"])
        self.assertEqual(1, plan.path.read_text(encoding="utf-8").count("pitwall-channel:"))
        self._apply("goose", remove=True)
        self.assertEqual("GOOSE_MODEL: x\n", plan.path.read_text(encoding="utf-8"))

    def test_yaml_foreign_entry_is_refused(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        config.write_text("mcp_servers:\n  pitwall-channel:\n    command: someone-else\n", encoding="utf-8")
        with self.assertRaises(RegistrationError):
            plan_registration("hermes", self.env, self.home, command=CMD)

    def test_yaml_inline_section_is_refused(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        config.write_text("mcp_servers: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(RegistrationError, "not a block mapping"):
            plan_registration("hermes", self.env, self.home, command=CMD)

    def test_yaml_without_trailing_newline_is_refused(self) -> None:
        config = self.home / ".hermes" / "config.yaml"
        config.parent.mkdir()
        config.write_text("model:\n  default: glm-5.2", encoding="utf-8")
        with self.assertRaisesRegex(RegistrationError, "newline"):
            plan_registration("hermes", self.env, self.home, command=CMD)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/agents/test_mcp_registration.py -q -p no:randomly -k "hermes or goose or yaml" 2>&1 | tail -5`
Expected: 5 failed with `KeyError: 'hermes'` / `KeyError: 'goose'`.

- [ ] **Step 3: Create `src/pitwall/agents/yaml_channel.py`**

```python
"""The pitwall-channel entry of a YAML harness config, kept as one managed line.

JSON is valid YAML, so ``  pitwall-channel: {...}`` under a top-level block mapping loads in
Hermes and goose and reads back here with :mod:`json`. The agents package carries no YAML
parser (see ``capability_inventory._read_yaml``); this module edits only the entry it owns and,
when needed, the section header above it. goose rewrites its whole config in block style when it
saves settings, so a block-style entry is read back too.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

MARKER = "  # managed by pitwall\n"
_TOP_LEVEL = re.compile(r"^[^\s#-][^:]*:(?:\s|$)")


class YamlChannelError(ValueError):
    """The YAML file cannot be edited safely."""


def _section(lines: list[str], section: str) -> tuple[int, int] | None:
    """``(header, end)`` of the top-level block mapping *section*, or None when absent."""
    header = f"{section}:"
    starts = [i for i, line in enumerate(lines) if line.split("#", 1)[0].rstrip() == header]
    if len(starts) > 1:
        raise YamlChannelError(f"duplicate top-level {section!r} sections")
    if not starts:
        if any(line.startswith(header) for line in lines):
            raise YamlChannelError(f"top-level {section!r} is not a block mapping")
        return None
    start = starts[0]
    for index in range(start + 1, len(lines)):
        if _TOP_LEVEL.match(lines[index]):
            return start, index
    return start, len(lines)


def _entry_span(lines: list[str], start: int, end: int, name: str) -> tuple[int, int, int] | None:
    """``(first, key, finish)`` of the *name* entry: its marker, key line, and children."""
    key = f"  {name}:"
    for index in range(start + 1, end):
        line = lines[index]
        if not line.startswith(key) or line[len(key) : len(key) + 1] not in ("", " ", "\n"):
            continue
        first = index - 1 if lines[index - 1] == MARKER else index
        finish = index + 1
        while finish < end and (lines[finish].startswith("   ") or not lines[finish].strip()):
            finish += 1
        while finish > index + 1 and not lines[finish - 1].strip():
            finish -= 1
        return first, index, finish
    return None


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _block_entry(children: list[str]) -> dict[str, Any]:
    """The scalar and list-of-scalar keys directly under a block-style entry."""
    indents = [len(line) - len(line.lstrip(" ")) for line in children if line.strip()]
    if not indents:
        return {}
    base = min(indents)
    entry: dict[str, Any] = {}
    items: list[str] | None = None
    for line in children:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("- "):
            if items is not None:
                items.append(_scalar(text[2:]))
            continue
        if len(line) - len(line.lstrip(" ")) != base:
            continue
        key, separator, rest = text.partition(":")
        if not separator:
            continue
        if rest.strip():
            entry[key] = _scalar(rest)
            items = None
        else:
            items = []
            entry[key] = items
    return entry


def read_entry(text: str, section: str, name: str) -> dict[str, Any] | None:
    """The *name* entry under *section*: ``{}`` when present but unreadable, None when absent."""
    lines = text.splitlines(keepends=True)
    try:
        bounds = _section(lines, section)
    except YamlChannelError:
        return None
    if bounds is None:
        return None
    span = _entry_span(lines, *bounds, name)
    if span is None:
        return None
    _, key, finish = span
    inline = lines[key].split(":", 1)[1].strip()
    if not inline:
        return _block_entry(lines[key + 1 : finish])
    try:
        value = json.loads(inline)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def plan_text(
    before: str,
    section: str,
    name: str,
    entry: Mapping[str, Any] | None,
    is_ours: Callable[[Mapping[str, Any]], bool],
) -> str:
    """*before* with the *name* entry set to *entry*, or removed when *entry* is None."""
    if before and not before.endswith("\n"):
        raise YamlChannelError("the file must end with a newline before it can be edited safely")
    lines = before.splitlines(keepends=True)
    bounds = _section(lines, section)
    span = _entry_span(lines, *bounds, name) if bounds is not None else None
    if span is None and entry is None:
        return before
    if span is not None:
        existing = read_entry(before, section, name) or {}
        if not is_ours(existing):
            raise YamlChannelError(
                f"an existing {name} entry is not managed by pitwall; rename or remove it"
            )
        first, _, finish = span
        del lines[first:finish]
        bounds = _section(lines, section)
    if entry is None:
        if bounds is not None and not any(
            line.strip() for line in lines[bounds[0] + 1 : bounds[1]]
        ):
            del lines[bounds[0] : bounds[1]]
            while lines and not lines[-1].strip() and bounds[0] >= len(lines):
                lines.pop()
        return "".join(lines)
    rendered = [MARKER, f"  {name}: {json.dumps(dict(entry))}\n"]
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend([f"{section}:\n", *rendered])
    else:
        start, end = bounds
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = rendered
    return "".join(lines)
```

- [ ] **Step 4: Wire the harnesses**

`capability_inventory.py`: append `"hermes", "goose"` to `CHANNEL_HARNESSES`; add below `CHANNEL_NESTED_SECTION`:

```python
#: YAML harnesses and the top-level section that holds their MCP servers.
CHANNEL_YAML_SECTION: dict[str, str] = {"hermes": "mcp_servers", "goose": "extensions"}
```

Add to `channel_config_path`:

```text
    if harness_id == "hermes":
        return _path("${HERMES_HOME:-~/.hermes}/config.yaml", env, home)
    if harness_id == "goose":
        return _path("${XDG_CONFIG_HOME:-~/.config}/goose/config.yaml", env, home)
```

In `mcp_channel_registered`, after the `CHANNEL_NESTED_SECTION` branch:

```text
    yaml_section = CHANNEL_YAML_SECTION.get(harness_id)
    if yaml_section is not None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            return False
        return read_entry(text, yaml_section, CHANNEL_SERVER_NAME) is not None
```

with `from .yaml_channel import read_entry` at the top of the module, and `"CHANNEL_YAML_SECTION"` added to `__all__`.

`mcp_registration.py`: import `CHANNEL_YAML_SECTION` with the other `capability_inventory` names and `from . import yaml_channel`. Add to `render_entry`:

```text
    if harness == "hermes":
        # Hermes starts stdio servers with an allowlisted environment plus the entry's env,
        # expanding ${VAR} there (verified with `hermes mcp test`). timeout is per tool call.
        return {
            "command": command,
            "args": list(CHANNEL_ARGS),
            "env": {name: "${" + name + "}" for name in FORWARDED_ENV},
            "timeout": TOOL_TIMEOUT_S,
        }
    if harness == "goose":
        # goose starts stdio extensions with the parent environment (verified with
        # `goose mcp-probe`); env_keys stays empty so an unset variable never blocks loading.
        return {
            "enabled": True,
            "type": "stdio",
            "name": CHANNEL_SERVER_NAME,
            "cmd": command,
            "args": list(CHANNEL_ARGS),
            "envs": {},
            "env_keys": [],
            "timeout": TOOL_TIMEOUT_S,
            "bundled": None,
        }
```

Add the plan function and the `cmd` normalizer:

```python
def _with_command(entry: Mapping[str, Any]) -> dict[str, Any]:
    """goose names the executable ``cmd``; expose it as ``command`` like every other harness."""
    normalized = dict(entry)
    if "command" not in normalized and "cmd" in normalized:
        normalized["command"] = normalized["cmd"]
    return normalized


def _plan_yaml(harness: str, path: Path, command: str, *, remove: bool) -> SyncPlan:
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        after = yaml_channel.plan_text(
            before,
            CHANNEL_YAML_SECTION[harness],
            CHANNEL_SERVER_NAME,
            None if remove else render_entry(harness, command),
            lambda existing: _is_ours(_with_command(existing)),
        )
    except yaml_channel.YamlChannelError as exc:
        raise RegistrationError(f"{path}: {exc}") from exc
    return SyncPlan(harness, path, before, after, (), ())
```

In `plan_registration`, before the final JSON return:

```text
    if harness in CHANNEL_YAML_SECTION:
        return _plan_yaml(harness, channel_config_path(harness, env, home), command, remove=remove)
```

In `current_entry`, before the JSON read, and add `UnicodeDecodeError` to its except tuple:

```text
        if harness in CHANNEL_YAML_SECTION:
            found = yaml_channel.read_entry(
                path.read_text(encoding="utf-8"), CHANNEL_YAML_SECTION[harness], CHANNEL_SERVER_NAME
            )
            return _with_command(found) if found is not None else None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_mcp_registration.py tests/agents/test_capability_inventory.py tests/agents/test_install.py tests/agents/test_doctor.py tests/agents/test_migrate.py tests/agents/test_dispatch_import_weight.py tests/test_startup_imports.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed. `install`, `doctor`, and `migrate` loop over `CHANNEL_HARNESSES`, so these suites prove the five new harnesses flow through them.

- [ ] **Step 6: Commit**

```bash
git add src/pitwall/agents/yaml_channel.py src/pitwall/agents/capability_inventory.py src/pitwall/agents/mcp_registration.py tests/agents/test_mcp_registration.py
git commit -s -m "feat(agents): register pitwall-channel for Hermes and goose"
```

---

### Task 15: Codex and Copilot skills teach the managed path (C2)

**Files:**
- Modify: `plugins/codex/skills/subagent-model-routing/SKILL.md` ("Asking and steering through the orchestrator channel", first paragraph)
- Modify: `plugins/copilot/skills/subagent-model-routing/SKILL.md` (same section)
- Test: `tests/agents/test_parity.py::ParityTests.test_channel_discipline_is_taught_everywhere`

- [ ] **Step 1: Write the failing test.** In `test_channel_discipline_is_taught_everywhere`, add `"dispatch_and_wait"` and `"answer_and_wait"` to the `needles` tuple.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/agents/test_parity.py -q -p no:randomly -k channel_discipline 2>&1 | tail -5`
Expected: FAIL for `host=codex` and `host=copilot`: `'dispatch_and_wait' not found`.

- [ ] **Step 3: Implement.** In both skills, replace the section's first paragraph ("A dispatched model can ask you a blocking question mid-task instead of guessing. Opt in per dispatch with ...") with:

```markdown
A dispatched model can ask you a blocking question mid-task instead of guessing. For routed work,
call the managed `dispatch_and_wait` tool on the `pitwall-channel` MCP server (`pitwall agents setup mcp`
registers it for this host). It starts the child with the channel on and returns the child's next ask
or its terminal result; answer with `answer_and_wait`, keep waiting with `wait_dispatch`, and redirect
with `steer_and_wait`. A shim you run yourself (`*-shim.sh`, `route-shim.sh`) is non-interactive: its
child hears nothing about the channel unless you pass `--routing-ask-support` (cap it with
`--routing-max-asks N`; the default is 5), and it can then only pause on the file contract (exit 75)
until you run `pitwall agents runs resume`. Prefer `--routing-workspace isolated` for ask-prone shim
work, so a paused run resumes against its own worktree.
```

- [ ] **Step 4: Run the parity suite**

Run: `uv run pytest tests/agents/test_parity.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add plugins/codex/skills/subagent-model-routing/SKILL.md plugins/copilot/skills/subagent-model-routing/SKILL.md tests/agents/test_parity.py
git commit -s -m "docs(agents): Codex and Copilot routing skills use dispatch_and_wait"
```

---

### Task 16: One terminal-state set and one pid helper module (R14b)

**Files:**
- Create: `src/pitwall/agents/pids.py`
- Modify: `src/pitwall/agents/run_store.py` (new `TERMINAL_STATES`)
- Modify: `src/pitwall/agents/dispatch.py:54` (delete the local set; import it)
- Modify: `src/pitwall/agents/managed_channel.py:58` (delete the local set; import it) and `:179-252` (move `_process_identity`, `_process_state`, `_pid_alive` out)
- Test: `tests/agents/test_run_store.py`

**Interfaces:**
- Produces: `run_store.TERMINAL_STATES: frozenset[str]` = `{"preflight_failed", "succeeded", "failed", "timed_out", "cancelled"}`; `pids.process_identity(pid: int) -> str | None`; `pids.pid_alive(pid: int, identity: str | None) -> bool`; `pids._process_state(pid: int) -> str | None`. `managed_channel` keeps the names `_process_identity`, `_process_state`, `_pid_alive`, and `TERMINAL_STATES` as re-exports, so `migrate.py` and `tests/agents/test_managed_channel_edges.py` keep working.

- [ ] **Step 1: Write the failing tests** (add `import os` to the module imports)

```text
    def test_terminal_states_have_one_definition(self) -> None:
        from pitwall.agents import dispatch, managed_channel, migrate, run_store

        self.assertEqual(
            frozenset({"preflight_failed", "succeeded", "failed", "timed_out", "cancelled"}),
            run_store.TERMINAL_STATES,
        )
        for module in (dispatch, managed_channel, migrate):
            with self.subTest(module=module.__name__):
                self.assertIs(run_store.TERMINAL_STATES, module.TERMINAL_STATES)

    def test_pid_helpers_live_in_one_module(self) -> None:
        from pitwall.agents import managed_channel, pids

        self.assertIs(pids.pid_alive, managed_channel._pid_alive)
        self.assertIs(pids.process_identity, managed_channel._process_identity)
        self.assertTrue(pids.pid_alive(os.getpid(), pids.process_identity(os.getpid())))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_run_store.py -q -p no:randomly -k "one_definition or one_module" 2>&1 | tail -5`
Expected: 2 failed (`AttributeError: module 'pitwall.agents.run_store' has no attribute 'TERMINAL_STATES'`; `ModuleNotFoundError: No module named 'pitwall.agents.pids'`).

- [ ] **Step 3: Implement**

`run_store.py`, below `utc_now`:

```python
#: States after which a dispatch never changes again. ``paused`` is not terminal.
TERMINAL_STATES = frozenset({"preflight_failed", "succeeded", "failed", "timed_out", "cancelled"})
```

`dispatch.py`: delete line 54 (`TERMINAL_STATES = {..., "blocked"}`; no transition reaches `blocked`) and add `TERMINAL_STATES` to the `from .run_store import ...` line.

`managed_channel.py`: delete line 58, add `TERMINAL_STATES` to the `from .run_store import (...)` block. Move `_process_identity`, `_process_state`, and `_pid_alive` (lines 179-252) unchanged into the new `src/pitwall/agents/pids.py`, renaming the first and last to `process_identity` and `pid_alive`, with the imports they use and this docstring: `"""Process liveness by pid plus start identity, shared by the run store and the managed launcher."""`. In `pid_alive`, update the internal call to `process_identity`. In `managed_channel.py` add:

```python
from .pids import _process_state as _process_state
from .pids import pid_alive as _pid_alive
from .pids import process_identity as _process_identity
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_run_store.py tests/agents/test_managed_channel_edges.py tests/agents/test_migrate.py tests/agents/test_dispatch_import_weight.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/pids.py src/pitwall/agents/run_store.py src/pitwall/agents/dispatch.py src/pitwall/agents/managed_channel.py tests/agents/test_run_store.py
git commit -s -m "refactor(agents): one terminal-state set and one pid helper module"
```

---

### Task 17: The steering gate exempts Codex's channel tool names (R1)

**Files:**
- Modify: `src/pitwall/agents/steer_gate.py:is_channel_tool`
- Test: `tests/agents/test_steer_gate.py`

- [ ] **Step 1: Write the failing test** (add to the class that holds `test_blocking_steer_denies_other_tools_until_acked`)

```text
    def test_codex_underscore_tool_names_are_exempt(self) -> None:
        # Codex replaces the hyphen in the server name with an underscore.
        self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        for name in (
            "mcp__pitwall_channel__read_steering",
            "mcp__pitwall_channel__ack_steer",
            "mcp__pitwall_channel__ask_orchestrator",
        ):
            with self.subTest(tool=name):
                self.assertIsNone(decide({"tool_name": name}, self.env))
        self.assertIsNotNone(decide({"tool_name": "mcp__other_server__read_steering"}, self.env))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/agents/test_steer_gate.py -q -p no:randomly -k underscore 2>&1 | tail -5`
Expected: FAIL in the `mcp__pitwall_channel__read_steering` subtest: a deny decision is not None.

- [ ] **Step 3: Implement**

```python
def is_channel_tool(tool_name: str) -> bool:
    # Claude keeps the server name (`mcp__pitwall-channel__read_steering`); Codex turns its
    # hyphen into an underscore (`mcp__pitwall_channel__read_steering`). Match both.
    lowered = tool_name.lower().replace("-", "_")
    return "pitwall_channel" in lowered and lowered.endswith(CHANNEL_TOOLS)
```

- [ ] **Step 4: Run the gate suites**

Run: `uv run pytest tests/agents/test_steer_gate.py tests/agents/test_steer_gate_fail_closed.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/steer_gate.py tests/agents/test_steer_gate.py
git commit -s -m "fix(agents): steering gate exempts Codex's pitwall_channel tool names"
```

---

### Task 18: Record the supervisor and reconcile abandoned standalone runs (R2)

**Files:**
- Modify: `src/pitwall/agents/dispatch.py:Lifecycle._write`
- Modify: `src/pitwall/agents/run_store.py` (new `PIDLESS_ABANDON_SECONDS`, `newest_mtime`, `abandoned_reason`, `finalize_abandoned`, `reconcile_run`; call in `cleanup_runs`)
- Modify: `src/pitwall/agents/migrate.py` (`_newest_mtime` → import `newest_mtime`)
- Modify: `src/pitwall/agents/cli.py` (`_runs_list`, `_runs_show`, `_runs_stop`)
- Test: `tests/agents/test_run_store.py`, `tests/agents/test_steer_cli.py`

**Interfaces:**
- Consumes: Task 16's `TERMINAL_STATES` and `pids.pid_alive`, `pids.process_identity`.
- Produces: `run.json` gains `"supervisor": {"pid": int, "pidStartIdentity": str | None}`; `run_store.abandoned_reason(env: Mapping[str, str], run_dir: Path, now: float | None = None) -> str | None`; `run_store.finalize_abandoned(env, run_dir: Path, reason: str) -> None` (writes `abandoned.json` and appends a `failed` transition); `run_store.reconcile_run(env, run_dir: Path) -> str | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/agents/test_run_store.py` (add `import subprocess`, `import sys`, `import time`, and `abandoned_reason`, `reconcile_run` to the `run_store` import):

```text
    def _run(
        self,
        env: dict[str, str],
        dispatch_id: str,
        *,
        state: str = "running",
        supervisor: dict[str, object] | None = None,
        age_seconds: float = 0.0,
    ) -> RunStore:
        store = RunStore.create(env, dispatch_id)
        document: dict[str, object] = {
            "schemaVersion": 1,
            "dispatchId": dispatch_id,
            "state": state,
            "transitions": [{"state": state, "timestamp": "2026-09-28T00:00:00.000Z"}],
        }
        if supervisor is not None:
            document["supervisor"] = supervisor
        store.write_json("run.json", document)
        if age_seconds:
            stamp = time.time() - age_seconds
            for path in (store.path, *store.path.iterdir()):
                os.utime(path, (stamp, stamp))
        return store

    def test_dead_supervisor_run_is_recorded_failed_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            child = subprocess.Popen([sys.executable, "-c", "pass"])
            child.wait()
            store = self._run(
                env,
                "00000000-0000-4000-8000-0000000000b1",
                supervisor={"pid": child.pid, "pidStartIdentity": "exited"},
            )
            reason = reconcile_run(env, store.path)
            assert reason is not None
            self.assertIn(str(child.pid), reason)
            document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
            self.assertEqual(
                ("failed", "failed"), (document["state"], document["transitions"][-1]["state"])
            )
            abandoned = json.loads(store.artifact("abandoned.json").read_text(encoding="utf-8"))
            self.assertEqual(reason, abandoned["reason"])
            self.assertIsNone(reconcile_run(env, store.path))

    def test_live_recent_paused_and_managed_runs_are_never_abandoned(self) -> None:
        from pitwall.agents.pids import process_identity

        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            me = {"pid": os.getpid(), "pidStartIdentity": process_identity(os.getpid())}
            day = 24 * 3600
            live = self._run(env, "00000000-0000-4000-8000-0000000000b2", supervisor=me, age_seconds=30 * day)
            recent = self._run(env, "00000000-0000-4000-8000-0000000000b3")
            paused = self._run(env, "00000000-0000-4000-8000-0000000000b4", state="paused", age_seconds=2 * day)
            managed = self._run(env, "00000000-0000-4000-8000-0000000000b5", age_seconds=2 * day)
            (state_root(env) / "launches" / managed.path.name).mkdir(parents=True)
            stale = self._run(env, "00000000-0000-4000-8000-0000000000b6", age_seconds=25 * 3600)
            for store in (live, recent, paused, managed):
                with self.subTest(run=store.path.name):
                    self.assertIsNone(abandoned_reason(env, store.path))
            self.assertIn("24 hours", abandoned_reason(env, stale.path) or "")

    def test_lifecycle_records_its_supervisor(self) -> None:
        from pitwall.agents.dispatch import Lifecycle
        from pitwall.agents.events import EventEmitter

        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            store = RunStore.create(env, "00000000-0000-4000-8000-0000000000b7")
            Lifecycle(store, "codex", "m", EventEmitter(store, harness="codex", model="m"))
            document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
            self.assertEqual(os.getpid(), document["supervisor"]["pid"])
```

In `tests/agents/test_steer_cli.py` (add `import json` if absent):

```text
    def test_runs_stop_on_an_abandoned_run_records_the_failure(self) -> None:
        child = subprocess.Popen(["/bin/true"])
        child.wait()
        document = json.loads(self.store.artifact("run.json").read_text(encoding="utf-8"))
        document["supervisor"] = {"pid": child.pid, "pidStartIdentity": "exited"}
        self.store.write_json("run.json", document)
        result = self._cli("runs", "stop", DISPATCH_ID)
        self.assertEqual(1, result.returncode)
        self.assertIn("was abandoned", result.stderr)
        state = json.loads(self.store.artifact("run.json").read_text(encoding="utf-8"))["state"]
        self.assertEqual("failed", state)
        self.assertFalse((self.store.path / "mailbox" / "steer").exists())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_run_store.py tests/agents/test_steer_cli.py -q -p no:randomly -k "abandoned or supervisor" 2>&1 | tail -5`
Expected: a collection error for `tests/agents/test_run_store.py` (`ImportError: cannot import name 'abandoned_reason'`) and 1 failure in `tests/agents/test_steer_cli.py` (`runs stop` exits 0).

- [ ] **Step 3: Implement**

`dispatch.py:Lifecycle._write`: import `process_identity` from `.pids` and add the key after `"transitions"`:

```text
                # Lets another process tell a live run from one whose supervisor was
                # killed (run_store.abandoned_reason). A resumed attempt records its own.
                "supervisor": {
                    "pid": os.getpid(),
                    "pidStartIdentity": process_identity(os.getpid()),
                },
```

`run_store.py` (add `import time`):

```python
#: A run that recorded no supervisor pid is judged abandoned only after this long without a write.
PIDLESS_ABANDON_SECONDS = 24 * 3600.0


def newest_mtime(run_dir: Path) -> float:
    """The latest modification time of a run directory or any file directly in it."""
    newest = 0.0
    for path in (run_dir, *run_dir.iterdir()):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def abandoned_reason(env: Mapping[str, str], run_dir: Path, now: float | None = None) -> str | None:
    """Why a non-terminal standalone run has no live supervisor, or None while it may be alive.

    Managed runs are judged by their launcher sidecar (``managed_channel``), and paused runs
    have no supervisor by design, so neither is ever abandoned here.
    """
    try:
        document = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(document, dict):
        return None
    state = document.get("state")
    if not isinstance(state, str) or state in TERMINAL_STATES or state == "paused":
        return None
    if (state_root(env) / "launches" / run_dir.name).is_dir():
        return None
    supervisor = document.get("supervisor")
    pid = supervisor.get("pid") if isinstance(supervisor, dict) else None
    if isinstance(pid, int) and not isinstance(pid, bool):
        from .pids import pid_alive

        identity = supervisor.get("pidStartIdentity") if isinstance(supervisor, dict) else None
        if pid_alive(pid, identity if isinstance(identity, str) else None):
            return None
        return f"supervisor pid {pid} exited without recording a terminal state"
    moment = time.time() if now is None else now
    if moment - newest_mtime(run_dir) < PIDLESS_ABANDON_SECONDS:
        return None
    return "no supervisor pid was recorded and the run has not changed for 24 hours"


def finalize_abandoned(env: Mapping[str, str], run_dir: Path, reason: str) -> None:
    """Record an abandoned run as failed, keeping the reason in ``abandoned.json``."""
    store = RunStore(state_root(env), run_dir.name)
    document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
    if document.get("state") in TERMINAL_STATES:
        return
    stamp = utc_now()
    store.write_json("abandoned.json", {"schemaVersion": 1, "reason": reason, "detectedAt": stamp})
    transitions = document.get("transitions")
    document["state"] = "failed"
    document["transitions"] = [
        *(transitions if isinstance(transitions, list) else []),
        {"state": "failed", "timestamp": stamp},
    ]
    store.write_json("run.json", document)


def reconcile_run(env: Mapping[str, str], run_dir: Path) -> str | None:
    """Record *run_dir* as failed when its supervisor is gone; return the reason, else None."""
    reason = abandoned_reason(env, run_dir)
    if reason is not None:
        finalize_abandoned(env, run_dir, reason)
    return reason
```

In `cleanup_runs`, first statement inside the `for path in list_runs(env):` loop:

```text
        try:
            reconcile_run(env, path)
        except OSError, ValueError:
            pass
```

`migrate.py`: delete `_newest_mtime` and import `newest_mtime as _newest_mtime` from `.run_store`.

`cli.py`: in `_runs_list`, first statement inside the loop, and in `_runs_show`, right after `find_run`:

```text
        try:
            reconcile_run(os.environ, path)
        except OSError, ValueError:
            pass
```

In `_runs_stop`, after the `state` is read and before the `paused` check:

```text
    reason = reconcile_run(os.environ, run_path)
    if reason is not None:
        print(
            f"pitwall agents: run {run_path.name} was abandoned ({reason}); recorded as failed",
            file=sys.stderr,
        )
        return 1
```

Import `reconcile_run` from `.run_store` in `cli.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_run_store.py tests/agents/test_steer_cli.py tests/agents/test_migrate.py tests/agents/test_inbox_cli.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/dispatch.py src/pitwall/agents/run_store.py src/pitwall/agents/migrate.py src/pitwall/agents/cli.py tests/agents/test_run_store.py tests/agents/test_steer_cli.py
git commit -s -m "fix(agents): record the supervisor pid and reconcile abandoned standalone runs"
```

---

### Task 19: A managed wait never orphans a live standalone run (R4)

**Files:**
- Modify: `src/pitwall/agents/managed_channel.py:_next_event` (the `launcher_alive = _launcher_alive(env, dispatch_id)` statement)
- Test: `tests/agents/test_managed_channel_edges.py`

**Interfaces:**
- Consumes: Task 18's `run_store.reconcile_run`.

- [ ] **Step 1: Write the failing tests** (add `import json`; import `RunStore` is already present)

```text
    def _standalone_run(self, supervisor: dict[str, object]) -> RunStore:
        dispatch_id = str(uuid.uuid4())
        store = RunStore.create(self.env, dispatch_id)
        store.write_json(
            "run.json",
            {"schemaVersion": 1, "dispatchId": dispatch_id, "state": "running", "supervisor": supervisor},
        )
        return store

    def test_live_standalone_run_is_never_orphaned(self) -> None:
        store = self._standalone_run(
            {"pid": os.getpid(), "pidStartIdentity": _process_identity(os.getpid())}
        )
        event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=3)
        self.assertEqual("still_running", event["event"])
        self.assertFalse((store.path / "orphan.json").exists())

    def test_standalone_run_with_a_dead_supervisor_is_orphaned_and_failed(self) -> None:
        child = subprocess.Popen(["/bin/true"])
        child.wait()
        store = self._standalone_run({"pid": child.pid, "pidStartIdentity": "exited"})
        event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=3)
        self.assertEqual("orphan", event["event"])
        document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", document["state"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_managed_channel_edges.py -q -p no:randomly -k standalone 2>&1 | tail -5`
Expected: `test_live_standalone_run_is_never_orphaned` fails with `'orphan' != 'still_running'`; the dead-supervisor test fails on `'running' != 'failed'`.

- [ ] **Step 3: Implement.** Replace `launcher_alive = _launcher_alive(env, dispatch_id)` with:

```text
        if run_doc is not None and not _launch_path(env, dispatch_id).is_dir():
            # A standalone dispatch has no launcher to watch. Its wait ends early only
            # when its own supervisor is gone (run_store.reconcile_run records the failure).
            reason = reconcile_run(env, run_dir)
            if reason is not None:
                _raise_if_cancelled(cancel)
                orphan = _orphan_payload(dispatch_id, reason=reason)
                _write_orphan(env, dispatch_id, orphan)
                return orphan
            launcher_alive = True
        else:
            launcher_alive = _launcher_alive(env, dispatch_id)
```

Import `reconcile_run` from `.run_store`.

- [ ] **Step 4: Run the managed suites**

Run: `uv run pytest tests/agents/test_managed_channel_edges.py tests/agents/test_managed_route_pin.py tests/agents/test_scheduler_wait.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/managed_channel.py tests/agents/test_managed_channel_edges.py
git commit -s -m "fix(agents): a managed wait never orphans a live standalone run"
```

---

### Task 20: Empty output with exit 0 is a failure for OpenCode (R3)

**Files:**
- Modify: `src/pitwall/agents/harnesses/base.py` (`HarnessAdapter.empty_stdout_is_failure`)
- Modify: `src/pitwall/agents/harnesses/opencode.py`
- Modify: `src/pitwall/agents/dispatch.py:_LegacyDispatch.apply_soft_denial`
- Create: `tests/agents/test_empty_output.py`

**Interfaces:**
- Produces: `HarnessAdapter.empty_stdout_is_failure: bool = False`; `OpenCodeAdapter.empty_stdout_is_failure = True`.

- [ ] **Step 1: Write the failing test file**

```python
"""A zero exit with nothing on stdout is a failure for harnesses that always print an answer."""

from __future__ import annotations

import json
import unittest
from typing import Any

from tests.agents.shim_test_support import ShimSandbox


class EmptyOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.addCleanup(self.sandbox.cleanup)

    def _dispatch(self, harness: str, stdout: str) -> tuple[int, dict[str, Any]]:
        self.sandbox.install_harness(harness)
        prompt = str(self.sandbox.prompt())
        args = ["test-provider/test-model", prompt] if harness == "opencode" else [prompt]
        result = self.sandbox.run(harness, args, env=self.sandbox.environment(FAKE_STDOUT=stdout))
        (run,) = self.sandbox.run_directories()
        return result.returncode, json.loads((run / "result.json").read_text(encoding="utf-8"))

    def test_opencode_empty_stdout_is_exit_77(self) -> None:
        code, result = self._dispatch("opencode", "")
        self.assertEqual((77, "failed"), (code, result["status"]))

    def test_opencode_with_an_answer_still_succeeds(self) -> None:
        code, result = self._dispatch("opencode", "an answer\n")
        self.assertEqual((0, "succeeded"), (code, result["status"]))

    def test_codex_keeps_exit_0_on_empty_stdout(self) -> None:
        code, result = self._dispatch("codex", "")
        self.assertEqual((0, "succeeded"), (code, result["status"]))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/agents/test_empty_output.py -q -p no:randomly 2>&1 | tail -5`
Expected: `test_opencode_empty_stdout_is_exit_77` fails with `(0, 'succeeded') != (77, 'failed')`; the other two pass.

- [ ] **Step 3: Implement.** In `base.py`, next to `prompt_delivery`:

```text
    #: True for a harness that always prints its answer: exit 0 with nothing on stdout
    #: means it did no work, and dispatch records exit 77 instead.
    empty_stdout_is_failure = False
```

In `opencode.py`, next to its `prompt_delivery`:

```text
    # `opencode run` prints the model's answer; an empty stdout with exit 0 is a stalled
    # free-tier model (70 such runs on 2026-09-28/29), not a result.
    empty_stdout_is_failure = True
```

In `dispatch.py:apply_soft_denial`, after `soft_denial` is computed:

```text
        if (
            soft_denial is None
            and self.adapter.empty_stdout_is_failure
            and result.stdout_bytes == 0
        ):
            soft_denial = "exited 0 without writing anything to stdout; recording exit 77"
```

- [ ] **Step 4: Run the tests and the shim contract**

Run: `uv run pytest tests/agents/test_empty_output.py tests/agents/test_shim_contract.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/harnesses/base.py src/pitwall/agents/harnesses/opencode.py src/pitwall/agents/dispatch.py tests/agents/test_empty_output.py
git commit -s -m "fix(agents): OpenCode exit 0 with empty stdout is recorded as a failure"
```

---

### Task 21: An aborted run never reports exit 0 (R5)

**Files:**
- Modify: `src/pitwall/agents/process.py:run_process` (the `exit_code` expression)
- Test: `tests/agents/test_process.py`

- [ ] **Step 1: Write the failing tests**

```text
    def test_abort_of_a_child_that_exits_zero_on_sigterm_reports_143(self) -> None:
        import threading

        abort = threading.Event()
        threading.Timer(0.3, abort.set).start()
        script = (
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "time.sleep(30)\n"
        )
        result = run_process(
            [sys.executable, "-c", script],
            env=dict(os.environ),
            stdin=None,
            stdout_path=self.root / "o",
            stderr_path=self.root / "e",
            timeout_seconds=60,
            cwd=self.root,
            abort_event=abort,
            abort_grace_seconds=5,
        )
        self.assertEqual((True, False, 143), (result.aborted, result.killed, result.exit_code))

    def test_unaborted_zero_exit_stays_zero(self) -> None:
        result = run_process(
            [sys.executable, "-c", "pass"],
            env=dict(os.environ),
            stdin=None,
            stdout_path=self.root / "o",
            stderr_path=self.root / "e",
            timeout_seconds=60,
            cwd=self.root,
        )
        self.assertEqual((False, 0), (result.aborted, result.exit_code))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_process.py -q -p no:randomly -k "exits_zero or stays_zero" 2>&1 | tail -5`
Expected: `test_abort_of_a_child_that_exits_zero_on_sigterm_reports_143` fails with `(True, False, 0) != (True, False, 143)`.

- [ ] **Step 3: Implement.** Replace the `exit_code = (...)` expression:

```text
    exit_code = (
        124
        if timed_out
        else 130
        if cancelled
        else 128 + child_signal
        if child_signal
        # A child that exits 0 when told to stop (Codex does) was still cancelled.
        else 128 + int(signal.SIGTERM)
        if aborted and return_code == 0
        else return_code
    )
```

- [ ] **Step 4: Run the process and pause suites**

Run: `uv run pytest tests/agents/test_process.py tests/agents/test_pause_contract.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/process.py tests/agents/test_process.py
git commit -s -m "fix(agents): an aborted run reports 143 even when the child exits 0"
```

---

### Task 22: Steers to a run without the channel are refused, except stop (R6)

**Files:**
- Modify: `src/pitwall/agents/channel.py:send_steer`
- Modify: `src/pitwall/agents/managed_channel.py:steer_once`
- Modify: `src/pitwall/agents/cli.py:_runs_stop` (message)
- Test: `tests/agents/test_steer_cli.py`, `tests/agents/test_managed_channel_edges.py`

**Interfaces:**
- Produces: `steer_once(...)` returns the steer plus `"delivery": "abort-at-deadline"` for a `stop` steer to a run without `channel.json`.

- [ ] **Step 1: Write the failing tests.** In `tests/agents/test_steer_cli.py`, import `ChannelConfig, write_channel_config` from `pitwall.agents.channel` and add to the end of `setUp`, so the existing tests keep a channel run:

```text
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, tier="1"))
```

Then add:

```text
    def test_steer_is_refused_for_a_run_without_the_channel(self) -> None:
        self.store.artifact("channel.json").unlink()
        result = self._cli("steer", DISPATCH_ID, "--kind", "scope", "--message", "SQLite only")
        self.assertEqual(1, result.returncode)
        self.assertIn("without the orchestrator channel", result.stderr)
        self.assertFalse((self.store.path / "mailbox" / "steer").exists())
        stop = self._cli("runs", "stop", DISPATCH_ID)
        self.assertEqual(0, stop.returncode)
        self.assertIn("no orchestrator channel", stop.stdout)
```

In `tests/agents/test_managed_channel_edges.py` (import `steer_once` and `ManagedChannelError` from `pitwall.agents.managed_channel`):

```text
    def test_managed_steer_without_channel_accepts_only_stop(self) -> None:
        store = RunStore.create(self.env, self.dispatch_id)
        store.write_json("run.json", {"schemaVersion": 1, "state": "running"})
        with self.assertRaisesRegex(ManagedChannelError, "without the orchestrator channel"):
            steer_once(self.env, self.dispatch_id, kind="note", message="hello")
        stop = steer_once(self.env, self.dispatch_id, kind="stop", message="wrap up")
        self.assertEqual("abort-at-deadline", stop["delivery"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_steer_cli.py tests/agents/test_managed_channel_edges.py -q -p no:randomly -k "without_the_channel or without_channel" 2>&1 | tail -5`
Expected: 2 failed (the steer exits 0; no `ManagedChannelError` raised).

- [ ] **Step 3: Implement.** In `channel.py:send_steer`, after the `_STEERABLE_STATES` check:

```text
    if kind != "stop" and load_channel_config(run_path) is None:
        raise MailboxError(
            f"run {run_path.name} was dispatched without the orchestrator channel, so it cannot "
            f"read a {kind} steer; only `runs stop` applies, and it aborts the run at its deadline"
        )
```

In `managed_channel.py:steer_once`, after the `STEERABLE_STATES` check:

```text
    has_channel = load_channel_config(run_dir) is not None
    if not has_channel and kind != "stop":
        raise ManagedChannelError(
            f"run {dispatch_id} was dispatched without the orchestrator channel, so it cannot "
            f"read a {kind} steer; only a stop steer applies, and it aborts the run at its deadline"
        )
```

and replace the final `return steer` with:

```text
    return steer if has_channel else {**steer, "delivery": "abort-at-deadline"}
```

In `cli.py:_runs_stop`, replace the final `print(...)` with:

```text
    from .channel import load_channel_config

    if load_channel_config(run_path) is None:
        print(
            f"stop requested: steer {steer['steer_id']}; the run has no orchestrator channel, "
            f"so it is aborted in {grace}s"
        )
    else:
        print(
            f"stop requested: steer {steer['steer_id']}; the run is aborted if it has not "
            f"wrapped up within {grace}s"
        )
```

- [ ] **Step 4: Run the steering suites**

Run: `uv run pytest tests/agents/test_steer_cli.py tests/agents/test_managed_channel_edges.py tests/agents/test_channel_steer_integration.py tests/agents/test_steer_log_ordering.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/channel.py src/pitwall/agents/managed_channel.py src/pitwall/agents/cli.py tests/agents/test_steer_cli.py tests/agents/test_managed_channel_edges.py
git commit -s -m "fix(agents): refuse steers a channel-less run can never read"
```

---

### Task 23: Report steers still unacknowledged at exit; the inbox skips finished runs (R7)

**Files:**
- Modify: `src/pitwall/agents/channel.py` (`SteerWatcher.report_unacked_at_exit`, `ledger_fields`, `read_channel_inbox`)
- Modify: `src/pitwall/agents/dispatch.py:_LegacyDispatch._write_terminal` (the terminal `emit`)
- Modify: `src/pitwall/agents/managed_channel.py:_next_event` (terminal payload)
- Create: `tests/agents/test_unacked_at_exit.py`

**Interfaces:**
- Consumes: Task 16's `run_store.TERMINAL_STATES`.
- Produces: `SteerWatcher.report_unacked_at_exit() -> list[str]`; terminal event `data.unackedSteerIds`; ledger row `unackedSteerIds` (only when the run has a mailbox); managed terminal payload `unacked_steer_ids`.

- [ ] **Step 1: Write the failing test file**

```python
"""Blocking steers still unacknowledged when a run ends are reported, and the inbox forgets finished runs."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from pitwall.agents.channel import (
    ChannelConfig,
    SteerWatcher,
    read_channel_inbox,
    write_channel_config,
)
from pitwall.agents.events import EventEmitter
from pitwall.agents.run_store import RunStore

DISPATCH_ID = "00000000-0000-4000-8000-0000000000c1"


class UnackedAtExitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.env = {"PITWALL_AGENTS_STATE_HOME": str(Path(self._temp.name))}
        self.store = RunStore.create(self.env, DISPATCH_ID)
        self.store.write_json("run.json", {"schemaVersion": 1, "state": "running"})
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, tier="1"))

    def _events(self, name: str) -> list[dict[str, Any]]:
        lines = self.store.artifact("events.jsonl").read_text(encoding="utf-8").splitlines()
        return [event for event in map(json.loads, lines) if event["event"] == name]

    def test_pending_blocking_steer_is_reported_once_at_exit(self) -> None:
        steer = self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        watcher = SteerWatcher(
            self.store, EventEmitter(self.store, harness="codex", model="m"), threading.Event()
        )
        self.assertEqual([steer["steer_id"]], watcher.report_unacked_at_exit())
        watcher.report_unacked_at_exit()
        (event,) = self._events("steer.unacked")
        self.assertEqual(
            (steer["steer_id"], True), (event["data"]["steerId"], event["data"]["atExit"])
        )

    def test_all_runs_inbox_skips_finished_runs(self) -> None:
        self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        self.store.write_json("run.json", {"schemaVersion": 1, "state": "succeeded"})
        self.assertEqual([], read_channel_inbox(self.env, None)["steers"])
        self.assertEqual(1, len(read_channel_inbox(self.env, DISPATCH_ID)["steers"]))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/agents/test_unacked_at_exit.py -q -p no:randomly 2>&1 | tail -5`
Expected: 2 failed (`AttributeError: 'SteerWatcher' object has no attribute 'report_unacked_at_exit'`; the all-runs inbox lists the steer).

- [ ] **Step 3: Implement.** Add to `SteerWatcher`:

```text
    def report_unacked_at_exit(self) -> list[str]:
        """Emit ``steer.unacked`` once for every blocking steer still unacked as the run ends."""
        from .mailbox import Mailbox

        box = Mailbox(self.store.path, self.store.dispatch_id)
        if not (box.root / "steer").is_dir():
            return []
        pending = box.unacked_steers()
        for steer in pending:
            if steer["steer_id"] not in self._reported:
                self._reported.add(steer["steer_id"])
                self.emitter.emit(
                    "steer.unacked",
                    {"steerId": steer["steer_id"], "kind": steer["kind"], "atExit": True},
                )
        return [steer["steer_id"] for steer in pending]
```

In `ledger_fields`, add to the returned dict:

```text
        "unackedSteerIds": [steer["steer_id"] for steer in box.unacked_steers()],
```

In `read_channel_inbox`, first statement inside `for path in runs:`:

```text
        if dispatch_id is None and _finished(path):
            continue
```

with this module-level helper:

```python
def _finished(run_dir: Path) -> bool:
    """True for a terminal run; the all-runs inbox lists only live and paused runs."""
    import json as _json

    from .run_store import TERMINAL_STATES

    try:
        state = _json.loads((run_dir / "run.json").read_text(encoding="utf-8")).get("state")
    except OSError, ValueError, AttributeError:
        return False
    return state in TERMINAL_STATES
```

In `dispatch.py:_write_terminal`, replace the final `self.emitter.emit(terminal_event, {...})`:

```text
        unacked = self.watcher.report_unacked_at_exit()
        terminal_data: dict[str, Any] = {"exitCode": result.exit_code, "outcome": outcome}
        if unacked:
            terminal_data["unackedSteerIds"] = unacked
        self.emitter.emit(terminal_event, terminal_data)
```

In `managed_channel.py:_next_event`, inside `if result is not None:` after `payload` is built (import `Mailbox` from `.mailbox`):

```text
            if (run_dir / "mailbox").is_dir():
                payload["unacked_steer_ids"] = [
                    steer["steer_id"] for steer in Mailbox(run_dir, dispatch_id).unacked_steers()
                ]
```

- [ ] **Step 4: Run the channel suites**

Run: `uv run pytest tests/agents/test_unacked_at_exit.py tests/agents/test_channel_ledger.py tests/agents/test_inbox_cli.py tests/agents/test_channel_steer_integration.py tests/agents/test_managed_channel_edges.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/channel.py src/pitwall/agents/dispatch.py src/pitwall/agents/managed_channel.py tests/agents/test_unacked_at_exit.py
git commit -s -m "fix(agents): report steers still unacknowledged at exit; inbox skips finished runs"
```

---

### Task 24: Claude prompts go to stdin (R8)

**Files:**
- Modify: `src/pitwall/agents/harnesses/claude.py` (`prompt_delivery`, `prepare`)
- Modify: `src/pitwall/agents/resources/config/harness-registry.json` (`harnesses.claude.promptDelivery`) and its generated copy
- Modify: `tests/agents/test_shim_contract.py` (the two `claude` delivery expectations)
- Create: `tests/agents/test_claude_stdin.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Claude Code receives its prompt on stdin, so prompt size is not bounded by one argv entry."""

from __future__ import annotations

import unittest

from tests.agents.shim_test_support import ShimSandbox


class ClaudeStdinTests(unittest.TestCase):
    def test_a_200_kib_prompt_is_delivered_on_stdin(self) -> None:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        sandbox.install_harness("claude")
        text = "x" * (200 * 1024) + "\n"
        result = sandbox.run("claude", [str(sandbox.prompt(text))])
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        self.assertEqual(text.encode(), sandbox.captured_stdin())
        arguments = sandbox.captured_args()
        self.assertNotIn(text.rstrip("\n"), arguments)
        self.assertIn("-p", arguments)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/agents/test_claude_stdin.py -q -p no:randomly 2>&1 | tail -5`
Expected: FAIL with `64 != 0` and "prompt is 204801 bytes; claude takes its prompt as a command-line argument".

- [ ] **Step 3: Implement.** In `claude.py`, set `prompt_delivery = "stdin"` and end `prepare` with:

```text
        args.extend(["--output-format", "text"])
        # `claude -p` reads the prompt from standard input, so a prompt is not bounded by
        # the 128 KiB single-argument limit (nine Opus dispatches failed on it on 2026-10-05).
        return PreparedCommand([binary, *args], dict(env), prompt, self.sanitize_args(args))
```

In `harness-registry.json`, change `harnesses.claude.promptDelivery` from `"argv"` to `"stdin"`, then run `uv run python tools/agents/sync_routes.py`. In `tests/agents/test_shim_contract.py`, add `"claude"` to the `{"codex", "goose", "opencode"}` set in both `test_*` methods that branch on delivery (lines 227 and 341), and delete the two `elif shim == "claude":` branches (lines 230-232 and 343-344).

- [ ] **Step 4: Run the delivery and registry suites**

Run: `uv run pytest tests/agents/test_claude_stdin.py tests/agents/test_shim_contract.py tests/agents/test_registry.py tests/agents/test_workflow_all_harnesses.py -q -p no:randomly 2>&1 | tail -3 && uv run python tools/agents/check_generated.py && echo GENERATED-OK`
Expected: all passed, then `GENERATED-OK`.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/harnesses/claude.py src/pitwall/agents/resources/ tests/agents/test_claude_stdin.py tests/agents/test_shim_contract.py
git commit -s -m "fix(agents): deliver Claude prompts on stdin"
```

---

### Task 25: A flag is never taken as the prompt source (R9)

**Files:**
- Modify: `src/pitwall/agents/dispatch.py:_LegacyDispatch.parse_request`
- Create: `tests/agents/test_prompt_source_flags.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Flags in the prompt-source position are usage, never a run (and never a ledger row)."""

from __future__ import annotations

import unittest

from tests.agents.shim_test_support import ShimSandbox


class PromptSourceFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.sandbox.install_harness("codex")

    def test_help_prints_usage_and_creates_no_run(self) -> None:
        result = self.sandbox.run("codex", ["--help"])
        self.assertEqual(0, result.returncode)
        self.assertIn(b"usage:", result.stdout)
        self.assertEqual([], self.sandbox.run_directories())
        self.assertEqual([], self.sandbox.ledger_records())

    def test_flag_first_is_a_usage_error_without_a_run(self) -> None:
        result = self.sandbox.run("codex", ["-m", "gpt-6-sol", str(self.sandbox.prompt())])
        self.assertEqual(64, result.returncode)
        self.assertIn(b"is a flag, not a prompt source", result.stderr)
        self.assertEqual([], self.sandbox.run_directories())
        self.assertEqual([], self.sandbox.ledger_records())

    def test_stdin_prompt_source_still_dispatches(self) -> None:
        result = self.sandbox.run("codex", ["-"], input_bytes=b"hello\n")
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        self.assertEqual(1, len(self.sandbox.run_directories()))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/agents/test_prompt_source_flags.py -q -p no:randomly 2>&1 | tail -5`
Expected: the `--help` and `-m` tests fail (exit 66 and one run directory each); the stdin test passes.

- [ ] **Step 3: Implement.** In `parse_request`, right after `self.request = self.adapter.parse(forwarded, env, self.home)`:

```text
            source, model = self.request.source, self.request.model
            if source in {"-h", "--help"}:
                print(self.adapter.usage())
                _emit_sentinel(0, leading_newline=False)
                return 0
            for label, value in (("prompt source", source), ("model", model)):
                if value != "-" and value.startswith("-"):
                    raise UsageError(
                        f"{self.harness_id}-shim: {value!r} is a flag, not a {label}; put the "
                        f"prompt file (or - for stdin) first\n{self.adapter.usage()}"
                    )
```

- [ ] **Step 4: Run the tests and the shim contract**

Run: `uv run pytest tests/agents/test_prompt_source_flags.py tests/agents/test_shim_contract.py tests/agents/test_shim_receipt.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/dispatch.py tests/agents/test_prompt_source_flags.py
git commit -s -m "fix(agents): a flag in the prompt position is usage, not a run"
```

---

### Task 26: Managed launches stop storing child output twice (R10)

**Files:**
- Modify: `src/pitwall/agents/run_store.py` (new `MANAGED_LAUNCH_ENV`)
- Modify: `src/pitwall/agents/process.py` (`_pump`, `run_process` terminal fds may be None)
- Modify: `src/pitwall/agents/dispatch.py` (`_DISPATCHER_IDENTITY`, `launch_child`)
- Modify: `src/pitwall/agents/managed_channel.py:start_dispatch` (`child_env`)
- Test: `tests/agents/test_process.py`, new `tests/agents/test_managed_launch_output.py`

**Interfaces:**
- Produces: `run_store.MANAGED_LAUNCH_ENV = "PITWALL_AGENTS_MANAGED_LAUNCH"`; `run_process(..., terminal_stdout_fd: int | None = 1, terminal_stderr_fd: int | None = 2)`, where None means the child's output goes only to the run logs.

- [ ] **Step 1: Write the failing tests.** In `tests/agents/test_process.py`:

```text
    def test_output_reaches_only_the_logs_without_terminal_fds(self) -> None:
        result = run_process(
            [sys.executable, "-c", "import sys; sys.stdout.write('out'); sys.stderr.write('err')"],
            env=dict(os.environ),
            stdin=None,
            stdout_path=self.root / "o",
            stderr_path=self.root / "e",
            timeout_seconds=60,
            cwd=self.root,
            terminal_stdout_fd=None,
            terminal_stderr_fd=None,
        )
        self.assertEqual(0, result.exit_code)
        self.assertEqual((b"out", b"err"), ((self.root / "o").read_bytes(), (self.root / "e").read_bytes()))
```

New `tests/agents/test_managed_launch_output.py`:

```python
"""A managed launch keeps child output in the run logs only."""

from __future__ import annotations

import unittest

from tests.agents.shim_test_support import ShimSandbox


class ManagedLaunchOutputTests(unittest.TestCase):
    def test_managed_launch_does_not_mirror_child_output(self) -> None:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        sandbox.install_harness("codex")
        env = sandbox.environment(PITWALL_AGENTS_MANAGED_LAUNCH="1", FAKE_STDOUT="the answer\n")
        result = sandbox.run("codex", [str(sandbox.prompt())], env=env)
        self.assertEqual(0, result.returncode)
        self.assertNotIn(b"the answer", result.stdout)
        self.assertIn(b"SHIM-DONE exit=0", result.stdout)
        (run,) = sandbox.run_directories()
        self.assertEqual(b"the answer\n", (run / "stdout.log").read_bytes())
        self.assertNotIn("PITWALL_AGENTS_MANAGED_LAUNCH", sandbox.captured_env())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_process.py tests/agents/test_managed_launch_output.py -q -p no:randomly -k "only_the_logs or mirror" 2>&1 | tail -5`
Expected: the process test fails with `TypeError` (`os.write` on None); the managed test fails because the answer is on the shim's stdout.

- [ ] **Step 3: Implement.** `run_store.py`:

```python
#: Set by the managed launcher for its supervisor: child output goes to the run logs only.
MANAGED_LAUNCH_ENV = "PITWALL_AGENTS_MANAGED_LAUNCH"
```

`process.py`: type `terminal_fd: int | None` in `_pump` and change its mirror write to `if terminal_fd is not None: _write_all(terminal_fd, chunk)`; type `terminal_stdout_fd: int | None = 1` and `terminal_stderr_fd: int | None = 2` in `run_process`.

`dispatch.py`: add `MANAGED_LAUNCH_ENV` to `_DISPATCHER_IDENTITY` (imported from `.run_store`), so a harness and any dispatch it starts never inherit it. In `launch_child`, pass to `run_process`:

```text
                    # A managed launcher already captures the supervisor's own streams
                    # in launches/<id>/; copying child output there too doubled every byte.
                    terminal_stdout_fd=None if self.env.get(MANAGED_LAUNCH_ENV) == "1" else 1,
                    terminal_stderr_fd=None if self.env.get(MANAGED_LAUNCH_ENV) == "1" else 2,
```

`managed_channel.py:start_dispatch`, next to `PITWALL_AGENTS_DISPATCH_ID`: `child_env[MANAGED_LAUNCH_ENV] = "1"` (imported from `.run_store`).

- [ ] **Step 4: Run the process, managed, and import-weight suites**

Run: `uv run pytest tests/agents/test_process.py tests/agents/test_managed_launch_output.py tests/agents/test_managed_channel_edges.py tests/agents/test_dispatch_import_weight.py tests/agents/test_shim_contract.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/run_store.py src/pitwall/agents/process.py src/pitwall/agents/dispatch.py src/pitwall/agents/managed_channel.py tests/agents/test_process.py tests/agents/test_managed_launch_output.py
git commit -s -m "fix(agents): managed launches keep child output in the run logs only"
```

---

### Task 27: Delivery prompts are removed at exit unless retained (R11)

**Files:**
- Modify: `src/pitwall/agents/dispatch.py:_LegacyDispatch._write_terminal` (the `remove_delivery_prompt` condition)
- Modify: `docs/agents/run-records.md` (artifact table)
- Create: `tests/agents/test_delivery_prompt_retention.py`

- [ ] **Step 1: Write the failing test file**

```python
"""prompt.deliver.md lives while a run with ask support can still resume, and no longer."""

from __future__ import annotations

import unittest

from tests.agents.shim_test_support import ShimSandbox


class DeliveryPromptRetentionTests(unittest.TestCase):
    def _run(self, *extra: str) -> bool:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        sandbox.install_harness("codex")
        env = sandbox.environment(PITWALL_AGENTS_ASK_SUPPORT="1")
        result = sandbox.run("codex", [str(sandbox.prompt()), *extra], env=env)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        (run,) = sandbox.run_directories()
        return (run / "prompt.deliver.md").exists()

    def test_removed_after_a_finished_run(self) -> None:
        self.assertFalse(self._run())

    def test_kept_when_retention_was_requested(self) -> None:
        self.assertTrue(self._run("--routing-retain-prompt"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/agents/test_delivery_prompt_retention.py -q -p no:randomly 2>&1 | tail -5`
Expected: `test_removed_after_a_finished_run` fails with `True is not false`.

- [ ] **Step 3: Implement.** In `_write_terminal`, replace the three-part `if (...)` before `store.remove_delivery_prompt()` with:

```text
        # A paused run keeps its delivery prompt for `runs resume` (_finish_paused never
        # gets here); a finished run keeps it only on explicit request.
        if not self.routing.retain_prompt:
            store.remove_delivery_prompt()
```

In `docs/agents/run-records.md`, add after the `prompt.md` row:

```markdown
| `prompt.deliver.md` | The prompt exactly as delivered, including the channel instructions; present while a run with ask support is live or paused, and kept after it ends only with `--routing-retain-prompt` |
```

- [ ] **Step 4: Run the retention, pause, and resume suites**

Run: `uv run pytest tests/agents/test_delivery_prompt_retention.py tests/agents/test_pause_contract.py tests/agents/test_runs_resume.py tests/agents/test_run_store.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/dispatch.py docs/agents/run-records.md tests/agents/test_delivery_prompt_retention.py
git commit -s -m "fix(agents): remove the delivery prompt at exit unless retention was requested"
```

---

### Task 28: Migration leftovers: every legacy branch prefix, moved worktrees, current artifact paths (R12)

**Files:**
- Modify: `src/pitwall/agents/migrate_env.py` (new `LEGACY_BRANCH_PREFIXES`)
- Modify: `src/pitwall/agents/migrate.py:_migrate_worktree_branches`
- Modify: `src/pitwall/agents/managed_channel.py` (`_terminal_payload`, its call in `_next_event`)
- Test: `tests/agents/test_migrate.py`, `tests/agents/test_managed_channel_edges.py`

**Interfaces:**
- Produces: `migrate_env.LEGACY_BRANCH_PREFIXES = ("model-routing/", "pitwall-agent-routing/")`; `_terminal_payload(dispatch_id, result, *, launcher=None, artifacts: Mapping[str, str] | None = None)`.

- [ ] **Step 1: Write the failing tests.** In `tests/agents/test_migrate.py`, give `_add_legacy_dispatch` a `prefix: str = OLD_BRANCH_PREFIX` keyword and use it for the branch it creates and records, then add:

```python
def test_migrate_renames_pitwall_agent_routing_branches(station: Workstation) -> None:
    dispatch_id = "55555555-5555-4555-8555-555555555555"
    repo = _add_legacy_dispatch(station, dispatch_id, prefix="pitwall-agent-routing/")

    assert station.run() == 0, station.errors

    assert _branches(repo) == {"main", NEW_BRANCH_PREFIX + dispatch_id}
    assert _record(station, dispatch_id)["branch"] == NEW_BRANCH_PREFIX + dispatch_id


def test_record_follows_a_moved_worktree_whose_branch_is_gone(station: Workstation) -> None:
    dispatch_id = "66666666-6666-4666-8666-666666666666"
    repo = _add_legacy_dispatch(station, dispatch_id)
    legacy_wt = station.home / ".local/state/subagent-model-routing/worktrees" / dispatch_id
    _git(repo, "worktree", "remove", "--force", str(legacy_wt))
    _git(repo, "branch", "-D", OLD_BRANCH_PREFIX + dispatch_id)
    legacy_wt.mkdir(parents=True)
    (legacy_wt / "unapplied.txt").write_text("work\n", encoding="utf-8")

    assert station.run() == 0, station.errors

    assert _record(station, dispatch_id)["path"] == str(station.state / "worktrees" / dispatch_id)
```

In `tests/agents/test_managed_channel_edges.py`:

```text
    def test_terminal_artifacts_point_at_the_current_run_directory(self) -> None:
        run = self.root / "runs" / self.dispatch_id
        ensure_private_directory(run)
        atomic_write_json(
            run / "result.json",
            {"status": "succeeded", "outcome": "ok", "artifacts": {"result": "/gone/result.json"}},
        )
        atomic_write_json(self.launch / "launcher.json", {"pid": -1})
        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)
        self.assertEqual(str(run / "result.json"), event["artifacts"]["result"])
        self.assertEqual(event["artifacts"], event["receipt"]["artifacts"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_migrate.py tests/agents/test_managed_channel_edges.py -q -p no:randomly -k "pitwall_agent_routing or moved_worktree or current_run_directory" 2>&1 | tail -5`
Expected: 3 failed (branch not renamed; record path unchanged; artifact still `/gone/result.json`).

- [ ] **Step 3: Implement.** `migrate_env.py`:

```python
#: Every branch prefix an earlier release gave a dispatch worktree, oldest first.
LEGACY_BRANCH_PREFIXES = (LEGACY_BRANCH_PREFIX, "pitwall-agent-routing/")
```

`migrate.py:_migrate_worktree_branches` (import `LEGACY_BRANCH_PREFIXES`): replace the `old = ...` line and its `if not isinstance(record, dict) or record.get("branch") != old: continue` check with:

```text
        if not isinstance(record, dict):
            continue
        prefix = next(
            (p for p in LEGACY_BRANCH_PREFIXES if record.get("branch") == p + dispatch_id), None
        )
        if prefix is None:
            continue
        old = prefix + dispatch_id
```

and replace the `elif not has_new:` branch with:

```text
        elif not has_new:
            moved = worktree_path(env, dispatch_id)
            if moved.is_dir() and record.get("path") != str(moved):
                # The branch is gone but the moved worktree still holds unapplied work;
                # point the record at it so `runs discard` and cleanup can find it.
                record["path"] = str(moved)
                atomic_write_bytes(record_path, json.dumps(record, indent=2).encode("utf-8"))
                out(f"updated {dispatch_id}: branch {old} no longer exists; record now names {moved}")
                changed = True
            else:
                out(f"skipped {dispatch_id}: branch {old} no longer exists in {common_dir}")
            continue
```

`managed_channel.py:_terminal_payload`: add the keyword `artifacts: Mapping[str, str] | None = None` and, right after `receipt = dict(result)`:

```text
    if artifacts is not None:
        # result.json stores paths as of writing; a migrated run has moved since.
        receipt["artifacts"] = dict(artifacts)
```

In `_next_event`, pass `artifacts=RunStore(state_root(env), dispatch_id).artifact_summary()` to the `_terminal_payload(...)` call.

- [ ] **Step 4: Run the migrate and managed suites**

Run: `uv run pytest tests/agents/test_migrate.py tests/agents/test_managed_channel_edges.py tests/agents/test_scheduler_wait.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/migrate_env.py src/pitwall/agents/migrate.py src/pitwall/agents/managed_channel.py tests/agents/test_migrate.py tests/agents/test_managed_channel_edges.py
git commit -s -m "fix(agents): migrate every legacy branch prefix; report current artifact paths"
```

---

### Task 29: Launch-guard lock files are removed (R13)

**Files:**
- Modify: `plugins/claude/hooks/launch_guard_markers.py` (new `STALE_LOCK_SECONDS`, `sweep_stale_locks`)
- Modify: `plugins/claude/hooks/launch_guard_leases.py` (`deactivate_marker`, `activate_marker`)
- Create: `tests/agents/test_launch_guard_locks.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Launch-guard lock files do not outlive their sessions."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parents[2] / "plugins" / "claude" / "hooks"


class LaunchGuardLockTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(HOOKS))
        self.addCleanup(sys.path.remove, str(HOOKS))
        self.markers = importlib.import_module("launch_guard_markers")
        self.leases = importlib.import_module("launch_guard_leases")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "routing-sessions"
        self.root.mkdir()
        patcher = mock.patch.dict(os.environ, {"PITWALL_AGENTS_ROUTING_MARKER_DIR": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_session_end_removes_the_lock(self) -> None:
        (self.root / "s1.json").write_text("{}", encoding="utf-8")
        (self.root / ".s1.lock").write_text("", encoding="utf-8")
        self.leases.deactivate_marker({"session_id": "s1"})
        self.assertEqual([], sorted(p.name for p in self.root.iterdir()))

    def test_sweep_removes_only_old_locks_without_a_marker(self) -> None:
        old = time.time() - 8 * 24 * 3600
        for name in ("old", "recent", "live"):
            (self.root / f".{name}.lock").write_text("", encoding="utf-8")
        (self.root / "live.json").write_text("{}", encoding="utf-8")
        for name in ("old", "live"):
            os.utime(self.root / f".{name}.lock", (old, old))
        self.markers.sweep_stale_locks(self.root, time.time())
        self.assertEqual(
            [".live.lock", ".recent.lock", "live.json"], sorted(p.name for p in self.root.iterdir())
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/agents/test_launch_guard_locks.py -q -p no:randomly 2>&1 | tail -5`
Expected: the session-end test fails with `['.s1.lock'] != []`; the sweep test fails with `AttributeError: ... has no attribute 'sweep_stale_locks'`.

- [ ] **Step 3: Implement.** `launch_guard_markers.py`:

```python
#: A lock file whose session marker is gone and that nobody touched for this long is debris.
STALE_LOCK_SECONDS = 7 * 24 * 3600


def sweep_stale_locks(root: Path, now: float) -> None:
    """Remove lock files whose session marker is gone and that are older than a week."""
    for lock in root.glob(".*.lock"):
        session_id = lock.name[1 : -len(".lock")]
        try:
            if marker_path(root, session_id).exists():
                continue
            if now - lock.stat().st_mtime < STALE_LOCK_SECONDS:
                continue
            lock.unlink()
        except OSError:
            continue
```

`launch_guard_leases.py:deactivate_marker`: after the `with marker_lock(...)` block, and inside the same `try`, add `(root / f".{session_id}.lock").unlink(missing_ok=True)`. At the start of `activate_marker`, after `root` is known, call `sweep_stale_locks(root, time.time())` inside `contextlib.suppress(OSError)` (import `sweep_stale_locks`, `time`, and `contextlib` as needed).

- [ ] **Step 4: Run the hook suites and the plugin validator**

Run: `uv run pytest tests/agents/test_launch_guard_locks.py tests/agents/test_claude_tripwires.py -q -p no:randomly 2>&1 | tail -3 && uv run python tools/agents/validate_plugins.py && echo PLUGINS-OK`
Expected: all passed, then `PLUGINS-OK`.

- [ ] **Step 5: Commit**

```bash
git add plugins/claude/hooks/launch_guard_markers.py plugins/claude/hooks/launch_guard_leases.py tests/agents/test_launch_guard_locks.py
git commit -s -m "fix(plugins): launch-guard lock files do not outlive their sessions"
```

---

### Task 30: Retire `grok-4.5` through the model-facts pipeline (R14a)

**Files:**
- Modify: `docs/agents/model-facts/families/grok/facts.json` (and `sources.json` if the skill adds a source)
- Modify: every generated file `tools/agents/sync_model_facts.py` and `tools/agents/sync_routes.py` rewrite
- Modify: tests that pin `grok-4.5` as an offered route (`tests/agents/test_registry.py`, `test_discovery.py`, `test_workflow_all_harnesses.py`, `test_sync_model_facts.py`)

- [ ] **Step 1: Update the facts with the model-facts skill.** Invoke `pitwall:model-facts` for the `grok` family. It must set `models.grok-4.5.status` to `"retired"` and add a `retires` fact cited from a declared vendor or harness source (`xai-models`, or the Grok CLI's model list via a `grok-*` harness source). The validator requires that fact (`tools/agents/validate_model_facts.py:259`). The run evidence is `76d77e92` on 2026-09-30, `Couldn't set model 'grok-4.5': unknown model id`.

- [ ] **Step 2: Regenerate and validate**

Run: `uv run python tools/agents/sync_model_facts.py && uv run python tools/agents/sync_routes.py && uv run python tools/agents/validate_model_facts.py && uv run python tools/agents/check_generated.py && echo FACTS-OK`
Expected: `FACTS-OK`, and `grep -c '"grok-4.5"' src/pitwall/agents/resources/generated/harness-registry.generated.json` prints `0`.

- [ ] **Step 3: Update the pinned tests.** Run `grep -rn "grok-4.5" tests/`. Where a test asserts that `grok-4.5` is offered, registered, or listed, change it to `grok-4.7`. Where a test exercises retirement handling, keep `grok-4.5` as the retired example.

- [ ] **Step 4: Run the registry and facts suites**

Run: `uv run pytest tests/agents/test_registry.py tests/agents/test_discovery.py tests/agents/test_workflow_all_harnesses.py tests/agents/test_sync_model_facts.py -q -p no:randomly 2>&1 | tail -3`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add docs/agents/model-facts/ src/pitwall/agents/resources/ plugins/ tests/agents/
git commit -s -m "fix(agents): retire grok-4.5, which the Grok CLI no longer accepts"
```

---

### Task 31: Documentation, changelog, release inventory, and the full CI-parity gate

**Files:**
- Modify: `docs/agents/orchestrator-channel.md` ("Tier 1: the MCP server" table and the paragraph after it; the "Steering" section)
- Modify: `docs/agents/run-records.md` (artifact table)
- Modify: `qa/concepts/mcp-and-agent-clients.md` ("Common confusions")
- Modify: `CHANGELOG.md` (`## [Unreleased]`)
- Modify: `release_acceptance/denominator.json`, `release_acceptance/reviewed-bindings.json` (only if Step 6 reports changed surfaces)

- [ ] **Step 1: Channel registration table (C4).** Add these rows after the Qwen Code row of the table under "Tier 1: the MCP server":

```markdown
| ZCode | `~/.zcode/cli/config.json` → `mcp.servers` | `timeoutMs: 3660000` (parent env is inherited) |
| Grok | `~/.grok/config.toml` managed block | `enabled = true` (parent env is inherited) |
| Antigravity | `~/.gemini/config/mcp_config.json` → `mcpServers` | `disabled: false` (parent env is inherited) |
| Muse | `${XDG_CONFIG_HOME:-~/.config}/muse/settings.json` → `mcpServers` (adds `"schema_version": 1`) | `env_vars`, `startup_timeout_sec: 30` |
| Hermes | `${HERMES_HOME:-~/.hermes}/config.yaml` → `mcp_servers`, one managed JSON-flow line | `env` with `${VAR}` forwarding, `timeout: 3660` |
| goose | `${XDG_CONFIG_HOME:-~/.config}/goose/config.yaml` → `extensions`, one managed JSON-flow line | `type: stdio`, `cmd`, `timeout: 3660` (parent env is inherited) |
```

Then add directly after the table:

```markdown
dsh and Pi have no MCP client, so they have no tier-1 channel and cannot be managed children; a
dispatch with ask support gives them the tier-4 file contract only. Hermes leaves an unset `${VAR}`
unexpanded, which the server treats as misconfigured, so a Hermes *orchestrator* session sees no
channel tools; Hermes as a dispatched child works because the dispatcher always sets the dispatch id.
```

- [ ] **Step 2: Steering and run-record docs (R1, R2, R6, R7).** Append to the "Steering" section of `docs/agents/orchestrator-channel.md`:

```markdown
A steer needs a run that was told about the channel. For a run without `channel.json`, `steer` and
`steer_and_wait` refuse `note`, `scope`, `priority`, and `budget`; a `stop` steer is accepted and
aborts the run when its deadline passes. When a run ends with a blocking steer still unacknowledged,
the supervisor emits `steer.unacked` with `atExit: true`, and the terminal event (`unackedSteerIds`),
the ledger row, and the managed terminal payload (`unacked_steer_ids`) name it. `pitwall agents inbox`
without a dispatch id lists only live and paused runs.

The tier-2 gate recognizes both spellings of the channel tools: `mcp__pitwall-channel__*` (Claude
Code) and `mcp__pitwall_channel__*` (Codex).
```

Add these rows to the artifact table in `docs/agents/run-records.md`:

```markdown
| `run.json` `supervisor` | The supervisor's pid and start identity, so `runs list`, `runs show`, `runs stop`, `runs cleanup`, and a managed wait can tell a live run from one whose supervisor was killed |
| `abandoned.json` | Written when a run's supervisor died without a terminal state: the reason and when it was detected; the run is then recorded `failed` |
```

- [ ] **Step 3: QA concept page.** In `qa/concepts/mcp-and-agent-clients.md` "Common confusions", add:

  > - Pitwall speaks both MCP eras: newer clients send the protocol version on every request (2026-07-28), and older ones start with an `initialize` handshake. Both work.

- [ ] **Step 4: Changelog.** Under `## [Unreleased]` in `CHANGELOG.md`, merge into the existing `### Added` and `### Fixed` lists and add `### Changed`:

```markdown
### Added
- The `pitwall-channel` MCP server registers for Grok, Antigravity, Muse, Hermes, and goose, so each
  can run as a managed child that asks its parent. The Codex and Copilot routing skills now send
  routed work through `dispatch_and_wait`.

### Changed
- MCP: the broker and the orchestrator channel serve protocol 2026-07-28 (stateless requests,
  `server/discover`, `resultType`, cache hints, `subscriptions/listen`) as well as the legacy
  `initialize` handshake. The broker now runs on the MCP Python SDK 2 (`mcp>=2.3.0,<3`).
- MCP: every broker and channel tool has a title and behavior annotations, and every broker
  parameter is described. Broker discovery carries instructions and advertises tools only.
- MCP: an unknown tool is JSON-RPC error -32602. Argument errors name the declared parameters to
  fix. Tool calls are rate limited. Pitwall error classes moved to -31000..-31005 and the relay's
  error code to -31010, outside the JSON-RPC reserved range.

### Fixed
- `pitwall mcp serve broker --json` no longer writes to stdout ahead of the protocol.
- `pitwall mcp relay` drops non-JSON server output instead of forwarding it.
- The channel rejects undeclared tool arguments and null request ids, and no longer echoes
  exception text.
- The tier-2 steering gate no longer blocks Codex's own channel tools, which Codex names
  `mcp__pitwall_channel__*`; a blocking steer to a Codex child deadlocked it.
- A standalone run whose supervisor was killed is recorded `failed` (with `abandoned.json`) by
  `runs list`, `runs show`, `runs stop`, `runs cleanup`, and a managed wait, instead of staying
  `running`; `run.json` now records the supervisor pid.
- A managed wait no longer declares a live standalone run orphaned.
- OpenCode exiting 0 with nothing on stdout is recorded as exit 77, not success.
- An aborted run reports exit 143 even when the harness exits 0 on SIGTERM.
- `steer` refuses directives a run without the orchestrator channel can never read.
- Blocking steers still unacknowledged when a run ends are reported; the all-runs inbox skips
  finished runs.
- Claude prompts are delivered on stdin, so prompts over 120 KiB dispatch.
- A flag in the prompt-source position prints usage instead of creating a failed run.
- Managed launches no longer store every byte of child output a second time under `launches/`.
- `prompt.deliver.md` is removed when a run ends unless `--routing-retain-prompt` was passed.
- `pitwall agents migrate` handles every earlier worktree branch prefix and follows moved worktrees
  whose branch is gone; managed terminal payloads report current artifact paths.
- Launch-guard lock files are removed at session end and swept after a week.
- `grok-4.5`, which the Grok CLI no longer accepts, is retired.
```

- [ ] **Step 5: Confirm the release inventory still discovers all 81 MCP surfaces.** MCP input-schema digests are computed at test time (`tools/release_acceptance/inventory.py:_mcp_schema_metadata`), so nothing committed needs regenerating.

```bash
uv run python -m tools.release_acceptance.inventory --output /tmp/inventory.json
.venv/bin/python -c "import json; rows=json.load(open('/tmp/inventory.json'))['surfaces']; print(sum(r['surface_id'].startswith('mcp:') for r in rows))"
```

Expected: `81`.

- [ ] **Step 6: Run the unit suite and the release-acceptance pins**

Run: `make test-fast 2>&1 | tail -5` and `uv run pytest tests/release_acceptance -q 2>&1 | tail -5`
Expected: no failures in either. If `test_surface_counts_match_the_reviewed_pin` reports changed surfaces, bind each new surface to the tests of the task that added it and update `release_acceptance/denominator.json` (`reason` and `total`) and `release_acceptance/reviewed-bindings.json`, the way the ZCode commit did, then rerun until it passes.

- [ ] **Step 7: Run the full CI-parity gate.** Every command must succeed.

```bash
uv lock --check
uv sync --frozen --extra dev
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src/ 2>&1 | tail -2
uv run mypy tools/agents 2>&1 | tail -2
uv run python tools/agents/validate_json_schemas.py && uv run python tools/agents/validate_plugins.py
uv run python tools/agents/validate_registry.py && uv run python tools/agents/check_generated.py
uv run python tools/agents/sync_routes.py --check
make pi-extensions-check
git ls-files -z -- '*.py' | xargs -0 -r -n 200 uv run python tools/guards/python_policy.py
git ls-files -z | xargs -0 -r -n 200 uv run python tools/guards/repo_text_policy.py
uv run python tools/ci/check_markdown_links.py && uv run python tools/ci/check_markdown_links.py --external
make sec-semgrep 2>&1 | tail -3
uv run bandit -c pyproject.toml -r src/pitwall -ll -ii -b tools/security/bandit-baseline.json 2>&1 | tail -3
docker build --platform linux/amd64 -f docker/Dockerfile.mcp -t pitwall/mcp:ci . 2>&1 | tail -2
docker build --platform linux/amd64 -f docker/Dockerfile.api -t pitwall/api:ci . 2>&1 | tail -2
uv run python tools/ci/check_dco.py --base origin/main --head HEAD
```

Expected: `uv lock --check` reports no changes; ruff and mypy are clean; the validators, guards, and both link checks pass; semgrep and bandit report no new findings; both Docker builds succeed; DCO passes.

- [ ] **Step 8: Run the database suites one after another**

```bash
make up && make test-int 2>&1 | tail -5
uv run pytest tests/release/test_mcp_all_tools_journey.py -q -p no:randomly 2>&1 | tail -5
scripts/release/run-user-journeys.sh 2>&1 | tail -40
```

The journey runner takes `DATABASE_URL` and `REDIS_URL` pointing at the disposable `make up` services, as `RELEASING.md` requires.
Expected: no failures in any of the three.

- [ ] **Step 9: Commit**

```bash
git add docs/agents/orchestrator-channel.md docs/agents/run-records.md qa/concepts/mcp-and-agent-clients.md CHANGELOG.md release_acceptance/
git commit -s -m "docs: changelog, channel and run-record docs, QA page, and release bindings"
```

---

### Task 32: Live proof and workstation cleanup

Registration and a stdio handshake do not prove delivery (channel findings spec, Part 1, "Recommendation" item 5; C5). This task
uses the operator's live accounts for Grok, Antigravity, Muse, Hermes, and goose, and it rebuilds the local Docker stack; run it once
the operator approves that use.

- [ ] **Step 1: Install the branch build as a new release directory**, the way the 2026-09-30 upgrade did:

```bash
REL=~/.local/share/pitwall/releases/20261006-mcp-alignment
mkdir -p "$REL" && git archive HEAD | tar -x -C "$REL"
(cd "$REL" && uv sync --frozen --extra storage --no-dev)
ln -sfn "$REL/.venv/bin/pitwall" ~/.local/bin/pitwall && pitwall --version
```

Expected: `0.2.0a1`. Rollback: `ln -sfn ~/.local/share/pitwall/releases/20261005-zcode-channel/.venv/bin/pitwall ~/.local/bin/pitwall`.

- [ ] **Step 2: Register and check**

Run: `pitwall agents setup mcp --dry-run`, review the five new diffs, then `pitwall agents setup mcp --yes`, then:

```bash
pitwall agents doctor --json | uv run python -c 'import json,sys; [print(c["provider"], c["status"]) for c in json.load(sys.stdin)["checks"] if c["id"]=="channel.registration"]'
```

Expected: 13 lines, every one `PASS` (claude, codex, copilot, opencode, kimi, cline, qwen, zcode, grok, agy, muse, hermes, goose).

- [ ] **Step 3: Rebuild the broker image and restart the local stack (operator approves first).** The broker every harness reaches runs inside `pitwall-api-1` (`pitwall mcp relay -- docker exec -i pitwall-api-1 pitwall mcp serve broker`), so the SDK 2 broker goes live only with a rebuilt image. From the branch checkout:

```bash
docker inspect pitwall-api-1 --format '{{.Config.Image}}'   # record this tag for rollback
D=~/.config/pitwall/deployment
COMPOSE=(docker compose -p pitwall -f docker-compose.yml -f "$D/durable-gateway.compose.override.yml" --env-file "$D/broker.env" --env-file "$D/gateway.env")
export PITWALL_IMAGE_TAG=mcp-2026-07-28-20261006
"${COMPOSE[@]}" build && "${COMPOSE[@]}" up -d --wait
```

Expected: every service reports healthy. Rollback: the same `up -d --wait` with `PITWALL_IMAGE_TAG` set to the recorded tag.

- [ ] **Step 4: Prove both MCP eras live (F01, F02, F18)**

```bash
META='{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"probe","version":"0"}}'
REQ="{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"server/discover\",\"params\":{\"_meta\":$META}}"
printf '%s\n' "$REQ" | timeout 60 pitwall mcp relay -- docker exec -i pitwall-api-1 pitwall mcp serve broker | head -1
printf '%s\n' "$REQ" | timeout 30 pitwall mcp serve channel | head -1
pitwall agents doctor --json | uv run python -c 'import json,sys; [print(c["status"], c["summary"]) for c in json.load(sys.stdin)["checks"] if c["id"]=="channel.mcp_server"]'
```

Expected: both replies carry `"resultType": "complete"` and list `2026-07-28` in `supportedVersions`; doctor prints `PASS pitwall-channel answers the 2026-07-28 discover/tools path and the legacy handshake with the orchestrator and subagent tools`. Then, in a new Claude Code session (the legacy handshake through the registered relay), call `mcp__pitwall_broker__pitwall_health`. Expected: `{"ok": true, "database": true, "redis": true, "provider_registry": true}`.

- [ ] **Step 5: One managed ask per new harness.** From a routing-active Claude session, for each of `grok`, `agy`, `muse`, `hermes`, `goose`, call `mcp__pitwall-channel__dispatch_and_wait` with `provider` set to the harness and this prompt:

```text
Before anything else, call the ask_orchestrator tool: question "Which color?", blocked_on "choice",
options [{"id": "a", "text": "red"}, {"id": "b", "text": "blue"}], default "a",
default_rationale "first option", files_touched []. Then reply with only the chosen color and stop.
```

Expected: the call returns an `ask` event naming option ids `a` and `b`. `answer_and_wait` with choice `b` returns a terminal event whose output contains `blue`. In that run's `events.jsonl`, `grep -c '"event":"ask.resolved"'` prints `1`.

- [ ] **Step 6: Fix any harness whose child did not ask over MCP.** If the run instead paused with exit 75 and a file-contract ask, the dispatch id did not reach the server. For `agy`, render `"env": {name: "${" + name + "}" for name in FORWARDED_ENV}` in its `render_entry` branch (the shape `agy mcp add --env` writes), update the Task 13 expectation, rerun Task 13 Step 4, reinstall (Step 1), rerun `setup mcp --yes`, and repeat Step 5 for that harness. For `muse`, replace `env_vars` with that same `env` mapping and do the same. Any harness passes Task 32 only when Step 5's expected output holds.

- [ ] **Step 7: Prove a Codex child can acknowledge a blocking steer (R1).** From a routing-active Claude session, call `dispatch_and_wait` with `provider` `codex` and the prompt `Run "sleep 45" three times, each in its own shell call, then reply DONE.` While it runs, call `steer_and_wait` with kind `scope` and message `Reply STEERED instead of DONE.`
Expected: `steer_and_wait` returns a `steer_ack` event within the 300 s deadline; the terminal output contains `STEERED`; the run's `events.jsonl` has one `steer.acked` and no `steer.unacked`.

- [ ] **Step 8: Prove Claude stdin delivery (R8)**

```bash
printf 'Reply with exactly: ok\n' > /tmp/claude-small.md
~/.claude/scripts/claude-shim.sh /tmp/claude-small.md --model haiku | tail -2
uv run python -c 'import sys; sys.stdout.write("# filler\n" * 15000 + "Ignore the filler. Reply with exactly: ok\n")' > /tmp/claude-large.md
~/.claude/scripts/claude-shim.sh /tmp/claude-large.md --model haiku | tail -2
```

Expected for both: `ok`, then `SHIM-DONE exit=0`. The second prompt is about 135 KiB, above the old 120 KiB limit.

- [ ] **Step 9: Reconcile the stuck runs (R2)**

```bash
S=~/.local/state/pitwall/agents
pitwall agents runs list | awk -F'\t' '$4=="running"' | wc -l
ls "$S"/runs/*/abandoned.json | wc -l
```

Expected: the first count equals only the dispatches live at that moment (the 62 OpenCode runs from 2026-09-28/29 are gone from it); the second prints at least `62`.

- [ ] **Step 10: Remove duplicated launcher logs (finding 10; operator approves first).** Only a log that is byte-identical to its finished run's own `stderr.log` is emptied:

```bash
S=~/.local/state/pitwall/agents
for f in "$S"/launches/*/launcher.stderr.log; do
  id=$(basename "$(dirname "$f")"); r="$S/runs/$id"
  [ -f "$r/result.json" ] && cmp -s "$f" "$r/stderr.log" && : > "$f"
done
du -sh "$S/launches"
```

Expected: `launches` drops from about 395 MB to under 20 MB.

- [ ] **Step 11: Remove kept delivery prompts (finding 11; operator approves first).** Only finished runs whose request says the prompt was not retained:

```bash
S=~/.local/state/pitwall/agents
for f in "$S"/runs/*/prompt.deliver.md; do
  r=$(dirname "$f")
  [ -f "$r/result.json" ] && grep -q '"retained": false' "$r/request.json" && rm -f "$f"
done
for f in "$S"/runs/*/prompt.deliver.md; do [ -f "$(dirname "$f")/result.json" ] && echo "$f"; done | wc -l
```

Expected: the final count prints `0`.

- [ ] **Step 12: Repair the migrated worktree records (finding 12; operator approves first)**

Run: `pitwall agents migrate`, then `uv run python -c 'import json, pathlib; print(json.loads((pathlib.Path.home() / ".local/state/pitwall/agents/runs/75694a97-a293-4a23-a1a4-7a75e7c2fcba/workspace.json").read_text())["path"])'`
Expected: the migrate output names the 16 records it renamed or updated, and the printed path ends in `/.local/state/pitwall/agents/worktrees/75694a97-a293-4a23-a1a4-7a75e7c2fcba`. The operator then applies or discards that run's unapplied changes with `pitwall agents runs apply` or `runs discard`.

- [ ] **Step 13: Remove the two orphaned worktree directories (operator approves first).** `6b9afc34-c7b0-4fe7-8c0c-65d362d48c00` and `ea019e64-39d3-4eb0-9f6c-6645dc906c06` (58 MB each, 2026-09-14) have no run record, and their source repository no longer exists. For each id, confirm `ls ~/.local/state/pitwall/agents/runs/<id>` fails, then `rm -rf ~/.local/state/pitwall/agents/worktrees/<id>`.
Expected: `du -sh ~/.local/state/pitwall/agents/worktrees` drops by about 116 MB.

- [ ] **Step 14: Confirm the lock sweep (R13).** After the next Claude session starts with the branch build's plugin installed, run `ls -a ~/.local/state/pitwall/agents/routing-sessions | grep -c '\.lock$'`.
Expected: no more than the number of session markers plus the locks created in the last seven days (down from 250).

- [ ] **Step 15: Record the proof.** Add one line per harness to `docs/agents/orchestrator-channel.md` under the registration table: `Live delivery verified 2026-10-06: <harness> (dispatch <first 8 of the dispatch id>).` Commit with `git commit -s -m "docs(agents): live channel delivery verified for five harnesses"`.

---

## Finding coverage

| Finding | Task |
|---|---|
| F01 Broker legacy era / SDK pin | 2, 7 |
| F02 Channel legacy era | 8 |
| F03 Unknown tool not `-32602` | 4 |
| F04 Channel undeclared arguments | 9 |
| F05 No annotations | 5 (broker), 9 (channel) |
| F06 No titles, undocumented parameters | 5, 6 |
| F07 Generic `outputSchema` | 7 |
| F08 No actionable error detail | 4 |
| F09 No rate limiting | 4 (broker), 9 (channel) |
| F10 No instructions | 7 |
| F11 Legacy-range class codes and wrong docs | 3 |
| F12 Relay `-32004` | 10 |
| F13 Null id dropped | 8 |
| F14 `--json` on stdout | 11 |
| F15 Relay forwards non-JSON | 10 |
| F16 Channel echoes exception text | 9 |
| F17 Advertised prompts and resources | 7 |
| F18 Doctor probes legacy only | 9 |
| C1 Register Grok, Antigravity, Muse, Hermes, goose | 12, 13, 14; live proof 32 |
| C2 Codex and Copilot skills teach the managed path | 15 |
| C3 Ask support stays opt-in for direct shim callers | Global Constraints |
| C4 dsh/Pi tier-4 only, Hermes orchestrator limit, missing ZCode row | 31 |
| C5 Live delivery proof per new harness | 32 |
| R1 Steering gate blocks Codex's channel tools | 17 |
| R2 Dead standalone supervisor leaves `running` forever | 18 |
| R3 Empty output with exit 0 recorded as success | 20 |
| R4 Live standalone run declared orphaned | 19 |
| R5 Cancelled run reports exit 0 | 21 |
| R6 Steer to a channel-less run accepted as sent | 22 |
| R7 Unacknowledged steers vanish at exit | 23 |
| R8 Claude prompts over 120 KiB fail | 24 |
| R9 Flag taken as the prompt path | 25 |
| R10 Managed launches store child output twice | 26 |
| R11 Delivery prompts kept on every ask-support run | 27 |
| R12 Migration leftovers | 28 |
| R13 Launch-guard lock files never removed | 29 |
| R14 Two terminal-state sets; `grok-4.5` rejected | 16, 30 |
| Gap-analysis F02 harness table out of date | corrected in the spec (Task 1 commits it) |
| Rate limit too tight for the 162-call release journey; journey pinned the old error payload | 4 |

---

## Continuation (2026-10-07): comprehensive Sol review fixes

The operator asked for comprehensive GPT-6 Sol reviews of the whole project at `65debbc6` (head of
`feat/zcode-harness` after Task 31). Five area reviews ran read-only: r1 Agent Routing; r2 workbench,
gateway, gateway catalog, TUI; r3 REST API, broker MCP, security, policy, webhooks; r4 RunPod client,
providers, leases, reconciler, resolver, autopilot, routing; r5 cost, finops, database, audit, CLI,
core, the remaining modules, Docker, Compose, CI. Gap-fill passes (r1b, r2b, r4b, r5b) cover the
files the first passes did not trace. Reports: `.superpowers/sdd/2026-10-06-mcp-alignment-channel-and-run-store/reviews/`.
Every finding below was verified by the controller (the reviewer's reproduction script re-run at
`65debbc6`, or the code path read end to end). This section grows as the remaining reviews land.

### Decisions

- **r2-5 (gateway status contract):** the code is right and the SDLC text is wrong. `relay.py` keeps
  upstream 401/403/407 statuses and replaces their bodies (`_PASSTHROUGH_EXCLUDED`,
  `_safe_error_body`); `test_auth_error_echoing_the_authorization_header_never_relays_the_key` pins
  this (ported from the OmniRoute shim tests). Task 36 corrects `docs/sdlc/24-gateway.md`.
- **r2-4 (late task artifact):** a terminal task record is immutable (`task_record.py`, comment at
  line 578). A late artifact is refused before any file is created.
- **Lanes:** five lanes with exclusive file ownership (lane prompts from
  `~/.claude/templates/lane-prompt.md`), each its own worktree off `feat/zcode-harness`. Lanes run
  hermetic tests only; any database suite runs on a private database on `:5444`
  (`CREATE DATABASE pitwall_lane_<id>`), never on the shared `pitwall_test`.
- **Every task is test-first:** the regression test reproduces the reviewer's failure scenario and
  fails at `65debbc6` before the fix.
- **Grounding (2026-10-07 pass):** every task's **Grounding** paragraph cites the code it changes
  (file, function, line at `65debbc6`, read by the controller). It also cites the official
  documentation for each library or protocol behaviour the fix depends on:
  - git 2.53 `git-cherry-pick(1)` (installed manual)
  - Linux `flock(2)`
  - CPython 3.14.7 `asyncio/mixins.py`
  - HTTPX "Async Support" and "QuickStart"
  - Textual "Testing" guide
  - Redis `SET`
  - PostgreSQL 18 `SELECT` locking clause, "Advisory Lock Functions", and "JSON Functions and
    Operators"
  - RunPod "Create a new template"
  - the project's own SDLC chapters

  Web pages were fetched 2026-10-07.
- **r4c-3 (gateway credential reference with an unset variable):** not a defect.
  `docs/sdlc/20-provider-plugins.md:15` defines keyless pools as gateway rows needing no env var.
  Every gateway row carries the same default reference `PITWALL_GATEWAY_API_KEY`
  (`core/models.py:375`), so an unset variable is how a keyless pool is expressed, and the adapter
  cannot tell it apart from a forgotten key. A keyed gateway with the variable unset fails at the
  upstream with 401: no spend, no leak, no wrong account. Cost if wrong: a less specific error
  message for that misconfiguration.
- **r3-5 (Anthropic content blocks):** `docs/sdlc/02-api-rest.md:520` promises text, `tools`,
  `tool_use`, and `tool_result`. Other block types are refused with the Anthropic error envelope, not
  translated.
- **r4b-1 (Lambda quantity):** one lease per external resource is the adapter contract, so
  `quantity` must be 1.
- **Corrections from grounding:**
  - Task 43 needs no migration (`SubBudgetGate` is a library class with an injected spend source).
  - Task 45 does need one (`0043`).
  - Task 39 also covers pod creation, because the routed launch ignores `credential_ref` too.
  - Task 37 is rooted in `build_personal_service`, which the TUI and the MCP `serve` tool both call,
    not in the TUI alone.
  - Task 38 covers the TypeScript store as well.
  - Task 40 locks the provider row in both arming and disarming.
  - Task 47's lock must be per event loop.

### Lane ownership

| Lane | Tasks | Owns (exclusive) |
|---|---|---|
| A agents | 33, 34, 35 | `src/pitwall/agents/{run_store,workspace,mailbox}.py`, `tests/agents/test_{run_store,workspace,mailbox}.py` |
| B gateway/TUI/personal/workbench | 36, 37, 38 | `src/pitwall/gateway/relay.py`, `tests/gateway/test_relay.py`, `docs/sdlc/24-gateway.md`, `src/pitwall/personal/service.py`, `src/pitwall/cli/personal.py`, `src/pitwall/mcp/tools/serve.py`, `src/pitwall/tui/{app,personal,resources}.py`, `tests/personal/test_service.py`, `tests/mcp/test_serve_tool.py`, `tests/tui/test_{personal_screens,resources_screen}.py`, `src/pitwall/workbench/task_record.py`, `src/pitwall/workbench/pi_extensions/task-record.{ts,js}`, `tests/workbench/test_task_record.py`, `tests/workbench/pi_extensions/native-extension.test.mjs` |
| C leases/RunPod | 39, 40, 41, 42 | `src/pitwall/api/leases/{teardown,launch}.py`, `src/pitwall/runpod_client/{pods,templates}.py`, `src/pitwall/autopilot/controller.py`, `docs/sdlc/16-core-config.md`, `tests/leases/test_{teardown,launch,launch_failure_cleanup}.py`, `tests/runpod_client/test_{pods,templates}.py`, `tests/autopilot/test_controller.py` |
| D cost/audit/core | 43, 44, 45, 46 | `src/pitwall/cost/{sub_budgets,sync_gate,alerts}.py` and a new shared private Redis-claim module in `src/pitwall/cost/`, `src/pitwall/finops/burn_rate_alerts.py` (import change only), `src/pitwall/audit/capability.py`, `src/pitwall/core/idempotency.py`, `db/migrations/0043_idempotency_body_hash.sql`, `docs/sdlc/{05-cost-budget,07-data-model-db}.md`, `tests/cost/test_{sub_budgets,budget_alerts}.py`, `tests/audit/test_capability_audit.py`, `tests/core/test_idempotency.py` |
| E database | 47, 48, 49 | `src/pitwall/db/{__init__,drill_evidence}.py`, `src/pitwall/migrations.py`, `tests/db/test_pool_lifecycle.py`, `tests/test_migration_discovery.py`, new `tests/db/test_drill_evidence.py` |

Lane D owns the only new migration (`0043`); lane E edits `src/pitwall/db/__init__.py` lines
46-76 and 395-415 only. `CHANGELOG.md` and the status doc belong to Task 74 (the closing gate). Each lane runs its
integration tests on its own database (`pitwall_lane_<c|d|e>`) and Redis DB index (3, 4, 5).

### Task 33: Run cleanup never deletes a run that may still be running (r1-1)

**Files:** Modify `src/pitwall/agents/run_store.py` (`cleanup_runs`, line 574). Test
`tests/agents/test_run_store.py` (existing cleanup tests at lines 82 and 365 call
`cleanup_runs(env, older_than_seconds=..., remove_all=...)`).

**Grounding:** `abandoned_reason` (line 57) already decides liveness: a nonterminal, non-paused
standalone run is alive while `run.json` `supervisor.pid` passes `pids.pid_alive(pid,
supervisor.pidStartIdentity)`, and a pidless run is kept for `PIDLESS_ABANDON_SECONDS`.
`reconcile_run` (line 164) finalizes only runs it proves abandoned. `cleanup_runs` calls
`reconcile_run` first but then protects only managed runs (`_managed_run_is_live`, line 462) and
retained worktrees (`workspace.json`), so a live standalone run reaches `shutil.rmtree`. There is no
per-run lock in `run_store.py`.

- [ ] **Step 1: Failing test** `test_cleanup_keeps_a_nonterminal_run_whose_supervisor_is_alive`: write a
  standalone run whose `run.json` has `state: "running"` and `supervisor: {pid: os.getpid(),
  pidStartIdentity: pids.process_identity(os.getpid())}` (as `/tmp/review-r1/cleanup_live.py` does);
  `cleanup_runs(env, older_than_seconds=None, remove_all=True)`; assert the run is not in the result
  and its directory exists. Second case: the same run with `older_than_seconds=0`. Third case: an
  isolated run with no `workspace.json` yet is kept.
- [ ] **Step 2:** Run it; expect FAIL (`removed: ['…aa']`, `run_exists: False`).
- [ ] **Step 3: Fix:** after `reconcile_run`, re-read `run.json`; skip the run when its state is not in
  `TERMINAL_STATES` and is not `paused` (a run `reconcile_run` did not finalize is one
  `abandoned_reason` considers possibly alive). Immediately before `shutil.rmtree`, re-read the state
  once more and skip if it is no longer terminal, so a run that changed state during distillation is
  kept. Paused runs keep today's behaviour (they have no supervisor by design, line 60).
- [ ] **Step 4:** `uv run pytest tests/agents/test_run_store.py tests/agents/test_mcp_event_channel.py -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(agents): run cleanup keeps a run whose supervisor is alive` (`-s`).

### Task 34: A failed cherry-pick is never recorded as applied (r1-2)

**Files:** Modify `src/pitwall/agents/workspace.py` (`apply_run`, line 849; commit loop lines 924-943).
Test `tests/agents/test_workspace.py`.

**Grounding:** git 2.53 `git-cherry-pick(1)`, `--allow-empty` and `--empty`: "Commits that were
initially empty will still cause the cherry-pick to fail unless one of --empty=keep or --allow-empty
are specified." Git exits nonzero, leaves `CHERRY_PICK_HEAD`, and reports no unmerged paths, so
`git diff --name-only --diff-filter=U` is empty. `apply_run` only fills `conflicted` from that diff,
so `status = "conflicted" if conflicted else "applied"` (line 980) reports `applied`, and the
working patch is applied into the unfinished cherry-pick. The patch path in the same function already
falls back to `or ["<patch>"]` when git names no file.

- [ ] **Step 1: Failing test** `test_apply_commits_with_an_empty_commit_is_not_reported_applied`: as in
  `/tmp/review-r1/empty_commit_apply.py`, prepare an isolated worktree, `git commit --allow-empty`,
  `capture_changes`, then `apply_run(..., apply_commits=True)` into a temp target; assert
  `outcome.status == "conflicted"`, `result.json` integration status `conflicted`,
  `conflictedFiles` contains `<commit {sha[:12]}>`, the working patch was not applied, and the message
  names `git cherry-pick --skip` and `git cherry-pick --abort`.
- [ ] **Step 2:** Run it; expect FAIL (`outcome: applied applied_commits: 0`).
- [ ] **Step 3: Fix:** on any nonzero cherry-pick exit, set `conflicted` to the unmerged paths
  `or [f"<commit {sha[:12]}>"]`; when git named no unmerged path, the message says the commit could
  not be applied (for an empty commit: skip it with `git cherry-pick --skip` or cancel with
  `git cherry-pick --abort`). The existing `if not conflicted:` guard then keeps the working patch
  from being applied.
- [ ] **Step 4:** `uv run pytest tests/agents/test_workspace.py -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(agents): a failed cherry-pick is never recorded as applied`.

### Task 35: The one-open-ask limit holds under concurrent asks (r1-3)

**Files:** Modify `src/pitwall/agents/mailbox.py` (`write_ask`, line 433). Test `tests/agents/test_mailbox.py`.

**Grounding:** `write_ask` counts `pending_asks()` (line 455) and later publishes through `_append`
with an exclusive create; neither takes a lock, so two MCP worker threads
(`mcp_server.py` lines 349-398) both pass the check. `_locked_file` (line 76) is the mailbox's
inter-process `fcntl.flock` helper, already used per ask by `_answer_lock` under
`_ANSWER_LOCKS_DIR = "answer-locks"`. flock(2): locks belong to the open file description, so two
separate `open()` calls in one process contend; each `_locked_file` call opens its own descriptor,
so the lock serializes threads as well as processes.

- [ ] **Step 1: Failing test** `test_concurrent_asks_respect_max_open`: two threads, synchronised on a
  `threading.Barrier` after the pending-ask read (wrap `pending_asks` as
  `/tmp/review-r1/open_ask_race.py` does), call `write_ask(..., max_open=1)`; assert exactly one
  returns, the other raises `MailboxOpenAskError`, and `pending_asks()` has one entry.
- [ ] **Step 2:** Run it; expect FAIL (`open_asks: ['0001', '0002']`).
- [ ] **Step 3: Fix:** when `max_open` is set, hold
  `_locked_file(self.root / _ANSWER_LOCKS_DIR / "open-ask.lock")` across the pending count and the
  `_append("asks", build)` publication. The name cannot collide with the four-digit per-ask lock
  files in that directory, and no new file appears in the mailbox root.
- [ ] **Step 4:** `uv run pytest tests/agents/test_mailbox.py tests/agents/test_channel_chaos.py -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(agents): the open-ask limit holds under concurrent asks`.

### Task 36: Upstream SSE errors are bounded and redacted; gateway status doc corrected (r2-1, r2-5)

**Files:** Modify `src/pitwall/gateway/relay.py` (`_relay` SSE branch, lines 165-170). Modify
`docs/sdlc/24-gateway.md` (lines 120-122). Test `tests/gateway/test_relay.py`.

**Grounding:** `_relay` checks `content-type: text/event-stream` and returns a streaming
`RelayResult` before `_read_body` (lines 233-239) can route `status >= 400` through
`_safe_error_body` (line 327, which uses `_PASSTHROUGH_EXCLUDED = {401, 403, 407}`,
`_CREDENTIAL_LEAK`, and `redact_text`). HTTPX (python-httpx.org, Async Support / QuickStart,
fetched 2026-10-07): a response opened with `send(..., stream=True)` exposes `status_code` and
headers before the body is read, so the status check needs no body read.
`test_auth_error_echoing_the_authorization_header_never_relays_the_key` (line 386) pins the
intended contract for non-streamed errors: status kept, body replaced.

- [ ] **Step 1: Failing test** `test_sse_error_status_is_bounded_and_redacted`, parametrised over 401,
  429, 500: upstream returns that status, `content-type: text/event-stream`, body
  `data: Bearer {KEY}\n\n`; assert `out.stream is None`, `out.status` equals the upstream status,
  content type `application/json`, and `KEY.encode() not in out.body`.
- [ ] **Step 2:** Run it; expect FAIL (`body: data: Bearer …` relayed).
- [ ] **Step 3: Fix:** take the SSE streaming branch only when `response.status_code < 400`; error
  statuses fall through to `_read_body`, which bounds the read and redacts.
- [ ] **Step 4: Doc:** replace "Upstream `401`, `403`, and `407` statuses are not passed through, and
  upstream error text is redacted …" with: upstream `401`, `403`, and `407` keep their status, but
  their bodies are never passed through (the client receives the gateway's own error envelope);
  every other upstream error body, streamed or not, is redacted (credentials, stack traces, internal
  paths) and bounded before it reaches the client.
- [ ] **Step 5:** `uv run pytest tests/gateway -q -p no:randomly` → all pass;
  `uv run python tools/ci/check_markdown_links.py` → exit 0.
- [ ] **Step 6:** Commit `fix(gateway): upstream SSE errors are bounded and redacted`.

### Task 37: Personal serving resolves its RunPod key like the CLI; the registry dashboard needs none; Resources keeps its summary on a failed refresh (r2-2, r2-3)

**Files:** Modify `src/pitwall/personal/service.py` (`build_personal_service`, lines 676-700),
`src/pitwall/tui/app.py` (`on_mount`, lines 363-370), `src/pitwall/tui/personal.py`
(`ServicePersonalSource.__init__`, line 62), `src/pitwall/mcp/tools/serve.py` (`serve_model`, line
152), `src/pitwall/tui/resources.py` (`_refresh` failure branch, lines 371-389). Tests
`tests/personal/test_service.py`, `tests/tui/test_personal_screens.py`,
`tests/tui/test_resources_screen.py`, `tests/mcp/test_serve_tool.py`.

**Grounding:** `build_personal_service` writes `endpoint.key` (`ensure_endpoint_key`) and then
indexes `os.environ["RUNPOD_API_KEY"]` (line 691). With no key it raises `KeyError` *after* creating
personal state (reproduced: `build: KeyError 'RUNPOD_API_KEY'`, `…/pitwall/endpoint.key` created).
The CLI avoids this by resolving first (`cli/personal.py` lines 76-89: `resolve_runpod_api_key`,
env then runpodctl's `~/.runpod/config.toml`, exporting a runpodctl key), but the TUI
(`ServicePersonalSource`) and the MCP `serve` tool call `build_personal_service` directly, so they
crash without an env key and ignore a runpodctl credential `pitwall setup` accepts
(`personal/setup.py` line 54). `on_mount` calls `_install_personal()` (line 368) before
`select_backend()` (line 369), so even the registry dashboard builds the personal service; the
`action_show_serve/pods/routes` handlers (lines 402-418) already install it lazily.
`runpod_credentials.MISSING_CREDENTIAL_MESSAGE` is the existing user-facing text. In
`resources.py`, `_refresh` sets only the summary to `Loading resources`, so on failure the summary
becomes `Unavailable` while the tables keep the previous snapshot. Textual testing guide
(textual.textualize.io/guide/testing, fetched 2026-10-07): `App.run_test()` runs headless and yields
a `Pilot`; `on_mount` runs inside it.

- [ ] **Step 1: Failing tests:**
  (a) `tests/personal/test_service.py::test_build_without_a_key_fails_before_writing_state`: env
  without `RUNPOD_API_KEY`, `HOME`/XDG in `tmp_path`, no runpodctl config → raises the typed error
  carrying `MISSING_CREDENTIAL_MESSAGE`, and no `endpoint.key` exists.
  (b) `…::test_build_uses_the_runpodctl_credential`: only `~/.runpod/config.toml` with `apikey` →
  the built service's RunPod client uses that key.
  (c) `tests/tui/test_personal_screens.py::test_registry_dashboard_mounts_without_runpod_key`:
  registry config, no key, `app.run_test()` → overview screen shown, no personal state directory.
  (d) `…::test_serve_screen_without_a_key_shows_the_credential_message`: personal backend, no key →
  the serve screen shows `MISSING_CREDENTIAL_MESSAGE` instead of crashing.
  (e) `tests/mcp/test_serve_tool.py::test_serve_without_a_key_is_a_tool_error`: a structured tool
  error with that message, never `KeyError`.
  (f) `tests/tui/test_resources_screen.py::test_failed_refresh_keeps_the_previous_snapshot`: a
  source that succeeds, then raises → summary and tables both still show the first snapshot and the
  error line shows the failure.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:**
  - `build_personal_service` resolves the key with `resolve_runpod_api_key(os.environ)` before
    touching `StateStore`. It raises a typed `RunPodCredentialMissing(RuntimeError)` with
    `MISSING_CREDENTIAL_MESSAGE` when there is none, and passes the resolved key to `LiveRunPod`.
  - `on_mount` calls `select_backend()` first and installs the personal screens only on the
    personal path. The personal source surfaces `RunPodCredentialMissing` as its screen error.
  - The MCP tool maps it to its existing structured error shape.
  - The CLI keeps its interactive setup prompt and catches the same error.
  - `resources.py`: on failure, re-render the last successful snapshot when there is one (summary
    and tables together), then show the error line.
- [ ] **Step 4:** `uv run pytest tests/personal tests/tui tests/mcp/test_serve_tool.py tests/cli -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(personal): resolve the RunPod key once; the dashboard and MCP never crash without one`.

### Task 38: A terminal task record refuses late artifacts before writing a file, in both stores (r2-4)

**Files:** Modify `src/pitwall/workbench/task_record.py` (`_artifact_unlocked`, line 728) and
`src/pitwall/workbench/pi_extensions/task-record.ts` (`artifactUnlocked`, lines 518-532) with its
compiled `task-record.js`. Tests `tests/workbench/test_task_record.py`,
`tests/workbench/pi_extensions/native-extension.test.mjs` (run by `test_node_suites.py`).

**Grounding:** both stores define the same terminal set (`TERMINAL_STATES` line 26:
`succeeded, failed, cancelled, interrupted`; TS `terminalExecutionStates`). Terminal records are
immutable (`_update_unlocked`, lines 578-581, which drops the `artifacts` patch), but both
`artifact` paths create the `.txt` file first and return it as if it were linked. Neither store's
`finish` depends on late artifacts: TS `finish` writes `result` and `summary` before the terminal
update and returns early on an already-terminal record.

- [ ] **Step 1: Failing tests:** Python and TS: finish a task, then `artifact(taskId, "summary",
  "late")` → error naming the terminal state, and no `{taskId}.summary-*.txt` file exists.
- [ ] **Step 2:** Run them; expect FAIL (file created, no error).
- [ ] **Step 3: Fix:** in both artifact functions, refuse when the record's `executionState` is
  terminal, before opening the file.
- [ ] **Step 4:** `make pi-extensions-check` → no diff;
  `uv run pytest tests/workbench/test_task_record.py tests/workbench/pi_extensions -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(workbench): a terminal task record refuses late artifacts`.

### Task 39: Every RunPod pod is created and terminated with the provider's own credential (r4-1, r4-2)

**Files:** Modify `src/pitwall/api/leases/launch.py` (`run_launch` RunPod branch, lines 2057-2090;
compensation `_abandon_on_failure(... terminate=terminate_pod_sync ...)`, line 1993),
`src/pitwall/api/leases/teardown.py` (`run_teardown` → `_terminate_resource`, lines 168 and
257-296), `src/pitwall/runpod_client/pods.py` (`_terminate_orphaned_create`, lines 1481-1506). Tests
`tests/leases/test_teardown.py`, `tests/leases/test_launch*.py` (the existing launch test file),
`tests/runpod_client/test_pods.py`.

**Grounding:**
- `docs/sdlc/08-runpod-integration.md:51`: "A persisted `credential_ref` is resolved only inside the
  chosen adapter operation." The RunPod adapter does this: `RunPodProvider.provision` and `.teardown`
  (`providers/runpod.py` lines 365-378 and 435-445) resolve `request.credentials` and pass
  `api_key`/REST URLs to `run_launch`/`run_teardown`.
- The routed REST launch (`launch.py:2477`), `serve.py:2100`, and REST stop
  (`routes/leases.py:285`) call `run_launch`/`run_teardown` directly with no key. They fall back to
  the process key: `terminate_pod(pod_id)` at `teardown.py:296`, and
  `resolve_runpod_api_key(os.environ)` for creates.
- So a pod created through the adapter under `credential_ref = SECOND_RUNPOD_KEY` and stopped over
  REST is terminated against the wrong account (`repro_teardown_auth.py`:
  `[('ambient', 'pod-paid')]`). A cancelled create (`_terminate_orphaned_create`) and a failed launch
  after create (`terminate_pod_sync`) also terminate with the ambient key
  (`repro_cancel_auth.py`).
- `CredentialReference` (`providers/interface.py:54`) and `resolve_adapter_credentials` are the
  existing resolution path.

- [ ] **Step 1: Failing tests** (ambient key ≠ provider key, provider `credential_ref =
  "SECOND_RUNPOD_KEY"`):
  (a) `run_teardown` with no overrides terminates with `SECOND_RUNPOD_KEY`'s value.
  (b) `run_launch` with no `api_key` creates with that value.
  (c) a create cancelled after the pod exists terminates with the create's key and REST URL.
  (d) a launch failing after create terminates with the create's key.
  An explicit `api_key` override still wins in (a) and (b).
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:**
  - In `run_launch`'s RunPod branch and in `_terminate_resource`, when the caller passed no key,
    resolve the provider's `credential_ref` through `resolve_adapter_credentials(
    CredentialReference(provider.credential_ref), RunPodCredentials, adapter_id="runpod")` and pass
    it explicitly. The missing-variable error stays typed (`CredentialResolutionError`) and names
    only the reference.
  - Carry the create's `api_key` and `rest_api_url` into `_terminate_orphaned_create` and into the
    compensation terminator; call `_terminate_pod(pod_id, api_key=..., rest_api_url=...)`.
- [ ] **Step 4:** `uv run pytest tests/leases tests/runpod_client tests/providers -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(leases): create and terminate pods with the provider's own RunPod credential`.

### Task 40: Arming and disarming a serve provider decide on the live row (r4-3)

**Files:** Modify `src/pitwall/api/leases/teardown.py` (disarm helper, lines 380-420),
`src/pitwall/api/leases/launch.py` (`arm_serve_provider`, lines 660-700). Tests
`tests/leases/test_teardown.py` plus an integration test on a private database.

**Grounding:**
- Disarm compares `active_lease_id` on the `Provider` snapshot read at teardown start (line 393),
  then writes `repo.patch(provider.id, config=new_config, health_status="disarmed")` built from that
  snapshot.
- Arming likewise writes `{**config_snapshot, active_pod_id, active_lease_id}`.
- A lease armed between the snapshot and the write is disarmed (`repro_disarm_race.py`:
  `new-lease` → `disarmed`), and either path can overwrite the other's config.
- PostgreSQL 18 `SELECT` reference, "The Locking Clause": `FOR UPDATE` locks the selected rows
  against concurrent `UPDATE`/`SELECT FOR UPDATE` until the transaction ends.

- [ ] **Step 1: Failing tests:**
  (a) Hermetic: arm A, snapshot, arm B, finish A's teardown with the stale snapshot → provider still
  names B, `health_status` not `disarmed`, no `lease_closed` audit row.
  (b) Integration on `pitwall_lane_c`: the same interleaving with real transactions.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** inside the existing `conn.transaction()` blocks, read
  `SELECT config, health_status FROM pitwall.providers WHERE id = $1 FOR UPDATE`, decide on that live
  row (disarm only if its `active_lease_id` equals this lease), build the new config from it, write,
  and audit with the live old value. Disarm returns `False` and writes no audit when the row names
  another lease.
- [ ] **Step 4:** `uv run pytest tests/leases -q -p no:randomly -m "not integration"` → all pass; then on
  a private database: `docker exec pitwall-integ-postgres-1 psql -U pitwall -c "CREATE DATABASE pitwall_lane_c"`,
  `PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_lane_c PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/3 uv run pytest -m "integration and not live" tests/leases -p no:randomly -q` → all pass.
- [ ] **Step 5:** Commit `fix(leases): arming and disarming decide on the live provider row`.

### Task 41: The RunPod template cache is keyed by account and every template setting (r4-4)

**Files:** Modify `src/pitwall/runpod_client/templates.py` (`config_sha` inputs at lines 965-972;
`_lookup_cached`, line 898). Modify `docs/sdlc/16-core-config.md` (line 135, the `config_sha`
sentence). Test `tests/runpod_client/test_templates.py`.

**Grounding:**
- The cache is the `runpod_templates` table keyed by `(name, config_sha)`.
- `config_sha` covers `image_ref`, entrypoint, start command, ports, and non-secret env key names
  (as `16-core-config.md:135` documents). It does not cover the container disk size, the registry
  auth id, or the account.
- RunPod templates are per account, and `containerDiskInGb` and `containerRegistryAuthId` are
  template fields (RunPod docs, "Create a new template", docs.runpod.io/api-reference/templates/POST/templates,
  fetched 2026-10-07).
- So a template from another account, or with an old 50 GB disk, is reused
  (`repro_template_cache.py`).
- The project's precedent for an account-scoped digest that never stores a credential is
  `default_launch_fingerprint` (`launch.py:1092-1125`): HMAC-SHA256 keyed by SHA-256 of a context
  string plus the API key.

- [ ] **Step 1: Failing tests:** same name and image under two API keys → two template creates; a changed
  disk size → a new create; a changed `registry_auth_id` → a new create; an unchanged request → a
  cache hit.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** add `container_disk_gb` and `registry_auth_id` (both already `ensure_template` parameters, line 940) and an account identity
  (HMAC-SHA256 of a fixed context string keyed by the resolved API key, as in
  `default_launch_fingerprint`) to the `config_sha` inputs. Existing cache rows simply miss once; no
  migration. Update the `16-core-config.md` sentence to list the new inputs and say the account
  enters only as a keyed digest.
- [ ] **Step 4:** `uv run pytest tests/runpod_client/test_templates.py tests/runpod_client -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(runpod): template cache keyed by account, disk size, and registry auth`.

### Task 42: Autopilot shadow mode counts what it would have applied (r4-5)

**Files:** Modify `src/pitwall/autopilot/controller.py` (`run` loop, lines 107-118). Test
`tests/autopilot/test_controller.py`.

**Grounding:** `docs/sdlc/21-autopilot.md:11`: "In shadow mode the controller records the same audit
trail it would use for apply mode, but it never calls the executor." The run loop advances
`applied_count` and `accepted_reserved_usd` only for `outcome == "applied"`. `_decide` returns
`"shadowed"` after every other gate passes (lines 272-285), so shadow runs never hit
`max_actions_per_run` or `max_reserved_usd_per_run` (lines 360-371) and report actions an apply run
would deny.

- [ ] **Step 1: Failing test** `test_shadow_and_apply_reach_the_same_hard_limit_decisions`: the same three
  approvable actions, `AutopilotHardLimits(max_actions_per_run=1)` (`autopilot/schema.py:62`), once in shadow and once in apply with
  an always-applying executor → shadow decides `shadowed, denied, denied` where apply decides
  `applied, denied, denied`.
- [ ] **Step 2:** Run it; expect FAIL (`shadowed` three times).
- [ ] **Step 3: Fix:** advance both counters for `outcome in {"applied", "shadowed"}` when a simulation
  is present.
- [ ] **Step 4:** `uv run pytest tests/autopilot -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(autopilot): shadow mode counts the actions it would apply`.

### Task 43: The sub-budget check runs inside the global admission lock (r5-1)

**Files:** Modify `src/pitwall/cost/sub_budgets.py` (`SubBudgetGate.try_launch`, lines 184-225).
Modify `docs/sdlc/05-cost-budget.md` (the `SubBudgetGate` entry, lines 515-522). Test
`tests/cost/test_sub_budgets.py`.

**Grounding:**
- `SubBudgetGate` is a library class (`05-cost-budget.md:517`, `SubBudgetGate(budget_gate, config,
  tag_mtd_spend)`); no module in `src/` constructs it. Tag spend comes from the injected
  `tag_mtd_spend` callable, or from `self._memory_spend`.
- `BudgetGate.try_launch_admission` (`budget_gate.py:320-397`) runs its `before_new_admission`
  callback and its `after_new_admission(conn, workload_id)` callback inside one transaction holding
  `pg_advisory_xact_lock(PITWALL_BUDGET_LOCK_KEY)` (PostgreSQL 18, "Advisory Lock Functions": held
  until the transaction ends).
- `try_launch` passes `before_new_admission` only when an idempotency key is set. Without one it
  checks the tag before taking the lock, and it updates `_memory_spend` after the transaction
  commits.
- So two concurrent admissions both fit (`repro.py`: `admitted= 2 reserved= 120 limit= 100`).
- Both gates charge the same amount: `_positive_estimate` and `_admission_cost` both use
  `upper_bound()`.

- [ ] **Step 1: Failing test** `test_concurrent_admissions_respect_the_tag_allocation`: a fake gate whose
  `try_launch_admission` serializes callers on an `asyncio.Lock` and runs both callbacks inside it
  (as the real one does), allocation 100, two concurrent `try_launch(estimate_usd=60)` with no
  idempotency key → exactly one admitted, the other raises `SubBudgetRejected("sub_budget")`.
- [ ] **Step 2:** Run it; expect FAIL (both admitted).
- [ ] **Step 3: Fix:** always pass `before_new_admission` (the tag check), and record memory spend in
  an `after_new_admission` callback rather than after the call returns. Drop the pre-lock check;
  keep it only as the replay path's `is_new=False` no-op. In `05-cost-budget.md`, state that the
  injected `tag_mtd_spend` is evaluated under the global admission lock and must count admitted
  workloads.
- [ ] **Step 4:** `uv run pytest tests/cost/test_sub_budgets.py tests/cost -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(cost): the sub-budget check runs inside the admission lock`.

### Task 44: The capability audit checks the limits and ceilings admission enforces (r5-2, r5-3)

**Files:** Modify `src/pitwall/audit/capability.py` (estimates at lines 266-280;
`_check_cost_estimate_under_cap`, line 368; `_per_request_cap`, line 535; `_resolve_budget_state`,
line 510). Test `tests/audit/test_capability_audit.py`.

**Grounding:**
- `_resolve_budget_state` applies runtime limits through `cost.budget_limits.effective_limits`, but
  `_per_request_cap()` returns the configured cap unless a budget state was injected. A lowered
  runtime per-request cap is therefore ignored by the cap check (`audit_repro.py`:
  `cap_pass= True cap= 0.050000` against a lower runtime cap).
- The audit also compares `estimator.estimate(...)` (line 271). Admission compares
  `quote_cost(...).upper_bound()`: `sync_gate._quote` (line 265) and `budget_gate._admission_cost`
  (line 441) both use the ceiling. So a 0.0001 estimate with a 0.1 ceiling passes a 0.05 cap.

- [ ] **Step 1: Failing tests:** (a) runtime per-request limit 0.01 below configured 0.05 and a 0.03
  ceiling → the cap check fails, citing 0.01. (b) estimate 0.0001, ceiling 0.1, cap 0.05 → the cap
  and headroom checks fail.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** resolve the budget state once at the start of the audit run, and make the cap
  and headroom checks read it. Quote each provider with `quote_cost(capability=...,
  provider_cost=provider.config, payload=...)` and check `upper_bound()`. Keep the estimate in
  `ProviderEstimate` for display, beside a new `ceiling_usd` field.
- [ ] **Step 4:** `uv run pytest tests/audit -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(audit): capability audit checks the limits admission enforces`.

### Task 45: An idempotency key replays only the same request (r5-4)

**Files:** Create `db/migrations/0043_idempotency_body_hash.sql`. Modify
`src/pitwall/core/idempotency.py` (`_INSERT_SQL`, `_LOOKUP_SQL`, `reserve_idempotency_key`, lines
44-92), `src/pitwall/cost/sync_gate.py` (replay load, lines 296-316),
`docs/sdlc/07-data-model-db.md` (`pitwall.idempotency_keys`, line 298). Tests
`tests/core/test_idempotency.py`, plus an integration test on a private database.

**Grounding:**
- `pitwall.idempotency_keys` (`0014_async_job_migration.sql`) holds only `idempotency_key`,
  `workload_id`, `created_at`.
- On a key conflict, `reserve_idempotency_key` compares the body hash against `workloads.input`,
  and the sync-gate replay does the same. Both skip the comparison while `input` is NULL (lines
  88-91 and 305-308), so a different body sent before the winner persists its input gets
  `{"status": "queued"}` (`idempotency_repro.py`: `mismatch_accepted= True`).
- The newest migration is `0042_config_audit_cli_reconciler_actors.sql`.
- `retention/archive.py:37` archives rows with `to_jsonb(i)`, so a new column is picked up without
  change.

- [ ] **Step 1: Failing tests:** hermetic: reserve key K for body A with a fake connection; reserve K
  again for body B while `workloads.input` is NULL → `IdempotencyMismatch`. Same body → replay.
  Integration on `pitwall_lane_d`: the migration applies, and the same two cases hold against real
  rows.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:**
  - The migration runs `ALTER TABLE pitwall.idempotency_keys ADD COLUMN body_hash TEXT` (nullable
    for existing rows) and adds a `COMMENT`.
  - The insert stores the body hash.
  - The conflict path compares the stored `body_hash` whenever it is non-NULL; the `workloads.input`
    comparison stays as the fallback for rows created before the migration.
  - The sync-gate replay loads and compares the reservation's `body_hash` the same way.
  - Document the column in `07-data-model-db.md`.
- [ ] **Step 4:** `uv run pytest tests/core tests/cost -q -p no:randomly -m "not integration"`; then
  `docker exec pitwall-integ-postgres-1 psql -U pitwall -c "CREATE DATABASE pitwall_lane_d"` and
  `PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_lane_d PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/4 uv run pytest -m "integration and not live" tests/core tests/cost tests/db -p no:randomly -q` → all pass.
- [ ] **Step 5:** Commit `fix(core): an idempotency key replays only the same request`.

### Task 46: The monthly budget alert is sent once under concurrent checks (r5-6)

**Files:** Modify `src/pitwall/cost/alerts.py` (`check_and_send_budget_alert` dedupe, lines 102-140).
Test `tests/cost/test_budget_alerts.py`.

**Grounding:**
- The monthly path tests `redis_client.exists(alert_key)`, sends, and only then calls `SET`, so two
  concurrent checks both send (`repro.py`: `alert_race send_count= 2`).
- The forecast alert already does this correctly (`finops/burn_rate_alerts.py:81-110`):
  `_redis_reserve` claims a pending key with `set(key, owner, ex=…, nx=True)`; `_redis_release`
  and `_redis_complete` use owner-checked `EVAL` scripts.
- Redis `SET` (redis.io/docs/latest/commands/set, fetched 2026-10-07): with `NX` the key is set
  only if it does not exist, and the reply is null when the condition was not met.

- [ ] **Step 1: Failing test:** two concurrent checks at 90% against one fake Redis (with a real `SET NX`
  semantic) and a notifier that yields during send → one send, the other `skipped_duplicate=True`.
  A failed send releases the claim, so a later check can retry.
- [ ] **Step 2:** Run it; expect FAIL (`send_count= 2`).
- [ ] **Step 3: Fix:** reserve with `SET NX EX` (owner token, pending TTL) before sending. On success,
  complete the key to the sent marker with `_BUDGET_ALERT_TTL_SECONDS`; on failure, release it.
  Reuse the burn-rate helpers by moving them to a shared private module in `cost/`, owned by this
  lane; `finops/burn_rate_alerts.py` then imports them, with no behaviour change there.
- [ ] **Step 4:** `uv run pytest tests/cost/test_budget_alerts.py tests/finops -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(cost): the monthly alert is sent once`.

### Task 47: Concurrent first use creates one database pool (r5-5)

**Files:** Modify `src/pitwall/db/__init__.py` (`get_pool`, lines 46-68; `close_pool`, lines 71-76).
Test `tests/db/test_pool_lifecycle.py`.

**Grounding:**
- `get_pool` checks `_pool is None` and then awaits `asyncpg.create_pool`, so two first callers both
  create a pool; one is never closed (`repro.py`: `pool_race created= 2`).
- An `asyncio.Lock` binds to the first event loop that contends for it, and a later contention from
  another loop raises `RuntimeError(... is bound to a different event loop)`. That is the 3.14.7
  `asyncio/mixins.py` `_LoopBoundMixin._get_loop`. The CLI and the tests call `asyncio.run`
  repeatedly, so a bare module-level lock would break them.

- [ ] **Step 1: Failing tests:** patch `asyncpg.create_pool` with a fake that yields before returning →
  two concurrent `get_pool()` calls make one creation and return the same object, and
  `close_pool()` closes it. A second `asyncio.run` after `close_pool()` works (the lock is not tied
  to the old loop).
- [ ] **Step 2:** Run them; expect FAIL (two creations).
- [ ] **Step 3: Fix:** keep `(loop, asyncio.Lock)` at module level and create a new lock whenever
  `asyncio.get_running_loop()` differs from the stored loop. Take the lock, re-check `_pool`, then
  create.
- [ ] **Step 4:** `uv run pytest tests/db/test_pool_lifecycle.py tests/db -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(db): concurrent first use creates one pool`.

### Task 48: Migration drift reports applied versions missing from the package (r5-7)

**Files:** Modify `src/pitwall/migrations.py` (`detect_drift`, lines 100-134; `DriftEntry`, line
137) and `src/pitwall/db/__init__.py` (migrate drift message, lines 403-410). Test
`tests/test_migration_discovery.py`.

**Grounding:**
- `detect_drift` iterates only the expected (packaged) records, so a version in
  `pitwall.schema_migrations` with no packaged file is ignored, and `pending` treats the run as
  clean (`migration_repro.py`: `orphan_applied_migration_drift= []`).
- `DriftEntry` requires `filename` and `current_checksum`, which an orphan does not have.

- [ ] **Step 1: Failing tests:** applied `{"0001": a, "0002": b}`, packaged only `0002` with checksum
  `b` → one drift entry for `0001`, kind `missing_from_package`. `db migrate` refuses with a message
  naming version `0001` as applied but not packaged.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** add `kind: Literal["checksum_changed", "missing_from_package"]` to `DriftEntry`
  and make `filename` and `current_checksum` optional for the second kind. Compare both directions.
  The CLI prints one line per kind.
- [ ] **Step 4:** `uv run pytest tests/test_migration_discovery.py tests/db -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(db): drift reports applied migrations missing from the package`.

### Task 49: Drill evidence filters by type before the limit (r5-8)

**Files:** Modify `src/pitwall/db/drill_evidence.py` (`get_drill_evidence`, lines 160-222). Create
`tests/db/test_drill_evidence.py`.

**Grounding:**
- The query applies `ORDER BY created_at DESC LIMIT $n` and filters `drill_type` in Python
  afterwards, so newer drills of other types hide matches (`drill_repro.py`:
  `wanted_results= []`).
- `pitwall.config_audit.new_value` is `JSONB` (`0007_config_audit.sql:8`).
- PostgreSQL 18, "JSON Functions and Operators": `jsonb ->> text` extracts the field as `text`, so
  `new_value ->> 'drill_type' = $k` is a valid SQL predicate.

- [ ] **Step 1: Failing tests:** hermetic: a fake pool records the SQL and args; with `drill_type`
  set, the SQL contains `new_value ->> 'drill_type' = $` before `LIMIT` and the args carry the
  type. Integration on `pitwall_lane_e`: rows `wanted` (older) and `newer` (newer), `limit=1`,
  `drill_type="wanted"` → returns the `wanted` row.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** append the parameterised predicate to `conditions` and remove the Python filter.
- [ ] **Step 4:** hermetic `uv run pytest tests/db/test_drill_evidence.py -q -p no:randomly -m "not integration"`;
  `docker exec pitwall-integ-postgres-1 psql -U pitwall -c "CREATE DATABASE pitwall_lane_e"`, then
  `PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_lane_e PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/5 uv run pytest -m "integration and not live" tests/db -p no:randomly -q` → all pass.
- [ ] **Step 5:** Commit `fix(db): drill evidence filters by type before the limit`.

### Lane ownership (second wave)

| Lane | Tasks | Owns (exclusive) |
|---|---|---|
| F API/MCP/policy | 50, 51, 52, 53 | `src/pitwall/api/routes/messages.py`, `src/pitwall/api/routes/openai.py` (shared admission helpers only), `src/pitwall/api/anthropic_translate.py`, `src/pitwall/mcp/relay.py`, `src/pitwall/policy/engine.py`, `docs/sdlc/02-api-rest.md`, `tests/api/test_messages_route.py`, `tests/api/test_anthropic_translate.py`, `tests/mcp/test_relay.py`, `tests/policy/` |
| G agents profiles | 54, 55, 56 | `src/pitwall/agents/{profiles,profiles_sync,profiles_resolve,profiles_probe}.py`, `src/pitwall/agents/harnesses/{hermes,dsh,cline,opencode,codex}.py`, `tests/agents/test_profiles*.py`, `tests/agents/test_harness_*.py` |
| H workbench evidence | 57, 58 | `src/pitwall/workbench/comparison/{reevaluate,evidence}.py`, `src/pitwall/workbench/hosted/evaluation.py`, `tests/workbench/test_{reevaluate,comparison_evidence,hosted_evaluation}*.py` |
| B (adds) | 59 | `src/pitwall/tui/leases.py`, `tests/tui/test_leases_screen.py` |
| I providers/RunPod clients | 60, 61, 62, 63 | `src/pitwall/providers/{lambda_cloud,vast,provisioning,runpod}.py`, `src/pitwall/runpod_client/{serverless_lb,pod_logs}.py`, `tests/providers/`, `tests/runpod_client/test_pod_logs.py`, new `tests/runpod_client/test_serverless_lb.py` |
| J config/CLI/doctor | 64, 65, 66, 67, 69 | `src/pitwall/config.py` (rate-limit parsers only), `src/pitwall/rate_limits/` token bucket constructor, `src/pitwall/cli/{mcp_install,capabilities,endpoints,init,serve_model,models}.py`, `src/pitwall/agents/cli.py` (`setup_mcp` preview only), `src/pitwall/doctor.py`, their tests |
| D (adds) | 68, 70 | `src/pitwall/cost/simulator.py`, `src/pitwall/audit/_runtime_config.py`, their tests |
| I (adds) | 71, 72 | `src/pitwall/routing/lockout.py`, `src/pitwall/db/quota_repository.py` (`set_lockout` only), `src/pitwall/runpod_client/mounts.py`, their tests; private database `pitwall_lane_i`, Redis DB 6 |
| C (adds) | 73 | `src/pitwall/autopilot/schema.py`, its test |

Lane I's `providers/runpod.py` edit (Task 62) is limited to the embedding call; Task 39 (lane C) reads
but does not modify that file. The file lists name test files by their existing prefixes; a lane
adds cases to the existing file for its module and creates one only when none exists.

### Task 50: Streaming `/v1/messages` is inspected, budget-admitted, and recorded before egress (r3-1, Critical)

**Files:** Modify `src/pitwall/api/routes/messages.py` (`create_messages`, lines 312-344;
`_stream_messages`, lines 258-310; `_execute_stream_chain`, lines 207-218). Reuse, do not copy,
`src/pitwall/api/routes/openai.py` admission helpers (`_admit_passthrough`, line 662;
`_OpenAIFallbackBudgetQuote`, line 190; `_record_proxy_cancellation`, line 530;
`_record_pre_egress_failure`, line 586; `_record_upstream_outcome`, line 784; pre-spend inspection
via `security.pre_spend.get_pre_spend_inspection_service`, imported at line 78). Test
`tests/api/test_messages_route.py`.

**Grounding:**
- `create_messages` sends `stream: true` to `_stream_messages` before the non-stream
  `service.execute_sync` (line 346). Only that path runs pre-spend inspection and budget admission
  (`routing/production.py:886-1013`).
- `_stream_messages` selects providers and calls `execute_openai_with_fallback`
  (`routing/fallback.py:103-185`), which only handles transport and fallback. So a stream reaches a
  paid provider with no inspection, admission, workload, or cost record.
- The OpenAI proxy route already streams through an admitted, recorded chain. That is the contract
  `docs/sdlc/02-api-rest.md:520` lists for `/v1/messages`: a `spend` route.

- [ ] **Step 1: Failing tests:**
  (a) A budget gate that rejects, `stream: true` → Anthropic error envelope (`type: error`), zero
  upstream calls.
  (b) A payload carrying a credential-shaped secret → rejected by pre-spend inspection with zero
  upstream calls.
  (c) An admitted stream → one workload row, `X-Pitwall-Workload-ID` header, and the outcome
  recorded when the stream ends.
  (d) A client disconnect mid-stream → the cancellation record is written.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** before any upstream I/O, run the same pre-spend inspection and
  `_admit_passthrough` admission the OpenAI streaming route uses (move the helpers into a shared
  private module under `api/routes/` if a cross-route import would otherwise be needed), then relay
  through the admitted chain and settle with `_record_upstream_outcome` /
  `_record_proxy_cancellation`.
- [ ] **Step 4:** `uv run pytest tests/api -q -p no:randomly -m "not integration"` → all pass; the
  journeys J33 and J34 pass in Task 74's journey run.
- [ ] **Step 5:** Commit `fix(api): streaming Messages requests are inspected, admitted, and recorded`.

### Task 51: The Anthropic translator refuses unsupported blocks and keeps stream errors (r3-5, r3-6, r3-7)

**Files:** Modify `src/pitwall/api/anthropic_translate.py` (`anthropic_to_openai` block loop,
lines 99-108; SSE splitter, line 260; stream translator, line 321). Test
`tests/api/test_anthropic_translate.py` (or the existing translator test module) and
`tests/api/test_messages_route.py`.

**Grounding:**
- The block loop keeps `text` and `tool_use`, maps `tool_result`, and silently drops every other
  type, so an image-only message is billed as an empty prompt. `docs/sdlc/02-api-rest.md:520`
  promises text, `tools`, `tool_use`, and `tool_result` only.
- Anthropic streaming docs (docs.anthropic.com/en/api/streaming, fetched 2026-10-07): a stream can
  carry `event: error` with `data: {"type":"error","error":{"type":…,"message":…}}` after a 200.
  The translator drops upstream error frames and still emits `message_stop`
  (`messages_error.py`: `error included: False`, `message_stop included: True`).
- WHATWG HTML §9.2 "Server-sent events" (fetched 2026-10-07): a line ends with CRLF, LF, or CR. The
  splitter recognises only `\n\n`.

- [ ] **Step 1: Failing tests:**
  (a) An `image` block, or any type outside {text, tool_use, tool_result} → 400 Anthropic
  `invalid_request_error` naming the block type, before budget or provider work.
  (b) An upstream stream that yields text and then an error frame or a failing iterator → the
  translated stream ends with `event: error` and no `message_stop`.
  (c) A CRLF-framed upstream stream, including a `\r\n\r\n` split across chunks → the same events as
  LF framing.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** raise `AnthropicInvalidRequest` for unsupported block types. Map upstream error
  frames and iterator failures to one Anthropic `error` event, and suppress `message_stop` after an
  error. Split events on any of `\r\n\r\n`, `\n\n`, `\r\r`, carrying partial frames across chunks.
  Update the `02-api-rest.md` row to say other block types are refused.
- [ ] **Step 4:** `uv run pytest tests/api -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(api): Messages translation refuses unsupported blocks and keeps stream errors`.

### Task 52: The broker MCP relay survives a child exit mid-write and never steals a client reply (r3-3, r3-4)

**Files:** Modify `src/pitwall/mcp/relay.py` (`_from_client` write, line 135; reply routing,
line 192). Test `tests/mcp/test_relay.py`.

**Grounding:**
- A child that exits between `_ready` and the stdin write raises `BrokenPipeError` out of
  `Relay.run`, ending the client session (`relay_pipe.py`: `raised: BrokenPipeError`,
  `pending: {'7': 7}`).
- The relay's fixed internal replay id `"pitwall-relay-replay"` is matched before pending client
  ids, so a client request using that id never gets its reply (`relay_id.py`: `forwarded: []`).
- JSON-RPC 2.0 §5 (jsonrpc.org/specification): the response `id` MUST equal the request's `id`, so
  the relay must route every reply by the id the client sent.
- Task 10's contract still applies: relay failures answer pending requests with application error
  `-31010`, marked `retryable`.

- [ ] **Step 1: Failing tests:** (a) close the child's stdin after `_ready` and send a request → the
  request gets a `-31010` retryable error, `run` keeps serving, and the supervisor restarts the
  child. (b) A client request with id `"pitwall-relay-replay"` while no replay is outstanding gets
  its reply.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** catch `BrokenPipeError`/`ConnectionResetError` on the child write, fail that
  request with `-31010 retryable`, and trigger the existing restart path. Generate the internal
  replay id per replay (for example `pitwall-relay-replay-<uuid4>`), never one a pending client id
  holds, and match it only while that replay is outstanding.
- [ ] **Step 4:** `uv run pytest tests/mcp -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(mcp): relay survives a child exit mid-write and routes every client id`.

### Task 53: Policy violation evidence redacts camel-case credential keys (r3-2)

**Files:** Modify `src/pitwall/policy/engine.py` (`_is_sensitive_key`, line 302). Tests under
`tests/policy/`.

**Grounding:**
- `_is_sensitive_key` lowercases the key and maps `-` to `_`, then looks for fragments such as
  `api_key`. So `apiKey` (→ `apikey`) and `clientSecret` (→ `clientsecret`) are previewed unredacted.
- The project already normalises camel case, snake case, and separators for credential-shaped keys:
  `docs/sdlc/16-core-config.md:135` says provider config writes "reject raw credential-shaped
  values recursively across snake, camel, and separator variants".

- [ ] **Step 1: Failing test:** violation evidence for `{"apiKey": "…", "clientSecret": "…",
  "auth": {"accessToken": ["…"]}}` → every value `[REDACTED]`.
- [ ] **Step 2:** Run it; expect FAIL.
- [ ] **Step 3: Fix:** normalise keys with `core.models._normalize_provider_config_key` (line 415,
  "Normalize snake, kebab, dotted, spaced, and camel-case config keys"; promote it to a public
  helper), the one the provider-config writer uses, and apply it to nested mappings and lists under a
  sensitive key.
- [ ] **Step 4:** `uv run pytest tests/policy tests/security -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(policy): violation evidence redacts camel-case credential keys`.

### Task 54: Agent profiles refuse credentials embedded in endpoint URLs (r1b-1)

**Files:** Modify `src/pitwall/agents/profiles.py` (endpoint validation, lines 157 and 229) and
`src/pitwall/agents/cli.py` (`profiles` add/edit path, line 646; owned by lane G for this function
only). Tests `tests/agents/test_profiles*.py`.

**Grounding:**
- The agents docs state that key values never live in profiles: `apiKeyEnv` names an environment
  variable.
- Validation accepts `baseUrl` values such as `https://user:sk-…@host/v1` or `?api_key=…`, which are
  saved, loaded, and shown (`repro_url_secret.py`: `saved URL contains credential: True`).
- RFC 3986 §3.2.1 defines the userinfo subcomponent and says its `user:password` form is
  deprecated.

- [ ] **Step 1: Failing tests:** saving or loading a profile (inline endpoint and shared endpoint)
  whose `baseUrl` has userinfo, or a query parameter named like a credential (`key`, `api_key`,
  `token`, `secret`, `password`, any case) → a validation error naming the field, not the value.
  `profiles show` never prints such a URL.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** parse with `urllib.parse.urlsplit`; reject `username`/`password` and
  credential-named query keys in the validator both paths share.
- [ ] **Step 4:** `uv run pytest tests/agents -q -p no:randomly -k profile` → all pass.
- [ ] **Step 5:** Commit `fix(agents): profiles refuse credentials embedded in endpoint URLs`.

### Task 55: Profile sync uses each route's effective harness and one expected URL (r1b-2, r1b-3)

**Files:** Modify `src/pitwall/agents/profiles_sync.py` (route filter, line 47),
`src/pitwall/agents/harnesses/{hermes.py:119,dsh.py:89,cline.py:96,opencode.py:62,107}`,
`src/pitwall/agents/profiles_resolve.py` (line 320). Tests `tests/agents/test_profiles_sync*.py`.

**Grounding:**
- Routes that inherit `defaults.endpointHarness` have no explicit `entry.harness`, and the
  Hermes/dsh/Cline adapters filter on `entry.harness` only. Inherited routes therefore resolve to
  the harness but are never synced (`repro_default_endpoint.py`: `has_provider= False
  status= missing`; `repro_cline_default.py`: `sync_commands: []`).
- OpenCode's Anthropic Model Studio sync writes `…/apps/anthropic/v1`, while status compares
  against the unsuffixed endpoint. A freshly synced route is therefore always `stale`
  (`repro_sync.py`).

- [ ] **Step 1: Failing tests:** inherited-harness routes for Hermes, dsh, and Cline appear in their sync
  plans and are `current` after apply; an Anthropic Model Studio route is `current` right after
  OpenCode sync.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** resolve each route's effective harness once (`profiles_resolve`) and give
  adapters the resolved routes. Put the OpenCode expected-URL computation in one helper, used by both
  plan/apply and status.
- [ ] **Step 4:** `uv run pytest tests/agents -q -p no:randomly -k "sync or profile or opencode or hermes or dsh or cline"` → all pass.
- [ ] **Step 5:** Commit `fix(agents): profile sync covers inherited harness routes; OpenCode status matches its sync`.

### Task 56: The Codex adapter honours `CODEX_HOME`; the Model Studio probe tolerates malformed JSON (r1b-4, r1b-5)

**Files:** Modify `src/pitwall/agents/harnesses/codex.py` (lines 40, 72) and
`src/pitwall/agents/profiles_probe.py` (lines 234, 251). Tests `tests/agents/test_harness_codex*.py`
and `tests/agents/test_profiles_probe*.py`.

**Grounding:**
- Codex reads its config from `$CODEX_HOME/config.toml` (default `~/.codex`; OpenAI Codex docs, "Environment variables",
  developers.openai.com/codex/environment-variables, fetched 2026-10-07). The adapter always reads `~/.codex/config.toml`, so telemetry records the
  default account's model (`repro_codex_home.py`: `gen_ai.request.model=old-model`).
- The probe calls `.get` on whatever JSON arrives. A JSON list raises `AttributeError`
  (`repro_probe.py`) instead of returning a `down` `ProbeResult`.

- [ ] **Step 1: Failing tests:** `CODEX_HOME=<tmp>` with `model = "new-model"` → the adapter reports
  `new-model`. A probe answering `[]` or `{"data": "x"}` → a `ProbeResult` with status `down` and a
  bounded detail.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** read `Path(env.get("CODEX_HOME") or ~/.codex) / "config.toml"`. Check that the
  payload is a mapping and `data` is a list of mappings before iterating.
- [ ] **Step 4:** `uv run pytest tests/agents -q -p no:randomly -k "codex or probe"` → all pass.
- [ ] **Step 5:** Commit `fix(agents): Codex adapter honours CODEX_HOME; probe tolerates malformed JSON`.

### Task 57: Reevaluation never overwrites its source report (r2b-1)

**Files:** Modify `src/pitwall/workbench/comparison/reevaluate.py` (`reevaluate_comparison` output,
lines 510-545; `reevaluate_child_only`, lines 601-607, 717). Tests: the existing reevaluation test
module under `tests/workbench/`.

**Grounding:**
- `reevaluate_comparison` writes `output_path.write_text(...)` with no source check at all.
- `reevaluate_child_only` compares `os.path.abspath` strings (line 605). Python docs: `abspath` does
  not resolve symlinks, while `os.path.samefile` compares device and inode, so it also catches
  hardlinks.
- An alias path therefore overwrites the preserved original, which the report then describes as
  unmodified (`originalReportUnmodified: true`).

- [ ] **Step 1: Failing tests:** for both commands: the same path, a symlink to the source, and a
  hardlink to the source → `ReevaluationError`, source bytes unchanged. A distinct output → written.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** one helper refuses when the output exists and `os.path.samefile(source,
  output)`, or when `Path(output).resolve() == Path(source).resolve()`. Write through a temp file plus
  `os.replace` into the distinct path, mode 0600.
- [ ] **Step 4:** `uv run pytest tests/workbench -q -p no:randomly -k reevaluat` → all pass.
- [ ] **Step 5:** Commit `fix(workbench): reevaluation never overwrites its source report`.

### Task 58: Child read evidence must come from the expected child; hosted evaluation accepts a later passing run (r2b-2, r2b-3)

**Files:** Modify `src/pitwall/workbench/comparison/evidence.py` (`persisted_child_evidence`,
lines 124-137) and `src/pitwall/workbench/hosted/evaluation.py` (line 152). Tests: the existing
evidence and hosted-evaluation test modules.

**Grounding:**
- A receipt-supplied session file is counted if it differs from the parent's path and proves a
  read, without checking its header. The fallback scan (lines 155-164) does check the header through
  `files_containing_child_evidence`. So another child's session satisfies the child-only gate
  (`runner.py:807`).
- Hosted evaluation returns at the first successful `bash` execution of the command, even when that
  output lacks passing-test evidence, and ignores a later passing run.

- [ ] **Step 1: Failing tests:**
  (a) A receipt pointing at a session whose header has `childId=other-child` or another
  `parentSession` → `correlated=False`.
  (b) A failing-output run followed by a passing run of the same command → evaluation passes, with
  the earlier failure kept as diagnostic evidence.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** validate the receipt file's typed session header against `receipt.child_id` and
  the expected parent before counting it, using the same header check as the fallback scan. Evaluate
  every correlated allowed invocation and take the last completed one as the final state.
- [ ] **Step 4:** `uv run pytest tests/workbench -q -p no:randomly -k "evidence or hosted"` → all pass.
- [ ] **Step 5:** Commit `fix(workbench): child evidence must name the expected child; later passing run counts`.

### Task 59: A failed lease refresh never shows a false zero (r2b-4)

**Files:** Modify `src/pitwall/tui/leases.py` (refresh failure path, line 272). Test
`tests/tui/test_leases_screen.py`. Lane B, after Task 37 (same pattern as the Resources fix).

**Grounding:** on failure the screen renders `0 active pod leases` although the count is unknown and
the previous snapshot had active leases. Task 37 fixes the same pattern in `resources.py`, so both
screens should behave alike.

- [ ] **Step 1: Failing test:** success with two active leases, then a raising source → the summary
  still shows the last snapshot (marked stale) and the error line shows the failure.
- [ ] **Step 2:** Run it; expect FAIL (`0 active pod leases`).
- [ ] **Step 3: Fix:** on failure, re-render the last successful snapshot as stale when there is one,
  otherwise `Unavailable`.
- [ ] **Step 4:** `uv run pytest tests/tui -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(tui): a failed lease refresh keeps the last known count`.

### Task 60: Lambda Cloud provisioning launches exactly one instance (r4b-1)

**Files:** Modify `src/pitwall/providers/lambda_cloud.py` (payload validation, line 536; provision,
line 272). Test `tests/providers/test_lambda_cloud_provider.py`.

**Grounding:**
- Lambda Cloud API "Launch instances" (docs.lambda.ai/api/cloud, fetched 2026-10-07): `quantity` sets
  how many instances launch, and the response lists them in `instance_ids`.
- The adapter accepts any positive `quantity` but records one lease for `instance_ids[0]` (line 717),
  so the other VMs are billed and never torn down (`repro_lambda_quantity.py`:
  `provider_created ['vm-1', 'vm-2']`, `recorded_external_ids ['vm-1']`).
- The adapter contract is one lease per external resource (`docs/sdlc/20-provider-plugins.md`).

- [ ] **Step 1: Failing tests:** `quantity: 2` → a typed validation error before admission and before
  any HTTP call. A provider response with two ids for `quantity: 1` → both ids terminated and a
  typed error.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** reject `quantity != 1` in payload validation. Treat a multi-id response as a
  provider contract violation: terminate every returned id, then raise.
- [ ] **Step 4:** `uv run pytest tests/providers -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(providers): Lambda provisioning launches exactly one instance`.

### Task 61: A completed compute admission replays only the same request (r4b-2)

**Files:** Modify `src/pitwall/providers/provisioning.py` (`load_provision_replay`, line 153) and its
callers `src/pitwall/providers/vast.py:295`, `src/pitwall/providers/lambda_cloud.py:247`. Tests
`tests/providers/test_provisioning_state.py` and the adapter tests.

**Grounding:**
- The replay lookup matches key, capability, provider, and type, but does not read the stored
  request digest, so a different request with the same key gets the original VM
  (`repro_replay.py`: `selected_input_column False`, `different_request_replayed …vm-original`;
  `repro_replay_adapter.py`: `provider_calls 0`).
- The launch path already carries `request_fingerprint` (`launch.py`, `default_launch_fingerprint`).
  Task 45 fixes the same class of defect for the sync-gate idempotency keys.

- [ ] **Step 1: Failing tests:** both adapters: reuse a key with a different payload → a typed
  idempotency-mismatch error, with no provider call and no replay. The same payload → replay. A
  stored row with no digest (legacy) → replay only when the caller's digest is also absent;
  otherwise mismatch.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** pass the current fingerprint to `load_provision_replay`, select the stored
  digest, and compare before returning the external id.
- [ ] **Step 4:** `uv run pytest tests/providers -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(providers): compute replay requires the same request`.

### Task 62: Broker-internal RunPod embedding never sends the RunPod key to Pitwall (r4b-3)

**Files:** Modify `src/pitwall/runpod_client/serverless_lb.py` (Pitwall mode, lines 125-140) and
`src/pitwall/providers/runpod.py` (embedding call, line 287). Create
`tests/runpod_client/test_serverless_lb.py` (no existing module covers Pitwall mode).

**Grounding:**
- In Pitwall embedding mode the client posts to `{settings.pitwall_base_url}/v1/inference` and
  copies its `Authorization` header, which holds the RunPod bearer key. The RunPod adapter's
  synchronous inference (`providers/runpod.py:287`) builds that same client. So a broker request can
  loop back into the broker, carrying the RunPod key to the Pitwall origin.

- [ ] **Step 1: Failing tests:** an adapter `infer` with the Pitwall-mode flag on → the request goes to
  `https://{endpoint}.api.runpod.ai`, never to `pitwall_base_url`. A Pitwall-mode client never sends
  the RunPod `Authorization` to the Pitwall origin.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** the adapter constructs the client in direct LB mode explicitly. Pitwall mode, for
  external callers, sends a Pitwall API token header (`PITWALL_API_TOKEN` by name) and never copies
  the RunPod key.
- [ ] **Step 4:** `uv run pytest tests/runpod_client tests/providers -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(runpod): broker embedding never sends the RunPod key to Pitwall`.

### Task 63: The bounded pod-log reader reports truncation exactly (r4b-4)

**Files:** Modify `src/pitwall/runpod_client/pod_logs.py` (lines 132, 137). Test
`tests/runpod_client/test_pod_logs.py`.

**Grounding:** at an exact byte cap the reader reports `truncated=True` with nothing left; at a line
cap it reports `False` with lines remaining (`repro_pod_logs.py`:
`exact_byte_response … truncated=True`, `two_line_response … truncated=False`).

- [ ] **Step 1: Failing tests:** content exactly at the byte cap → `False`; one byte over → `True`;
  exactly N lines at an N-line cap → `False`; N+1 → `True`; a cap reached on a chunk boundary.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** on reaching either cap, read at most one more byte (within the deadline) to
  decide whether content remains.
- [ ] **Step 4:** `uv run pytest tests/runpod_client/test_pod_logs.py -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(runpod): pod log truncation flag is exact`.

### Task 64: Rate-limit windows must be finite (r5b-1)

**Files:** Modify `src/pitwall/config.py` (`parse_inbound_rate_limit` and `parse_rate_limit`, lines
1155-1165 and 1207) and the token bucket constructor in `src/pitwall/rate_limits/`. Tests: the
existing config and rate-limit test modules.

**Grounding:**
- The parsers reject `window_s <= 0` only. `float("nan") <= 0` is `False` and `inf` passes, so
  `"1/nan"` creates a limiter that admits every request (`rate_nan.py`: `first= True second= True`).
- Python docs, `math.isfinite`: True only when the value is neither infinite nor NaN.

- [ ] **Step 1: Failing tests:** `"1/nan"`, `"1/inf"`, `"1/-inf"` → a configuration error from both
  parsers, and `TokenBucket(window=nan)` → `ValueError`.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** require `math.isfinite(window_s) and window_s > 0` in both parsers and in the
  bucket constructor.
- [ ] **Step 4:** `uv run pytest tests -q -p no:randomly -m "not integration" -k "rate_limit or ratelimit or config"` → all pass.
- [ ] **Step 5:** Commit `fix(config): rate-limit windows must be finite`.

### Task 65: `pitwall mcp install` previews only the managed channel entry and honours scope (r5b-2, r5b-6)

**Files:** Modify `src/pitwall/cli/mcp_install.py` (`_register_channel`, lines 95-102) and the
preview in `src/pitwall/agents/cli.py` `setup_mcp`. Tests: the existing `mcp install` and agents
`setup` test modules.

**Grounding:**
- `_register_channel` calls `setup_mcp(selected, None, remove, args.dry_run, True)`, which is
  user-scope only, even for `--scope project`. It prints a unified diff of the whole existing harness
  config, context lines included (`channel_diff.py`: `marker_in_rendered_diff= True`), so unrelated
  config text, possibly secrets, reaches the terminal.
- Task 14 established the related rule: config content is never echoed (`ConfigFileError`).

- [ ] **Step 1: Failing tests:**
  (a) A dry run with a harness config containing a marker line outside the managed entry → output
  shows only the target path and the managed entry.
  (b) `--scope project` → the channel is registered for the project (where the harness supports
  project scope), or the command refuses before any broker or channel write.
  (c) The channel step runs only for harnesses whose broker step succeeded.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** the preview renders only the managed entry and path. Pass scope and project root
  through; harnesses without project-scope channel support refuse up front with a named error.
- [ ] **Step 4:** `uv run pytest tests/cli tests/agents -q -p no:randomly -k "mcp_install or setup_mcp"` → all pass.
- [ ] **Step 5:** Commit `fix(cli): mcp install previews only the managed entry and honours scope`.

### Task 66: CLI error boundaries never print exception text; init's smoke command is shell-quoted (r5b-3, r5b-10)

**Files:** Modify `src/pitwall/cli/capabilities.py` (lines 144, 221), `src/pitwall/cli/endpoints.py`
(lines 251, 303), `src/pitwall/cli/init.py` (lines 255-265, 281), `src/pitwall/cli/serve_model.py`
(line 462). Tests: the existing CLI test modules.

**Grounding:**
- These catch-all handlers serialise `str(exc)`. A database error carrying
  `password=SYNTHETIC_TEST_MARKER` is printed verbatim (`cli_leak.py`). The newer commands use a
  fixed-code, non-reflecting boundary.
- `init` prints `-d '{json}'` with user-supplied text inside single quotes, which breaks on an
  apostrophe. Python docs, `shlex.quote`: returns a shell-escaped version of the string.

- [ ] **Step 1: Failing tests:** each handler with an exception whose message contains a marker →
  output has a fixed error code, the exception class, and no marker. An init smoke command whose
  capability name contains `'` and `$(…)` → it parses back to the same argv with `shlex.split`.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** route these handlers through the existing safe CLI error boundary (the one the
  newer commands use), and quote the URL and body with `shlex.quote`.
- [ ] **Step 4:** `uv run pytest tests/cli -q -p no:randomly -m "not integration"` → all pass.
- [ ] **Step 5:** Commit `fix(cli): error boundaries never reflect exception text; init command is shell-quoted`.

### Task 67: `pitwall doctor` reports safe reasons and probes the loaded settings (r5b-4, r5b-7)

**Files:** Modify `src/pitwall/doctor.py` (section failure, line 104; registry probes, lines 593 and
613). Test: the existing doctor test module.

**Grounding:**
- A failing section's `str(exc)` lands in `DoctorCheck.detail` and in both report formats
  (`doctor_leak.py`: `secret_in_report= True`).
- Registry mode probes raw `DATABASE_URL`/`REDIS_URL` instead of the loaded `PitwallSettings`, so a
  TOML-configured database is probed as empty (`doctor_toml.py`: `probed= [('db', '')]`).

- [ ] **Step 1: Failing tests:** a section raising with a marker → detail holds the section and
  exception class only. Settings from TOML with no environment URLs → the probe receives the TOML
  values.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** keep the class name and a safe reason only, and probe `loaded.database_url` and
  `loaded.redis_url` when settings loaded.
- [ ] **Step 4:** `uv run pytest tests -q -p no:randomly -m "not integration" -k doctor` → all pass.
- [ ] **Step 5:** Commit `fix(doctor): safe failure reasons; probes use loaded settings`.

### Task 68: The free-prong comparison counts a shared quota pool once (r5b-5)

**Files:** Modify `src/pitwall/cost/simulator.py` (line 502). Test: the existing simulator test
module. Lane D.

**Grounding:** the comparison sums every provider's quota record without deduplicating a shared
`pool_key`, so two providers drawing on one free pool double its coverage (`free_pool.py`:
`shared_pool_coverage= 100.0` where the true figure is half).

- [ ] **Step 1: Failing test:** two providers sharing one `pool_key` → coverage counts the pool once.
- [ ] **Step 2:** Run it; expect FAIL.
- [ ] **Step 3: Fix:** deduplicate quota records by `pool_key` (records without one stay per provider)
  before summing.
- [ ] **Step 4:** `uv run pytest tests/cost -q -p no:randomly -m "not integration" -k simulat` → all pass.
- [ ] **Step 5:** Commit `fix(cost): a shared free quota pool is counted once`.

### Task 69: `pitwall models fit --inventory` prints a human result (r5b-8)

**Files:** Modify `src/pitwall/cli/models.py` (line 206). Test: the existing models CLI test module.

**Grounding:** the local-inventory branch always calls `Output.set_json()` and `emit()`, which print
only in `--json` mode, so the human command exits 0 with no output (`models_local.py`).

- [ ] **Step 1: Failing test:** `models fit --inventory <file>` without `--json` → a human table of the
  fit results; with `--json` → the same JSON as today.
- [ ] **Step 2:** Run it; expect FAIL.
- [ ] **Step 3: Fix:** render the human result through the same table helper the remote branch uses.
- [ ] **Step 4:** `uv run pytest tests/cli -q -p no:randomly -k models` → all pass.
- [ ] **Step 5:** Commit `fix(cli): models fit --inventory prints a human result`.

### Task 70: Malformed audit timeout settings fail the check, not the runner (r5b-9)

**Files:** Modify `src/pitwall/audit/_runtime_config.py` (`timeout_config`, line 74). Test: the
existing audit runtime-config test module. Lane D.

**Grounding:** `timeout_config` applies raw `int()` to `PITWALL_AUDIT_EXEC_TIMEOUT_S`,
`PITWALL_AUDIT_EXEC_TIMEOUT_MAX_S`, and `PITWALL_DEFAULT_LEASE_TTL_S` outside `CheckFailed`
handling, so `abc` crashes the whole audit run. The module already has `_int_config` for validated
parsing.

- [ ] **Step 1: Failing test:** `PITWALL_AUDIT_EXEC_TIMEOUT_S=abc` → that check fails with a named
  reason, and the runner completes and emits the full report.
- [ ] **Step 2:** Run it; expect FAIL.
- [ ] **Step 3: Fix:** parse through `_int_config`, raising `CheckFailed` on a bad value.
- [ ] **Step 4:** `uv run pytest tests/audit -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(audit): malformed timeout settings fail the check, not the runner`.

### Task 71: Lockout persistence never lets an older state overwrite a newer one (r4c-1)

**Files:** Modify `src/pitwall/routing/lockout.py` (`_persist`, lines 119-136; the transition methods
that call it) and `src/pitwall/db/quota_repository.py` (`set_lockout`, lines 69-81). Tests: the
existing lockout and quota-repository test modules, plus an integration case on `pitwall_lane_i`.
Lane I.

**Grounding:** every transition schedules an independent `loop.create_task(run())`, and
`set_lockout` stamps `updated_at` when the write runs, not when the transition happened. So a
delayed older write lands last and replaces a newer failure or success in
`provider_quotas.evidence.lockout` (`repro.py`: `persist order [2, 1] memory 2`). The in-memory
state stays correct; only the persisted copy (read at startup through `load`) regresses.

- [ ] **Step 1: Failing tests:** a delayed persister completing failure #1 after failure #2, and
  failure after success → the stored state equals the newest transition. Integration: two
  `set_lockout` calls applied out of order leave the newer row.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** assign a per-key monotonic `seq` under `self._lock` at transition time and put it
  in the document. `set_lockout` updates only when
  `COALESCE((evidence->'lockout'->>'seq')::bigint, -1) < $seq` (PostgreSQL 18 JSON operators).
- [ ] **Step 4:** `uv run pytest tests/routing tests/db -q -p no:randomly -m "not integration" -k "lockout or quota"`
  → all pass; the integration case on `pitwall_lane_i` (Redis DB 6) → pass.
- [ ] **Step 5:** Commit `fix(routing): lockout persistence keeps the newest state`.

### Task 72: Ranged volume reads reject a server that ignored `Range` (r4c-2)

**Files:** Modify `src/pitwall/runpod_client/mounts.py` (`get_object_range`, lines 473-506). Test: the
existing mounts test module. Lane I.

**Grounding:**
- RFC 9110 §14.2: a server MAY ignore `Range` and answer 200 with the whole representation. A
  partial answer is `206 Partial Content` carrying `Content-Range` (§15.3.7).
- `get_object_range` accepts any body, so a short full-object 200 is returned as if it started at the
  requested offset (`repro.py`: `requested bytes=5-24 range result b'abcdefghij'`). Its consumer,
  `runpod_files.py:1060`, then returns the wrong bytes to the caller.

- [ ] **Step 1: Failing tests:** offset 5 with a fake S3 answering 200 and the whole short body → typed
  error. 206 with `Content-Range: bytes 5-24/100` → accepted. 206 whose range starts elsewhere →
  error. Offset 0 with a 200 whose body fits the request → accepted.
- [ ] **Step 2:** Run them; expect FAIL.
- [ ] **Step 3: Fix:** for nonzero offsets, require HTTP 206 and a `ContentRange` starting at `offset`.
  Otherwise raise the module's typed range error.
- [ ] **Step 4:** `uv run pytest tests/runpod_client -q -p no:randomly -m "not integration" -k "mount or range or volume"` → all pass.
- [ ] **Step 5:** Commit `fix(runpod): ranged volume reads reject an ignored Range`.

### Task 73: Autopilot snapshots are deeply immutable (r4c-4)

**Files:** Modify `src/pitwall/autopilot/schema.py` (snapshot helper, line 378; used at lines 129 and
222). Test `tests/autopilot/test_schema.py` (or the existing schema test module). Lane C.

**Grounding:** the frozen signal and action dataclasses keep the caller's nested dicts, so mutating the
caller's `params` after construction changes the "frozen" snapshot (`repro.py`:
`frozen params {'priority': 999}`). Python docs, `types.MappingProxyType`: a read-only proxy of a
mapping that reflects later changes to the underlying mapping, so a deep copy must come first.

- [ ] **Step 1: Failing test:** build a signal and an action, then mutate the caller's nested `params`
  and policy inputs → the snapshots are unchanged, and assigning into them raises `TypeError`.
- [ ] **Step 2:** Run it; expect FAIL.
- [ ] **Step 3: Fix:** deep-copy, then recursively wrap mappings in `MappingProxyType` and turn
  lists into tuples. Serialisation helpers convert back to plain dicts.
- [ ] **Step 4:** `uv run pytest tests/autopilot -q -p no:randomly` → all pass.
- [ ] **Step 5:** Commit `fix(autopilot): snapshots are deeply immutable`.

### Task 74: Closing gate for the review fixes

Runs after every finding task above (Tasks 33-73) is merged onto `feat/zcode-harness`.

- [ ] **Step 1:** One `CHANGELOG.md` `## [Unreleased]` entry per user-visible fix.
- [ ] **Step 2:** Release bindings: `uv run --frozen python -m tools.release_acceptance.bind_surfaces`
  → no drift (re-accept only reviewed changes), then regenerate and audit `.secrets.baseline` until
  `uv run python tools/security/check_secrets.py` passes.
- [ ] **Step 3:** Full CI parity (`/tmp/ci-parity/run-a.sh`, `run-c.sh`, `run-b.sh`, serialized; semgrep
  with `EIO_BACKEND=posix` on this host) → every step exit 0; user journeys 0 failed.
- [ ] **Step 4:** One scoped Sol re-review of the whole fix range against these tasks; close every
  finding it raises.
- [ ] **Step 5:** Update `docs/superpowers/plans/2026-10-06-mcp-alignment-status.md`.

### Finding coverage (continuation)

| Finding | Task |
|---|---|
| r1-1 cleanup deletes a live standalone run | 33 |
| r1-2 empty cherry-pick recorded as applied | 34 |
| r1-3 open-ask limit races | 35 |
| r2-1 SSE error bypasses redaction | 36 |
| r2-2 registry dashboard needs RunPod key | 37 |
| r2-3 failed refresh drops Resources summary | 37 |
| r2-4 late artifact on terminal task | 38 |
| r2-5 gateway status doc contradicts code | 36 |
| r4-1 teardown uses ambient RunPod key | 39 |
| r4-2 cleanup after create drops explicit key | 39 |
| r4-3 teardown disarms a newer lease | 40 |
| r4-4 template cache crosses accounts/settings | 41 |
| r4-5 shadow mode skips counters | 42 |
| r5-1 sub-budget race | 43 |
| r5-2 audit ignores runtime per-request cap | 44 |
| r5-3 audit estimate vs admission ceiling | 44 |
| r5-4 idempotency mismatch replays | 45 |
| r5-5 pool creation race | 47 |
| r5-6 monthly alert sent twice | 46 |
| r5-7 migration drift one-directional | 48 |
| r5-8 drill evidence filtered after limit | 49 |
| r3-1 streaming Messages bypasses inspection and budget (Critical) | 50 |
| r3-2 camel-case credential keys unredacted in policy evidence | 53 |
| r3-3 relay dies on child exit mid-write | 52 |
| r3-4 relay steals a client reply with the internal id | 52 |
| r3-5 unsupported Anthropic blocks silently dropped | 51 |
| r3-6 stream errors dropped, `message_stop` emitted | 51 |
| r3-7 CRLF SSE framing lost | 51 |
| r1b-1 credentials in profile `baseUrl` | 54 |
| r1b-2 inherited-harness routes never synced | 55 |
| r1b-3 OpenCode Model Studio always stale | 55 |
| r1b-4 Codex adapter ignores `CODEX_HOME` | 56 |
| r1b-5 probe crashes on malformed JSON | 56 |
| r2b-1 reevaluation overwrites its source | 57 |
| r2b-2 child evidence from the wrong child | 58 |
| r2b-3 hosted evaluation stops at the first run | 58 |
| r2b-4 failed lease refresh shows zero | 59 |
| r4b-1 Lambda quantity > 1 leaks VMs | 60 |
| r4b-2 compute replay ignores request digest | 61 |
| r4b-3 embedding mode sends RunPod key to Pitwall | 62 |
| r4b-4 pod-log truncation flag wrong at boundaries | 63 |
| r5b-1 NaN/inf rate-limit windows | 64 |
| r5b-2 channel preview shows unrelated config | 65 |
| r5b-3 CLI handlers print exception text | 66 |
| r5b-4 doctor reports exception text | 67 |
| r5b-5 shared free pool counted per provider | 68 |
| r5b-6 project-scope install writes user-scope channel | 65 |
| r5b-7 doctor ignores TOML settings | 67 |
| r5b-8 `models fit --inventory` prints nothing | 69 |
| r5b-9 malformed audit timeout crashes runner | 70 |
| r5b-10 init smoke command not shell-quoted | 66 |
| r4c-1 older lockout state overwrites newer | 71 |
| r4c-2 ranged read accepts an ignored Range | 72 |
| r4c-3 missing gateway key becomes keyless | Decision (documented keyless design) |
| r4c-4 autopilot snapshots mutable | 73 |
