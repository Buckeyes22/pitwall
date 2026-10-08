# Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tick each box in this file in the same commit that completes the step.

**Goal:** Close every finding in the 2026-09-22 intent-versus-implementation review and finish the unfinished work carried in from the merged worktrees, so local `main` is green, its claims are true, and it is ready to push.

**Architecture:** Six phases ordered by dependency. Phase 1 makes the integrated tree deterministic and green. Phase 2 fixes product defects (catalogue data, CUDA placement, the in-pod deadline, gateway routing). Phase 3 turns scheduled CI green. Phase 4 makes the documentation true. Phase 5 produces the live evidence the support claims need, under explicit spend caps. Phase 6 finishes the release-acceptance and Pi Workbench work merged from the worktrees and ends with the push. The journey-coverage plan runs between Phase 4 and Phase 5.

**Tech Stack:** Python 3.14.7 (uv, pytest, pydantic v2, httpx, asyncpg), the standard-library Agent Routing component, Node 22 + TypeScript (`packages/gateway`, `packages/pi-workbench`, vitest), GitHub Actions.

**Spec:** [`docs/evidence/2026-09-22-intent-vs-implementation-review.md`](../../evidence/2026-09-22-intent-vs-implementation-review.md). Finding numbers below (R1–R16) refer to its section 3 table.

## Execution order across both plans

1. This plan, Phases 1–4.
2. [`2026-09-23-journey-coverage.md`](2026-09-23-journey-coverage.md), every task.
3. This plan, Phases 5–6. Task 28 is the push.

## Global Constraints

- Work on `main` locally. Commit after every task with `git commit -s` and the session attribution trailers. Push nothing until Task 28.
- Root Python runs through `uv run --frozen`; never bare `python`. Agent Routing runs from `packages/agent-routing` with `.venv/bin/python`.
- Hermetic tests make no provider calls; `tests/conftest.py` blocks provider DNS. Live steps run only inside the Phase 5 and Phase 6 tasks that name them, with the spend cap written in the step.
- Credentials never appear in argv, logs, committed files, or evidence. Evidence cites environment-variable names only.
- Money is `Decimal`. Migrations are append-only.
- Every behavior change lands with the test that fails before it and passes after it, in the same commit.
- Do not weaken, skip, or delete an existing test to get green. If a test is wrong, fix the test and say why in the commit body.
- Do not run `/code-review` at high effort or any multi-agent workflow while executing this plan.

## Review Focus

- A gateway request whose `x-pitwall-route` header is absent while a route table is loaded must be refused with a structured 400, never sent to an arbitrary upstream (Task 8 step 1 pins it).
- A dossier `min_cuda` written with stray quotes, a leading `v`, or three components must fail catalogue load, not fail after a pod exists (Task 5 step 1 pins it).
- A live CUDA list that offers nothing at or above the floor must refuse before any pod write on both the personal and registry paths (Task 6 step 1 pins it).
- A second SIGINT during deferred collection must still end with a receipt, not a traceback (Task 1 step 1 pins it).
- A keyed gateway route whose key variable is unset must return a structured 503 without contacting the upstream (Task 8 step 1 pins it).

---

## Phase 1 — Make the integrated tree deterministic and green

### Task 1: The run recorder keeps output flushed before an interrupt (R15, release-acceptance carry-in)

`tools/release_acceptance/run_recorder.py::record_run` loses child output when SIGINT arrives while `subprocess.Popen.communicate()` is between `os.read()` returning the child's bytes and appending them to its buffer: the `KeyboardInterrupt` unwinds through that gap and the bytes are gone, while the receipt still claims `output.complete: true`. Reproduced 5/12 on `feat/release-acceptance` and 1/15 under instrumentation (`COMM interrupted` → resumed collection `len=0`). The fix defers SIGINT for the whole owned-process lifetime, then re-raises it after the receipt is written.

**Files:**
- Modify: `tools/release_acceptance/run_recorder.py` (`record_run`, new `_DeferredInterrupt`)
- Test: `tests/release_acceptance/test_run_recorder.py`

**Interfaces:**
- Produces: `_DeferredInterrupt` context manager with attributes `count: int` and method `pending() -> bool`; `record_run` behavior unchanged except that interrupts no longer interrupt collection.

- [x] **Step 1: Write the failing tests**

Add to `tests/release_acceptance/test_run_recorder.py`:

```python
def test_sigint_never_loses_flushed_output_across_repeats(
    source_repo: Path, tmp_path: Path
) -> None:
    # The race needs many attempts to show; 30 is enough to fail the old code.
    for attempt in range(30):
        output_root = tmp_path / f"sigint-{attempt}"
        completed = _run_sigint_wrapper(source_repo, output_root)
        assert completed.returncode != 0
        [run_dir] = list(output_root.glob("run-*"))
        saved = json.loads((run_dir / "run.json").read_text())
        assert saved["outcome"] == "interrupted"
        assert saved["output"]["complete"] is True
        assert b"interrupt-stdout" in Path(saved["output"]["stdout"]).read_bytes(), attempt


def test_second_sigint_during_collection_still_writes_a_receipt(
    source_repo: Path, tmp_path: Path
) -> None:
    output_root = tmp_path / "double-sigint"
    completed = _run_sigint_wrapper(source_repo, output_root, signals=2)
    assert completed.returncode != 0
    [run_dir] = list(output_root.glob("run-*"))
    saved = json.loads((run_dir / "run.json").read_text())
    assert saved["outcome"] == "interrupted"
    assert saved["interrupted"] is True
    assert "Traceback" not in completed.stderr.decode()
```

Extract the existing `subprocess.run([...wrapper...])` body of `test_real_sigint_retain_flushed_output_before_re_raise` into a module helper `_run_sigint_wrapper(source_repo, output_root, signals=1)` whose child code sends `signals` SIGINTs to its parent (`for _ in range(signals): os.kill(os.getppid(), signal.SIGINT); time.sleep(0.05)`), and make the existing test call it. In the wrapper, catch the final `KeyboardInterrupt` with `except KeyboardInterrupt: sys.exit(130)` so a clean re-raise does not print a traceback.

- [x] **Step 2: Run to verify they fail**

Run: `uv run --frozen pytest -q -p no:randomly tests/release_acceptance/test_run_recorder.py -k "sigint"`
Expected: `test_sigint_never_loses_flushed_output_across_repeats` FAILS with `assert b'interrupt-stdout' in b''` on some attempt.

- [x] **Step 3: Implement deferral**

Add near the other helpers in `tools/release_acceptance/run_recorder.py`:

```python
class _DeferredInterrupt:
    """Record SIGINT instead of raising it while the recorder owns a process.

    CPython can raise KeyboardInterrupt between ``os.read`` returning child bytes
    and ``communicate`` storing them, which silently drops output. Deferring the
    signal keeps collection atomic; ``record_run`` re-raises after the receipt.
    """

    def __init__(self) -> None:
        self.count = 0
        self._previous: Any = None
        self._installed = False

    def __enter__(self) -> _DeferredInterrupt:
        if threading.current_thread() is threading.main_thread():
            self._previous = signal.signal(signal.SIGINT, self._handle)
            self._installed = True
        return self

    def _handle(self, _signum: int, _frame: Any) -> None:
        self.count += 1

    def pending(self) -> bool:
        return self.count > 0

    def __exit__(self, *_exc: object) -> None:
        if self._installed:
            signal.signal(signal.SIGINT, self._previous)
```

Import `threading` at the top. In `record_run`, wrap everything from the `try: process = subprocess.Popen(...)` statement through the `finally` block's end in `with _DeferredInterrupt() as deferred:`. After that block, and before the existing `if interrupted is not None:` receipt logic, add:

```python
    if interrupted is None and deferred.pending():
        interrupted = KeyboardInterrupt()
        if process is not None and process.poll() is None:
            term_sent = _send_group_signal(process.pid, signal.SIGTERM) or term_sent
            final_stdout, final_stderr, final_expired = _communicate(process, terminate_grace_s)
            raw_stdout = _prefer_latest_output(raw_stdout, final_stdout)
            raw_stderr = _prefer_latest_output(raw_stderr, final_stderr)
            if final_expired:
                kill_sent = _send_group_signal(process.pid, signal.SIGKILL) or kill_sent
                mark_output_loss("pipes remained open after bounded process-group cleanup")
            _close_process_pipes(process)
            pipes_closed = True
```

With the handler installed, the child's SIGINT no longer interrupts the first `_communicate`, which returns when the SIGTERM path above runs, so the existing `except KeyboardInterrupt` branches stay as defense for non-main-thread callers.

- [x] **Step 4: Verify**

Run: `for i in 1 2 3; do uv run --frozen pytest -q -p no:randomly tests/release_acceptance/test_run_recorder.py; done`
Expected: three runs, each ending `passed`, zero failures.

- [x] **Step 5: Commit**

```bash
git add tools/release_acceptance/run_recorder.py tests/release_acceptance/test_run_recorder.py
git commit -s -m "fix(release-acceptance): defer SIGINT while the recorder owns a process so flushed output is never lost"
```

### Task 2: Reconcile the two `packages/pi-workbench` copies (R15)

The merge kept the pi-workbench worktree copy and added `tests/dynamic-contract-acceptance.test.ts` from the release-acceptance copy (`0e7f232`). Two of its cases fail against the kept sources: `applies both the tool deadline and the planning block when both are enabled` and `registers the validated profile provider identity, providerConfig, api, and stream wrapper`. The release-acceptance copy also carried about 196 lines absent from the kept copy (for example `readJsonFile` in `src/cli.ts` and the forced-repair comparison task in `scripts/comparison-runner.ts`).

**Files:**
- Modify: `packages/pi-workbench/src/*.ts`, `packages/pi-workbench/scripts/*.ts`, `packages/pi-workbench/tests/dynamic-contract-acceptance.test.ts` (as determined by the diff in step 1)
- Create: `packages/pi-workbench/docs/merge-reconciliation.md`

