# Orchestrator Channel — Phase A Completion + Phases B, C, D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the orchestrator ↔ subagent channel: close the Phase A spec items the first batch skipped, then ship Phase B (stdio MCP server and tier-1 asking), Phase C (steering and graceful abort), and Phase D (workflow `wait_for_answer` and the D3 auto-answer policy).

**Architecture:** Mailbox files under `runs/<dispatch_id>/mailbox/` stay the single source of truth. A new `channel.py` owns per-run channel settings (`channel.json`), default/expiry resolution, answer and steer services, and ledger aggregates. Every surface is a view over those services: the operator CLI verbs, the loopback broker, a new stdlib stdio MCP server (`pitwall-agent-routing mcp`), a tier-2 PreToolUse hook gate, the `_shim` supervisor's steer watcher, and the workflow scheduler's wait loop.

**Tech Stack:** Python 3.14 standard library only (`dependencies = []`), `unittest`, ruff, mypy; the component's pinned venv at `packages/agent-routing/.venv`.

**Spec:** `docs/research/2026-09-08-orchestrator-channel.md` (§4–§11, §13 Phases B–D, decisions D1–D6 in §14). Read it alongside this plan.

**Relationship to the Phase 0/A plan:** `docs/superpowers/plans/2026-09-08-orchestrator-channel-phase-0-a.md` is complete and merged. It skipped these Phase A spec items, which Part 1 below closes: deadline expiry applying the default (§10), `resolved_by: "default"` (§10), the D1 derived deadline (`derive_deadline_s` has no runtime caller), the D1 per-dispatch cap override, the D2 `runs diff` pointer on the answer surfaces, the §9.3 ledger fields, the §10 "steering ignored" signal and sequence-gap logging, the §11 property and chaos tests, and operator documentation for the whole Phase A surface (no component doc mentions `inbox`, `runs resume`, or the mailbox). Part 1 also fixes a defect found while grounding this plan: concurrent mailbox writers lose messages (Task 1).

## Global Constraints

- All component commands run from `packages/agent-routing` with the pinned venv: `.venv/bin/python`, `.venv/bin/ruff`, `.venv/bin/mypy`. Never run bare `python`.
- The runtime stays standard-library only. No new dependency in `pyproject.toml`.
- The completion contract is sacred: every dispatch ends with a `SHIM-DONE exit=<n>` sentinel or a recorded `paused` classification. Steering never bypasses the sentinel.
- Mailbox files are authoritative. Subagent-written data is untrusted: size-capped at 64 KiB, schema-validated on write and read, quarantined to `dead-letter/` on failure.
- Directory mode 0700 and file mode 0600 for everything under the state root.
- Registration and mailbox files never contain credentials. Endpoint keys are read from the variable named by `apiKeyEnv` at call time, as `route_probe.py` does.
- The MCP server is stdio only. It never opens a socket (spec §10).
- The automated suite makes no live model calls and no network calls outside loopback. Live exit checks are operator-run steps, listed with exact commands.
- Tests are hermetic: every test sets `HOME`, `XDG_STATE_HOME`, `XDG_CONFIG_HOME`, and `SUBAGENT_MODEL_ROUTING_LEDGER` to temporary paths.
- Work on branch `feat/orchestrator-channel-b-d` off current `main`. Commit after every task with `git commit -s`. Do not push.
- Every behavior change lands with its test in the same task. Existing tests keep passing. The only intended changes to existing assertions are named in the task that makes them.

## Decisions recorded for this batch

Each decision is grounded in the code as of `main` at `81110fe` (2026-09-10).

