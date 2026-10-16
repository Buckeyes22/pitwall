# Per-session usage accounting for agent dispatches — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `pitwall agents runs usage --current` answers "how many tokens (and, where the harness reports
it, dollars) have all the dispatches launched by this session used?", for every session on the machine,
including dispatches nested inside other dispatches.

**Architecture:** Two records are added to every dispatch. (1) A **caller**: the launching session's key,
resolved once from an inherited `PITWALL_AGENTS_SESSION`, a host-provided session id, or the nearest
ancestor host process, then exported to the child so nested dispatches inherit it. (2) A **usage**: token
counts parsed from the harness's own machine-readable output, where pitwall runs the harness in its JSON
mode and turns that back into the plain answer text callers already get. Both are written to
`result.json` and to the durable `finished` ledger row; the report reads the ledger, so `runs cleanup`
does not erase history.

**Tech Stack:** Python 3.14.7, `uv`, pytest + unittest, the run store (`src/pitwall/agents/run_store.py`),
the observations ledger (`~/.local/state/pitwall/agents/ledger/observations.jsonl`).

**Spec:** This plan; its design facts are the evidence below (development workstation, 2026-10-15).

**Order:** Executes first on `fix/agents-child-pwd` (maintainer decision, 2026-10-15), after that branch's PWD-fix
commit and before `2026-10-15-mcode-harness-and-child-pwd.md`; both plans land in one PR and one CI run.
The mcode harness does not exist yet, so its parser is part of the mcode plan (its Task 3), not Task 5 here.

## Evidence (the spec)

**Session identity available to pitwall**

| Host | What a launched process sees | Source |
|---|---|---|
| Claude Code | `CLAUDE_CODE_SESSION_ID` in Bash children *and* in the channel MCP server (parent `claude`) | `/proc/<pid>/environ` |
| OpenCode | `OPENCODE_PID` only (channel server, parent `opencode`) | `/proc/<pid>/environ` |
| Codex | no session variable in the binary's `CODEX_*` strings | `strings` on the 0.x musl binary |
| Managed launches | the channel server passes its whole environment to the launcher (`managed_channel.py`, `child_env = dict(env)`) | code |
| Nested dispatch | a child already gets `PITWALL_AGENTS_CHANNEL_DISPATCH_ID=<its dispatch id>` (`dispatch.py` `launch_child`) | code |

**Usage each harness reports** (captured samples saved under `tests/agents/fixtures/harness_usage/`)

| Harness | How | Usage location | Answer location |
|---|---|---|---|
| claude | `--output-format json` (array) | last element `type=="result"`: `usage.input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`; `total_cost_usd` | same element `result` |
| codex | `exec --json` (JSONL) | each `turn.completed.usage`: `input_tokens` (includes cached), `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens` | last `item.completed` with `item.type=="agent_message"` → `item.text` |
| opencode | `run --format json` (JSONL) | each `step_finish.part.tokens`: `input`, `output`, `reasoning`, `cache.read`, `cache.write`, `total`; `part.cost` | `text` events → `part.text`, in order |
| grok | `--output-format json` (object) | `usage.input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, `reasoning_tokens`, `total_tokens`; `total_cost_usd` | `text` |
| pi | `--mode json` (JSONL) | each `turn_end.usage`: `input`, `output`, `cacheRead`, `cacheWrite`, `reasoning`, `totalTokens`, `cost.total` (`message_end` repeats it — count `turn_end` only) | last `message_end` with `message.role=="assistant"` → `content[].text` |
| qwen | `--output-format json` (array) | last `type=="result"`: `usage.input_tokens`, `output_tokens`, `cache_read_input_tokens`; `total_cost_usd` (may be null) | `result` |
| agy | `--output-format json` (object) | `usage.input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens` | `response`; `status=="ERROR"` carries `error` |
| mcode | `--output-format json` (object) | `usage.inputTokens`, `outputTokens`, `cacheReadTokens`, `totalTokens` | `output` |
| hermes | `--usage-file PATH` (stdout unchanged) | file keys `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`, `total_tokens`, `estimated_cost_usd` (from `hermes_cli/oneshot.py` `_write_usage_file`) | stdout as today |

Arithmetic checks on the samples: opencode `total 56161 = 54215 + 3 + 1943`; grok `total 30716 = 29530 + 1152 + 34`.

**Harnesses with no captured sample yet** — kimi, dsh (both crash with `ENOSPC` on `watch`: two
`claude --resume` processes hold 21,831 inotify watches each and an `opencode` process 20,688, against
`fs.inotify.max_user_watches = 65536`), cline (timed out), goose and muse (no provider/login), zcode
(`Model creation failed`). Task 6 covers them and is blocked on those preconditions.

**Run history on this machine** (7,667 runs): opencode 6,223, codex 1,117, claude 238, zcode 13, grok 7,
kimi 2, agy/goose/hermes/muse 1 each. Tasks 1–5 cover all but 19 of them (mcode joins through the mcode plan).

## Global Constraints

- Python only through `uv run --frozen` or `.venv/bin/python`.
- Shim and `_shim` paths keep the import-weight rules (`tests/test_startup_imports.py`, `tests/agents/test_dispatch_import_weight.py`): the new modules import only the standard library and `pitwall.agents` siblings.
- `pitwall.agents` never imports broker or `pitwall.db` code.
- Callers keep receiving plain answer text on stdout; JSON stays inside the run directory.
- Hermetic tests only: canned harness output through `FAKE_STDOUT` and the fixtures; no live calls.
- New names: env `PITWALL_AGENTS_SESSION`; modules `caller.py`, `token_usage.py`, `session_usage.py`; artifacts `harness-output.jsonl`, `harness-usage.json`; command `pitwall agents runs usage`.
- Ledger rows stay `schema_version: 4`; new keys are optional, like `softDenialReason`.
- DCO-signed commits with the session attribution lines; never weaken a test or gate.

## Review Focus

1. **Harness prints something that is not the JSON pitwall asked for** (a crash trace, a login prompt) — the caller must still see that text, and the run records `usage: null` with a reason. Pinned in Task 4 (`test_unparseable_output_reaches_the_caller_unchanged`).
2. **A run that times out or is cancelled mid-stream** — usage counts every completed turn/step so far and is marked `complete: false`. Pinned in Task 4 (`test_partial_stream_counts_completed_steps`) and Task 5 per parser.
3. **A dispatch launched by another dispatch's child** — it must carry the root session, not the child harness's process. Pinned in Task 2 (`test_nested_dispatch_inherits_the_root_session`).
4. **Two sessions on one host at once** (the normal case here) — their totals never mix. Pinned in Task 7 (`test_two_sessions_are_reported_separately`).
5. **A session with failed or usage-less runs** — the report counts them separately instead of hiding them or treating them as zero tokens. Pinned in Task 7 (`test_runs_without_usage_are_counted_not_zeroed`).

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `src/pitwall/agents/caller.py` (new) | resolve the launching session | 1 |
| `src/pitwall/agents/dispatch.py` (`_LegacyDispatch.open_run`, `launch_child`, new `render_output`, `finish`, `_ledger_record`, `_base_result`, `_dispatch_legacy`) | record caller and usage | 2, 4 |
| `src/pitwall/agents/managed_channel.py` (launcher `child_env`) | stamp the session before detaching | 2 |
| `src/pitwall/agents/result.py` (`validate_result`), `resources/schemas/dispatch-result.schema.json` | allow `caller`, `usage` | 2, 4 |
| `src/pitwall/agents/token_usage.py` (new) | `TokenUsage`, `ParsedOutput`, JSONL helpers | 3 |
| `src/pitwall/agents/harnesses/base.py` | `output_mode`, `parse_output`, `parse_usage_file` | 3 |
| `src/pitwall/agents/harnesses/{claude,codex,opencode,grok,pi,qwen,agy,hermes}.py` | JSON flags and parsers | 5 |
| `src/pitwall/agents/session_usage.py` (new), `cli.py` (`_add_run_commands`, runs dispatch) | the report | 7 |
| tests, fixtures, docs, release acceptance | as listed per task | all |

One sequential lane: Tasks 4 and 5 both change `dispatch.py` and every adapter matrix.

---

### Task 1: Resolve the launching session

**Files:**
- Create: `src/pitwall/agents/caller.py`
- Test: `tests/agents/test_caller.py`

**Interfaces:**
- Produces:
  ```python
  SESSION_ENV = "PITWALL_AGENTS_SESSION"
  @dataclass(frozen=True, slots=True)
  class Caller:
      session: str            # e.g. "claude:6b1f…", "opencode:pid:4242:linux:28953197"
      source: str             # "inherited" | "host-env" | "host-process" | "unknown"
      host: str | None
      parent_dispatch_id: str | None
      def to_public_dict(self) -> dict[str, str | None]  # keys: session, source, host, parentDispatchId
  def resolve_caller(env: Mapping[str, str], *, start_pid: int,
                     read_process: Callable[[int], ProcessInfo | None] = read_process) -> Caller
  @dataclass(frozen=True, slots=True)
  class ProcessInfo:
      ppid: int
      names: frozenset[str]   # basename of comm, argv[0], and argv[1] with .js/.mjs/.cjs stripped
  def read_process(pid: int) -> ProcessInfo | None
  ```

- [ ] **Step 1: Failing tests** — `tests/agents/test_caller.py`:

```python
from pitwall.agents.caller import SESSION_ENV, ProcessInfo, resolve_caller