- [x] **Step 1: Produce the line-level delta**

Run: `git diff d71c0d6 0e7f232 -- packages/pi-workbench/src packages/pi-workbench/scripts > /tmp/pi-copy-delta.diff; grep -c '^+[^+]' /tmp/pi-copy-delta.diff`
Expected: a count near 196.

- [x] **Step 2: Classify every added hunk in `merge-reconciliation.md`**

For each `+` hunk write one row: file, function, `older-version` (the kept copy already contains a newer form of the same logic — cite the kept function) or `missing-capability` (the kept copy lacks it). Every hunk gets a row.

- [x] **Step 3: Port each `missing-capability` hunk**

Apply it to the kept file, adapted to the kept file's current names. Add or keep a vitest case that exercises it; for `readJsonFile`, the existing `tests/cli.test.ts` "malformed profile JSON" case covers it.

- [x] **Step 4: Make the two failing contract cases pass against the real sources**

Run: `cd packages/pi-workbench && npx vitest run tests/dynamic-contract-acceptance.test.ts`
For each failure, correct whichever side is wrong: when the test asserts an older API shape that the kept source deliberately replaced (the classification row says `older-version`), update the assertion to the kept shape and keep its oracle strength; otherwise fix the source. Record which in `merge-reconciliation.md`.

- [x] **Step 5: Verify**

Run: `cd packages/pi-workbench && npx tsc --noEmit && npx vitest run 2>&1 | tail -3`
Expected: typecheck silent; `Test Files  36 passed`, `0 failed`.

- [x] **Step 6: Commit**

```bash
git add packages/pi-workbench
git commit -s -m "fix(pi-workbench): reconcile the release-acceptance package copy into the kept sources"
```

### Task 3: Remove wall-clock sensitivity from the restricted-tool test (R15)

`packages/pi-workbench/tests/restricted.test.ts` "restricted tool filter is passed by unlinked fd across sequential tool calls" passes alone (6/6, three runs) and times out at vitest's default 5,000 ms when the machine is loaded.

**Files:**
- Modify: `packages/pi-workbench/tests/restricted.test.ts`

- [x] **Step 1: Give the case an explicit budget that reflects its sequential subprocess work**

Change the case signature to `test("restricted tool filter is passed by unlinked fd across sequential tool calls", async () => { ... }, 30_000);` and replace any fixed `setTimeout` waits inside it with awaiting the child's exit promise.

- [x] **Step 2: Verify under load**

Run in one shell: `cd ~/git/pitwall && uv run --frozen pytest -q -m "not integration and not slow" >/dev/null &` and in the same shell: `cd packages/pi-workbench && for i in 1 2 3; do npx vitest run tests/restricted.test.ts 2>&1 | grep 'Tests '; done; wait`
Expected: `Tests  6 passed (6)` three times.

- [x] **Step 3: Commit**

```bash
git add packages/pi-workbench/tests/restricted.test.ts
git commit -s -m "test(pi-workbench): restricted-tool fd test waits on the child, not the clock"
```

### Task 4: Gateway supervisor tests stop reading the real PATH (R9)

`src/pitwall/personal/gateway.py::default_argv` returns `[launcher, "--port", ...]` when `shutil.which("pitwall-gateway")` finds an installed launcher and the checkout's `dist/shim.js` is unbuilt; `tests/personal/test_gateway_supervisor.py::test_default_argv_is_absolute_and_independent_of_cwd` indexes `DEFAULT_ARGV[1]` and `test_start_spawns_stock_sidecar_and_persists_owner_only_state` compares against the module constant, so both fail on any machine with the gateway installed.

**Files:**
- Modify: `src/pitwall/personal/gateway.py` (`default_argv`)
- Modify: `tests/personal/test_gateway_supervisor.py`

**Interfaces:**
- Produces: `default_program(*, which: Callable[[str], str | None] = shutil.which, shim: Path = _SHIM_IN_CHECKOUT) -> list[str]`; `default_argv()` returns `[*default_program(), "--port", "20130", "--bind", "127.0.0.1"]`.

- [x] **Step 1: Write the failing tests**

```python
def test_default_program_prefers_the_built_checkout(tmp_path: Path) -> None:
    shim = tmp_path / "dist" / "shim.js"
    shim.parent.mkdir()
    shim.write_text("")
    assert gateway_module.default_program(
        which=lambda _n: "/usr/bin/pitwall-gateway", shim=shim
    ) == [
        "node",
        str(shim),
    ]


def test_default_program_uses_the_installed_launcher_when_unbuilt(tmp_path: Path) -> None:
    missing = tmp_path / "dist" / "shim.js"
    assert gateway_module.default_program(
        which=lambda _n: "/home/u/.local/bin/pitwall-gateway", shim=missing
    ) == ["/home/u/.local/bin/pitwall-gateway"]


def test_default_program_is_absolute_and_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program = gateway_module.default_program(which=lambda _n: None, shim=tmp_path / "shim.js")
    assert Path(program[-1]).is_absolute()
    monkeypatch.chdir(tmp_path)
    assert (
        gateway_module.default_program(which=lambda _n: None, shim=tmp_path / "shim.js") == program
    )
```

Delete `test_default_argv_is_absolute_and_independent_of_cwd` (replaced by the third case above, which asserts the same property without reading the machine). In `test_start_spawns_stock_sidecar_and_persists_owner_only_state`, construct the supervisor with an explicit `argv=["node", "/abs/shim.js", "--port", "20130", "--bind", "127.0.0.1"]` and assert against that list.

- [x] **Step 2: Run to verify they fail**

Run: `uv run --frozen pytest -q tests/personal/test_gateway_supervisor.py -k default_program`
Expected: FAIL with `AttributeError: module ... has no attribute 'default_program'`.

- [x] **Step 3: Implement**

```python
def default_program(
    *,
    which: Callable[[str], str | None] = shutil.which,
    shim: Path = _SHIM_IN_CHECKOUT,
) -> list[str]:
    """The gateway executable: the built checkout shim, else the installed launcher."""
    launcher = which("pitwall-gateway")
    if shim.is_file() or launcher is None:
        return ["node", str(shim)]
    return [launcher]


def default_argv() -> list[str]:
    return [*default_program(), "--port", "20130", "--bind", "127.0.0.1"]
```

Keep the docstring text from the old `default_argv` on `default_program`.

- [x] **Step 4: Verify on this machine, which has `~/.local/bin/pitwall-gateway`**

Run: `rm -rf packages/gateway/dist && uv run --frozen pytest -q tests/personal/test_gateway_supervisor.py`
Expected: all passed.

- [x] **Step 5: Commit**

```bash
git add src/pitwall/personal/gateway.py tests/personal/test_gateway_supervisor.py
git commit -s -m "fix(personal): resolve the gateway program through an injectable helper; tests no longer read PATH"
```

---

## Phase 2 — Product defects

### Task 5: Valid `min_cuda` values, enforced at catalogue load (R5)

Ten dossiers write `min_cuda: \"12.8"` (or `\"13.0"`), which YAML loads as the literal string `\"12.8"`; `Variant.min_cuda` (`src/pitwall/models/schema.py:78`) accepts it and `serve._allowed_cuda_versions` raises `ValueError: invalid literal for int()`. Affected: `MiniMaxAI--MiniMax-M2.7.md`, `MiniMaxAI--MiniMax-M3.md`, `deepseek-ai--DeepSeek-V4-Flash-Vision-Exp.md`, `meituan-longcat--LongCat-2.0.md`, `moonshotai--Kimi-K2.6.md`, `moonshotai--Kimi-K2.7-Code.md`, `tencent--Hy3.md`, `tencent--Hy4-preview.md`, `zai-org--GLM-5.1.md`, `zai-org--GLM-5.3.md`.

**Files:**
- Modify: `src/pitwall/models/schema.py` (`Variant`)
- Modify: the ten `docs/models/*.md` files above
- Test: `tests/models/test_schema.py` (create if absent), `tests/models/test_catalogue_integrity.py` (create)

- [x] **Step 1: Write the failing tests**

`tests/models/test_schema.py`:

```python
import pytest
from pydantic import ValidationError

from pitwall.models.schema import Variant

_BASE = {
    "id": "fp8",
    "engine": "vllm",
    "image": "vllm/vllm-openai:latest",
    "repo": "org/model",
    "format": "safetensors",
    "min_vram_gb": 24,
    "context": 8192,
    "container_disk_gb": 50,
    "startup_min": 5,
    "confidence": "high",
    "sources": ("https://example.test/card",),
}


@pytest.mark.parametrize("value", ['\\"12.8"', '"12.8"', "v12.8", "12.8.1", "12", "12.x", ""])
def test_min_cuda_rejects_malformed_versions(value: str) -> None:
    with pytest.raises(ValidationError, match="min_cuda"):
        Variant.model_validate({**_BASE, "min_cuda": value})


@pytest.mark.parametrize("value", ["12.8", "13.0", "12.10"])
def test_min_cuda_accepts_major_minor(value: str) -> None:
    assert Variant.model_validate({**_BASE, "min_cuda": value}).min_cuda == value
```

Adjust `_BASE` field values to satisfy the model's other validators (read `Variant` and its enums in `schema.py` for the exact accepted `engine`, `format`, and `confidence` literals).

`tests/models/test_catalogue_integrity.py`:

```python
from pitwall.models import load_catalogue
from pitwall.serve import _allowed_cuda_versions


def test_every_catalogue_variant_has_a_usable_cuda_floor() -> None:
    catalogue = load_catalogue()
    for model in catalogue.models():
        for variant in model.variants:
            if variant.min_cuda is not None:
                assert _allowed_cuda_versions(variant.min_cuda, ["12.8", "13.0"]) is not None or (
                    variant.min_cuda > "13.0"
                ), (model.id, variant.id)
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run --frozen pytest -q tests/models/test_schema.py tests/models/test_catalogue_integrity.py`
Expected: the malformed-value cases FAIL (no ValidationError), and the integrity test FAILS with `ValueError: invalid literal for int()`.

