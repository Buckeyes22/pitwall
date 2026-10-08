# Pitwall Personal-First Implementation Plan

**Status:** complete; merged as `f79a6de` (historical, private repository; merge of `personal-first/integration`). Checkboxes were not maintained during execution; the commit history and tests are the record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pitwall` a single command that, with only a RunPod credential, serves a catalogue model on a self-terminating pod and attaches it to the `pitwall` Claude Code plugin, while the hosted registry features keep working unchanged when a database is configured.

**Architecture:** Phase 1 is a pure rename (script, images, plugin ids, namespaces). Phase 2 adds a `pitwall.personal` package: one `PersonalServeService` engine over a `LeaseBackend` chosen by configuration, with a `LocalBackend` (atomic JSON state file, in-pod deadline timer, plugin route attachment) and a `RegistryBackend` that wraps today's `serve_model`, lease listing, and lease stop unchanged. Phase 3 adds the console screens over a source protocol so the TUI guard stays green.

**Tech Stack:** Python 3.14, pydantic v2, httpx, Textual, pytest with anyio, uv; RunPod REST v1 (for `dockerEntrypoint`) and v2 through the existing client; the `pitwall-agent-routing` CLI for routes.

**Spec:** `docs/superpowers/specs/2026-09-02-pitwall-personal-first-design.md`

## Global Constraints

- Run every command through `uv run --frozen`; never `pip install`.
- Tests are hermetic; `tests/conftest.py` blocks provider DNS. Only the one `live`-marked test may reach RunPod, and only under `PITWALL_RUN_LIVE`.
- Credentials never appear in output, logs, argv previews, or the state file.
- Every new module under `src/pitwall/personal/` imports nothing from `pitwall.db`, `asyncpg`, or `redis`.
- TUI widget methods call no business logic; screens talk to source protocols (`tests/tui/test_no_business_logic_guard.py` enforces it).
- Commit after every task with `git commit -s`. Work on a branch off `main`; do not push.
- Do not run `/code-review` at `high` effort or any multi-agent workflow during this plan.
- Plugin data paths (`~/.claude/subagent-model-routing/`) and `SUBAGENT_MODEL_ROUTING_*` environment variable names are NOT renamed; only package ids, namespaces, and scripts are.

## File Map

| File | Responsibility |
| --- | --- |
| `pyproject.toml`, `Makefile`, `docker-compose*.yml`, `.github/workflows/*.yml`, `README.md`, `docs/**`, `tests/**` | Phase 1 rename targets |
| `packages/agent-routing/**` | Phase 1 plugin rename targets |
| `src/pitwall/cli.py` | rename usage, `serve` alias, bare-`pitwall` console launch, dispatch of new verbs |
| `src/pitwall/personal/state.py` | `PersonalLease`, `StateStore` |
| `src/pitwall/personal/keys.py` | endpoint key, RunPod credential resolution |
| `src/pitwall/personal/deadline.py` | `wrap_start_command` |
| `src/pitwall/personal/routes.py` | `RouteRunner`: attach, probe, remove |
| `src/pitwall/personal/backend.py` | `LeaseBackend`, `select_backend`, `RegistryBackend` |
| `src/pitwall/personal/service.py` | `PersonalServeService` |
| `src/pitwall/personal/setup.py` | setup steps |
| `src/pitwall/cli_personal.py` | `serve`, `status`, `stop`, `setup` verbs |
| `src/pitwall/tui/personal.py` | `PersonalServeSource`, `ServeWizardScreen`, `PodsScreen`, `RoutesScreen` |
| `src/pitwall/tui/app.py` | backend-dependent view set |
| `tests/personal/**`, `tests/cli/test_personal_verbs.py`, `tests/tui/test_personal_screens.py`, `tests/live/test_personal_serve_live.py` | tests |

---

## Phase 1: Rename

### Task 1: Rename the console script and every reference to it

**Files:**
- Modify: `pyproject.toml:49`, `src/pitwall/cli.py`, and every tracked file containing `pitwall-gpu-broker` (87 files)

**Interfaces:**
- Produces: the only console script is `pitwall`; container images are `pitwall/<service>`.

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_cli_dispatch.py`:

```python
def test_console_script_is_named_pitwall() -> None:
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    scripts = data["project"]["scripts"]
    assert scripts["pitwall"] == "pitwall.cli:main"
    assert "pitwall-gpu-broker" not in scripts


def test_repository_has_no_old_script_name() -> None:
    import subprocess
    from pathlib import Path

    root = Path(__file__).parents[2]
    hits = subprocess.run(
        ["git", "grep", "-l", "pitwall-gpu-broker", "--", ".", ":!docs/evidence", ":!CHANGELOG.md"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert hits == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/cli/test_cli_dispatch.py -q -k "named_pitwall or old_script_name"`
Expected: both FAIL.

- [ ] **Step 3: Apply the mechanical rename**

Evidence documents and the changelog are history and keep the old name. Everything else is renamed:

```bash
git grep -lz 'pitwall-gpu-broker' -- . ':!docs/evidence' ':!CHANGELOG.md' \
  | xargs -0 sed -i 's/pitwall-gpu-broker/pitwall/g'
```

Then check the two places where the substitution produces a bad result and fix them by hand:

```bash
git grep -n 'pitwall/pitwall' | head          # a path like pitwall-gpu-broker/pitwall would double up
git grep -n 'ghcr.io/[a-z0-9-]*/pitwall/' .github/workflows/release.yml
```

If the release workflow pushes images to GHCR under `<owner>/pitwall-gpu-broker/<service>`, the new
repository path is `<owner>/pitwall/<service>`; make sure the login and push steps agree.

- [ ] **Step 4: Regenerate anything derived from the name**

```bash
uv run --frozen python tools/ci/export_openapi.py --help   # confirm the flag names, then:
make openapi-check
```

If `openapi-check` fails only because the `info.title` or server description carried the old
name, regenerate the baseline with the export tool and re-run the check.

- [ ] **Step 5: Run the gates**

```bash
uv run --frozen pytest tests/cli tests/db tests/runpod_client/test_cli.py tests/test_install_acceptance.py tests/test_selfhosted_docs.py tests/test_serve_model_quickstart.py -q -p no:randomly
uv run --frozen ruff check . && uv run --frozen ruff format --check .
make docs-check
git ls-files -z | xargs -0 -r -n 200 uv run --frozen python tools/guards/repo_text_policy.py
uv sync --frozen --extra dev && uv run --frozen pitwall --version
```

Expected: all pass; `pitwall --version` prints the version.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -s -m "refactor: rename the console script and images to pitwall"
```

### Task 2: `serve` replaces `serve-model`, bare `pitwall` opens the console

**Files:**
- Modify: `src/pitwall/cli.py:158-232` (dispatch), `:703-712` (`_usage`), `:2259` (`prog=`)
- Modify: docs that show `pitwall serve-model` (grep below)
- Test: `tests/cli/test_cli_dispatch.py`

**Interfaces:**
- Produces: `main(["serve", ...])` dispatches to `cmd_serve_model`; `main([])` launches the console; usage text lists verbs first.

- [ ] **Step 1: Write the failing tests**

```python
def test_serve_dispatches_to_serve_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall import cli

    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "cmd_serve_model", lambda argv: seen.append(argv) or 0)

    assert cli.main(["serve", "--plan-only", "--model", "x", "--gpu-class", "y"]) == 0
    assert seen == [["--plan-only", "--model", "x", "--gpu-class", "y"]]
    assert cli.main(["serve-model"]) == 1  # removed name is an error


def test_bare_invocation_opens_console(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall import cli

    launched: list[bool] = []
    monkeypatch.setattr(cli, "cmd_dashboard", lambda argv: launched.append(True) or 0)

    assert cli.main([]) == 0
    assert launched == [True]


def test_usage_lists_personal_verbs_first(capsys: pytest.CaptureFixture[str]) -> None:
    from pitwall import cli

    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    first_line = out.splitlines()[0]
    assert first_line.startswith("Usage: pitwall {setup|serve|status|stop|")
    assert "serve-model" not in out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/cli/test_cli_dispatch.py -q -k "dispatches_to_serve_model or opens_console or verbs_first"`
Expected: FAIL.

- [ ] **Step 3: Change the dispatch and usage**

In `main`, replace the empty-args branch and the `serve-model` branch:

```python
    if not args:
        return cmd_dashboard([])
    ...
    if group == "serve":
        return cmd_serve_model(rest)
```

Remove the `if group == "serve-model":` branch entirely. In `_usage`, replace the first `print` with:

```python
print(
    "Usage: pitwall {setup|serve|status|stop|models|db|leases|mcp|retention|init|create-capability|seed|config|register-template|register-endpoint|set-provider-health|terminate-pod|warm-volume|cost|burn-rate|guardrails|routing|runpod|runpod-onboard|provider-ops|volume-files|dashboard} <command>",
    file=stream,
)
print("  pitwall                                Open the console", file=stream)
print(
    "  pitwall setup                          First-run setup (credential, endpoint key, plugin)",
    file=stream,
)
print("  pitwall serve                          Serve a catalogue model on a pod", file=stream)
print("  pitwall status                         Show what is running and when it ends", file=stream)
print("  pitwall stop <route>                   Terminate a pod and remove its route", file=stream)
```

Keep the remaining usage lines, changing `serve-model` to `serve` in the one that describes it. In
`_parse_serve_model_args` change `prog="pitwall serve-model"` to `prog="pitwall serve"`. The `setup`,
`status`, and `stop` branches are added in Task 10; until then they fall through to the existing
unknown-group error, which is acceptable for this task's tests.

- [ ] **Step 4: Rename in docs**

```bash
git grep -lz 'serve-model' -- README.md docs ':!docs/evidence' ':!CHANGELOG.md' | xargs -0 sed -i 's/pitwall serve-model/pitwall serve/g'
git grep -n 'serve-model' -- README.md docs ':!docs/evidence' ':!CHANGELOG.md' | grep -v 'serve-model-quickstart.md' | head
```

Anything left is a file name (`docs/operator/serve-model-quickstart.md`) or prose; rename the file
to `docs/operator/serve-quickstart.md` with `git mv` and fix its inbound links (`make docs-check`
lists them).

- [ ] **Step 5: Run the gates and commit**

```bash
uv run --frozen pytest tests/cli tests/test_serve_model_quickstart.py tests/test_selfhosted_docs.py -q -p no:randomly
make docs-check
git add -A && git commit -s -m "feat(cli): pitwall serve replaces serve-model; bare pitwall opens the console"
```

### Task 3: Rename the plugin packages and namespaces