TREE = {
    500: ProcessInfo(ppid=400, names=frozenset({"pitwall", "python"})),
    400: ProcessInfo(ppid=300, names=frozenset({"bash"})),
    300: ProcessInfo(ppid=1, names=frozenset({"opencode"})),
}


def fake(pid: int) -> ProcessInfo | None:
    return TREE.get(pid)


def test_inherited_session_wins_over_everything() -> None:
    env = {
        SESSION_ENV: "claude:root",
        "CLAUDE_CODE_SESSION_ID": "other",
        "PITWALL_AGENTS_CHANNEL_DISPATCH_ID": "11111111-1111-4111-8111-111111111111",
    }
    caller = resolve_caller(env, start_pid=500, read_process=fake)
    assert (caller.session, caller.source) == ("claude:root", "inherited")
    assert caller.parent_dispatch_id == "11111111-1111-4111-8111-111111111111"


def test_claude_session_id_is_used_when_nothing_is_inherited() -> None:
    caller = resolve_caller({"CLAUDE_CODE_SESSION_ID": "abc"}, start_pid=500, read_process=fake)
    assert (caller.session, caller.source, caller.host) == ("claude:abc", "host-env", "claude")


def test_nearest_host_ancestor_identifies_hosts_without_a_session_variable(monkeypatch) -> None:
    monkeypatch.setattr("pitwall.agents.caller.process_identity", lambda pid: f"linux:{pid}9")
    caller = resolve_caller({}, start_pid=500, read_process=fake)
    assert (caller.session, caller.source, caller.host) == (
        "opencode:pid:300:linux:3009",
        "host-process",
        "opencode",
    )


def test_no_host_found_is_unknown_not_an_error() -> None:
    caller = resolve_caller({}, start_pid=400, read_process=lambda pid: None)
    assert (caller.session, caller.source, caller.host) == ("unknown", "unknown", None)


def test_ancestor_walk_stops_on_a_cycle() -> None:
    loop = {
        10: ProcessInfo(ppid=11, names=frozenset({"bash"})),
        11: ProcessInfo(ppid=10, names=frozenset({"bash"})),
    }
    assert resolve_caller({}, start_pid=10, read_process=loop.get).source == "unknown"


def test_read_process_sees_this_test_process() -> None:
    import os
    from pitwall.agents.caller import read_process

    info = read_process(os.getpid())
    assert info is not None and info.ppid == os.getppid()