- [x] **Step 3: Validate the field**

In `Variant`, add:

```python
    @field_validator("min_cuda")
    @classmethod
    def cuda_major_minor(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"\d+\.\d+", value) is None:
            raise ValueError(f"min_cuda must be MAJOR.MINOR, got {value!r}")
        return value
```

Import `re` if absent.

- [x] **Step 4: Fix the ten dossiers**

Run: `sed -i -E 's/^(\s*min_cuda: )\\"([0-9]+\.[0-9]+)"$/\1"\2"/' docs/models/MiniMaxAI--MiniMax-M2.7.md docs/models/MiniMaxAI--MiniMax-M3.md docs/models/deepseek-ai--DeepSeek-V4-Flash-Vision-Exp.md docs/models/meituan-longcat--LongCat-2.0.md docs/models/moonshotai--Kimi-K2.6.md docs/models/moonshotai--Kimi-K2.7-Code.md docs/models/tencent--Hy3.md docs/models/tencent--Hy4-preview.md docs/models/zai-org--GLM-5.1.md docs/models/zai-org--GLM-5.3.md`
Then: `grep -c 'min_cuda: \\' docs/models/*.md | grep -v ':0'`
Expected: no output.

- [x] **Step 5: Verify**

Run: `uv run --frozen pytest -q tests/models tests/serve tests/personal`
Expected: all passed.

- [x] **Step 6: Commit**

```bash
git add src/pitwall/models/schema.py docs/models tests/models
git commit -s -m "fix(models): reject malformed min_cuda at load; repair ten dossiers that quoted it"
```

### Task 6: Allow every CUDA version at or above the floor on both serve paths (R6)

RunPod treats `allowedCudaVersions` as an exact set. `PersonalServeService._request` (`src/pitwall/personal/service.py:365`) sends `[min_cuda]`; `serve._provider_config` (`src/pitwall/serve.py:860-864`) sends `[min_cuda]` when `_live_cuda_versions` returns `None`. Both exclude newer-driver hosts, including the CUDA 13.0 hosts where the 2026-08-30 live runs succeeded.

**Files:**
- Modify: `src/pitwall/serve.py` (new `RUNPOD_CUDA_VERSIONS`, new `cuda_allow_list`, `_provider_config`)
- Modify: `src/pitwall/personal/service.py` (`PersonalServeService.__init__`, `serve`, `_request`)
- Test: `tests/serve/test_cuda_allow_list.py` (create), `tests/personal/test_service.py:304`

**Interfaces:**
- Produces: `RUNPOD_CUDA_VERSIONS: tuple[str, ...]`; `cuda_allow_list(min_cuda: str | None, offered: Sequence[str] | None) -> list[str] | None` — returns `None` when `min_cuda` is `None`; raises `ServeTemplateInvalid` when nothing offered satisfies the floor; uses `RUNPOD_CUDA_VERSIONS` when `offered` is `None`.
- `PersonalServeService(..., cuda_versions: Callable[[PitwallSettings, str], Awaitable[tuple[str, ...] | None]] | None = None)`.

- [x] **Step 1: Write the failing tests**

`tests/serve/test_cuda_allow_list.py`:

```python
import pytest

from pitwall.serve import RUNPOD_CUDA_VERSIONS, ServeTemplateInvalid, cuda_allow_list


def test_no_floor_leaves_placement_unconstrained() -> None:
    assert cuda_allow_list(None, ["12.4"]) is None


def test_live_offer_is_filtered_by_the_floor() -> None:
    assert cuda_allow_list("12.8", ["12.4", "12.8", "13.0", "13.2"]) == ["12.8", "13.0", "13.2"]


def test_unknown_offer_uses_every_known_version_at_or_above_the_floor() -> None:
    allowed = cuda_allow_list("12.8", None)
    assert allowed == [v for v in RUNPOD_CUDA_VERSIONS if tuple(map(int, v.split("."))) >= (12, 8)]
    assert "13.0" in allowed and "12.4" not in allowed


def test_nothing_offered_at_the_floor_refuses_before_launch() -> None:
    with pytest.raises(ServeTemplateInvalid, match="12.9"):
        cuda_allow_list("12.9", ["12.4", "12.8"])
```

In `tests/personal/test_service.py`, change the assertion at line 304 to `assert runpod.created[0]["workload"].allowed_cuda_versions == ["12.8", "13.0"]` and construct that test's service with `cuda_versions=_fake_cuda(("12.4", "12.8", "13.0"))`, where `_fake_cuda` is a module helper returning an async function that ignores its arguments and returns the tuple. Add a second test in the same file where `cuda_versions` returns `("12.4",)` for a `12.8` variant and assert `ServeRefused` with code `cuda_unavailable` and that `runpod.created == []`.

- [x] **Step 2: Run to verify they fail**

Run: `uv run --frozen pytest -q tests/serve/test_cuda_allow_list.py tests/personal/test_service.py`
Expected: FAIL with `ImportError: cannot import name 'RUNPOD_CUDA_VERSIONS'`.

- [x] **Step 3: Implement the shared helper in `serve.py`**

```python
RUNPOD_CUDA_VERSIONS: tuple[str, ...] = (
    "12.0",
    "12.1",
    "12.2",
    "12.3",
    "12.4",
    "12.5",
    "12.6",
    "12.7",
    "12.8",
    "12.9",
    "13.0",
    "13.1",
    "13.2",
)
"""Driver CUDA versions RunPod has offered (live gpuTypes, 2026-08-30 through 2026-09-21).
Used only when a live offer is unavailable, so a floor never collapses to one exact version."""


def cuda_allow_list(min_cuda: str | None, offered: Sequence[str] | None) -> list[str] | None:
    if min_cuda is None:
        return None
    candidates = RUNPOD_CUDA_VERSIONS if offered is None else tuple(offered)
    allowed = _allowed_cuda_versions(min_cuda, candidates)
    if not allowed:
        raise ServeTemplateInvalid(f"no CUDA version at or above {min_cuda} is offered")
    return allowed
```

Replace the `allowed_cuda_versions = (...)` expression and the following `== []` check in `_provider_config` with `allowed_cuda_versions = cuda_allow_list(info.min_cuda if info is not None else None, offered_cuda_versions)`.

- [x] **Step 4: Use it on the personal path**

In `PersonalServeService.__init__` add the `cuda_versions` parameter; default it to:

```python
async def _default_cuda_versions(
    settings: PitwallSettings, gpu_class: str
) -> tuple[str, ...] | None:
    from pitwall.serve import _live_cuda_versions

    return await _live_cuda_versions(None, settings, gpu_class)
```

In `serve()`, before calling `self._request(spec, plan)`, compute `offered = await self._cuda_versions(self._settings, spec.gpu_class)` when the variant has `min_cuda`, then `allowed = cuda_allow_list(min_cuda, offered)` inside `try/except ServeTemplateInvalid as exc: raise ServeRefused("cuda_unavailable", str(exc)) from exc`. This refusal must happen before `create_pod`. Pass `allowed` into `_request` and set `allowed_cuda_versions=allowed` in the `WorkloadConfig`.

- [x] **Step 5: Verify**

Run: `uv run --frozen pytest -q tests/serve tests/personal tests/models && uv run --frozen mypy src`
Expected: all passed; `Success: no issues found`.

- [x] **Step 6: Commit**

```bash
git add src/pitwall/serve.py src/pitwall/personal/service.py tests/serve/test_cuda_allow_list.py tests/personal/test_service.py
git commit -s -m "fix(serve): allow every CUDA version at or above the floor on both serve paths; refuse before launch when none is offered"
```

### Task 7: The in-pod deadline uses the v2 action endpoint and leaves a trace (R3)

`src/pitwall/personal/deadline.py` sends `DELETE /v2/pods/$RUNPOD_POD_ID` and, for the stop fallback, `POST /v2/pods/$RUNPOD_POD_ID/stop`. The captured contract (`tests/fixtures/runpod_v2_contract_2026-08-31.json`) routes lifecycle through `pods/{id}/action` with actions `start|stop|restart|terminate`, which is also what `runpod_client/pods.py::_terminate_pod_sync` uses. The timer's output goes to `/dev/null`, so a failed call is invisible in pod logs.

**Files:**
- Modify: `src/pitwall/personal/deadline.py`
- Test: `tests/personal/test_deadline.py`

- [x] **Step 1: Write the failing tests**

Replace the two endpoint assertions in `tests/personal/test_deadline.py` with:

```python
import json
from pathlib import Path

_CONTRACT = json.loads(Path("tests/fixtures/runpod_v2_contract_2026-08-31.json").read_text())


def test_terminate_uses_the_v2_action_endpoint() -> None:
    [script] = wrap_start_command(["/app/llama-server"], ["-m", "x"], ttl_seconds=60)
    assert (
        "https://api.runpod.io/v2/"
        + _CONTRACT["paths"]["podAction"].replace("{id}", "$RUNPOD_POD_ID")
        in script
    )
    assert '\'{"action":"terminate"}\'' in script
    assert "-X DELETE" not in script


def test_stop_fallback_uses_the_v2_stop_action() -> None:
    [script] = wrap_start_command(
        ["/app/llama-server"], ["-m", "x"], ttl_seconds=60, terminate=False
    )
    assert '\'{"action":"stop"}\'' in script
    assert "stop" in _CONTRACT["podActions"]


def test_timer_failures_reach_the_pod_log() -> None:
    [script] = wrap_start_command(["/app/llama-server"], ["-m", "x"], ttl_seconds=60)
    assert ">/dev/null" not in script
    assert "pitwall-deadline:" in script
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run --frozen pytest -q tests/personal/test_deadline.py`
Expected: the three new cases FAIL.