1. **Exclusive-create sequencing.** Mailbox writes publish with `os.link` from a fully written temporary file, so a taken name fails instead of being overwritten. Evidence: eight threads calling `Mailbox.write_steer` on one run lost steers in 30 of 30 trials, because `_next_id` plus `os.replace` lets two writers pick the same id. The same fix makes "one answer per ask" atomic and makes the ask cap hold under floods.
2. **Answer sources.** `answered_by` accepts `operator`, `orchestrator`, `default`, and `policy:<model>`. Bare `policy` is no longer accepted. No writer ever produced it; the only occurrence is a negative test at `tests/test_mailbox.py:117`.
3. **Effective deadline.** An ask's effective deadline is `min(deadline_s, D1 cap)`, where the D1 cap is `derive_deadline_s(remaining attempt timeout at the ask's created_at)`. Writers that can refuse (broker, MCP server) clamp at write time. Tier-4 files written directly by a subagent are clamped when read.
4. **Where defaults apply.** Tier 1: the MCP `ask_orchestrator` call applies the default when its deadline passes. Tier 4: `runs resume` applies defaults to expired asks before resuming. Phase D: the workflow wait loop applies them. `inbox` is read-only and marks expired asks.
5. **One registration mechanism.** `pitwall-agent-routing setup mcp` writes one user-scope entry per harness at the §6.3 paths for all six MCP-capable harnesses. The three plugin bundles do not also ship an MCP entry, because a bundle entry plus a user-scope entry registers the server twice, and only the harnesses' own config schemas carry the environment-forwarding and tool-timeout keys this server needs (Codex `env_vars` and `tool_timeout_sec`, Kimi `toolTimeoutMs`, Cline `timeout`). The bundles carry the tier-2 hook gate instead (Task 23). `install.sh --with-mcp` runs `setup mcp`.
6. **Role by environment.** The server lists the subagent tools only when `SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID` holds a UUID, and the orchestrator tools only when it is unset or empty. A non-UUID value lists no tools. If a harness drops the variable, its subagent simply sees no ask tool and uses the tier-4 file contract, which the prompt always carries.
7. **New channel variables.** The dispatcher gives the harness `SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID` and `SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT` (the resolved absolute state root). It does not reuse `SUBAGENT_MODEL_ROUTING_DISPATCH_ID`, which a nested dispatch would read as its own id and fail with "dispatch ID already exists".
8. **Needs operator yes before Task 12 runs.** Today `dispatch_legacy` passes its own identity variables into the harness environment, so any harness started by the workflow scheduler or by `runs resume` that dispatches again collides on the parent's dispatch id. Task 12 removes exactly `SUBAGENT_MODEL_ROUTING_DISPATCH_ID`, `SUBAGENT_MODEL_ROUTING_ATTEMPT`, `SUBAGENT_MODEL_ROUTING_WORKFLOW_ID`, and `SUBAGENT_MODEL_ROUTING_TASK_ID` from the harness environment, and adds `SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT`. Two test fakes read the old variables and are updated. If the answer is no, skip Task 12; nothing else depends on it.
9. **Cline path correction.** Cline 3.0.61 reads MCP servers from `$CLINE_MCP_SETTINGS_PATH`, else `${CLINE_DATA_DIR:-${CLINE_DIR:-~/.cline}/data}/settings/cline_mcp_settings.json` (function `nC()` in the installed binary). `capability_inventory.py` and spec §6.3 list `mcp_settings.json`. Task 10 corrects both.
10. **Open-ask limit.** Programmatic writers (broker `POST /asks`, MCP `ask_orchestrator`) refuse a second open ask. A tier-4 pause may carry several pending asks, because the run has already stopped and every one is answered before resume. The ask cap is enforced for tier-4 files at the pause boundary.
11. **Graceful abort outcome.** An abort (SIGTERM or SIGHUP to the `_shim` supervisor, or an ignored `stop` steer) ends the run as result status `cancelled`. The ledger outcome is `cancelled`, or `killed` when SIGKILL was needed after the grace period. The result schema does not change.
12. **Orphaned asks.** A failed attempt (non-zero exit that is not a timeout or cancellation) that leaves a pending ask finishes as `paused`, so `runs resume` can default the orphaned ask and continue (§11 chaos case).
13. **Operator verbs.** `answer`, `steer`, and `runs stop` are added. The Phase B exit needs an operator answer and the Phase C exit needs a scope change; today the only writer for either is the HMAC-signed broker. They are thin wrappers over the same `channel.py` services the MCP tools and broker use.
14. **D3 answerer transport.** The policy answerer calls an endpoint route's OpenAI-compatible `/chat/completions` directly with `urllib`, not through a nested agentic dispatch. Only routes with an `endpoint` qualify; the `gateway` seat is the intended one.
15. **Additive workflow keys.** `askSupport`, `maxAsks`, and `autoAnswer` appear in a normalized workflow only when the author sets them. Existing normalized documents and digests are unchanged.

## File Map

| File | Responsibility |
| --- | --- |
| `runtime/model_routing/run_store.py` | `atomic_create_bytes`/`atomic_create_json`; `ledger_path` (moved from `dispatch._ledger_path`); mailbox cap from `channel.json`; D5 aggregate before `cleanup_runs` removes a run |
| `runtime/model_routing/mailbox.py` | exclusive sequencing, answer sources, open-ask limit, cap enforcement, sequence gaps, delivery-id lookup, public `steers()`/`acks()` |
| `runtime/model_routing/channel.py` *(new)* | `ChannelConfig` + `channel.json`; effective deadlines; default resolution and expiry; `answer_ask`/`write_steer` services; §9.3 ledger fields; D5 aggregates |
| `runtime/model_routing/dispatch.py` | `--routing-max-asks`; `channel.json`; tier-1/tier-4 prompt suffix; channel env; finished-row channel fields; orphaned-ask pause; SIGTERM handling and steer watcher; resume applies defaults |
| `runtime/model_routing/process.py` | abort event, grace period, `aborted`/`killed` result flags |
| `runtime/model_routing/pitwall_sync.py` | inbox D2 pointers, expiry/ignored flags, sequence gaps; idempotent broker retries |
| `runtime/model_routing/cli.py` | `answer`, `steer`, `runs stop`, `mcp`, `setup mcp`, hidden `_steer-gate`; `inbox` and `runs diff` changes |
| `runtime/model_routing/mcp_server.py` *(new)* | stdio JSON-RPC MCP server core (framing, roles, workers, cancellation, progress) |
| `runtime/model_routing/mcp_tools.py` *(new)* | the five channel tools and `build_server` |
| `runtime/model_routing/mcp_registration.py` *(new)* | per-harness registration entries, plans, apply/remove |
| `runtime/model_routing/steer_gate.py` *(new)* | tier-2 PreToolUse decision logic |
| `runtime/model_routing/channel_policy.py` *(new)* | D3 eligibility and the endpoint-route answerer |
| `runtime/model_routing/capability_inventory.py` | Cline path fix; `mcp_channel_registered`; `mcpChannel` flag |
| `runtime/model_routing/doctor.py` | `channel.mcp_server` and `channel.registration` checks |
| `runtime/model_routing/workflow.py`, `scheduler.py` | `askSupport`/`maxAsks`/`autoAnswer`; `wait_for_answer` loop; runner `resume` |
| `scripts/install.sh`, `docs/agent-routing/migration-from-standalone.md` | `--with-mcp`; uninstall removes the registrations |
| `plugins/pitwall/hooks/*`, `plugins/pitwall-codex/hooks/*`, `plugins/pitwall-codex/.codex-plugin/plugin.json` | tier-2 steer gate |
| `plugins/*/skills/subagent-model-routing/SKILL.md`, `.../references/model-prompting.md`, `prompting/*.md` | questioning discipline and per-family notes |
| `plugins/pitwall/commands/distill.md` | read `event:"channel"` aggregates |
| `packages/agent-routing/docs/orchestrator-channel.md` *(new)*, `README.md`, `docs/run-records.md`, `docs/workflows.md`, `docs/doctor.md`, `docs/lifecycle-hooks.md` | operator documentation |

All `runtime/`, `tests/`, `scripts/`, `plugins/`, `prompting/`, and `docs/` paths above are relative to `packages/agent-routing/`, except `docs/agent-routing/…` and `docs/research/…`, which are repository-root paths.

## Execution order and lanes

Tasks are ordered by dependency. Tasks that share a file never run at the same time. Lanes branch from `feat/orchestrator-channel-b-d`, rebase onto it before merging, and merge with `git merge --ff-only` after the lane's validation passes. Never land a lane with a path-unscoped patch: on 2026-09-10 base-skewed patches silently reverted other lanes' files.

| Wave | Tasks | Lanes that may run at once | Blocked by |
| --- | --- | --- | --- |
| A | 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 | one lane (shared `mailbox.py`, `dispatch.py`, `cli.py`) | — |
| B | 9; 10; 13 → 14 → 15; 25 | four lanes: docs (9), inventory (10), MCP (13–15), policy (25) | Wave A |
| C | 11 → 12; 16 → 17 → 18; 19; then 20 | three lanes: dispatch (11–12), registration (16–18), teaching (19); Task 20 runs last because it shares `README.md` with Task 17 and needs every other Phase B task | Wave B |
| D | 21 → 22 → 23 → 24 | one lane | Wave C |
| E | 26 → 27 → 28 → 29 → 30 | one lane | Wave D and Task 25 |

Lane prompts start from `~/.claude/templates/lane-prompt.md`. Each task's **Files** block is that lane's exclusive ownership list; every other file is on its do-not-touch list.

## Task 0: Baseline

- [x] **Step 1: Branch and record the baseline**

```bash
cd ~/git/pitwall && git switch -c feat/orchestrator-channel-b-d main
cd packages/agent-routing
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check runtime tests tools scripts/pitwall-agent-routing
.venv/bin/mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing
```

Expected (measured on `main` at `81110fe`, 2026-09-10): `Ran 596 tests` and `OK (skipped=4)`; ruff prints `All checks passed!`; mypy prints `Success: no issues found in 60 source files`. Every later full-suite run must show at least 596 tests.

---

# Part 1 — Phase A completion

## Task 1: Exclusive-create mailbox sequencing

Fixes the lost-write defect (Decision 1) and gives §11 its interleaving and flood properties.

**Files:**
- Modify: `runtime/model_routing/run_store.py` (add `atomic_create_bytes`, `atomic_create_json` after `atomic_write_json`, line ~65)
- Modify: `runtime/model_routing/mailbox.py` (`Mailbox._next_id` :240, `_write_doc` :249, `write_ask` :258, `write_answer` :307, `write_steer` :333, `write_ack` :356, `quarantine` :370; add `_append`, `steers`, `acks`)
- Create: `tests/test_mailbox_concurrency.py`

**Interfaces:**
- Produces: `run_store.atomic_create_bytes(path: Path, content: bytes) -> bool` and `atomic_create_json(path: Path, value: Any) -> bool` (True when created, False when the name already existed).
- Produces: `Mailbox._write_doc(box, name, doc)` raises `FileExistsError` when the name is taken; `Mailbox._append(box: str, build: Callable[[str], dict[str, Any]]) -> dict[str, Any]`; public `Mailbox.steers() -> list[dict]` (steer-id order) and `Mailbox.acks() -> list[dict]`.
- Unchanged for callers: `write_ask`, `write_answer`, `write_steer`, `write_ack` signatures. `write_answer` still raises `MailboxError` ("already has an answer"); `write_ask` still raises `MailboxCapError`, whose message keeps the `ask cap exceeded` prefix.

- [x] **Step 1: Write the failing tests**

```python
"""Mailbox interleaving and flood properties (spec §11; plan Task 1)."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.mailbox import Mailbox, MailboxCapError, MailboxError  # noqa: E402


DISPATCH = "00000000-0000-4000-8000-0000000000c1"
OPTIONS = [{"id": "a", "text": "first"}, {"id": "b", "text": "second"}]


def _ask_in_process(run_dir: str, index: int) -> str:
    """Module level so ProcessPoolExecutor can import it in a worker."""
    sys.path.insert(0, str(ROOT / "runtime"))
    try:
        ask = Mailbox(Path(run_dir), DISPATCH).write_ask(
            blocked_on="choice", question=f"q{index}", options=OPTIONS, default="a", deadline_s=600
        )
    except MailboxCapError:
        return "capped"
    return str(ask["ask_id"])


def _run_threads(count: int, target) -> None:  # type: ignore[no-untyped-def]
    barrier = threading.Barrier(count)

    def wrapped(index: int) -> None:
        barrier.wait()
        target(index)

    threads = [threading.Thread(target=wrapped, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


class MailboxInterleavingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_concurrent_steer_writers_never_lose_a_message(self) -> None:
        for trial in range(20):
            run = self.root / f"steer-{trial}"
            _run_threads(
                8,
                lambda i, run=run: Mailbox(run, DISPATCH).write_steer(kind="note", message=f"m{i}"),
            )
            steers = Mailbox(run, DISPATCH).steers()
            self.assertEqual(
                sorted(f"m{i}" for i in range(8)), sorted(s["message"] for s in steers)
            )
            self.assertEqual([f"{n:04d}" for n in range(1, 9)], [s["steer_id"] for s in steers])

    def test_concurrent_acks_never_lose_a_message(self) -> None:
        run = self.root / "acks"
        box = Mailbox(run, DISPATCH)
        steer_ids = [box.write_steer(kind="note", message=f"m{i}")["steer_id"] for i in range(8)]
        _run_threads(8, lambda i: Mailbox(run, DISPATCH).write_ack(steer_ids[i]))
        self.assertEqual(
            sorted(steer_ids), sorted(a["steer_id"] for a in Mailbox(run, DISPATCH).acks())
        )

    def test_exactly_one_answer_wins_a_race(self) -> None:
        run = self.root / "answer"
        Mailbox(run, DISPATCH).write_ask(
            blocked_on="choice", question="q", options=OPTIONS, default="a", deadline_s=600
        )
        outcomes: list[str] = []
        lock = threading.Lock()

        def answer(index: int) -> None:
            try:
                Mailbox(run, DISPATCH).write_answer(
                    "0001",
                    choice="a" if index % 2 else "b",
                    answered_by="operator",
                    note=str(index),
                )
                result = f"won:{index}"
            except MailboxError:
                result = "lost"
            with lock:
                outcomes.append(result)

        _run_threads(6, answer)
        winners = [outcome for outcome in outcomes if outcome.startswith("won:")]
        self.assertEqual(1, len(winners), outcomes)
        stored = Mailbox(run, DISPATCH).get_answer("0001")
        assert stored is not None
        self.assertEqual(winners[0].split(":")[1], stored["note"])

    def test_ask_cap_holds_under_a_cross_process_flood(self) -> None:
        run = self.root / "flood"
        Mailbox(run, DISPATCH).ensure()
        with ProcessPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_ask_in_process, [str(run)] * 16, range(16)))
        self.assertEqual(
            ["0001", "0002", "0003", "0004", "0005"], sorted(r for r in results if r != "capped")
        )
        self.assertEqual(11, results.count("capped"))
        self.assertEqual(5, len(Mailbox(run, DISPATCH).asks()))

    def test_ids_stay_monotonic_after_the_newest_ask_is_quarantined(self) -> None:
        run = self.root / "monotonic"
        box = Mailbox(run, DISPATCH)
        box.write_ask(
            blocked_on="choice", question="q1", options=OPTIONS, default="a", deadline_s=600
        )
        (box.root / "asks" / "0002.json").write_text("{not json", encoding="utf-8")
        box.asks()  # quarantines 0002
        self.assertEqual(
            "0003",
            box.write_ask(
                blocked_on="choice", question="q3", options=OPTIONS, default="a", deadline_s=600
            )["ask_id"],
        )


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_mailbox_concurrency -v 2>&1 | tail -15`
Expected: FAIL. `test_concurrent_steer_writers_never_lose_a_message` fails with `Lists differ`, the answer race reports more than one winner, the flood test reports more than five asks, the monotonic test gets `0002`, and `steers`/`acks` raise `AttributeError`.

- [x] **Step 3: Add the exclusive-create primitive to `run_store.py`**

```python
def atomic_create_bytes(path: Path, content: bytes) -> bool:
    """Create *path* holding *content*; return False if the name already exists.

    The content is written and fsynced under a temporary name first, then
    published with ``os.link``. ``link`` refuses an existing target, so two
    writers racing for one name can never overwrite each other the way
    ``os.replace`` does.
    """
    ensure_private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def atomic_create_json(path: Path, value: Any) -> bool:
    return atomic_create_bytes(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
```

- [x] **Step 4: Route every mailbox write through exclusive creation**

In `mailbox.py`, import `Callable` from `typing` and `atomic_create_bytes` from `.run_store` (drop the `atomic_write_json` import once unused), add `_MAX_ALLOCATION_ATTEMPTS = 1000`, and replace `_next_id` and `_write_doc`:

```python
def _next_id(self, box: str) -> str:
    """Next number above every live and quarantined document in *box*."""
    self.ensure()
    taken = 0
    for path in (self.root / box).glob("*.json"):
        if len(path.stem) >= 4 and path.stem.isdigit():
            taken = max(taken, int(path.stem))
    for path in (self.root / "dead-letter").glob(f"{box}_*.json"):
        stem = _dead_letter_parts(path.name)[0].removeprefix(f"{box}_")
        if len(stem) >= 4 and stem.isdigit():
            taken = max(taken, int(stem))
    return f"{taken + 1:04d}"


def _write_doc(self, box: str, name: str, doc: Mapping[str, Any]) -> dict[str, Any]:
    """Publish *doc* as ``box/name``; raise FileExistsError when the name is taken."""
    self.ensure()
    raw = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _check_cap(raw, what=f"{box}/{name}")
    if not atomic_create_bytes(self.root / box / name, raw):
        raise FileExistsError(f"{box}/{name} already exists")
    return dict(doc)


def _append(self, box: str, build: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """Publish ``build(seq)`` under the next free sequence number in *box*.

    ``build`` validates and may raise (schema, caps). Losing a race for a
    number retries with the next one, so concurrent writers never overwrite.
    """
    for _attempt in range(_MAX_ALLOCATION_ATTEMPTS):
        seq = self._next_id(box)
        try:
            return self._write_doc(box, f"{seq}.json", build(seq))
        except FileExistsError:
            continue
    raise MailboxError(f"could not allocate a sequence number in {box}/")
```

Rewrite the writers on top of `_append`. `write_ask` drops the glob-count cap check; the id bound replaces it and cannot be raced:

```python
def build(ask_id: str) -> dict[str, Any]:
    if int(ask_id) > self.max_asks:
        raise MailboxCapError(
            f"ask cap exceeded: ask {ask_id} is past the limit of {self.max_asks} asks per run"
        )
    context: dict[str, Any] = {
        "files_touched": list(files_touched or []),
        "options": [dict(o) for o in options],
    }
    if worktree is not None:
        context["worktree"] = worktree
    ask: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "dispatch_id": self.dispatch_id,
        "ask_id": ask_id,
        "created_at": utc_now(),
        "blocked_on": blocked_on,
        "question": question,
        "context": context,
        "default": default,
        "deadline_s": deadline_s,
        "severity": severity,
    }
    if default_rationale is not None:
        ask["default_rationale"] = default_rationale
    return validate_ask(ask)


return self._append("asks", build)
```

`write_steer` builds its document the same way with `steer_id=seq` and returns `self._append("steer", build)`. `write_ack` returns `self._append("acks", lambda _seq: validated)`. `write_answer` keeps its validation and replaces its pre-check and write with:

```python
        try:
            return self._write_doc("answers", f"{ask_id}.json", validated)
        except FileExistsError as exc:
            raise MailboxError(f"ask {ask_id} already has an answer") from exc
```

`quarantine` keeps its naming scheme but publishes with `atomic_create_bytes`, bumping the counter until a name is free. Add the public readers:

```python
def steers(self) -> list[dict[str, Any]]:
    """All steers in steer-id order (invalid files are quarantined)."""
    return sorted(self._read_box("steer"), key=lambda steer: steer["steer_id"])


def acks(self) -> list[dict[str, Any]]:
    """All acknowledgements in file order (invalid files are quarantined)."""
    return self._read_box("acks")
```

`RunStore.mailbox()`'s `_StoredMailbox._write_doc` override (`run_store.py:203`) needs no change: it calls `super()._write_doc` and refreshes `mailbox.json` only after a successful publish.

- [x] **Step 5: Run the new and the existing channel tests**

Run: `.venv/bin/python -m unittest tests.test_mailbox_concurrency tests.test_mailbox tests.test_channel_run_store tests.test_channel_broker tests.test_runs_resume tests.test_pause_contract tests.test_inbox_cli 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 6: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/run_store.py runtime/model_routing/mailbox.py tests/test_mailbox_concurrency.py
git commit -s -m "fix(agent-routing): exclusive-create mailbox sequencing so concurrent writers never lose messages"
```

## Task 2: Per-run channel record, D1 derived deadlines, per-dispatch ask cap

**Files:**
- Create: `runtime/model_routing/channel.py`, `tests/test_channel_config.py`
- Modify: `runtime/model_routing/dispatch.py` (`RoutingOptions` :163, `_strip_routing_args` :204, `tier4_prompt_suffix` :522, `dispatch_legacy` :592 — ask-support block ~:772, timeout parse ~:960, pause classification ~:1000; `resume_legacy` :1048)
- Modify: `runtime/model_routing/run_store.py` (`RunStore.mailbox` :175), `runtime/model_routing/mailbox.py` (add `enforce_cap`; `deadline_remaining_s` :476 gains `deadline_s`), `runtime/model_routing/pitwall_sync.py` (`apply_channel_ask` :294)

**Interfaces:**
- Produces (`channel.py`): `CHANNEL_DISPATCH_ENV = "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID"`, `CHANNEL_STATE_ROOT_ENV = "SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT"`, `CHANNEL_ATTEMPT_ENV = "SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT"` (the only definitions; every later module imports them from here), `CHANNEL_ARTIFACT = "channel.json"`, `MAX_ASKS_CEILING = 50`, `@dataclass(frozen=True) ChannelConfig(dispatch_id, max_asks=5, max_open_asks=1, timeout_seconds=1140.0, attempt_started_epoch=0.0, wall_seconds_prior=0, tier="4")` with `deadline_cap_s(at_epoch: float) -> int` and `to_json() -> dict`; `load_channel_config(run_dir: Path) -> ChannelConfig | None`; `write_channel_config(store: RunStore, config: ChannelConfig) -> None`; `parse_max_asks(raw: str, *, source: str) -> int`; `epoch_of(value: Any) -> float | None`; `effective_deadline_s(ask: Mapping, config: ChannelConfig | None) -> int`; `write_deadline_cap_s(config: ChannelConfig | None, now_epoch: float) -> int`.
- Produces: routing flag `--routing-max-asks N` and env `SUBAGENT_MODEL_ROUTING_MAX_ASKS` (1..50); `tier4_prompt_suffix(mailbox_dir, dispatch_id, *, max_asks, deadline_cap_s=MAX_DEADLINE_S)`; `Mailbox.enforce_cap() -> list[str]`; `Mailbox.deadline_remaining_s(ask, *, now=None, deadline_s=None)`.
- `resume.json` gains `maxAsks`; `channel.json` is listed by `RunStore.artifact_summary()`.

- [x] **Step 1: Write the failing tests** (`tests/test_channel_config.py`)

```python
"""channel.json, D1 derived deadlines, and the per-dispatch ask cap (plan Task 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.shim_test_support import ShimSandbox  # noqa: E402

from model_routing.channel import (  # noqa: E402
    ChannelConfig,
    effective_deadline_s,
    load_channel_config,
    write_channel_config,
)
from model_routing.mailbox import MailboxCapError  # noqa: E402
from model_routing.run_store import RunStore  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000c2"

_ASKING_PROVIDER = """\
#!/usr/bin/env python3
import os, sys
sys.path.insert(0, {runtime!r})
from pathlib import Path
from model_routing.mailbox import Mailbox
from model_routing.run_store import state_root
# The channel variable arrives in Task 11; the fallback keeps this fake valid before and after Task 12.
dispatch_id = os.environ.get("SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID") or os.environ["SUBAGENT_MODEL_ROUTING_DISPATCH_ID"]
box = Mailbox(state_root(dict(os.environ)) / "runs" / dispatch_id, dispatch_id, max_asks=99)
for index in range(int(os.environ.get("FAKE_ASKS", "1"))):
    box.write_ask(blocked_on="choice", question=f"q{{index}}",
                  options=[{{"id": "a", "text": "x"}}, {{"id": "b", "text": "y"}}],
                  default="a", deadline_s=3600)
sys.exit(75)
"""


class ChannelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.state = Path(self._temp.name)
        self.store = RunStore(self.state, DISPATCH_ID)
        self.store.path.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_round_trip_and_strict_bounds(self) -> None:
        config = ChannelConfig(
            DISPATCH_ID, max_asks=2, timeout_seconds=600.0, attempt_started_epoch=100.0
        )
        write_channel_config(self.store, config)
        self.assertEqual(config, load_channel_config(self.store.path))
        for bad in ({"maxAsks": 0}, {"maxAsks": 51}, {"tier": "3"}, {"timeoutSeconds": 0}):
            doc = {**config.to_json(), **bad}
            self.store.write_json("channel.json", doc)
            self.assertIsNone(load_channel_config(self.store.path), bad)

    def test_d1_cap_clamps_the_effective_deadline(self) -> None:
        started = time.time()
        config = ChannelConfig(DISPATCH_ID, timeout_seconds=600.0, attempt_started_epoch=started)
        created = (
            datetime.fromtimestamp(started, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        )
        ask = {"deadline_s": 3600, "created_at": created}
        self.assertEqual(90, effective_deadline_s(ask, config))  # 15% of 600 s
        self.assertEqual(3600, effective_deadline_s(ask, None))
        late = (
            datetime.fromtimestamp(started, tz=timezone.utc) + timedelta(seconds=400)
        ).isoformat()
        self.assertEqual(30, effective_deadline_s({"deadline_s": 3600, "created_at": late}, config))

    def test_store_mailbox_reads_the_cap_from_channel_json(self) -> None:
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, max_asks=2))
        box = self.store.mailbox()
        for _ in range(2):
            box.write_ask(
                blocked_on="choice",
                question="q",
                options=[{"id": "a", "text": "x"}],
                default="a",
                deadline_s=60,
            )
        with self.assertRaises(MailboxCapError):
            box.write_ask(
                blocked_on="choice",
                question="q",
                options=[{"id": "a", "text": "x"}],
                default="a",
                deadline_s=60,
            )


class DispatchCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        target = self.sandbox.bin / "codex"
        target.write_text(_ASKING_PROVIDER.format(runtime=str(ROOT / "runtime")), encoding="utf-8")
        target.chmod(0o755)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _dispatch(self, *extra: str, **env: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/pitwall-agent-routing"),
                "dispatch",
                "codex",
                str(self.sandbox.prompt()),
                "--routing-ask-support",
                *extra,
            ],
            capture_output=True,
            env=self.sandbox.environment(
                SUBAGENT_MODEL_ROUTING_DISPATCH_ID=DISPATCH_ID, SHIM_TIMEOUT_SECS="600", **env
            ),
            check=False,
        )

    def _run_dir(self) -> Path:
        return self.sandbox.state / "subagent-model-routing" / "runs" / DISPATCH_ID

    def test_routing_flag_sets_cap_suffix_and_resume_record(self) -> None:
        result = self._dispatch("--routing-max-asks", "2")
        self.assertEqual(75, result.returncode, result.stderr)
        run_dir = self._run_dir()
        config = load_channel_config(run_dir)
        assert config is not None
        self.assertEqual(2, config.max_asks)
        self.assertEqual(600.0, config.timeout_seconds)
        prompt = (run_dir / "prompt.deliver.md").read_text(encoding="utf-8")
        self.assertIn("at most 2", prompt)
        self.assertIn("`deadline_s` is at most 90", prompt)
        self.assertEqual(
            2, json.loads((run_dir / "resume.json").read_text(encoding="utf-8"))["maxAsks"]
        )

    def test_env_sets_the_cap_and_bad_values_are_usage_errors(self) -> None:
        self.assertEqual(75, self._dispatch(SUBAGENT_MODEL_ROUTING_MAX_ASKS="3").returncode)
        config = load_channel_config(self._run_dir())
        assert config is not None
        self.assertEqual(3, config.max_asks)
        for bad in ("0", "51", "x"):
            with self.subTest(bad=bad):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts/pitwall-agent-routing"),
                        "dispatch",
                        "codex",
                        str(self.sandbox.prompt()),
                        f"--routing-max-asks={bad}",
                    ],
                    capture_output=True,
                    env=self.sandbox.environment(),
                    check=False,
                )
                self.assertEqual(64, result.returncode)
                self.assertIn(b"--routing-max-asks expects an integer 1..50", result.stderr)

    def test_pause_quarantines_tier4_asks_past_the_cap(self) -> None:
        result = self._dispatch("--routing-max-asks", "2", FAKE_ASKS="3")
        self.assertEqual(75, result.returncode, result.stderr)
        run_dir = self._run_dir()
        pause = json.loads((run_dir / "pause.json").read_text(encoding="utf-8"))
        self.assertEqual(["0001", "0002"], pause["pendingAskIds"])
        dead = [path.name for path in (run_dir / "mailbox" / "dead-letter").iterdir()]
        self.assertEqual(["asks_0003.json"], dead)


if __name__ == "__main__":
    unittest.main()
```

Add one broker case to `tests/test_channel_broker.py`: with `channel.json` written for the run (`timeout_seconds=600`, `attempt_started_epoch=time.time()`), `POST /asks` with `deadline_s: 3600` stores `deadline_s == 90`.

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_channel_config -v 2>&1 | tail -8`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_routing.channel'`.

- [x] **Step 3: Create `runtime/model_routing/channel.py`**

```python
"""Per-dispatch orchestrator-channel settings and services (standard library only).

``channel.json`` in a run directory records what the dispatcher decided for
the channel: the D1 ask cap (default 5, overridable per dispatch), the
attempt timeout that D1 deadlines derive from, and the delivery tier. The
broker, MCP server, CLI, and workflow scheduler all read this one record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from .errors import UsageError
from .mailbox import DEFAULT_MAX_ASKS, MAX_DEADLINE_S, derive_deadline_s
from .run_store import RunStore


CHANNEL_DISPATCH_ENV = "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID"
CHANNEL_STATE_ROOT_ENV = "SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT"
CHANNEL_ATTEMPT_ENV = "SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT"
CHANNEL_ARTIFACT = "channel.json"
MAX_ASKS_CEILING = 50
TIERS = ("1", "4")


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    dispatch_id: str
    max_asks: int = DEFAULT_MAX_ASKS
    max_open_asks: int = 1
    timeout_seconds: float = 1140.0
    attempt_started_epoch: float = 0.0
    wall_seconds_prior: int = 0
    tier: str = "4"

    def deadline_cap_s(self, at_epoch: float) -> int:
        """D1: ``min(3600, 15% of the remaining attempt timeout)`` at *at_epoch*."""
        return derive_deadline_s(self.attempt_started_epoch + self.timeout_seconds - at_epoch)

    def to_json(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "dispatchId": self.dispatch_id,
            "maxAsks": self.max_asks,
            "maxOpenAsks": self.max_open_asks,
            "timeoutSeconds": self.timeout_seconds,
            "attemptStartedEpoch": self.attempt_started_epoch,
            "wallSecondsPrior": self.wall_seconds_prior,
            "tier": self.tier,
        }


def _config_from_json(doc: Any) -> ChannelConfig | None:
    if not isinstance(doc, dict) or doc.get("schemaVersion") != 1:
        return None
    try:
        config = ChannelConfig(
            dispatch_id=str(doc["dispatchId"]),
            max_asks=int(doc["maxAsks"]),
            max_open_asks=int(doc["maxOpenAsks"]),
            timeout_seconds=float(doc["timeoutSeconds"]),
            attempt_started_epoch=float(doc["attemptStartedEpoch"]),
            wall_seconds_prior=int(doc["wallSecondsPrior"]),
            tier=str(doc["tier"]),
        )
    except KeyError, TypeError, ValueError:
        return None
    if not 1 <= config.max_asks <= MAX_ASKS_CEILING or config.max_open_asks < 1:
        return None
    if config.timeout_seconds <= 0 or config.wall_seconds_prior < 0 or config.tier not in TIERS:
        return None
    return config


def load_channel_config(run_dir: Path) -> ChannelConfig | None:
    """The run's channel record, or None when absent or out of bounds."""
    try:
        doc = json.loads((run_dir / CHANNEL_ARTIFACT).read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return _config_from_json(doc)


def write_channel_config(store: RunStore, config: ChannelConfig) -> None:
    store.write_json(CHANNEL_ARTIFACT, config.to_json())


def parse_max_asks(raw: str, *, source: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= MAX_ASKS_CEILING:
        raise UsageError(f"{source} expects an integer 1..{MAX_ASKS_CEILING}")
    return value


def epoch_of(value: Any) -> float | None:
    """Parse a mailbox ISO-8601 timestamp (``Z`` or offset) into epoch seconds."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def effective_deadline_s(ask: Mapping[str, Any], config: ChannelConfig | None) -> int:
    """The ask's own ``deadline_s`` clamped by the D1 cap at its creation time."""
    requested = int(ask.get("deadline_s", MAX_DEADLINE_S))
    created = epoch_of(ask.get("created_at"))
    if config is None or created is None:
        return requested
    return min(requested, config.deadline_cap_s(created))


def write_deadline_cap_s(config: ChannelConfig | None, now_epoch: float) -> int:
    """The largest deadline a writer may accept now (``MAX_DEADLINE_S`` without a record)."""
    return MAX_DEADLINE_S if config is None else config.deadline_cap_s(now_epoch)
```

- [x] **Step 4: Wire the cap and deadline into dispatch, run store, mailbox, and broker**

1. `RoutingOptions` gains `max_asks: int | None = None`. In `_strip_routing_args`, add `("--routing-max-asks", "max_asks", None)` to the flag tuple and convert before `setattr`: `if field == "max_asks": value = parse_max_asks(value, source=flag)`. In `dispatch_legacy`'s opening `try` (after `_strip_routing_args`), add: `if routing.max_asks is None and env.get("SUBAGENT_MODEL_ROUTING_MAX_ASKS"): routing.max_asks = parse_max_asks(env["SUBAGENT_MODEL_ROUTING_MAX_ASKS"], source="SUBAGENT_MODEL_ROUTING_MAX_ASKS")`. Both raise `UsageError`, which the existing handler turns into exit 64.
2. Move the `SHIM_TIMEOUT_SECS` parse (the `try: timeout_seconds = _parse_timeout(...)` block just before `run_process`) above the ask-support block, unchanged. Compute `max_asks = routing.max_asks or (previous.max_asks if (previous := load_channel_config(store.path)) else DEFAULT_MAX_ASKS)`.
3. `tier4_prompt_suffix` takes `deadline_cap_s: int = MAX_DEADLINE_S` and renders the rule as ``"`deadline_s` is at most __DEADLINE_CAP__; if the deadline passes, your stated default is applied by the orchestrator when the run is resumed. The run stays paused until then."`` (the phrases asserted by `tests/test_runs_resume.py:249-251` stay). The call passes `max_asks=max_asks, deadline_cap_s=derive_deadline_s(timeout_seconds)`.
4. `resume.json` gains `"maxAsks": max_asks`. `resume_legacy` appends `f"--routing-max-asks={resume_doc['maxAsks']}"` when that value is an int.
5. Right after `lifecycle.transition("running")`, when `ask_support`: `write_channel_config(store, ChannelConfig(dispatch_id, max_asks=max_asks, timeout_seconds=timeout_seconds, attempt_started_epoch=time.time(), wall_seconds_prior=previous.wall_seconds_prior if previous else 0))`.
6. Before the paused classification: `if state == "failed" and process_result.exit_code == PAUSED_EXIT_CODE and (store.path / "mailbox").is_dir(): store.mailbox().enforce_cap()`.
7. `RunStore.mailbox(max_asks=None)` reads the cap from `load_channel_config(self.path)` (imported inside the method to avoid an import cycle), falling back to `DEFAULT_MAX_ASKS`. `artifact_summary()` lists `channel.json`.
8. `Mailbox.enforce_cap()` quarantines every `asks/NNNN.json` numbered above `max_asks` with reason `ask cap exceeded (max N per run)` and returns their ids. `deadline_remaining_s` uses `deadline_s` when given, else the ask's own.
9. `apply_channel_ask` clamps the payload deadline: `min(deadline, write_deadline_cap_s(load_channel_config(store.path), time.time()))` when the payload value is a non-bool int.

- [x] **Step 5: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_channel_config tests.test_channel_broker tests.test_runs_resume tests.test_pause_contract tests.test_mailbox tests.test_mailbox_concurrency 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 6: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/channel.py runtime/model_routing/dispatch.py runtime/model_routing/run_store.py \
  runtime/model_routing/mailbox.py runtime/model_routing/pitwall_sync.py tests/test_channel_config.py tests/test_channel_broker.py
git commit -s -m "feat(agent-routing): channel.json with D1 derived deadlines and a per-dispatch ask cap"
```

## Task 3: Default and policy answer sources; deadline expiry; resume applies defaults

**Files:**
- Modify: `runtime/model_routing/mailbox.py` (`ANSWERED_BY` :47, `validate_answer` :140)
- Modify: `runtime/model_routing/channel.py` (add `resolve_with_default`, `expire_overdue`)
- Modify: `runtime/model_routing/dispatch.py` (`build_resume_prompt` :559, `resume_legacy` :1048)
- Modify: `runtime/model_routing/pitwall_sync.py` (`read_channel_inbox` :387 moves to `channel.py`; the GET `/inbox` handler at :473 keeps mapping an unknown run to 404)
- Modify: `tests/test_mailbox.py:117` (the bare-`policy` case now expects rejection for two reasons; keep it and add the new source cases), `tests/test_runs_resume.py`, `tests/test_inbox_cli.py`
- Create: `tests/test_channel_expiry.py`

**Interfaces:**
- Produces: `mailbox.ANSWERED_BY = frozenset({"operator", "orchestrator", "default"})`, `mailbox.POLICY_PREFIX = "policy:"`, `mailbox.answer_source_valid(answered_by: str) -> bool`.
- Produces: `channel.resolve_with_default(store: RunStore, ask: Mapping, *, emitter: EventEmitter | None) -> dict` and `channel.expire_overdue(store: RunStore, *, emitter: EventEmitter | None, now_epoch: float | None = None) -> list[dict]`.
- `read_channel_inbox(env, dispatch_id)` moves to `channel.py` and raises `FileNotFoundError` for an unknown run. `pitwall_sync.read_channel_inbox` stays as a thin wrapper that converts that to `ChannelError(status=404)`, so `cli.py:41` and the broker keep their imports. The MCP server (Task 15) imports the `channel.py` version and so never loads the receiver's HTTP modules.
- Inbox ask entries gain `expired: bool`, and `deadlineRemainingS` now uses the effective deadline.

- [x] **Step 1: Write the failing tests** (`tests/test_channel_expiry.py`)

```python
"""Default/policy answer sources, deadline expiry, and resume-time defaults (plan Task 3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.channel import expire_overdue  # noqa: E402
from model_routing.dispatch import build_resume_prompt  # noqa: E402
from model_routing.events import EventEmitter  # noqa: E402
from model_routing.mailbox import Mailbox, MailboxError, validate_answer  # noqa: E402
from model_routing.run_store import RunStore  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000c3"
OPTIONS = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]


def _age_ask(run_dir: Path, ask_id: str, *, seconds: int) -> None:
    path = run_dir / "mailbox" / "asks" / f"{ask_id}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["created_at"] = (
        (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(doc), encoding="utf-8")


class AnswerSourceTests(unittest.TestCase):
    def _answer(self, answered_by: str, choice: str = "a") -> dict:
        return {
            "version": 1,
            "ask_id": "0001",
            "answered_by": answered_by,
            "choice": choice,
            "answered_at": "2026-09-10T00:00:00Z",
        }

    def test_sources(self) -> None:
        for good in (
            "operator",
            "orchestrator",
            "default",
            "policy:glm-5.3-flash",
            "policy:zai/glm-5.3",
        ):
            validate_answer(self._answer(good), options=["a", "b"])
        for bad in ("policy", "policy:", "policy: spaced", "cron", ""):
            with self.subTest(bad=bad), self.assertRaises(MailboxError):
                validate_answer(self._answer(bad), options=["a", "b"])

    def test_policy_may_not_abort(self) -> None:
        with self.assertRaises(MailboxError):
            validate_answer(self._answer("policy:m", choice="abort"), options=["a", "b"])


class ExpiryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self._temp.name), DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self.emitter = EventEmitter(self.store, provider="test", model="m")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _ask(self, deadline_s: int, default: str = "b") -> str:
        return str(
            self.store.mailbox().write_ask(
                blocked_on="choice",
                question="q",
                options=OPTIONS,
                default=default,
                deadline_s=deadline_s,
            )["ask_id"]
        )

    def test_only_overdue_asks_get_their_default_once(self) -> None:
        overdue, fresh = self._ask(60), self._ask(600)
        _age_ask(self.store.path, overdue, seconds=120)
        applied = expire_overdue(self.store, emitter=self.emitter)
        self.assertEqual([overdue], [a["ask_id"] for a in applied])
        answer = self.store.mailbox().get_answer(overdue)
        assert answer is not None
        self.assertEqual(("default", "b"), (answer["answered_by"], answer["choice"]))
        self.assertIsNone(self.store.mailbox().get_answer(fresh))
        self.assertEqual([], expire_overdue(self.store, emitter=self.emitter))
        events = [
            json.loads(line)
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        resolved = [e for e in events if e["event"] == "ask.resolved"]
        self.assertEqual(
            [{"askId": overdue, "resolvedBy": "default"}], [e["data"] for e in resolved]
        )

    def test_an_operator_answer_beats_expiry(self) -> None:
        ask_id = self._ask(60)
        _age_ask(self.store.path, ask_id, seconds=120)
        self.store.mailbox().write_answer(ask_id, choice="a", answered_by="operator")
        self.assertEqual([], expire_overdue(self.store, emitter=self.emitter))

    def test_resume_prompt_is_deterministic_and_marks_abort(self) -> None:
        ask = {
            "ask_id": "0001",
            "blocked_on": "choice",
            "question": "q",
            "default": "abort",
            "context": {"options": OPTIONS},
        }
        answer = {"answered_by": "default", "answered_at": "t", "choice": "abort"}
        first = build_resume_prompt(b"original\n", [(ask, answer)], 2)
        self.assertEqual(first, build_resume_prompt(b"original\n", [(ask, answer)], 2))
        self.assertIn(b"The orchestrator chose to abort", first)


if __name__ == "__main__":
    unittest.main()
```

Add this method to `RunsResumeTests` in `tests/test_runs_resume.py`, with a module-level copy of `_age_ask` (it reuses that class's `_sandbox`, `_dispatch`, `_resume`, and `_run_dir` helpers, so it lives beside them instead of subclassing them into a second module):

```python
    def test_resume_applies_expired_defaults_without_an_operator(self) -> None:
        sandbox = self._sandbox()
        self.assertEqual(75, self._dispatch(sandbox, sandbox.prompt(), "ask").returncode)
        _age_ask(self._run_dir(sandbox), "0001", seconds=4000)
        resumed = self._resume(sandbox, "done")
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        self.assertIn(b"ask 0001 expired; applied its default 'b'", resumed.stderr)
        rebuilt = (self._run_dir(sandbox) / "prompt.deliver.md").read_text(encoding="utf-8")
        self.assertIn("## A (by default at", rebuilt)
        finished = [row for row in sandbox.ledger_records() if row.get("event") == "finished"]
        self.assertEqual(["0001:default"], finished[-1]["askResolutions"])
```

Add one inbox case to `tests/test_inbox_cli.py`: an ask aged past its deadline appears with `"expired": true` in `--json` and `expired` in the table's REMAINING column.

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_channel_expiry -v 2>&1 | tail -8`
Expected: FAIL with `ImportError: cannot import name 'expire_overdue'`.

- [x] **Step 3: Implement the answer sources in `mailbox.py`**

```python
ANSWERED_BY = frozenset({"operator", "orchestrator", "default"})
POLICY_PREFIX = "policy:"
_POLICY_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")


def answer_source_valid(answered_by: str) -> bool:
    """operator | orchestrator | default | policy:<model> (D3 provenance)."""
    if answered_by in ANSWERED_BY:
        return True
    model = answered_by.removeprefix(POLICY_PREFIX)
    return answered_by.startswith(POLICY_PREFIX) and bool(_POLICY_MODEL.fullmatch(model))
```

In `validate_answer`, replace the `ANSWERED_BY` membership test with `answer_source_valid`, keep the option check, and add: `if answered_by.startswith(POLICY_PREFIX) and choice == "abort": raise MailboxError("a policy answer must select one of the ask's options")`. Export `POLICY_PREFIX` and `answer_source_valid` in `__all__`.

- [x] **Step 4: Implement expiry in `channel.py`**

```python
import time

from .events import EventEmitter
from .mailbox import MailboxError


def resolve_with_default(
    store: RunStore, ask: Mapping[str, Any], *, emitter: EventEmitter | None
) -> dict[str, Any]:
    """Apply an ask's stated default; if another answer won the race, return that one."""
    box = store.mailbox()
    ask_id = str(ask["ask_id"])
    try:
        answer = box.write_answer(
            ask_id,
            choice=str(ask["default"]),
            answered_by="default",
            note="deadline expired; the ask's stated default was applied",
        )
    except MailboxError:
        existing = box.get_answer(ask_id)
        if existing is None:
            raise
        return existing
    if emitter is not None:
        emitter.emit_ask_resolved(ask_id, "default")
    return answer


def expire_overdue(
    store: RunStore, *, emitter: EventEmitter | None, now_epoch: float | None = None
) -> list[dict[str, Any]]:
    """Resolve every pending ask whose effective deadline has passed with its default."""
    moment = time.time() if now_epoch is None else now_epoch
    config = load_channel_config(store.path)
    applied: list[dict[str, Any]] = []
    for ask in store.mailbox().pending_asks():
        created = epoch_of(ask.get("created_at"))
        if created is None or created + effective_deadline_s(ask, config) > moment:
            continue
        answer = resolve_with_default(store, ask, emitter=emitter)
        if answer.get("answered_by") == "default":
            applied.append(answer)
    return applied
```

- [x] **Step 5: Apply defaults on resume, mark abort, and flag expiry in the inbox**

- `resume_legacy`: before `pending = box.pending_asks()`, build `emitter = EventEmitter(store, provider=provider, model=str(run_doc.get("model") or "unknown"), callback=HookRunner(env))` and, for each answer from `expire_overdue(store, emitter=emitter)`, print `pitwall-agent-routing: ask {id} expired; applied its default {choice!r}` to stderr.
- `build_resume_prompt`: after the `Choice:` line, when the choice is `abort`, append `The orchestrator chose to abort: stop now, report what you completed, and exit 0.`
- Move `read_channel_inbox` into `channel.py` as described above. In it, load the run's `ChannelConfig` once per run, compute `remaining = box.deadline_remaining_s(ask, deadline_s=effective_deadline_s(ask, config))`, and add `"expired": remaining <= 0` to each ask entry.

- [x] **Step 6: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_channel_expiry tests.test_mailbox tests.test_inbox_cli tests.test_runs_resume tests.test_channel_broker 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 7: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/mailbox.py runtime/model_routing/channel.py runtime/model_routing/dispatch.py \
  runtime/model_routing/pitwall_sync.py tests/test_channel_expiry.py tests/test_mailbox.py tests/test_inbox_cli.py \
  tests/test_runs_resume.py
git commit -s -m "feat(agent-routing): default and policy answer sources; expired asks resolve to their default"
```

## Task 4: §9.3 ledger fields on the finished row

**Files:**
- Modify: `runtime/model_routing/channel.py` (add `ledger_fields`)
- Modify: `runtime/model_routing/dispatch.py` (`_ledger_record` :67 — replace `ask_resolutions` with `channel`; `_finish_paused` :448 — accumulate wall time; finished path ~:1015)
- Create: `tests/test_channel_ledger.py`

**Interfaces:**
- Produces: `channel.ledger_fields(store: RunStore, *, wall_seconds_total: int) -> dict[str, Any]` returning `askCount`, `askRatePerHour`, `askResolutions` (`"<ask_id>:<answered_by>"` in ask order), `steerCount`, `steerAckLatencyS` (seconds from each steer's `created_at` to its first ack, steer order).
- `_ledger_record(..., channel: Mapping[str, Any] | None = None)` merges those keys into the row. The finished row carries them whenever the run has a `mailbox/` directory, not only after a pause. Runs without a mailbox keep today's row shape.
- `_finish_paused` rewrites `channel.json` with `wall_seconds_prior += wall_seconds`, so `askRatePerHour` covers every attempt.

- [x] **Step 1: Write the failing tests** (`tests/test_channel_ledger.py`)

```python
"""§9.3 ledger fields: ask count/rate/resolutions, steer count/ack latency (plan Task 4)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.shim_test_support import ShimSandbox  # noqa: E402

from model_routing.channel import load_channel_config  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000c4"

# Simulates a tier-1 session: the ask is answered mid-run and a steer is acked.
_TIER1_PROVIDER = """\
#!/usr/bin/env python3
import os, sys
sys.path.insert(0, {runtime!r})
from model_routing.mailbox import Mailbox
from model_routing.run_store import state_root
dispatch_id = os.environ.get("SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID") or os.environ["SUBAGENT_MODEL_ROUTING_DISPATCH_ID"]
box = Mailbox(state_root(dict(os.environ)) / "runs" / dispatch_id, dispatch_id)
if os.environ.get("FAKE_MODE") == "channel":
    box.write_ask(blocked_on="naming", question="suffix?", options=[{{"id": "a", "text": "x"}}],
                  default="a", deadline_s=60)
    box.write_answer("0001", choice="a", answered_by="orchestrator")
    steer = box.write_steer(kind="scope", message="narrow")
    box.write_ack(steer["steer_id"])
print("done", flush=True)
"""


class LedgerFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        target = self.sandbox.bin / "codex"
        target.write_text(_TIER1_PROVIDER.format(runtime=str(ROOT / "runtime")), encoding="utf-8")
        target.chmod(0o755)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _dispatch(self, mode: str, *extra: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/pitwall-agent-routing"),
                "dispatch",
                "codex",
                str(self.sandbox.prompt()),
                *extra,
            ],
            capture_output=True,
            env=self.sandbox.environment(
                SUBAGENT_MODEL_ROUTING_DISPATCH_ID=DISPATCH_ID, FAKE_MODE=mode
            ),
            check=False,
        )

    def _finished(self) -> dict:
        rows = [row for row in self.sandbox.ledger_records() if row.get("event") == "finished"]
        self.assertEqual(1, len(rows))
        return rows[0]

    def test_channel_run_records_ask_and_steer_aggregates(self) -> None:
        self.assertEqual(0, self._dispatch("channel", "--routing-ask-support").returncode)
        row = self._finished()
        self.assertEqual(1, row["askCount"])
        self.assertEqual(["0001:orchestrator"], row["askResolutions"])
        self.assertEqual(1, row["steerCount"])
        self.assertEqual(1, len(row["steerAckLatencyS"]))
        self.assertGreaterEqual(row["steerAckLatencyS"][0], 0)
        self.assertGreater(row["askRatePerHour"], 0)

    def test_run_without_a_mailbox_keeps_the_old_row_shape(self) -> None:
        self.assertEqual(0, self._dispatch("plain").returncode)
        row = self._finished()
        for key in (
            "askCount",
            "askRatePerHour",
            "askResolutions",
            "steerCount",
            "steerAckLatencyS",
        ):
            self.assertNotIn(key, row)


class WallAccumulationTests(unittest.TestCase):
    def test_pause_accumulates_prior_wall_seconds(self) -> None:
        from tests.test_runs_resume import DISPATCH_ID as RESUME_ID, _FAKE_PROVIDER

        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        target = sandbox.bin / "codex"
        target.write_text(_FAKE_PROVIDER.format(runtime=str(ROOT / "runtime")), encoding="utf-8")
        target.chmod(0o755)
        env = sandbox.environment(
            SUBAGENT_MODEL_ROUTING_DISPATCH_ID=RESUME_ID,
            SUBAGENT_MODEL_ROUTING_ASK_SUPPORT="1",
            FAKE_RESUME_MODE="ask",
        )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/pitwall-agent-routing"),
                "dispatch",
                "codex",
                str(sandbox.prompt()),
            ],
            capture_output=True,
            env=env,
            check=False,
        )
        run_dir = sandbox.state / "subagent-model-routing" / "runs" / RESUME_ID
        paused = [row for row in sandbox.ledger_records() if row.get("event") == "paused"][0]
        config = load_channel_config(run_dir)
        assert config is not None
        self.assertEqual(paused["wall_s"], config.wall_seconds_prior)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_channel_ledger -v 2>&1 | tail -8`
Expected: FAIL with `KeyError: 'askCount'` (the finished row has no channel fields yet).

- [x] **Step 3: Implement `ledger_fields` in `channel.py`**

```python
def ledger_fields(store: RunStore, *, wall_seconds_total: int) -> dict[str, Any]:
    """§9.3 aggregates for the finished ledger row."""
    box = store.mailbox()
    asks = box.asks()
    answers = {answer["ask_id"]: answer for answer in box.answers()}
    first_ack: dict[str, float] = {}
    for ack in box.acks():
        acked = epoch_of(ack.get("acked_at"))
        if acked is not None:
            first_ack[ack["steer_id"]] = min(acked, first_ack.get(ack["steer_id"], acked))
    steers = box.steers()
    latencies: list[int] = []
    for steer in steers:
        created = epoch_of(steer.get("created_at"))
        if created is not None and steer["steer_id"] in first_ack:
            latencies.append(max(0, int(first_ack[steer["steer_id"]] - created)))
    return {
        "askCount": len(asks),
        "askRatePerHour": round(len(asks) * 3600 / max(wall_seconds_total, 1), 3),
        "askResolutions": [
            f"{ask['ask_id']}:{answers[ask['ask_id']]['answered_by']}"
            for ask in asks
            if ask["ask_id"] in answers
        ],
        "steerCount": len(steers),
        "steerAckLatencyS": latencies,
    }
```

- [x] **Step 4: Emit the fields from `dispatch.py`**

- `_ledger_record`: replace the `ask_resolutions` parameter with `channel: Mapping[str, Any] | None = None`; after the base record is built, `if channel: record.update(channel)`.
- Finished path: replace the `if store.artifact("pause.json").exists():` block with:

```python
        channel_fields: dict[str, Any] | None = None
        if (store.path / "mailbox").is_dir():
            config = load_channel_config(store.path)
            prior = config.wall_seconds_prior if config is not None else 0
            channel_fields = ledger_fields(store, wall_seconds_total=prior + wall_seconds)
```

  and pass `channel=channel_fields` to the finished `_ledger_record` call.
- `_finish_paused`: after `lifecycle.transition("paused")`, `if (config := load_channel_config(store.path)) is not None: write_channel_config(store, replace(config, wall_seconds_prior=config.wall_seconds_prior + wall_seconds))` (`dataclasses.replace` is already imported).

- [x] **Step 5: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_channel_ledger tests.test_runs_resume tests.test_pause_contract tests.test_shim_receipt tests.test_dispatch_contract 2>&1 | tail -3`
Expected: `OK` (`test_runs_resume` still sees `askResolutions == ["0001:operator"]`).

- [x] **Step 6: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/channel.py runtime/model_routing/dispatch.py tests/test_channel_ledger.py
git commit -s -m "feat(agent-routing): ask rate, resolutions, steer count and ack latency on the finished ledger row"
```

## Task 5: Operator surfaces — `answer` CLI, D2 pointers, live `runs diff`

**Files:**
- Modify: `runtime/model_routing/channel.py` (add `answer_ask`)
- Modify: `runtime/model_routing/cli.py` (`build_parser` :1044 — new `answer` subcommand; `main` :1173; `_inbox` :224; `_runs_diff` :160)
- Modify: `runtime/model_routing/channel.py` (`read_channel_inbox` ask entries)
- Create: `tests/test_answer_cli.py`

**Interfaces:**
- Produces: `channel.answer_ask(env: Mapping[str, str], dispatch_id: str, ask_id: str, *, choice: str, answered_by: str, note: str | None, provider: str) -> dict[str, Any]`. It resolves the run by id or unique prefix (`find_run`), writes the answer, and emits `ask.resolved` with the hook runner attached. It raises `FileNotFoundError` for an unknown run and `MailboxError` for an invalid or duplicate answer.
- CLI: `pitwall-agent-routing answer <dispatch-id> <ask-id> <choice> [--note TEXT] [--by operator|orchestrator] [--json]`. Exit 0 on success, 1 on any refusal, with the reason on stderr.
- Inbox ask entries gain `filesTouched`, `options`, `default`, `defaultRationale`, and `contextCommand` (`pitwall-agent-routing runs diff <dispatch-id>`, D2). The table prints one `context:` line per dispatch that has asks.
- `runs diff` refreshes the snapshot with `workspace.capture_changes` when the run is `running` or `paused` in an isolated worktree. A shared-workspace run prints `run <id> uses the shared workspace; inspect the files named by its asks in place` and exits 1.

- [x] **Step 1: Write the failing tests** (`tests/test_answer_cli.py`)

```python
"""Operator `answer` verb, D2 context pointers, and live `runs diff` (plan Task 5)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.run_store import RunStore  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000c5"
CLI = [sys.executable, str(ROOT / "scripts/pitwall-agent-routing")]


class AnswerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.env = {
            "HOME": str(root / "home"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "PATH": "/usr/bin:/bin",
            "SUBAGENT_MODEL_ROUTING_LEDGER": str(root / "ledger.jsonl"),
        }
        self.store = RunStore(root / "state" / "subagent-model-routing", DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self.store.mailbox().write_ask(
            blocked_on="file-selection",
            question="Which migration first?",
            options=[{"id": "a", "text": "0031"}, {"id": "b", "text": "0032"}],
            default="b",
            deadline_s=600,
            files_touched=["db/migrations/0032_rename.sql"],
            default_rationale="0031 is additive",
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*CLI, *args], capture_output=True, text=True, env=self.env, check=False
        )

    def test_answer_writes_once_and_emits_ask_resolved(self) -> None:
        result = self._cli("answer", DISPATCH_ID[:8], "0001", "a", "--note", "0031 first")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"answered {DISPATCH_ID}/0001: a (by operator)", result.stdout)
        answer = self.store.mailbox().get_answer("0001")
        assert answer is not None
        self.assertEqual(
            ("operator", "a", "0031 first"),
            (answer["answered_by"], answer["choice"], answer["note"]),
        )
        events = [
            json.loads(line)
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [{"askId": "0001", "resolvedBy": "operator"}],
            [e["data"] for e in events if e["event"] == "ask.resolved"],
        )
        again = self._cli("answer", DISPATCH_ID, "0001", "b")
        self.assertEqual(1, again.returncode)
        self.assertIn("already has an answer", again.stderr)

    def test_refusals(self) -> None:
        self.assertIn("not found", self._cli("answer", "ffffffff", "0001", "a").stderr)
        bad = self._cli("answer", DISPATCH_ID, "0001", "zzz")
        self.assertEqual(1, bad.returncode)
        self.assertIsNone(self.store.mailbox().get_answer("0001"))
        by = self._cli("answer", DISPATCH_ID, "0001", "a", "--by", "orchestrator", "--json")
        self.assertEqual("orchestrator", json.loads(by.stdout)["answered_by"])

    def test_inbox_carries_d2_pointers(self) -> None:
        payload = json.loads(self._cli("inbox", "--json").stdout)
        ask = payload["asks"][0]
        self.assertEqual(["db/migrations/0032_rename.sql"], ask["filesTouched"])
        self.assertEqual(["a", "b"], [o["id"] for o in ask["options"]])
        self.assertEqual("b", ask["default"])
        self.assertEqual("0031 is additive", ask["defaultRationale"])
        self.assertEqual(f"pitwall-agent-routing runs diff {DISPATCH_ID}", ask["contextCommand"])
        self.assertIn(
            f"context: pitwall-agent-routing runs diff {DISPATCH_ID}", self._cli("inbox").stdout
        )

    def test_runs_diff_explains_shared_workspace_runs(self) -> None:
        result = self._cli("runs", "diff", DISPATCH_ID)
        self.assertEqual(1, result.returncode)
        self.assertIn("uses the shared workspace", result.stderr)


if __name__ == "__main__":
    unittest.main()
```

Add a live-diff case to `tests/test_workspace.py` beside its existing isolated-worktree fixtures: after an isolated dispatch pauses (fake harness writes `notes.txt`, an ask, exits 75), edit `notes.txt` in the retained worktree and assert `runs diff` prints the edited content.

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_answer_cli -v 2>&1 | tail -8`
Expected: FAIL with `invalid choice: 'answer'` from argparse.

- [x] **Step 3: Implement `channel.answer_ask`**

```python
from .hooks import HookRunner
from .run_store import find_run, state_root


def answer_ask(
    env: Mapping[str, str],
    dispatch_id: str,
    ask_id: str,
    *,
    choice: str,
    answered_by: str,
    note: str | None,
    provider: str,
) -> dict[str, Any]:
    """Record one answer and emit ``ask.resolved`` (the shared answer service)."""
    run_path = find_run(env, dispatch_id)
    store = RunStore(state_root(env), run_path.name)
    answer = store.mailbox().write_answer(ask_id, choice=choice, answered_by=answered_by, note=note)
    EventEmitter(
        store, provider=provider, model="channel", callback=HookRunner(env)
    ).emit_ask_resolved(ask_id, answered_by)
    return answer
```

- [x] **Step 4: Add the CLI verb, inbox pointers, and live diff**

```python
def _answer(
    dispatch_id: str,
    ask_id: str,
    choice: str,
    note: str | None,
    answered_by: str,
    json_output: bool,
) -> int:
    try:
        answer = answer_ask(
            os.environ,
            dispatch_id,
            ask_id,
            choice=choice,
            answered_by=answered_by,
            note=note,
            provider="operator",
        )
    except (FileNotFoundError, MailboxError) as exc:
        print(f"pitwall-agent-routing: {exc}", file=sys.stderr)
        return 1
    run_id = find_run(os.environ, dispatch_id).name
    if json_output:
        print(json.dumps(answer, indent=2, sort_keys=True))
    else:
        print(f"answered {run_id}/{ask_id}: {choice} (by {answered_by})")
    return 0
```

Parser: `answer` with positional `dispatch_id`, `ask_id`, `choice`; `--note`; `--by` (`choices=("operator", "orchestrator")`, default `operator`, `dest="answered_by"`); `--json` (`dest="json_output"`). `main` routes `args.command == "answer"` to `_answer`.

`read_channel_inbox` adds to each ask entry: `"filesTouched": ask["context"].get("files_touched", [])`, `"options": ask["context"].get("options", [])`, `"default": ask["default"]`, `"defaultRationale": ask.get("default_rationale")`, `"contextCommand": f"pitwall-agent-routing runs diff {path.name}"`. `_inbox` prints, after the rows, one `context: pitwall-agent-routing runs diff <dispatch>` line per distinct dispatch among the asks.

`_runs_diff`: read `run.json` state; when it is `running` or `paused` and `workspace.json` exists, call `capture_changes(os.environ, path.name)` inside `try/except WorkspaceError` (keep the last snapshot on failure). When there is no `workspace.json`, print the shared-workspace message and return 1.

- [x] **Step 5: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_answer_cli tests.test_inbox_cli tests.test_workspace tests.test_channel_broker 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 6: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/channel.py runtime/model_routing/cli.py tests/test_answer_cli.py tests/test_workspace.py
git commit -s -m "feat(agent-routing): answer verb, runs-diff context pointers, live diff for mid-run answerers"
```

## Task 6: §10 invariants — open-ask limit, "steering ignored", sequence gaps

**Files:**
- Modify: `runtime/model_routing/mailbox.py` (add `MailboxOpenAskError`, `write_ask(..., max_open=None)`, `sequence_gaps`, `summary` gains `sequenceGaps`)
- Modify: `runtime/model_routing/pitwall_sync.py` (`apply_channel_ask` open-ask refusal → 409), `runtime/model_routing/channel.py` (`read_channel_inbox` steer deadline/ignored and top-level `sequenceGaps`)
- Modify: `runtime/model_routing/cli.py` (`_inbox` REMAINING column for steers, gap warnings on stderr)
- Modify: `tests/test_inbox_cli.py:100` and `tests/test_channel_broker.py:324` — the exact empty-inbox payload becomes `{"asks": [], "steers": [], "sequenceGaps": []}`
- Create: `tests/test_channel_invariants.py`

**Interfaces:**
- Produces: `class MailboxOpenAskError(MailboxCapError)`; `Mailbox.write_ask(..., max_open: int | None = None)` refuses when `len(pending_asks()) >= max_open`. The default `None` keeps direct callers and tier-4 fakes unlimited (Decision 10).
- Produces: `Mailbox.sequence_gaps() -> dict[str, list[str]]` for `asks`, `steer`, `acks`: numbers missing between 1 and the highest present, excluding numbers explained by a `dead-letter/` file.
- Broker: `POST /asks` passes `max_open=config.max_open_asks` (1 without a record) and maps `MailboxOpenAskError` to HTTP 409 `ask_already_open`, before the existing 429 mapping.
- Inbox steer entries gain `deadlineRemainingS` and `ignored` (`requires_ack` and past its deadline with no ack). The payload gains top-level `sequenceGaps: [{"dispatchId", "box", "missing": [...]}]`.

- [x] **Step 1: Write the failing tests** (`tests/test_channel_invariants.py`)

```python
"""§10 protocol invariants (plan Task 6)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.mailbox import Mailbox, MailboxOpenAskError  # noqa: E402
from model_routing.channel import read_channel_inbox  # noqa: E402
from model_routing.pitwall_sync import ChannelError, apply_channel_ask  # noqa: E402
from model_routing.run_store import RunStore  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000c6"
OPTIONS = [{"id": "a", "text": "x"}]


class InvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.env = {"HOME": str(root), "XDG_STATE_HOME": str(root / "state")}
        self.store = RunStore(root / "state" / "subagent-model-routing", DISPATCH_ID)
        self.store.path.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _ask(self, **extra: object) -> dict:
        return self.store.mailbox().write_ask(
            blocked_on="choice", question="q", options=OPTIONS, default="a", deadline_s=600, **extra
        )

    def test_programmatic_writers_get_one_open_ask(self) -> None:
        self._ask(max_open=1)
        with self.assertRaises(MailboxOpenAskError):
            self._ask(max_open=1)
        self.store.mailbox().write_answer("0001", choice="a", answered_by="operator")
        self.assertEqual("0002", self._ask(max_open=1)["ask_id"])
        self.assertEqual("0003", self._ask()["ask_id"])  # direct callers stay unlimited

    def test_broker_refuses_a_second_open_ask_with_409(self) -> None:
        payload = {
            "delivery_id": "d1",
            "dispatch_id": DISPATCH_ID,
            "blocked_on": "choice",
            "question": "q",
            "options": OPTIONS,
            "default": "a",
            "deadline_s": 60,
        }
        apply_channel_ask(payload, json.dumps(payload).encode(), self.env)
        second = {**payload, "delivery_id": "d2"}
        with self.assertRaises(ChannelError) as caught:
            apply_channel_ask(second, json.dumps(second).encode(), self.env)
        self.assertEqual(409, caught.exception.status)
        self.assertEqual("ask_already_open", str(caught.exception))

    def test_unacked_steer_past_deadline_is_flagged_ignored(self) -> None:
        box = self.store.mailbox()
        steer = box.write_steer(kind="scope", message="narrow", deadline_s=60)
        path = box.root / "steer" / f"{steer['steer_id']}.json"
        doc = json.loads(path.read_text())
        doc["created_at"] = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        path.write_text(json.dumps(doc))
        entry = read_channel_inbox(self.env, DISPATCH_ID)["steers"][0]
        self.assertTrue(entry["ignored"])
        self.assertLess(entry["deadlineRemainingS"], 0)

    def test_sequence_gaps_are_logged_not_fatal(self) -> None:
        box = self.store.mailbox()
        box.write_steer(kind="note", message="one")
        (box.root / "steer" / "0003.json").write_text(
            json.dumps({**box.steers()[0], "steer_id": "0003"}), encoding="utf-8"
        )
        self.assertEqual({"steer": ["0002"]}, Mailbox(self.store.path, DISPATCH_ID).sequence_gaps())
        gaps = read_channel_inbox(self.env, None)["sequenceGaps"]
        self.assertEqual([{"dispatchId": DISPATCH_ID, "box": "steer", "missing": ["0002"]}], gaps)
        self.store.refresh_mailbox_summary()
        summary = json.loads(self.store.artifact("mailbox.json").read_text())
        self.assertEqual({"steer": ["0002"]}, summary["sequenceGaps"])


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_channel_invariants -v 2>&1 | tail -8`
Expected: FAIL with `ImportError: cannot import name 'MailboxOpenAskError'`.

- [x] **Step 3: Implement the mailbox pieces**

```python
class MailboxOpenAskError(MailboxCapError):
    """A programmatic writer tried to open a second ask while one is unresolved (§10)."""
```

In `write_ask`, before `_append`: `if max_open is not None and (open_ids := [a["ask_id"] for a in self.pending_asks()]) and len(open_ids) >= max_open: raise MailboxOpenAskError(f"ask {open_ids[0]} is still open; one open ask at a time")`.

```python
    def sequence_gaps(self) -> dict[str, list[str]]:
        """Missing sequence numbers per box (tolerated, crash-safe, but reported)."""
        gaps: dict[str, list[str]] = {}
        for box in ("asks", "steer", "acks"):
            present = {int(p.stem) for p in (self.root / box).glob("*.json") if p.stem.isdigit()}
            explained = {
                int(stem)
                for p in (self.root / "dead-letter").glob(f"{box}_*.json")
                if (stem := _dead_letter_parts(p.name)[0].removeprefix(f"{box}_")).isdigit()
            }
            if present:
                missing = [f"{n:04d}" for n in range(1, max(present)) if n not in present | explained]
                if missing:
                    gaps[box] = missing
        return gaps
```

`summary()` adds `"sequenceGaps": self.sequence_gaps()`.

- [x] **Step 4: Surface them in the broker, inbox JSON, and table**

- `apply_channel_ask` passes `max_open=config.max_open_asks if config else 1` and adds `except MailboxOpenAskError as exc: raise ChannelError("ask_already_open", status=409) from exc` ahead of the `MailboxCapError` clause.
- `read_channel_inbox` computes each steer's `deadlineRemainingS` from `created_at + deadline_s` and sets `ignored` when it is negative and the steer `requires_ack`. It appends `{"dispatchId", "box", "missing"}` rows from `box.sequence_gaps()` to a top-level `sequenceGaps` list.
- `_inbox` prints `ignored` in a steer's REMAINING column (otherwise its remaining time), and in table mode writes `warning: <dispatch> <box> sequence gap: <ids>` to stderr for each gap row. `--json` output stays on stdout only.
- Update the two exact empty-payload assertions to `{"asks": [], "steers": [], "sequenceGaps": []}`.

- [x] **Step 5: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_channel_invariants tests.test_inbox_cli tests.test_channel_broker tests.test_channel_run_store tests.test_mailbox_concurrency 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 6: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/mailbox.py runtime/model_routing/pitwall_sync.py runtime/model_routing/channel.py \
  runtime/model_routing/cli.py tests/test_channel_invariants.py tests/test_inbox_cli.py tests/test_channel_broker.py
git commit -s -m "feat(agent-routing): one open ask per writer, steering-ignored signal, logged sequence gaps"
```

## Task 7: Idempotent broker retries (§11 chaos: broker killed mid-answer)

**Files:**
- Modify: `runtime/model_routing/mailbox.py` (`validate_ask`/`validate_steer` accept optional `delivery_id`; `write_ask`/`write_steer` take `delivery_id: str | None = None`; add `find_by_delivery`)
- Modify: `runtime/model_routing/pitwall_sync.py` (`apply_channel_ask` :294, `apply_channel_answer` :331, `apply_channel_steer` :361)
- Create: `tests/test_channel_chaos.py`

**Interfaces:**
- Produces: `Mailbox.find_by_delivery(box: str, delivery_id: str) -> dict[str, Any] | None` for `asks` and `steer`. `delivery_id` is an optional non-empty string of at most 128 characters in both schemas.
- Broker behavior after a crash between "file written" and "delivery remembered": a retried `POST /asks` or `POST /steer` returns the existing id with `"idempotent": true`. A retried `POST /answers/{id}` whose `choice` and `answered_by` match the stored answer returns 200 `idempotent`. A retry that conflicts with the stored answer returns 409 `ask_already_answered` and quarantines nothing. Every idempotent success remembers the delivery id, so a further retry is a normal 409 replay.

- [x] **Step 1: Write the failing tests** (`tests/test_channel_chaos.py`)

```python
"""Broker crash windows stay idempotent (spec §11 chaos; plan Task 7)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.test_channel_broker import SECRET, _ask_payload, _signature  # noqa: E402

from model_routing.pitwall_sync import (  # noqa: E402
    ChannelError,
    apply_channel_answer,
    apply_channel_ask,
)
from model_routing.run_store import RunStore  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000c7"


def _start_receiver(env: dict[str, str]) -> tuple[str, subprocess.Popen[str]]:
    """Start a real receiver and return its base URL and process (so a test can SIGKILL it)."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts" / "pitwall-agent-routing"),
            "pitwall",
            "receiver",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    opener = request.build_opener(request.ProxyHandler({}))
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.5):
                return f"http://127.0.0.1:{port}", process
        except error.URLError, TimeoutError:
            time.sleep(0.05)
    process.kill()
    raise AssertionError("receiver did not become healthy")


class BrokerCrashTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.env = {
            "HOME": str(root),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "PATH": os.environ.get("PATH", ""),
            "PITWALL_AGENT_ROUTING_WEBHOOK_SECRET": SECRET,
        }
        self.store = RunStore(root / "state" / "subagent-model-routing", DISPATCH_ID)
        self.store.path.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_answer_written_before_the_crash_is_acknowledged_on_retry(self) -> None:
        self.store.mailbox().write_ask(
            blocked_on="choice",
            question="q",
            options=[{"id": "a", "text": "x"}],
            default="a",
            deadline_s=60,
        )
        self.store.mailbox().write_answer(
            "0001", choice="a", answered_by="operator"
        )  # broker died here
        payload = {
            "delivery_id": "ans-1",
            "dispatch_id": DISPATCH_ID,
            "choice": "a",
            "answered_by": "operator",
        }
        result = apply_channel_answer("0001", payload, json.dumps(payload).encode(), self.env)
        self.assertTrue(result.get("idempotent"))
        self.assertFalse(any((self.store.path / "mailbox" / "dead-letter").iterdir()))
        with self.assertRaises(ChannelError) as replay:
            apply_channel_answer("0001", payload, json.dumps(payload).encode(), self.env)
        self.assertEqual(409, replay.exception.status)

    def test_conflicting_retry_is_409_and_keeps_the_stored_answer(self) -> None:
        self.store.mailbox().write_ask(
            blocked_on="choice",
            question="q",
            options=[{"id": "a", "text": "x"}, {"id": "b", "text": "y"}],
            default="a",
            deadline_s=60,
        )
        self.store.mailbox().write_answer("0001", choice="a", answered_by="operator")
        payload = {
            "delivery_id": "ans-2",
            "dispatch_id": DISPATCH_ID,
            "choice": "b",
            "answered_by": "operator",
        }
        with self.assertRaises(ChannelError) as caught:
            apply_channel_answer("0001", payload, json.dumps(payload).encode(), self.env)
        self.assertEqual(
            (409, "ask_already_answered"), (caught.exception.status, str(caught.exception))
        )
        stored = self.store.mailbox().get_answer("0001")
        assert stored is not None
        self.assertEqual("a", stored["choice"])

    def test_ask_written_before_the_crash_is_not_duplicated(self) -> None:
        payload = _ask_payload("ask-1", DISPATCH_ID)
        self.store.mailbox().write_ask(
            blocked_on="choice",
            question="Proceed with the plan?",
            options=payload["options"],
            default="a",
            deadline_s=600,  # type: ignore[arg-type]
            delivery_id="ask-1",
        )
        result = apply_channel_ask(payload, json.dumps(payload).encode(), self.env)
        self.assertEqual(("0001", True), (result["askId"], result.get("idempotent")))
        self.assertEqual(1, len(self.store.mailbox().asks()))

    def test_mailbox_survives_a_killed_receiver_and_the_retry_is_a_replay(self) -> None:
        body = json.dumps(_ask_payload("ask-kill", DISPATCH_ID)).encode()
        opener = request.build_opener(request.ProxyHandler({}))

        def post(base: str) -> int:
            headers = {"Content-Type": "application/json", "X-Pitwall-Signature": _signature(body)}
            try:
                with opener.open(
                    request.Request(f"{base}/asks", data=body, headers=headers), timeout=5
                ) as reply:
                    return int(reply.status)
            except error.HTTPError as exc:
                return int(exc.code)

        base, process = _start_receiver(self.env)
        try:
            self.assertEqual(200, post(base))
        finally:
            process.kill()  # SIGKILL: no cleanup runs
            process.wait(timeout=10)
        base, process = _start_receiver(self.env)
        try:
            self.assertEqual(409, post(base))  # the delivery id survived the crash
        finally:
            process.terminate()
            process.wait(timeout=10)
        self.assertEqual(["0001"], [a["ask_id"] for a in self.store.mailbox().asks()])


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_channel_chaos -v 2>&1 | tail -8`
Expected: FAIL — the first test gets `ChannelError: invalid answer` (400, and the retry payload is quarantined), and the ask test fails with `TypeError: ... unexpected keyword argument 'delivery_id'`.

- [x] **Step 3: Implement delivery ids and the idempotent paths**

- `validate_ask` and `validate_steer`: if `delivery_id` is present, require a non-empty string of at most 128 characters.
- `write_ask`/`write_steer`: when `delivery_id` is given, store it in the document.
- `find_by_delivery(box, delivery_id)`: scan `asks()` or `steers()` for a matching `delivery_id`.
- `apply_channel_ask` and `apply_channel_steer`, inside `_DELIVERY_LOCK` after the replay check: `if (existing := box.find_by_delivery(...)) is not None: _remember_delivery(env, delivery_id); return {..., "idempotent": True}`. Otherwise write with `delivery_id=delivery_id`.
- `apply_channel_answer`: on `MailboxError`, look up `box.get_answer(ask_id)`. If it matches the payload's `choice` and `answered_by`, remember the delivery and return `{"status": f"recorded:answers/{ask_id}", "idempotent": True}` without re-emitting `ask.resolved`. If it differs, raise `ChannelError("ask_already_answered", status=409)` without quarantining. Only when no answer exists does the existing quarantine-and-400 path run.

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_channel_chaos tests.test_channel_broker tests.test_mailbox tests.test_channel_invariants 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/mailbox.py runtime/model_routing/pitwall_sync.py tests/test_channel_chaos.py
git commit -s -m "fix(agent-routing): broker retries after a crash are idempotent, never duplicated or quarantined"
```

## Task 8: Orphaned asks pause the run (§11 chaos: subagent killed mid-ask)

**Files:**
- Modify: `runtime/model_routing/dispatch.py` (pause classification ~:1000; `_finish_paused` records a reason)
- Modify: `tests/test_pause_contract.py`

**Interfaces:**
- Produces: `_finish_paused(..., reason: str)` with `reason ∈ {"question", "orphaned-ask"}`, written to `pause.json` as `"reason"` and to the `dispatch.paused` event data.
- Classification (Decision 12): exit 75 with a pending ask → paused (`question`), as today. Any other `failed` attempt of an ask-support dispatch that leaves a pending ask → paused (`orphaned-ask`). Timeouts, cancellations, and failures without a pending ask are unchanged.

- [x] **Step 1: Write the failing tests** (add to `tests/test_pause_contract.py`)

Extend `_FAKE_PROVIDER` (`tests/test_pause_contract.py:19`): read the dispatch id as `env.get("SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID") or env["SUBAGENT_MODEL_ROUTING_DISPATCH_ID"]`, treat mode `crash` like `ask` but end with `os.kill(os.getpid(), signal.SIGKILL)` instead of `sys.exit(75)` (add `import signal`), and make mode `done` print `resumed` and `sys.exit(0)` before any ask is written. Give `_dispatch` an `ask_support: bool = False` parameter that adds `SUBAGENT_MODEL_ROUTING_ASK_SUPPORT="1"` to the environment. Then add:

```python
def _age_ask(run_dir: Path, ask_id: str, *, seconds: int) -> None:
    path = run_dir / "mailbox" / "asks" / f"{ask_id}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["created_at"] = (
        (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(doc), encoding="utf-8")


class OrphanedAskTests(_PauseHelpers, unittest.TestCase):
    def _resume(self, sandbox: ShimSandbox) -> subprocess.CompletedProcess[bytes]:
        env = sandbox.environment(
            SUBAGENT_MODEL_ROUTING_DISPATCH_ID=DISPATCH_ID,
            FAKE_PAUSE_MODE="done",
            SUBAGENT_MODEL_ROUTING_ASK_SUPPORT="1",
        )
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/pitwall-agent-routing"),
                "runs",
                "resume",
                DISPATCH_ID,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def _crashing_sandbox(self) -> ShimSandbox:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        self._install_ask_then_exit_provider(sandbox)
        return sandbox

    def test_crash_with_a_pending_ask_pauses_and_resume_defaults_it(self) -> None:
        sandbox = self._crashing_sandbox()
        crashed = self._dispatch(sandbox, sandbox.prompt(), "crash", ask_support=True)
        self.assertEqual(137, crashed.returncode)
        self.assertTrue(crashed.stdout.endswith(b"SHIM-DONE exit=137\n"))
        run_dir = self._run_dir(sandbox)
        self.assertEqual("paused", json.loads((run_dir / "run.json").read_text())["state"])
        self.assertEqual("orphaned-ask", json.loads((run_dir / "pause.json").read_text())["reason"])
        _age_ask(run_dir, "0001", seconds=4000)
        resumed = self._resume(sandbox)
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        finished = [r for r in sandbox.ledger_records() if r.get("event") == "finished"]
        self.assertEqual(["0001:default"], finished[-1]["askResolutions"])

    def test_crash_without_ask_support_stays_a_failure(self) -> None:
        sandbox = self._crashing_sandbox()
        crashed = self._dispatch(sandbox, sandbox.prompt(), "crash", ask_support=False)
        self.assertEqual(137, crashed.returncode)
        self.assertEqual(
            "failed", json.loads((self._run_dir(sandbox) / "run.json").read_text())["state"]
        )
```

Move `setUp`, `tearDown`, `_install_ask_then_exit_provider`, `_dispatch`, and `_run_dir` out of `PauseContractTests` into `class _PauseHelpers` and declare `class PauseContractTests(_PauseHelpers, unittest.TestCase)`, so both classes share the helpers and the existing pause tests run once. Add `from datetime import datetime, timedelta, timezone` and `import json` if absent.

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_pause_contract -v 2>&1 | tail -8`
Expected: FAIL — run state is `failed`, not `paused`.

- [x] **Step 3: Generalize the classification**

```python
pending_asks = (
    state == "failed" and (store.path / "mailbox").is_dir() and bool(store.mailbox().pending_asks())
)
if pending_asks and process_result.exit_code == PAUSED_EXIT_CODE:
    return _finish_paused(..., reason="question")
if pending_asks and ask_support:
    print(
        f"{provider_id}-shim: attempt ended with exit {process_result.exit_code} while ask(s) "
        "were pending; pausing so `runs resume` can apply their defaults",
        file=sys.stderr,
    )
    return _finish_paused(..., reason="orphaned-ask")
```

(`...` stands for the unchanged keyword arguments of the existing `_finish_paused` call; `enforce_cap()` from Task 2 still runs first.) `_finish_paused` writes `"reason": reason` into `pause.json` and passes it in the `dispatch.paused` event data.

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_pause_contract tests.test_runs_resume tests.test_shim_contract 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/dispatch.py tests/test_pause_contract.py
git commit -s -m "feat(agent-routing): a crashed attempt with a pending ask pauses so resume can default it"
```

## Task 9: Phase A operator documentation and Part 1 exit check

**Files:**
- Create: `docs/orchestrator-channel.md` (component docs directory)
- Modify: `README.md` (Documentation list :476; Run records section :384), `docs/run-records.md` (core artifacts, inspecting runs), `docs/lifecycle-hooks.md` (event names)

- [x] **Step 1: Write `docs/orchestrator-channel.md`** with these sections, each stating behavior as built:
  1. **What the channel is.** ASK, ANSWER, STEER, REPORT; mailbox files are the source of truth; tiers 1–4 (tier 1 arrives in Part 2).
  2. **Mailbox layout.** The `runs/<dispatch_id>/mailbox/` tree, `mailbox.json`, `channel.json`, `pause.json`, `resume.json`; 0700/0600; 64 KiB cap; dead-letter quarantine and its 20-file retention.
  3. **Enabling asks.** `--routing-ask-support` or `SUBAGENT_MODEL_ROUTING_ASK_SUPPORT=1`; `--routing-max-asks N` or `SUBAGENT_MODEL_ROUTING_MAX_ASKS` (1–50, default 5); why ask-prone dispatches should use `--routing-workspace isolated`.
  4. **Tier-4 contract.** The prompt suffix, exit 75, the paused classification, orphaned-ask pauses, and that a paused dispatch writes no `result.json`.
  5. **Deadlines and defaults.** The D1 cap formula with a worked example (600 s timeout → 90 s cap), effective deadlines, and where defaults apply (Decision 4).
  6. **Operator commands.** `inbox` (fields, `expired`, `ignored`, sequence-gap warnings), `answer`, `runs diff` (live refresh and the shared-workspace message), `runs resume`.
  7. **Broker endpoints.** `POST /asks`, `POST /answers/{id}`, `POST /steer`, `GET /inbox`; HMAC, 1 MiB cap, replay 409, `ask_cap_exceeded` 429, `ask_already_open` 409, `ask_already_answered` 409, idempotent retries.
  8. **Events and ledger.** `dispatch.paused`, `dispatch.resumed`, `ask.resolved`, `steer.acked`; the §9.3 finished-row fields.
- [x] **Step 2: Link it.** Add `docs/orchestrator-channel.md` to the README Documentation list; list `channel.json`, `mailbox.json`, `pause.json`, `resume.json` under Core artifacts in `docs/run-records.md` and add `runs resume`, `inbox`, and `answer` to its command block; add the four channel event names to `docs/lifecycle-hooks.md`.
- [x] **Step 3: Validate links and run the Part 1 exit check**

```bash
.venv/bin/python tools/check_markdown_links.py
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check runtime tests tools scripts/pitwall-agent-routing
.venv/bin/mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing
```

Expected: `validated N local links across M Markdown files` (exit 0); the suite reports at least 596 tests and `OK`; ruff and mypy clean.

- [x] **Step 4: Commit**

```bash
git add docs/orchestrator-channel.md README.md docs/run-records.md docs/lifecycle-hooks.md
git commit -s -m "docs(agent-routing): document the orchestrator channel's Phase A surface"
```

---

# Part 2 — Phase B: MCP server and tier 1

## Task 10: Harness registration paths, Cline correction, `mcpChannel` flag

**Files:**
- Modify: `runtime/model_routing/capability_inventory.py` (`PROFILES["cline"]` :55; add `CHANNEL_SERVER_NAME`, `CHANNEL_HARNESSES`, `channel_config_path`, `mcp_channel_registered`; `build_inventory` :236 and `render_context` :279)
- Modify: `docs/research/2026-09-08-orchestrator-channel.md` (§6.3 Cline row :256, §17 item 5 :637 — repository-root path)
- Modify: `tests/test_capability_inventory.py`

**Interfaces:**
- Produces: `CHANNEL_SERVER_NAME = "pitwall-channel"`, `CHANNEL_HARNESSES = ("claude", "codex", "copilot", "opencode", "kimi", "cline")`, `channel_config_path(harness_id: str, env: Mapping[str, str], home: Path) -> Path`, `mcp_channel_registered(harness_id: str, env: Mapping[str, str], home: Path) -> bool`.
- Inventory harness rows gain `"mcpChannel": bool`. The context markdown prints `Orchestrator channel: tier 1 (MCP server pitwall-channel registered)` or `Orchestrator channel: tier 4 (run pitwall-agent-routing setup mcp)` for channel harnesses only.

- [x] **Step 1: Write the failing tests** (add to `tests/test_capability_inventory.py`)

```python
class ChannelRegistrationPathTests(unittest.TestCase):
    def test_paths_follow_each_harness_and_cline_3(self) -> None:
        home = Path("/h")
        path = capability_inventory.channel_config_path
        self.assertEqual(Path("/h/.claude.json"), path("claude", {}, home))
        self.assertEqual(Path("/c/.claude.json"), path("claude", {"CLAUDE_CONFIG_DIR": "/c"}, home))
        self.assertEqual(Path("/h/.codex/config.toml"), path("codex", {}, home))
        self.assertEqual(Path("/x/config.toml"), path("codex", {"CODEX_HOME": "/x"}, home))
        self.assertEqual(Path("/h/.copilot/mcp-config.json"), path("copilot", {}, home))
        self.assertEqual(Path("/h/.config/opencode/opencode.json"), path("opencode", {}, home))
        self.assertEqual(Path("/h/.kimi-code/mcp.json"), path("kimi", {}, home))
        self.assertEqual(
            Path("/h/.cline/data/settings/cline_mcp_settings.json"), path("cline", {}, home)
        )
        self.assertEqual(
            Path("/d/settings/cline_mcp_settings.json"),
            path("cline", {"CLINE_DATA_DIR": "/d"}, home),
        )
        self.assertEqual(
            Path("/m.json"), path("cline", {"CLINE_MCP_SETTINGS_PATH": "/m.json"}, home)
        )

    def test_registered_flag_reads_each_config_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".codex").mkdir()
            (home / ".codex/config.toml").write_text(
                '[mcp_servers.pitwall-channel]\ncommand = "/bin/true"\nargs = ["mcp"]\n',
                encoding="utf-8",
            )
            (home / ".kimi-code").mkdir()
            (home / ".kimi-code/mcp.json").write_text(
                '{"mcpServers": {"other": {}}}', encoding="utf-8"
            )
            self.assertTrue(capability_inventory.mcp_channel_registered("codex", {}, home))
            self.assertFalse(capability_inventory.mcp_channel_registered("kimi", {}, home))
            self.assertFalse(capability_inventory.mcp_channel_registered("grok", {}, home))

    def test_inventory_rows_carry_the_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            executable(binary_dir / "codex")
            (home / ".codex").mkdir()
            (home / ".codex/config.toml").write_text(
                '[mcp_servers.pitwall-channel]\ncommand = "/bin/true"\n', encoding="utf-8"
            )
            inventory = capability_inventory.build_inventory(
                load_registry(),
                {"HOME": str(home), "PATH": str(binary_dir)},
                generated_at="2026-09-10T00:00:00.000Z",
            )
            row = {r["id"]: r for r in inventory["harnesses"]}["codex"]
            self.assertTrue(row["mcpChannel"])
            self.assertIn(
                "Orchestrator channel: tier 1", capability_inventory.render_context(inventory)
            )
```

`executable(...)` and `load_registry` are already imported by the module (`tests/test_capability_inventory.py:20-23`).

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_capability_inventory -v 2>&1 | tail -8`
Expected: FAIL with `AttributeError: module 'model_routing.capability_inventory' has no attribute 'channel_config_path'`.

- [x] **Step 3: Implement**

```python
CHANNEL_SERVER_NAME = "pitwall-channel"
CHANNEL_HARNESSES = ("claude", "codex", "copilot", "opencode", "kimi", "cline")


def channel_config_path(harness_id: str, env: Mapping[str, str], home: Path) -> Path:
    """User-scope file that registers MCP servers for *harness_id* (spec §6.3, Cline 3.x corrected)."""
    if harness_id == "claude":
        root = env.get("CLAUDE_CONFIG_DIR")
        return Path(root).expanduser() / ".claude.json" if root else home / ".claude.json"
    if harness_id == "codex":
        return _path("${CODEX_HOME:-~/.codex}/config.toml", env, home)
    if harness_id == "copilot":
        return home / ".copilot" / "mcp-config.json"
    if harness_id == "opencode":
        return _path("${XDG_CONFIG_HOME}/opencode/opencode.json", env, home)
    if harness_id == "kimi":
        return _path("${KIMI_CODE_HOME:-~/.kimi-code}/mcp.json", env, home)
    if harness_id == "cline":
        if env.get("CLINE_MCP_SETTINGS_PATH"):
            return Path(env["CLINE_MCP_SETTINGS_PATH"]).expanduser()
        data = env.get("CLINE_DATA_DIR") or str(_path("${CLINE_DIR:-~/.cline}", env, home) / "data")
        return Path(data).expanduser() / "settings" / "cline_mcp_settings.json"
    raise KeyError(harness_id)


def mcp_channel_registered(harness_id: str, env: Mapping[str, str], home: Path) -> bool:
    """True when the harness's user-scope MCP config names the channel server."""
    if harness_id not in CHANNEL_HARNESSES:
        return False
    path = channel_config_path(harness_id, env, home)
    if not path.is_file():
        return False
    found: dict[str, set[str]] = {kind: set() for kind in CAPABILITY_KINDS}
    return _read_structured(path, found) is None and CHANNEL_SERVER_NAME in found["mcps"]
```

Change the Cline profile's second file to `"${CLINE_DIR:-~/.cline}/data/settings/cline_mcp_settings.json"`. In `build_inventory`, add `"mcpChannel": mcp_channel_registered(harness_id, env, home)` to each row. In `render_context`, after the sources line, print the tier line for ids in `CHANNEL_HARNESSES`. In the spec, correct the §6.3 Cline row to the `cline_mcp_settings.json` resolution and add a §19 note recording the correction and its evidence (Decision 9).

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_capability_inventory tests.test_harnesses 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/capability_inventory.py tests/test_capability_inventory.py ../../docs/research/2026-09-08-orchestrator-channel.md
git commit -s -m "feat(agent-routing): channel registration paths, Cline 3.x MCP path, per-harness mcpChannel flag"
```

## Task 11: Channel environment for every harness; tier-1 prompt block

**Files:**
- Modify: `runtime/model_routing/dispatch.py` (ask-support block ~:772; the `adapter.prepare` call ~:950; `channel.json` write from Task 2 records `tier`)
- Modify: `tests/fixtures/fake_provider.py` (capture the channel keys and `MCP_TOOL_TIMEOUT`)
- Create: `tests/test_channel_env.py`

**Interfaces:**
- Consumes: `channel.CHANNEL_DISPATCH_ENV`, `CHANNEL_STATE_ROOT_ENV`, `CHANNEL_ATTEMPT_ENV` (Task 2); `capability_inventory.mcp_channel_registered` (Task 10), imported inside the ask-support branch so ordinary dispatches do not load the inventory module.
- Produces (`dispatch.py`): `tier1_prompt_block(*, max_asks: int) -> str`.
- Every dispatch (ask support or not) passes the three channel variables to the harness, so any `pitwall-channel` server the harness starts runs in the subagent role and never exposes `inbox`/`answer_ask` to a subagent (Decision 6). For provider `claude`, `MCP_TOOL_TIMEOUT` defaults to `3660000` so a blocking ask can outlast the harness's tool timeout.
- Ask-support dispatches whose harness has the channel registered (`mcp_channel_registered(provider_id, env, home)`) get the tier-1 block before the tier-4 block and `tier: "1"` in `channel.json`. The tier-4 block always follows as the fallback.

- [x] **Step 1: Write the failing tests** (`tests/test_channel_env.py`)

```python
"""Channel variables reach the harness; tier-1 prompt block when registered (plan Task 11)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.shim_test_support import ShimSandbox  # noqa: E402

from model_routing.channel import load_channel_config  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000d1"


class ChannelEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.sandbox.install_provider("codex")
        self.sandbox.install_provider("claude")

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _run(self, provider: str, *extra: str) -> dict:
        env = self.sandbox.environment(SUBAGENT_MODEL_ROUTING_DISPATCH_ID=DISPATCH_ID)
        result = self.sandbox.run(provider, [str(self.sandbox.prompt()), *extra], env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        return self.sandbox.captured_env()

    def test_every_dispatch_carries_the_channel_variables(self) -> None:
        captured = self._run("codex")
        self.assertEqual(DISPATCH_ID, captured["SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID"])
        self.assertEqual(
            str(self.sandbox.state / "subagent-model-routing"),
            captured["SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT"],
        )
        self.assertEqual("1", captured["SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT"])
        self.assertIsNone(captured["MCP_TOOL_TIMEOUT"])

    def test_claude_gets_a_tool_timeout_that_outlasts_an_ask(self) -> None:
        self.assertEqual("3660000", self._run("claude")["MCP_TOOL_TIMEOUT"])

    def test_registered_harness_gets_the_tier1_block_and_tier_record(self) -> None:
        (self.sandbox.home / ".codex").mkdir()
        (self.sandbox.home / ".codex/config.toml").write_text(
            '[mcp_servers.pitwall-channel]\ncommand = "/bin/true"\nargs = ["mcp"]\n',
            encoding="utf-8",
        )
        self._run("codex", "--routing-ask-support")
        prompt = self.sandbox.captured_stdin().decode()
        self.assertLess(
            prompt.index("# Orchestrator channel (tier-1 ask tool)"),
            prompt.index("# Orchestrator channel (tier-4 ask contract)"),
        )
        run_dir = self.sandbox.state / "subagent-model-routing" / "runs" / DISPATCH_ID
        config = load_channel_config(run_dir)
        assert config is not None
        self.assertEqual("1", config.tier)

    def test_unregistered_harness_gets_only_the_file_contract(self) -> None:
        self._run("codex", "--routing-ask-support")
        prompt = self.sandbox.captured_stdin().decode()
        self.assertNotIn("tier-1 ask tool", prompt)
        self.assertIn("tier-4 ask contract", prompt)


if __name__ == "__main__":
    unittest.main()
```

Extend the fixture's captured key list in `tests/fixtures/fake_provider.py` with `SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID`, `SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT`, `SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT`, and `MCP_TOOL_TIMEOUT`.

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_channel_env -v 2>&1 | tail -8`
Expected: FAIL with `KeyError: 'SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID'`.

- [x] **Step 3: Implement**

```python
def tier1_prompt_block(*, max_asks: int) -> str:
    """Tier-1 instructions for harnesses with the pitwall-channel MCP server registered."""
    return f"""
---

# Orchestrator channel (tier-1 ask tool)

When a blocking decision is dangerous or irreversible, call the MCP tool
`ask_orchestrator` (server `pitwall-channel`) instead of guessing. Pass one precise
`question`, its `blocked_on` class, 2-8 `options` as {{id, text}}, a real `default`
with a `default_rationale`, and `files_touched` (paths only, never diffs). The call
blocks until the orchestrator answers or the deadline passes, then returns the
answer or your default. If the call fails with a timeout, call it again with the
same question: it resumes waiting on the same open ask. One open ask at a time, at
most {max_asks} asks for this run. Never ask anything the worktree can answer.

At natural boundaries (before a risky tool call, after each milestone) call
`read_steering` and acknowledge each directive with `ack_steer`. A `stop` directive
means: wrap up, report what you completed, and exit normally.

If `ask_orchestrator` is not available in this session, use the file contract below.
"""
```

In `dispatch_legacy`:
- Compute `channel_tier = "1" if ask_support and mcp_channel_registered(provider_id, env, home) else "4"`. When `channel_tier == "1"`, the suffix is `tier1_prompt_block(max_asks=max_asks)` followed by the existing `tier4_prompt_suffix(...)`. Record `tier=channel_tier` in the `ChannelConfig` written before `run_process`.
- Replace `prepared = adapter.prepare(request, binary, prompt, env, preflight_data)` with:

```python
    harness_env = dict(env)
    harness_env[CHANNEL_DISPATCH_ENV] = dispatch_id
    harness_env[CHANNEL_STATE_ROOT_ENV] = str(state_root(env))
    harness_env[CHANNEL_ATTEMPT_ENV] = str(context.attempt)
    if provider_id == "claude":
        harness_env.setdefault("MCP_TOOL_TIMEOUT", "3660000")
    prepared = adapter.prepare(request, binary, prompt, harness_env, preflight_data)
```

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_channel_env tests.test_shim_contract tests.test_provider_adapters tests.test_runs_resume tests.test_dispatch_contract 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/dispatch.py tests/fixtures/fake_provider.py tests/test_channel_env.py
git commit -s -m "feat(agent-routing): channel variables for every harness and the tier-1 ask block"
```

## Task 12: Stop leaking dispatcher identity into harness environments (needs operator yes — Decision 8)

Skip this task if the operator declines; no other task depends on it.

**Files:**
- Modify: `runtime/model_routing/dispatch.py` (the `harness_env` block from Task 11)
- Modify: `tests/test_runs_resume.py` (fake reads `SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID` and `SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT` at :38 and :58–59), `tests/test_pause_contract.py` (:33)
- Modify: `tests/test_channel_env.py`

**Interfaces:**
- Removes from the harness environment only: `SUBAGENT_MODEL_ROUTING_DISPATCH_ID`, `SUBAGENT_MODEL_ROUTING_ATTEMPT`, `SUBAGENT_MODEL_ROUTING_WORKFLOW_ID`, `SUBAGENT_MODEL_ROUTING_TASK_ID`. The dispatcher's own environment, hooks (`hooks.py` sets its own), and ledger rows keep them.

- [x] **Step 1: Write the failing tests** (add to `tests/test_channel_env.py`)

```python
def test_identity_variables_stay_with_the_dispatcher(self) -> None:
    captured = self._run("codex")
    for key in (
        "SUBAGENT_MODEL_ROUTING_DISPATCH_ID",
        "SUBAGENT_MODEL_ROUTING_ATTEMPT",
        "SUBAGENT_MODEL_ROUTING_WORKFLOW_ID",
        "SUBAGENT_MODEL_ROUTING_TASK_ID",
    ):
        self.assertIsNone(captured[key], key)


def test_a_harness_can_dispatch_again_without_colliding(self) -> None:
    env = self.sandbox.environment(
        SUBAGENT_MODEL_ROUTING_DISPATCH_ID=DISPATCH_ID,
        FAKE_NESTED_DISPATCH=str(ROOT / "scripts" / "pitwall-agent-routing"),
    )
    result = self.sandbox.run("codex", [str(self.sandbox.prompt())], env=env)
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertEqual(2, len(self.sandbox.run_directories()))
```

Add the four identity keys to the fixture's captured list. Add fixture mode `FAKE_NESTED_DISPATCH=<launcher>`: pop the variable, then run `[sys.executable, launcher, "dispatch", "codex", <a temp prompt>]` with the inherited environment and exit with its return code.

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_channel_env -v 2>&1 | tail -8`
Expected: FAIL — the identity variables are present, and the nested dispatch exits 64 with `dispatch ID already exists`.

- [x] **Step 3: Implement**

```python
_DISPATCHER_IDENTITY = (
    "SUBAGENT_MODEL_ROUTING_DISPATCH_ID",
    "SUBAGENT_MODEL_ROUTING_ATTEMPT",
    "SUBAGENT_MODEL_ROUTING_WORKFLOW_ID",
    "SUBAGENT_MODEL_ROUTING_TASK_ID",
)
```

In the `harness_env` block, `for key in _DISPATCHER_IDENTITY: harness_env.pop(key, None)` before adding the channel variables. Update the two pre-existing fakes to read the channel variables (the resume test's captured `attempt` comes from `SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT`).

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_channel_env tests.test_runs_resume tests.test_pause_contract tests.test_scheduler tests.test_shim_contract 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/dispatch.py tests/fixtures/fake_provider.py tests/test_channel_env.py \
  tests/test_runs_resume.py tests/test_pause_contract.py
git commit -s -m "fix(agent-routing): keep dispatcher identity out of harness environments so harnesses can dispatch again"
```

## Task 13: Stdio MCP server core

**Files:**
- Create: `runtime/model_routing/mcp_server.py`, `runtime/model_routing/mcp_tools.py`, `tests/mcp_test_client.py`, `tests/test_mcp_server.py`
- Modify: `runtime/model_routing/cli.py` (`build_parser`: `mcp` subcommand; `main`: route to `mcp_server.main`)

**Interfaces:**
- Produces: `SERVER_NAME = "pitwall-channel"`, `SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")`, `server_role(env) -> Literal["subagent", "orchestrator", "misconfigured"]`, `class ToolError(Exception)`, `class ToolCancelled(Exception)`, `ToolHandler = Callable[[dict[str, Any], threading.Event, Callable[[str], None]], dict[str, Any]]`, `class ChannelServer(env, stdin: BinaryIO, stdout: BinaryIO)` with `register(definition: dict, handler: ToolHandler) -> None` and `serve() -> int`, and `main(env: Mapping[str, str]) -> int`. `mcp_tools.build_server(env, stdin, stdout) -> ChannelServer` is where tools are registered; this task creates it with no registrations, and Tasks 14 and 15 add the five tools there.
- Protocol: newline-delimited JSON-RPC 2.0; `initialize` (version negotiation, `capabilities.tools`), `notifications/initialized` (ignored), `ping`, `tools/list` (role-filtered), `tools/call` (one worker thread per call), `notifications/cancelled` (the worker stops and no response is sent, per the MCP cancellation rule), `notifications/progress` while a call runs when the request carries `_meta.progressToken`. Tool refusals are `isError: true` results; unknown methods are `-32601`; unknown or out-of-role tools are `-32602`; unparsable lines are `-32700` with `id: null`. Lines over 1 MiB are rejected with `-32600`. stdout carries protocol messages only; diagnostics go to stderr. The module imports no networking module.

- [x] **Step 1: Write the test client** (`tests/mcp_test_client.py`)

```python
"""Minimal stdio MCP client for tests: one reader thread, requests matched by id."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class McpTestClient:
    def __init__(self, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "pitwall-agent-routing"), "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self._next_id = 0
        self._responses: dict[Any, dict[str, Any]] = {}
        self.notifications: list[dict[str, Any]] = []
        self.raw_lines: list[bytes] = []
        self._cond = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            with self._cond:
                self.raw_lines.append(line)
                message = json.loads(line)
                if "id" in message and ("result" in message or "error" in message):
                    self._responses[message["id"]] = message
                else:
                    self.notifications.append(message)
                self._cond.notify_all()

    def send(
        self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False
    ) -> int | None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        request_id = None
        if not notify:
            self._next_id += 1
            request_id = self._next_id
            message["id"] = request_id
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()
        return request_id

    def wait(self, request_id: Any, timeout: float = 10.0) -> dict[str, Any]:
        with self._cond:
            if not self._cond.wait_for(lambda: request_id in self._responses, timeout=timeout):
                raise TimeoutError(f"no response to request {request_id}")
            return self._responses.pop(request_id)

    def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0
    ) -> dict[str, Any]:
        return self.wait(self.send(method, params), timeout)

    def initialize(self, version: str = "2025-06-18") -> dict[str, Any]:
        reply = self.request(
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        )
        self.send("notifications/initialized", notify=True)
        return reply

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout: float = 10.0,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name, "arguments": arguments}
        if meta is not None:
            params["_meta"] = meta
        return self.request("tools/call", params, timeout)

    def close(self) -> int:
        assert self.process.stdin is not None
        self.process.stdin.close()
        return self.process.wait(timeout=10)
```

- [x] **Step 2: Write the failing tests** (`tests/test_mcp_server.py`)

```python
"""Protocol behavior of the stdio MCP server (plan Task 13)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.mcp_test_client import McpTestClient  # noqa: E402

from model_routing import mcp_server  # noqa: E402
from model_routing.mcp_server import ChannelServer, ToolCancelled, ToolError, server_role  # noqa: E402


def _env(root: Path, **extra: str) -> dict[str, str]:
    return {
        "HOME": str(root),
        "XDG_STATE_HOME": str(root / "state"),
        "PATH": os.environ.get("PATH", ""),
        **extra,
    }


class RoleTests(unittest.TestCase):
    def test_role_follows_the_channel_variable(self) -> None:
        self.assertEqual("orchestrator", server_role({}))
        self.assertEqual(
            "orchestrator", server_role({"SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID": ""})
        )
        self.assertEqual(
            "subagent",
            server_role(
                {
                    "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID": "00000000-0000-4000-8000-000000000001"
                }
            ),
        )
        self.assertEqual(
            "misconfigured",
            server_role(
                {
                    "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID": "${SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID}"
                }
            ),
        )


class SubprocessProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.client = McpTestClient(_env(Path(self._temp.name)))

    def tearDown(self) -> None:
        self.client.close()
        self._temp.cleanup()

    def test_initialize_negotiates_and_ping_answers(self) -> None:
        reply = self.client.initialize("2025-06-18")["result"]
        self.assertEqual("2025-06-18", reply["protocolVersion"])
        self.assertEqual("pitwall-channel", reply["serverInfo"]["name"])
        self.assertIn("tools", reply["capabilities"])
        self.assertEqual({}, self.client.request("ping")["result"])

    def test_unknown_version_gets_the_latest_supported(self) -> None:
        self.assertEqual(
            "2025-11-25", self.client.initialize("1999-01-01")["result"]["protocolVersion"]
        )

    def test_errors_are_json_rpc_shaped_and_stdout_is_protocol_only(self) -> None:
        self.client.initialize()
        self.assertEqual(-32601, self.client.request("nope")["error"]["code"])
        self.assertEqual(
            -32602,
            self.client.request("tools/call", {"name": "missing", "arguments": {}})["error"][
                "code"
            ],
        )
        assert self.client.process.stdin is not None
        self.client.process.stdin.write(b"{not json\n")
        self.client.process.stdin.flush()
        self.assertEqual(
            -32700, self.client.wait(None)["error"]["code"]
        )  # parse errors carry id null
        for line in self.client.raw_lines:
            json.loads(line)  # every stdout line is a JSON-RPC message

    def test_eof_exits_cleanly(self) -> None:
        self.client.initialize()
        self.assertEqual(0, self.client.close())


class InProcessMechanicsTests(unittest.TestCase):
    """Worker threads, cancellation, and progress, with a registered stand-in tool."""

    def _serve(self, lines: list[dict]) -> tuple[ChannelServer, list[dict]]:
        read_fd, write_fd = os.pipe()
        stdin, feeder = os.fdopen(read_fd, "rb"), os.fdopen(write_fd, "wb")
        stdout = io.BytesIO()
        server = ChannelServer(
            {"SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID": "00000000-0000-4000-8000-000000000001"},
            stdin,
            stdout,
        )
        started = threading.Event()

        def slow(arguments: dict, cancel: threading.Event, progress) -> dict:  # type: ignore[no-untyped-def]
            started.set()
            for _ in range(50):
                progress("still waiting")
                if cancel.wait(0.05):
                    raise ToolCancelled()
            return {"done": True}

        def refuse(arguments: dict, cancel: threading.Event, progress) -> dict:  # type: ignore[no-untyped-def]
            raise ToolError("refused on purpose")

        server.register({"name": "ask_orchestrator", "inputSchema": {"type": "object"}}, slow)
        server.register({"name": "read_steering", "inputSchema": {"type": "object"}}, refuse)
        thread = threading.Thread(target=server.serve)
        thread.start()
        for message in lines:
            feeder.write((json.dumps(message) + "\n").encode())
            feeder.flush()
            if message.get("id") == 1:
                started.wait(5)
        feeder.close()
        thread.join(10)
        return server, [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_cancelled_call_sends_no_response(self) -> None:
        _server, out = self._serve(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "ask_orchestrator",
                        "arguments": {},
                        "_meta": {"progressToken": "p1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
            ]
        )
        self.assertFalse(any(m.get("id") == 1 for m in out))
        self.assertTrue(
            any(
                m.get("method") == "notifications/progress" and m["params"]["progressToken"] == "p1"
                for m in out
            )
        )

    def test_tool_error_is_an_is_error_result(self) -> None:
        _server, out = self._serve(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "read_steering", "arguments": {}},
                },
            ]
        )
        reply = next(m for m in out if m.get("id") == 2)
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("refused on purpose", reply["result"]["content"][0]["text"])

    def test_module_opens_no_network_transport(self) -> None:
        source = Path(mcp_server.__file__).read_text(encoding="utf-8")
        for forbidden in ("import socket", "http.server", "urllib"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
```

In the in-process test the feeder closes stdin after the cancellation; `serve()` sets every in-flight cancel event at EOF and joins workers, so the call ends without a response either way. `ToolCancelled` is what a handler raises when its cancel event fires.

- [x] **Step 3: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_mcp_server -v 2>&1 | tail -8`
Expected: FAIL with `ImportError: cannot import name 'mcp_server'`.

- [x] **Step 4: Implement `runtime/model_routing/mcp_server.py`**

```python
"""Stdio MCP server for the orchestrator channel: ``pitwall-agent-routing mcp``.

Newline-delimited JSON-RPC 2.0 on stdin/stdout, standard library only. It never
opens a socket (spec §10). stdout carries protocol messages only; diagnostics go
to stderr. Role follows the environment (plan Decision 6): with a UUID in
SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID it serves the subagent tools,
without one the orchestrator tools, and with anything else no tools.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from typing import Any, BinaryIO, Callable, Literal, Mapping
import uuid


from .channel import CHANNEL_DISPATCH_ENV


SERVER_NAME = "pitwall-channel"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUBAGENT_TOOLS = ("ask_orchestrator", "read_steering", "ack_steer")
ORCHESTRATOR_TOOLS = ("inbox", "answer_ask")
MAX_LINE_BYTES = 1024 * 1024
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

Role = Literal["subagent", "orchestrator", "misconfigured"]
ToolHandler = Callable[[dict[str, Any], threading.Event, Callable[[str], None]], dict[str, Any]]


class ToolError(Exception):
    """A tool refused; the client receives an ``isError`` result, not a protocol error."""


class ToolCancelled(Exception):
    """The client cancelled the call; per MCP, no response is sent."""


def server_role(env: Mapping[str, str]) -> Role:
    raw = env.get(CHANNEL_DISPATCH_ENV, "")
    if not raw:
        return "orchestrator"
    try:
        uuid.UUID(raw)
    except ValueError:
        return "misconfigured"
    return "subagent"


class ChannelServer:
    def __init__(self, env: Mapping[str, str], stdin: BinaryIO, stdout: BinaryIO) -> None:
        self.env = dict(env)
        self.role = server_role(self.env)
        self._stdin = stdin
        self._stdout = stdout
        self._write_lock = threading.Lock()
        self._inflight: dict[Any, threading.Event] = {}
        self._inflight_lock = threading.Lock()
        self._tools: dict[str, tuple[dict[str, Any], ToolHandler]] = {}
        self._workers: list[threading.Thread] = []

    def register(self, definition: dict[str, Any], handler: ToolHandler) -> None:
        self._tools[str(definition["name"])] = (definition, handler)

    def _visible(self) -> dict[str, tuple[dict[str, Any], ToolHandler]]:
        allowed = {"subagent": SUBAGENT_TOOLS, "orchestrator": ORCHESTRATOR_TOOLS}.get(
            self.role, ()
        )
        return {name: entry for name, entry in self._tools.items() if name in allowed}

    def _send(self, message: dict[str, Any]) -> None:
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self._write_lock:
            self._stdout.write(payload)
            self._stdout.flush()

    def _result(self, request_id: Any, result: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: Any, code: int, message: str) -> None:
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    def serve(self) -> int:
        for raw in self._stdin:
            if len(raw) > MAX_LINE_BYTES:
                self._error(None, INVALID_REQUEST, "message exceeds 1 MiB")
                continue
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError, UnicodeDecodeError:
                self._error(None, PARSE_ERROR, "parse error")
                continue
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                self._error(
                    message.get("id") if isinstance(message, dict) else None,
                    INVALID_REQUEST,
                    "invalid request",
                )
                continue
            self._dispatch(message)
        with self._inflight_lock:
            for event in self._inflight.values():
                event.set()
        for worker in self._workers:
            worker.join(timeout=5)
        return 0

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "notifications/cancelled":
            with self._inflight_lock:
                event = self._inflight.get(params.get("requestId"))
            if event is not None:
                event.set()
            return
        if request_id is None:
            return  # other notifications, including notifications/initialized
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = (
                requested
                if requested in SUPPORTED_PROTOCOL_VERSIONS
                else SUPPORTED_PROTOCOL_VERSIONS[0]
            )
            self._result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": _version(self.env)},
                    "instructions": "Orchestrator channel for Pitwall Agent Routing dispatches.",
                },
            )
            return
        if method == "ping":
            self._result(request_id, {})
            return
        if method == "tools/list":
            self._result(
                request_id, {"tools": [definition for definition, _ in self._visible().values()]}
            )
            return
        if method == "tools/call":
            self._start_call(request_id, params)
            return
        self._error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")

    def _start_call(self, request_id: Any, params: dict[str, Any]) -> None:
        name = params.get("name")
        entry = self._visible().get(str(name))
        if entry is None:
            self._error(request_id, INVALID_PARAMS, f"unknown tool: {name}")
            return
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        token = meta.get("progressToken")
        cancel = threading.Event()
        with self._inflight_lock:
            self._inflight[request_id] = cancel
        counter = [0]

        def progress(text: str) -> None:
            if token is None:
                return
            counter[0] += 1
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progressToken": token, "progress": counter[0], "message": text},
                }
            )

        handler = entry[1]

        def run() -> None:
            try:
                payload = handler(arguments, cancel, progress)
                self._result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
                        "structuredContent": payload,
                        "isError": False,
                    },
                )
            except ToolCancelled:
                pass
            except ToolError as exc:
                self._result(
                    request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True}
                )
            except Exception as exc:  # a tool bug must not kill the server
                traceback.print_exc(file=sys.stderr)
                self._result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": f"internal error: {exc}"}],
                        "isError": True,
                    },
                )
            finally:
                with self._inflight_lock:
                    self._inflight.pop(request_id, None)

        worker = threading.Thread(target=run, name=f"mcp-call-{request_id}", daemon=True)
        self._workers.append(worker)
        worker.start()


def _version(env: Mapping[str, str]) -> str:
    try:
        from .execution import distribution_version

        return distribution_version(env)
    except Exception:
        return "unknown"


def main(env: Mapping[str, str]) -> int:
    from .mcp_tools import build_server  # mcp_tools imports this module; import at call time

    return build_server(env, sys.stdin.buffer, sys.stdout.buffer).serve()
```

Create `runtime/model_routing/mcp_tools.py` with the registration seam that Tasks 14 and 15 extend:

```python
"""The orchestrator-channel MCP tools, registered by role (plan Tasks 14 and 15)."""

from __future__ import annotations

from typing import BinaryIO, Mapping

from .mcp_server import ChannelServer


def build_server(env: Mapping[str, str], stdin: BinaryIO, stdout: BinaryIO) -> ChannelServer:
    return ChannelServer(env, stdin, stdout)
```

In `cli.py`: `subparsers.add_parser("mcp")`, and in `main`: `if args.command == "mcp": return mcp_server.main(os.environ)`.

- [x] **Step 5: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_mcp_server -v 2>&1 | tail -5`
Expected: `OK`.

- [x] **Step 6: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/mcp_server.py runtime/model_routing/mcp_tools.py runtime/model_routing/cli.py \
  tests/mcp_test_client.py tests/test_mcp_server.py
git commit -s -m "feat(agent-routing): stdlib stdio MCP server core for the orchestrator channel"
```

## Task 14: Subagent tools — `ask_orchestrator`, `read_steering`, `ack_steer`

**Files:**
- Modify: `runtime/model_routing/mcp_tools.py`
- Create: `tests/test_mcp_subagent_tools.py`

**Interfaces:**
- Consumes: `channel.load_channel_config`, `effective_deadline_s`, `epoch_of`, `resolve_with_default` (Tasks 2–3); `Mailbox.write_ask(..., max_open=)` and `MailboxOpenAskError` (Task 6); `Mailbox.steers()`/`acks()` (Task 1); `ToolError`, `ToolCancelled`, `ChannelServer.register` (Task 13).
- Produces: `class SubagentTools(env)` with handlers `ask`, `read_steering`, `ack_steer`; tool definitions `ASK_TOOL`, `READ_STEERING_TOOL`, `ACK_STEER_TOOL`; `STOP_INSTRUCTION`; module constants `POLL_INTERVAL_S = 0.5`, `PROGRESS_INTERVAL_S = 15.0`; `clean_env(env) -> dict[str, str]` (drops empty values and unexpanded `${VAR}`, `${VAR:-x}`, `{env:VAR}` references a harness passed through literally).
- `ask_orchestrator` result: `{"ask_id", "choice", "answered_by", "resolved_by", "note"?, "instruction"?}` where `resolved_by == answered_by` (the spec's `{"resolved_by": "default", …}` shape). Refusals are `ToolError`s whose text tells the subagent what to do next.
- Spec §9.1 soft threshold: once an ask has waited half its effective deadline, the server emits `ask.escalated` `{askId, reason: "unanswered past half its deadline"}` once, so an operator hook (for example a terminal bell, documented in Task 29) can pull the human in before the default fires.

- [x] **Step 1: Write the failing tests** (`tests/test_mcp_subagent_tools.py`)

```python
"""Subagent-role MCP tools against a real server process (plan Task 14)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.mcp_test_client import McpTestClient  # noqa: E402

from model_routing import mcp_tools  # noqa: E402
from model_routing.channel import ChannelConfig, answer_ask, write_channel_config  # noqa: E402
from model_routing.run_store import RunStore  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000d4"
OPTIONS = [{"id": "a", "text": "-alpha"}, {"id": "b", "text": "-beta"}]


def _ask_args(**extra: object) -> dict:
    return {
        "question": "Which suffix?",
        "blocked_on": "naming",
        "options": OPTIONS,
        "default": "a",
        "default_rationale": "alpha is conventional",
        **extra,
    }


class SubagentToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.state_root = root / "state" / "subagent-model-routing"
        self.store = RunStore(self.state_root, DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        write_channel_config(
            self.store,
            ChannelConfig(
                DISPATCH_ID, timeout_seconds=600.0, attempt_started_epoch=time.time(), tier="1"
            ),
        )
        self.env = {
            "HOME": str(root),
            "PATH": os.environ.get("PATH", ""),
            "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID": DISPATCH_ID,
            "SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT": str(self.state_root),
            "XDG_STATE_HOME": "${XDG_STATE_HOME:-}",
        }  # an unexpanded reference must be ignored
        self.operator_env = {"HOME": str(root), "XDG_STATE_HOME": str(root / "state")}
        self.client = McpTestClient(self.env)
        self.client.initialize()

    def tearDown(self) -> None:
        self.client.close()
        self._temp.cleanup()

    def _call_in_background(
        self, arguments: dict, timeout: float = 20.0
    ) -> tuple[threading.Thread, dict]:
        box: dict = {}
        thread = threading.Thread(
            target=lambda: box.update(self.client.call("ask_orchestrator", arguments, timeout))
        )
        thread.start()
        return thread, box

    def _wait_for(self, path: Path, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while not path.exists():
            if time.monotonic() > deadline:
                raise AssertionError(f"{path} never appeared")
            time.sleep(0.05)

    def test_tools_listed_for_the_subagent_role(self) -> None:
        names = {tool["name"] for tool in self.client.request("tools/list")["result"]["tools"]}
        self.assertEqual({"ask_orchestrator", "read_steering", "ack_steer"}, names)

    def test_ask_blocks_until_the_operator_answers(self) -> None:
        thread, box = self._call_in_background(_ask_args())
        self._wait_for(self.store.path / "mailbox" / "asks" / "0001.json")
        answer_ask(
            self.operator_env,
            DISPATCH_ID,
            "0001",
            choice="b",
            answered_by="operator",
            note="beta",
            provider="operator",
        )
        thread.join(15)
        payload = box["result"]["structuredContent"]
        self.assertEqual(
            ("b", "operator", "operator"),
            (payload["choice"], payload["answered_by"], payload["resolved_by"]),
        )

    def test_deadline_returns_the_default_and_records_it(self) -> None:
        reply = self.client.call("ask_orchestrator", _ask_args(deadline_s=1), timeout=15)
        payload = reply["result"]["structuredContent"]
        self.assertEqual(("a", "default"), (payload["choice"], payload["resolved_by"]))
        stored = self.store.mailbox().get_answer("0001")
        assert stored is not None
        self.assertEqual("default", stored["answered_by"])
        events = [
            json.loads(line)
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            ["ask.escalated", "ask.resolved"],
            [e["event"] for e in events if e["event"] in ("ask.escalated", "ask.resolved")],
        )

    def test_d1_cap_clamps_the_requested_deadline(self) -> None:
        thread, _box = self._call_in_background(_ask_args(deadline_s=3600))
        ask_file = self.store.path / "mailbox" / "asks" / "0001.json"
        self._wait_for(ask_file)
        self.assertEqual(90, json.loads(ask_file.read_text())["deadline_s"])  # 15% of 600 s
        answer_ask(
            self.operator_env,
            DISPATCH_ID,
            "0001",
            choice="a",
            answered_by="operator",
            note=None,
            provider="operator",
        )
        thread.join(15)

    def test_one_open_ask_and_same_question_rewaits(self) -> None:
        first, first_box = self._call_in_background(_ask_args())
        self._wait_for(self.store.path / "mailbox" / "asks" / "0001.json")
        other = self.client.call("ask_orchestrator", _ask_args(question="Something else?"))
        self.assertTrue(other["result"]["isError"])
        self.assertIn("ask 0001 is still open", other["result"]["content"][0]["text"])
        again, again_box = self._call_in_background(_ask_args())
        time.sleep(0.3)
        answer_ask(
            self.operator_env,
            DISPATCH_ID,
            "0001",
            choice="b",
            answered_by="operator",
            note=None,
            provider="operator",
        )
        first.join(15)
        again.join(15)
        self.assertEqual("b", first_box["result"]["structuredContent"]["choice"])
        self.assertEqual("b", again_box["result"]["structuredContent"]["choice"])
        self.assertEqual(1, len(self.store.mailbox().asks()))

    def test_cap_and_disabled_channel_are_refusals(self) -> None:
        write_channel_config(
            self.store,
            ChannelConfig(
                DISPATCH_ID, max_asks=1, timeout_seconds=600.0, attempt_started_epoch=time.time()
            ),
        )
        self.client.call("ask_orchestrator", _ask_args(deadline_s=1), timeout=15)
        capped = self.client.call("ask_orchestrator", _ask_args(question="Second?"))
        self.assertIn("ask cap exceeded", capped["result"]["content"][0]["text"])
        self.store.artifact("channel.json").unlink()
        disabled = self.client.call("ask_orchestrator", _ask_args(question="Third?"))
        self.assertIn("asks are not enabled", disabled["result"]["content"][0]["text"])

    def test_steering_delivery_and_acknowledgement(self) -> None:
        box = self.store.mailbox()
        note = box.write_steer(kind="note", message="FYI", requires_ack=False)
        stop = box.write_steer(kind="stop", message="wrap up")
        first = self.client.call("read_steering", {})["result"]["structuredContent"]["steers"]
        self.assertEqual([note["steer_id"], stop["steer_id"]], [s["steer_id"] for s in first])
        second = self.client.call("read_steering", {})["result"]["structuredContent"]["steers"]
        self.assertEqual(
            [stop["steer_id"]], [s["steer_id"] for s in second]
        )  # the note was delivered once
        acked = self.client.call("ack_steer", {"steer_id": stop["steer_id"]})["result"][
            "structuredContent"
        ]
        self.assertEqual("stop", acked["kind"])
        self.assertIn("wrap up", acked["instruction"])
        self.assertTrue(
            self.client.call("ack_steer", {"steer_id": stop["steer_id"]})["result"][
                "structuredContent"
            ]["already"]
        )
        self.assertTrue(self.client.call("ack_steer", {"steer_id": "0099"})["result"]["isError"])
        events = [
            json.loads(line)["event"]
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(2, events.count("steer.acked"))


class ProgressTests(unittest.TestCase):
    def test_long_waits_emit_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            store = RunStore(root, DISPATCH_ID)
            store.path.mkdir(parents=True)
            write_channel_config(
                store,
                ChannelConfig(
                    DISPATCH_ID, timeout_seconds=600.0, attempt_started_epoch=time.time()
                ),
            )
            tools = mcp_tools.SubagentTools(
                {
                    "HOME": directory,
                    "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID": DISPATCH_ID,
                    "SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT": str(root),
                }
            )
            messages: list[str] = []
            with (
                mock.patch.object(mcp_tools, "PROGRESS_INTERVAL_S", 0.1),
                mock.patch.object(mcp_tools, "POLL_INTERVAL_S", 0.05),
            ):
                tools.ask(_ask_args(deadline_s=1), threading.Event(), messages.append)
            self.assertGreaterEqual(len(messages), 3)
            self.assertIn("ask 0001", messages[0])


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_mcp_subagent_tools -v 2>&1 | tail -8`
Expected: FAIL — `tools/list` returns an empty list and `AttributeError: module 'model_routing.mcp_tools' has no attribute 'SubagentTools'`.

- [x] **Step 3: Implement the subagent tools in `mcp_tools.py`**

```python
from pathlib import Path
import re
import threading
import time
from typing import Any, BinaryIO, Callable, Mapping

from .channel import (
    CHANNEL_DISPATCH_ENV,
    CHANNEL_STATE_ROOT_ENV,
    effective_deadline_s,
    epoch_of,
    load_channel_config,
    resolve_with_default,
)
from .events import EventEmitter
from .hooks import HookRunner
from .mailbox import (
    BLOCKED_ON,
    MAX_DEADLINE_S,
    MAX_OPTIONS,
    SEVERITIES,
    MailboxCapError,
    MailboxError,
)
from .mcp_server import ChannelServer, ToolCancelled, ToolError
from .run_store import RunStore, state_root


POLL_INTERVAL_S = 0.5
PROGRESS_INTERVAL_S = 15.0
STOP_INSTRUCTION = "Wrap up now: start no new work, report what you completed, and exit normally."
_UNEXPANDED = re.compile(
    r"^(\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^}]*)?\}|\{env:[A-Za-z_][A-Za-z0-9_]*\})$"
)


def clean_env(env: Mapping[str, str]) -> dict[str, str]:
    """Drop empty values and references a harness passed through without expanding."""
    return {key: value for key, value in env.items() if value and not _UNEXPANDED.match(value)}


_OPTION = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
    "required": ["id", "text"],
}
ASK_TOOL = {
    "name": "ask_orchestrator",
    "title": "Ask the orchestrator",
    "description": (
        "Ask the orchestrator one blocking question when the default is dangerous or irreversible. "
        "Blocks until an answer or the deadline, then returns the answer or your default. Give 2-8 "
        "options, a real default with a rationale, and file paths only, never diffs. If the call times "
        "out, call it again with the same question to keep waiting."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 1},
            "blocked_on": {"type": "string", "enum": sorted(BLOCKED_ON)},
            "options": {"type": "array", "minItems": 1, "maxItems": MAX_OPTIONS, "items": _OPTION},
            "default": {"type": "string", "description": "One of the option ids, or 'abort'."},
            "default_rationale": {"type": "string"},
            "files_touched": {"type": "array", "items": {"type": "string"}},
            "severity": {"type": "string", "enum": sorted(SEVERITIES)},
            "deadline_s": {"type": "integer", "minimum": 1, "maximum": MAX_DEADLINE_S},
        },
        "required": ["question", "blocked_on", "options", "default"],
        "additionalProperties": False,
    },
}
READ_STEERING_TOOL = {
    "name": "read_steering",
    "title": "Read orchestrator steering",
    "description": "Return orchestrator directives not yet acknowledged. Call before risky tool calls and after each milestone.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}
ACK_STEER_TOOL = {
    "name": "ack_steer",
    "title": "Acknowledge a steering directive",
    "description": "Acknowledge one directive after applying it. A stop directive means wrap up and exit normally.",
    "inputSchema": {
        "type": "object",
        "properties": {"steer_id": {"type": "string"}, "note": {"type": "string"}},
        "required": ["steer_id"],
        "additionalProperties": False,
    },
}


def _answer_payload(ask: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ask_id": ask["ask_id"],
        "choice": answer["choice"],
        "answered_by": answer["answered_by"],
        "resolved_by": answer["answered_by"],
    }
    if answer.get("note"):
        payload["note"] = answer["note"]
    if answer["choice"] == "abort":
        payload["instruction"] = (
            "The orchestrator chose to abort: stop now, report what you completed, and exit normally."
        )
    return payload


class SubagentTools:
    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = clean_env(env)
        self.dispatch_id = self.env[CHANNEL_DISPATCH_ENV]
        root = self.env.get(CHANNEL_STATE_ROOT_ENV)
        self.state_root = Path(root) if root else state_root(self.env)

    def _store(self) -> RunStore:
        store = RunStore(self.state_root, self.dispatch_id)
        if not store.path.is_dir():
            raise ToolError(
                f"run {self.dispatch_id} not found under {self.state_root}; use the file contract in your prompt"
            )
        return store

    def _emitter(self, store: RunStore) -> EventEmitter:
        return EventEmitter(store, provider="mcp", model="channel", callback=HookRunner(self.env))

    def ask(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        store = self._store()
        config = load_channel_config(store.path)
        if config is None:
            raise ToolError(
                "asks are not enabled for this dispatch; proceed with your best judgment and state the assumption in your report"
            )
        box = store.mailbox()
        question = str(args.get("question", ""))
        open_asks = box.pending_asks()
        if open_asks and open_asks[0]["question"] == question:
            ask = open_asks[0]  # re-entry after a client-side timeout
        else:
            requested = args.get("deadline_s")
            cap = config.deadline_cap_s(time.time())
            deadline = (
                min(requested, cap)
                if isinstance(requested, int) and not isinstance(requested, bool)
                else cap
            )
            try:
                ask = box.write_ask(
                    blocked_on=str(args.get("blocked_on", "")),
                    question=question,
                    options=list(args.get("options") or []),
                    default=args.get("default"),
                    deadline_s=deadline,
                    files_touched=list(args.get("files_touched") or []),
                    default_rationale=args.get("default_rationale"),
                    severity=str(args.get("severity", "normal")),
                    max_open=config.max_open_asks,
                )
            except MailboxCapError as exc:  # includes MailboxOpenAskError
                raise ToolError(f"{exc}; proceed with your default") from exc
            except MailboxError as exc:
                raise ToolError(f"invalid ask: {exc}") from exc
        return self._await(store, ask, cancel, progress)

    def _await(
        self,
        store: RunStore,
        ask: Mapping[str, Any],
        cancel: threading.Event,
        progress: Callable[[str], None],
    ) -> dict[str, Any]:
        ask_id = str(ask["ask_id"])
        answer_file = store.path / "mailbox" / "answers" / f"{ask_id}.json"
        created = epoch_of(ask.get("created_at")) or time.time()
        effective = effective_deadline_s(ask, load_channel_config(store.path))
        deadline_at = created + effective
        escalate_at = created + effective / 2
        escalated = False
        next_progress = time.monotonic() + PROGRESS_INTERVAL_S
        while True:
            if answer_file.exists() and (answer := store.mailbox().get_answer(ask_id)) is not None:
                return _answer_payload(ask, answer)
            now = time.time()
            if not escalated and now >= escalate_at:
                escalated = True
                self._emitter(store).emit(
                    "ask.escalated",
                    {"askId": ask_id, "reason": "unanswered past half its deadline"},
                )
            remaining = deadline_at - now
            if remaining <= 0:
                return _answer_payload(
                    ask, resolve_with_default(store, ask, emitter=self._emitter(store))
                )
            if cancel.wait(min(POLL_INTERVAL_S, remaining)):
                raise ToolCancelled()
            if time.monotonic() >= next_progress:
                progress(
                    f"waiting for the orchestrator's answer to ask {ask_id} ({int(remaining)}s left)"
                )
                next_progress = time.monotonic() + PROGRESS_INTERVAL_S

    def read_steering(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        store = self._store()
        box = store.mailbox()
        acked = {ack["steer_id"] for ack in box.acks()}
        pending = [steer for steer in box.steers() if steer["steer_id"] not in acked]
        emitter = self._emitter(store)
        for steer in pending:
            if not steer["requires_ack"]:  # advisory notes are delivered exactly once
                box.write_ack(steer["steer_id"], note="delivered by read_steering")
                emitter.emit_steer_acked(steer["steer_id"], {"delivery": "read_steering"})
        keys = ("steer_id", "kind", "message", "requires_ack", "deadline_s", "created_at")
        return {"steers": [{key: steer[key] for key in keys} for steer in pending]}

    def ack_steer(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        steer_id = str(args.get("steer_id", ""))
        store = self._store()
        box = store.mailbox()
        steer = next((s for s in box.steers() if s["steer_id"] == steer_id), None)
        if steer is None:
            raise ToolError(f"unknown steer {steer_id}")
        if steer_id in {ack["steer_id"] for ack in box.acks()}:
            return {"acked": steer_id, "kind": steer["kind"], "already": True}
        note = args.get("note")
        box.write_ack(steer_id, note=note if isinstance(note, str) else None)
        self._emitter(store).emit_steer_acked(steer_id)
        result: dict[str, Any] = {"acked": steer_id, "kind": steer["kind"]}
        if steer["kind"] == "stop":
            result["instruction"] = STOP_INSTRUCTION
        return result


def build_server(env: Mapping[str, str], stdin: BinaryIO, stdout: BinaryIO) -> ChannelServer:
    server = ChannelServer(env, stdin, stdout)
    if server.role == "subagent":
        tools = SubagentTools(env)
        server.register(ASK_TOOL, tools.ask)
        server.register(READ_STEERING_TOOL, tools.read_steering)
        server.register(ACK_STEER_TOOL, tools.ack_steer)
    return server
```

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_mcp_subagent_tools tests.test_mcp_server -v 2>&1 | tail -5`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/mcp_tools.py tests/test_mcp_subagent_tools.py
git commit -s -m "feat(agent-routing): ask_orchestrator, read_steering, and ack_steer MCP tools"
```

## Task 15: Orchestrator tools — `inbox`, `answer_ask`

**Files:**
- Modify: `runtime/model_routing/mcp_tools.py`
- Create: `tests/test_mcp_orchestrator_tools.py`

**Interfaces:**
- Consumes: `channel.read_channel_inbox` (Task 3) and `channel.answer_ask` (Task 5).
- Produces: `class OrchestratorTools(env)` with `inbox` and `answer`; definitions `INBOX_TOOL` (`dispatch_id?`) and `ANSWER_ASK_TOOL` (`dispatch_id`, `ask_id`, `choice`, `note?`, `answered_by?` ∈ `orchestrator|operator`, default `orchestrator`). `answer_ask` returns `{"answer": {...}, "contextCommand": "pitwall-agent-routing runs diff <dispatch-id>"}` (D2).

- [x] **Step 1: Write the failing tests** (`tests/test_mcp_orchestrator_tools.py`)

```python
"""Orchestrator-role MCP tools and role separation (plan Task 15)."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.mcp_test_client import McpTestClient  # noqa: E402

from model_routing.run_store import RunStore  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000d5"


class OrchestratorToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.store = RunStore(root / "state" / "subagent-model-routing", DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self.store.mailbox().write_ask(
            blocked_on="choice",
            question="Apply 0032 first?",
            options=[{"id": "a", "text": "yes"}, {"id": "b", "text": "no"}],
            default="b",
            deadline_s=600,
        )
        self.client = McpTestClient(
            {
                "HOME": str(root),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ.get("PATH", ""),
            }
        )
        self.client.initialize()

    def tearDown(self) -> None:
        self.client.close()
        self._temp.cleanup()

    def test_only_orchestrator_tools_are_listed(self) -> None:
        names = {tool["name"] for tool in self.client.request("tools/list")["result"]["tools"]}
        self.assertEqual({"inbox", "answer_ask"}, names)
        refused = self.client.request("tools/call", {"name": "ask_orchestrator", "arguments": {}})
        self.assertEqual(-32602, refused["error"]["code"])

    def test_inbox_then_answer(self) -> None:
        inbox = self.client.call("inbox", {})["result"]["structuredContent"]
        self.assertEqual(["0001"], [a["askId"] for a in inbox["asks"]])
        self.assertEqual(
            f"pitwall-agent-routing runs diff {DISPATCH_ID}", inbox["asks"][0]["contextCommand"]
        )
        reply = self.client.call(
            "answer_ask",
            {
                "dispatch_id": DISPATCH_ID,
                "ask_id": "0001",
                "choice": "a",
                "note": "0032 renames first",
            },
        )["result"]
        self.assertFalse(reply["isError"])
        self.assertEqual("orchestrator", reply["structuredContent"]["answer"]["answered_by"])
        self.assertEqual([], self.client.call("inbox", {})["result"]["structuredContent"]["asks"])

    def test_refusals_are_is_error_results(self) -> None:
        self.assertTrue(self.client.call("inbox", {"dispatch_id": "ffffffff"})["result"]["isError"])
        bad = self.client.call(
            "answer_ask", {"dispatch_id": DISPATCH_ID, "ask_id": "0001", "choice": "zzz"}
        )
        self.assertTrue(bad["result"]["isError"])
        policy = self.client.call(
            "answer_ask",
            {
                "dispatch_id": DISPATCH_ID,
                "ask_id": "0001",
                "choice": "a",
                "answered_by": "policy:x",
            },
        )
        self.assertTrue(
            policy["result"]["isError"]
        )  # the policy source belongs to the scheduler, not a tool


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_mcp_orchestrator_tools -v 2>&1 | tail -8`
Expected: FAIL — `tools/list` returns an empty list for the orchestrator role.

- [x] **Step 3: Implement**

```python
from .channel import answer_ask, read_channel_inbox
from .run_store import find_run

INBOX_TOOL = {
    "name": "inbox",
    "title": "Channel inbox",
    "description": (
        "List unresolved asks and unacknowledged steers across runs (or one run). Each ask carries "
        "its options, default, remaining deadline, and a `runs diff` command for context."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"dispatch_id": {"type": "string"}},
        "additionalProperties": False,
    },
}
ANSWER_ASK_TOOL = {
    "name": "answer_ask",
    "title": "Answer a subagent's ask",
    "description": (
        "Answer one ask by choosing one of its option ids (or 'abort'). Escalate schema, destructive, "
        "spend, and blocking asks to the operator instead of answering them yourself."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "dispatch_id": {"type": "string"},
            "ask_id": {"type": "string"},
            "choice": {"type": "string"},
            "note": {"type": "string"},
            "answered_by": {"type": "string", "enum": ["orchestrator", "operator"]},
        },
        "required": ["dispatch_id", "ask_id", "choice"],
        "additionalProperties": False,
    },
}


class OrchestratorTools:
    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = clean_env(env)

    def inbox(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        dispatch_id = args.get("dispatch_id")
        try:
            return read_channel_inbox(
                self.env, dispatch_id if isinstance(dispatch_id, str) and dispatch_id else None
            )
        except FileNotFoundError as exc:
            raise ToolError(str(exc)) from exc

    def answer(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        answered_by = args.get("answered_by", "orchestrator")
        if answered_by not in ("orchestrator", "operator"):
            raise ToolError("answered_by must be orchestrator or operator")
        try:
            dispatch_id = str(args["dispatch_id"])
            answer = answer_ask(
                self.env,
                dispatch_id,
                str(args["ask_id"]),
                choice=str(args["choice"]),
                answered_by=answered_by,
                note=args.get("note"),
                provider="mcp",
            )
            run_id = find_run(self.env, dispatch_id).name
        except KeyError as exc:
            raise ToolError("dispatch_id, ask_id, and choice are required") from exc
        except (FileNotFoundError, MailboxError) as exc:
            raise ToolError(str(exc)) from exc
        return {"answer": answer, "contextCommand": f"pitwall-agent-routing runs diff {run_id}"}
```

In `build_server`, add: `elif server.role == "orchestrator": tools = OrchestratorTools(env); server.register(INBOX_TOOL, tools.inbox); server.register(ANSWER_ASK_TOOL, tools.answer)`.

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_mcp_orchestrator_tools tests.test_mcp_subagent_tools tests.test_mcp_server 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/mcp_tools.py tests/test_mcp_orchestrator_tools.py
git commit -s -m "feat(agent-routing): inbox and answer_ask MCP tools for the orchestrator role"
```

## Task 16: Registration for six harnesses — `setup mcp`

**Files:**
- Create: `runtime/model_routing/mcp_registration.py`, `tests/test_mcp_registration.py`
- Modify: `runtime/model_routing/cli.py` (`setup` subcommands :1086 — add `mcp`; `main` :1195)

**Interfaces:**
- Consumes: `capability_inventory.CHANNEL_HARNESSES`, `CHANNEL_SERVER_NAME`, `channel_config_path` (Task 10); `route_sync.SyncPlan`, `render_diff`, `apply_plan` (existing, `route_sync.py:22-147`); `execution.child_execution` (existing, `execution.py:63`).
- Produces: `FORWARDED_ENV = ("SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID", "SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT", "SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT", "SUBAGENT_MODEL_ROUTING_STATE_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME")`; `TOOL_TIMEOUT_S = 3660`; `class RegistrationError(RuntimeError)`; `channel_server_command(env) -> str`; `render_entry(harness: str, command: str) -> Any`; `plan_registration(harness, env, home, *, command: str, remove: bool = False) -> SyncPlan`; `current_entry(harness, env, home) -> Mapping[str, Any] | None`.
- CLI: `pitwall-agent-routing setup mcp [--harness H]... [--command PATH] [--remove] [--dry-run] [--yes]`. `--command` overrides the registered server command (for tests and non-standard installs); without it the command comes from `channel_server_command`, which needs the verified user-bin alias in source mode. Default harness set: installed channel harnesses for registration; every channel harness for `--remove`. Confirmation and exit codes follow `routes sync` (`cli.py:742-771`). With `--remove`, a config file that cannot be parsed is reported and left untouched, and the command still exits 0.

Entries (golden-tested byte for byte). `CMD` is the absolute command from `channel_server_command`: the source launcher alias in source mode, else `pitwall-agent-routing` resolved on `PATH`.

| Harness | File | Entry |
| --- | --- | --- |
| Claude Code | `channel_config_path("claude")` via `claude mcp add-json --scope user pitwall-channel <json>` (removal: `claude mcp remove --scope user pitwall-channel`); the tool never edits `~/.claude.json` itself, because Claude Code rewrites that file while running | `{"type": "stdio", "command": CMD, "args": ["mcp"], "env": {V: "${V:-}" for V in FORWARDED_ENV}}` |
| Codex | `config.toml`, managed block between `# >>> pitwall-channel (managed by pitwall-agent-routing setup mcp)` and `# <<< pitwall-channel` | `[mcp_servers.pitwall-channel]`, `command`, `args = ["mcp"]`, `env_vars = [FORWARDED_ENV…]`, `startup_timeout_sec = 30`, `tool_timeout_sec = 3660` |
| Copilot CLI | `~/.copilot/mcp-config.json` → `mcpServers` | `{"type": "local", "command": CMD, "args": ["mcp"], "env": {V: "${V}"}, "tools": ["*"]}` |
| OpenCode | `opencode.json` → `mcp` (strict JSON, as `providers/opencode.py:58-107` requires) | `{"type": "local", "command": [CMD, "mcp"], "enabled": true, "environment": {V: "{env:V}"}}` |
| Kimi | `mcp.json` → `mcpServers` (Kimi passes the parent environment to stdio servers: `mergeStdioEnv` in its bundle) | `{"command": CMD, "args": ["mcp"], "toolTimeoutMs": 3660000}` |
| Cline | `cline_mcp_settings.json` → `mcpServers` | `{"type": "stdio", "command": CMD, "args": ["mcp"], "timeout": 3660, "disabled": false}` |

An existing `pitwall-channel` entry counts as ours when its command's basename is `pitwall-agent-routing` and its arguments include `mcp`; any other same-named entry makes the plan raise `RegistrationError("… is not managed by pitwall-agent-routing; rename or remove it")`. A server that receives an unexpanded `${…}` or `{env:…}` value ignores it (`mcp_tools.clean_env`, Task 14), so a harness that does not expand references degrades to the tier-4 fallback instead of misbehaving.

- [x] **Step 1: Write the failing tests** (`tests/test_mcp_registration.py`)

```python
"""Per-harness pitwall-channel registration (plan Task 16)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.capability_inventory import mcp_channel_registered  # noqa: E402
from model_routing.mcp_registration import (  # noqa: E402
    FORWARDED_ENV,
    RegistrationError,
    plan_registration,
)
from model_routing.route_sync import apply_plan  # noqa: E402


CMD = "/opt/pitwall/bin/pitwall-agent-routing"


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.home = Path(self._temp.name)
        self.env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _apply(self, harness: str, *, remove: bool = False):  # type: ignore[no-untyped-def]
        plan = plan_registration(harness, self.env, self.home, command=CMD, remove=remove)
        apply_plan(plan)
        return plan

    def test_codex_block_is_valid_toml_idempotent_and_removable(self) -> None:
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir()
        config.write_text(
            'model = "gpt-5.6-sol"\n\n[mcp_servers.docs]\ncommand = "docs"\n', encoding="utf-8"
        )
        original = config.read_text(encoding="utf-8")
        self._apply("codex")
        data = tomllib.loads(config.read_text(encoding="utf-8"))
        entry = data["mcp_servers"]["pitwall-channel"]
        self.assertEqual(
            (CMD, ["mcp"], list(FORWARDED_ENV), 3660),
            (entry["command"], entry["args"], entry["env_vars"], entry["tool_timeout_sec"]),
        )
        self.assertEqual("docs", data["mcp_servers"]["docs"]["command"])
        self.assertFalse(plan_registration("codex", self.env, self.home, command=CMD).changed)
        self.assertTrue(mcp_channel_registered("codex", self.env, self.home))
        self._apply("codex", remove=True)
        self.assertEqual(original, config.read_text(encoding="utf-8"))

    def test_json_harness_entries(self) -> None:
        expected = {
            "copilot": (
                "mcpServers",
                {
                    "type": "local",
                    "command": CMD,
                    "args": ["mcp"],
                    "env": {v: "${" + v + "}" for v in FORWARDED_ENV},
                    "tools": ["*"],
                },
            ),
            "opencode": (
                "mcp",
                {
                    "type": "local",
                    "command": [CMD, "mcp"],
                    "enabled": True,
                    "environment": {v: "{env:" + v + "}" for v in FORWARDED_ENV},
                },
            ),
            "kimi": ("mcpServers", {"command": CMD, "args": ["mcp"], "toolTimeoutMs": 3660000}),
            "cline": (
                "mcpServers",
                {
                    "type": "stdio",
                    "command": CMD,
                    "args": ["mcp"],
                    "timeout": 3660,
                    "disabled": False,
                },
            ),
        }
        for harness, (section, entry) in expected.items():
            with self.subTest(harness=harness):
                plan = self._apply(harness)
                data = json.loads(plan.path.read_text(encoding="utf-8"))
                self.assertEqual(entry, data[section]["pitwall-channel"])
                self.assertEqual(0o600, plan.path.stat().st_mode & 0o777)
                self.assertTrue(mcp_channel_registered(harness, self.env, self.home))
                self._apply(harness, remove=True)
                self.assertNotIn(
                    "pitwall-channel",
                    json.loads(plan.path.read_text(encoding="utf-8")).get(section, {}),
                )

    def test_claude_registers_through_its_cli(self) -> None:
        plan = plan_registration("claude", self.env, self.home, command=CMD)
        self.assertEqual(plan.before, plan.after)  # ~/.claude.json is never edited directly
        (command,) = plan.commands
        self.assertEqual(
            ["claude", "mcp", "add-json", "--scope", "user", "pitwall-channel"], list(command[:6])
        )
        entry = json.loads(command[6])
        self.assertEqual({v: "${" + v + ":-}" for v in FORWARDED_ENV}, entry["env"])

    def test_foreign_entries_and_jsonc_are_refused(self) -> None:
        kimi = self.home / ".kimi-code" / "mcp.json"
        kimi.parent.mkdir()
        kimi.write_text(
            '{"mcpServers": {"pitwall-channel": {"command": "someone-else"}}}', encoding="utf-8"
        )
        with self.assertRaises(RegistrationError):
            plan_registration("kimi", self.env, self.home, command=CMD)
        opencode = self.home / ".config" / "opencode" / "opencode.json"
        opencode.parent.mkdir(parents=True)
        opencode.write_text("{\n  // comment\n}\n", encoding="utf-8")
        with self.assertRaises(RegistrationError):
            plan_registration("opencode", self.env, self.home, command=CMD)

    def test_setup_mcp_dry_run_writes_nothing(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/pitwall-agent-routing"),
                "setup",
                "mcp",
                "--harness",
                "kimi",
                "--command",
                CMD,
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            env={**self.env, "PATH": "/usr/bin:/bin"},
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("pitwall-channel", result.stdout)
        self.assertFalse((self.home / ".kimi-code" / "mcp.json").exists())


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_mcp_registration -v 2>&1 | tail -8`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_routing.mcp_registration'`.

- [x] **Step 3: Implement `mcp_registration.py`**

Structure (all functions pure except `channel_server_command`):

```python
"""Register the pitwall-channel MCP server in each harness's user-scope config (spec §6.3)."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tomllib
from typing import Any, Mapping

from .capability_inventory import CHANNEL_HARNESSES, CHANNEL_SERVER_NAME, channel_config_path
from .execution import ExecutionError, child_execution
from .route_sync import SyncPlan


FORWARDED_ENV = (
    "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID",
    "SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT",
    "SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT",
    "SUBAGENT_MODEL_ROUTING_STATE_HOME",
    "XDG_STATE_HOME",
    "XDG_CONFIG_HOME",
)
TOOL_TIMEOUT_S = 3660
_BEGIN = f"# >>> {CHANNEL_SERVER_NAME} (managed by pitwall-agent-routing setup mcp)"
_END = f"# <<< {CHANNEL_SERVER_NAME}"
_JSON_SECTION = {
    "copilot": "mcpServers",
    "opencode": "mcp",
    "kimi": "mcpServers",
    "cline": "mcpServers",
}


class RegistrationError(RuntimeError):
    """A harness config cannot be registered safely."""


def channel_server_command(env: Mapping[str, str]) -> str:
    try:
        descriptor = child_execution(env)
    except ExecutionError as exc:
        raise RegistrationError(str(exc)) from exc
    if descriptor.mode == "source" and descriptor.launcher is not None:
        return str(descriptor.launcher)
    found = shutil.which("pitwall-agent-routing", path=env.get("PATH"))
    if found is None:
        raise RegistrationError(
            "pitwall-agent-routing is not on PATH; install it before `setup mcp`"
        )
    return str(Path(found).resolve())


def render_entry(harness: str, command: str) -> Any:
    if harness == "claude":
        return {
            "type": "stdio",
            "command": command,
            "args": ["mcp"],
            "env": {name: "${" + name + ":-}" for name in FORWARDED_ENV},
        }
    if harness == "copilot":
        return {
            "type": "local",
            "command": command,
            "args": ["mcp"],
            "env": {name: "${" + name + "}" for name in FORWARDED_ENV},
            "tools": ["*"],
        }
    if harness == "opencode":
        return {
            "type": "local",
            "command": [command, "mcp"],
            "enabled": True,
            "environment": {name: "{env:" + name + "}" for name in FORWARDED_ENV},
        }
    if harness == "kimi":
        return {"command": command, "args": ["mcp"], "toolTimeoutMs": TOOL_TIMEOUT_S * 1000}
    if harness == "cline":
        return {
            "type": "stdio",
            "command": command,
            "args": ["mcp"],
            "timeout": TOOL_TIMEOUT_S,
            "disabled": False,
        }
    if harness == "codex":
        return (
            "\n".join(
                [
                    _BEGIN,
                    f"[mcp_servers.{CHANNEL_SERVER_NAME}]",
                    f"command = {json.dumps(command)}",
                    'args = ["mcp"]',
                    "env_vars = [" + ", ".join(json.dumps(name) for name in FORWARDED_ENV) + "]",
                    "startup_timeout_sec = 30",
                    f"tool_timeout_sec = {TOOL_TIMEOUT_S}",
                    _END,
                ]
            )
            + "\n"
        )
    raise KeyError(harness)


def _is_ours(entry: Any) -> bool:
    if not isinstance(entry, Mapping):
        return False
    command = entry.get("command")
    args = entry.get("args", [])
    if isinstance(command, list):  # OpenCode keeps argv in one list
        command, args = (command[0] if command else ""), command[1:]
    return Path(str(command)).name == "pitwall-agent-routing" and "mcp" in list(args or [])
```

Then:
- `_plan_json(harness, path, command, remove)`: read `before` (empty when missing); parse with `json.loads` (on failure raise `RegistrationError(f"{path} is not strict JSON …")`); take `section = data.setdefault(_JSON_SECTION[harness], {})`; if `pitwall-channel` exists and is not ours, raise; set or pop the entry; `after = json.dumps(data, indent=2) + "\n"` (on removal from a file that never had the entry, `after = before`).
- `_plan_codex(path, command, remove)`: strip any existing managed block (lines from `_BEGIN` through `_END`, plus the blank separator line directly before `_BEGIN`) to get `base`; if `tomllib.loads(base)` still has `mcp_servers["pitwall-channel"]`, raise (unmanaged); `after = base` on removal, else `base.rstrip("\n") + ("\n\n" if base.strip() else "") + render_entry("codex", command)`; verify `tomllib.loads(after)` parses before returning.
- `_plan_claude(path, command, remove)`: read `mcpServers["pitwall-channel"]` from `path` if it exists (read-only); desired = `render_entry("claude", command)`; commands are `()` when already equal, `(("claude", "mcp", "remove", "--scope", "user", name),)` for removal when present and ours, and add-json (preceded by remove when an older entry of ours differs) otherwise. `before == after == current text`.
- `plan_registration` dispatches on harness and returns `SyncPlan(harness, path, before, after, (), commands)`.
- `current_entry(harness, env, home)` returns the parsed entry for Codex (from `tomllib`) or JSON harnesses, else `None`.

CLI `_setup_mcp(harnesses, command, remove, dry_run, yes)`: take `command` from `--command` or `channel_server_command(os.environ)` (printing a `RegistrationError` and exiting 2 when neither works); resolve the harness list (installed harnesses via `provider_setup.resolve_provider_binary` plus `shutil.which("copilot")`, or all six for `--remove`); for each, build the plan, print `== <harness>: <path>`, `already registered`/`not registered` when unchanged, else `render_diff(plan)`; confirm exactly as `_routes_sync` does; `apply_plan`. With `--remove`, catch `RegistrationError` per harness, print `skipped <harness>: <reason>`, and continue.

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_mcp_registration tests.test_route_sync tests.test_capability_inventory 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/mcp_registration.py runtime/model_routing/cli.py tests/test_mcp_registration.py
git commit -s -m "feat(agent-routing): setup mcp registers pitwall-channel for six harnesses"
```

## Task 17: `install.sh --with-mcp`; uninstall removes registrations

**Files:**
- Modify: `scripts/install.sh` (argument parsing :19–35; after the installation doctor, ~:205)
- Modify: `docs/agent-routing/migration-from-standalone.md` (repository root; the `# BEGIN MANUAL UNINSTALL` block)
- Modify: `tests/test_installer.py`, `tests/test_uninstall.py`, `README.md` (Install and Uninstall sections)

**Interfaces:**
- `install.sh [--with-mcp] [scripts-destination]`. The usage line becomes `usage: install.sh [--with-mcp] [scripts-destination]`; more than one positional argument still exits 2. With `--with-mcp`, after the installation doctor passes, run `"$BIN_DEST/pitwall-agent-routing" setup mcp --yes`; its failure fails the install with its message.
- Uninstall: before the symlink loop, run the payload's own module (never a possibly foreign `$BIN_DIR` entry): `PYTHONPATH="$COMPONENT_ROOT/runtime" "$PYTHON_BIN" -m model_routing setup mcp --remove --yes || true`. The closing line becomes `User routing state, plugin data, provider config (apart from the pitwall-channel MCP entries), and the Pitwall payload were retained.`

- [x] **Step 1: Write the failing tests**

In `tests/test_installer.py`, add: `--with-mcp` accepted in either position; two positionals still exit 2 with the new usage line; with `--with-mcp` and a fake `kimi` on `PATH`, `~/.kimi-code/mcp.json` gains the `pitwall-channel` entry after install. Update any existing assertion on the old usage text to the new line.

In `tests/test_uninstall.py`, extend the existing sandbox: before running the block, write `home/.codex/config.toml` containing `model = "x"` plus a managed block (render it with `mcp_registration.render_entry("codex", "/p/pitwall-agent-routing")`) and `home/.kimi-code/mcp.json` containing only our entry. Assert afterwards: `config.toml` is exactly `model = "x"\n`; `mcp.json` has an empty `mcpServers`; the unparseable `home/.config/opencode/opencode.json` (`state 3`) is byte-identical; the recorded client call list is unchanged (no `claude mcp remove`, because `~/.claude.json` has no entry).

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_installer tests.test_uninstall -v 2>&1 | tail -8`
Expected: FAIL — `--with-mcp` is treated as a destination path, and the uninstall leaves both registrations in place.

- [x] **Step 3: Implement.** Parse arguments with a loop that sets `WITH_MCP=1` for `--with-mcp` and collects at most one positional; keep every existing validation. Add the post-doctor step and the uninstall line above. Document both in the README Install and Uninstall sections.

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_installer tests.test_uninstall tests.test_bootstrap 2>&1 | tail -3 && bash -n scripts/install.sh`
Expected: `OK`, and `bash -n` prints nothing.

- [x] **Step 5: Commit**

```bash
git add scripts/install.sh ../../docs/agent-routing/migration-from-standalone.md tests/test_installer.py tests/test_uninstall.py README.md
git commit -s -m "feat(agent-routing): install.sh --with-mcp; uninstall removes pitwall-channel registrations"
```

## Task 18: Doctor channel checks

**Files:**
- Modify: `runtime/model_routing/doctor.py` (add `CHANNEL_CATEGORY = "channel"`, `_probe_channel_server`, `_channel_checks`; call from `run_doctor` :2168 in the full, non-installation-only branch after `_routes_checks`)
- Modify: `tests/test_doctor.py`, `docs/doctor.md`

**Interfaces:**
- `channel.mcp_server`: spawn `[sys.executable, "-m", "model_routing", "mcp"]` with `PYTHONPATH` set to the runtime root, once without the channel variable and once with the nil UUID `00000000-0000-4000-8000-000000000000`; send `initialize`, `notifications/initialized`, `tools/list` on stdin and close it; 5-second timeout. PASS when the orchestrator role lists exactly `inbox`, `answer_ask` and the subagent role lists exactly `ask_orchestrator`, `read_steering`, `ack_steer`; otherwise WARN (spec §6.3 decision 3: tier 1 unavailable, tier 4 still works, reported, not fatal).
- `channel.registration` (provider-scoped, one per installed channel harness): PASS when `mcp_channel_registered` and the registered command is an executable file; WARN with remediation `pitwall-agent-routing setup mcp --harness <id>` otherwise. Harnesses that are not installed get no check.

- [x] **Step 1: Write the failing tests** (add to `tests/test_doctor.py`)

```python
class ChannelDoctorTests(unittest.TestCase):
    def test_server_handshake_and_registration_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            binary_dir.mkdir()
            for name in ("codex", "kimi"):
                target = binary_dir / name
                target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                target.chmod(0o755)
            launcher = binary_dir / "pitwall-agent-routing"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(0o755)
            (home / ".codex").mkdir()
            (home / ".codex/config.toml").write_text(
                f'[mcp_servers.pitwall-channel]\ncommand = "{launcher}"\nargs = ["mcp"]\n',
                encoding="utf-8",
            )
            env = {
                "HOME": str(home),
                "PATH": f"{binary_dir}:/usr/bin:/bin",
                "XDG_STATE_HOME": str(home / "state"),
            }
            report = run_doctor(None, env)
        checks = {
            (c["id"], c.get("provider")): c for c in report["checks"] if c["category"] == "channel"
        }
        self.assertEqual("PASS", checks[("channel.mcp_server", None)]["status"])
        self.assertEqual("PASS", checks[("channel.registration", "codex")]["status"])
        kimi = checks[("channel.registration", "kimi")]
        self.assertEqual("WARN", kimi["status"])
        self.assertEqual("pitwall-agent-routing setup mcp --harness kimi", kimi["remediation"])
        self.assertNotIn(("channel.registration", "opencode"), checks)
```

Adapt the construction of `run_doctor` inputs to the file's existing helpers if it wraps `run_doctor` (for example a `report(...)` helper); the assertions stay as written.

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_doctor -v 2>&1 | tail -8`
Expected: FAIL with `KeyError: ('channel.mcp_server', None)`.

- [x] **Step 3: Implement** `_probe_channel_server(env, dispatch_id) -> list[str] | None` (the tool names from the `tools/list` response, or None on any failure) and `_channel_checks(env, registry)`, which yields the server check plus one registration check per installed harness in `CHANNEL_HARNESSES` (installation via `resolve_provider_binary`, and `shutil.which("copilot")` for Copilot). Read the registered command through `mcp_registration.current_entry`. Document both checks in `docs/doctor.md`.

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_doctor 2>&1 | tail -3 && .venv/bin/python scripts/pitwall-agent-routing doctor --json > /dev/null; echo "doctor exit=$?"`
Expected: `OK`, then `doctor exit=0` (WARN checks do not fail the command).

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/doctor.py tests/test_doctor.py docs/doctor.md
git commit -s -m "feat(agent-routing): doctor verifies the channel MCP handshake and per-harness registration"
```

## Task 19: Teach the questioning discipline (routing skills and prompting references)

**Files:**
- Modify: `plugins/pitwall/skills/subagent-model-routing/SKILL.md` (new section after `## MCP tools in dispatched CLIs`, ~:770)
- Modify: `plugins/pitwall-codex/skills/subagent-model-routing/SKILL.md` and `plugins/pitwall-copilot/skills/subagent-model-routing/SKILL.md` (new section before `## Prompt Reference Cards`)
- Modify: the three byte-identical `plugins/*/skills/subagent-model-routing/references/model-prompting.md` copies (new section after `## Shared agentic prompt contract`, plus its `## Contents` entry)
- Modify: every `prompting/*.md` family reference except the index, and `prompting/00-prompt-reference-index.md` (Update Checklist)
- Modify: `tests/test_parity.py`