```

Run: `.venv/bin/python -m pytest tests/agents/test_caller.py -q 2>&1 | tail -2` → `ModuleNotFoundError`.

- [ ] **Step 2: Implement** — `src/pitwall/agents/caller.py`:

```python
"""Which session launched a dispatch: inherited key, host session id, or host process."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .channel import CHANNEL_DISPATCH_ENV
from .pids import process_identity

SESSION_ENV = "PITWALL_AGENTS_SESSION"
# Host CLIs that export their own session id to the processes they start (verified:
# Claude Code sets it for Bash children and for stdio MCP servers).
HOST_SESSION_ENVS: tuple[tuple[str, str], ...] = (("CLAUDE_CODE_SESSION_ID", "claude"),)
# Process names that mark the host session when no variable names it.
HOST_PROCESS_NAMES = frozenset(
    {
        "claude",
        "codex",
        "copilot",
        "opencode",
        "kimi",
        "qwen",
        "grok",
        "agy",
        "pi",
        "hermes",
        "cline",
        "goose",
        "muse",
        "zcode",
        "mcode",
        "dsh",
    }
)
_MAX_DEPTH = 64
_SCRIPT_SUFFIXES = (".js", ".mjs", ".cjs")


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    ppid: int
    names: frozenset[str]


@dataclass(frozen=True, slots=True)
class Caller:
    session: str
    source: str
    host: str | None
    parent_dispatch_id: str | None

    def to_public_dict(self) -> dict[str, str | None]:
        return {
            "session": self.session,
            "source": self.source,
            "host": self.host,
            "parentDispatchId": self.parent_dispatch_id,
        }


def _name(value: str) -> str:
    base = Path(value).name
    for suffix in _SCRIPT_SUFFIXES:
        base = base.removesuffix(suffix)
    return base


def read_process(pid: int) -> ProcessInfo | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        comm = stat[stat.index("(") + 1 : stat.rindex(")")]
        ppid = int(stat.rsplit(") ", 1)[1].split()[1])
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError, ValueError, IndexError, UnicodeDecodeError:
        return _read_process_ps(pid)
    names = {comm, *(_name(a.decode("utf-8", "replace")) for a in argv[:2] if a)}
    return ProcessInfo(ppid, frozenset(names))


def _read_process_ps(pid: int) -> ProcessInfo | None:
    # macOS has no procfs; ps reports the parent and the full command line.
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid=,command="],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        ).stdout.split()
        return ProcessInfo(int(out[0]), frozenset(_name(a) for a in out[1:3]))
    except OSError, subprocess.SubprocessError, ValueError, IndexError:
        return None


def resolve_caller(
    env: Mapping[str, str],
    *,
    start_pid: int,
    read_process: Callable[[int], ProcessInfo | None] = read_process,
) -> Caller:
    parent = env.get(CHANNEL_DISPATCH_ENV) or None
    inherited = env.get(SESSION_ENV, "").strip()
    if inherited:
        host = inherited.split(":", 1)[0] if ":" in inherited else None
        return Caller(inherited, "inherited", host, parent)
    for variable, host in HOST_SESSION_ENVS:
        value = env.get(variable, "").strip()
        if value:
            return Caller(f"{host}:{value}", "host-env", host, parent)
    pid, seen = start_pid, set()
    for _ in range(_MAX_DEPTH):
        if pid <= 1 or pid in seen:
            break
        seen.add(pid)
        info = read_process(pid)
        if info is None:
            break
        match = sorted(info.names & HOST_PROCESS_NAMES)
        if match:
            identity = process_identity(pid) or "unknown-start"
            return Caller(f"{match[0]}:pid:{pid}:{identity}", "host-process", match[0], parent)
        pid = info.ppid
    return Caller("unknown", "unknown", None, parent)
```

- [ ] **Step 3: Pass** — `.venv/bin/python -m pytest tests/agents/test_caller.py tests/agents/test_dispatch_import_weight.py tests/test_startup_imports.py -q 2>&1 | tail -2` → all pass.

- [ ] **Step 4: Commit** — `git add src/pitwall/agents/caller.py tests/agents/test_caller.py && git commit -s -m "feat(agents): resolve the session that launched a dispatch"`

---

### Task 2: Every dispatch records its caller and passes the session on

**Files:**
- Modify: `src/pitwall/agents/dispatch.py` — `_LegacyDispatch.open_run` (resolve `self.caller` right after `self.dispatch_id`), `launch_child` (beside `harness_env[CHANNEL_DISPATCH_ENV] = self.dispatch_id`), `_base_result` (new keyword `caller`), `finish` (pass it; add `session` / `parentDispatchId` to the `finished` ledger row), `_ledger_record` (new optional `caller` parameter)
- Modify: `src/pitwall/agents/managed_channel.py` — launcher `child_env` block (`child_env["PITWALL_AGENTS_DISPATCH_ID"] = dispatch_id`): add `child_env.setdefault(SESSION_ENV, resolve_caller(env, start_pid=os.getpid()).session)` so the key is fixed while the channel server's parent is still the host
- Modify: `src/pitwall/agents/result.py` — `validate_result`: `extra = set(value) - REQUIRED_FIELDS - {"route", "caller", "usage"}`; if present, `caller` must be a dict with string `session` and `source`
- Modify: `src/pitwall/agents/resources/schemas/dispatch-result.schema.json` — optional `caller` object (`session`, `source`, `host`, `parentDispatchId`; nullable strings for the last two)
- Test: `tests/agents/test_dispatch_contract.py`, `tests/agents/test_managed_channel.py` (or the file that covers the launcher; `grep -ln 'MANAGED_LAUNCH_ENV' tests/agents`)

**Interfaces:**
- Consumes: Task 1 `resolve_caller`, `SESSION_ENV`.
- Produces: `result.json["caller"]`; ledger `finished` rows with `session` (always) and `parentDispatchId` (when nested); child env `PITWALL_AGENTS_SESSION`.

- [ ] **Step 1: Failing tests** (add to `tests/agents/test_dispatch_contract.py`, using its existing `ShimSandbox` style):

```python
def test_result_and_ledger_carry_the_launching_session(self) -> None:
    sandbox = ShimSandbox()
    try:
        sandbox.install_harness("codex")
        result = sandbox.run(
            "codex",
            [str(sandbox.prompt())],
            env=sandbox.environment(
                CLAUDE_CODE_SESSION_ID="sess-1", FAKE_ENV_FILE=str(sandbox.env_file)
            ),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        run = sandbox.run_directories()[-1]
        caller = json.loads((run / "result.json").read_text())["caller"]
        self.assertEqual(("claude:sess-1", "host-env"), (caller["session"], caller["source"]))
        finished = [r for r in sandbox.ledger_records() if r["event"] == "finished"][-1]
        self.assertEqual("claude:sess-1", finished["session"])
        child_env = json.loads(sandbox.env_file.read_text())
        self.assertEqual("claude:sess-1", child_env["PITWALL_AGENTS_SESSION"])
    finally:
        sandbox.cleanup()


def test_nested_dispatch_inherits_the_root_session(self) -> None:
    # FAKE_NESTED_DISPATCH makes the fake harness run a second shim from inside itself.
    sandbox = ShimSandbox()
    try:
        sandbox.install_harness("codex")
        result = sandbox.run(
            "codex",
            [str(sandbox.prompt())],
            env=sandbox.environment(CLAUDE_CODE_SESSION_ID="root", FAKE_NESTED_DISPATCH="codex"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        rows = [r for r in sandbox.ledger_records() if r["event"] == "finished"]
        self.assertEqual({"claude:root"}, {r["session"] for r in rows})
        outer = rows[-1]["dispatch_id"]
        self.assertEqual([outer], [r["parentDispatchId"] for r in rows if "parentDispatchId" in r])
    finally:
        sandbox.cleanup()
```

Read `FAKE_NESTED_DISPATCH` handling in `tests/agents/fixtures/fake_harness.py` (lines 55–75) first and pass the value it expects; the assertions stay as written. Expected now: `KeyError: 'caller'`.

- [ ] **Step 2: Implement** the edits listed under **Files**. In `open_run`: `self.caller = resolve_caller(self.env, start_pid=os.getppid())`. In `launch_child`: `harness_env[SESSION_ENV] = self.caller.session`. In `_base_result`: `if caller is not None: result["caller"] = caller.to_public_dict()`; `_finish_early` and `finish` pass `caller=self.caller` (early exits that happen before `open_run` pass `None`). In `_ledger_record`: `if caller is not None: record["session"] = caller.session; if caller.parent_dispatch_id: record["parentDispatchId"] = caller.parent_dispatch_id`; `_LegacyDispatch._ledger` passes `caller=self.caller`.

- [ ] **Step 3: Pass**

```bash
.venv/bin/python -m pytest tests/agents/test_dispatch_contract.py tests/agents/test_shim_contract.py tests/agents/test_journeys.py tests/agents/test_result.py $(grep -ln 'MANAGED_LAUNCH_ENV' tests/agents) -q 2>&1 | tail -2
uv run --frozen python tools/agents/validate_json_schemas.py
```
Expected: pass; validator OK. (`test_result.py` is the result-validation suite; if it is named differently, `grep -ln validate_result tests/agents`.)

- [ ] **Step 4: Commit** — `git commit -s -am "feat(agents): dispatches record and pass on the launching session"`

---

### Task 3: Usage types and the adapter hooks

**Files:**
- Create: `src/pitwall/agents/token_usage.py`
- Modify: `src/pitwall/agents/harnesses/base.py` — class attributes and two methods on `HarnessAdapter`
- Test: `tests/agents/test_token_usage.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class TokenUsage:
      input_tokens: int; output_tokens: int
      cache_read_tokens: int | None = None; cache_write_tokens: int | None = None
      reasoning_tokens: int | None = None; total_tokens: int | None = None
      cost_usd: float | None = None
      complete: bool = True
      source: str = "harness-json"      # or "harness-report"
      def __add__(self, other: "TokenUsage") -> "TokenUsage"
      def to_public_dict(self) -> dict[str, Any]   # camelCase keys below
      @classmethod
      def from_public_dict(cls, data: Mapping[str, Any]) -> "TokenUsage"   # inverse; used by Task 7
  @dataclass(frozen=True, slots=True)
  class ParsedOutput:
      answer: bytes; usage: TokenUsage | None; diagnostic: str | None = None
  def json_lines(raw: bytes) -> list[dict[str, Any]]      # skips blank and non-JSON lines
  def json_document(raw: bytes) -> Any | None              # whole-buffer JSON or None
  def as_int(value: Any) -> int | None                     # non-bool int ≥ 0, else None
  ```
  `HarnessAdapter.output_mode: str = "text"` (`"text" | "json" | "usage-file"`);
  `HarnessAdapter.parse_output(self, raw: bytes) -> ParsedOutput` (default: `ParsedOutput(raw, None)`);
  `HarnessAdapter.parse_usage_file(self, data: Any) -> TokenUsage | None` (default `None`).
  Public dict keys: `inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens, reasoningTokens, totalTokens, costUsd, complete, source`.

- [ ] **Step 1: Failing tests** — `tests/agents/test_token_usage.py`:

```python
from pitwall.agents.token_usage import TokenUsage, as_int, json_document, json_lines


def test_addition_keeps_unknowns_unknown_and_sums_knowns() -> None:
    a = TokenUsage(10, 2, cache_read_tokens=5, cost_usd=0.5)
    b = TokenUsage(1, 1, cache_read_tokens=None, cost_usd=0.25, complete=False)
    total = a + b
    assert (total.input_tokens, total.output_tokens) == (11, 3)
    assert total.cache_read_tokens == 5
    assert total.cost_usd == 0.75
    assert total.complete is False


def test_public_dict_shape() -> None:
    assert TokenUsage(1, 2, total_tokens=3).to_public_dict() == {
        "inputTokens": 1,
        "outputTokens": 2,
        "cacheReadTokens": None,
        "cacheWriteTokens": None,
        "reasoningTokens": None,
        "totalTokens": 3,
        "costUsd": None,
        "complete": True,
        "source": "harness-json",
    }


def test_json_helpers_tolerate_noise() -> None:
    assert json_lines(b'{"a":1}\nnot json\n\n{"b":2}\n') == [{"a": 1}, {"b": 2}]
    assert json_document(b"Reading additional input\n") is None
    assert as_int(True) is None and as_int(-1) is None and as_int(7) == 7
```

- [ ] **Step 2: Implement** `token_usage.py` per the interface (in `__add__`, a field is `None` only when both sides are `None`; `complete` is the AND; `source` is kept from the left side). In `base.py` add after `private_prompt_file`:

```python
# How the dispatch reads the harness's answer and usage: "text" passes stdout through;
# "json" runs the harness in its JSON mode and parse_output turns it back into the answer;
# "usage-file" leaves stdout alone and reads the report the harness writes beside it.
output_mode = "text"


def parse_output(self, raw: bytes) -> ParsedOutput:
    """Answer text and token usage from the harness's raw stdout (json mode)."""
    return ParsedOutput(raw, None)