- [x] **Step 3: Implement**

```python
_ACTION_URL = '"https://api.runpod.io/v2/pods/$RUNPOD_POD_ID/action"'


def _lifecycle(action: str) -> str:
    return (
        'curl -fsS -X POST -H "Authorization: Bearer $RUNPOD_API_KEY" '
        '-H "Content-Type: application/json" '
        f'-d \'{{"action":"{action}"}}\' {_ACTION_URL}'
    )


_TERMINATE = _lifecycle("terminate")
_STOP = _lifecycle("stop")
```

In `wrap_start_command`, replace the timer line with:

```python
    script = (
        f"( sleep {ttl_seconds}; echo 'pitwall-deadline: ttl reached' >&2; "
        f"{action} >&2 || echo 'pitwall-deadline: lifecycle call failed' >&2 ) &\n"
        f'exec {exec_line} --api-key "$PITWALL_ENDPOINT_KEY"'
    )
```

stderr of the background subshell is the container's stderr, which `BoundedPodLogClient` reads. The response body contains no credential.

- [x] **Step 4: Verify**

Run: `uv run --frozen pytest -q tests/personal`
Expected: all passed.

- [x] **Step 5: Commit**

```bash
git add src/pitwall/personal/deadline.py tests/personal/test_deadline.py
git commit -s -m "fix(personal): the in-pod deadline calls the v2 pod action endpoint and logs its outcome"
```

Task 18 decides with a live pod whether the pod-scoped key may `terminate` or only `stop`.

### Task 8: The gateway routes each provider to its own upstream (R1)

All 41 enabled seeded providers point at `http://127.0.0.1:20130/v1`, but `packages/gateway/src/shim.ts::defaultExecutor` forwards every request to the single `PITWALL_GATEWAY_UPSTREAM_URL` (default `http://127.0.0.1:65535/v1`, `config.ts:120`). Model ids repeat across pools (57 duplicated ids in 338), so routing must key on the provider, not the model. The design: the sync writes a route table keyed by seed name; the broker adapter names the route in an `x-pitwall-route` header; the gateway resolves base URL, upstream model id, and key variable from the table. Keys stay in the environment, named by the table.

**Files:**
- Modify: `src/pitwall/gateway_catalog/sync.py` (`CatalogArtifacts`, `transform`, `write_artifacts`, new `route_key_env`)
- Create: `config/gateway-routes.json` (generated)
- Modify: `pyproject.toml` (wheel force-include and sdist include for `config/gateway-routes.json`)
- Modify: `src/pitwall/providers/gateway.py` (`_infer_headers`, `GatewayProvider.infer`)
- Modify: `src/pitwall/personal/gateway.py` (`GatewaySupervisor.start` sets `PITWALL_GATEWAY_ROUTES`)
- Modify: `packages/gateway/src/config.ts` (`ShimConfig.routes`, `readRoutes`, fail-closed boot)
- Modify: `packages/gateway/src/shim.ts` (`handleChatCompletions`, `handleEmbeddings`, `handleModels`)
- Modify: `.env.example`, `docs/sdlc/24-gateway.md`, `packages/gateway/README.md`
- Test: `tests/test_gateway_catalog.py`, `tests/providers/test_gateway_provider.py`, `tests/personal/test_gateway_supervisor.py`, `packages/gateway/tests/routes.test.ts` (create)

**Interfaces:**
- Route table schema (`config/gateway-routes.json`): `{"schema_version": 1, "routes": {"<seed name>": {"base_url": str, "model_id": str, "api_key_env": str | null, "key_required": bool}}}`. Only routable rows whose `upstream_format == "openai"` are emitted; the sync also forces `enabled: false` on any seed row that routes through the fork without a route.
- `route_key_env(row: CatalogRow) -> str | None`: `None` when `auth_type == "none"`; otherwise `"PITWALL_GATEWAY_KEY_" + re.sub(r"[^A-Z0-9]", "_", (row.pool_key or row.provider).upper())`.
- Header: `x-pitwall-route: <provider_record.name>` on every loopback-fork request.
- Gateway env: `PITWALL_GATEWAY_ROUTES=<absolute path>`. Boot fails with `ConfigError` when neither `PITWALL_GATEWAY_ROUTES` nor `PITWALL_GATEWAY_UPSTREAM_URL` is set; the `127.0.0.1:65535` default is removed.
- Gateway errors: missing header with routes loaded → 400 `{type:"invalid_request_error", code:"route_required"}`; unknown route → 404 `code:"route_not_found"`; `key_required` and variable unset → 503 `code:"upstream_key_missing"` (no upstream call).

- [x] **Step 1: Write the failing gateway tests** (`packages/gateway/tests/routes.test.ts`)

```ts
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, test } from "vitest";
import { startShim } from "../src/shim.js";

const dir = mkdtempSync(join(tmpdir(), "routes-"));
const routesPath = join(dir, "routes.json");
writeFileSync(routesPath, JSON.stringify({
  schema_version: 1,
  routes: {
    "gw-a": { base_url: "http://upstream-a.test/v1", model_id: "m-a", api_key_env: null, key_required: false },
    "gw-b": { base_url: "http://upstream-b.test/v1", model_id: "m-b", api_key_env: "PITWALL_GATEWAY_KEY_B", key_required: true },
  },
}));

const calls: { url: string; auth: string | null; model: unknown }[] = [];
const upstreamFetch: typeof fetch = async (input, init) => {
  const body = JSON.parse(String(init?.body ?? "{}"));
  calls.push({ url: String(input), auth: new Headers(init?.headers).get("authorization"), model: body.model });
  return new Response(JSON.stringify({ id: "x", choices: [] }), { status: 200, headers: { "content-type": "application/json" } });
};

async function boot(env: Record<string, string> = {}) {
  return startShim({
    env: { PITWALL_GATEWAY_TOKEN: "t", PITWALL_GATEWAY_ROUTES: routesPath, ...env },
    argv: ["--port", "0"],
    upstreamFetch,
  });
}

afterEach(() => { calls.length = 0; });

async function chat(port: number, route?: string) {
  const headers: Record<string, string> = { Authorization: "Bearer t", "Content-Type": "application/json" };
  if (route) headers["x-pitwall-route"] = route;
  return fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
    method: "POST", headers, body: JSON.stringify({ model: "ignored", messages: [{ role: "user", content: "hi" }] }),
  });
}

test("each route reaches its own upstream with its own model id", async () => {
  const s = await boot({ PITWALL_GATEWAY_KEY_B: "k-b" });
  try {
    expect((await chat(s.port, "gw-a")).status).toBe(200);
    expect((await chat(s.port, "gw-b")).status).toBe(200);
    expect(calls).toEqual([
      { url: "http://upstream-a.test/v1/chat/completions", auth: null, model: "m-a" },
      { url: "http://upstream-b.test/v1/chat/completions", auth: "Bearer k-b", model: "m-b" },
    ]);
  } finally { await s.close(); }
});

test("a missing route header is refused without an upstream call", async () => {
  const s = await boot();
  try {
    const r = await chat(s.port);
    expect(r.status).toBe(400);
    expect((await r.json()).error.code).toBe("route_required");
    expect(calls).toHaveLength(0);
  } finally { await s.close(); }
});

test("an unknown route is 404", async () => {
  const s = await boot();
  try {
    const r = await chat(s.port, "gw-nope");
    expect(r.status).toBe(404);
    expect((await r.json()).error.code).toBe("route_not_found");
  } finally { await s.close(); }
});

test("a keyed route without its key is 503 and never contacts the upstream", async () => {
  const s = await boot();
  try {
    const r = await chat(s.port, "gw-b");
    expect(r.status).toBe(503);
    expect((await r.json()).error.code).toBe("upstream_key_missing");
    expect(calls).toHaveLength(0);
  } finally { await s.close(); }
});

test("boot fails closed with neither a route table nor an upstream URL", async () => {
  await expect(startShim({ env: { PITWALL_GATEWAY_TOKEN: "t" }, argv: ["--port", "0"] })).rejects.toThrow(
    /PITWALL_GATEWAY_ROUTES or PITWALL_GATEWAY_UPSTREAM_URL/,
  );
});
```

Match `startShim`'s actual option and return names in `packages/gateway/src/shim.ts` (`startShim(options)` returns an object with the bound port and `close()`); rename `s.port`/`s.close` here if they differ.

- [x] **Step 2: Run to verify they fail**

Run: `cd packages/gateway && npx vitest run tests/routes.test.ts`
Expected: FAIL (requests reach the single upstream; boot succeeds with the 65535 default).

- [x] **Step 3: Implement the gateway side**

In `config.ts`, add `readonly routes: ReadonlyMap<string, GatewayRoute> | undefined;` to `ShimConfig`, export `interface GatewayRoute { baseUrl: string; modelId: string; apiKeyEnv: string | null; keyRequired: boolean; }`, and add:

```ts
function readRoutes(env: NodeJS.ProcessEnv): ReadonlyMap<string, GatewayRoute> | undefined {
  const path = env.PITWALL_GATEWAY_ROUTES?.trim();
  if (!path) return undefined;
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(path, "utf8"));
  } catch (err) {
    throw new ConfigError(`PITWALL_GATEWAY_ROUTES could not be read as JSON: ${path}`);
  }
  const routes = (raw as { schema_version?: unknown; routes?: unknown }) ?? {};
  if (routes.schema_version !== 1 || typeof routes.routes !== "object" || routes.routes === null) {
    throw new ConfigError("PITWALL_GATEWAY_ROUTES must be schema_version 1 with a routes object");
  }
  const out = new Map<string, GatewayRoute>();
  for (const [name, value] of Object.entries(routes.routes as Record<string, unknown>)) {
    const r = value as Record<string, unknown>;
    if (typeof r.base_url !== "string" || typeof r.model_id !== "string") {
      throw new ConfigError(`route ${name} needs base_url and model_id strings`);
    }
    new URL(r.base_url);
    out.set(name, {
      baseUrl: r.base_url,
      modelId: r.model_id,
      apiKeyEnv: typeof r.api_key_env === "string" ? r.api_key_env : null,
      keyRequired: r.key_required === true,
    });
  }
  return out;
}
```