**Interfaces:**
- Every SKILL.md gains `## Asking and steering through the orchestrator channel`. Every `model-prompting.md` copy gains `## Asking through the orchestrator channel` (anchor `#asking-through-the-orchestrator-channel`). Every family reference gains a section of the same name stating its harness tier.

- [x] **Step 1: Write the failing parity test** (add to `ParityTests` in `tests/test_parity.py`)

```python
def test_channel_discipline_is_taught_everywhere(self) -> None:
    skills = {
        "claude": self.claude_skill,
        "codex": CODEX_SKILL.read_text(encoding="utf-8"),
        "copilot": COPILOT_SKILL.read_text(encoding="utf-8"),
    }
    for host, text in skills.items():
        with self.subTest(host=host):
            self.assertIn("## Asking and steering through the orchestrator channel", text)
            for needle in (
                "--routing-ask-support",
                "--routing-workspace isolated",
                "pitwall-agent-routing inbox",
                "pitwall-agent-routing answer",
                "pitwall-agent-routing runs resume",
                "dangerous and irreversible",
            ):
                self.assertIn(needle, text)
    for bundle in ROOT.glob(
        "plugins/*/skills/subagent-model-routing/references/model-prompting.md"
    ):
        self.assertIn(
            "## Asking through the orchestrator channel", bundle.read_text(encoding="utf-8")
        )
    for reference in sorted(ROOT.glob("prompting/*-prompting-reference.md")):
        with self.subTest(reference=reference.name):
            self.assertIn(
                "## Asking through the orchestrator channel", reference.read_text(encoding="utf-8")
            )
```