**Files:**
- Rename: `packages/agent-routing/plugins/subagent-model-routing-{claude,codex,copilot}` → `pitwall`, `pitwall-codex`, `pitwall-copilot`
- Modify: `.claude-plugin/marketplace.json`, `packages/agent-routing/pyproject.toml`, `packages/agent-routing/scripts/install.sh`, every `*-shim.sh`, `packages/agent-routing/scripts/model-routing` (renamed), all plugin docs and skills
- Modify: `src/pitwall/config.py:399-402`

**Interfaces:**
- Produces: agent types `pitwall:<shim>`, skills `pitwall:<skill>`, one script `pitwall-agent-routing`, setting default `pitwall_routing_cli = "pitwall-agent-routing"`.

- [ ] **Step 1: Write the failing tests**

Add `packages/agent-routing/tests/test_plugin_identity.py`:

```python
"""The plugin family is named pitwall."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
REPO = ROOT.parents[1]


def test_plugin_ids_are_pitwall() -> None:
    names = {
        json.loads((ROOT / "plugins/pitwall/.claude-plugin/plugin.json").read_text())["name"],
        json.loads((ROOT / "plugins/pitwall-codex/.codex-plugin/plugin.json").read_text())["name"],
        json.loads((ROOT / "plugins/pitwall-copilot/plugin.json").read_text())["name"],
    }
    assert names == {"pitwall", "pitwall-codex", "pitwall-copilot"}
    market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
    assert market["name"] == "pitwall"
    assert [p["name"] for p in market["plugins"]] == ["pitwall"]


def test_no_old_namespace_remains() -> None:
    hits = subprocess.run(
        [
            "git",
            "grep",
            "-l",
            "subagent-model-routing-claude\\|subagent-model-routing-codex\\|subagent-model-routing-copilot",
            "--",
            ".",
            ":!docs/evidence",
            ":!CHANGELOG.md",
            ":!packages/agent-routing/CHANGELOG.md",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert hits == []


def test_only_one_plugin_script() -> None:
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert list(data["project"]["scripts"]) == ["pitwall-agent-routing"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/agent-routing && uv run --frozen pytest tests/test_plugin_identity.py -q`
Expected: FAIL.

- [ ] **Step 3: Move directories and replace ids**

```bash
cd packages/agent-routing/plugins
git mv subagent-model-routing-claude pitwall
git mv subagent-model-routing-codex pitwall-codex
git mv subagent-model-routing-copilot pitwall-copilot
cd ../../..
# ids and namespaces; the three patterns are distinct strings, so order does not matter
git grep -lz 'subagent-model-routing-claude' -- . ':!docs/evidence' ':!CHANGELOG.md' ':!packages/agent-routing/CHANGELOG.md' | xargs -0 sed -i 's/subagent-model-routing-claude/pitwall/g'
git grep -lz 'subagent-model-routing-codex' -- . ':!docs/evidence' ':!CHANGELOG.md' ':!packages/agent-routing/CHANGELOG.md' | xargs -0 sed -i 's/subagent-model-routing-codex/pitwall-codex/g'
git grep -lz 'subagent-model-routing-copilot' -- . ':!docs/evidence' ':!CHANGELOG.md' ':!packages/agent-routing/CHANGELOG.md' | xargs -0 sed -i 's/subagent-model-routing-copilot/pitwall-copilot/g'
```

Then edit by hand:

- `.claude-plugin/marketplace.json`: `"name": "subagent-model-routing"` → `"name": "pitwall"`; the plugin `source` path already changed with the sed.
- Do NOT touch `SUBAGENT_MODEL_ROUTING_*` env var names, the `~/.claude/subagent-model-routing/` ledger path, or the skill directory `skills/subagent-model-routing` (its namespaced name becomes `pitwall:subagent-model-routing`).

- [ ] **Step 4: One plugin script**

In `packages/agent-routing/pyproject.toml` delete the line `model-routing = "model_routing.cli:main"`.
Rename the installed runtime script and every reference:

```bash
git mv packages/agent-routing/scripts/model-routing packages/agent-routing/scripts/pitwall-agent-routing
git grep -lz '\bmodel-routing\b' -- packages/agent-routing ':!packages/agent-routing/CHANGELOG.md' | xargs -0 sed -i 's/\bmodel-routing\b/pitwall-agent-routing/g'
git grep -n 'pitwall-agent-routing' packages/agent-routing/scripts/install.sh | head
```

`install.sh` line 215 previously advertised two aliases; make it advertise one. The shims'
`ENTRYPOINT="$HERE/model-routing"` became `ENTRYPOINT="$HERE/pitwall-agent-routing"` through the
sed. In `src/pitwall/config.py` change the `pitwall_routing_cli` default to
`"pitwall-agent-routing"` and update its test in `tests/` (grep `model-routing` under `tests/`).

- [ ] **Step 5: Bump the plugin versions**

In the three `plugin.json` files and in `packages/agent-routing/pyproject.toml`, change `0.10.0`
to `0.11.0`. Add a `packages/agent-routing/CHANGELOG.md` entry under a new `## 0.11.0` heading:
"Renamed the plugin family to `pitwall`; agent types are now `pitwall:<shim>`; the `model-routing`
script is removed in favour of `pitwall-agent-routing`. Ledger paths and environment variable names
are unchanged."

- [ ] **Step 6: Run the plugin gates**

```bash
cd packages/agent-routing
uv run --frozen pytest -q
uv run --frozen ruff check . && uv run --frozen ruff format --check .
bash scripts/install.sh --help 2>&1 | head -3
cd ../..
uv run --frozen pytest tests -q -p no:randomly -m "not integration and not slow and not live" -k "routing_cli or register_route or agent_routing"
```

Expected: pass. The Agent Routing CI workflow has its own schema, plugin, and registry checks; run
the same commands it lists (grep `.github/workflows/agent-routing*.yml` for `run:` lines) locally.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -s -m "refactor(plugin): rename the plugin family to pitwall and keep one script"
```

### Task 4: Full-tree verification of the rename

- [ ] **Step 1: Run the whole hermetic suite under two seeds**

```bash
for seed in 1 987654; do uv run --frozen pytest -q -m "not integration and not slow and not live" -p randomly --randomly-seed=$seed | tail -1; done
uv run --frozen mypy src
make openapi-check && make docs-check
```

Expected: `0 failed` both seeds; mypy clean; both checks pass.

- [ ] **Step 2: Commit any regenerated baseline**

```bash
git add -A && git commit -s -m "chore: regenerate baselines after the pitwall rename" || echo "nothing to commit"
```

---

## Phase 2: Personal backend and verbs

### Task 5: State store

**Files:**
- Create: `src/pitwall/personal/__init__.py` (empty docstring module)
- Create: `src/pitwall/personal/state.py`
- Test: `tests/personal/__init__.py`, `tests/personal/test_state.py`

**Interfaces:**
- Produces:

```python
LeaseState = Literal["launching", "ready", "stopped", "failed", "gone"]

class PersonalLease(BaseModel):  # pydantic v2, frozen
    route: str; pod_id: str; model: str; served_model_id: str; engine: str
    variant: str | None; image: str; gpu_class: str; gpu_count: int; cloud: str
    price_per_hour_usd: str | None; endpoint_url: str; key_env: str
    launched_at: datetime; deadline_at: datetime; state: LeaseState; failure: str | None = None

class StateStore:
    def __init__(self, root: Path | None = None) -> None   # default from XDG_STATE_HOME
    @property root(self) -> Path
    def load(self) -> list[PersonalLease]
    def get(self, route: str) -> PersonalLease | None
    def upsert(self, lease: PersonalLease) -> None          # atomic write, owner-only
    def update(self, route: str, **changes: object) -> PersonalLease