Change `readUpstreamBaseUrl` to return `string | undefined` (no default), and in `resolveConfig` throw `new ConfigError("set PITWALL_GATEWAY_ROUTES or PITWALL_GATEWAY_UPSTREAM_URL")` when both are absent. Make `upstreamBaseUrl` `string | undefined` in `ShimConfig`.

In `shim.ts::handleChatCompletions`, after the body is validated and translated and before the executor is chosen:

```ts
  let upstreamBaseUrl = config.upstreamBaseUrl;
  let upstreamApiKey = config.upstreamApiKey;
  if (config.routes) {
    const routeName = headerValue(req, "x-pitwall-route");
    if (!routeName) {
      await writeResponse(res, errorJsonResponse(400, "x-pitwall-route is required.", parsed.requestId,
        { type: "invalid_request_error", code: "route_required" }));
      return;
    }
    const route = config.routes.get(routeName);
    if (!route) {
      await writeResponse(res, errorJsonResponse(404, `Unknown route ${routeName}.`, parsed.requestId,
        { type: "invalid_request_error", code: "route_not_found" }));
      return;
    }
    const key = route.apiKeyEnv ? process.env[route.apiKeyEnv]?.trim() : undefined;
    if (route.keyRequired && !key) {
      await writeResponse(res, errorJsonResponse(503, `Route ${routeName} has no upstream key configured.`,
        parsed.requestId, { type: "api_error", code: "upstream_key_missing" }));
      return;
    }
    upstreamBaseUrl = route.baseUrl;
    upstreamApiKey = key;
    (outbound as Record<string, unknown>).model = route.modelId;
  }
```

Pass `upstreamBaseUrl` and `upstreamApiKey` (not `config.*`) into the executor context. `headerValue` returns the first string value of a request header or `undefined`; add it next to `readBearerToken` if no equivalent exists. Apply the same resolution in `handleEmbeddings`. In `handleModels`, when `config.routes` is set, list `Array.from(config.routes.keys())` merged with registered executor ids and skip the single-upstream catalog call.

- [x] **Step 4: Verify the gateway side**

Run: `cd packages/gateway && npm run -s typecheck && npm run -s lint && npx vitest run`
Expected: typecheck and lint silent; all test files passed. Existing tests that relied on the 65535 default now pass `PITWALL_GATEWAY_UPSTREAM_URL` explicitly through their `startTestShim` helper.

- [x] **Step 5: Write the failing Python tests**

In `tests/test_gateway_catalog.py`:

```python
def test_route_table_covers_every_enabled_fork_row() -> None:
    routes = json.loads((REPO_ROOT / "config" / "gateway-routes.json").read_text())["routes"]
    seeds = yaml.safe_load((REPO_ROOT / "seed" / "gateway-providers.yaml").read_text())["providers"]
    fork = [r for r in seeds if r["enabled"] and r["gateway"]["base_url"] == FORK_BASE_URL]
    assert fork, "expected enabled fork rows"
    for row in fork:
        route = routes[row["name"]]
        assert route["model_id"] == row["gateway"]["model_id"]
        assert route["base_url"].startswith("https://") or route["base_url"].startswith("http://")
        assert route["base_url"] != FORK_BASE_URL


def test_route_key_env_is_derived_from_the_pool() -> None:
    row = _row(pool_key="groq-free", auth_type="apikey")
    assert route_key_env(row) == "PITWALL_GATEWAY_KEY_GROQ_FREE"
    assert route_key_env(_row(pool_key=None, provider="pollinations", auth_type="none")) is None
```

(`_row` builds a `CatalogRow` with defaults; add it beside the existing fixtures in that file.)

In `tests/providers/test_gateway_provider.py`, assert that an `infer` call against `http://127.0.0.1:20130/v1` sends `x-pitwall-route` equal to the provider record's `name`, and that a direct (non-loopback) base URL sends no such header.

In `tests/personal/test_gateway_supervisor.py`, assert that `start()` passes `PITWALL_GATEWAY_ROUTES` pointing at an existing absolute file in the child environment.

- [x] **Step 6: Implement the Python side**

`sync.py`: add `routes_json: dict[str, Any]` to `CatalogArtifacts`; in `transform`, build it from `rows` where `row.routable and row.upstream_format == "openai" and _routed_base_url(row, direct_keyless=direct_keyless) == FORK_BASE_URL`, with `key_required = row.auth_type == "apikey"`. For routable fork rows with another `upstream_format`, set that provider's seed `enabled` to `False`. `write_artifacts` writes `config/gateway-routes.json` with `json.dumps(..., indent=2, sort_keys=True) + "\n"`.

`providers/gateway.py::GatewayProvider.infer`: pass `route=request.provider_record.name` into `_infer_headers`, which adds `headers["x-pitwall-route"] = route` only inside the `is_loopback_base_url(creds.base_url)` branch.

`personal/gateway.py::GatewaySupervisor.start`: set `child_env.setdefault("PITWALL_GATEWAY_ROUTES", str(_routes_path()))`, where `_routes_path()` returns the checkout `config/gateway-routes.json` when present, else `Path(__file__).resolve().parents[1] / "gateway_catalog" / "data" / "config" / "gateway-routes.json"`.

`pyproject.toml`: add `"config/gateway-routes.json" = "pitwall/gateway_catalog/data/config/gateway-routes.json"` to wheel force-include and `"/config/gateway-routes.json"` to the sdist include list.

Regenerate artifacts: `uv run --frozen pitwall gateway sync` (the extractor's pinned upstream version and tarball hash are recorded in `config/gateway-catalog.lock.json`; the sync must reproduce them byte-for-byte apart from the new routes file). Then add `PITWALL_GATEWAY_ROUTES` and one example `PITWALL_GATEWAY_KEY_<POOL>` line to `.env.example`, and describe the route table, header, and three error codes in `docs/sdlc/24-gateway.md` and `packages/gateway/README.md`.

- [x] **Step 7: Verify**

Run: `uv run --frozen pytest -q tests/test_gateway_catalog.py tests/test_gateway_seed.py tests/providers/test_gateway_provider.py tests/personal tests/cli/test_gateway_cli.py && git diff --stat config/gateway-catalog.lock.json`
Expected: all passed; the lock file is unchanged.

- [x] **Step 8: Commit**

```bash
git add src/pitwall/gateway_catalog/sync.py config/gateway-routes.json seed/gateway-providers.yaml pyproject.toml src/pitwall/providers/gateway.py src/pitwall/personal/gateway.py packages/gateway .env.example docs/sdlc/24-gateway.md tests
git commit -s -m "feat(gateway): per-provider upstream routing through a generated route table"
```

---

## Phase 3 — Scheduled CI green

### Task 9: Rebuild images on a patched base (R8)

The 2026-09-21 scheduled run's `container-build` jobs failed Trivy with 16 HIGH/CRITICAL findings in the pinned `python:3.14.7-slim@sha256:caaf356f…` (gzip, libpcre2-8-0, libsqlite3-0 and others, fixed versions available).

**Files:**
- Modify: `docker/Dockerfile.api`, `docker/Dockerfile.cost-exporter`, `docker/Dockerfile.mcp`, `docker/Dockerfile.reconciler`, `docker/Dockerfile.webhook` (`ARG PYTHON_IMAGE`)

- [x] **Step 1: Resolve the current digest**

Run: `docker pull python:3.14.7-slim >/dev/null && docker inspect --format '{{index .RepoDigests 0}}' python:3.14.7-slim`
Expected: `python@sha256:<digest>`.

- [x] **Step 2: Pin it in all five Dockerfiles**

Replace the digest after `python:3.14.7-slim@` on the `ARG PYTHON_IMAGE=` line of each file.

- [x] **Step 3: Verify with the CI scanner settings**

Run: `for s in api cost-exporter mcp reconciler webhook; do docker build -q -f docker/Dockerfile.$s -t pitwall/$s:scan . && docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.70.0 image --quiet --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 pitwall/$s:scan && echo "$s clean"; done`
Expected: `api clean` … `webhook clean`. If a finding remains that has a fixed Debian package, add `RUN apt-get update && apt-get install -y --only-upgrade <package> && rm -rf /var/lib/apt/lists/*` to the runtime stage of every Dockerfile and rerun.

- [x] **Step 4: Commit**

```bash
git add docker/Dockerfile.*
git commit -s -m "fix(images): pin the patched python:3.14.7-slim digest"
```

### Task 10: Mutation smoke installs its interpreter (R8)

`.github/workflows/ci.yml` job `mutation-smoke` runs `astral-sh/setup-uv` then `uv sync --frozen --extra dev` with no Python step; the runner reported `No interpreter found for Python 3.14.7`.

**Files:**
- Modify: `.github/workflows/ci.yml` (`mutation-smoke` steps)
- Test: `tests/release/test_release_policy.py`

- [x] **Step 1: Write the failing policy test**

```python
def test_every_job_that_syncs_the_root_project_provisions_python_3_14_7() -> None:
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    for name, job in ci["jobs"].items():
        runs = [s.get("run", "") for s in job.get("steps", [])]
        if any("uv sync" in r for r in runs):
            provisions = [
                s
                for s in job["steps"]
                if "setup-python" in str(s.get("uses", ""))
                or "uv python install 3.14.7" in s.get("run", "")
            ]
            assert provisions, f"{name} syncs without provisioning Python 3.14.7"
```

- [x] **Step 2: Run to verify it fails**

Run: `uv run --frozen pytest -q tests/release/test_release_policy.py -k provisions`
Expected: FAIL naming `mutation-smoke`.

- [x] **Step 3: Add the step**

After `setup-uv` in `mutation-smoke`, add `- run: uv python install 3.14.7`.

- [x] **Step 4: Verify and commit**

Run: `uv run --frozen pytest -q tests/release/test_release_policy.py && make ci-tools`
Expected: passed; ci-tools exit 0.

```bash
git add .github/workflows/ci.yml tests/release/test_release_policy.py
git commit -s -m "ci: mutation smoke provisions Python 3.14.7 before syncing"
```

### Task 11: The fuzz lane survives the highest dependency resolution (R8)

With `uv lock --upgrade --resolution highest`, `tests/security/test_schemathesis_fuzz.py` fails to import: `ModuleNotFoundError: No module named 'starlette_testclient'` (the local hermetic run already warns `Using httpx with starlette.testclient is deprecated`).

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies].dev`), `uv.lock`

- [x] **Step 1: Reproduce in a scratch copy**

Run: `rm -rf /tmp/pw-high && git worktree add -q --detach /tmp/pw-high HEAD && cd /tmp/pw-high && uv lock --upgrade --resolution highest -q && uv sync --frozen --extra dev -q && uv run pytest -q tests/security/test_schemathesis_fuzz.py -x 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'starlette_testclient'`.

- [x] **Step 2: Add the distribution that provides it**

The module is published as the `starlette-testclient` distribution. In the scratch copy run `uv add --optional dev starlette-testclient` and rerun the command from step 1.
Expected: the fuzz module collects and passes.

- [x] **Step 3: Apply to the real tree with the frozen lock**

In `~/git/pitwall`: `uv add --optional dev "starlette-testclient>=<version resolved in step 2>"`, then `uv run --frozen pytest -q tests/security/test_schemathesis_fuzz.py`.
Expected: passed with the default lock; `git worktree remove --force /tmp/pw-high`.

- [x] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -s -m "chore(deps): declare starlette-testclient so the fuzz lane resolves at the highest versions"
```