def parse_usage_file(self, data: Any) -> TokenUsage | None:
    """Token usage from the harness's own report file (usage-file mode)."""
    del data
    return None
```

- [ ] **Step 3: Pass** — `.venv/bin/python -m pytest tests/agents/test_token_usage.py -q 2>&1 | tail -1` → pass.
- [ ] **Step 4: Commit** — `git add src/pitwall/agents/token_usage.py src/pitwall/agents/harnesses/base.py tests/agents/test_token_usage.py && git commit -s -m "feat(agents): token usage type and adapter output hooks"`

---

### Task 4: Dispatch captures raw output, renders the answer, records usage

**Files:**
- Modify: `src/pitwall/agents/dispatch.py`:
  - `prepare_workspace`, where it sets `self.preflight_data = {..., "workspacePath": ...}`: add `"usageFile": str(self.store.artifact("harness-usage.json"))`.
  - `launch_child`: when `self.adapter.output_mode == "json"`, pass `stdout_path=store.artifact("harness-output.jsonl")` and `terminal_stdout_fd=None` to `run_process`.
  - new method `render_output(self) -> None`, called in `_dispatch_legacy` between `run.launch_child()` and `run.apply_soft_denial()`.
  - `finish`: `_base_result(..., usage=...)`; add `usage` / `usageError` to the `finished` ledger row.
- Modify: `src/pitwall/agents/run_store.py` — `artifact_summary` names: add `"harness-output.jsonl"`, `"harness-usage.json"`.
- Modify: `src/pitwall/agents/result.py` and `dispatch-result.schema.json` — optional `usage` (object with the public keys, or `null`) and optional `usageError` string. (Add `"usageError"` to the allowed extras.)
- Test: `tests/agents/test_dispatch_usage.py` (new)

**Interfaces:**
- Consumes: Task 3 types and hooks.
- Produces: `self.usage: TokenUsage | None`, `self.usage_error: str | None`; `stdout.log` always holds the answer text; `harness-output.jsonl` holds raw JSON for json-mode harnesses.

`render_output` behavior:
```python
    def render_output(self) -> None:
        self.usage, self.usage_error = None, None
        mode = self.adapter.output_mode
        if mode == "usage-file":
            path = self.store.artifact("harness-usage.json")
            try:
                self.usage = self.adapter.parse_usage_file(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                self.usage_error = f"no usage report: {exc.__class__.__name__}"
            return
        if mode != "json":
            self.usage_error = f"{self.harness_id} reports no token usage"
            return
        raw_path = self.store.artifact("harness-output.jsonl")
        raw = raw_path.read_bytes() if raw_path.is_file() else b""
        parsed = self.adapter.parse_output(raw)
        answer = parsed.answer
        if parsed.usage is None and not answer:
            answer = raw  # never hide what the harness printed
        self.store.write_bytes("stdout.log", answer)
        self.process_result = replace(self.process_result, stdout_bytes=len(answer))
        if parsed.diagnostic:
            with self.store.artifact("stderr.log").open("ab") as handle:
                handle.write(parsed.diagnostic.encode() + b"\n")
            print(f"{self.harness_id}-shim: {parsed.diagnostic}", file=sys.stderr)
        if self.env.get(MANAGED_LAUNCH_ENV) != "1" and answer:
            os.write(1, answer)
        self.usage = parsed.usage
        if parsed.usage is None:
            self.usage_error = parsed.diagnostic or "harness output carried no usage"
```

(Confirm the `ProcessResult` field name for stdout byte count in `process.py`; `apply_soft_denial` reads it as `result.stdout_bytes`.)

- [ ] **Step 1: Failing tests** — `tests/agents/test_dispatch_usage.py` drives a json-mode harness (codex after Task 5; for this task, a test-only adapter registered with `unittest.mock.patch.dict("pitwall.agents.harnesses._ADAPTERS", {"codex": FakeJsonCodex})` where `FakeJsonCodex(CodexAdapter)` sets `output_mode = "json"` and `parse_output` returns `ParsedOutput(b"answer\n", TokenUsage(3, 1))` when `raw` contains `"turn.completed"`, else `ParsedOutput(b"", None, "unparseable")`):

```python
def test_json_mode_answer_reaches_stdout_and_usage_is_recorded():
    ...
    # FAKE_STDOUT='{"type":"turn.completed"}\n' → stdout contains b"answer\n" before SHIM-DONE,
    # result.json usage == {"inputTokens": 3, "outputTokens": 1, ...}, harness-output.jsonl holds the raw line,
    # finished ledger row has usage.


def test_unparseable_output_reaches_the_caller_unchanged():
    ...
    # FAKE_STDOUT='Traceback: boom\n' → stdout contains b"Traceback: boom", result.json usage is None,
    # usageError == "unparseable".


def test_text_mode_harness_records_usage_unavailable():
    ...
    # a text-mode adapter → usage None, usageError "<id> reports no token usage", stdout unchanged.


def test_partial_stream_counts_completed_steps():
    ...
    # FakeJsonCodex returning TokenUsage(3, 1, complete=False) with FAKE_EXIT=124 → usage.complete is False,
    # exit code preserved.


def test_managed_launch_does_not_echo_the_answer_to_fd1():
    ...
    # with PITWALL_AGENTS_MANAGED_LAUNCH=1 (MANAGED_LAUNCH_ENV) the answer is in stdout.log only.
```

Write each body with the `ShimSandbox` calls used in `tests/agents/test_shim_contract.py` (`install_harness`, `run`, `environment`, `run_directories`, `ledger_records`).

- [ ] **Step 2: Implement** the edits above.
- [ ] **Step 3: Pass** — `.venv/bin/python -m pytest tests/agents/test_dispatch_usage.py tests/agents/test_dispatch_contract.py tests/agents/test_shim_contract.py tests/agents/test_journeys.py tests/agents/test_run_store.py -q 2>&1 | tail -2` → pass.
- [ ] **Step 4: Commit** — `git commit -s -am "feat(agents): dispatch renders JSON-mode answers and records token usage"`

---

### Task 5: Harness parsers and JSON flags

Each adapter gets `output_mode`, its output flag, and `parse_output` (or `parse_usage_file`), tested
against its fixture. Flag edits (in each adapter's `prepare`):

| Adapter | Change |
|---|---|
| `claude.py` | `["--output-format", "text"]` → `["--output-format", "json"]` |
| `codex.py` | `args = ["exec", "--skip-git-repo-check"]` → `["exec", "--json", "--skip-git-repo-check"]` |
| `opencode.py` | after `args = ["run"]` append `"--format", "json"` |
| `grok.py` | `"--output-format", "plain"` → `"--output-format", "json"` |
| `pi.py` | add `"--mode", "json"` after `"--no-session"` |
| `qwen.py` | `"--output-format", "text"` → `"--output-format", "json"` |
| `agy.py` | the `"--output-format", "text"` pair → `"json"` |
| `hermes.py` | `output_mode = "usage-file"`; add `"--usage-file", preflight_data["usageFile"]` before `-z` |

Each json adapter also adds the output-format flag to its `_RESERVED_FLAGS` set (or equivalent parse-time check) when it has one, so a caller cannot switch the format back.

**Files:**
- Modify: the eight adapters above.
- Create: `tests/agents/test_harness_usage_parsers.py`
- Fixtures (already captured): `tests/agents/fixtures/harness_usage/{claude.json,codex.jsonl,opencode.jsonl,grok.json,pi.jsonl,qwen.json,agy-error.json,mcode.json,hermes-usage.json}`
- Modify (argv expectations): `tests/agents/test_journeys.py` `ARGV` for the nine; `tests/agents/test_shim_contract.py` tables that pin argv/policy for them; any `tests/agents/test_<harness>.py` that asserts the old flag (find with `grep -rn '"--output-format", "text"\|"plain"\|"--skip-git-repo-check"' tests/agents`).

**Interfaces:**
- Consumes: Task 3 `ParsedOutput`, `TokenUsage`, `json_lines`, `json_document`, `as_int`.
- Produces: `get_adapter(h).output_mode` is `"json"` for claude, codex, opencode, grok, pi, qwen, agy; `"usage-file"` for hermes; `"text"` for the rest.

- [ ] **Step 1: Failing parser tests** — `tests/agents/test_harness_usage_parsers.py`:

```python
import json
from pathlib import Path

import pytest

from pitwall.agents.harnesses import get_adapter

FIX = Path(__file__).parent / "fixtures" / "harness_usage"


def parsed(harness: str, name: str):
    return get_adapter(harness).parse_output((FIX / name).read_bytes())


@pytest.mark.parametrize(
    "harness,name,answer,inp,out,cache_r,cache_w,reason,total,cost",
    [
        ("claude", "claude.json", b"ok\n", 2, 4, 0, 42123, None, 42129, 0.008426800000000002),
        ("codex", "codex.jsonl", b"ok\n", 6202, 5, 12288, 0, 0, 18495, None),
        ("opencode", "opencode.jsonl", b"ok\n", 54215, 3, 1943, 0, 0, 56161, 0),
        ("grok", "grok.json", b"ok\n", 29530, 34, 1152, 0, 33, 30716, 0.05984),
        ("pi", "pi.jsonl", b"ok\n", 2426, 2, 0, 0, 0, 2428, 0),
    ],
)
def test_parser_reads_answer_and_usage(
    harness, name, answer, inp, out, cache_r, cache_w, reason, total, cost
):
    result = parsed(harness, name)
    assert result.answer == answer
    u = result.usage
    assert u is not None and u.complete
    assert (
        u.input_tokens,
        u.output_tokens,
        u.cache_read_tokens,
        u.cache_write_tokens,
        u.reasoning_tokens,
        u.total_tokens,
        u.cost_usd,
    ) == (inp, out, cache_r, cache_w, reason, total, cost)


def test_qwen_error_result_still_reports_zero_usage_and_its_text() -> None:
    result = parsed("qwen", "qwen.json")
    assert result.answer == b"[API Error: 405 status code (no body)]\n"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (0, 0)


def test_agy_error_document_becomes_a_diagnostic() -> None:
    result = parsed("agy", "agy-error.json")
    assert result.answer == b""
    assert result.diagnostic == "agy reported ERROR: authentication failed or timed out"
    assert result.usage is not None and result.usage.total_tokens == 0


def test_hermes_usage_file() -> None:
    u = get_adapter("hermes").parse_usage_file(json.loads((FIX / "hermes-usage.json").read_text()))
    assert (
        u.input_tokens,
        u.output_tokens,
        u.cache_read_tokens,
        u.cache_write_tokens,
        u.reasoning_tokens,
        u.total_tokens,
        u.cost_usd,
        u.source,
    ) == (1200, 34, 800, 0, 12, 1234, 0.0123, "harness-report")


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode", "grok", "pi", "qwen", "agy"])
def test_garbage_is_not_usage(harness: str) -> None:
    result = get_adapter(harness).parse_output(b"Traceback (most recent call last):\n  boom\n")
    assert result.usage is None and result.answer == b""


def test_codex_partial_stream_counts_completed_turns() -> None:
    raw = (FIX / "codex.jsonl").read_bytes() + b'{"type":"turn.started"}\n'
    u = get_adapter("codex").parse_output(raw).usage
    assert u.total_tokens == 18495 and u.complete is False


def test_opencode_sums_every_step() -> None:
    line = [l for l in (FIX / "opencode.jsonl").read_bytes().splitlines() if b"step_finish" in l][0]
    raw = (FIX / "opencode.jsonl").read_bytes() + line + b"\n"
    assert get_adapter("opencode").parse_output(raw).usage.total_tokens == 2 * 56161
```

Mapping rules each parser implements (from the evidence table):
- **claude / qwen:** last element with `type=="result"`; `input=usage.input_tokens`, `cache_read=cache_read_input_tokens`, `cache_write=cache_creation_input_tokens` (qwen: `None` when absent), `output=output_tokens`, `total=input+cache_read+(cache_write or 0)+output`, `cost=total_cost_usd`; answer `result + "\n"`; no result element → `complete=False` usage `None`.
- **codex:** sum every `turn.completed.usage`: `cache_read=cached_input_tokens`, `input=input_tokens-cached_input_tokens`, `cache_write=cache_write_input_tokens`, `output=output_tokens`, `reasoning=reasoning_output_tokens`, `total=input_tokens+output_tokens`; `complete` is False when the last `turn.started` has no later `turn.completed`; answer = last `agent_message` text + `"\n"`.
- **opencode:** sum every `step_finish.part.tokens` (`input`, `output`, `reasoning`, `cache.read`, `cache.write`, `total`) and `part.cost`; answer = `"\n".join(text parts) + "\n"`; `complete` is False when a `step_start` has no matching later `step_finish`.
- **grok:** single object; fields as named; `cost=total_cost_usd`; answer `text + "\n"`.
- **pi:** sum `turn_end.usage` only; `cost=usage.cost.total`; answer from the last assistant `message_end`; `complete` False without `agent_end`.
- **agy:** single object; `reasoning=thinking_tokens`; `status=="ERROR"` → `answer=b""`, `diagnostic=f"agy reported ERROR: {error}"`.
- **hermes:** `parse_usage_file` maps the keys named in the evidence; `source="harness-report"`; `complete = not failed`.
- An empty answer gets no trailing newline.

- [ ] **Step 2: Run** — `.venv/bin/python -m pytest tests/agents/test_harness_usage_parsers.py -q 2>&1 | tail -2` → fails.
- [ ] **Step 3: Implement** the eight adapters and flag changes; update the argv expectation tables.
- [ ] **Step 4: Pass**

```bash
.venv/bin/python -m pytest tests/agents -q -p no:randomly 2>&1 | tail -3
uv run --frozen python tools/agents/check_generated.py && uv run --frozen python tools/agents/sync_routes.py --check
```
Expected: `0 failed`.

- [ ] **Step 5: Commit** — `git commit -s -am "feat(agents): read token usage from eight harnesses' own reports"`

---

### Task 6: Remaining harnesses — kimi, dsh, cline, goose, muse, zcode

**Blocked until the maintainer clears the preconditions**; nothing here starts before then.

Preconditions (each is a change to this machine or an account, which only the maintainer can approve):
1. `sudo sysctl fs.inotify.max_user_watches=524288` (and persist it in `/etc/sysctl.d/`) — kimi and dsh crash with `ENOSPC` today.
2. Configured and logged-in: `goose configure`, `muse login`, zcode's model (`zcode` reports `Model creation failed`), cline's provider.

Then, for each harness, in a scratch git repo:

| Harness | Capture command (one-word prompt, no tools) |
|---|---|
| kimi | `kimi --output-format stream-json --prompt 'Reply with the single word ok.' > kimi.jsonl` |
| dsh | `dsh --help \| grep -i json` first; then its JSON flag if listed, else `dsh --profile headless 'Reply with ok.' 2> dsh.err` and look for a usage line |
| cline | `cline --json 'Reply with the single word ok.' > cline.jsonl` |
| goose | `goose run --no-session -q --stats --text 'Reply with the single word ok.' > goose.out 2> goose.err` |
| muse | `muse exec --json 'Reply with the single word ok.' > muse.jsonl` |
| zcode | `zcode --cwd . --json --prompt 'Reply with the single word ok.' > zcode.json` |

For each sample that contains token counts: trim it into `tests/agents/fixtures/harness_usage/`, add a
row to `test_parser_reads_answer_and_usage`, add the mapping, flag change, and `output_mode` to the
adapter exactly as Task 5 did, and commit per harness
(`feat(agents): read token usage from <harness>'s reports`). A harness whose sample contains no token
counts stays `output_mode = "text"`, and its `usageError` ("<id> reports no token usage") is the truthful
record; note the sample in `docs/agents/run-records.md`.

Validation: `.venv/bin/python -m pytest tests/agents/test_harness_usage_parsers.py -q 2>&1 | tail -1` → pass.

---

### Task 7: The report — `pitwall agents runs usage`

**Files:**
- Create: `src/pitwall/agents/session_usage.py`
- Modify: `src/pitwall/agents/cli.py` — `_add_run_commands` (new `usage` subparser) and the `args.runs_command` dispatch block (`if args.runs_command == "usage": return _runs_usage(args)`)
- Test: `tests/agents/test_session_usage.py`

**Interfaces:**
- Consumes: ledger `finished` rows (`session`, `usage`, `usageError`, `shim`, `model`, `dispatch_id`, `ts`) via `pitwall.agents.paths.ledger_path`.
- Produces:
  ```python
  @dataclass(slots=True)
  class SessionTotals:
      session: str; dispatches: int; with_usage: int; without_usage: int
      usage: TokenUsage | None; first_ts: str; last_ts: str
      by_model: dict[tuple[str, str], TokenUsage]   # (shim, model) → sum
  def load_finished(env: Mapping[str, str]) -> list[dict[str, Any]]
  def summarize(rows: list[dict[str, Any]], *, session: str | None = None,
                since: str | None = None) -> list[SessionTotals]   # newest last_ts first
  ```
  CLI:
  ```
  pitwall agents runs usage [--session KEY | --current] [--since YYYY-MM-DD] [--json]
  ```
  `--current` resolves the key with `resolve_caller(os.environ, start_pid=os.getppid())`. Each argument has a `help=` string (`test_every_argument_surface_is_registered_and_documented` requires it).
  Text output, one block per session:
  ```
  claude:6b1f…  dispatches 14 (12 with usage, 2 without)  2026-10-15T08:13Z → 2026-10-15T18:06Z
    tokens in 412,880  cache-read 1,203,442  cache-write 42,123  out 18,233  total 1,676,678  cost $0.41 (known for 5 of 12)
    codex gpt-6.1-sol        8  total 1,402,110
    opencode minimax-coding-plan/MiniMax-M3.1-Flash-Preview  4  total 274,568
  ```
  `--json`: `{"sessions": [{"session", "dispatches", "withUsage", "withoutUsage", "usage", "firstTs", "lastTs", "byModel": [{"shim", "model", "dispatches", "usage"}]}]}`.

- [ ] **Step 1: Failing tests** — `tests/agents/test_session_usage.py`:

```python
from pitwall.agents.session_usage import summarize


def row(session, total, *, shim="codex", model="m", usage=True, ts="2026-10-15T10:00:00Z"):
    r = {
        "event": "finished",
        "session": session,
        "shim": shim,
        "model": model,
        "ts": ts,
        "dispatch_id": f"{session}-{total}-{ts}",
    }
    if usage:
        r["usage"] = {
            "inputTokens": total - 1,
            "outputTokens": 1,
            "cacheReadTokens": None,
            "cacheWriteTokens": None,
            "reasoningTokens": None,
            "totalTokens": total,
            "costUsd": None,
            "complete": True,
            "source": "harness-json",
        }
    else:
        r["usageError"] = "codex reports no token usage"
    return r


def test_two_sessions_are_reported_separately() -> None:
    totals = {t.session: t for t in summarize([row("a", 10), row("b", 5), row("a", 7)])}
    assert (totals["a"].usage.total_tokens, totals["b"].usage.total_tokens) == (17, 5)


def test_runs_without_usage_are_counted_not_zeroed() -> None:
    [t] = summarize([row("a", 10), row("a", 0, usage=False)])
    assert (t.dispatches, t.with_usage, t.without_usage, t.usage.total_tokens) == (2, 1, 1, 10)


def test_filters_by_session_and_since() -> None:
    rows = [
        row("a", 10, ts="2026-10-01T00:00:00Z"),
        row("a", 3, ts="2026-10-15T00:00:00Z"),
        row("b", 1),
    ]
    [t] = summarize(rows, session="a", since="2026-10-10")
    assert t.usage.total_tokens == 3


def test_rows_from_before_this_feature_are_grouped_as_unattributed() -> None:
    old = {
        "event": "finished",
        "shim": "codex",
        "model": "m",
        "ts": "2026-09-01T00:00:00Z",
        "dispatch_id": "x",
    }
    [t] = summarize([old])
    assert (t.session, t.without_usage) == ("unattributed", 1)
```

Plus one CLI test through the existing CLI test style (`grep -ln '"runs", "list"' tests/agents`): seed a ledger under a temp `XDG_STATE_HOME`, run `pitwall agents runs usage --json`, and assert the JSON keys and totals.

- [ ] **Step 2: Implement** `session_usage.py` (rows without `session` group under `"unattributed"`; a usage dict becomes `TokenUsage` through `TokenUsage.from_public_dict`, which Task 3 adds so this task and Task 4 share no file) and the CLI.
- [ ] **Step 3: Pass** — `.venv/bin/python -m pytest tests/agents/test_session_usage.py tests/agents/test_cli*.py -q 2>&1 | tail -2` → pass.
- [ ] **Step 4: Commit** — `git commit -s -am "feat(agents): runs usage reports tokens and cost per launching session"`

---

### Task 8: Docs, plugin skills, release acceptance

**Files:**
- Modify: `docs/agents/run-records.md` — `caller` and `usage` in `result.json`; `harness-output.jsonl` / `harness-usage.json`; the ledger keys; the per-harness usage table (evidence table, plus Task 6 outcomes); the meaning of each field (`input` excludes cache reads for every harness after normalization; `costUsd` only where the harness reports it).
- Modify: `docs/agents/routing-readme.md` — a "Usage by session" section: `pitwall agents runs usage --current`, `--session`, `--since`, `--json`; how the session key is chosen; `PITWALL_AGENTS_SESSION` to group work explicitly.
- Modify: `plugins/{claude,codex,copilot}/skills/subagent-model-routing/SKILL.md` — one line in the operations section: "Check accumulated subagent usage with `pitwall agents runs usage --current`."
- Modify: `CHANGELOG.md` `## [Unreleased]` → `### Added`: the session record, usage capture (eight harnesses), and the `runs usage` command; `### Changed`: claude, codex, opencode, grok, pi, qwen, and agy now run in their JSON output modes inside the dispatch (callers still receive plain text).
- Modify: `tests/release/cli_fixtures.json` — `"pitwall|agents runs usage": {"argv": ["agents", "runs", "usage", "--json"], "database": false, "exit": 0, "json_keys": ["sessions"]}`.
- Modify: `tests/release/test_cli_all_commands_journey.py` — `assert len(surfaces) == 485` → the new count the test reports (the `usage` subcommand plus `--session`, `--current`, `--since`, `--json`; expected 490).
- Modify: `release_acceptance/denominator.json` — `cli_argument` family and `total` by the same delta, with a dated `reason` prefix naming the new command.
- Regenerate: `uv run --frozen python -m tools.release_acceptance.bind_surfaces`.

- [ ] **Step 1: Edit.**
- [ ] **Step 2: Check**

```bash
uv run --frozen python tools/ci/check_markdown_links.py
uv run --frozen python tools/agents/validate_plugins.py
uv run --frozen python -m tools.release_acceptance.bind_surfaces
.venv/bin/python -m pytest tests/release_acceptance -q -p no:randomly 2>&1 | tail -2
PITWALL_JOURNEY_HARNESS=1 uv run --frozen pytest -q -m release tests/release/test_cli_all_commands_journey.py -p no:randomly 2>&1 | tail -2
```
Expected: all pass; bind reports 0 unmapped.

- [ ] **Step 3: Commit** — `git add -A docs plugins CHANGELOG.md tests/release release_acceptance && git commit -s -m "docs(agents): session usage records and the runs usage command"`

---

### Task 9: Whole-branch verification

- [ ] **Step 1: CI parity** — the exact command block in Task 8 of `2026-10-15-mcode-harness-and-child-pwd.md` (lint job, shim syntax, doctor, python policy, DCO, pi-extensions, semgrep).
- [ ] **Step 2:** `make test-fast 2>&1 | tail -3` → `0 failed`.
- [ ] **Step 3:** `make up && make test-int 2>&1 | tail -3` → `0 failed` (alone; shared test database).
- [ ] **Step 4:** `bash scripts/release/run-user-journeys.sh 2>&1 | tail -20` with `live-run.env` sourced → matrix gate passes.
- [ ] **Step 5: Live check (the maintainer's accounts; run only with the maintainer's approval)** — from this Claude session's Bash, run one tiny dispatch each through codex, claude (`--model haiku`), opencode (`opencode/space-bunny-free`), and mcode, then:
  ```bash
  pitwall agents runs usage --current
  ```
  Expected: one block whose session is `claude:$CLAUDE_CODE_SESSION_ID`, four dispatches with usage, per-model rows for each, and the tokens matching each run's `result.json` `usage.totalTokens`.
- [ ] **Step 6: Managed path** — one `dispatch_and_wait` through the channel tool from this session; `pitwall agents runs usage --current` now shows five dispatches.