- [x] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m unittest tests.test_parity -v 2>&1 | tail -5`
Expected: FAIL on the missing section.

- [x] **Step 3: Write the skill section** (identical in all three SKILL.md files except the host note on the first line: the Claude host dispatches through `~/.claude/scripts/<shim>.sh`; Codex and Copilot hosts do too)

```markdown
## Asking and steering through the orchestrator channel

A dispatched model can ask you a blocking question mid-task instead of guessing. Opt in per dispatch with `--routing-ask-support` (cap it with `--routing-max-asks N`; the default is 5) and prefer `--routing-workspace isolated` for ask-prone work, so a paused run resumes against its own worktree.

The dispatcher appends the asking rules to the prompt; you do not write them. The model asks only when its default is dangerous and irreversible, gives 2–8 options with a real default and rationale, points at files instead of pasting diffs, and never asks what the worktree answers. Harnesses with the `pitwall-channel` MCP server registered (`pitwall-agent-routing setup mcp`; the capability inventory shows "Orchestrator channel: tier 1") call `ask_orchestrator` and wait. Every other harness writes `mailbox/asks/NNNN.json` and exits 75, which pauses the run.

Your duties as orchestrator:
- Between dispatch batches, run `pitwall-agent-routing inbox` (or the `inbox` MCP tool). Each ask shows its options, default, remaining deadline, and a `runs diff` command for context.
- Answer routine asks with `pitwall-agent-routing answer <dispatch-id> <ask-id> <choice> --note "<why>"` (or `answer_ask`). Take `schema`, `destructive`, `spend`, and `blocking` asks to the operator.
- Resume a paused run with `pitwall-agent-routing runs resume <dispatch-id>`. Asks past their deadline take their stated default automatically; that is not a failure.
```

- [x] **Step 4: Write the shared prompting section** (all three `model-prompting.md` copies, byte-identical)

```markdown
## Asking through the orchestrator channel