### Task 12: The external link check treats this repository's own URLs as local (R8)

`tools/ci/check_markdown_links.py --external` requests `https://github.com/Buckeyes22/pitwall/...` and gets 404 while the repository is private, failing `docs` on every scheduled run.

**Files:**
- Modify: `tools/ci/check_markdown_links.py` (`_external_urls`, new `_own_repo_path`)
- Test: `tests/test_markdown_links.py`

- [x] **Step 1: Write the failing tests**

```python
from tools.ci.check_markdown_links import _own_repo_path


def test_blob_and_tree_urls_map_to_checkout_paths() -> None:
    assert (
        _own_repo_path("https://github.com/Buckeyes22/pitwall/blob/main/README.md") == "README.md"
    )
    assert (
        _own_repo_path("https://github.com/Buckeyes22/pitwall/tree/main/docs/sdlc") == "docs/sdlc"
    )


def test_other_own_repo_pages_are_not_fetched() -> None:
    for url in (
        "https://github.com/Buckeyes22/pitwall/actions/workflows/ci.yml/badge.svg",
        "https://github.com/Buckeyes22/pitwall/issues/new/choose",
        "https://github.com/Buckeyes22/pitwall/pull/30",
        "https://github.com/Buckeyes22/pitwall/discussions",
    ):
        assert _own_repo_path(url) == ""


def test_foreign_urls_are_not_own_repo() -> None:
    assert _own_repo_path("https://github.com/astral-sh/uv") is None
```

- [x] **Step 2: Implement**

```python
_OWN_REPO = "https://github.com/Buckeyes22/pitwall"


def _own_repo_path(url: str) -> str | None:
    """Map this repository's GitHub URLs to checkout paths.

    ``None`` means a foreign URL. ``""`` means an own-repo page with no file
    (issues, pulls, badges); those are not fetched because they depend on
    repository visibility, not on the documentation.
    """
    if not url.startswith(_OWN_REPO + "/") and url != _OWN_REPO:
        return None
    match = re.match(re.escape(_OWN_REPO) + r"/(?:blob|tree)/main/(.+?)(?:#.*)?$", url)
    return match.group(1) if match else ""
```

In `_external_urls`, drop URLs where `_own_repo_path(url) is not None`; in `check_internal`, for each own-repo URL with a non-empty path, report a failure when `(root / path)` does not exist.

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest -q tests/test_markdown_links.py && uv run --frozen python tools/ci/check_markdown_links.py --external | tail -2`
Expected: passed; the external run reports no `github.com/Buckeyes22/pitwall` failures.

```bash
git add tools/ci/check_markdown_links.py tests/test_markdown_links.py
git commit -s -m "ci(docs): own-repository links are checked against the checkout, not fetched"
```

---

## Phase 4 — Documentation that is true

### Task 13: Counts and screen names match the code (R11)

**Files:**
- Modify: `CHANGELOG.md` (the "52 REST operations, 49 MCP tools" line), `docs/api/openapi-baseline.json`, `docs/sdlc/17-testing-strategy.md:88-89`, `docs/sdlc/00-overview.md` ("16-point readiness audit"), `docs/operator/user-journey-catalog.md` (J09 "all six screens"), `scripts/release/run-user-journeys.sh` (J09 oracle), `tests/mcp/test_registry_health.py:43` (comment "(78)")
- Create: `tests/test_doc_counts.py`

- [x] **Step 1: Write the failing test**

```python
import json
import re
from pathlib import Path

from pitwall.audit import checks
from pitwall.mcp.registry import TOOL_REGISTRY

ROOT = Path(__file__).resolve().parents[1]


def _openapi_operations() -> int:
    spec = json.loads((ROOT / "docs/api/openapi-baseline.json").read_text())
    return sum(
        1
        for p in spec["paths"].values()
        for m in p
        if m in {"get", "post", "put", "patch", "delete"}
    )


def test_testing_strategy_states_the_baseline_operation_count() -> None:
    text = (ROOT / "docs/sdlc/17-testing-strategy.md").read_text()
    assert (
        f"all {_openapi_operations()}\ncurrent operations" in text
        or f"all {_openapi_operations()} current operations" in text
    )


def test_overview_states_the_audit_check_count() -> None:
    count = len([name for name in dir(checks) if re.match(r"check_\d{2}_", name)])
    assert f"{count}-check" in (ROOT / "docs/sdlc/00-overview.md").read_text()


def test_changelog_does_not_state_stale_surface_counts() -> None:
    text = (ROOT / "CHANGELOG.md").read_text()
    for match in re.finditer(r"(\d+) MCP tools", text):
        assert int(match.group(1)) == len(TOOL_REGISTRY)
```

The audit checks are the `check_NN_*` functions in `src/pitwall/audit/checks.py` (19 today); the test counts them by name.

- [x] **Step 2: Run to verify it fails, then fix the docs**

Run: `uv run --frozen pytest -q tests/test_doc_counts.py` (FAIL). Then:
- Regenerate the baseline: `make openapi-check` prints the export command; run the export so `docs/api/openapi-baseline.json` contains every live operation (the compatibility gate only rejects removals).
- Set the count in `17-testing-strategy.md` to the regenerated number.
- Replace "16-point readiness audit" with "19-check readiness audit" in `00-overview.md`.
- Replace the CHANGELOG line with the counts from `TOOL_REGISTRY` and the regenerated baseline.
- Change J09's expected text to "all ten views registered" in the catalogue and make the harness's J09 assertion check the ten `BINDINGS` view names in `src/pitwall/tui/app.py`.
- Change the `(78)` comment to reference `len(TOOL_REGISTRY)`.

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest -q tests/test_doc_counts.py tests/mcp && make openapi-check && make docs-check`
Expected: passed; both make targets exit 0.

```bash
git add CHANGELOG.md docs scripts/release/run-user-journeys.sh tests/test_doc_counts.py tests/mcp/test_registry_health.py
git commit -s -m "docs: surface counts, audit count, and console view names follow the code"
```

### Task 14: Plans and the live-findings register record what happened (R11)

**Files:**
- Modify: `docs/superpowers/plans/2026-09-02-pitwall-personal-first.md`, `docs/superpowers/plans/2026-09-02-retained-backlog-review-fixes.md`, `docs/superpowers/plans/2026-09-11-qa-onboarding.md` (status line under the title)
- Modify: `docs/evidence/2026-08-30-runpod-live-findings.md` (new final section "Disposition")

- [x] **Step 1: Status lines**

Under each plan's title add one line: `**Status:** complete; merged as <commit or PR>. Checkboxes were not maintained during execution; the commit history and tests are the record.` Use `f79a6de` (personal-first), `e6fbffb` (backlog fixes), and `4cb8be0` / PR #44 (QA onboarding). For QA onboarding, first confirm every file in its File Map exists: `grep -oE 'qa/[A-Za-z0-9/_.-]+\.md' docs/superpowers/plans/2026-09-11-qa-onboarding.md | sort -u | while read f; do [ -f "$f" ] || echo "missing $f"; done` must print nothing.

- [x] **Step 2: Disposition table for defects 1–22**