```

- [ ] **Step 1: Write the failing tests**

```python
"""Atomic, owner-only personal lease state."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from pitwall.personal.state import PersonalLease, StateStore


def _lease(route: str = "ornith", state: str = "launching") -> PersonalLease:
    now = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.UTC)
    return PersonalLease(
        route=route,
        pod_id="pod123",
        model="ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        served_model_id="Ornith-1.5-35B-A3B",
        engine="llama.cpp",
        variant="gguf:Q4_K_M",
        image="ghcr.io/ggml-org/llama.cpp:server-cuda",
        gpu_class="NVIDIA GeForce RTX 3090",
        gpu_count=1,
        cloud="community",
        price_per_hour_usd="0.220000",
        endpoint_url="https://pod123-8000.proxy.runpod.net/v1",
        key_env="PITWALL_ENDPOINT_KEY",
        launched_at=now,
        deadline_at=now + dt.timedelta(minutes=45),
        state=state,  # type: ignore[arg-type]
    )


def test_default_root_honours_xdg_state_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert StateStore().root == tmp_path / "state" / "pitwall"


def test_upsert_is_atomic_and_owner_only(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "pitwall")
    store.upsert(_lease())

    path = store.root / "leases.json"
    assert oct(store.root.stat().st_mode & 0o777) == "0o700"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert [p.name for p in store.root.iterdir()] == ["leases.json"]  # no temp file left behind
    assert json.loads(path.read_text())[0]["route"] == "ornith"


def test_update_changes_only_named_fields(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.upsert(_lease())

    updated = store.update("ornith", state="ready")

    assert updated.state == "ready"
    assert updated.pod_id == "pod123"
    assert store.get("ornith") == updated
    with pytest.raises(KeyError):
        store.update("missing", state="gone")


def test_load_tolerates_missing_file(tmp_path: Path) -> None:
    assert StateStore(tmp_path).load() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/personal/test_state.py -q`
Expected: FAIL with `ModuleNotFoundError: pitwall.personal`.

- [ ] **Step 3: Implement**

`src/pitwall/personal/__init__.py`:

```python
"""Personal-first serving: one engine, a local or registry backend."""
```

`src/pitwall/personal/state.py`:

```python
"""Atomic, owner-only state for personal leases."""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LeaseState = Literal["launching", "ready", "stopped", "failed", "gone"]

_LEASES_FILE = "leases.json"


class PersonalLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    pod_id: str = Field(min_length=1)
    model: str
    served_model_id: str
    engine: str
    variant: str | None
    image: str
    gpu_class: str
    gpu_count: int = Field(ge=1)
    cloud: str
    price_per_hour_usd: str | None
    endpoint_url: str
    key_env: str
    launched_at: dt.datetime
    deadline_at: dt.datetime
    state: LeaseState
    failure: str | None = None


def default_state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "pitwall"


class StateStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else default_state_root()

    @property
    def root(self) -> Path:
        return self._root

    def load(self) -> list[PersonalLease]:
        path = self._root / _LEASES_FILE
        if not path.exists():
            return []
        raw: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        return [PersonalLease.model_validate(item) for item in raw]

    def get(self, route: str) -> PersonalLease | None:
        return next((lease for lease in self.load() if lease.route == route), None)

    def upsert(self, lease: PersonalLease) -> None:
        leases = [item for item in self.load() if item.route != lease.route]
        leases.append(lease)
        self._write(leases)

    def update(self, route: str, **changes: object) -> PersonalLease:
        current = self.get(route)
        if current is None:
            raise KeyError(route)
        updated = current.model_copy(update=changes)
        self.upsert(updated)
        return updated

    def _write(self, leases: list[PersonalLease]) -> None:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, 0o700)
        payload = json.dumps([lease.model_dump(mode="json") for lease in leases], indent=2)
        fd, temp_name = tempfile.mkstemp(dir=self._root, prefix=".leases-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self._root / _LEASES_FILE)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
```

- [ ] **Step 4: Run, lint, commit**

```bash
uv run --frozen pytest tests/personal/test_state.py -q && uv run --frozen ruff check src/pitwall/personal tests/personal && uv run --frozen mypy src/pitwall/personal
git add src/pitwall/personal tests/personal && git commit -s -m "feat(personal): atomic owner-only lease state store"
```

### Task 6: Keys and credential resolution

**Files:**
- Create: `src/pitwall/personal/keys.py`
- Test: `tests/personal/test_keys.py`

**Interfaces:**
- Produces:

```python
ENDPOINT_KEY_ENV = "PITWALL_ENDPOINT_KEY"
def ensure_endpoint_key(root: Path) -> Path            # creates <root>/endpoint.key (0600) if absent
def read_endpoint_key(root: Path) -> str | None
def resolve_runpod_api_key(environ: Mapping[str, str], runpodctl_config: Path | None = None) -> tuple[str | None, Literal["env", "runpodctl", "none"]]
def runpodctl_config_path(environ: Mapping[str, str]) -> Path   # ~/.runpod/config.toml
def config_file_is_shared(path: Path) -> bool           # group or world readable
```

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import os
from pathlib import Path

from pitwall.personal.keys import (
    ENDPOINT_KEY_ENV,
    config_file_is_shared,
    ensure_endpoint_key,
    read_endpoint_key,
    resolve_runpod_api_key,
)


def test_endpoint_key_is_created_once_and_owner_only(tmp_path: Path) -> None:
    first = ensure_endpoint_key(tmp_path)
    second = ensure_endpoint_key(tmp_path)

    assert first == second == tmp_path / "endpoint.key"
    assert oct(first.stat().st_mode & 0o777) == "0o600"
    value = read_endpoint_key(tmp_path)
    assert value is not None and len(value) >= 32
    assert ENDPOINT_KEY_ENV == "PITWALL_ENDPOINT_KEY"


def test_env_key_wins_over_runpodctl(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('apikey = "from-file"\n')

    assert resolve_runpod_api_key({"RUNPOD_API_KEY": "from-env"}, config) == ("from-env", "env")
    assert resolve_runpod_api_key({}, config) == ("from-file", "runpodctl")
    assert resolve_runpod_api_key({}, tmp_path / "missing.toml") == (None, "none")


def test_shared_config_detection(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('apikey = "x"\n')
    os.chmod(config, 0o644)
    assert config_file_is_shared(config) is True
    os.chmod(config, 0o600)
    assert config_file_is_shared(config) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/personal/test_keys.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Endpoint key lifecycle and RunPod credential resolution. Never writes the RunPod key."""

from __future__ import annotations

import os
import secrets
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

ENDPOINT_KEY_ENV = "PITWALL_ENDPOINT_KEY"
_KEY_FILE = "endpoint.key"

CredentialSource = Literal["env", "runpodctl", "none"]


def ensure_endpoint_key(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / _KEY_FILE
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(32))
    os.chmod(path, 0o600)
    return path


def read_endpoint_key(root: Path) -> str | None:
    path = root / _KEY_FILE
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def runpodctl_config_path(environ: Mapping[str, str]) -> Path:
    home = environ.get("HOME") or str(Path.home())
    return Path(home) / ".runpod" / "config.toml"


def resolve_runpod_api_key(
    environ: Mapping[str, str],
    runpodctl_config: Path | None = None,
) -> tuple[str | None, CredentialSource]:
    from_env = environ.get("RUNPOD_API_KEY", "").strip()
    if from_env:
        return from_env, "env"
    path = runpodctl_config if runpodctl_config is not None else runpodctl_config_path(environ)
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError, tomllib.TOMLDecodeError:
            return None, "none"
        value = data.get("apikey") or data.get("api_key") or data.get("apiKey")
        if isinstance(value, str) and value.strip():
            return value.strip(), "runpodctl"
    return None, "none"


def config_file_is_shared(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o077)
```

Check the real key name in `~/.runpod/config.toml` on this machine with
`grep -oE '^[a-zA-Z_]+ *=' ~/.runpod/config.toml` and put that name first in the lookup.

- [ ] **Step 4: Run, lint, commit**

```bash
uv run --frozen pytest tests/personal/test_keys.py -q && uv run --frozen ruff check src/pitwall/personal tests/personal && uv run --frozen mypy src/pitwall/personal
git add src/pitwall/personal/keys.py tests/personal/test_keys.py && git commit -s -m "feat(personal): endpoint key and RunPod credential resolution"
```

### Task 7: The in-pod deadline wrapper

**Files:**
- Create: `src/pitwall/personal/deadline.py`
- Test: `tests/personal/test_deadline.py`

**Interfaces:**
- Produces:

```python
def server_binary(engine: str, image: str) -> list[str]    # ["/app/llama-server"] or ["vllm", "serve"]
def wrap_start_command(server: Sequence[str], argv: Sequence[str], *, ttl_seconds: int, terminate: bool = True) -> list[str]
    # returns the single-element docker_start_cmd for docker_entrypoint=["sh", "-c"]
def redact_for_display(command: Sequence[str]) -> list[str]  # replaces "$PITWALL_ENDPOINT_KEY" expansions and literal keys
```

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import shlex

from pitwall.personal.deadline import redact_for_display, server_binary, wrap_start_command


def test_wrapper_terminates_after_ttl_then_execs_server_with_key() -> None:
    cmd = wrap_start_command(
        ["/app/llama-server"],
        ["--hf-repo", "org/model", "--alias", "m", "--port", "8000"],
        ttl_seconds=2700,
    )

    assert len(cmd) == 1
    script = cmd[0]
    assert "sleep 2700;" in script
    assert (
        'curl -fsS -X DELETE -H "Authorization: Bearer $RUNPOD_API_KEY" "https://api.runpod.io/v2/pods/$RUNPOD_POD_ID"'
        in script
    )
    assert script.rstrip().endswith(
        'exec /app/llama-server --hf-repo org/model --alias m --port 8000 --api-key "$PITWALL_ENDPOINT_KEY"'
    )
    assert script.index("sleep") < script.index("exec ")


def test_stop_fallback_uses_stop_endpoint() -> None:
    script = wrap_start_command(["vllm", "serve"], ["org/model"], ttl_seconds=60, terminate=False)[
        0
    ]
    assert "https://api.runpod.io/v2/pods/$RUNPOD_POD_ID/stop" in script
    assert "-X POST" in script


def test_argv_is_shell_quoted() -> None:
    script = wrap_start_command(
        ["/app/llama-server"], ["--ctx-size", "32768", "--alias", "a b"], ttl_seconds=60
    )[0]
    assert shlex.quote("a b") in script


def test_server_binary_by_engine() -> None:
    assert server_binary("llama.cpp", "ghcr.io/ggml-org/llama.cpp:server-cuda") == [
        "/app/llama-server"
    ]
    assert server_binary("vllm", "vllm/vllm-openai:latest") == ["vllm", "serve"]
    assert server_binary("sglang", "lmsysorg/sglang:latest") == [
        "python3",
        "-m",
        "sglang.launch_server",
    ]


def test_redaction_hides_key_values() -> None:
    shown = redact_for_display(
        ["--api-key", "sk-live-secret", "--api-key", '"$PITWALL_ENDPOINT_KEY"']
    )
    assert shown == ["--api-key", "<redacted>", "--api-key", "<redacted>"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/personal/test_deadline.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
"""Build the pod start command: a self-termination timer plus the model server."""

from __future__ import annotations

import shlex
from collections.abc import Sequence

_TERMINATE = (
    'curl -fsS -X DELETE -H "Authorization: Bearer $RUNPOD_API_KEY" '
    '"https://api.runpod.io/v2/pods/$RUNPOD_POD_ID"'
)
_STOP = (
    'curl -fsS -X POST -H "Authorization: Bearer $RUNPOD_API_KEY" '
    '"https://api.runpod.io/v2/pods/$RUNPOD_POD_ID/stop"'
)


def server_binary(engine: str, image: str) -> list[str]:
    if engine == "llama.cpp":
        return ["/app/llama-server"]
    if engine == "vllm":
        return ["vllm", "serve"]
    if engine == "sglang":
        return ["python3", "-m", "sglang.launch_server"]
    raise ValueError(f"unsupported engine: {engine!r}")


def wrap_start_command(
    server: Sequence[str],
    argv: Sequence[str],
    *,
    ttl_seconds: int,
    terminate: bool = True,
) -> list[str]:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    action = _TERMINATE if terminate else _STOP
    exec_line = " ".join(shlex.quote(part) for part in [*server, *argv])
    script = (
        f"( sleep {ttl_seconds}; {action} ) >/dev/null 2>&1 &\n"
        f'exec {exec_line} --api-key "$PITWALL_ENDPOINT_KEY"'
    )
    return [script]


def redact_for_display(command: Sequence[str]) -> list[str]:
    shown: list[str] = []
    hide_next = False
    for part in command:
        if hide_next:
            shown.append("<redacted>")
            hide_next = False
            continue
        shown.append(part)
        if part == "--api-key":
            hide_next = True
    return shown
```

Check the RunPod v2 stop path in `docs.runpod.io/api-reference-v2/pods/trigger-a-pod-state-transition.md`;
if the stop transition is `POST /v2/pods/{id}/stop`, the constant above is right. If it is a different
path, use that one.

- [ ] **Step 4: Run, lint, commit**

```bash
uv run --frozen pytest tests/personal/test_deadline.py -q && uv run --frozen ruff check src/pitwall/personal tests/personal && uv run --frozen mypy src/pitwall/personal
git add src/pitwall/personal/deadline.py tests/personal/test_deadline.py && git commit -s -m "feat(personal): in-pod deadline wrapper for the model server"
```