When a dispatch opts into the channel, the dispatcher appends the asking contract; the task prompt only needs to say which decisions count as dangerous or irreversible for this task. Tier 1 harnesses (Claude Code, Codex, OpenCode, Kimi Code, Cline, and Copilot CLI after `pitwall-agent-routing setup mcp`) get the `ask_orchestrator` tool, which blocks until an answer or the deadline. Every other harness uses tier 4: write `mailbox/asks/NNNN.json` and exit 75; the run resumes with the answer appended. Never embed diffs or file contents in an ask; name the files.
```

- [x] **Step 5: Write the family notes.** Add this section to each canonical reference, filling in the harness and tier from the table below:

```markdown
## Asking through the orchestrator channel

Through `<harness>`, this family asks on tier <1|4>. <Tier 1: after `pitwall-agent-routing setup mcp --harness <harness>`, the dispatcher gives the model the `ask_orchestrator` tool and it waits for the answer. | Tier 4: the model writes `mailbox/asks/NNNN.json` and exits 75; the run pauses until the orchestrator answers and runs `pitwall-agent-routing runs resume`.> In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
```

| Reference file | Harness | Tier |
| --- | --- | --- |
| `anthropic-claude-code-`, `anthropic-claude-fable-5-`, `anthropic-claude-opus-4.8-`, `anthropic-claude-sonnet-5-prompting-reference.md` | `claude` (from Codex and Copilot hosts) | 1 |
| `openai-codex-gpt-prompting-reference.md` | `codex` | 1 |
| `kimi-moonshot-prompting-reference.md` | `kimi` | 1 |
| `glm-zhipu-`, `minimax-`, `meituan-longcat-`, `xiaomi-mimo-`, `tencent-hy-prompting-reference.md` | `opencode` | 1 |
| `google-gemini-prompting-reference.md` | `agy` | 4 |
| `xai-grok-prompting-reference.md` | `grok` | 4 |
| `qwen-alibaba-prompting-reference.md` | `qwen` | 4 |
| `meta-muse-code-prompting-reference.md` | `muse` | 4 |
| `meta-muse-glimmer-`, `deepseek-`, `google-gemma-prompting-reference.md` | the route's harness (`route-shim`): tier 1 through `opencode`, tier 4 through `qwen` | 1 or 4 |

Add to the index's Update Checklist: `5. When a harness gains or loses MCP registration support, update the "Asking through the orchestrator channel" tier in every reference that routes through it, and the tier list in model-prompting.md.`

- [x] **Step 6: Validate and commit**

Run:

```bash
.venv/bin/python -m unittest tests.test_parity tests.test_registry tests.test_plugin_identity 2>&1 | tail -3
.venv/bin/python tools/validate_plugins.py
.venv/bin/python tools/check_generated.py
.venv/bin/python tools/check_markdown_links.py
```

Expected: `OK`; `all plugin structures and host-native boundaries are valid`; `generated route assets are current`; `validated N local links …`.

```bash
git add plugins/*/skills/subagent-model-routing/SKILL.md plugins/*/skills/subagent-model-routing/references/model-prompting.md \
  prompting tests/test_parity.py
git commit -s -m "docs(agent-routing): teach the questioning discipline in every routing skill and prompting reference"
```

## Task 20: Phase B documentation and exit check

**Files:**
- Modify: `docs/orchestrator-channel.md` (new "Tier 1: the MCP server" section), `README.md` (Install: `--with-mcp`; Documentation list unchanged)
- Create: `tests/test_channel_tier1_integration.py`

- [x] **Step 1: Write the scripted exit test** (`tests/test_channel_tier1_integration.py`)

```python
"""Phase B exit, scripted: a harness asks through MCP mid-run (plan Task 20)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.shim_test_support import ShimSandbox  # noqa: E402

from model_routing.mcp_registration import plan_registration  # noqa: E402
from model_routing.route_sync import apply_plan  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000e1"
LAUNCHER = ROOT / "scripts" / "pitwall-agent-routing"

# A fake codex that behaves like an MCP-capable harness: it starts the registered
# server with its own environment and calls ask_orchestrator once.
_MCP_HARNESS = """\
#!/usr/bin/env python3
import json, os, subprocess, sys
prompt = sys.stdin.read()
server = subprocess.Popen([sys.executable, {launcher!r}, "mcp"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, env=dict(os.environ))
def send(message):
    server.stdin.write((json.dumps(message) + "\\n").encode())
    server.stdin.flush()
def receive(request_id):
    for line in server.stdout:
        message = json.loads(line)
        if message.get("id") == request_id:
            return message
send({{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {{"protocolVersion": "2025-06-18",
      "capabilities": {{}}, "clientInfo": {{"name": "fake", "version": "0"}}}}}})
receive(1)
send({{"jsonrpc": "2.0", "method": "notifications/initialized"}})
send({{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
      "params": {{"name": "ask_orchestrator", "arguments": json.loads(os.environ["FAKE_ASK"])}}}})
reply = receive(2)
print("tier1-block" if "tier-1 ask tool" in prompt else "no-tier1-block")
print("ANSWER " + json.dumps(reply["result"]["structuredContent"], sort_keys=True), flush=True)
server.stdin.close()
server.wait()
"""


class Tier1IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        target = self.sandbox.bin / "codex"
        target.write_text(_MCP_HARNESS.format(launcher=str(LAUNCHER)), encoding="utf-8")
        target.chmod(0o755)
        env = self.sandbox.environment()
        apply_plan(plan_registration("codex", env, self.sandbox.home, command=str(LAUNCHER)))
        self.run_dir = self.sandbox.state / "subagent-model-routing" / "runs" / DISPATCH_ID

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _start(self, deadline_s: int) -> subprocess.Popen[bytes]:
        ask = {
            "question": "Which suffix?",
            "blocked_on": "naming",
            "default": "a",
            "deadline_s": deadline_s,
            "options": [{"id": "a", "text": "-alpha"}, {"id": "b", "text": "-beta"}],
        }
        return subprocess.Popen(
            [
                sys.executable,
                str(LAUNCHER),
                "dispatch",
                "codex",
                str(self.sandbox.prompt()),
                "--routing-ask-support",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.sandbox.environment(
                SUBAGENT_MODEL_ROUTING_DISPATCH_ID=DISPATCH_ID, FAKE_ASK=json.dumps(ask)
            ),
        )

    def _finished_row(self) -> dict:
        return [row for row in self.sandbox.ledger_records() if row.get("event") == "finished"][-1]

    def test_operator_answer_reaches_a_running_subagent(self) -> None:
        process = self._start(600)
        ask_file = self.run_dir / "mailbox" / "asks" / "0001.json"
        deadline = time.monotonic() + 30
        while not ask_file.exists():
            self.assertLess(time.monotonic(), deadline, "the harness never asked")
            time.sleep(0.1)
        answered = subprocess.run(
            [sys.executable, str(LAUNCHER), "answer", DISPATCH_ID, "0001", "b", "--note", "beta"],
            capture_output=True,
            env=self.sandbox.environment(),
            check=False,
        )
        self.assertEqual(0, answered.returncode, answered.stderr)
        out, err = process.communicate(timeout=60)
        self.assertEqual(0, process.returncode, err)
        self.assertIn(b"tier1-block", out)
        self.assertIn(b'"choice": "b"', out)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=0\n"), out[-200:])
        self.assertFalse((self.run_dir / "pause.json").exists())
        row = self._finished_row()
        self.assertEqual((1, ["0001:operator"]), (row["askCount"], row["askResolutions"]))

    def test_unanswered_ask_resolves_to_its_default(self) -> None:
        process = self._start(1)
        out, err = process.communicate(timeout=60)
        self.assertEqual(0, process.returncode, err)
        self.assertIn(b'"resolved_by": "default"', out)
        self.assertEqual(["0001:default"], self._finished_row()["askResolutions"])


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it**

Run: `.venv/bin/python -m unittest tests.test_channel_tier1_integration -v 2>&1 | tail -5`
Expected: `OK` (two tests). If it fails, fix the defect in the owning task's module in this task; do not weaken the assertions.

- [x] **Step 3: Document tier 1.** In `docs/orchestrator-channel.md`, add: the five tools and their roles; the registration table from Task 16 with each harness's environment-forwarding and timeout keys; `setup mcp` and `install.sh --with-mcp`; the channel environment variables; the doctor checks; and the fallback rule (no ask tool in a session → the tier-4 file contract, which every channel prompt carries). In the README Install section, show `scripts/install.sh --with-mcp`.

- [x] **Step 4: Full validation**

```bash
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check runtime tests tools scripts/pitwall-agent-routing
.venv/bin/mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing
.venv/bin/python tools/check_markdown_links.py
```

Expected: at least 596 tests and `OK`; ruff and mypy clean; links valid.

- [x] **Step 5: Commit**

```bash
git add docs/orchestrator-channel.md README.md tests/test_channel_tier1_integration.py
git commit -s -m "docs(agent-routing): tier-1 channel guide; scripted Phase B exit test"
```

- [x] **Step 6: Live Phase B exit check (operator-run; spec §13 Phase B exit).** Run from a terminal outside any Claude Code session. It spends one Claude Code dispatch per check.

```bash
pitwall-agent-routing setup mcp --yes
pitwall-agent-routing doctor --json | python3 -c 'import json,sys; [print(c["id"], c.get("provider"), c["status"]) for c in json.load(sys.stdin)["checks"] if c["category"] == "channel"]'
WORK="$(mktemp -d)" && cd "$WORK" && git init -q . && git commit -q --allow-empty -m seed
cat > prompt.md <<'EOF'
Create the file channel-live.txt in the current directory. Before writing it, call the
ask_orchestrator tool exactly once with blocked_on "naming", question "Which filename suffix?",
options [{"id":"a","text":"-alpha"},{"id":"b","text":"-beta"}], default "a", and
default_rationale "alpha is the conventional first suffix". Write the chosen option's text
into the file, then stop.
EOF
env -u CLAUDECODE pitwall-agent-routing dispatch claude prompt.md --routing-ask-support > run.log 2>&1 &
# once `pitwall-agent-routing inbox` lists the ask:
pitwall-agent-routing answer <dispatch-id> 0001 b --note "live check"
wait; tail -1 run.log; cat channel-live.txt
```

Expected: every `channel.*` check prints `PASS` for installed harnesses; `run.log` ends with `SHIM-DONE exit=0`; `channel-live.txt` contains `-beta`; the ledger's last `finished` row has `"askResolutions": ["0001:operator"]`. Repeat with `"deadline_s": 60` added to the ask and no answer: the file contains `-alpha` and the row shows `["0001:default"]`. Repeat the answered check for each other installed channel harness (`dispatch codex`, `dispatch opencode zai-coding-plan/glm-5.3`, `dispatch kimi`, `dispatch cline`). A run that pauses with exit 75 instead of asking through the tool means that harness did not expose `ask_orchestrator`; fix its registration entry in `mcp_registration.py` (Task 16's table) and re-run until it asks through the tool.

---

# Part 3 — Phase C: steering

## Task 21: Operator steering verbs; steers delivered on resume

**Files:**
- Modify: `runtime/model_routing/channel.py` (add `send_steer`)
- Modify: `runtime/model_routing/cli.py` (new `steer` subcommand; `runs stop`)
- Modify: `runtime/model_routing/dispatch.py` (`build_resume_prompt` :559 and `resume_legacy` :1048 deliver unacked steers)
- Create: `tests/test_steer_cli.py`

**Interfaces:**
- Produces: `channel.send_steer(env, dispatch_id, *, kind: str, message: str, requires_ack: bool, deadline_s: int, provider: str) -> dict[str, Any]`. It refuses a run in a terminal state (`FileNotFoundError` for unknown runs, `MailboxError` for a terminal run: `run <id> is <state>; steering applies to running or paused runs`), writes the steer, and emits `steer.sent` with `{"steerId", "kind"}`.
- CLI: `pitwall-agent-routing steer <dispatch-id> --kind note|scope|budget|priority|stop --message TEXT [--no-ack] [--deadline SECONDS] [--json]` (deadline default 300, range 1–3600). `pitwall-agent-routing runs stop <dispatch-id> [--message TEXT] [--grace SECONDS]` writes a `stop` steer with `requires_ack=true` and `deadline_s=grace` (default 60) and prints `stop requested: steer <id>; the run is aborted if it has not wrapped up within <grace>s`. `runs stop` refuses a paused run: `run <id> is paused; nothing is running (resume it or discard it)`.
- Spec §7 tier-4 steering ("advisory prompt-prefix on resume"): `build_resume_prompt(original, resolved, attempt, steers=())` appends `# Orchestrator steering (delivered on resume)` with each unacked steer; `resume_legacy` acks each delivered steer with note `delivered on resume` and emits `steer.acked`.

- [x] **Step 1: Write the failing tests** (`tests/test_steer_cli.py`)

```python
"""steer and runs stop verbs; steering delivered on resume (plan Task 21)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.dispatch import build_resume_prompt  # noqa: E402
from model_routing.run_store import RunStore  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000f1"
CLI = [sys.executable, str(ROOT / "scripts/pitwall-agent-routing")]


class SteerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.env = {
            "HOME": str(root),
            "XDG_STATE_HOME": str(root / "state"),
            "PATH": "/usr/bin:/bin",
        }
        self.store = RunStore(root / "state" / "subagent-model-routing", DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self._state("running")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _state(self, state: str) -> None:
        self.store.write_json(
            "run.json",
            {
                "schemaVersion": 1,
                "dispatchId": DISPATCH_ID,
                "state": state,
                "provider": "codex",
                "model": "m",
                "attempt": 1,
            },
        )

    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*CLI, *args], capture_output=True, text=True, env=self.env, check=False
        )

    def test_steer_writes_and_emits_sent(self) -> None:
        result = self._cli(
            "steer", DISPATCH_ID, "--kind", "scope", "--message", "SQLite only", "--json"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        steer = json.loads(result.stdout)
        self.assertEqual(
            ("0001", "scope", True, 300),
            (steer["steer_id"], steer["kind"], steer["requires_ack"], steer["deadline_s"]),
        )
        events = [
            json.loads(line)
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [{"steerId": "0001", "kind": "scope"}],
            [e["data"] for e in events if e["event"] == "steer.sent"],
        )
        note = json.loads(
            self._cli(
                "steer", DISPATCH_ID, "--kind", "note", "--message", "fyi", "--no-ack", "--json"
            ).stdout
        )
        self.assertFalse(note["requires_ack"])

    def test_terminal_and_paused_runs_are_refused(self) -> None:
        self._state("succeeded")
        refused = self._cli("steer", DISPATCH_ID, "--kind", "note", "--message", "late")
        self.assertEqual(1, refused.returncode)
        self.assertIn("is succeeded", refused.stderr)
        self._state("paused")
        self.assertEqual(
            0,
            self._cli("steer", DISPATCH_ID, "--kind", "scope", "--message", "on resume").returncode,
        )
        stop = self._cli("runs", "stop", DISPATCH_ID)
        self.assertEqual(1, stop.returncode)
        self.assertIn("is paused", stop.stderr)

    def test_runs_stop_writes_a_stop_steer_with_the_grace_window(self) -> None:
        result = self._cli("runs", "stop", DISPATCH_ID, "--grace", "5")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("stop requested: steer 0001", result.stdout)
        steer = self.store.mailbox().steers()[0]
        self.assertEqual(
            ("stop", True, 5), (steer["kind"], steer["requires_ack"], steer["deadline_s"])
        )

    def test_resume_prompt_carries_undelivered_steering(self) -> None:
        steer = {"steer_id": "0002", "kind": "scope", "message": "SQLite only"}
        rebuilt = build_resume_prompt(b"original\n", [], 2, steers=[steer])
        self.assertIn(b"# Orchestrator steering (delivered on resume)", rebuilt)
        self.assertIn(b"[scope 0002] SQLite only", rebuilt)


if __name__ == "__main__":
    unittest.main()
```

Add to `tests/test_runs_resume.py`: a steer written while the run is paused appears in the rebuilt prompt, and after resume it has an ack whose note is `delivered on resume`.

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_steer_cli -v 2>&1 | tail -8`
Expected: FAIL with `invalid choice: 'steer'`.

- [x] **Step 3: Implement**

```python
_STEERABLE_STATES = {
    "created",
    "preflighting",
    "ready",
    "workspace_preparing",
    "workspace_ready",
    "running",
    "paused",
}


def send_steer(
    env: Mapping[str, str],
    dispatch_id: str,
    *,
    kind: str,
    message: str,
    requires_ack: bool,
    deadline_s: int,
    provider: str,
) -> dict[str, Any]:
    """Write one STEER for a live or paused run and emit ``steer.sent``."""
    run_path = find_run(env, dispatch_id)
    try:
        state = json.loads((run_path / "run.json").read_text(encoding="utf-8")).get("state")
    except OSError, json.JSONDecodeError:
        state = None
    if state not in _STEERABLE_STATES:
        raise MailboxError(
            f"run {run_path.name} is {state}; steering applies to running or paused runs"
        )
    store = RunStore(state_root(env), run_path.name)
    steer = store.mailbox().write_steer(
        kind=kind, message=message, requires_ack=requires_ack, deadline_s=deadline_s
    )
    EventEmitter(store, provider=provider, model="channel", callback=HookRunner(env)).emit(
        "steer.sent", {"steerId": steer["steer_id"], "kind": kind}
    )
    return steer
```

`_steer` and `_runs_stop` in `cli.py` wrap `send_steer` (catch `FileNotFoundError`/`MailboxError`, exit 1). `_runs_stop` checks `run.json` for `paused` first. `build_resume_prompt` gains `steers: Sequence[Mapping[str, Any]] = ()` and, when non-empty, appends a `# Orchestrator steering (delivered on resume)` block with one `- [<kind> <steer_id>] <message>` line per steer, then `Apply these directives before continuing.` `resume_legacy` passes the unacked steers (`[s for s in box.steers() if s["steer_id"] not in acked]`) and, after writing the rebuilt prompt, acks each with note `delivered on resume` and emits `steer.acked`.

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_steer_cli tests.test_runs_resume tests.test_channel_expiry 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/channel.py runtime/model_routing/cli.py runtime/model_routing/dispatch.py \
  tests/test_steer_cli.py tests/test_runs_resume.py
git commit -s -m "feat(agent-routing): steer and runs stop verbs; steering delivered on resume"
```

## Task 22: Graceful abort in the `_shim` supervisor

**Files:**
- Modify: `runtime/model_routing/process.py` (`ProcessResult` :21, `_terminate_group` :115, `run_process` :225)
- Modify: `runtime/model_routing/channel.py` (add `SteerWatcher`)
- Modify: `runtime/model_routing/dispatch.py` (split `dispatch_legacy` into a signal-handling wrapper and `_dispatch_legacy`; run-state mapping; ledger outcome; `abort.json`)
- Modify: `tests/fixtures/fake_provider.py` (`FAKE_IGNORE_TERM=1` ignores SIGTERM), `tests/test_process.py`
- Create: `tests/test_graceful_abort.py`

**Interfaces:**
- `ProcessResult` gains `aborted: bool = False` and `killed: bool = False` (defaults keep the positional constructor at `dispatch.py` working). `_terminate_group(process, grace_seconds) -> bool` returns True when SIGKILL was needed. `run_process(..., abort_event: threading.Event | None = None, abort_grace_seconds: float = 10.0, watch: Callable[[], None] | None = None)`: `watch` runs about once a second; when `abort_event` is set, the group gets SIGTERM, then SIGKILL after the grace period. Exit codes follow the existing formula: 143 after SIGTERM, 137 after SIGKILL.
- `channel.SteerWatcher(store, emitter, abort_event)` is callable. Each call reads the mailbox without rewriting `mailbox.json`, emits `steer.unacked` once per `requires_ack` steer past its deadline, and sets `abort_event` (recording `reason`) when a `stop` steer's window has passed: from `created_at` while unacked, from the ack time once acked.
- `dispatch_legacy` installs SIGTERM and SIGHUP handlers that only set the abort event (main thread only), restores the previous handlers on return, and passes `SUBAGENT_MODEL_ROUTING_ABORT_GRACE_SECS` (default 10) as the grace. An aborted attempt finishes as state `cancelled`, event `dispatch.cancelled`, ledger outcome `killed` or `cancelled`, ledger field `abortReason`, and an `abort.json` artifact `{schemaVersion, reason, signal, killed, graceSeconds}`. The sentinel is always emitted. An abort signal that arrives before the child starts skips the spawn and finishes the same way.

- [x] **Step 1: Write the failing tests**

`tests/test_process.py` additions:

```python
def test_abort_event_terminates_the_group_gracefully(self) -> None:
    abort = threading.Event()
    threading.Timer(0.3, abort.set).start()
    calls: list[float] = []
    result = run_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env=dict(os.environ),
        stdin=None,
        stdout_path=self.root / "o",
        stderr_path=self.root / "e",
        timeout_seconds=60,
        cwd=self.root,
        abort_event=abort,
        abort_grace_seconds=5,
        watch=lambda: calls.append(1),
    )
    self.assertEqual((True, False, 143), (result.aborted, result.killed, result.exit_code))


def test_abort_escalates_to_sigkill_after_the_grace(self) -> None:
    abort = threading.Event()
    abort.set()
    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
    result = run_process(
        [sys.executable, "-c", code],
        env=dict(os.environ),
        stdin=None,
        stdout_path=self.root / "o",
        stderr_path=self.root / "e",
        timeout_seconds=60,
        cwd=self.root,
        abort_event=abort,
        abort_grace_seconds=0.5,
    )
    self.assertEqual((True, True, 137), (result.aborted, result.killed, result.exit_code))