Append a table with one row per defect: number, fix commit (find with `git log --oneline -S '<distinctive identifier from the defect>'`), and the regression test path. Use these known anchors: 1–3 `create_template_rest`/`delete_template` in `runpod_client/templates.py`; 4 `GPU_VRAM_GB` 4080 SUPER; 5 `fit_options` `max_count`; 6 and the 500-class note `classify_runpod_failure`; 11 migration `0027`; 12 `tests/integration/test_provisioning_budget_lifecycle.py::test_arm_provider_rolls_back_when_audit_insert_is_rejected`; 13 `tests/_migration_ledger.py::restore_migration_ledger(applied_versions=...)`; 15 `cuda_allow_list` (Task 6); 16 `datacenter` → `data_center_id` in `serve.py`; 17 `tests/integration/test_lease_acceptance.py`; 18 `api/lifespan.py` `app.state.redis`; 19 `leases stop|renew` subparsers in `cli.py`; 20 v2 nested serverless shape; 21 `mounts.py` delete `already_gone`; 22 `mounts.py` shrink refusal. For 7 (crash-loop fast fail), 9 (rate from snapshot), 10 (v2 migration, ADR 0006), and 14 (billing lag), cite the commit and test, or state the implemented behavior in `pods.py`/`serve.py` with its test.

- [x] **Step 3: Verify and commit**

Run: `make docs-check`
Expected: exit 0.

```bash
git add docs/superpowers/plans docs/evidence/2026-08-30-runpod-live-findings.md
git commit -s -m "docs: plan status lines and a disposition for every 2026-08-30 live defect"
```

### Task 15: No absolute local paths in tracked files (R12)

151 references to `/home/<user>` in 30 tracked files (release-acceptance records, Pi Workbench docs and tests, plans, research, evidence).

**Files:**
- Modify: every file listed by `git grep -lE '/home/[a-z]+/'`
- Modify: `tools/guards/repo_text_policy.py` (new rule)
- Test: `tests/test_repo_text_policy_guard.py`

- [x] **Step 1: Write the failing guard test**

```python
def test_absolute_home_paths_are_rejected(tmp_path: Path) -> None:
    sample = tmp_path / "doc.md"
    sample.write_text("evidence at " + "/home/" + "alice/.local/state/x.json\n")
    assert run_policy([sample]) != 0


def test_placeholder_roots_are_accepted(tmp_path: Path) -> None:
    sample = tmp_path / "doc.md"
    sample.write_text("evidence at $PITWALL_EVIDENCE_ROOT/x.json and ~/.local/state/x.json\n")
    assert run_policy([sample]) == 0
```

Use the module's existing test entry point in place of `run_policy` (see the current tests in `tests/test_repo_text_policy_guard.py`).

- [x] **Step 2: Add the rule**

In `tools/guards/repo_text_policy.py`, reject any line matching `r"/home/[a-z_][a-z0-9_-]*/"` with the message `absolute home path; use $PITWALL_EVIDENCE_ROOT, $HOME, or a repo-relative path`.

- [x] **Step 3: Rewrite the references**

Evidence under `~/.local/state/pitwall-readiness/` becomes `$PITWALL_EVIDENCE_ROOT/...`; checkout paths `<x>` become repo-relative `<x>`; other worktree paths become their branch names. Define `PITWALL_EVIDENCE_ROOT` once in `docs/release/external-release-gates.md` ("operator-local evidence directory, default `~/.local/state/pitwall-readiness`"). For JSON records under `release_acceptance/`, rewrite the same way and rerun `uv run --frozen pytest -q tests/release_acceptance` so any hash pins that cover those files are refreshed through their own tooling, not by hand.

- [x] **Step 4: Verify and commit**

Run: `git grep -cE '/home/[a-z]+/' | wc -l && git ls-files -z | xargs -0 -r -n 200 uv run --frozen python tools/guards/repo_text_policy.py && uv run --frozen pytest -q tests/test_repo_text_policy_guard.py tests/release_acceptance`
Expected: `0`; policy exit 0; passed.

```bash
git add -A -- ':!*.lock'
git commit -s -m "docs: replace absolute home paths with evidence-root and repo-relative references; guard against new ones"
```

### Task 16: Planning documents live in the repository; stray resource forks are gone (repository hygiene)

Untracked at the repo root: `pi_harness_architecture_plan_v2.md`, `pi_harness_codex_handoff.md`, `pitwall_pi_integration_review_and_plan(1).md`, `release_acceptance_matrix_and_test_plan.md`, and three macOS AppleDouble files `._pi_harness_architecture_plan_v2.md`, `._pi_harness_codex_handoff.md`, `._pitwall_pi_integration_review_and_plan(1).md`.

- [x] **Step 1: Move the four documents**

```bash
mv pi_harness_architecture_plan_v2.md docs/research/2026-09-19-pi-workbench-architecture.md
mv pi_harness_codex_handoff.md docs/research/2026-09-19-pi-workbench-codex-handoff.md
mv 'pitwall_pi_integration_review_and_plan(1).md' docs/research/2026-09-19-pi-workbench-integration-review.md
mv release_acceptance_matrix_and_test_plan.md docs/superpowers/specs/2026-09-20-release-acceptance-matrix.md
```

Update every reference to the old names (`git grep -n 'release_acceptance_matrix_and_test_plan\|pi_harness_\|pitwall_pi_integration_review'`), including the spec reference in the journey-coverage plan.

- [x] **Step 2: Delete the AppleDouble files (only after the maintainer says yes in the conversation; they are macOS resource-fork copies, not documents)**

`rm ./._pi_harness_architecture_plan_v2.md ./._pi_harness_codex_handoff.md './._pitwall_pi_integration_review_and_plan(1).md'` and add `._*` to `.gitignore`.

- [x] **Step 3: Verify and commit**

Run: `make docs-check && git status --short | grep '^??' ; uv run --frozen ruff format --check . | tail -1`
Expected: docs-check exit 0; no untracked files; ruff reports all files formatted.

```bash
git add docs .gitignore
git commit -s -m "docs: bring the Pi Workbench and release-acceptance planning documents into the repository"
```

### Task 17: Changelog for the integrated work (R11)

- [x] **Step 1: Add `[Unreleased]` entries**

Under Added: the event-driven orchestrator channel and Claude launch guard with Qwen Code registration (`feat/qwen-code-channel`); `packages/pi-workbench`; `pitwall doctor`, `pitwall mcp install|uninstall`, the `pitwall_doctor` tool (79 MCP tools); release-acceptance discovery and matrix tooling under `tools/release_acceptance`; gateway upstream authentication, SSE relay, client-abort cancellation, `/v1/models` upstream catalog, and per-provider route table (Task 8). Under Fixed: every Phase 1–3 fix above. Under Security: the patched base image (Task 9).

- [x] **Step 2: Verify and commit**

Run: `uv run --frozen pytest -q tests/test_doc_counts.py && make docs-check`
Expected: passed; exit 0.

```bash
git add CHANGELOG.md packages/agent-routing/CHANGELOG.md packages/gateway/CHANGELOG.md
git commit -s -m "docs(changelog): record the integrated branches and the remediation fixes"
```

**Phase 4 exit:** run the full gate set and record the output in `docs/evidence/2026-09-23-remediation-gates.md`:

```bash
uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy src
uv run --frozen pytest -q -m "not integration and not slow" | tail -1
make up && DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/0 uv run --frozen pytest -q -m integration | tail -1
DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test REDIS_URL=redis://127.0.0.1:6380/0 bash scripts/release/run-user-journeys.sh | tail -1
(cd packages/agent-routing && HOME=$(mktemp -d) .venv/bin/python -m unittest discover -s tests 2>&1 | tail -1)
(cd packages/gateway && npm ci --silent && npm run -s typecheck && npm run -s lint && npx vitest run | tail -3)
(cd packages/pi-workbench && npm ci --silent && npx tsc --noEmit && npx vitest run | tail -3)
make sec && make docs-check && make openapi-check && make ci-tools
```

Expected: every line green; journeys `27 passed, 0 failed`.

Then execute the journey-coverage plan in full before Phase 5.

---

## Phase 5 — Live evidence (explicit spend caps; the maintainer runs or authorizes each task at execution time)

Each task writes a sanitized evidence file under `docs/evidence/` naming the command, the date, identifiers with credentials removed, the observed result, the spend, and the cleanup verification. A task whose credential or account does not exist changes the published claim instead (the task says how).

### Task 18: Personal serve round trip and in-pod termination (R3, R4)

- [x] **Step 1: Run the live test** (cap: one RTX 3090 community pod, 15-minute TTL, `max_usd_per_hour` 0.60 → at most $0.20)

```bash
source ~/.config/pitwall/live-run.env
PITWALL_RUN_LIVE=1 uv run --frozen pytest -m live tests/live/test_personal_serve_live.py -s 2>&1 | tee /tmp/personal-live.log | tail -20
```

Expected: `/v1/models` 200 with the served id, 401/403 without the key, route probe ok, and the final assertion `in-pod timer did not terminate the pod` not raised.

- [x] **Step 2: If the pod was stopped rather than terminated**

Set `terminate=False` at the `wrap_start_command` call in `PersonalServeService._request`, rerun step 1, and confirm the stopped pod bills only while running (no volume is attached).

- [x] **Step 3: Verify cleanup independently**

Run: `uv run --frozen pitwall runpod pods list --json | .venv/bin/python -c "import json,sys; print([p['id'] for p in json.load(sys.stdin) if p['name'].startswith('pitwall-live-')])"`
Expected: `[]`.

- [x] **Step 4: Record and align wording**

Write `docs/evidence/2026-09-2x-personal-serve-live.md`. Update "self-termination" wording in `README.md`, `docs/operator/personal-serving.md`, and `docs/operator/serve-quickstart.md` to match the observed action (terminate or stop). Commit.

### Task 19: Free-pool benchmark with real numbers (R2; needs Task 8)

- [x] **Step 1: Run** (cost $0: keyless pools only)

```bash
PITWALL_GATEWAY_TOKEN=$(openssl rand -hex 16) uv run --frozen pitwall gateway status  # starts the supervised sidecar with the route table
uv run --frozen python tools/gateway/bench_free_pools.py --pools keyless --minutes 10 --rpm 6 --out docs/research/2026-09-10-free-pool-benchmark.md
```