### Task 8: Route runner over the plugin CLI

**Files:**
- Create: `src/pitwall/personal/routes.py`
- Test: `tests/personal/test_routes.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class RouteOutcome: ok: bool; action: Literal["added","probed","removed","failed"]; detail: str  # detail never contains stderr with secrets

class RouteRunner:
    def __init__(self, cli: str, *, env: Mapping[str, str], run=subprocess.run) -> None
    def attach(self, route: str, *, base_url: str, model_id: str, key_env: str) -> RouteOutcome
    def probe(self, route: str) -> RouteOutcome
    def remove(self, route: str) -> RouteOutcome
    def exists(self, route: str) -> bool
```

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import subprocess
from typing import Any

from pitwall.personal.routes import RouteRunner


class _FakeRun:
    def __init__(self, results: dict[str, tuple[int, str, str]]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        code, out, err = self.results.get(command[2], (0, "", ""))
        return subprocess.CompletedProcess(command, code, stdout=out, stderr=err)


def test_attach_uses_base_url_model_and_key_env() -> None:
    run = _FakeRun({})
    runner = RouteRunner("pitwall-agent-routing", env={"PATH": "/usr/bin"}, run=run)

    outcome = runner.attach(
        "ornith",
        base_url="https://p-8000.proxy.runpod.net/v1",
        model_id="Ornith-1.5-35B-A3B",
        key_env="PITWALL_ENDPOINT_KEY",
    )

    assert outcome.ok and outcome.action == "added"
    assert run.calls == [
        [
            "pitwall-agent-routing",
            "routes",
            "add",
            "ornith",
            "--base-url",
            "https://p-8000.proxy.runpod.net/v1",
            "--model",
            "Ornith-1.5-35B-A3B",
            "--api-key-env",
            "PITWALL_ENDPOINT_KEY",
            "--seat",
            "local",
        ]
    ]


def test_failure_detail_is_bounded_and_not_reflected() -> None:
    run = _FakeRun({"add": (1, "", "boom Bearer sk-secret-canary " + "x" * 500)})
    runner = RouteRunner("pitwall-agent-routing", env={}, run=run)

    outcome = runner.attach("r", base_url="u", model_id="m", key_env="K")

    assert not outcome.ok and outcome.action == "failed"
    assert "sk-secret-canary" not in outcome.detail
    assert len(outcome.detail) <= 160


def test_exists_uses_show_exit_code() -> None:
    run = _FakeRun({"show": (0, "{}", "")})
    assert RouteRunner("pitwall-agent-routing", env={}, run=run).exists("r") is True
    run = _FakeRun({"show": (2, "", "not found")})
    assert RouteRunner("pitwall-agent-routing", env={}, run=run).exists("r") is False


def test_missing_cli_is_a_failed_outcome() -> None:
    def raising(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    outcome = RouteRunner("nope", env={}, run=raising).probe("r")
    assert outcome == outcome.__class__(
        ok=False, action="failed", detail="routing cli not found: nope"
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/personal/test_routes.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
"""Attach, probe, and remove plugin routes through the pitwall-agent-routing CLI."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pitwall.security.redaction import redact_text

_TIMEOUT_S = 30
_DETAIL_MAX = 160

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True, slots=True)
class RouteOutcome:
    ok: bool
    action: Literal["added", "probed", "removed", "failed"]
    detail: str


class RouteRunner:
    def __init__(self, cli: str, *, env: Mapping[str, str], run: Runner = subprocess.run) -> None:
        self._cli = cli
        self._env = dict(env)
        self._run = run

    def attach(self, route: str, *, base_url: str, model_id: str, key_env: str) -> RouteOutcome:
        return self._invoke(
            "added",
            [
                "routes",
                "add",
                route,
                "--base-url",
                base_url,
                "--model",
                model_id,
                "--api-key-env",
                key_env,
                "--seat",
                "local",
            ],
        )

    def probe(self, route: str) -> RouteOutcome:
        return self._invoke("probed", ["routes", "probe", route])

    def remove(self, route: str) -> RouteOutcome:
        return self._invoke("removed", ["routes", "remove", route])

    def exists(self, route: str) -> bool:
        try:
            result = self._run(
                [self._cli, "routes", "show", route],
                env=self._env,
                capture_output=True,
                text=True,
                check=False,
                timeout=_TIMEOUT_S,
            )
        except OSError, subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def _invoke(
        self, action: Literal["added", "probed", "removed"], args: list[str]
    ) -> RouteOutcome:
        try:
            result = self._run(
                [self._cli, *args],
                env=self._env,
                capture_output=True,
                text=True,
                check=False,
                timeout=_TIMEOUT_S,
            )
        except FileNotFoundError:
            return RouteOutcome(
                ok=False, action="failed", detail=f"routing cli not found: {self._cli}"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return RouteOutcome(ok=False, action="failed", detail=type(exc).__name__)
        if result.returncode == 0:
            return RouteOutcome(ok=True, action=action, detail="")
        detail = redact_text(" ".join(result.stderr.split()))[:_DETAIL_MAX]
        return RouteOutcome(ok=False, action="failed", detail=detail)
```

`redact_text` already exists in `pitwall.security.redaction` and masks bearer tokens and key-shaped
strings; the test's canary must be masked by it. If the canary survives, tighten the test's canary
to a shape `redact_text` covers (for example `sk-` followed by 32 hex characters).

- [ ] **Step 4: Run, lint, commit**

```bash
uv run --frozen pytest tests/personal/test_routes.py -q && uv run --frozen ruff check src/pitwall/personal tests/personal && uv run --frozen mypy src/pitwall/personal
git add src/pitwall/personal/routes.py tests/personal/test_routes.py && git commit -s -m "feat(personal): route runner over pitwall-agent-routing"
```

### Task 9: The engine and the local backend

**Files:**
- Create: `src/pitwall/personal/service.py`, `src/pitwall/personal/backend.py`
- Test: `tests/personal/test_service.py`

**Interfaces:**
- Produces:

```python
class ServeSpec(BaseModel):   # what the caller asked for
    model: str; variant: str | None = None; gpu_class: str; gpu_count: int = 1
    cloud: Literal["secure", "community"] = "community"; ttl_minutes: int = 60
    max_usd_per_hour: Decimal; rate_per_second: Decimal | None = None; route: str

class ServePreview(BaseModel):  # returned by plan(); shown before confirmation
    plan: ServePlanResult; price_per_hour_usd: Decimal | None; max_spend_usd: Decimal | None
    deadline_at: datetime; request_preview: dict[str, object]   # argv redacted

class PersonalServeService:
    def __init__(self, *, store, settings, catalogue, runpod: RunPodPorts, routes: RouteRunner, clock=..., prices=load_gpu_price_snapshot, fit=fit_options) -> None
    async def plan(self, spec: ServeSpec) -> ServePreview          # raises ServeRefused(code) before any write
    async def serve(self, spec: ServeSpec, *, progress: Callable[[str], None] | None = None) -> PersonalLease
    async def status(self) -> list[PersonalLease]                  # reconciles against get_pod
    async def stop(self, route: str) -> PersonalLease
    async def logs(self, route: str, *, max_lines: int = 40) -> str

class RunPodPorts(Protocol):   # the client functions the service needs, injectable for tests
    async def create_pod(...)->dict; async def get_pod(pod_id)->dict|None; async def terminate_pod(pod_id)->None; async def read_logs(pod_id, max_lines)->str
```

- [ ] **Step 1: Write the failing tests**

Create `tests/personal/test_service.py` with a fake RunPod port, a fake catalogue that returns the
Ornith plan from `docs/operator/serve-quickstart.md`, a fake price snapshot, and a `_FakeRoutes`
that records calls. Cover:

```python
async def test_plan_refuses_over_cap_before_any_write(service, runpod, routes) -> None:
    spec = _spec(max_usd_per_hour=Decimal("0.10"))  # live price is 0.22
    with pytest.raises(ServeRefused) as exc:
        await service.plan(spec)
    assert exc.value.code == "price_over_cap"
    assert runpod.created == [] and routes.calls == []


async def test_plan_refuses_unpriced_without_rate(service_unpriced) -> None:
    with pytest.raises(ServeRefused, match="unpriced"):
        await service_unpriced.plan(_spec())


async def test_plan_refuses_existing_route(service, routes) -> None:
    routes.existing.add("ornith")
    with pytest.raises(ServeRefused, match="route_exists"):
        await service.plan(_spec())


async def test_serve_writes_launching_record_then_ready(service, runpod, routes, store) -> None:
    lease = await service.serve(_spec())

    assert runpod.created[0]["docker_entrypoint"] == ["sh", "-c"]
    assert "exec /app/llama-server" in runpod.created[0]["docker_start_cmd"][0]
    assert runpod.created[0]["workload"].ports == "8000/http"
    assert runpod.created[0]["env"] == {}  # no HF token for an ungated model
    assert runpod.created[0]["wait_for_readiness"] is False
    assert lease.state == "ready"
    assert lease.endpoint_url == "https://pod123-8000.proxy.runpod.net/v1"
    assert routes.calls[0][:3] == ("attach", "ornith", "https://pod123-8000.proxy.runpod.net/v1")
    assert routes.calls[1] == ("probe", "ornith")
    assert store.get("ornith") == lease


async def test_readiness_timeout_terminates_and_records_failure(
    service_slow_models, runpod, store
) -> None:
    with pytest.raises(ServeFailed, match="readiness_timeout"):
        await service_slow_models.serve(_spec())
    assert runpod.terminated == ["pod123"]
    assert store.get("ornith").state == "failed"


async def test_route_attach_failure_terminates(service, runpod, routes, store) -> None:
    routes.fail_attach = True
    with pytest.raises(ServeFailed, match="route_attach_failed"):
        await service.serve(_spec())
    assert runpod.terminated == ["pod123"]


async def test_status_marks_missing_pods_gone_and_terminates_late(
    service, runpod, store, clock
) -> None:
    await service.serve(_spec(ttl_minutes=1))
    runpod.pods.clear()  # pod vanished
    assert (await service.status())[0].state == "gone"

    await service.serve(_spec(route="late", ttl_minutes=1))
    clock.advance(minutes=5)  # past deadline, pod still present
    statuses = await service.status()
    assert runpod.terminated[-1] == "pod-late"
    assert next(s for s in statuses if s.route == "late").state == "stopped"


async def test_stop_terminates_removes_route_and_tolerates_gone(service, runpod, routes) -> None:
    await service.serve(_spec())
    lease = await service.stop("ornith")
    assert lease.state == "stopped" and runpod.terminated == ["pod123"]
    assert ("remove", "ornith") in routes.calls
    runpod.terminate_raises_not_found = True
    assert (await service.stop("ornith")).state == "stopped"
```

Write the fakes at the top of the file: `_FakeRunPod` records `created` kwargs, returns
`{"id": "pod123"}` (or `pod-<route>` when the route is passed in the pod name), keeps `pods`,
`terminated`; `_FakeRoutes` has `existing: set[str]`, `calls: list[tuple]`, `fail_attach: bool`;
`_FakeClock` has `now()` and `advance()`; `_fake_verify` resolves immediately or, for
`service_slow_models`, raises `ServeVerificationFailed`. Use pytest fixtures for each variant.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/personal/test_service.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `service.py`**

```python
"""One serve engine: plan, launch, wait, attach, stop, status."""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from pitwall.config import PitwallSettings
from pitwall.models.fit import fit_options
from pitwall.models.prices import load_gpu_price_snapshot
from pitwall.personal.deadline import redact_for_display, server_binary, wrap_start_command
from pitwall.personal.keys import ENDPOINT_KEY_ENV
from pitwall.personal.routes import RouteRunner
from pitwall.personal.state import PersonalLease, StateStore
from pitwall.runpod_client.workloads import WorkloadConfig
from pitwall.serve import (
    PlanCatalogue,
    ServePlanRequest,
    ServePlanResult,
    ServeVerificationFailed,
    plan_catalogue_model,
    verify_served_model,
)

Cloud = Literal["secure", "community"]
_PORT = 8000
_CONTAINER_DISK_GB = 50


class ServeRefused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ServeFailed(Exception):
    def __init__(self, code: str, pod_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.pod_id = pod_id


class ServeSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    variant: str | None = None
    gpu_class: str
    gpu_count: int = Field(default=1, ge=1)
    cloud: Cloud = "community"
    ttl_minutes: int = Field(default=60, ge=5, le=10_080)
    max_usd_per_hour: Decimal = Field(gt=0)
    rate_per_second: Decimal | None = None
    route: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")


class ServePreview(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    plan: ServePlanResult
    price_per_hour_usd: Decimal | None
    max_spend_usd: Decimal | None
    deadline_at: dt.datetime
    request_preview: dict[str, Any]


class RunPodPorts(Protocol):
    async def create_pod(self, **kwargs: Any) -> dict[str, Any]: ...
    async def get_pod(self, pod_id: str) -> dict[str, Any] | None: ...
    async def terminate_pod(self, pod_id: str) -> None: ...
    async def read_logs(self, pod_id: str, *, max_lines: int) -> str: ...


class LiveRunPod:
    """The real client functions behind the protocol."""

    def __init__(self, settings: PitwallSettings, api_key: str) -> None:
        self._settings = settings
        self._api_key = api_key

    async def create_pod(self, **kwargs: Any) -> dict[str, Any]:
        from pitwall.runpod_client.pods import create_pod_with_fallback

        return await create_pod_with_fallback(**kwargs)

    async def get_pod(self, pod_id: str) -> dict[str, Any] | None:
        from pitwall.runpod_client.pods import get_pod

        return await get_pod(pod_id)

    async def terminate_pod(self, pod_id: str) -> None:
        from pitwall.runpod_client.pods import terminate_pod

        await terminate_pod(pod_id)

    async def read_logs(self, pod_id: str, *, max_lines: int) -> str:
        from pitwall.runpod_client.pod_logs import BoundedPodLogClient

        client = BoundedPodLogClient(api_key=self._api_key)
        try:
            payload = await client.read(pod_id, max_lines=max_lines, max_bytes=64 * 1024)
        finally:
            await client.aclose()
        return payload.text


Verifier = Callable[[str, str], Awaitable[list[str]]]


class PersonalServeService:
    def __init__(
        self,
        *,
        store: StateStore,
        settings: PitwallSettings,
        catalogue: PlanCatalogue,
        runpod: RunPodPorts,
        routes: RouteRunner,
        clock: Callable[[], dt.datetime] | None = None,
        prices: Callable[..., Awaitable[Any]] = load_gpu_price_snapshot,
        fit: Callable[..., Sequence[Any]] = fit_options,
        verify: Verifier = verify_served_model,
        hf_token: str | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._catalogue = catalogue
        self._runpod = runpod
        self._routes = routes
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self._prices = prices
        self._fit = fit
        self._verify = verify
        self._hf_token = hf_token

    async def plan(self, spec: ServeSpec) -> ServePreview:
        if self._store.get(spec.route) is not None and self._store.get(spec.route).state in {
            "launching",
            "ready",
        }:
            raise ServeRefused("route_exists", spec.route)
        if self._routes.exists(spec.route):
            raise ServeRefused("route_exists", spec.route)
        plan = await plan_catalogue_model(
            ServePlanRequest(
                model=spec.model,
                gpu_class=spec.gpu_class,
                gpu_count=spec.gpu_count,
                variant=spec.variant,
                ttl_minutes=spec.ttl_minutes,
            ),
            settings=self._settings,
            catalogue=self._catalogue,
        )
        if plan.fit == "does_not_fit":
            raise ServeRefused("does_not_fit", spec.gpu_class)
        price = await self._price_for(spec, plan)
        if price is None:
            raise ServeRefused("unpriced", "pass --rate-per-second")
        if price > spec.max_usd_per_hour:
            raise ServeRefused("price_over_cap", f"{price} > {spec.max_usd_per_hour}")
        now = self._clock()
        preview = self._request(spec, plan)
        preview["docker_start_cmd"] = redact_for_display(preview["docker_start_cmd"])
        return ServePreview(
            plan=plan,
            price_per_hour_usd=price,
            max_spend_usd=(price * Decimal(spec.ttl_minutes) / Decimal(60)).quantize(
                Decimal("0.000001")
            ),
            deadline_at=now + dt.timedelta(minutes=spec.ttl_minutes),
            request_preview=preview,
        )

    async def serve(
        self, spec: ServeSpec, *, progress: Callable[[str], None] | None = None
    ) -> PersonalLease:
        report = progress or (lambda _message: None)
        preview = await self.plan(spec)
        request = self._request(spec, preview.plan)
        report("creating pod")
        created = await self._runpod.create_pod(**request)
        pod_id = str(created["id"])
        endpoint = f"https://{pod_id}-{_PORT}.proxy.runpod.net/v1"
        lease = PersonalLease(
            route=spec.route,
            pod_id=pod_id,
            model=spec.model,
            served_model_id=preview.plan.model_id,
            engine=preview.plan.engine,
            variant=preview.plan.variant,
            image=preview.plan.image,
            gpu_class=spec.gpu_class,
            gpu_count=spec.gpu_count,
            cloud=spec.cloud,
            price_per_hour_usd=str(preview.price_per_hour_usd)
            if preview.price_per_hour_usd is not None
            else None,
            endpoint_url=endpoint,
            key_env=ENDPOINT_KEY_ENV,
            launched_at=self._clock(),
            deadline_at=preview.deadline_at,
            state="launching",
        )
        self._store.upsert(lease)
        report("waiting for the model to answer")
        try:
            await self._verify(f"{endpoint}/models", preview.plan.model_id)
        except ServeVerificationFailed:
            await self._fail(lease, "readiness_timeout")
        report("attaching route")
        attached = self._routes.attach(
            spec.route, base_url=endpoint, model_id=preview.plan.model_id, key_env=ENDPOINT_KEY_ENV
        )
        if not attached.ok:
            await self._fail(lease, "route_attach_failed")
        self._routes.probe(spec.route)
        return self._store.update(spec.route, state="ready")

    async def status(self) -> list[PersonalLease]:
        now = self._clock()
        result: list[PersonalLease] = []
        for lease in self._store.load():
            if lease.state in {"launching", "ready"}:
                pod = await self._runpod.get_pod(lease.pod_id)
                if pod is None:
                    lease = self._store.update(lease.route, state="gone")
                elif now >= lease.deadline_at:
                    await self._terminate_quietly(lease.pod_id)
                    self._routes.remove(lease.route)
                    lease = self._store.update(
                        lease.route, state="stopped", failure="terminated_late"
                    )
            result.append(lease)
        return result

    async def stop(self, route: str) -> PersonalLease:
        lease = self._store.get(route)
        if lease is None:
            raise KeyError(route)
        await self._terminate_quietly(lease.pod_id)
        self._routes.remove(route)
        return self._store.update(route, state="stopped")

    async def logs(self, route: str, *, max_lines: int = 40) -> str:
        lease = self._store.get(route)
        if lease is None:
            raise KeyError(route)
        return await self._runpod.read_logs(lease.pod_id, max_lines=max_lines)

    async def _price_for(self, spec: ServeSpec, plan: ServePlanResult) -> Decimal | None:
        if spec.rate_per_second is not None:
            return (spec.rate_per_second * Decimal(3600)).quantize(Decimal("0.000001"))
        snapshot = await self._prices(cloud=spec.cloud, settings=self._settings)
        variant = self._catalogue.dossier_variant(spec.model, spec.variant)
        for option in self._fit(
            variant, gpu_types=snapshot.gpu_types, ttl_minutes=spec.ttl_minutes, cloud=spec.cloud
        ):
            if option.gpu_class == spec.gpu_class and option.gpu_count == spec.gpu_count:
                return option.price_per_hour
        return None

    def _request(self, spec: ServeSpec, plan: ServePlanResult) -> dict[str, Any]:
        ttl_seconds = spec.ttl_minutes * 60
        start = wrap_start_command(
            server_binary(plan.engine, plan.image), plan.argv, ttl_seconds=ttl_seconds
        )
        env: dict[str, str] = {}
        if self._hf_token and getattr(
            self._catalogue.dossier_variant(spec.model, spec.variant), "gated", False
        ):
            env["HF_TOKEN"] = self._hf_token
        return {
            "name": f"pitwall-{spec.route}",
            "template_id": None,
            "image_name": plan.image,
            "workload": WorkloadConfig(
                name=f"pitwall-{spec.route}",
                capability=spec.route,
                gpu_types=[spec.gpu_class],
                gpu_count=spec.gpu_count,
                container_disk_gb=_CONTAINER_DISK_GB,
                cloud_type=spec.cloud.upper(),
                ports=f"{_PORT}/http",
            ),
            "env": env,
            "cloud_type_override": spec.cloud.upper(),
            "docker_entrypoint": ["sh", "-c"],
            "docker_start_cmd": start,
            "max_cost_per_hr": float(spec.max_usd_per_hour),
            "wait_for_readiness": False,
        }

    async def _fail(self, lease: PersonalLease, code: str) -> None:
        await self._terminate_quietly(lease.pod_id)
        self._store.update(lease.route, state="failed", failure=code)
        raise ServeFailed(code, lease.pod_id)

    async def _terminate_quietly(self, pod_id: str) -> None:
        try:
            await self._runpod.terminate_pod(pod_id)
        except Exception:  # reason: a pod that is already gone is not an error for stop
            return
```

Two things to check while implementing: `PersonalLease` is frozen, so the `env` for the pod must
also carry `PITWALL_ENDPOINT_KEY`; add `env[ENDPOINT_KEY_ENV] = <key value>` in `_request` by
passing the key value into the service constructor as `endpoint_key: str` (read once from
`read_endpoint_key`). Keep it out of `request_preview` by deleting that env entry in `plan()`.
Also confirm `ServePlanResult.fit` values from `pitwall.serve` (`FitVerdict`) and use the exact
literal for the refusal.

- [ ] **Step 4: Implement `backend.py`**

```python
"""Choose where lease state lives from configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal, Protocol

BackendName = Literal["personal", "registry"]


def select_backend(environ: Mapping[str, str] | None = None) -> BackendName:
    env = os.environ if environ is None else environ
    return "registry" if env.get("DATABASE_URL", "").strip() else "personal"


class RegistryBackend:
    """The hosted path: delegates to the existing registry-backed CLI commands unchanged."""

    def serve(self, argv: list[str]) -> int:
        from pitwall.cli import cmd_serve_model

        return cmd_serve_model(argv)

    def status(self) -> int:
        from pitwall.cli import cmd_leases

        return cmd_leases(["list"])

    def stop(self, lease_id: str) -> int:
        from pitwall.cli import cmd_leases

        return cmd_leases(["stop", lease_id])
```

Add `tests/personal/test_backend.py`:

```python
from pitwall.personal.backend import select_backend


def test_backend_follows_database_url() -> None:
    assert select_backend({}) == "personal"
    assert select_backend({"DATABASE_URL": "  "}) == "personal"
    assert select_backend({"DATABASE_URL": "postgresql://x"}) == "registry"
```

- [ ] **Step 5: Run, lint, type-check, commit**

```bash
uv run --frozen pytest tests/personal -q && uv run --frozen ruff check src/pitwall/personal tests/personal && uv run --frozen ruff format --check src/pitwall/personal tests/personal && uv run --frozen mypy src/pitwall/personal
git add src/pitwall/personal tests/personal && git commit -s -m "feat(personal): serve engine with local and registry backends"
```

### Task 10: The verbs: `serve`, `status`, `stop`

**Files:**
- Create: `src/pitwall/cli_personal.py`
- Modify: `src/pitwall/cli.py` dispatch
- Test: `tests/cli/test_personal_verbs.py`

**Interfaces:**
- Produces: `cmd_serve(argv)`, `cmd_status(argv)`, `cmd_stop(argv)` in `pitwall.cli_personal`; `main` routes `serve|status|stop` to them; on the registry backend they delegate to `RegistryBackend`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from pitwall import cli, cli_personal


def test_serve_on_registry_backend_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "cmd_serve_model", lambda argv: seen.append(argv) or 0)

    assert cli.main(["serve", "--capability", "c", "--model", "m", "--gpu-class", "g"]) == 0
    assert seen == [["--capability", "c", "--model", "m", "--gpu-class", "g"]]


def test_serve_personal_requires_cap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    rc = cli.main(["serve", "--model", "m", "--gpu-class", "g", "--route", "r"])
    assert rc == 2
    assert "--max-usd-per-hour" in capsys.readouterr().err


def test_serve_personal_prints_route_and_shim_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_service: Any
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setattr(cli_personal, "_build_service", lambda settings: fake_service)

    rc = cli.main(
        [
            "serve",
            "--model",
            "m",
            "--gpu-class",
            "g",
            "--route",
            "ornith",
            "--max-usd-per-hour",
            "1",
            "--json",
        ]
    )

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["route"] == "ornith" and data["state"] == "ready"
    assert data["try"] == "route-shim.sh ornith prompt.md"


def test_status_and_stop_personal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fake_service: Any
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setattr(cli_personal, "_build_service", lambda settings: fake_service)

    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["leases"][0]["route"] == "ornith"
    assert cli.main(["stop", "ornith", "--json"]) == 0
    assert fake_service.stopped == ["ornith"]
```

Provide `fake_service` as a fixture whose `serve` returns a ready `PersonalLease`, `status` returns
one lease, and `stop` records the route.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/cli/test_personal_verbs.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement `cli_personal.py`**

```python
"""pitwall serve|status|stop: personal-first verbs over the shared engine."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from decimal import Decimal, InvalidOperation

from pitwall.personal.backend import RegistryBackend, select_backend


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _service_or_exit(json_mode: bool):  # noqa: ANN202 - returns PersonalServeService or exits
    from pitwall.config import get_settings
    from pitwall.personal.keys import resolve_runpod_api_key

    key, source = resolve_runpod_api_key(os.environ)
    if key is None:
        _err("no RunPod credential: run `pitwall setup` or export RUNPOD_API_KEY")
        raise SystemExit(2)
    if source == "runpodctl":
        os.environ.setdefault("RUNPOD_API_KEY", key)
    return _build_service(get_settings())


def _build_service(settings):  # noqa: ANN001, ANN202
    from pitwall.models import load_catalogue
    from pitwall.personal.keys import ENDPOINT_KEY_ENV, ensure_endpoint_key, read_endpoint_key
    from pitwall.personal.routes import RouteRunner
    from pitwall.personal.service import LiveRunPod, PersonalServeService
    from pitwall.personal.state import StateStore

    store = StateStore()
    ensure_endpoint_key(store.root)
    key = read_endpoint_key(store.root) or ""
    env = dict(os.environ)
    env.setdefault(ENDPOINT_KEY_ENV, key)
    return PersonalServeService(
        store=store,
        settings=settings,
        catalogue=load_catalogue(),
        runpod=LiveRunPod(settings, os.environ["RUNPOD_API_KEY"]),
        routes=RouteRunner(settings.pitwall_routing_cli, env=env),
        endpoint_key=key,
        hf_token=os.environ.get("HF_TOKEN") or None,
    )


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a decimal: {value!r}") from exc


def cmd_serve(argv: list[str]) -> int:
    if select_backend() == "registry":
        return RegistryBackend().serve(argv)
    parser = argparse.ArgumentParser(
        prog="pitwall serve", description="Serve a catalogue model on a self-terminating pod."
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--variant")
    parser.add_argument("--gpu-class", required=True)
    parser.add_argument("--gpu-count", type=int, default=1)
    parser.add_argument("--cloud", choices=["secure", "community"], default="community")
    parser.add_argument("--ttl-minutes", "--ttl", type=int, default=60)
    parser.add_argument("--max-usd-per-hour", type=_decimal)
    parser.add_argument("--rate-per-second", type=_decimal)
    parser.add_argument("--route", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.max_usd_per_hour is None:
        _err("--max-usd-per-hour is required: it caps what one hour of this pod may cost")
        return 2
    from pitwall.personal.service import ServeFailed, ServeRefused, ServeSpec

    spec = ServeSpec(
        model=args.model,
        variant=args.variant,
        gpu_class=args.gpu_class,
        gpu_count=args.gpu_count,
        cloud=args.cloud,
        ttl_minutes=args.ttl_minutes,
        max_usd_per_hour=args.max_usd_per_hour,
        rate_per_second=args.rate_per_second,
        route=args.route,
    )
    service = _service_or_exit(args.json)
    try:
        lease = asyncio.run(service.serve(spec, progress=None if args.json else _err))
    except ServeRefused as exc:
        _err(f"refused: {exc.code} {exc.detail}".rstrip())
        return 1
    except ServeFailed as exc:
        _err(f"failed after launch: {exc.code}; pod {exc.pod_id} was terminated")
        return 3
    except KeyboardInterrupt:
        _err("interrupted; any pod created has been terminated")
        return 130
    data = lease.model_dump(mode="json")
    data["try"] = f"route-shim.sh {lease.route} prompt.md"
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"route {lease.route} is ready: {lease.served_model_id} at {lease.endpoint_url}")
        print(f"deadline {lease.deadline_at.isoformat()} (terminates itself)")
        print(f"try it: {data['try']}")
    return 0


def cmd_status(argv: list[str]) -> int:
    if select_backend() == "registry":
        return RegistryBackend().status()
    parser = argparse.ArgumentParser(prog="pitwall status")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    service = _service_or_exit(args.json)
    leases = asyncio.run(service.status())
    if args.json:
        print(json.dumps({"leases": [lease.model_dump(mode="json") for lease in leases]}, indent=2))
        return 0
    if not leases:
        print("nothing running")
        return 0
    for lease in leases:
        print(
            f"{lease.route:<20} {lease.state:<10} {lease.served_model_id:<28} {lease.gpu_class:<28} ends {lease.deadline_at.isoformat()}"
        )
    from pitwall.personal.keys import ENDPOINT_KEY_ENV

    if not os.environ.get(ENDPOINT_KEY_ENV):
        print(
            f"note: {ENDPOINT_KEY_ENV} is not set in this shell; routes will not authenticate until it is"
        )
    return 0


def cmd_stop(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pitwall stop")
    parser.add_argument("route", nargs="?")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if select_backend() == "registry":
        if args.route is None:
            _err("pitwall stop <lease-id> on the registry backend")
            return 2
        return RegistryBackend().stop(args.route)
    if args.route is None and not args.all:
        _err("pitwall stop <route> or pitwall stop --all")
        return 2
    service = _service_or_exit(args.json)

    async def _run() -> list[dict[str, object]]:
        targets = (
            [args.route]
            if args.route
            else [
                lease.route
                for lease in await service.status()
                if lease.state in {"launching", "ready"}
            ]
        )
        return [(await service.stop(route)).model_dump(mode="json") for route in targets]

    stopped = asyncio.run(_run())
    print(
        json.dumps({"stopped": stopped}, indent=2)
        if args.json
        else "\n".join(f"stopped {item['route']}" for item in stopped) or "nothing to stop"
    )
    return 0
```

In `cli.py` `main`, add before the `models` branch:

```python
    if group == "serve":
        from pitwall.cli_personal import cmd_serve

        return cmd_serve(rest)
    if group == "status":
        from pitwall.cli_personal import cmd_status

        return cmd_status(rest)
    if group == "stop":
        from pitwall.cli_personal import cmd_stop

        return cmd_stop(rest)
```

and remove the Task 2 `serve → cmd_serve_model` branch (the registry delegation now lives in
`cmd_serve`). Update the Task 2 test `test_serve_dispatches_to_serve_model` to set `DATABASE_URL`.
Wire `endpoint_key` into `PersonalServeService.__init__` as decided in Task 9.

- [ ] **Step 4: Run, lint, commit**

```bash
uv run --frozen pytest tests/cli tests/personal -q -p no:randomly && uv run --frozen ruff check . && uv run --frozen mypy src/pitwall/cli_personal.py src/pitwall/personal
git add src/pitwall/cli_personal.py src/pitwall/cli.py tests/cli/test_personal_verbs.py tests/cli/test_cli_dispatch.py && git commit -s -m "feat(cli): pitwall serve, status, and stop over the shared engine"
```

### Task 11: `pitwall setup`

**Files:**
- Create: `src/pitwall/personal/setup.py`
- Modify: `src/pitwall/cli_personal.py` (add `cmd_setup`), `src/pitwall/cli.py` dispatch
- Test: `tests/personal/test_setup.py`

**Interfaces:**
- Produces: `run_setup(*, environ, home, state_root, prompt: Callable[[str], bool], run=subprocess.run, out: Callable[[str], None]) -> SetupReport` with fields `credential_source`, `config_tightened`, `endpoint_key_path`, `profile_updated`, `routing_cli_found`, `backend`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import os
from pathlib import Path

from pitwall.personal.setup import run_setup


def test_setup_creates_key_and_offers_profile_line(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("# rc\n")
    (home / ".runpod").mkdir()
    (home / ".runpod" / "config.toml").write_text('apikey = "k"\n')
    os.chmod(home / ".runpod" / "config.toml", 0o644)
    answers = iter([True, True])  # tighten config, edit profile
    lines: list[str] = []

    report = run_setup(
        environ={"HOME": str(home), "SHELL": "/bin/bash", "PATH": ""},
        home=home,
        state_root=tmp_path / "state",
        prompt=lambda _q: next(answers),
        run=lambda *a, **k: None,
        out=lines.append,
    )

    assert report.credential_source == "runpodctl"
    assert (
        report.config_tightened is True
        and oct((home / ".runpod" / "config.toml").stat().st_mode & 0o777) == "0o600"
    )
    assert (tmp_path / "state" / "endpoint.key").exists()
    assert 'export PITWALL_ENDPOINT_KEY="$(cat ' in (home / ".bashrc").read_text()
    assert report.profile_updated is True
    assert report.routing_cli_found is False
    assert report.backend == "personal"
    assert any("pitwall-agent-routing" in line for line in lines)


def test_setup_is_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("")
    env = {"HOME": str(home), "SHELL": "/bin/bash", "RUNPOD_API_KEY": "k", "PATH": ""}
    run_setup(
        environ=env,
        home=home,
        state_root=tmp_path / "s",
        prompt=lambda _q: True,
        run=lambda *a, **k: None,
        out=lambda _m: None,
    )
    key_before = (tmp_path / "s" / "endpoint.key").read_text()
    run_setup(
        environ=env,
        home=home,
        state_root=tmp_path / "s",
        prompt=lambda _q: True,
        run=lambda *a, **k: None,
        out=lambda _m: None,
    )
    assert (tmp_path / "s" / "endpoint.key").read_text() == key_before
    assert (home / ".bashrc").read_text().count("PITWALL_ENDPOINT_KEY") == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/personal/test_setup.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
"""First-run setup: credential, endpoint key, shell profile, plugin CLI, backend."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pitwall.personal.backend import BackendName, select_backend
from pitwall.personal.keys import (
    ENDPOINT_KEY_ENV,
    CredentialSource,
    config_file_is_shared,
    ensure_endpoint_key,
    resolve_runpod_api_key,
    runpodctl_config_path,
)

_PROFILE_MARK = "# pitwall endpoint key"


@dataclass(frozen=True, slots=True)
class SetupReport:
    credential_source: CredentialSource
    config_tightened: bool
    endpoint_key_path: Path
    profile_updated: bool
    routing_cli_found: bool
    backend: BackendName


def _profile_for(environ: Mapping[str, str], home: Path) -> Path:
    shell = environ.get("SHELL", "")
    if shell.endswith("zsh"):
        return home / ".zshrc"
    if shell.endswith("fish"):
        return home / ".config" / "fish" / "config.fish"
    return home / ".bashrc"


def run_setup(
    *,
    environ: Mapping[str, str],
    home: Path,
    state_root: Path,
    prompt: Callable[[str], bool],
    run: Callable[..., object] = subprocess.run,
    out: Callable[[str], None] = print,
) -> SetupReport:
    key, source = resolve_runpod_api_key(environ, runpodctl_config_path({"HOME": str(home)}))
    if key is None:
        if shutil.which("runpodctl", path=environ.get("PATH")) and prompt(
            "No RunPod credential found. Run `runpodctl doctor` to sign in now?"
        ):
            run(["runpodctl", "doctor"], check=False)
            key, source = resolve_runpod_api_key(
                environ, runpodctl_config_path({"HOME": str(home)})
            )
        else:
            out(
                "No RunPod credential: export RUNPOD_API_KEY, or install runpodctl and run `runpodctl doctor`."
            )
    out(f"RunPod credential: {source}")

    tightened = False
    config = runpodctl_config_path({"HOME": str(home)})
    if (
        config.exists()
        and config_file_is_shared(config)
        and prompt(f"{config} is readable by other users. Make it owner-only?")
    ):
        os.chmod(config, 0o600)
        tightened = True

    key_path = ensure_endpoint_key(state_root)
    line = f'export {ENDPOINT_KEY_ENV}="$(cat {key_path})"'
    profile = _profile_for(environ, home)
    profile_updated = False
    existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
    if _PROFILE_MARK in existing or line in existing:
        out(f"Endpoint key already exported in {profile}")
    elif prompt(f"Add the endpoint key export to {profile}?"):
        profile.parent.mkdir(parents=True, exist_ok=True)
        with profile.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{_PROFILE_MARK}\n{line}\n")
        profile_updated = True
        out(f"Added to {profile}; open a new shell before using Claude Code.")
    else:
        out(f"Run this in shells that use Claude Code: {line}")

    routing_found = shutil.which("pitwall-agent-routing", path=environ.get("PATH")) is not None
    if not routing_found:
        out(
            "The pitwall-agent-routing plugin CLI is not on PATH; install the pitwall plugin to attach routes."
        )

    backend = select_backend(environ)
    out(
        "Backend: personal (local state file)"
        if backend == "personal"
        else "Backend: registry (DATABASE_URL is set)"
    )
    return SetupReport(source, tightened, key_path, profile_updated, routing_found, backend)
```

Add to `cli_personal.py`:

```python
def cmd_setup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pitwall setup")
    parser.add_argument("--yes", action="store_true", help="answer yes to every prompt")
    args = parser.parse_args(argv)
    from pathlib import Path

    from pitwall.personal.setup import run_setup
    from pitwall.personal.state import default_state_root

    def ask(question: str) -> bool:
        if args.yes:
            return True
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}

    run_setup(environ=os.environ, home=Path.home(), state_root=default_state_root(), prompt=ask)
    return 0
```

and the `setup` branch in `cli.py` `main`. Make `cmd_serve` and `cmd_dashboard` call
`run_setup(..., prompt=ask)` automatically when `resolve_runpod_api_key` finds nothing and stdin is
a TTY; otherwise print the remedy and exit 2.

- [ ] **Step 4: Run, lint, commit**

```bash
uv run --frozen pytest tests/personal tests/cli -q -p no:randomly && uv run --frozen ruff check . && uv run --frozen mypy src/pitwall/personal src/pitwall/cli_personal.py
git add -A src/pitwall/personal/setup.py src/pitwall/cli_personal.py src/pitwall/cli.py tests/personal/test_setup.py && git commit -s -m "feat(cli): pitwall setup"
```

### Task 12: MCP and docs for the verbs

**Files:**
- Modify: `src/pitwall/mcp/tools/serve.py` (`pitwall_serve_model` uses the backend selection so it works without a database), `docs/operator/serve-quickstart.md`, `README.md`
- Test: `tests/mcp/test_registry.py` stays at 75 tools; `tests/test_serve_model_quickstart.py`

- [ ] **Step 1: Make the MCP tool backend-aware**

In `pitwall_serve_model`, when `select_backend() == "personal"`, build the service with
`_build_service` from `cli_personal` (move `_build_service` into `pitwall.personal.service` as
`build_personal_service(settings)` so both import it) and return `lease.model_dump(mode="json")`.
Keep the registry path unchanged. Add a test in `tests/mcp/test_serve_tool.py` that patches
`build_personal_service` with the fake service from Task 10 and asserts the returned dict has
`route` and `try`.

- [ ] **Step 2: Rewrite the quickstart's first section**

The first section of `docs/operator/serve-quickstart.md` becomes the personal path: `pitwall setup`,
`pitwall serve …`, `pitwall status`, `route-shim.sh <route> prompt.md`, `pitwall stop <route>`. The
existing registry sections follow under a heading "With a database configured". README's quickstart
gains the same four-command block before the compose-based section.

- [ ] **Step 3: Gates and commit**

```bash
uv run --frozen pytest tests/mcp tests/test_serve_model_quickstart.py tests/test_selfhosted_docs.py -q -p no:randomly && make docs-check
git add -A && git commit -s -m "docs(personal): quickstart and MCP serve tool follow the backend"
```

### Task 13: The live test

**Files:**
- Create: `tests/live/test_personal_serve_live.py`

- [ ] **Step 1: Write the test**

```python
"""Opt-in live check of the personal serve path. Spends money; run deliberately."""

from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal

import httpx
import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]


@pytest.mark.skipif(
    not os.environ.get("PITWALL_RUN_LIVE"), reason="set PITWALL_RUN_LIVE=1 and RUNPOD_API_KEY"
)
async def test_personal_serve_round_trip_and_self_termination(tmp_path) -> None:
    from pitwall.config import get_settings
    from pitwall.models import load_catalogue
    from pitwall.personal.keys import ensure_endpoint_key, read_endpoint_key
    from pitwall.personal.routes import RouteRunner
    from pitwall.personal.service import LiveRunPod, PersonalServeService, ServeSpec
    from pitwall.personal.state import StateStore

    store = StateStore(tmp_path)
    ensure_endpoint_key(tmp_path)
    key = read_endpoint_key(tmp_path) or ""
    settings = get_settings()
    service = PersonalServeService(
        store=store,
        settings=settings,
        catalogue=load_catalogue(),
        runpod=LiveRunPod(settings, os.environ["RUNPOD_API_KEY"]),
        routes=RouteRunner(
            settings.pitwall_routing_cli, env={**os.environ, "PITWALL_ENDPOINT_KEY": key}
        ),
        endpoint_key=key,
    )
    spec = ServeSpec(
        model="ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        gpu_class="NVIDIA GeForce RTX 3090",
        cloud="community",
        ttl_minutes=15,
        max_usd_per_hour=Decimal("0.60"),
        route="live-ornith",
    )
    lease = await service.serve(spec, progress=print)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            models = await client.get(
                f"{lease.endpoint_url}/models", headers={"Authorization": f"Bearer {key}"}
            )
            assert models.status_code == 200 and lease.served_model_id in models.text
            denied = await client.get(f"{lease.endpoint_url}/models")
            assert denied.status_code in {401, 403}
        assert service._routes.probe("live-ornith").ok  # noqa: SLF001 - live check of the attached route
        # wait for the in-pod timer: TTL plus a grace period
        deadline = time.monotonic() + 15 * 60 + 240
        while time.monotonic() < deadline:
            if await service._runpod.get_pod(lease.pod_id) is None:  # noqa: SLF001
                break
            await asyncio.sleep(30)
        assert await service._runpod.get_pod(lease.pod_id) is None, (
            "in-pod timer did not terminate the pod"
        )  # noqa: SLF001
    finally:
        await service.stop("live-ornith")
```

- [ ] **Step 2: Confirm it is excluded from the hermetic suite and commit**

```bash
uv run --frozen pytest tests/live/test_personal_serve_live.py -q -m "not live"   # expected: deselected
git add tests/live/test_personal_serve_live.py && git commit -s -m "test(live): personal serve round trip and self-termination"
```

The run itself is a deliberate operator action: `PITWALL_RUN_LIVE=1 uv run --frozen pytest -m live
tests/live/test_personal_serve_live.py -s`. If the final assertion fails because the pod was stopped
rather than terminated, set `terminate=False` in `wrap_start_command`'s call site and record the
finding in `docs/evidence/`.

---

## Phase 3: Console

### Task 14: Personal source protocol and the Serve wizard

**Files:**
- Create: `src/pitwall/tui/personal.py`
- Test: `tests/tui/test_personal_screens.py`

**Interfaces:**
- Produces:

```python
class PersonalServeSource(Protocol):
    async def preview(self, spec: ServeSpec) -> ServePreview
    async def serve(self, spec: ServeSpec, progress: Callable[[str], None]) -> PersonalLease
    async def status(self) -> list[PersonalLease]
    async def stop(self, route: str) -> PersonalLease
    async def logs(self, route: str) -> str
    def key_env_present(self) -> bool
    async def gpu_choices(self, model: str, variant: str | None, cloud: str, ttl_minutes: int) -> list[FitOption]

class ServicePersonalSource(PersonalServeSource)   # wraps PersonalServeService; the only place that imports it
class StaticPersonalSource(PersonalServeSource)    # scripted, for tests
class ServeWizardScreen(Screen[None]); class PodsScreen(Screen[None]); class RoutesScreen(Screen[None])
```

- [ ] **Step 1: Write the failing Pilot tests**

```python
async def test_wizard_previews_before_confirm_and_requires_typed_route(tmp_path) -> None:
    source = StaticPersonalSource(preview=_preview(), lease=_lease())
    app = PitwallApp(personal_source=source, models_source=StaticModelCatalogueSource(_models()))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#serve-model", Input).value = "ornith-ai/Ornith-1.5-35B-A3B-GGUF"
        screen.query_one("#serve-gpu-class", Input).value = "NVIDIA GeForce RTX 3090"
        screen.query_one("#serve-cap", Input).value = "1.00"
        screen.query_one("#serve-route", Input).value = "ornith"
        screen.query_one("#serve-preview", Button).press()
        await pilot.pause()
        rendered = str(screen.query_one("#serve-preview-result", Static).content)
        assert "max spend" in rendered and "<redacted>" in rendered and source.serve_calls == 0
        screen.query_one("#serve-confirm-text", Input).value = "wrong"
        screen.query_one("#serve-launch", Button).press()
        await pilot.pause()
        assert source.serve_calls == 0
        screen.query_one("#serve-confirm-text", Input).value = "ornith"
        screen.query_one("#serve-launch", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert source.serve_calls == 1
        assert "ready" in str(screen.query_one("#serve-status", Static).content)


async def test_wizard_shows_refusal_without_launch() -> None:
    source = StaticPersonalSource(
        preview_error=ServeRefused("price_over_cap", "0.22 > 0.10"), lease=_lease()
    )
    ...  # press Preview, assert "refused: price_over_cap" rendered and serve_calls == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/tui/test_personal_screens.py -q -k wizard`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the source classes and the wizard**

The wizard is a `Screen` with `Input`s for model, variant, GPU class, cloud, TTL, cap, route; a
`Preview` button that calls `source.preview(spec)` and renders `ServePreview.request_preview`
(already redacted), price, max spend, and deadline; a confirmation `Input` plus `Launch` button
that calls `source.serve(spec, progress)` only when the typed text equals the route name; a
`#serve-status` `Static` for progress lines; and `Binding("escape", "back", "Back")`. On
`ServeRefused` render `refused: <code> <detail>`; on `ServeFailed` render
`failed after launch: <code>; pod terminated`. `ServicePersonalSource` wraps `PersonalServeService`
and is the only class in `pitwall.tui` that imports it; widgets call only the source.

Add to `PitwallApp`: a `personal_source: PersonalServeSource | None = None` constructor argument,
`Binding("s", "show_serve", "Serve")`, `Binding("d", "show_pods", "Pods")`, `Binding("t",
"show_routes", "Routes")`, and `_install_personal()` that builds `ServicePersonalSource` from
`build_personal_service(get_settings())` when no source was injected.

- [ ] **Step 4: Run, guard, commit**

```bash
uv run --frozen pytest tests/tui/test_personal_screens.py tests/tui/test_no_business_logic_guard.py tests/tui/test_help_overlay.py -q
git add src/pitwall/tui/personal.py src/pitwall/tui/app.py tests/tui/test_personal_screens.py && git commit -s -m "feat(tui): serve wizard over the personal source"
```

### Task 15: Pods and Routes views

**Files:**
- Modify: `src/pitwall/tui/personal.py`, `src/pitwall/tui/app.py`
- Test: `tests/tui/test_personal_screens.py`

- [ ] **Step 1: Write the failing tests**

```python
async def test_pods_view_lists_leases_with_remaining_time_and_stops_with_confirmation() -> None:
    source = StaticPersonalSource(leases=[_lease(state="ready")], logs="loading model\nready")
    app = PitwallApp(personal_source=source, ...)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("d"); await pilot.pause()
        table = app.screen.query_one("#pods-table", DataTable)
        assert table.row_count == 1
        assert "ready" in str(app.screen.query_one("#pods-logs", Static).content)
        app.screen.query_one("#pods-stop", Button).press(); await pilot.pause()
        app.screen.query_one("#pods-confirm-text", Input).value = "ornith"
        app.screen.query_one("#pods-confirm-stop", Button).press(); await pilot.pause()
        assert source.stopped == ["ornith"]


async def test_routes_view_shows_probe_and_shim_line_and_key_warning() -> None:
    source = StaticPersonalSource(leases=[_lease(state="ready")], key_present=False)
    ...  # press "t"; assert "route-shim.sh ornith prompt.md" and "PITWALL_ENDPOINT_KEY is not set" rendered


async def test_view_set_follows_backend(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = PitwallApp(personal_source=StaticPersonalSource(leases=[]), models_source=StaticModelCatalogueSource(_models()))
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert app.screen.name == "serve"           # personal backend opens on Serve
        await pilot.press("o"); await pilot.pause()
        assert app.screen.name == "serve"           # Overview is not installed without a database
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement**

`PodsScreen`: a `DataTable` with columns Route, Model, GPU, State, Ends in, Price/h; selecting a row
loads `source.logs(route)` into `#pods-logs`; `Stop` opens a typed confirmation (route name) then
calls `source.stop(route)`; `r` refreshes via `source.status()`. `RoutesScreen`: a `DataTable` with
Route, Endpoint, Served model, State, and a detail `Static` with the `route-shim.sh <route>
prompt.md` line and, when `source.key_env_present()` is false, the warning line. In
`PitwallApp.on_mount`, when `select_backend() == "personal"`, install only Models, Fit, Serve,
Pods, and Routes and push `serve`; otherwise keep today's behaviour and additionally install the
three personal screens.

- [ ] **Step 4: Run the TUI suite, guard, and commit**

```bash
uv run --frozen pytest tests/tui -q -p no:randomly && uv run --frozen ruff check . && uv run --frozen mypy src/pitwall/tui/personal.py src/pitwall/tui/app.py
git add src/pitwall/tui/personal.py src/pitwall/tui/app.py tests/tui/test_personal_screens.py && git commit -s -m "feat(tui): pods and routes views; view set follows the backend"
```

### Task 16: Changelog and final verification

- [ ] **Step 1: Changelog**

Under `## [Unreleased]` in `CHANGELOG.md`: `### Changed` gets "The console script is `pitwall`;
`serve-model` is `serve`; bare `pitwall` opens the console; container images are `pitwall/<service>`;
the plugin family is `pitwall`, `pitwall-codex`, `pitwall-copilot` with agent types `pitwall:<shim>`
and one script `pitwall-agent-routing`." `### Added` gets "Personal-first serving: `pitwall setup`,
`serve`, `status`, `stop` work with only a RunPod credential, keep state in
`$XDG_STATE_HOME/pitwall`, protect the endpoint with a generated key, and give every pod an in-pod
deadline; the console gains Serve, Pods, and Routes views." `### Removed` gets
"`pitwall-gpu-broker` and `model-routing` command names."

- [ ] **Step 2: Final verification**

```bash
uv run --frozen ruff check . && uv run --frozen ruff format --check .
uv run --frozen mypy src
for seed in 1 12345 987654; do uv run --frozen pytest -q -m "not integration and not slow and not live" -p randomly --randomly-seed=$seed | tail -1; done
make openapi-check && make docs-check
git ls-files -z | xargs -0 -r -n 200 uv run --frozen python tools/guards/repo_text_policy.py
uv run --frozen pytest -q -m release tests/release -p no:randomly
cd packages/agent-routing && uv run --frozen pytest -q && cd ../..
```

Expected: all clean; each seed ends with `0 failed`.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md && git commit -s -m "docs: changelog for the pitwall rename and personal-first serving"
```

Then run the live test from Task 13 once, deliberately, with the caps stated there, and record the
outcome (including whether the pod-scoped key could terminate) in a new
`docs/evidence/<date>-personal-serve-live.md`.