```

(Use the file's existing temporary-directory fixture for `self.root`; add it if absent.)

`tests/test_graceful_abort.py`:

```python
"""Graceful abort: SIGTERM and ignored stop steers end runs with a receipt (plan Task 22)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.shim_test_support import ShimSandbox  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000f2"
LAUNCHER = ROOT / "scripts" / "pitwall-agent-routing"


class GracefulAbortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.sandbox.install_provider("codex")
        self.run_dir = self.sandbox.state / "subagent-model-routing" / "runs" / DISPATCH_ID
        self.pid_file = self.sandbox.root / "child.pid"

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _start(self, **env: str) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, str(LAUNCHER), "dispatch", "codex", str(self.sandbox.prompt())],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.sandbox.environment(
                SUBAGENT_MODEL_ROUTING_DISPATCH_ID=DISPATCH_ID,
                FAKE_SLEEP_SECS="30",
                FAKE_PID_FILE=str(self.pid_file),
                **env,
            ),
        )
        deadline = time.monotonic() + 30
        while not self.pid_file.exists():
            self.assertLess(time.monotonic(), deadline, "child never started")
            time.sleep(0.05)
        return process

    def _finished(self) -> dict:
        return [row for row in self.sandbox.ledger_records() if row.get("event") == "finished"][-1]

    def test_sigterm_to_the_supervisor_ends_with_a_receipt(self) -> None:
        process = self._start()
        process.send_signal(signal.SIGTERM)
        out, _err = process.communicate(timeout=30)
        self.assertEqual(143, process.returncode)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=143\n"), out[-200:])
        self.assertEqual(
            "cancelled", json.loads((self.run_dir / "result.json").read_text())["status"]
        )
        self.assertEqual(
            ("cancelled", "signal SIGTERM"),
            (self._finished()["outcome"], self._finished()["abortReason"]),
        )
        with self.assertRaises(ProcessLookupError):
            os.kill(int(self.pid_file.read_text()), 0)

    def test_a_child_that_ignores_sigterm_is_killed_after_the_grace(self) -> None:
        process = self._start(FAKE_IGNORE_TERM="1", SUBAGENT_MODEL_ROUTING_ABORT_GRACE_SECS="1")
        process.send_signal(signal.SIGTERM)
        out, _err = process.communicate(timeout=30)
        self.assertEqual(137, process.returncode)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=137\n"))
        self.assertEqual("killed", self._finished()["outcome"])
        self.assertTrue(json.loads((self.run_dir / "abort.json").read_text())["killed"])

    def test_an_ignored_stop_steer_aborts_after_its_window(self) -> None:
        process = self._start()
        subprocess.run(
            [sys.executable, str(LAUNCHER), "runs", "stop", DISPATCH_ID, "--grace", "1"],
            env=self.sandbox.environment(),
            check=True,
            capture_output=True,
        )
        out, _err = process.communicate(timeout=30)
        self.assertEqual(143, process.returncode)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=143\n"))
        self.assertEqual("stop steer 0001 not honored within 1s", self._finished()["abortReason"])
        events = [
            json.loads(line)["event"]
            for line in (self.run_dir / "events.jsonl").read_text().splitlines()
        ]
        self.assertIn("steer.unacked", events)


if __name__ == "__main__":
    unittest.main()
```

Confirm the fixture already writes `FAKE_PID_FILE` (it reads `FAKE_PID_FILE` at `tests/fixtures/fake_provider.py:34`) and add `FAKE_IGNORE_TERM`.

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_process tests.test_graceful_abort -v 2>&1 | tail -10`
Expected: FAIL — `run_process() got an unexpected keyword argument 'abort_event'`; the SIGTERM test sees the supervisor die with no sentinel.

- [x] **Step 3: Implement `process.py`**

```python
def _terminate_group(process: subprocess.Popen[bytes], grace_seconds: float = 2.0) -> bool:
    """SIGTERM the group, wait up to *grace_seconds*, then SIGKILL; True when SIGKILL was sent."""
    if process.poll() is not None:
        return False
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + grace_seconds
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return False
        return True
    return False
```

In `run_process`, extend the poll loop:

```python
    aborted = False
    killed = False
    next_watch = started
    try:
        while process.poll() is None:
            now = time.monotonic()
            if now - started >= timeout_seconds:
                timed_out = True
                _terminate_group(process)
                break
            if abort_event is not None and abort_event.is_set():
                aborted = True
                killed = _terminate_group(process, abort_grace_seconds)
                break
            if watch is not None and now >= next_watch:
                watch()
                next_watch = now + 1.0
            time.sleep(0.02)
    except KeyboardInterrupt:
        cancelled = True
        _terminate_group(process)
```

and return `aborted=aborted, killed=killed` in the `ProcessResult`.

- [x] **Step 4: Implement `SteerWatcher` in `channel.py`**

```python
import threading


class SteerWatcher:
    """Polled by the supervisor: flags overdue steers and enforces ignored stop steers."""

    def __init__(self, store: RunStore, emitter: EventEmitter, abort: threading.Event) -> None:
        self.store = store
        self.emitter = emitter
        self.abort = abort
        self.reason: str | None = None
        self._reported: set[str] = set()

    def __call__(self) -> None:
        box = Mailbox(
            self.store.path, self.store.dispatch_id
        )  # plain reader: no mailbox.json rewrite per poll
        if not (box.root / "steer").is_dir():
            return
        now = time.time()
        acked_at: dict[str, float] = {}
        for ack in box.acks():
            moment = epoch_of(ack.get("acked_at"))
            if moment is not None:
                acked_at[ack["steer_id"]] = min(moment, acked_at.get(ack["steer_id"], moment))
        for steer in box.steers():
            steer_id = steer["steer_id"]
            created = epoch_of(steer.get("created_at")) or now
            ack = acked_at.get(steer_id)
            if (
                steer["requires_ack"]
                and ack is None
                and now > created + steer["deadline_s"]
                and steer_id not in self._reported
            ):
                self._reported.add(steer_id)
                self.emitter.emit("steer.unacked", {"steerId": steer_id, "kind": steer["kind"]})
            if steer["kind"] == "stop" and not self.abort.is_set():
                window_start = ack if ack is not None else created
                if now > window_start + steer["deadline_s"]:
                    self.reason = f"stop steer {steer_id} not honored within {steer['deadline_s']}s"
                    self.abort.set()
```

(`Mailbox` is imported from `.mailbox` in `channel.py`.)

- [x] **Step 5: Wire it into `dispatch.py`**

```python
def dispatch_legacy(
    provider_id: str,
    argv: list[str],
    *,
    environ: Mapping[str, str] | None = None,
    route: "ResolvedRoute | None" = None,
) -> int:
    """Supervisor entry point: SIGTERM/SIGHUP request a graceful abort instead of killing the supervisor."""
    abort = threading.Event()
    reasons: list[str] = []

    def request_abort(signum: int, _frame: object) -> None:
        if not abort.is_set():
            reasons.append(f"signal {signal.Signals(signum).name}")
        abort.set()

    previous: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, request_abort)
    try:
        return _dispatch_legacy(
            provider_id, argv, environ=environ, route=route, abort=abort, abort_reasons=reasons
        )
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
```

`_dispatch_legacy` is the current body with two additions:
1. Before `run_process`: build `watcher = SteerWatcher(store, emitter, abort)`; if `abort.is_set()`, skip the spawn and use `ProcessResult(143, int(signal.SIGTERM), False, False, 0, 0, 0, aborted=True)`; otherwise call `run_process(..., abort_event=abort, abort_grace_seconds=grace, watch=watcher)` with `grace` parsed from `SUBAGENT_MODEL_ROUTING_ABORT_GRACE_SECS` (default 10, positive float).
2. First branch of the state mapping: `if process_result.aborted: state, outcome, terminal_event = "cancelled", "cancelled", "dispatch.cancelled"`. The finished ledger row uses `outcome="killed" if process_result.killed else "cancelled"` when aborted and adds `abortReason` (the watcher's `reason`, else the first signal reason). Write `abort.json` before `result.json`. The pause classification is unaffected, because it only applies to `failed` attempts.

- [x] **Step 6: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_process tests.test_graceful_abort tests.test_shim_contract tests.test_dispatch_contract tests.test_pause_contract tests.test_runs_resume 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 7: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/process.py runtime/model_routing/channel.py runtime/model_routing/dispatch.py \
  tests/fixtures/fake_provider.py tests/test_process.py tests/test_graceful_abort.py
git commit -s -m "feat(agent-routing): graceful abort with a receipt on SIGTERM and ignored stop steers"
```

## Task 23: Tier-2 hook gate (Claude Code and Codex bundles)

**Files:**
- Create: `runtime/model_routing/steer_gate.py`, `plugins/pitwall/hooks/steer-gate.py`, `plugins/pitwall-codex/hooks/steer-gate.py` (byte-identical), `plugins/pitwall-codex/hooks/hooks.json`, `tests/test_steer_gate.py`
- Modify: `plugins/pitwall/hooks/hooks.json` (add `PreToolUse`), `plugins/pitwall-codex/.codex-plugin/plugin.json` (add `"hooks": "./hooks/hooks.json"`, following the existing explicit `"skills": "./skills/"`), `runtime/model_routing/cli.py` (hidden `_steer-gate`), `plugins/pitwall-codex/README.md` (the package now ships one hook)

**Interfaces:**
- `steer_gate.decide(hook_input: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any] | None`. It returns a PreToolUse deny document, or None to allow. It allows when there is no valid channel dispatch id, when the tool is a channel tool (name contains `pitwall-channel` and ends with `read_steering`, `ack_steer`, or `ask_orchestrator`), when the run's `channel.json` is missing or not tier 1 (without the ack tool the gate would deadlock), or when no unacked steer of kind `scope`, `priority`, or `stop` exists.
- Deny document (same contract for Claude Code and Codex; the Codex binary implements `hookSpecificOutput.permissionDecision` and sets `CLAUDE_PLUGIN_ROOT` for plugin hooks):

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": "Orchestrator steering needs acknowledgement before any other tool call: [scope 0002] SQLite only. Call read_steering, apply the direction, then ack_steer with steer_id 0002."}}
```

- `pitwall-agent-routing _steer-gate` reads at most 1 MiB of hook JSON from stdin, prints the decision when there is one, and always exits 0. Any exception is reported on stderr and allows the call (fail-open: a broken gate must not wedge a run).
- The plugin wrapper execs `pitwall-agent-routing _steer-gate` when the channel variable is set and the command is on `PATH`; otherwise it exits 0 silently.

- [x] **Step 1: Write the failing tests** (`tests/test_steer_gate.py`)

```python
"""Tier-2 PreToolUse steering gate (plan Task 23)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.channel import ChannelConfig, write_channel_config  # noqa: E402
from model_routing.run_store import RunStore  # noqa: E402
from model_routing.steer_gate import decide  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000f3"
WRAPPERS = [
    ROOT / "plugins/pitwall/hooks/steer-gate.py",
    ROOT / "plugins/pitwall-codex/hooks/steer-gate.py",
]


class GateDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.state = Path(self._temp.name) / "state"
        self.store = RunStore(self.state, DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, tier="1"))
        self.env = {
            "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID": DISPATCH_ID,
            "SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT": str(self.state),
        }

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_blocking_steer_denies_other_tools_until_acked(self) -> None:
        steer = self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        decision = decide({"tool_name": "Bash", "tool_input": {}}, self.env)
        assert decision is not None
        output = decision["hookSpecificOutput"]
        self.assertEqual(
            ("PreToolUse", "deny"), (output["hookEventName"], output["permissionDecision"])
        )
        self.assertIn("[scope 0001] SQLite only", output["permissionDecisionReason"])
        self.assertIsNone(decide({"tool_name": "mcp__pitwall-channel__ack_steer"}, self.env))
        self.assertIsNone(decide({"tool_name": "mcp__pitwall-channel__read_steering"}, self.env))
        self.store.mailbox().write_ack(steer["steer_id"])
        self.assertIsNone(decide({"tool_name": "Bash"}, self.env))

    def test_allows_when_the_gate_does_not_apply(self) -> None:
        self.store.mailbox().write_steer(kind="note", message="fyi")
        self.assertIsNone(decide({"tool_name": "Bash"}, self.env))  # advisory kinds never block
        self.store.mailbox().write_steer(kind="scope", message="narrow")
        self.assertIsNone(decide({"tool_name": "Bash"}, {}))  # not a dispatched harness
        self.assertIsNone(
            decide(
                {"tool_name": "Bash"},
                {**self.env, "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID": "x"},
            )
        )
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, tier="4"))
        self.assertIsNone(decide({"tool_name": "Bash"}, self.env))  # no ack tool, so no gate