Expected: the dossier's "LIVE RUN PENDING" banner is replaced by measured latency, uptime, and 429 counts per pool.

- [x] **Step 2: Apply verdicts and verify**

Run: `uv run --frozen pitwall gateway sync --apply-verdicts docs/research/2026-09-10-free-pool-benchmark.md && uv run --frozen pytest -q tests/test_gateway_catalog.py tests/test_gateway_seed.py`
Expected: `kill` rows become `enabled: false`; passed.

- [x] **Step 3: One routed request per surviving keyless pool through the broker**

For each `keep` pool: `curl -s -X POST http://127.0.0.1:8080/v1/inference -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' -d '{"capability":"coding.chat","provider_id":"<prov id>","messages":[{"role":"user","content":"reply with PITWALL_FREE_OK"}]}'` and confirm the text and a `$0` cost rollup via `pitwall cost workloads --limit 5 --json`. Commit the dossier, seeds, and evidence.

### Task 20: RunPod lifecycle for this candidate (R14)

Cap: $2.00 total; one pod lease, one serverless endpoint with `workersMin: 0`, one 10 GB network volume, each deleted in the same session.

- [x] **Step 1: Registry pod lease end to end**

`pitwall serve --model ornith-ai/Ornith-1.5-35B-A3B-GGUF --gpu-class "NVIDIA GeForce RTX 3090" --ttl-minutes 15 --max-usd-per-hour 0.60` with `DATABASE_URL` set; proxy one chat completion through `/v1/openai/<capability>/v1/chat/completions`; `pitwall leases stop <lease-id>`; confirm the pod is absent with `pitwall runpod pods list --json`, the lease is `stopped`, and three `config_audit` rows (`lease_ready`, `stop`, `lease_closed`) exist via `psql-remote` or the local test database.

- [x] **Step 2: Serverless and volume control plane**

Create, read, update, and delete one endpoint and one volume through `pitwall runpod`; confirm each delete with a 404 read.

- [x] **Step 3: Record** `docs/evidence/2026-09-2x-runpod-candidate-live.md` with spend from pod uptime × rate (billing lags, per defect 14). Commit.

### Task 21: Vast.ai, Together, and Lambda Cloud countersigns (R14)

- [ ] **Step 1: For each provider with an operator account and credential reference**

Run its adapter's availability read and the smallest supported operation under the live gate described in `docs/operator/current-providers.md` (compute providers: create and immediately destroy the cheapest instance; Together: one bounded chat completion). Cap $1.00 per provider.

- [ ] **Step 2: For each provider without an account**

Change its support-matrix row in `docs/support-matrix.md` and `docs/capability-matrix.md` from "Code-complete; countersign pending" to "Code-complete; not live-verified (no operator account)" and update `tests/test_current_provider_live_guard.py` or the matrix test that pins that wording.

- [ ] **Step 3: Record and commit** `docs/evidence/2026-09-2x-provider-countersigns.md`.

### Task 22: Orchestrator channel live exits per harness (R14)

The release-acceptance lane has live DeepSeek (Alibaba route) and Claude Sonnet ask/answer and steer-acknowledgement journeys; the event-driven channel branch has installed Codex and Qwen Code child acceptance.

- [x] **Step 1: Import the existing evidence**

Summarize each existing live journey (harness, route, date, observed ASK/ANSWER/STEER/ack and final marker, cleanup) into `docs/evidence/2026-09-2x-channel-live.md`, citing `$PITWALL_EVIDENCE_ROOT` paths.

- [x] **Step 2: Run the missing harnesses**

For every harness with `mcpChannel` true in `packages/agent-routing/runtime/model_routing/capability_inventory.py` that the evidence file does not already cover, run the Phase B/C exit from `packages/agent-routing/docs/orchestrator-channel.md` with a disposable fixture repository: a child asks, the operator answers with `pitwall-agent-routing answer`, a scope steer is acknowledged, the final report reflects the steer. Record each.

- [x] **Step 3: Phase D unattended fan-out**

Run the Phase D exit (at least one policy-answered routine ask and one operator escalation) with the `gateway` seat as the policy answerer after Task 19. Record and commit.

---

## Phase 6 — Finish the merged worktree work, then push

### Task 23: RA-050 intermittent API failure

The release-acceptance lane fixed the test's masked early-request errors and leaked request (`$PITWALL_EVIDENCE_ROOT/release-acceptance/ra050-root-20260921/REVIEW.md`); the original timeout trigger at the stream readiness event is unresolved.

- [x] **Step 1: Reproduce under stress**

Run: `for i in $(seq 1 200); do uv run --frozen pytest -q -p no:randomly <the RA-050 node id from REVIEW.md> -x || break; done`, in parallel with `uv run --frozen pytest -q -m "not integration and not slow" -n 4` to create load (add `pytest-xdist` only to the command line via `uv run --with pytest-xdist`).

- [x] **Step 2: On reproduction, capture the event timeline**

Enable the route's debug logging for the run and save the timeline of readiness, first byte, and cancellation to `$PITWALL_EVIDENCE_ROOT/release-acceptance/ra050-<date>/`. The fix goes in the code path the timeline implicates, with a regression test that forces the same ordering deterministically (an injected event or clock). No timing bound is loosened.

- [x] **Step 3: If 200 stressed runs do not reproduce**

Record the exact commands, counts, and environment in `docs/evidence/2026-09-2x-ra050.md` and keep RA-050 open in that file with the next reproduction strategy; this is the one allowed "not reproduced" outcome and it must say so in the final status message.

### Task 24: Diagnose the two historical Pi terminal-driving timeouts

- [x] **Step 1:** Rerun both recorded terminal-driving scenarios from `packages/pi-workbench/docs/baseline-results.md` with `PI_WORKBENCH_TRACE=1` (add that environment switch to `src/launcher.ts` if absent: it tees the child's stderr and the RPC event log to the evidence directory).
- [x] **Step 2:** For each timeout, name the stalled phase (provider request, tool execution, compaction, child wait) from the trace and fix the owning module with a vitest case that reproduces the stall with a fake provider or tool; or, when both scenarios now complete, record the passing runs as superseding evidence.
- [x] **Step 3:** Update `baseline-results.md` and commit.

### Task 25: Pi Workbench comparison open items

- [x] **Step 1: Core-task failures (14/18).** For each of the four failing rows in the preserved reevaluation, rerun that candidate/task with `npm run comparison -- --only <candidate>:<task>` and fix the Workbench-owned cause (profile, extension, admission) where the failure is Workbench's; a failure owned by the upstream candidate stays recorded as that candidate's result.
- [x] **Step 2: Warm reuse and queue timing.** Add a two-repetition warm mode to `scripts/comparison-runner.ts` that reuses the fixture and session across repetitions and records `warmAcrossRepetitions`; record admission queue time from the Workbench admission lock timestamps in `src/admission.ts` (or its current equivalent) into `queueMs`.
- [x] **Step 3: M02 source correspondence.** Fetch the published tarball for the pinned Pi version, compare its file hashes against the tag in the upstream repository, and record the matching commit (or the mismatch) in `docs/source-lock.md`.
- [x] **Step 4:** Update `comparison-suite.md` status, run `npx vitest run`, commit.

### Task 26: Bring out-of-repository evidence in

- [x] **Step 1:** For each receipt cited in `$PITWALL_EVIDENCE_ROOT/release-acceptance/MACHINE-HANDBACK.md` and `~/.local/state/pitwall-handoffs/OUTSTANDING-WORK.md` that supports a published claim, add a sanitized summary to `docs/evidence/2026-09-2x-release-acceptance-live.md` (date, command, identifiers without credentials, observed result, evidence path under `$PITWALL_EVIDENCE_ROOT`).
- [x] **Step 2:** `make docs-check`, commit.

### Task 27: Candidate freeze and acceptance packet

Runs after the journey-coverage plan has closed its matrix.

- [x] **Step 1:** Build the candidate identity: `uv run --frozen python -m tools.release_acceptance.candidate --root . --output-dir $PITWALL_EVIDENCE_ROOT/release-acceptance/candidate-<date>`.
- [x] **Step 2:** Run the Phase 4 exit gate set on the frozen tree and the matrix assembler from the journey-coverage plan against it; every required row is `pass` or an approved exception with scope and expiry.
- [x] **Step 3:** Write `docs/release/acceptance-packet-<date>.md` (candidate id, matrix summary, gate outputs, evidence index, exceptions, known limits as stated in the support matrix). Commit.

### Task 28: Push

- [x] **Step 1:** `git status --short` is empty; `git log origin/main..main --oneline | wc -l` is recorded.
- [x] **Step 2:** With the maintainer's explicit go in the conversation: `git push origin main`, then `fj-wait-sha`-equivalent for GitHub: `gh run watch --repo Buckeyes22/pitwall $(gh run list --repo Buckeyes22/pitwall --branch main --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status`.
- [ ] **Step 3:** After CI is green, and with the maintainer's yes for each, remove the merged worktrees (`~/git/pitwall-{channel-redesign,readiness,gateway-catalog-fix,pi-workbench,qa-onboarding,release-acceptance}` via `git worktree remove`) and delete the local branches whose content is on `main`: `chore/dependabot-2026-09-18`, `chore/remove-dependabot`, `design/event-driven-channel`, `docs/decisions-2026-09-18`, `docs/qa-onboarding`, `feat/agent-install-first-wave`, `feat/orchestrator-channel-b-d`, `feat/pi-workbench`, `feat/qwen-code-channel`, `feat/release-acceptance`, `fix/agent-routing-sbom-name`, `fix/component-release-metadata`, `fix/readiness`, `fix/gateway-installed-catalog`, `fix/gateway-release-sbom`, `fix/macos-killpg-eperm`, `test/dispatch-contract-diagnostics`, `integration/2026-09-22`.