class GateCommandTests(unittest.TestCase):
    def test_cli_prints_the_decision_and_fails_open(self) -> None:
        launcher = [sys.executable, str(ROOT / "scripts/pitwall-agent-routing"), "_steer-gate"]
        garbage = subprocess.run(
            launcher,
            input=b"{not json",
            capture_output=True,
            env={"PATH": "/usr/bin:/bin"},
            check=False,
        )
        self.assertEqual((0, b""), (garbage.returncode, garbage.stdout))

    def test_wrappers_are_identical_and_fail_open_without_the_command(self) -> None:
        self.assertEqual(WRAPPERS[0].read_bytes(), WRAPPERS[1].read_bytes())
        result = subprocess.run(
            [sys.executable, str(WRAPPERS[0])],
            input=b"{}",
            capture_output=True,
            env={"PATH": "/nonexistent", "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID": DISPATCH_ID},
            check=False,
        )
        self.assertEqual((0, b""), (result.returncode, result.stdout))

    def test_both_bundles_register_the_pre_tool_use_gate(self) -> None:
        for hooks in (
            ROOT / "plugins/pitwall/hooks/hooks.json",
            ROOT / "plugins/pitwall-codex/hooks/hooks.json",
        ):
            entries = json.loads(hooks.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
            commands = [h["command"] for entry in entries for h in entry["hooks"]]
            self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/steer-gate.py"', commands)
        manifest = json.loads(
            (ROOT / "plugins/pitwall-codex/.codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual("./hooks/hooks.json", manifest["hooks"])


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_steer_gate -v 2>&1 | tail -8`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_routing.steer_gate'`.

- [x] **Step 3: Implement `steer_gate.py`**

```python
"""Tier-2 steering gate: deny tool calls while a blocking steer waits for acknowledgement (spec §8.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import uuid

from .channel import CHANNEL_DISPATCH_ENV, CHANNEL_STATE_ROOT_ENV, load_channel_config
from .mailbox import Mailbox
from .run_store import state_root


BLOCKING_KINDS = frozenset({"scope", "priority", "stop"})
CHANNEL_TOOLS = ("read_steering", "ack_steer", "ask_orchestrator")


def is_channel_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return "pitwall-channel" in lowered and lowered.endswith(CHANNEL_TOOLS)


def decide(hook_input: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any] | None:
    dispatch_id = env.get(CHANNEL_DISPATCH_ENV, "")
    try:
        uuid.UUID(dispatch_id)
    except ValueError:
        return None
    if is_channel_tool(str(hook_input.get("tool_name", ""))):
        return None
    root = Path(env[CHANNEL_STATE_ROOT_ENV]) if env.get(CHANNEL_STATE_ROOT_ENV) else state_root(env)
    run_dir = root / "runs" / dispatch_id
    config = load_channel_config(run_dir)
    if config is None or config.tier != "1":
        return None
    blocking = [
        s for s in Mailbox(run_dir, dispatch_id).unacked_steers() if s["kind"] in BLOCKING_KINDS
    ]
    if not blocking:
        return None
    directives = " | ".join(f"[{s['kind']} {s['steer_id']}] {s['message']}" for s in blocking)
    reason = (
        "Orchestrator steering needs acknowledgement before any other tool call: "
        f"{directives}. Call read_steering, apply the direction, then ack_steer with steer_id {blocking[0]['steer_id']}."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
```

The wrapper (`plugins/pitwall/hooks/steer-gate.py`, copied byte-for-byte to the Codex bundle):

```python
#!/usr/bin/env python3
"""PreToolUse tier-2 steering gate: defer to `pitwall-agent-routing _steer-gate`; fail open."""

import os
import shutil
import subprocess
import sys


def main() -> int:
    if not os.environ.get("SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID"):
        return 0
    command = shutil.which("pitwall-agent-routing")
    if command is None:
        return 0
    payload = sys.stdin.buffer.read(1024 * 1024)
    try:
        completed = subprocess.run(
            [command, "_steer-gate"], input=payload, capture_output=True, timeout=10, check=False
        )
    except OSError, subprocess.TimeoutExpired:
        return 0
    if completed.returncode == 0:
        sys.stdout.buffer.write(completed.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Hook entry for both `hooks.json` files (keep the Claude bundle's two `Stop` entries unchanged):

```json
"PreToolUse": [
  {
    "matcher": "*",
    "hooks": [
      {"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/steer-gate.py\"", "timeout": 15,
       "statusMessage": "orchestrator steering gate"}
    ]
  }
]
```

`cli.py`: add a hidden `_steer-gate` parser (`add_help=False`, like `_shim`); the handler reads stdin, calls `decide(json.loads(raw), os.environ)` inside `try/except Exception` (print the exception to stderr), prints `json.dumps(decision)` when not None, and returns 0.

- [x] **Step 4: Run the tests and the plugin validators**

Run: `.venv/bin/python -m unittest tests.test_steer_gate tests.test_claude_tripwires tests.test_plugin_identity tests.test_marketplaces 2>&1 | tail -3 && .venv/bin/python tools/validate_plugins.py`
Expected: `OK`, then `all plugin structures and host-native boundaries are valid`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/steer_gate.py runtime/model_routing/cli.py plugins/pitwall/hooks plugins/pitwall-codex/hooks \
  plugins/pitwall-codex/.codex-plugin/plugin.json plugins/pitwall-codex/README.md tests/test_steer_gate.py
git commit -s -m "feat(agent-routing): tier-2 PreToolUse steering gate for the Claude Code and Codex bundles"
```

## Task 24: Phase C documentation and exit check

**Files:**
- Modify: `docs/orchestrator-channel.md` (Steering section), `docs/lifecycle-hooks.md` (`steer.sent`, `steer.unacked`, `dispatch.cancelled` with `abortReason`), the three SKILL.md files (append the steering duties), `plugins/pitwall/commands/distill.md` (outcomes `cancelled` and `killed` are operator aborts, not model failures)
- Create: `tests/test_channel_steer_integration.py`

- [x] **Step 1: Write the scripted exit test** (`tests/test_channel_steer_integration.py`)

```python
"""Phase C exit, scripted: a scope change and a stop land in a running dispatch (plan Task 24)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.shim_test_support import ShimSandbox  # noqa: E402

from model_routing.mcp_registration import plan_registration  # noqa: E402
from model_routing.route_sync import apply_plan  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000e2"
LAUNCHER = ROOT / "scripts" / "pitwall-agent-routing"

# A fake MCP-capable codex that polls read_steering and obeys the first scope or stop directive.
_STEERED_HARNESS = """\
#!/usr/bin/env python3
import json, os, subprocess, sys, time
sys.stdin.read()
server = subprocess.Popen([sys.executable, {launcher!r}, "mcp"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, env=dict(os.environ))
counter = [0]
def call(method, params=None):
    counter[0] += 1
    message = {{"jsonrpc": "2.0", "id": counter[0], "method": method}}
    if params is not None:
        message["params"] = params
    server.stdin.write((json.dumps(message) + "\\n").encode())
    server.stdin.flush()
    for line in server.stdout:
        reply = json.loads(line)
        if reply.get("id") == counter[0]:
            return reply
call("initialize", {{"protocolVersion": "2025-06-18", "capabilities": {{}},
                     "clientInfo": {{"name": "fake", "version": "0"}}}})
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    reply = call("tools/call", {{"name": "read_steering", "arguments": {{}}}})
    for steer in reply["result"]["structuredContent"]["steers"]:
        if steer["kind"] in ("scope", "stop"):
            call("tools/call", {{"name": "ack_steer", "arguments": {{"steer_id": steer["steer_id"]}}}})
            print("stopping" if steer["kind"] == "stop" else "scope: " + steer["message"], flush=True)
            server.stdin.close()
            server.wait()
            sys.exit(0)
    time.sleep(0.2)
sys.exit(1)
"""


class SteerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        target = self.sandbox.bin / "codex"
        target.write_text(_STEERED_HARNESS.format(launcher=str(LAUNCHER)), encoding="utf-8")
        target.chmod(0o755)
        apply_plan(
            plan_registration(
                "codex", self.sandbox.environment(), self.sandbox.home, command=str(LAUNCHER)
            )
        )
        self.run_dir = self.sandbox.state / "subagent-model-routing" / "runs" / DISPATCH_ID

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _start(self) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [
                sys.executable,
                str(LAUNCHER),
                "dispatch",
                "codex",
                str(self.sandbox.prompt()),
                "--routing-ask-support",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.sandbox.environment(SUBAGENT_MODEL_ROUTING_DISPATCH_ID=DISPATCH_ID),
        )
        deadline = time.monotonic() + 30
        while True:
            self.assertLess(time.monotonic(), deadline, "the dispatch never reached running")
            try:
                if json.loads((self.run_dir / "run.json").read_text())["state"] == "running":
                    return process
            except OSError, json.JSONDecodeError, KeyError:
                pass
            time.sleep(0.05)

    def _cli(self, *args: str) -> None:
        subprocess.run(
            [sys.executable, str(LAUNCHER), *args],
            env=self.sandbox.environment(),
            check=True,
            capture_output=True,
        )

    def test_scope_change_lands_without_killing_the_run(self) -> None:
        process = self._start()
        self._cli("steer", DISPATCH_ID, "--kind", "scope", "--message", "SQLite only")
        out, err = process.communicate(timeout=60)
        self.assertEqual(0, process.returncode, err)
        self.assertIn(b"scope: SQLite only", out)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=0\n"), out[-200:])
        self.assertEqual(
            "succeeded", json.loads((self.run_dir / "result.json").read_text())["status"]
        )
        row = [r for r in self.sandbox.ledger_records() if r.get("event") == "finished"][-1]
        self.assertEqual((1, 1), (row["steerCount"], len(row["steerAckLatencyS"])))
        events = [
            json.loads(line)["event"]
            for line in (self.run_dir / "events.jsonl").read_text().splitlines()
        ]
        self.assertLess(events.index("steer.sent"), events.index("steer.acked"))

    def test_honored_stop_is_a_clean_finish(self) -> None:
        process = self._start()
        self._cli("runs", "stop", DISPATCH_ID, "--grace", "30")
        out, err = process.communicate(timeout=60)
        self.assertEqual(0, process.returncode, err)
        self.assertIn(b"stopping", out)
        self.assertEqual(
            "succeeded", json.loads((self.run_dir / "result.json").read_text())["status"]
        )
        self.assertFalse((self.run_dir / "abort.json").exists())


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run it**

Run: `.venv/bin/python -m unittest tests.test_channel_steer_integration -v 2>&1 | tail -5`
Expected: `OK` (two tests).

- [x] **Step 3: Document.** Add a Steering section to `docs/orchestrator-channel.md`: STEER kinds; `steer` and `runs stop`; the delivery ladder as built (tier 1 `read_steering` and `ack_steer`, tier 2 PreToolUse gate for `scope`/`priority`/`stop` in tier-1 runs of Claude Code and Codex, tier 4 delivery on resume, graceful abort after an ignored stop or on SIGTERM/SIGHUP with `SUBAGENT_MODEL_ROUTING_ABORT_GRACE_SECS`); `ignored` in the inbox; `abort.json`. Append these bullets to the orchestrator duties in all three SKILL.md files:

```markdown
- Change a running dispatch's scope with `pitwall-agent-routing steer <dispatch-id> --kind scope --message "…"`. Stop it gracefully with `pitwall-agent-routing runs stop <dispatch-id>`; a run that does not wrap up within the grace period is aborted with its receipt intact.
- An `ignored` steer in the inbox means the model did not acknowledge it before its deadline; decide whether to stop that run.
```

Extend `test_channel_discipline_is_taught_everywhere` (Task 19) with the needles `pitwall-agent-routing steer` and `pitwall-agent-routing runs stop`.

- [x] **Step 4: Full validation**

```bash
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check runtime tests tools scripts/pitwall-agent-routing
.venv/bin/mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing
.venv/bin/python tools/validate_plugins.py && .venv/bin/python tools/check_markdown_links.py
```

Expected: at least 596 tests and `OK`; ruff and mypy clean; validators pass.

- [x] **Step 5: Commit**

```bash
git add docs/orchestrator-channel.md docs/lifecycle-hooks.md plugins/*/skills/subagent-model-routing/SKILL.md \
  plugins/pitwall/commands/distill.md tests/test_channel_steer_integration.py tests/test_parity.py
git commit -s -m "docs(agent-routing): steering guide; scripted Phase C exit test"
```

- [x] **Step 6: Live Phase C exit check (operator-run; spec §13 Phase C exit: a mid-task scope change lands in a live run without killing it).** Requires Task 20's live setup (`setup mcp`, and the updated `pitwall` plugin installed in Claude Code so the gate hook is active).

```bash
WORK="$(mktemp -d)" && cd "$WORK" && git init -q . && git commit -q --allow-empty -m seed
cat > prompt.md <<'EOF'
Create five files named step1.txt through step5.txt in the current directory, one at a time.
Between files, run `sleep 20`. Call read_steering before creating each file and follow any
directive it returns.
EOF
env -u CLAUDECODE pitwall-agent-routing dispatch claude prompt.md --routing-ask-support > run.log 2>&1 &
# after step1.txt appears:
pitwall-agent-routing steer <dispatch-id> --kind scope --message "Create only step1.txt and step2.txt, then stop."
wait; tail -1 run.log; ls step*.txt
```

Expected: `run.log` ends with `SHIM-DONE exit=0`; only `step1.txt` and `step2.txt` exist; the ledger's last `finished` row shows `"steerCount": 1` and one `steerAckLatencyS` value. Then repeat with `pitwall-agent-routing runs stop <dispatch-id> --grace 30` instead of the scope steer: the run ends within the grace period with `SHIM-DONE` and outcome `ok` (the model wrapped up) or `cancelled` (the supervisor aborted it).

---

# Part 4 — Phase D: workflow wait and the auto-answer policy

## Task 25: D3 policy answerer

**Files:**
- Create: `runtime/model_routing/channel_policy.py`, `tests/test_channel_policy.py`

**Interfaces:**
- Consumes: `routes.resolved_endpoint` (`routes.py:182`), `pitwall.resolve_pitwall_api_token` (as `route_probe.py:88` uses it), `mailbox.POLICY_PREFIX` (Task 3).
- Produces: `POLICY_BLOCKED_ON = frozenset({"choice", "naming", "file-selection"})`; `@dataclass(frozen=True) PolicyDecision(choice: str | None, answered_by: str | None, reason: str)`; `escalation_reason(ask) -> str | None` (None means D3 allows a policy answer); `build_messages(ask) -> list[dict[str, str]]`; `request_choice(ask, *, route_name: str, entry: Mapping, env: Mapping[str, str], timeout: float = 30.0) -> PolicyDecision`.
- D3 rules as built: eligible only for `blocked_on ∈ {choice, naming, file-selection}`, `severity == "normal"`, a non-`abort` default, and at least two options. The reply must select one of the ask's option ids (never `abort`, never free text). Provenance is `policy:<model>`. The request carries the question, option ids and texts, the default and its rationale, and file names only (D2), with `temperature: 0` and `max_tokens: 64`.

- [x] **Step 1: Write the failing tests** (`tests/test_channel_policy.py`)

```python
"""D3 eligibility and the endpoint-route answerer (plan Task 25)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.channel_policy import escalation_reason, request_choice  # noqa: E402


def _ask(**overrides: object) -> dict:
    ask = {
        "ask_id": "0001",
        "blocked_on": "naming",
        "severity": "normal",
        "question": "Which suffix?",
        "default": "a",
        "default_rationale": "conventional",
        "context": {
            "options": [{"id": "a", "text": "-alpha"}, {"id": "b", "text": "-beta"}],
            "files_touched": ["db/x.sql"],
        },
    }
    ask.update(overrides)
    return ask


class PolicyStub:
    def __init__(self) -> None:
        self.reply = '{"choice": "b"}'
        self.status = 200
        self.delay = 0.0
        self.requests: list[tuple[dict, dict]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append((dict(self.headers), body))
                time.sleep(stub.delay)
                payload = json.dumps(
                    {"choices": [{"message": {"role": "assistant", "content": stub.reply}}]}
                ).encode()
                self.send_response(stub.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.entry = {
            "model": "stub-model",
            "endpoint": {
                "baseUrl": f"http://127.0.0.1:{self.server.server_port}/v1",
                "apiKeyEnv": "STUB_KEY",
            },
        }

    def close(self) -> None:
        self.server.shutdown()


class EligibilityTests(unittest.TestCase):
    def test_d3_table(self) -> None:
        self.assertIsNone(escalation_reason(_ask()))
        for blocked in ("schema", "destructive", "spend"):
            self.assertIn("escalates", escalation_reason(_ask(blocked_on=blocked)) or "")
        self.assertIn("blocking", escalation_reason(_ask(severity="blocking")) or "")
        self.assertIn("abort", escalation_reason(_ask(default="abort")) or "")
        self.assertIn(
            "two options",
            escalation_reason(_ask(context={"options": [{"id": "a", "text": "x"}]})) or "",
        )


@mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
class AnswererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stub = PolicyStub()
        self.env = {"STUB_KEY": "stub-secret"}

    def tearDown(self) -> None:
        self.stub.close()

    def _decide(self, **kwargs: object):  # type: ignore[no-untyped-def]
        return request_choice(
            _ask(**kwargs),
            route_name="policy-stub",
            entry=self.stub.entry,
            env=self.env,
            timeout=1.0,
        )

    def test_selects_an_option_with_provenance_and_pointer_context(self) -> None:
        decision = self._decide()
        self.assertEqual(("b", "policy:stub-model"), (decision.choice, decision.answered_by))
        headers, body = self.stub.requests[0]
        self.assertEqual("Bearer stub-secret", headers["Authorization"])
        self.assertEqual(("stub-model", 0), (body["model"], body["temperature"]))
        user = body["messages"][-1]["content"]
        self.assertIn("b: -beta", user)
        self.assertIn("db/x.sql", user)

    def test_prose_wrapped_json_is_accepted(self) -> None:
        self.stub.reply = 'Sure. {"choice": "a"}'
        self.assertEqual("a", self._decide().choice)

    def test_anything_but_a_provided_option_escalates(self) -> None:
        for reply in ('{"choice": "abort"}', '{"choice": "zzz"}', "pick b", ""):
            with self.subTest(reply=reply):
                self.stub.reply = reply
                decision = self._decide()
                self.assertIsNone(decision.choice)
                self.assertIn("did not select", decision.reason)

    def test_transport_failures_escalate(self) -> None:
        self.stub.status = 500
        self.assertIn("HTTP 500", self._decide().reason)
        self.stub.status, self.stub.delay = 200, 2.0
        self.assertIn("failed", self._decide().reason)

    def test_routes_without_an_endpoint_cannot_answer(self) -> None:
        decision = request_choice(
            _ask(), route_name="plain", entry={"model": "m"}, env={}, timeout=1.0
        )
        self.assertIn("has no endpoint", decision.reason)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_channel_policy -v 2>&1 | tail -8`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_routing.channel_policy'`.

- [x] **Step 3: Implement `channel_policy.py`**

```python
"""D3 auto-answer policy: routine asks answered by an endpoint route (spec §9.2, §14 D3)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import socket
from typing import Any, Mapping
from urllib import error, request

from .mailbox import POLICY_PREFIX
from .pitwall import resolve_pitwall_api_token
from .routes import resolved_endpoint


POLICY_BLOCKED_ON = frozenset({"choice", "naming", "file-selection"})
DEFAULT_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 64 * 1024
_SYSTEM = (
    "You answer routine clarification questions from a coding agent. Choose exactly one of the "
    'provided option ids. Reply with only a JSON object: {"choice": "<option id>"}.'
)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    choice: str | None
    answered_by: str | None
    reason: str


def escalation_reason(ask: Mapping[str, Any]) -> str | None:
    """None when D3 allows a policy answer; otherwise why the ask goes to the operator."""
    blocked_on = ask.get("blocked_on")
    if blocked_on not in POLICY_BLOCKED_ON:
        return f"blocked_on {blocked_on!r} always escalates to the operator"
    if ask.get("severity", "normal") != "normal":
        return "blocking severity always escalates to the operator"
    if ask.get("default") == "abort":
        return "an abort default always escalates to the operator"
    if len(ask.get("context", {}).get("options", [])) < 2:
        return "the ask offers fewer than two options"
    return None


def build_messages(ask: Mapping[str, Any]) -> list[dict[str, str]]:
    context = ask.get("context", {})
    lines = [f"Question: {ask['question']}", "Options:"]
    lines += [f"- {option['id']}: {option['text']}" for option in context.get("options", [])]
    lines.append(f"The agent's default: {ask['default']}")
    if ask.get("default_rationale"):
        lines.append(f"Default rationale: {ask['default_rationale']}")
    if context.get("files_touched"):
        lines.append("Files involved (names only): " + ", ".join(context["files_touched"]))
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


def _parse_choice(content: str, option_ids: set[str]) -> str | None:
    candidates = [content]
    if (match := re.search(r"\{.*?\}", content, re.S)) is not None:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, dict)
            and isinstance(value.get("choice"), str)
            and value["choice"] in option_ids
        ):
            return str(value["choice"])
    return None


def request_choice(
    ask: Mapping[str, Any],
    *,
    route_name: str,
    entry: Mapping[str, Any],
    env: Mapping[str, str],
    timeout: float = DEFAULT_TIMEOUT_S,
) -> PolicyDecision:
    if (reason := escalation_reason(ask)) is not None:
        return PolicyDecision(None, None, reason)
    endpoint = resolved_endpoint(entry)
    if endpoint is None:
        return PolicyDecision(
            None, None, f"route {route_name} has no endpoint; only endpoint routes answer asks"
        )
    model = str(entry["model"])
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key_env := endpoint.get("apiKeyEnv"):
        key, _resolved = resolve_pitwall_api_token(env, str(key_env))
        if key:
            headers["Authorization"] = f"Bearer {key}"
    body = json.dumps(
        {"model": model, "messages": build_messages(ask), "temperature": 0, "max_tokens": 64}
    ).encode()
    url = str(endpoint["baseUrl"]).rstrip("/") + "/chat/completions"
    try:
        with request.urlopen(
            request.Request(url, data=body, headers=headers, method="POST"), timeout=timeout
        ) as reply:
            payload = json.loads(reply.read(MAX_RESPONSE_BYTES).decode("utf-8"))
    except error.HTTPError as exc:
        return PolicyDecision(None, None, f"policy route {route_name} returned HTTP {exc.code}")
    except (error.URLError, socket.timeout, OSError, ValueError) as exc:
        return PolicyDecision(None, None, f"policy route {route_name} failed: {exc}")
    try:
        content = str(payload["choices"][0]["message"]["content"])
    except KeyError, IndexError, TypeError:
        return PolicyDecision(None, None, "the policy reply had no message content")
    option_ids = {str(option["id"]) for option in ask["context"]["options"]}
    if (choice := _parse_choice(content, option_ids)) is None:
        return PolicyDecision(
            None, None, "the policy reply did not select one of the ask's options"
        )
    return PolicyDecision(
        choice, f"{POLICY_PREFIX}{model}", f"selected by policy route {route_name}"
    )
```

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_channel_policy -v 2>&1 | tail -5`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/channel_policy.py tests/test_channel_policy.py
git commit -s -m "feat(agent-routing): D3 policy answerer over endpoint routes with policy:<model> provenance"
```

## Task 26: Workflow keys — `askSupport`, `maxAsks`, `autoAnswer`

**Files:**
- Modify: `runtime/model_routing/workflow.py` (`_normalize_task` :307, `_normalize_defaults` :398)
- Modify: `runtime/model_routing/scheduler.py` (`_ProductionRunner.__call__` :212 argument list)
- Modify: `tests/test_workflow.py`, `tests/test_scheduler.py`, `docs/workflows.md`

**Interfaces:**
- Task keys: `askSupport` (bool) and `maxAsks` (int 1–50; requires effective `askSupport`). Defaults keys: `askSupport` (bool) and `autoAnswer` (`{"route": <route name>, "timeoutSeconds": <number 1–120, default 30>}`). Route names must match `[A-Za-z][A-Za-z0-9._-]{0,63}` (the `routes.json` rule); existence is checked when the scheduler starts (Task 27).
- Normalization (Decision 15): a task's normalized document gains `"askSupport": true` when the task or the defaults enable it, and `"maxAsks": n` when set; `defaults` gains `autoAnswer` only when set. A workflow that uses none of these normalizes byte-identically to today.
- `_ProductionRunner` appends `--routing-ask-support` when `task.get("askSupport")` and `--routing-max-asks=<n>` when `task.get("maxAsks")`. `.get` keeps persisted pre-upgrade workflows resumable.

- [x] **Step 1: Write the failing tests** (add to `tests/test_workflow.py`, reusing its `validate` and `minimal_task` helpers)

```python
def test_channel_keys_normalize_only_when_set(self):
    with tempfile.TemporaryDirectory() as td:
        plain, _ = validate(
            {"schemaVersion": 1, "name": "x", "tasks": {"a": minimal_task()}}, tmp_path=Path(td)
        )
        self.assertNotIn("askSupport", plain["tasks"]["a"])
        self.assertNotIn("autoAnswer", plain["defaults"])
        asking, _ = validate(
            {
                "schemaVersion": 1,
                "name": "x",
                "defaults": {"askSupport": True, "autoAnswer": {"route": "policy-stub"}},
                "tasks": {"a": minimal_task(), "b": {**minimal_task(), "maxAsks": 2}},
            },
            tmp_path=Path(td),
        )
        self.assertTrue(asking["tasks"]["a"]["askSupport"])
        self.assertEqual(2, asking["tasks"]["b"]["maxAsks"])
        self.assertEqual(
            {"route": "policy-stub", "timeoutSeconds": 30}, asking["defaults"]["autoAnswer"]
        )


def test_channel_keys_are_validated(self):
    cases = [
        ({"askSupport": "yes"}, "askSupport must be a boolean"),
        ({"maxAsks": 3}, "maxAsks requires askSupport"),
        ({"askSupport": True, "maxAsks": 51}, "maxAsks must be an integer 1..50"),
    ]
    for extra, message in cases:
        with self.subTest(extra=extra), tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(WorkflowError, message):
                validate(
                    {"schemaVersion": 1, "name": "x", "tasks": {"a": {**minimal_task(), **extra}}},
                    tmp_path=Path(td),
                )
    for auto, message in (
        ({"route": "1bad"}, "autoAnswer.route"),
        ({"route": "ok", "timeoutSeconds": 0}, "timeoutSeconds"),
    ):
        with self.subTest(auto=auto), tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(WorkflowError, message):
                validate(
                    {
                        "schemaVersion": 1,
                        "name": "x",
                        "defaults": {"autoAnswer": auto},
                        "tasks": {"a": minimal_task()},
                    },
                    tmp_path=Path(td),
                )
```

Add to `ProductionRunnerTests` in `tests/test_scheduler.py`: a task with `"askSupport": True, "maxAsks": 2` dispatches `_shim` with `--routing-ask-support` and `--routing-max-asks=2` (assert through the fake binary's captured argv or the run's `resume.json`, which records `askSupport` and `maxAsks`).

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_workflow tests.test_scheduler 2>&1 | tail -5`
Expected: FAIL — `unexpected key askSupport`.

- [x] **Step 3: Implement.** Add the keys to both `_require_keys` sets. Validate as specified, raising through `_fail` with the messages the tests match. Build the normalized additions only when set. Append the routing flags in `_ProductionRunner.__call__` after the existing `--routing-workspace`/`--routing-task-mode` arguments. Document the keys in `docs/workflows.md` (a "Questions and auto-answers" subsection).

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_workflow tests.test_scheduler 2>&1 | tail -3`
Expected: `OK`, with the exact-shape assertions at `tests/test_workflow.py:1246` and `:1411` unchanged.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/workflow.py runtime/model_routing/scheduler.py tests/test_workflow.py tests/test_scheduler.py docs/workflows.md
git commit -s -m "feat(agent-routing): askSupport, maxAsks, and autoAnswer workflow keys"
```

## Task 27: Scheduler `wait_for_answer`

**Files:**
- Modify: `runtime/model_routing/scheduler.py` (`AttemptOutcome` :44, `TaskRunner` :55, `_ProductionRunner` :195, `_execute_task` :472, `_run_state_machine` :560, `run_workflow` :708, `resume_workflow` :739)
- Create: `tests/test_scheduler_wait.py`

**Interfaces:**
- Consumes: `channel.expire_overdue`, `channel.answer_ask`, `channel_policy.escalation_reason`/`request_choice`, `routes.load_routes`.
- `_ProductionRunner` reports `status="paused"` (not a transport error) when the child exits 75, writes no `result.json`, and `run.json` says `paused`. `_ProductionRunner.resume(task_id, task, attempt, dispatch_id, workflow_id, workflow_dir, env, repo_root) -> AttemptOutcome` runs `[*execution.argv, "runs", "resume", dispatch_id]` with the same lineage environment as `__call__` (resume sets the dispatch id and attempt itself); both share one `_launch(...)` helper. `TaskRunner` gains `resume` as an optional member; a runner without it fails a paused task with `runner cannot resume paused dispatches`.
- `_AutoAnswer(route_name: str | None, entry: Mapping | None, timeout: float, missing_reason: str | None)` is built once per scheduler run from `normalized["defaults"].get("autoAnswer")` and `load_routes(env, registry=registry)`. Its `missing_reason` is `no autoAnswer route is configured` or `autoAnswer route <name> is not in routes.json`. `_run_state_machine` gains a `registry` parameter; `run_workflow` and `resume_workflow` pass it.
- `_wait_for_answers(task_id, dispatch_id, controller, env, cancel_event, auto, *, poll_seconds=1.0) -> Literal["answered", "cancelled"]`. It sets the task state to `waiting_for_answer` (with `waitingOn` ask ids) and loops: apply `expire_overdue`; for each pending ask not yet tried, attempt the policy once (recording `policy:<model>`), otherwise emit `ask.escalated` `{askId, reason}` once and print `… answer with pitwall-agent-routing answer <dispatch-id> <ask-id> <choice>` to stderr. It returns when nothing is pending or on cancel.
- `_execute_task`: after an attempt finishes as `paused`, wait, then call `runner.resume` as a new attempt record with the same `dispatchId` and `"resumed": true`; repeat while the outcome is `paused`; then continue with the existing success/verification/retry handling.
- `resume_workflow`: a task persisted as `waiting_for_answer` becomes `pending` with `pausedDispatchId`. `_execute_task` then skips a fresh dispatch for it and re-enters the wait loop for that dispatch. The final cancellation sweep also converts `waiting_for_answer` to `cancelled`.

- [x] **Step 1: Write the failing tests** (`tests/test_scheduler_wait.py`)

```python
"""wait_for_answer: paused dispatches resume when asks resolve (plan Task 27)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.test_channel_policy import PolicyStub  # noqa: E402
from tests.test_scheduler import FakeRunner, SchedulerFixture, task  # noqa: E402

from model_routing.channel import answer_ask  # noqa: E402
from model_routing.run_store import RunStore, atomic_write_json, state_root  # noqa: E402
from model_routing.scheduler import AttemptOutcome, cancel_workflow, resume_workflow  # noqa: E402


class PausingRunner(FakeRunner):
    """First attempt of each task pauses on one ask; resume() succeeds."""

    def __init__(self, env: dict[str, str], repo: Path, asks: dict[str, dict]) -> None:
        super().__init__(env, repo)
        self.asks = asks
        self.resumes: list[str] = []

    def __call__(
        self,
        task_id,
        task_value,
        prompt,
        attempt,
        dispatch_id,
        workflow_id,
        workflow_dir,
        env,
        repo_root,
    ):  # type: ignore[no-untyped-def]
        store = RunStore(state_root(env), dispatch_id)
        store.path.mkdir(parents=True, exist_ok=True)
        ask = self.asks[task_id]
        written = store.mailbox().write_ask(
            blocked_on=ask["blocked_on"],
            question="q",
            default=ask.get("default", "a"),
            options=[{"id": "a", "text": "x"}, {"id": "b", "text": "y"}],
            deadline_s=ask.get("deadline_s", 600),
        )
        if "age" in ask:
            path = store.path / "mailbox" / "asks" / f"{written['ask_id']}.json"
            doc = json.loads(path.read_text())
            doc["created_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=ask["age"])
            ).isoformat()
            path.write_text(json.dumps(doc))
        atomic_write_json(
            store.artifact("run.json"),
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "state": "paused",
                "provider": "opencode",
                "model": "m",
                "attempt": 1,
            },
        )
        return AttemptOutcome(dispatch_id, "paused", 75)

    def resume(
        self, task_id, task_value, attempt, dispatch_id, workflow_id, workflow_dir, env, repo_root
    ):  # type: ignore[no-untyped-def]
        self.resumes.append(dispatch_id)
        return super().__call__(
            task_id,
            task_value,
            b"",
            attempt,
            dispatch_id,
            workflow_id,
            workflow_dir,
            env,
            repo_root,
        )


@mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
class WaitForAnswerTests(SchedulerFixture):
    """The workflow runs on the main thread (so its SIGINT cancel handler is installed, as in production);
    operator actions happen on a helper thread once the task is waiting."""

    def _when_waiting(self, task_id: str, action) -> threading.Thread:  # type: ignore[no-untyped-def]
        def watch() -> None:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                for state_file in (state_root(self.env) / "workflows").glob("*/state.json"):
                    try:
                        state = json.loads(state_file.read_text())
                    except json.JSONDecodeError:
                        continue
                    record = state["tasks"][task_id]
                    if record["state"] == "waiting_for_answer":
                        action(state_file.parent.name, record["attempts"][0]["dispatchId"])
                        return
                time.sleep(0.05)

        helper = threading.Thread(target=watch, daemon=True)
        helper.start()
        return helper

    def _answered_by(self, state: dict, task_id: str) -> str:
        dispatch_id = state["tasks"][task_id]["attempts"][0]["dispatchId"]
        answer = RunStore(state_root(self.env), dispatch_id).mailbox().get_answer("0001")
        assert answer is not None
        return str(answer["answered_by"])

    def test_policy_answers_routine_asks_and_the_dispatch_resumes(self) -> None:
        stub = PolicyStub()
        self.addCleanup(stub.close)
        routes = Path(self.env["HOME"]) / ".config" / "subagent-model-routing" / "routes.json"
        routes.parent.mkdir(parents=True)
        routes.write_text(
            json.dumps({"schemaVersion": 1, "models": {"policy-stub": stub.entry}}),
            encoding="utf-8",
        )
        runner = PausingRunner(self.env, self.repo, {"a": {"blocked_on": "naming"}})
        state = self.execute(
            self.workflow({"a": task()}, {"autoAnswer": {"route": "policy-stub"}}), runner
        )
        self.assertEqual("succeeded", state["status"], state)
        self.assertEqual("policy:stub-model", self._answered_by(state, "a"))
        attempts = state["tasks"]["a"]["attempts"]
        self.assertEqual(2, len(attempts))
        self.assertEqual(attempts[0]["dispatchId"], attempts[1]["dispatchId"])
        self.assertTrue(attempts[1]["resumed"])
        self.assertEqual([attempts[0]["dispatchId"]], runner.resumes)

    def test_consequential_asks_escalate_to_the_operator(self) -> None:
        seen: dict[str, str] = {}

        def operator(_workflow_id: str, dispatch_id: str) -> None:
            seen["events"] = (
                RunStore(state_root(self.env), dispatch_id).artifact("events.jsonl").read_text()
            )
            answer_ask(
                self.env,
                dispatch_id,
                "0001",
                choice="b",
                answered_by="operator",
                note=None,
                provider="operator",
            )

        helper = self._when_waiting("a", operator)
        runner = PausingRunner(self.env, self.repo, {"a": {"blocked_on": "destructive"}})
        state = self.execute(self.workflow({"a": task()}), runner)
        helper.join(5)
        self.assertEqual("succeeded", state["status"])
        self.assertEqual("operator", self._answered_by(state, "a"))
        self.assertIn('"event":"ask.escalated"', seen["events"])

    def test_expired_asks_take_their_default(self) -> None:
        runner = PausingRunner(
            self.env, self.repo, {"a": {"blocked_on": "spend", "deadline_s": 60, "age": 120}}
        )
        state = self.execute(self.workflow({"a": task()}), runner)
        self.assertEqual("succeeded", state["status"])
        self.assertEqual("default", self._answered_by(state, "a"))

    def _cancelled_while_waiting(self) -> tuple[str, str]:
        ids: dict[str, str] = {}

        def cancel(workflow_id: str, dispatch_id: str) -> None:
            ids.update(workflow=workflow_id, dispatch=dispatch_id)
            cancel_workflow(
                self.env, workflow_id
            )  # SIGINT to this process, handled by the scheduler

        helper = self._when_waiting("a", cancel)
        state = self.execute(
            self.workflow({"a": task()}),
            PausingRunner(self.env, self.repo, {"a": {"blocked_on": "schema"}}),
        )
        helper.join(5)
        self.assertEqual("cancelled", state["status"])
        return ids["workflow"], ids["dispatch"]

    def test_cancel_while_waiting_leaves_the_run_paused(self) -> None:
        _workflow_id, dispatch_id = self._cancelled_while_waiting()
        run = json.loads(
            RunStore(state_root(self.env), dispatch_id).artifact("run.json").read_text()
        )
        self.assertEqual("paused", run["state"])

    def test_resume_workflow_reenters_the_wait_for_a_paused_task(self) -> None:
        workflow_id, dispatch_id = self._cancelled_while_waiting()
        # A scheduler that died mid-wait leaves the task persisted as waiting_for_answer.
        state_file = state_root(self.env) / "workflows" / workflow_id / "state.json"
        persisted = json.loads(state_file.read_text())
        persisted["tasks"]["a"]["state"] = "waiting_for_answer"
        atomic_write_json(state_file, persisted)
        answer_ask(
            self.env,
            dispatch_id,
            "0001",
            choice="a",
            answered_by="operator",
            note=None,
            provider="operator",
        )
        second = PausingRunner(self.env, self.repo, {"a": {"blocked_on": "schema"}})
        resumed = resume_workflow(
            workflow_id, repo_root=self.repo, env=self.env, registry=self.registry, runner=second
        )
        self.assertEqual("succeeded", resumed["status"])
        self.assertEqual([dispatch_id], second.resumes)
        self.assertEqual(
            1, len(RunStore(state_root(self.env), dispatch_id).mailbox().asks())
        )  # no fresh dispatch


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_scheduler_wait -v 2>&1 | tail -8`
Expected: FAIL — the paused outcome is treated as a failure (`status: failed`).

- [x] **Step 3: Implement the wait loop**

```python
@dataclass(frozen=True, slots=True)
class _AutoAnswer:
    route_name: str | None
    entry: Mapping[str, Any] | None
    timeout: float
    missing_reason: str | None


def _auto_answer(
    normalized: Mapping[str, Any], env: Mapping[str, str], registry: Mapping[str, Any]
) -> _AutoAnswer:
    config = normalized.get("defaults", {}).get("autoAnswer")
    if not config:
        return _AutoAnswer(None, None, 0.0, "no autoAnswer route is configured")
    name = str(config["route"])
    try:
        entry = load_routes(env, registry=registry)["models"].get(name)
    except RoutesError as exc:
        return _AutoAnswer(name, None, 0.0, f"routes.json is unusable: {exc}")
    if entry is None:
        return _AutoAnswer(name, None, 0.0, f"autoAnswer route {name} is not in routes.json")
    return _AutoAnswer(name, entry, float(config.get("timeoutSeconds", 30)), None)


def _wait_for_answers(
    task_id: str,
    dispatch_id: str,
    controller: _StateController,
    env: Mapping[str, str],
    cancel_event: threading.Event,
    auto: _AutoAnswer,
    *,
    poll_seconds: float = 1.0,
) -> str:
    store = RunStore(state_root(env), dispatch_id)
    emitter = EventEmitter(
        store,
        provider="workflow",
        model="channel",
        workflow_id=controller.state["workflowId"],
        task_id=task_id,
        callback=HookRunner(env),
    )
    tried: set[str] = set()

    def mark(state_name: str, waiting: list[str]) -> None:
        def update(state: dict[str, Any]) -> None:
            state["tasks"][task_id].update({"state": state_name, "waitingOn": waiting})

        controller.mutate(update)

    mark("waiting_for_answer", [ask["ask_id"] for ask in store.mailbox().pending_asks()])
    while True:
        expire_overdue(store, emitter=emitter)
        pending = store.mailbox().pending_asks()
        if not pending:
            mark("running", [])
            return "answered"
        for ask in pending:
            ask_id = str(ask["ask_id"])
            if ask_id in tried:
                continue
            tried.add(ask_id)
            reason = auto.missing_reason or escalation_reason(ask)
            if reason is None and auto.entry is not None and auto.route_name is not None:
                decision = request_choice(
                    ask, route_name=auto.route_name, entry=auto.entry, env=env, timeout=auto.timeout
                )
                if decision.choice is not None and decision.answered_by is not None:
                    try:
                        answer_ask(
                            env,
                            dispatch_id,
                            ask_id,
                            choice=decision.choice,
                            answered_by=decision.answered_by,
                            note=decision.reason,
                            provider="workflow",
                        )
                        continue
                    except MailboxError as exc:
                        if store.mailbox().get_answer(ask_id) is not None:
                            continue  # someone answered first
                        reason = f"policy answer rejected: {exc}"
                else:
                    reason = decision.reason
            emitter.emit("ask.escalated", {"askId": ask_id, "reason": reason})
            print(
                f"pitwall-agent-routing: workflow {controller.state['workflowId']} task {task_id}: ask {ask_id} "
                f"needs the operator ({reason}); answer with "
                f"`pitwall-agent-routing answer {dispatch_id} {ask_id} <choice>`",
                file=sys.stderr,
            )
        if cancel_event.wait(poll_seconds):
            return "cancelled"
```

Wire it as the Interfaces block describes: paused handling right after `controller.mutate(finished)` in `_execute_task`; `waiting_for_answer` handling in `resume_workflow` and in the final cancellation sweep; `registry` threaded into `_run_state_machine`; `_launch` shared by `__call__` and `resume` in `_ProductionRunner`.

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_scheduler_wait tests.test_scheduler tests.test_workflow 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/scheduler.py tests/test_scheduler_wait.py
git commit -s -m "feat(agent-routing): wait_for_answer — workflows resume paused dispatches when asks resolve"
```

## Task 28: D5 — channel aggregates reach the ledger before a run expires

**Files:**
- Modify: `runtime/model_routing/run_store.py` (add `ledger_path`, moved from `dispatch._ledger_path` :60; `cleanup_runs` :246)
- Modify: `runtime/model_routing/dispatch.py` (`_ledger_path = ledger_path`, so existing callers and imports keep working)
- Modify: `runtime/model_routing/channel.py` (add `channel_aggregate`, `distill_before_cleanup`)
- Modify: `plugins/pitwall/commands/distill.md` (step 3)
- Create: `tests/test_channel_distill.py`

**Interfaces:**
- Produces: `run_store.ledger_path(env) -> Path`; `channel.channel_aggregate(run_dir: Path) -> dict[str, Any] | None` (None when the run has no asks and no steers); `channel.distill_before_cleanup(env, run_dir) -> bool` (appends the aggregate row, True when a row was written).
- Aggregate row: `{"ts", "source": "shim", "event": "channel", "schema_version": 4, "dispatch_id", "shim", "model", "workflow_id", "task_id", "asks", "blockedOn": {class: count}, "resolvedBy": {source: count}, "latencyToAnswerS": [...], "steers", "steerAckLatencyS": [...]}`. `shim`/`model` come from `run.json`. It carries counts and timings only, no question text or file names (D5: the routing signal survives, the code content does not).
- `cleanup_runs` calls `distill_before_cleanup` before `shutil.rmtree`; when the ledger append raises `OSError`, the run is kept (not removed) so the signal is never lost.

- [x] **Step 1: Write the failing tests** (`tests/test_channel_distill.py`)

```python
"""D5: raw Q&A expires with the run; aggregates reach the ledger first (plan Task 28)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.run_store import RunStore, cleanup_runs  # noqa: E402


DISPATCH_ID = "00000000-0000-4000-8000-0000000000a9"


class DistillBeforeCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.ledger = root / "ledger.jsonl"
        self.env = {
            "HOME": str(root),
            "XDG_STATE_HOME": str(root / "state"),
            "SUBAGENT_MODEL_ROUTING_LEDGER": str(self.ledger),
        }
        self.store = RunStore(root / "state" / "subagent-model-routing", DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self.store.write_json(
            "run.json",
            {
                "schemaVersion": 1,
                "dispatchId": DISPATCH_ID,
                "provider": "opencode",
                "model": "zai-coding-plan/glm-5.3",
                "state": "succeeded",
            },
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_aggregate_row_precedes_removal_and_holds_no_content(self) -> None:
        box = self.store.mailbox()
        box.write_ask(
            blocked_on="naming",
            question="SECRET QUESTION TEXT",
            options=[{"id": "a", "text": "x"}],
            default="a",
            deadline_s=60,
            files_touched=["secret/path.py"],
        )
        box.write_answer("0001", choice="a", answered_by="operator")
        box.write_steer(kind="scope", message="narrow")
        self.assertEqual(
            [self.store.path], cleanup_runs(self.env, older_than_seconds=None, remove_all=True)
        )
        rows = [json.loads(line) for line in self.ledger.read_text().splitlines()]
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(
            ("channel", "opencode", "zai-coding-plan/glm-5.3"),
            (row["event"], row["shim"], row["model"]),
        )
        self.assertEqual(
            (1, {"naming": 1}, {"operator": 1}, 1),
            (row["asks"], row["blockedOn"], row["resolvedBy"], row["steers"]),
        )
        self.assertNotIn("SECRET", json.dumps(row))
        self.assertNotIn("secret/path.py", json.dumps(row))

    def test_runs_without_channel_activity_add_no_row(self) -> None:
        cleanup_runs(self.env, older_than_seconds=None, remove_all=True)
        self.assertFalse(self.ledger.exists())

    def test_an_unwritable_ledger_keeps_the_run(self) -> None:
        self.ledger.mkdir()  # appending to a directory raises OSError
        self.store.mailbox().write_steer(kind="note", message="m")
        self.assertEqual([], cleanup_runs(self.env, older_than_seconds=None, remove_all=True))
        self.assertTrue(self.store.path.is_dir())


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and watch them fail**

Run: `.venv/bin/python -m unittest tests.test_channel_distill -v 2>&1 | tail -8`
Expected: FAIL — no ledger row is written.

- [x] **Step 3: Implement**

```python
def channel_aggregate(run_dir: Path) -> dict[str, Any] | None:
    """Counts and timings for one run's channel traffic; no question text, options, or paths."""
    box = Mailbox(run_dir, run_dir.name)
    if not (box.root).is_dir():
        return None
    asks, answers, steers = box.asks(), {a["ask_id"]: a for a in box.answers()}, box.steers()
    if not asks and not steers:
        return None
    latencies: list[int] = []
    for ask in asks:
        answer = answers.get(ask["ask_id"])
        created, answered = (
            epoch_of(ask.get("created_at")),
            epoch_of(answer.get("answered_at")) if answer else None,
        )
        if created is not None and answered is not None:
            latencies.append(max(0, int(answered - created)))
    store = RunStore(run_dir.parent.parent, run_dir.name)
    return {
        "asks": len(asks),
        "blockedOn": dict(Counter(str(ask["blocked_on"]) for ask in asks)),
        "resolvedBy": dict(Counter(str(answer["answered_by"]) for answer in answers.values())),
        "latencyToAnswerS": latencies,
        "steers": len(steers),
        "steerAckLatencyS": ledger_fields(store, wall_seconds_total=1)["steerAckLatencyS"],
    }


def distill_before_cleanup(env: Mapping[str, str], run_dir: Path) -> bool:
    aggregate = channel_aggregate(run_dir)
    if aggregate is None:
        return False
    try:
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        run = {}
    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "shim",
        "event": "channel",
        "schema_version": 4,
        "dispatch_id": run_dir.name,
        "shim": run.get("provider"),
        "model": run.get("model"),
        "workflow_id": run.get("workflowId"),
        "task_id": run.get("taskId"),
        **aggregate,
    }
    append_jsonl(ledger_path(env), row)  # OSError propagates: cleanup keeps the run
    return True
```

(`Counter` from `collections`; `append_jsonl` and `ledger_path` from `.run_store`.) In `cleanup_runs`, immediately before `shutil.rmtree(path)`:

```python
            from .channel import distill_before_cleanup  # channel imports run_store; import at call time

            try:
                distill_before_cleanup(env, path)
            except OSError:
                continue  # keep the run until its routing signal can be recorded
```

Add to `distill.md` step 3: `Also summarize source:"shim", event:"channel" rows per model: asks per dispatch, blockedOn classes, resolvedBy sources (operator, orchestrator, default, policy:<model>), latencyToAnswerS, and steerAckLatencyS. They are routing-quality signal (a model that asks fewer, sharper questions is finishing work), never dispatch counts.`

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_channel_distill tests.test_run_store tests.test_parity tests.test_distill_source 2>&1 | tail -3`
Expected: `OK`.

- [x] **Step 5: Lint, type-check, commit**

Run: `.venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing`
Expected: `All checks passed!` and `Success: no issues found`.

```bash
git add runtime/model_routing/run_store.py runtime/model_routing/dispatch.py runtime/model_routing/channel.py \
  plugins/pitwall/commands/distill.md tests/test_channel_distill.py
git commit -s -m "feat(agent-routing): D5 — channel aggregates reach the ledger before a run is cleaned up"
```

## Task 29: Phase D documentation and exit check

**Files:**
- Modify: `docs/orchestrator-channel.md` (Workflows and auto-answers section; retention section), `docs/workflows.md` (the `waiting_for_answer` state and resume behavior), `docs/lifecycle-hooks.md` (`ask.escalated`, with a terminal-bell hook example for the operator)
- Create: `tests/test_workflow_channel_e2e.py`
- Modify: `tests/test_scheduler.py` (extract `ProductionEnvMixin`)

- [x] **Step 1: Write the scripted exit test** (`tests/test_workflow_channel_e2e.py`)

```python
"""Phase D exit, scripted: unattended fan-out, one policy answer, one escalation (plan Task 29)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from tests.test_channel_policy import PolicyStub  # noqa: E402
from tests.test_scheduler import ProductionEnvMixin, SchedulerFixture, task  # noqa: E402

from model_routing.run_store import RunStore, state_root  # noqa: E402
from model_routing.scheduler import run_workflow  # noqa: E402


LAUNCHER = ROOT / "scripts" / "pitwall-agent-routing"

# A fake opencode: attempt 1 asks (routine or destructive, by prompt) and pauses; attempt 2 finishes.
_ASKING_OPENCODE = """\
#!/usr/bin/env python3
import os, sys
from pathlib import Path
sys.path.insert(0, {runtime!r})
from model_routing.mailbox import Mailbox
prompt = sys.stdin.read()
if os.environ["SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT"] == "1":
    dispatch_id = os.environ["SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID"]
    root = Path(os.environ["SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT"])
    Mailbox(root / "runs" / dispatch_id, dispatch_id).write_ask(
        blocked_on="naming" if "ROUTINE" in prompt else "destructive", question="Which option?",
        options=[{{"id": "a", "text": "first"}}, {{"id": "b", "text": "second"}}], default="a", deadline_s=600)
    sys.exit(75)
print("resumed", flush=True)
"""


@mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
class WorkflowChannelEndToEndTests(ProductionEnvMixin, SchedulerFixture):
    def test_unattended_fan_out_with_one_policy_answer_and_one_escalation(self) -> None:
        stub = PolicyStub()
        self.addCleanup(stub.close)
        routes = Path(self.env["HOME"]) / ".config" / "subagent-model-routing" / "routes.json"
        routes.parent.mkdir(parents=True)
        routes.write_text(
            json.dumps({"schemaVersion": 1, "models": {"policy-stub": stub.entry}}),
            encoding="utf-8",
        )
        fake = self.root / "asking-opencode"
        fake.write_text(_ASKING_OPENCODE.format(runtime=str(ROOT / "runtime")), encoding="utf-8")
        fake.chmod(0o755)
        ledger = self.root / "ledger.jsonl"
        env = self.production_env(OPENCODE_BIN=str(fake), SUBAGENT_MODEL_ROUTING_LEDGER=str(ledger))
        workflow = self.workflow(
            {
                "routine": {**task(), "prompt": {"text": "ROUTINE task"}},
                "dangerous": {**task(), "prompt": {"text": "DANGEROUS task"}},
            },
            {"maxConcurrency": 2, "askSupport": True, "autoAnswer": {"route": "policy-stub"}},
        )

        def operator() -> None:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                for state_file in (state_root(env) / "workflows").glob("*/state.json"):
                    try:
                        record = json.loads(state_file.read_text())["tasks"]["dangerous"]
                    except json.JSONDecodeError, KeyError:
                        continue
                    if record["state"] == "waiting_for_answer":
                        subprocess.run(
                            [
                                sys.executable,
                                str(LAUNCHER),
                                "answer",
                                record["attempts"][0]["dispatchId"],
                                "0001",
                                "b",
                            ],
                            env=env,
                            check=True,
                            capture_output=True,
                        )
                        return
                time.sleep(0.1)

        helper = threading.Thread(target=operator, daemon=True)
        helper.start()
        state = run_workflow(
            workflow, host="copilot", repo_root=self.repo, env=env, registry=self.registry
        )
        helper.join(5)
        self.assertEqual("succeeded", state["status"], state)
        expected = {"routine": "policy:stub-model", "dangerous": "operator"}
        finished = {
            row["dispatch_id"]: row
            for row in map(json.loads, ledger.read_text().splitlines())
            if row.get("event") == "finished"
        }
        for task_id, source in expected.items():
            with self.subTest(task=task_id):
                attempts = state["tasks"][task_id]["attempts"]
                self.assertEqual(2, len(attempts))
                dispatch_id = attempts[0]["dispatchId"]
                self.assertEqual(dispatch_id, attempts[1]["dispatchId"])
                store = RunStore(state_root(env), dispatch_id)
                answer = store.mailbox().get_answer("0001")
                assert answer is not None
                self.assertEqual(source, answer["answered_by"])
                self.assertEqual(
                    "succeeded", json.loads(store.artifact("result.json").read_text())["status"]
                )
                self.assertEqual([f"0001:{source}"], finished[dispatch_id]["askResolutions"])
        dangerous_events = (
            RunStore(state_root(env), state["tasks"]["dangerous"]["attempts"][0]["dispatchId"])
            .artifact("events.jsonl")
            .read_text()
        )
        self.assertIn('"event":"ask.escalated"', dangerous_events)


if __name__ == "__main__":
    unittest.main()
```

In the same commit, move `production_env` out of `ProductionRunnerTests` into `class ProductionEnvMixin` in `tests/test_scheduler.py` and declare `class ProductionRunnerTests(ProductionEnvMixin, SchedulerFixture)`, so this module reuses the environment builder without re-running the production-runner tests.

- [x] **Step 2: Run it**

Run: `.venv/bin/python -m unittest tests.test_workflow_channel_e2e -v 2>&1 | tail -5`
Expected: `OK`.

- [x] **Step 3: Document** the workflow keys, the wait loop, D3 eligibility and provenance, escalation (`ask.escalated`, stderr hint), `resume_workflow` re-entry, and D5 retention (raw Q&A expires with `runs cleanup`; the `event:"channel"` row survives in the ledger).
- [x] **Step 4: Full validation**

```bash
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check runtime tests tools scripts/pitwall-agent-routing
.venv/bin/mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing
.venv/bin/python tools/validate_plugins.py && .venv/bin/python tools/check_generated.py && .venv/bin/python tools/check_markdown_links.py
.venv/bin/python tools/sync_routes.py --check
```

Expected: at least 596 tests and `OK`; ruff and mypy clean; every validator passes.

- [x] **Step 5: Commit**

```bash
git add docs/orchestrator-channel.md docs/workflows.md docs/lifecycle-hooks.md tests/test_workflow_channel_e2e.py tests/test_scheduler.py
git commit -s -m "docs(agent-routing): workflow auto-answer guide; scripted Phase D exit test"
```

- [x] **Step 6: Live Phase D exit check (operator-run; spec §13 Phase D exit: an unattended fan-out where at least one routine clarification is policy-answered and at least one escalates).** Requires the free-tier gateway running with a `gateway`-seat route (for example `gw-free` in `routes.json`).

```bash
WORK="$(mktemp -d)" && cd "$WORK" && git init -q . && git commit -q --allow-empty -m seed
cat > routine.md <<'EOF'
Create notes.txt. Before writing it, ask the orchestrator (blocked_on "naming") whether the
title line should be "Notes" (option a) or "Scratch" (option b); default a. Write the chosen title.
EOF
cat > dangerous.md <<'EOF'
Before deleting anything, ask the orchestrator (blocked_on "destructive") whether to remove the
empty directory tmp-scratch (option a: remove, option b: keep); default b. Then do what it says.
EOF
mkdir tmp-scratch
cat > wf.json <<'EOF'
{"schemaVersion": 1, "name": "channel-live",
 "defaults": {"askSupport": true, "autoAnswer": {"route": "gw-free"}, "workspace": "isolated"},
 "tasks": {
   "routine": {"route": {"provider": "opencode", "model": "zai-coding-plan/glm-5.3"}, "mode": "write", "prompt": {"file": "routine.md"}},
   "dangerous": {"route": {"provider": "opencode", "model": "zai-coding-plan/glm-5.3"}, "mode": "write", "prompt": {"file": "dangerous.md"}}}}
EOF
pitwall-agent-routing workflow run wf.json --host codex &
# when stderr prints "ask 0001 needs the operator":
pitwall-agent-routing inbox
pitwall-agent-routing answer <dangerous-dispatch-id> 0001 b --note "keep it"
wait
```

Expected: the workflow finishes `succeeded`; the routine task's answer shows `answered_by` `policy:<gateway model>`; the dangerous task's answer shows `operator`; both dispatch ids appear twice in their task's attempts; `pitwall-agent-routing runs cleanup --all` afterwards appends two `event:"channel"` rows to the ledger. If the gateway model returns something other than an option id, the routine ask escalates instead; answer it, then fix the policy prompt in `channel_policy.build_messages` in this task and re-run until the policy answers.

## Task 30: Close-out

**Files:**
- Modify: `docs/research/2026-09-08-orchestrator-channel.md` (Status line)
- Modify: this plan (tick every box as work lands)

- [x] **Step 1: Update the spec status** to say Phases B–D are implemented, name this plan, record the exit checks with their dates and the measured suite numbers, and list which live checks ran (Phase B per harness, Phase C, Phase D).
- [x] **Step 2: Final validation from a clean tree**

```bash
cd packages/agent-routing
git status --short                       # expect no output
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
.venv/bin/ruff check runtime tests tools scripts/pitwall-agent-routing
.venv/bin/mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing
for s in scripts/*.sh; do bash -n "$s"; done
.venv/bin/python tools/validate_plugins.py && .venv/bin/python tools/check_generated.py && \
  .venv/bin/python tools/check_markdown_links.py && .venv/bin/python tools/sync_routes.py --check
cd ~/git/pitwall && uv run --frozen python tools/ci/check_markdown_links.py
```

Expected: clean status; the suite reports more than 596 tests and `OK`; every other command succeeds with the outputs quoted in earlier tasks.
- [x] **Step 3: Commit** (`git commit -s -m "docs: orchestrator channel Phases B–D implemented"`). Merging to `main` and pushing wait for the operator.

---

## Spec coverage map

| Spec item | Task |
| --- | --- |
| §4.1 D1 derived deadline and per-dispatch cap | 2 |
| §4.1 D2 pointer context on answer surfaces | 5, 15 |
| §4.2 answer sources, one answer per ask | 1, 3 |
| §4.3 STEER kinds; `stop` keeps the sentinel | 21, 22 |
| §5.1 atomic, monotonic mailbox writes | 1 |
| §6.1 stdio MCP server and five tools | 13, 14, 15 |
| §6.1 questioning discipline (skill, prompting notes) | 19, 24 |
| §6.3 registration for six harnesses, `--with-mcp`, uninstall | 10, 16, 17 |
| §6.3 doctor handshake and registration checks | 18 |
| §6.3 `mcp_channel` inventory flag | 10 |
| §7 tier 1 prompt, tier 4 fallback, tier-4 steering on resume | 11, 21 |
| §8 steer ladder: advisory, hook gate, graceful abort | 14, 23, 22 |
| §9.1 inbox, escalation to the operator | 5, 6, 27 |
| §9.2 `wait_for_answer` and D3 policy | 25, 26, 27 |
| §9.3 ledger fields | 4 |
| §10 one open ask, caps, deadlines and defaults, dead-letter, ignored steers, sequence gaps, stdio only | 1, 3, 6, 13 |
| §11 unit, property, integration, chaos tests | 1, 3, 7, 8, 20, 24, 29 |
| §13 Phase B, C, D exits (scripted and live) | 20, 24, 29 |
| §14 D5 retention and distill | 28 |

## Continuation tasks (pre-merge review, 2026-09-17)

A high-effort code review of `origin/main...HEAD` (vendored `open-sse` skipped) plus a full gate
run found these. Each is fixed in place on this branch with a test that fails before the fix.

- [x] **R1 — Budget gate must not block spend-reducing mutations.** `src/pitwall/mcp/tools/runpod_resources.py` `_admit_mutation`: terminate, stop, and delete operations skip the budget check (audit still runs). Validate: `uv run pytest -q tests/mcp/test_runpod_resource_gating.py`. Done: `2bb7e59`.
- [x] **R2 — `pitwall gateway sync` works when installed.** `src/pitwall/cli_gateway.py` imports the sync implementation from a package module (`pitwall.gateway_catalog`), and `tools/gateway/sync_catalog.py` becomes a thin wrapper. Validate: `.venv/bin/pitwall gateway sync --help` from `/tmp` exits 0. Done: `2bb7e59`.
- [x] **R3 — Scheduler recognises a paused run by state, not only exit 75.** `packages/agent-routing/runtime/model_routing/scheduler.py` reads `run.json` state after a dispatch and treats `paused` as paused. Validate: a unittest in `tests/test_scheduler_asks.py` with a non-75 orphaned-ask pause. Done: `90d8e53`.
- [x] **R4 — Ask cap enforced before pending asks are counted; resume loop bounded.** `dispatch.py`: `enforce_cap()` runs first; a pause with no pending asks is a failure, not a pause; the workflow resume loop stops after `maxAsks` resumes. Validate: unittests in `tests/test_dispatch_asks.py`. Done: `90d8e53`.
- [x] **R5 — Escape hatch fires from a real plan.** `src/pitwall/routing/cascade_seed.py` `escape_hatch_message` reads quota-ineligible providers from `plan.eliminated`, not from `ranked_candidates`. Validate: `uv run pytest -q tests/routing/test_cascade_seed.py` with a plan built by `build_production_plan`. Done: `2343527`.
- [x] **R6 — Streaming `/v1/messages` rewrites the model for every provider.** `src/pitwall/api/routes/messages.py` `_stream_messages` applies `gateway.model_id` per provider like the non-stream path. Validate: `uv run pytest -q tests/api/test_messages_route.py`. Done: `89c3f3f`.
- [x] **R7 — Tool calls round-trip through the Anthropic surface.** `src/pitwall/api/anthropic_translate.py`: assistant `tool_calls` become `tool_use` blocks; user `tool_result` blocks become OpenAI `tool` messages; streaming emits `input_json_delta`. Validate: `uv run pytest -q tests/api/test_anthropic_translate.py`. Done: `89c3f3f`.
- [x] **R8 — Lockout state is shared across processes.** `src/pitwall/routing/lockout.py` persists to `provider_quotas.evidence` through the pool when one is available; the exporter and reconciler read that. Validate: `uv run pytest -q tests/routing/test_lockout.py tests/test_webhook_and_exporter.py`. Done: `2343527`.
- [x] **R9 — 429 reset headers parsed safely.** `src/pitwall/providers/gateway.py` `classify_429`: epoch seconds and milliseconds recognised, deltas capped at 24 h, naive timestamps treated as UTC. Validate: `uv run pytest -q tests/providers/test_gateway_provider.py`. Done: `2343527`.
- [x] **R10 — `ask_id` validated as `NNNN` and tied to the file name.** `mailbox.py` `validate_ask` rejects anything else; `write_answer` refuses ids that are not existing asks. Validate: `tests/test_mailbox.py`. Done: `90d8e53`.
- [x] **R11 — Reconciler quota poll never overwrites `used_units` with a stale snapshot.** `src/pitwall/reconciler/__init__.py` `_quota_poll` uses `add_usage`/`set_window` semantics, not a whole-row write. Validate: `tests/reconciler/test_quota_poll.py`. Done: `2343527`.
- [x] **R12 — `dedupSystemPrompt` removes only consecutive duplicate lines.** `packages/gateway/src/compression.ts`. Validate: `npm test` in `packages/gateway`. Done: `2bb7e59`.
- [x] **R13 — `create_pod` never fabricates a pod id.** `src/pitwall/mcp/tools/runpod_resources.py`: a mutation result without an id is an error that records the attempt. Validate: `tests/mcp/test_runpod_resource_gating.py`. Done: `2bb7e59`.
- [x] **R14 — `GatewaySupervisor.stop()` signals only a process it started; `DEFAULT_ARGV` resolves from the package, not cwd.** `src/pitwall/gateway/supervisor.py`. Validate: `tests/test_gateway_supervisor.py`. Done: `2bb7e59`.
- [x] **R15 — `pending_asks()` does not rewrite the mailbox on read.** `mailbox.py`/`run_store.py`. Validate: `tests/test_mailbox.py`. Done: `90d8e53`.
- [x] **R16 — Gates.** `ruff format .` over the 53 drifted files; `.secrets.baseline` regenerated and every new finding audited as a false positive; `vitest` bumped to 5 in `packages/gateway` so `npm audit --audit-level=high` passes. Validate: the three commands exit 0. Done: `57355ae, 4e4c1cd, 1641ee7`.
- [x] **R17 — Merge `main` (QA program, #44) and resolve the six conflicts**, keeping both the budget-rejection path (`_budget_rejection`, `cheapest_over_budget_usd`) and the quota changes. Validate: `make test`, `make test-int`, and the journey harness. Done: `799ab88`.
- [x] **R18 — `pitwall init` seeds the zero-cost gateway capability.** The 0001 `capabilities_cost_mode_check` did not admit `zero`, so init failed on every fresh database and journeys J01, J02, J07, J10, J15, J16 failed with it (found by the final harness run). `db/migrations/0034_capabilities_zero_cost_mode.sql` widens the check; `tests/db/test_zero_cost_mode_migration.py` inserts a zero-cost capability under the integration lane. Done: `8c51ef1`.
- [x] **R19 — `pitwall init` marks the demo capability's provider healthy.** With the gateway seed files in `seed/`, `result.providers[0]` was a disabled gateway provider, so the demo provider stayed `unknown` and every dry-run inference answered 503 (found by the harness after R18). `_init_async` now marks the first provider that serves the first capability; `tests/cli/test_init_seed.py` covers a seed directory with gateway files. Done: `1d27d20`.
- [x] **R20 — CI parity.** Gateway CI and Gateway Release pinned `actions/setup-node` to a nonexistent SHA and asserted a `dist/pitwall-gateway` file the build never writes (the launcher is `dist/shim.js`); the tripwire tests read the developer's real `~/.claude` ledger; the nested-dispatch test used the runner's system `python3`; on macOS the broker's 413 never reached the client and the deadline test was timing-exact. Fixed in the workflows, the broker, and the tests. Validate: PR 45 checks all green.

## Decisions (2026-09-18)

- Live Phase B/C/D exit checks (Tasks 20, 24, 29 step 6) -> deferred, not waived; the maintainer will circle back (the plan stays open until then; the scripted exits pass in CI).
- Repository visibility -> stays private for now; re-decide at the core release or after the QA program has run (attestation jobs keep failing on a private repo and are continue-on-error).
- Free-tier gateway on the maintainer's workstation -> not now; the gateway is exercised through the QA program (T2-04, T2-05, T2-11), not by the maintainer (exercising it here would be testing).
- Unpublished tags `agent-routing/v0.11.0`, `gateway/v0.1.0`, `gateway/v0.1.1` -> deleted (no release, artifact, or install referenced them; the "never move a tag" rule guards published assets). Released: `agent-routing/v0.11.1`, `gateway/v0.1.2`.
- `LLAMA_SWAP_API_KEY` for the `q38-*` routes -> stored in `~/.config/environment.d/pitwall-agent-routing.conf` (mode 600, sourced by the shell), so `doctor` stops warning and the Qwen routes work without fetching the key from the inference host per session.

## Continuation tasks (event-driven channel intent review, 2026-09-22)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement Tasks 31–36 task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close every gap an intent review found between `packages/agent-routing/docs/orchestrator-channel-redesign.md` and the `design/event-driven-channel` branch, so the "defect fixed" claim holds for non-Claude children.

**Architecture:** Changes stay inside the existing managed channel (`managed_channel.py`), the doctor (`doctor.py`), and the Claude launch guard (`plugins/pitwall/hooks/launch-guard.py`). One new parent-owned sidecar file records which defaulted asks were reported. The final task re-runs the installed-consumer journey with a real Codex child.

**Tech Stack:** Python 3.14.7 standard library, `unittest`, the component's own uv venv, Claude Code 2.1.278, Codex CLI.

**Spec:** `packages/agent-routing/docs/orchestrator-channel-redesign.md` (the design doc on this branch). Work happens in the worktree `~/git/pitwall-channel-redesign` on `design/event-driven-channel`, after the LOW-review fix commit (fd double-close, EPERM liveness, shared `TERMINAL_STATES`) has landed.

### Global Constraints

- Agent Routing runtime code stays standard-library-only.
- Run everything from `packages/agent-routing` with `.venv/bin/python`; never bare `python`.
- Tests are hermetic. Only Task 36 uses a live provider; the maintainer requested that run on 2026-09-22.
- Delivery stays at-least-once; answer writes stay exclusive and idempotent.
- Default doctor stays local and read-only and spends no provider quota.
- No machine-wide ban on subagents; the guard acts only while the session routing marker is active.

### Review Focus

- Parent reconnects after a default was applied → the default appears in `defaults_applied` on the next event, never lost.
- Parent calls `wait_dispatch` twice after one default → the `defaulted` event is returned once, not in a loop.
- `answer_and_wait` retried with the same choice after a lost response → returns the recorded answer, not an error.
- Native research `Agent` inside a routing turn whose own `Bash` call runs a shim → denied at that `Bash` call.
- A `Workflow` whose script cannot be read → denied in a routing turn (cannot inspect ⇒ not protected).

Tests for each line are in Tasks 31, 32, and 34.

### Lane ownership (if run as parallel lanes)

| Task | Exclusive files | Depends on |
| --- | --- | --- |
| 31, 32 (one lane, in order) | `runtime/model_routing/managed_channel.py`, `runtime/model_routing/mcp_tools.py`, `tests/test_mcp_event_channel.py` | LOW-review fix commit |
| 33 | `runtime/model_routing/doctor.py`, `tests/test_doctor.py` | — |
| 34 | `plugins/pitwall/hooks/launch-guard.py`, `tests/test_claude_tripwires.py` | — |
| 35 | `docs/orchestrator-channel.md`, `docs/orchestrator-channel-redesign.md` | 31, 33, 34 |
| 36 | `docs/isolated-consumer-acceptance-evidence.md` | 31–35, 37 |
| 37 | `plugins/pitwall/hooks/launch-guard.py`, `tests/test_claude_tripwires.py` | 34 |

Lane prompts start from `~/.claude/templates/lane-prompt.md`. Tasks 33 and 34 can run beside 31–32.

---

### Task 31: Defaulted asks reach the parent as an event

Finding 3. `wait_for_event` in `runtime/model_routing/managed_channel.py` skips an expired ask with `continue`. When the child writes the default, nothing is returned. The parent learns about it only from the final receipt. Returning the same default on every wait would loop forever, so this task adds the one delivery record the design doc permits once a test proves it is needed. The record is `launches/<uuid>/defaults_reported.json`, in the parent-owned sidecar. Every event also carries `defaults_applied`, so a lost response never loses a default.

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/managed_channel.py` (`wait_for_event`; new `_defaults_applied`, `_reported_defaults`, `_record_reported_default`)
- Modify: `packages/agent-routing/runtime/model_routing/mcp_tools.py` (descriptions of `dispatch_and_wait`, `wait_dispatch`, `answer_and_wait`, `steer_and_wait` list the `defaulted` event)
- Test: `packages/agent-routing/tests/test_mcp_event_channel.py`

**Interfaces:**
- Produces: event `{"event": "defaulted", "type": "defaulted", "dispatch_id", "ask_id", "choice", "answered_at", "reattach_handle"}`; key `defaults_applied: list[{"ask_id", "choice", "answered_at"}]` on every event returned by `wait_for_event`.

- [x] **Step 1: Let the fake child stay alive after its answer.** In `_ASKING_HARNESS`, after the `print(json.dumps(answer[...]))` line, add:

```python
time.sleep(float(os.environ.get("FAKE_AFTER_ANSWER_DELAY", "0")))
```

- [x] **Step 2: Write the failing tests** in `ManagedEventChannelTests`:

```python
def _call(self, name: str, arguments: dict) -> dict:
    return self.client.call(name, arguments, timeout=20)["result"]["structuredContent"]


def test_default_is_reported_once_as_an_event(self) -> None:
    self.client.close()
    self.env["FAKE_ASK_DEADLINE"] = "1"
    self.env["FAKE_AFTER_ANSWER_DELAY"] = "4"
    self.client = McpTestClient(self.env)
    self.client.initialize()
    first = self._call(
        "dispatch_and_wait", {"provider": "codex", "prompt": "default", "wait_seconds": 10}
    )
    self.assertEqual("ask", first["event"])
    self.assertEqual([], first["defaults_applied"])
    time.sleep(1.8)  # the child writes its default after the 1 s deadline
    second = self._call("wait_dispatch", {"dispatch_id": first["dispatch_id"], "wait_seconds": 5})
    self.assertEqual("defaulted", second["event"])
    self.assertEqual(("0001", "a"), (second["ask_id"], second["choice"]))
    third = self._call("wait_dispatch", {"dispatch_id": first["dispatch_id"], "wait_seconds": 1})
    self.assertIn(third["event"], {"still_running", "terminal"})
    self.assertEqual(["0001"], [d["ask_id"] for d in third["defaults_applied"]])


def test_terminal_after_default_lists_it(self) -> None:
    self.client.close()
    self.env["FAKE_ASK_DEADLINE"] = "1"
    self.client = McpTestClient(self.env)
    self.client.initialize()
    event = self._call(
        "dispatch_and_wait", {"provider": "codex", "prompt": "default", "wait_seconds": 10}
    )
    for _ in range(10):
        if event["event"] == "terminal":
            break
        time.sleep(0.3)
        event = self._call(
            "wait_dispatch", {"dispatch_id": event["dispatch_id"], "wait_seconds": 5}
        )
    self.assertEqual("terminal", event["event"])
    self.assertEqual(
        [("0001", "a")], [(d["ask_id"], d["choice"]) for d in event["defaults_applied"]]
    )
```

Update `test_child_deadline_applies_validated_default`: extend its allowed set to `{"ask", "still_running", "defaulted"}`, and treat `defaulted` like `still_running` in the re-wait branch.

- [x] **Step 3: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_mcp_event_channel -k default 2>&1 | tail -15`
Expected: the two new tests FAIL with `KeyError: 'defaults_applied'` (output shows `FAILED (errors=2)`).

- [x] **Step 4: Implement** in `managed_channel.py`:

```python
DEFAULTS_REPORTED = "defaults_reported.json"


def _defaults_applied(env: Mapping[str, str], dispatch_id: str) -> list[dict[str, Any]]:
    run_dir = _run_path(env, dispatch_id)
    if not run_dir.is_dir():
        return []
    try:
        answers = RunStore(state_root(env), dispatch_id).mailbox().answers()
    except MailboxError, OSError:
        return []
    return sorted(
        (
            {"ask_id": a["ask_id"], "choice": a["choice"], "answered_at": a.get("answered_at")}
            for a in answers
            if a.get("answered_by") == "default"
        ),
        key=lambda d: d["ask_id"],
    )


def _reported_defaults(env: Mapping[str, str], dispatch_id: str) -> set[str]:
    record = _read_json(_launch_path(env, dispatch_id) / DEFAULTS_REPORTED) or {}
    return {str(x) for x in record.get("askIds", [])}


def _record_reported_default(env: Mapping[str, str], dispatch_id: str, ask_id: str) -> None:
    launch_dir = _launch_path(env, dispatch_id)
    if not launch_dir.is_dir():
        return  # unmanaged runs keep defaults_applied only
    ids = sorted(_reported_defaults(env, dispatch_id) | {ask_id})
    atomic_write_json(launch_dir / DEFAULTS_REPORTED, {"schemaVersion": 1, "askIds": ids})
```

Rename the current `wait_for_event` body to `_next_event` (same signature). In its loop, after the terminal-result branch and before the pending-ask scan, add:

```python
        if run_doc is not None:
            reported = _reported_defaults(env, dispatch_id)
            for default in _defaults_applied(env, dispatch_id):
                if default["ask_id"] not in reported and default["ask_id"] != skip_ask_id:
                    _raise_if_cancelled(cancel)
                    _record_reported_default(env, dispatch_id, default["ask_id"])
                    return {
                        "event": "defaulted",
                        "type": "defaulted",
                        "dispatch_id": dispatch_id,
                        **default,
                        "reattach_handle": dispatch_id,
                    }
```

Then add the public wrapper:

```python
def wait_for_event(
    env,
    dispatch_id,
    cancel,
    progress=None,
    *,
    wait_seconds=DEFAULT_WAIT_SECONDS,
    skip_ask_id=None,
    steer_id=None,
) -> dict[str, Any]:
    """Wait for the next durable event; every event lists the defaults applied so far."""

    event = _next_event(
        env,
        dispatch_id,
        cancel,
        progress,
        wait_seconds=wait_seconds,
        skip_ask_id=skip_ask_id,
        steer_id=steer_id,
    )
    event["defaults_applied"] = _defaults_applied(env, require_dispatch_id(dispatch_id))
    return event
```

Keep the existing type annotations on the wrapper. Record before returning: if the MCP response is lost, the default still appears in `defaults_applied`.

- [x] **Step 5: Update tool descriptions** in `mcp_tools.py`. For `DISPATCH_AND_WAIT_TOOL`, `WAIT_DISPATCH_TOOL`, `ANSWER_AND_WAIT_TOOL`, and `STEER_AND_WAIT_TOOL`, list the returned events as `ask`, `defaulted`, `steer_ack`, `terminal`, orphan, or `still_running`. Add: "Every event lists `defaults_applied`."

- [x] **Step 6: Run to pass**

Run: `.venv/bin/python -m unittest tests.test_mcp_event_channel tests.test_managed_channel_edges 2>&1 | tail -15`
Expected: all pass.

- [x] **Step 7: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/managed_channel.py packages/agent-routing/runtime/model_routing/mcp_tools.py packages/agent-routing/tests/test_mcp_event_channel.py
git commit -m "feat(agent-routing): report defaulted asks to the managed parent"
```

### Task 32: Named managed-path tests for conflicting answers and the ask cap

Smaller point 3. `answer_once` already rejects conflicting answers, and `Mailbox.write_ask` enforces `max_asks`. Neither path is exercised through the managed MCP tools. If a test fails, fix `answer_once` or `wait_for_event` in the same task.

**Files:**
- Test: `packages/agent-routing/tests/test_mcp_event_channel.py`
- Modify only if a test fails: `packages/agent-routing/runtime/model_routing/managed_channel.py`

- [x] **Step 1: Add a two-ask harness** next to `_ORDERED_HARNESS`:

```python
_TWO_ASK_HARNESS = _ASKING_HARNESS.replace(
    'print(json.dumps(answer["result"]["structuredContent"], sort_keys=True), flush=True)',
    'second = request(3, "ask_orchestrator", {{"question": "Again?", "blocked_on": "choice", '
    '"severity": "normal", "options": [{{"id": "a", "text": "alpha"}}, {{"id": "b", "text": "beta"}}], '
    '"default": "a", "deadline_s": 60}})\n'
    "print(json.dumps(second, sort_keys=True), flush=True)",
)
```

The braces are doubled because the test calls `.format(launcher=...)` on the result, the same way `_ORDERED_HARNESS` is used.

- [x] **Step 2: Write the tests**

```python
def test_conflicting_duplicate_answer_is_rejected_and_same_choice_is_idempotent(self) -> None:
    first = self._call(
        "dispatch_and_wait", {"provider": "codex", "prompt": "ask", "wait_seconds": 20}
    )
    dispatch_id = first["dispatch_id"]
    done = self._call(
        "answer_and_wait",
        {"dispatch_id": dispatch_id, "ask_id": "0001", "choice": "b", "wait_seconds": 20},
    )
    self.assertEqual("terminal", done["event"])
    conflict = self.client.call(
        "answer_and_wait",
        {"dispatch_id": dispatch_id, "ask_id": "0001", "choice": "a", "wait_seconds": 1},
        timeout=20,
    )["result"]
    self.assertTrue(conflict["isError"])
    self.assertIn("conflicting answer", conflict["content"][0]["text"])
    retry = self._call(
        "answer_and_wait",
        {"dispatch_id": dispatch_id, "ask_id": "0001", "choice": "b", "wait_seconds": 1},
    )
    self.assertEqual("b", retry["answer"]["choice"])
    self.assertEqual("terminal", retry["event"])


def test_ask_over_the_cap_is_refused_without_hanging_the_parent(self) -> None:
    target = self.sandbox.bin / "codex"
    target.write_text(_TWO_ASK_HARNESS.format(launcher=str(LAUNCHER)), encoding="utf-8")
    target.chmod(0o755)
    first = self._call(
        "dispatch_and_wait",
        {"provider": "codex", "prompt": "cap", "max_asks": 1, "wait_seconds": 20},
    )
    self.assertEqual("ask", first["event"])
    done = self._call(
        "answer_and_wait",
        {"dispatch_id": first["dispatch_id"], "ask_id": "0001", "choice": "a", "wait_seconds": 20},
    )
    self.assertEqual("terminal", done["event"])
    answers = (
        self.sandbox.state
        / "subagent-model-routing"
        / "runs"
        / first["dispatch_id"]
        / "mailbox"
        / "answers"
    )
    self.assertEqual(["0001.json"], sorted(p.name for p in answers.glob("*.json")))
```

- [x] **Step 3: Run**

Run: `.venv/bin/python -m unittest tests.test_mcp_event_channel -k conflicting -k cap 2>&1 | tail -15`
Expected: PASS. On failure, fix the named function so the behavior above holds, then re-run.

- [x] **Step 4: Commit**

```bash
git add packages/agent-routing/tests/test_mcp_event_channel.py packages/agent-routing/runtime/model_routing/managed_channel.py
git commit -m "test(agent-routing): managed answers reject conflicts and respect the ask cap"
```

### Task 33: Doctor distinguishes an installed launch guard from none

Finding 2. `doctor._channel_checks` always emits `channel.launch_enforcement` as SKIP. It should read the Claude plugin install and report one of three states: available, unavailable, or no Claude host.

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/doctor.py` (`_channel_checks`; new `_claude_launch_guard_state`)
- Test: `packages/agent-routing/tests/test_doctor.py` (`ChannelDoctorTests`)

**Interfaces:**
- Produces: `_claude_launch_guard_state(env) -> tuple[bool | None, str]`, where `None` means no Claude config and `False` means unavailable (the string gives the reason). Check details: `{"enforcementAvailable": bool | None, "enforcementVerified": False}`.

- [x] **Step 1: Write failing tests** in `ChannelDoctorTests`:

```python
def _claude_home(self, home: Path, *, enabled: bool, guard: bool) -> None:
    install = home / ".claude/plugins/cache/pitwall/pitwall/0.0.0"
    (install / "hooks").mkdir(parents=True)
    pre = (
        [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/launch-guard.py"',
                    }
                ],
            }
        ]
        if guard
        else []
    )
    (install / "hooks/hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": pre}}), encoding="utf-8"
    )
    (home / ".claude/plugins/installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {"pitwall@pitwall": [{"scope": "user", "installPath": str(install)}]},
            }
        ),
        encoding="utf-8",
    )
    (home / ".claude/settings.json").write_text(
        json.dumps({"enabledPlugins": {"pitwall@pitwall": enabled}}), encoding="utf-8"
    )


def test_launch_enforcement_states(self) -> None:
    for enabled, guard, status in (
        (True, True, "PASS"),
        (False, True, "WARN"),
        (True, False, "WARN"),
    ):
        with self.subTest(enabled=enabled, guard=guard), tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._claude_home(home, enabled=enabled, guard=guard)
            env = {
                "HOME": str(home),
                "PATH": "/usr/bin:/bin",
                "XDG_STATE_HOME": str(home / "state"),
            }
            report = doctor.run_doctor(None, env)
            check = next(c for c in report["checks"] if c["id"] == "channel.launch_enforcement")
            self.assertEqual(status, check["status"])
            self.assertEqual(status == "PASS", check["details"]["enforcementAvailable"])
            self.assertFalse(check["details"]["enforcementVerified"])
            if status == "WARN":
                self.assertIn("unavailable", check["summary"])
```

The existing `test_server_handshake_and_registration_checks` (no `~/.claude`) keeps asserting SKIP.

- [x] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_doctor -k launch_enforcement -k handshake 2>&1 | tail -15`
Expected: `test_launch_enforcement_states` FAILs (status SKIP).

- [x] **Step 3: Implement** in `doctor.py`:

```python
def _claude_launch_guard_state(env: Mapping[str, str]) -> tuple[bool | None, str]:
    config = Path(
        env.get("CLAUDE_CONFIG_DIR") or Path(env.get("HOME", "~")) / ".claude"
    ).expanduser()
    if not config.is_dir():
        return None, "no Claude Code configuration found"
    try:
        installed = json.loads(
            (config / "plugins/installed_plugins.json").read_text(encoding="utf-8")
        )
        settings = json.loads((config / "settings.json").read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return False, "the pitwall Claude plugin is not installed"
    for key, entries in (installed.get("plugins") or {}).items():
        if not key.startswith("pitwall@"):
            continue
        if (settings.get("enabledPlugins") or {}).get(key) is not True:
            return False, f"the {key} Claude plugin is installed but not enabled"
        for entry in entries or []:
            try:
                hooks = json.loads(
                    (Path(entry["installPath"]) / "hooks/hooks.json").read_text(encoding="utf-8")
                )
            except KeyError, OSError, TypeError, json.JSONDecodeError:
                continue
            for group in (hooks.get("hooks") or {}).get("PreToolUse", []):
                if any(
                    "launch-guard.py" in str(h.get("command", "")) for h in group.get("hooks", [])
                ):
                    return True, f"{key} registers the PreToolUse launch guard"
        return False, f"the {key} Claude plugin has no PreToolUse launch guard"
    return False, "the pitwall Claude plugin is not installed"
```

Replace the fixed `channel.launch_enforcement` check in `_channel_checks`:

```python
available, reason = _claude_launch_guard_state(env)
checks.append(
    DoctorCheck(
        id="channel.launch_enforcement",
        category=CHANNEL_CATEGORY,
        status="SKIP" if available is None else ("PASS" if available else "WARN"),
        summary=(
            reason
            if available is None
            else f"launch guard installed: {reason}; default doctor does not exercise a live denial"
            if available
            else f"launch enforcement unavailable: {reason}"
        ),
        remediation="install and enable the pitwall Claude plugin (`claude plugin install pitwall@pitwall`)",
        details={"enforcementAvailable": available, "enforcementVerified": False},
    )
)
```

- [x] **Step 4: Run to pass**

Run: `.venv/bin/python -m unittest tests.test_doctor 2>&1 | tail -15`
Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/doctor.py packages/agent-routing/tests/test_doctor.py
git commit -m "feat(agent-routing): doctor reports launch enforcement as available or unavailable"
```

### Task 34: Launch guard blocks external launches, not native research

Smaller point 2. In a routing turn, `launch-guard.py` `main()` denies every native `Agent` (`kind = "native-agent"`) and every `Workflow`. The narrower rule: deny shim agent types, prompts or scripts that name a shim or provider launch, and scripts that cannot be read. Allow native research agents. This is safe only if the host runs `PreToolUse` on a subagent's own tool calls under the parent `session_id`, so that a native agent's shim `Bash` call is still denied. Step 1 checks that first.

**Files:**
- Modify: `packages/agent-routing/plugins/pitwall/hooks/launch-guard.py` (`main`; new `_workflow_launch`)
- Test: `packages/agent-routing/tests/test_claude_tripwires.py`
- Evidence: `packages/agent-routing/docs/claude-host-acceptance-evidence.md` (new section "Subagent tool calls reach PreToolUse")

- [x] **Step 1: Host probe.** Ran 2026-09-22 with Claude Code 2.1.278 (probe dir `/tmp/hookprobe.yzDn`). Result: the subagent's `Bash` call reached `PreToolUse` with the parent `session_id` (`eb44b301…`), `agent_id` `af32aa30f45ccd9e9`, and `agent_type` `general-purpose`, so the narrowing below proceeds. Original procedure, kept for reproduction: Create a disposable settings file whose `PreToolUse` hook appends each payload to a log:

```bash
T=$(mktemp -d); cat > $T/settings.json <<JSON
{"hooks":{"PreToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"cat >> $T/pre.jsonl; echo >> $T/pre.jsonl"}]}]}}
JSON
claude -p --settings $T/settings.json --model haiku \
  'Use the Agent tool with subagent_type general-purpose. Tell it to run the Bash command `printf probe` and report the output.'
.venv/bin/python -c "import json,sys;[print(d.get('tool_name'),d.get('session_id'),d.get('agent_id')) for d in map(json.loads,filter(str.strip,open('$T/pre.jsonl')))]"
```

Expected: one `Agent` line and one `Bash` line with the same `session_id`, and a non-empty `agent_id` on the `Bash` line. Record the redacted lines in the evidence doc.
**Decision rule:** if the `Bash` line is missing or has a different `session_id`, stop this task. Replace Steps 2–5 with a design-doc edit that records the probe as the reason native agents stay blocked, and keep `test_denies_native_agents_while_routing_is_active`. Otherwise continue.

- [x] **Step 2: Write the failing tests.** In `test_claude_tripwires.py`, replace `test_denies_native_agents_while_routing_is_active` and `test_workflow_is_denied_while_routing_is_active` with:

```python
def test_allows_native_research_agents_while_routing_is_active(self) -> None:
    for tool_input in (
        {
            "subagent_type": "Explore",
            "prompt": "find the doctor checks",
            "run_in_background": False,
        },
        {
            "subagent_type": "general-purpose",
            "prompt": "summarise README.md",
            "run_in_background": True,
        },
    ):
        with self.subTest(tool_input=tool_input):
            self.assertIsNone(run_launch_guard(self._payload("Agent", tool_input)))


def test_native_agent_shim_bash_call_is_denied(self) -> None:
    payload = self._payload("Bash", {"command": "~/.claude/scripts/codex-shim.sh prompt.md"})
    payload["agent_id"] = "a1b2c3"
    response = run_launch_guard(payload)
    self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])


def test_workflow_denied_only_when_it_launches_external_models(self) -> None:
    denied = (
        {"script": "await agent('x', {agentType: 'pitwall:codex-shim'})"},
        {"script": "await codex('review the diff')"},
        {"scriptPath": "/nonexistent/workflow.js"},
        {"name": "saved-workflow"},
    )
    for tool_input in denied:
        with self.subTest(tool_input=tool_input):
            response = run_launch_guard(self._payload("Workflow", tool_input))
            self.assertEqual("deny", response["hookSpecificOutput"]["permissionDecision"])
    self.assertIsNone(
        run_launch_guard(self._payload("Workflow", {"script": "await agent('summarise README')"}))
    )
```

In `test_dag_command_expansion_activates_before_first_child_launch`, change `first_launch` to `self._payload("Bash", {"command": "codex-shim.sh prompt.md"})`.

- [x] **Step 3: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_claude_tripwires 2>&1 | tail -15`
Expected: the research-agent test and the allowed-workflow assertion FAIL.

- [x] **Step 4: Implement** in `launch-guard.py`:

```python
SHIM_HELPER_RE = re.compile(
    r"\b(?:codex|agy|grok|kimi|qwen|pi|hermes|cline|muse|goose|dsh|route|glm|minimax)\s*\("
)


def _workflow_launch(tool_input: Mapping[str, Any]) -> str | None:
    script = tool_input.get("script")
    if not isinstance(script, str):
        path = tool_input.get("scriptPath")
        try:
            script = Path(str(path)).expanduser().read_text(encoding="utf-8") if path else None
        except OSError:
            script = None
    if script is None:
        return "workflow-uninspectable"
    lowered = script.lower()
    if (
        any(agent_type in lowered for agent_type in SHIM_AGENT_TYPES)
        or SHIM_HELPER_RE.search(script)
        or PROMPT_SHIM_RE.search(script)
        or _command_launch(script)
    ):
        return "workflow-shim"
    return None
```

In `main()`, delete the `kind = "native-agent"` fallback so a native `Agent` with no recognized launch leaves `kind` as `None`. Replace the `workflow` branch with `kind = _workflow_launch(tool_input)`. Update the comments on both branches: native agents are allowed because their own tool calls pass through this hook under the parent session.

- [x] **Step 5: Run to pass**

Run: `.venv/bin/python -m unittest tests.test_claude_tripwires 2>&1 | tail -15`
Expected: all pass.

- [x] **Step 6: Commit**

```bash
git add packages/agent-routing/plugins/pitwall/hooks/launch-guard.py packages/agent-routing/tests/test_claude_tripwires.py packages/agent-routing/docs/claude-host-acceptance-evidence.md
git commit -m "fix(agent-routing): launch guard blocks external launches, not native research agents"
```

### Task 35: Documentation matches the behavior

Findings: session isolation undocumented (smaller point 1) and the local path in the design doc (housekeeping). This task also brings the design doc in line with Tasks 31, 33, and 34.

**Files:**
- Modify: `packages/agent-routing/docs/orchestrator-channel.md` (section "Managed parent-child exchange")
- Modify: `packages/agent-routing/docs/orchestrator-channel-redesign.md` (Status, "Prevent disconnected launches", "Implementation boundaries", acceptance cases 5, 7, 8)

- [x] **Step 1: `orchestrator-channel.md`.** Append to "Managed parent-child exchange":

```markdown
When an ask's deadline passes, the child applies its validated default and the parent's next wait returns a `defaulted` event for that ask once. Every event also lists `defaults_applied`, so a parent that reconnects still sees every default.

Session isolation: run state is private to your OS user (`0700`/`0600`). It is not isolated between two sessions of the same user. Any local session that knows a full dispatch ID can wait on, answer, or steer that run. A dispatch ID is a handle, not a credential.
```

- [x] **Step 2: `orchestrator-channel-redesign.md`.**
  - Status: replace the sentence naming `~/.local/share/pitwall/releases/20260922-channel-redesign-candidate/packages/agent-routing` with "The active installation uses a staged release candidate of this component."
  - "Implementation boundaries", first bullet: add "The `defaulted` event is the one delivery record this rule allowed: `launches/<uuid>/defaults_reported.json` (Task 31 test `test_default_is_reported_once_as_an_event`)."
  - "Prevent disconnected launches" and acceptance case 5: replace "must refuse native `Agent`/shim Workflow launches" with "must refuse shim `Agent` types, native agents or Workflow scripts that name a shim or provider launch, and Workflow scripts it cannot read. Native research agents are allowed because the host runs `PreToolUse` on their own tool calls under the parent session (see claude-host-acceptance-evidence.md)." Use this wording only if Task 34 Step 1 confirmed that. Otherwise record the probe result as the reason native agents stay blocked.
  - Acceptance case 8: add "Doctor reports `channel.launch_enforcement` as WARN 'launch enforcement unavailable' when the plugin or its PreToolUse guard is missing, PASS when installed and enabled, and SKIP without a Claude configuration."

- [x] **Step 3: Validate**

Run from the repo root: `grep -nE "/home/[a-z]+/" packages/agent-routing/docs/orchestrator-channel-redesign.md; make docs-check 2>&1 | tail -10`
Expected: no home-directory path match, and the doc gate passes.

- [x] **Step 4: Commit**

```bash
git add packages/agent-routing/docs/orchestrator-channel.md packages/agent-routing/docs/orchestrator-channel-redesign.md
git commit -m "docs(agent-routing): defaulted events, session isolation boundary, narrowed guard"
```

### Task 36: Live acceptance with a real non-Claude child

Finding 1. The only live exchange used a Claude Haiku child. This task repeats acceptance case 1 with a real Codex child through an installed consumer, in the same shape as `isolated-consumer-acceptance-evidence.md` "Real Claude parent and child exchange". It also re-runs the rows that Tasks 33 and 34 changed. It uses the maintainer's Codex subscription (requested 2026-09-22), and the child prompt is capped to one short answer.

**Files:**
- Modify: `packages/agent-routing/docs/isolated-consumer-acceptance-evidence.md` (new sections "Real Codex child exchange", "Narrowed guard matrix", "Doctor launch-enforcement states"; refreshed SHA-256 table)

- [x] **Step 1: Stage the installed consumer**

```bash
T=/tmp/pitwall-isolated-consumer-codex; rm -rf $T; mkdir -p $T/{home,bin,state,config,cache,codex}
cp -a packages/agent-routing $T/component
cp ~/.codex/auth.json ~/.codex/config.toml $T/codex/
HOME=$T/home XDG_STATE_HOME=$T/state XDG_CONFIG_HOME=$T/config XDG_CACHE_HOME=$T/cache \
  PITWALL_AGENT_ROUTING_BIN_DIR=$T/bin CODEX_HOME=$T/codex $T/component/scripts/install.sh --with-mcp
HOME=$T/home CODEX_HOME=$T/codex $T/bin/pitwall-agent-routing setup mcp --harness codex --command $T/bin/pitwall-agent-routing --yes
```

Expected: the installer doctor reports 0 failures, and `$T/codex/config.toml` has `[mcp_servers.pitwall-channel]` pointing at `$T/bin/pitwall-agent-routing`.

- [x] **Step 2: Run the parent/child exchange**

```bash
cat > $T/mcp.json <<JSON
{"mcpServers":{"pitwall-channel":{"command":"$T/bin/pitwall-agent-routing","args":["mcp"],
 "env":{"XDG_STATE_HOME":"$T/state","CODEX_HOME":"$T/codex","HOME":"$T/home"}}}}
JSON
claude -p --model haiku --mcp-config $T/mcp.json --strict-mcp-config \
  'Call mcp__pitwall-channel__dispatch_and_wait with provider "codex" and prompt: "Before doing anything, call ask_orchestrator with question \"Include the exchange?\", blocked_on \"choice\", severity \"blocking\", options include/omit, default omit, deadline_s 120. Then reply with the chosen id only." When it returns an ask, answer it with answer_and_wait choosing include. Finally print EVENT_SEQUENCE followed by each tool name and returned event.'
```

Expected: the output shows `dispatch_and_wait=ask`, `answer_and_wait=terminal`, and `choice=include`, with a terminal `succeeded/ok` whose receipt names provider `codex`. There is no `inbox` call. Record the dispatch ID, event order, provider/model, and child exit in the evidence doc, with the same redaction as the Claude section.

- [x] **Step 3: Re-run the changed rows** in a fresh Claude session with the installed plugin from `$T/component/plugins/pitwall`, starting with `/pitwall:dag-routing`. Check that a native `Explore` Agent is allowed, that the native Agent's own `Bash` call to `./definitely-missing/codex-shim.sh --help` is denied, and that a Workflow script containing `codex(` is denied. Then run `$T/bin/pitwall-agent-routing doctor --json` twice: once with the plugin enabled (expect `channel.launch_enforcement` PASS) and once with it disabled in `$T/home/.claude/settings.json` (expect WARN "launch enforcement unavailable"). Record each result in the evidence doc.

- [x] **Step 4: Refresh hashes and the status line.** Regenerate the SHA-256 table for the frozen files with `sha256sum`. Update the redesign doc's Status to "Acceptance cases 1–8 pass with a real Codex child and a real Claude child."

- [x] **Step 5: Full gate**

Run: `.venv/bin/python -m unittest discover -s tests 2>&1 | tail -15`
Expected: all pass. Then clean up: `rm -rf /tmp/pitwall-isolated-consumer-codex`.

- [x] **Step 6: Commit**

```bash
git add packages/agent-routing/docs/isolated-consumer-acceptance-evidence.md packages/agent-routing/docs/orchestrator-channel-redesign.md
git commit -m "docs(agent-routing): installed acceptance with a real Codex child"
```

### Task 37: Routing leases are released on a real host

Found while executing this batch (2026-09-22), not by the review. In a real Claude Code session, all four `dispatch_and_wait` leases stayed in `routing-sessions/<session>.json` after their dispatches finished. The routing guard then stayed on and blocked provider commands in every later turn. There are two causes:

1. **MCP results arrive as a JSON string.** A captured `PostToolUse` payload for `mcp__pitwall-channel__inbox` had `"tool_response": "{\"asks\":[],...}"`, a string. `_response_details` in `launch-guard.py` only looks inside mappings and lists, so it never finds `"event": "terminal"`, and no lease is ever released. The existing tests pass only because they pass `tool_response` as a dict.
2. **Backgrounded calls never report completion.** Claude Code moves an MCP call that runs over 120 s to the background, and its later result produces no `PostToolUse`. That lease never learns its dispatch ID and is never released.

The fix: parse JSON strings in the hook result, and at `Stop`, check each lease against durable state instead of relying only on `PostToolUse`.

**Files:**
- Modify: `packages/agent-routing/plugins/pitwall/hooks/launch-guard.py` (`_response_details`, `_activate_marker`, `_clear_if_idle`, `_marker_is_present`; new `_state_root`, `_lease_live`, `_any_managed_launch_live`)
- Test: `packages/agent-routing/tests/test_claude_tripwires.py`

**Interfaces:**
- Produces: each lease in `pending_dispatches` carries `created_at` (epoch float). `_lease_live(lease, now) -> bool`.

- [x] **Step 1: Write failing tests.** Add them next to `test_dispatch_terminal_releases_one_lease_and_preserves_sibling`, reusing its `_payload` and `run_launch_guard` helpers and the marker directory from `run_launch_guard` (`Path(_HOOK_HOME) / "routing-markers"`):

```python
def test_string_tool_response_releases_the_lease(self) -> None:
    start = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "q38-cline"})
    start["tool_use_id"] = "toolu_string"
    self.assertIsNone(run_launch_guard(start, active=False))
    done = {
        "hook_event_name": "PostToolUse",
        "session_id": start["session_id"],
        "tool_name": start["tool_name"],
        "tool_use_id": "toolu_string",
        "tool_response": json.dumps({"event": "terminal", "dispatch_id": "d-string"}),
    }
    self.assertIsNone(run_launch_guard(done, active=None))
    marker = Path(_HOOK_HOME) / "routing-markers" / f"{start['session_id']}.json"
    self.assertFalse(marker.exists())


def test_stop_drops_leases_whose_dispatch_finished_without_a_post_hook(self) -> None:
    start = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "q38-cline"})
    start["tool_use_id"] = "toolu_backgrounded"
    self.assertIsNone(run_launch_guard(start, active=False))
    marker = Path(_HOOK_HOME) / "routing-markers" / f"{start['session_id']}.json"
    record = json.loads(marker.read_text(encoding="utf-8"))
    record["pending_dispatches"][0]["created_at"] -= 600  # older than the preflight window
    marker.write_text(json.dumps(record), encoding="utf-8")
    stop = {"hook_event_name": "Stop", "session_id": start["session_id"]}
    self.assertIsNone(run_launch_guard(stop, active=None))
    self.assertFalse(marker.exists())


def test_stop_keeps_a_lease_while_its_dispatch_is_running(self) -> None:
    dispatch_id = str(uuid.uuid4())
    state = Path(hook_env()["XDG_STATE_HOME"]) / "subagent-model-routing"
    (state / "launches" / dispatch_id).mkdir(parents=True)
    (state / "launches" / dispatch_id / "launcher.json").write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )
    start = self._payload("mcp__pitwall-channel__dispatch_and_wait", {"route": "q38-cline"})
    start["tool_use_id"] = "toolu_live"
    self.assertIsNone(run_launch_guard(start, active=False))
    still = {
        "hook_event_name": "PostToolUse",
        "session_id": start["session_id"],
        "tool_name": start["tool_name"],
        "tool_use_id": "toolu_live",
        "tool_response": json.dumps({"event": "still_running", "dispatch_id": dispatch_id}),
    }
    self.assertIsNone(run_launch_guard(still, active=None))
    self.assertIsNone(
        run_launch_guard(
            {"hook_event_name": "Stop", "session_id": start["session_id"]}, active=None
        )
    )
    marker = Path(_HOOK_HOME) / "routing-markers" / f"{start['session_id']}.json"
    self.assertTrue(marker.exists())
    (state / "runs" / dispatch_id).mkdir(parents=True)
    (state / "runs" / dispatch_id / "result.json").write_text("{}", encoding="utf-8")
    self.assertIsNone(
        run_launch_guard(
            {"hook_event_name": "Stop", "session_id": start["session_id"]}, active=None
        )
    )
    self.assertFalse(marker.exists())
```

Check first that `hook_env()` sets `XDG_STATE_HOME` to a temp directory. If it doesn't, set it in these tests before calling `run_launch_guard`, and pass it through the env that `run_launch_guard` builds, so the hook never reads the real `~/.local/state`. Add `import os`, `import uuid` if missing.

- [x] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_claude_tripwires -k lease -k string_tool_response 2>&1 | tail -15`
Expected: the three new tests FAIL (the marker file still exists).

- [x] **Step 3: Implement** in `launch-guard.py`:

```python
PREFLIGHT_WINDOW_SECONDS = 120.0


def _state_root() -> Path:
    configured_state = os.environ.get("SUBAGENT_MODEL_ROUTING_STATE_HOME")
    if configured_state:
        return Path(configured_state).expanduser()
    base = Path(
        os.environ.get(
            "XDG_STATE_HOME",
            str(Path(os.environ.get("HOME", "~")).expanduser() / ".local" / "state"),
        )
    )
    return base / "subagent-model-routing"


def _pid_running(pid: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _dispatch_running(dispatch_id: str) -> bool:
    root = _state_root()
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", dispatch_id):
        return False
    if (root / "runs" / dispatch_id / "result.json").exists():
        return False
    if (root / "launches" / dispatch_id / "orphan.json").exists():
        return False
    try:
        launcher = json.loads(
            (root / "launches" / dispatch_id / "launcher.json").read_text(encoding="utf-8")
        )
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return False
    return isinstance(launcher, dict) and _pid_running(launcher.get("pid"))


def _any_managed_launch_live() -> bool:
    try:
        entries = list((_state_root() / "launches").iterdir())
    except OSError:
        return False
    return any(_dispatch_running(entry.name) for entry in entries if entry.is_dir())


def _lease_live(lease: Mapping[str, Any], now: float) -> bool:
    dispatch_id = lease.get("dispatch_id")
    if isinstance(dispatch_id, str) and dispatch_id:
        return _dispatch_running(dispatch_id)
    created = lease.get("created_at")
    if isinstance(created, (int, float)) and now - created < PREFLIGHT_WINDOW_SECONDS:
        return True
    return _any_managed_launch_live()
```

- `_pending_dispatches`: keep a numeric `created_at` when present.
- `_activate_marker`: new leases get `"created_at": time.time()` (add `import time`).
- `_response_details.visit`: add a first branch. If `value` is a `str` under 1 MiB whose stripped form starts with `{` or `[`, `json.loads` it (ignore `ValueError`) and visit the result.
- `_clear_if_idle`: replace `if _pending_dispatches(record): return` with: filter the pending leases through `_lease_live(lease, time.time())`. If any remain, write them back and return; otherwise unlink the marker.
- `_marker_is_present`: when `record.get("routing_active")` is not true and no pending lease is live, return `False`.

- [x] **Step 4: Run to pass**

Run: `.venv/bin/python -m unittest tests.test_claude_tripwires 2>&1 | tail -5` then `.venv/bin/ruff check runtime tests plugins 2>&1 | tail -3`
Expected: OK; All checks passed!

- [x] **Step 5: Commit**

```bash
git add packages/agent-routing/plugins/pitwall/hooks/launch-guard.py packages/agent-routing/tests/test_claude_tripwires.py
git commit -m "fix(agent-routing): routing leases are released on a real Claude host"
```

Task 36 Step 3 also checks this on a real host. After the Codex exchange there, the session's `routing-sessions/<session>.json` must be gone once the turn ends.
