# MiniMax Code harness and child PWD fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship one branch (`fix/agents-child-pwd`) that (a) makes every harness child see `PWD` equal to its
working directory, so isolated opencode dispatches stop writing into the source checkout, and (b) adds
MiniMax Code (`mcode exec`) as Pitwall's sixteenth harness with shim, registry, installer, channel
registration, docs, plugins, and release-acceptance bindings.

**Architecture:** The PWD fix lives in `_start_supervised` (`src/pitwall/agents/process.py`), the one
`Popen` call shared by `run_process` and `run_bounded_capture`. The harness follows the ZCode commit
`836c4fba` file-for-file, but mcode is a stdin harness with a model flag and an effort flag, so its
adapter borrows the Codex/agy shapes: prompt on stdin (`--input -`), workspace via `--cwd`, policy via
`--permission smart|full`, always one explicit `--model provider/model[#variant]`. Its registered models
come from the MiniMax model-facts unit (`register: true` routes), not hand-written registry rows.

**Tech Stack:** Python 3.14.7, `uv`, pytest + unittest, JSON registries under
`src/pitwall/agents/resources/`, MiniMax Code CLI 0.6.12 (`@minimax-ai/code`).

**Spec:** This plan; the design facts it argues from are the probe evidence below (all observed on the development workstation,
2026-10-15, with `mcode` 0.6.12 logged in to a Global account).

## Probe evidence (the spec)

| Question | Observed |
|---|---|
| Headless entry point | `mcode exec [options] [prompt]`; `--input -` reads the prompt from stdin |
| Workspace | `--cwd <path>`; with `PWD=<main>` and `--cwd <worktree>` the write landed in the worktree |
| Model flag | `exec` accepts `--model provider/model[#variant]` only (no `-m`); malformed value → exit 2, `--model must use provider/model or provider/model#variant` |
| Default model | `minimax/MiniMax-M3.1-Flash-Preview`, variant `thinking` (`~/.minimax/config.yaml` `defaultModel`) |
| Models on the account | `minimax/MiniMax-M2.7`, `minimax/MiniMax-M2.7-highspeed`, `minimax/MiniMax-M3`, `minimax/MiniMax-M3.1-Flash-Preview` |
| Unknown model in valid form | `--model minimax/no-such-model` → exit 0, `"status":"succeeded"`, JSON reports `modelId: no-such-model`: **silent acceptance** |
| Effort | M3.1-Flash-Preview: `default, low, medium, high, xhigh, max`; M2.7, M2.7-highspeed, M3: any `--effort` → exit 2 `does not support reasoning effort selection` |
| Permission | `--permission smart` (default) writes inside `--cwd` but refuses risky commands; `full` runs everything; `ask` needs TUI/ACP |
| Smart refusal | `rm -rf ./.git` under `smart` → exit 0, `status: succeeded`, refusal only in the answer text: `HOST_CAPABILITY_UNAVAILABLE: this Runtime host cannot prompt for permission.` |
| Output | `--output-format text` prints only the final answer on stdout; `json` prints one `exec.result` object |
| Exit codes | 0 success; 2 usage/validation (bad model form, bad effort, empty input); 3 not signed in (`Sign in to MiniMax to use Agent features. Run \`mcode login\``); 6 `--timeout` exceeded |
| MCP (user scope) | `~/.minimax/mcp.json` = `{"mcpServers": {"<name>": {"command", "args", "timeout"}}}`; registering `pitwall mcp serve channel` there exposed all six channel tools |
| MCP env | stdio servers inherit mcode's environment (`PITWALL_AGENTS_CHANNEL_DISPATCH_ID` reached the server) |
| MCP disable flag | `"enabled": false` hides the server; `"disabled": true` is ignored |
| Channel tool under `smart` | `inbox` call succeeded |
| Install | npm package `@minimax-ai/code` 0.6.12, bin `mcode`; vendor installer puts it at `~/.minimax-code/bin/mcode` |
| Login | `mcode login --region global` (this account's saved region is `en`) |
| PWD bug (opencode) | `opencode run` with `cwd=<worktree>`, `PWD=<main>` → `creating instance directory=<main>`; with `PWD=<worktree>` it stays in the worktree |

## Global Constraints

- Python through `uv run --frozen` or `.venv/bin/python` only; never bare `python`.
- Python 3.14.7; setup `uv sync --frozen --extra dev --python 3.14.7`.
- Shim and `pitwall agents _shim` entry paths must not import fastapi, uvicorn, asyncpg, redis, arq, textual, runpod, mcp, or prometheus_client (`tests/test_startup_imports.py`, `tests/agents/test_dispatch_import_weight.py`).
- `pitwall.agents` never imports broker services, repositories, or `pitwall.db` (`tests/test_agents_import_direction.py`).
- Tests stay hermetic: fake harness binaries via `tests/agents/shim_test_support.ShimSandbox`; no live MiniMax calls in tests.
- Harness id `mcode`; shim `mcode-shim.sh`; binary override env `MCODE_BIN`; display name `MiniMax Code (MiniMax account)`.
- No new abstractions: reuse `_JSON_SECTION`, `channel_entry_disabled`, the npm installer recipe, and the model-facts `register` mechanism.
- Commits are DCO-signed (`git commit -s`) and end with the session attribution lines.
- Never weaken a test or gate to make a change pass.

## Review Focus

1. **A typo'd MiniMax model id** (`--model minimax/MiniMax-M3.2`) — mcode exits 0 and serves something anyway; the shim must refuse it with exit 64 before launch. Pinned in Task 3, Step 1 (`test_unregistered_minimax_model_is_refused`).
2. **A smart-mode refusal** — mcode exits 0 after declining a command; the dispatch must record exit 77 with a remediation, not success. Pinned in Task 3, Step 1 (`test_smart_mode_refusal_becomes_exit_77`).
3. **Dispatch started from a different directory than the worktree** (every managed channel dispatch) — the child must work in the worktree. Pinned in Task 1 (PWD test) and Task 3, Step 1 (`--cwd` equals `workspacePath`).
4. **The caller uses `-m` out of habit** — `mcode exec` has no `-m`; the shim must translate it to one `--model`. Pinned in Task 3, Step 1 (`test_short_model_flag_becomes_one_long_flag`).
5. **mcode not signed in** — exit 3 with mcode's own sign-in message must reach the caller unchanged as a failed dispatch. Pinned in Task 3, Step 1 (`test_not_signed_in_exit_propagates`).

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `src/pitwall/agents/process.py` (`_start_supervised`) | child `PWD` = `cwd` | 1 |
| `tests/agents/test_process.py` | PWD regression test | 1 |
| `docs/agents/model-facts/families/minimax/facts.json` | M3.1 opencode id fix, mcode routes, references, stale statement | 2 |
| `src/pitwall/agents/resources/config/harness-registry.json` | opencode MiniMax example id; `mcode` harness entry | 2, 3 |
| generated: `FACTS.md`, `harness-registry.generated.json`, `routes.generated.md`, MODEL-FACTS blocks in the three `model-prompting.md` | regenerated, never hand-edited | 2, 3 |
| `src/pitwall/agents/harnesses/mcode.py` | adapter | 3 |
| `src/pitwall/agents/harnesses/__init__.py` | `_ADAPTERS["mcode"]` | 3 |
| `src/pitwall/agents/cli.py` (`_add_dispatch_commands`, `_add_doctor_commands`) | `mcode` in shim and doctor choices | 3 |
| `src/pitwall/agents/doctor.py` (`HARNESS_HELP`) | help probe | 3 |
| `src/pitwall/agents/resources/schemas/doctor-result.schema.json` | `mcode` in both provider enums | 3 |
| `src/pitwall/agents/resources/config/harness-installers.json` | npm recipe | 3 |
| `tests/agents/test_mcode.py` (new) and the harness matrices listed in Task 3 | contract tests | 3 |
| `src/pitwall/agents/capability_inventory.py` (`CHANNEL_HARNESSES`, `channel_config_path`) | channel config location | 4 |
| `src/pitwall/agents/mcp_registration.py` (`_JSON_SECTION`, `render_entry`) | channel entry shape | 4 |
| `tests/agents/test_mcp_registration.py`, `tests/agents/test_capability_inventory.py` | channel tests | 4 |
| `release_acceptance/surface-test-map.json`, `denominator.json`, `reviewed-bindings.json` | surface bindings | 5 |
| `plugins/**` | agent card, hooks, skills, ledger card, READMEs | 6 |
| `docs/**`, `qa/concepts/**`, `README.md`, `CHANGELOG.md` | operator docs | 7 |

Execution is one sequential lane: Tasks 3–5 all edit the shared registry and test matrices, so no task can
run in parallel with another. No lane ownership file is needed.

---

### Task 1: Child processes see `PWD` equal to their working directory

The code and test are already in the worktree (uncommitted). This task records them.

**Files:**
- Modify: `src/pitwall/agents/process.py` — `_start_supervised` (before `subprocess.Popen`)
- Test: `tests/agents/test_process.py` — `ProcessTests.test_child_pwd_names_its_cwd_not_the_callers`
- Modify: `CHANGELOG.md` — `## [Unreleased]` → `### Fixed`

**Interfaces:**
- Consumes: nothing.
- Produces: every `run_process` / `run_bounded_capture` child gets `env["PWD"] == str(cwd)` whenever `cwd` is not None.

- [ ] **Step 1: Confirm the test fails without the fix**

```bash
cd ~/git/pitwall-fix-pwd
git stash push src/pitwall/agents/process.py
.venv/bin/python -m pytest tests/agents/test_process.py -k child_pwd -q 2>&1 | tail -2
git stash pop
```
Expected: `1 failed` before `stash pop`.

- [ ] **Step 2: Confirm the implementation is exactly this**

```python
    child_env = dict(env)
    if cwd is not None:
        # Popen changes the directory but not PWD. A harness that reads PWD (opencode
        # does) would otherwise work in the caller's checkout instead of *cwd*.
        child_env["PWD"] = str(cwd)
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
```

- [ ] **Step 3: Run the process tests**

Run: `.venv/bin/python -m pytest tests/agents/test_process.py -q 2>&1 | tail -2`
Expected: `24 passed`

- [ ] **Step 4: Add the changelog line** under `## [Unreleased]` → `### Fixed` (create the headings if absent, matching the file's existing style):

```markdown
- Harness children now see `PWD` set to their working directory. OpenCode builds its session from
  `$PWD`, so isolated dispatches started from another checkout wrote into that checkout instead of
  their worktree.
```

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/agents/process.py tests/agents/test_process.py CHANGELOG.md
git commit -s -m "fix(agents): harness children see PWD set to their working directory"
```

---

### Task 2: MiniMax facts name the routes that actually reach M3.1-Flash-Preview

The facts claim "no Pitwall route reaches it yet" and list the opencode route as
`minimax/MiniMax-M3.1-Flash-Preview`, which the public API does not serve (the same unit's vendor
statement). The Token Plan provider opencode exposes is `minimax-coding-plan/`.

**Files:**
- Modify: `docs/agents/model-facts/families/minimax/facts.json`
- Modify: `src/pitwall/agents/resources/config/harness-registry.json` — `harnesses.opencode.routeFamilies[id=minimax].example`
- Modify (hand-written lines): `plugins/{claude,codex,copilot}/skills/subagent-model-routing/references/model-prompting.md:373`, `plugins/claude/skills/subagent-model-routing/SKILL.md:398` and `:832`, `docs/prompting/00-prompt-reference-index.md:103`
- Regenerate: `FACTS.md`, the MODEL-FACTS blocks, `harness-registry.generated.json`, `routes.generated.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `facts.json` top-level `promptReference` and `runtimeReference` (Task 3's `register` routes need them).

- [ ] **Step 1: Prove the opencode id on this machine**

Run: `opencode models | grep -x 'minimax-coding-plan/MiniMax-M3.1-Flash-Preview'; opencode models | grep -c '^minimax/'`
Expected: the id printed once, then `0`.

- [ ] **Step 2: Edit `facts.json`**

1. Add top-level keys (same shape as `families/codex/facts.json`):
   ```json
   "promptReference": "prompting/minimax-prompting-reference.md",
   "runtimeReference": "references/model-prompting.md#minimax",
   ```
2. In `models["minimax-m3.1-flash-preview"].routes`, change the opencode route's `model` to
   `minimax-coding-plan/MiniMax-M3.1-Flash-Preview`.
3. In `guidance[id=minimax-m31-token-plan-only]`, replace `text` with:
   `MiniMax-M3.1-Flash-Preview is offered only through Token Plan and MiniMax Code for now. The public API and OpenCode Go serve M3.`
   (The routes table now carries reachability; the vendor source states only availability.)

- [ ] **Step 3: Fix the hand-written ids**

Replace `opencode-shim.sh minimax/MiniMax-M3.1-Flash-Preview` and `opencode-shim minimax/MiniMax-M3.1-Flash-Preview`
with the `minimax-coding-plan/` id in the five hand-written locations above, and set the registry
`example` to `minimax-coding-plan/MiniMax-M3.1-Flash-Preview`. The three `model-prompting.md` copies must
stay byte-identical.

- [ ] **Step 4: Regenerate and check**

```bash
uv run --frozen python tools/agents/sync_model_facts.py
uv run --frozen python tools/agents/sync_routes.py
uv run --frozen python tools/agents/sync_model_facts.py --check
uv run --frozen python tools/agents/sync_routes.py --check
grep -rn 'opencode-shim[^ ]* minimax/MiniMax-M3.1' docs plugins src | grep -v superpowers
cmp plugins/claude/skills/subagent-model-routing/references/model-prompting.md plugins/codex/skills/subagent-model-routing/references/model-prompting.md && cmp plugins/claude/skills/subagent-model-routing/references/model-prompting.md plugins/copilot/skills/subagent-model-routing/references/model-prompting.md && echo IDENTICAL
.venv/bin/python -m pytest tests/agents/test_sync_model_facts.py tests/agents/test_model_facts_command.py tests/agents/test_registry.py -q 2>&1 | tail -2
```
Expected: both `--check` runs print `model facts outputs are current` / no stale files; the grep prints nothing; `IDENTICAL`; tests pass.

- [ ] **Step 5: Commit**

```bash
git add -A docs/agents/model-facts/families/minimax docs/prompting/00-prompt-reference-index.md plugins src/pitwall/agents/resources
git commit -s -m "docs(model-facts): MiniMax M3.1 routes use the Token Plan id; drop the stale no-route claim"
```

---

### Task 3: The `mcode` harness — adapter, registry, installer, shim, and every harness matrix

Registering a harness turns every "all harnesses" matrix red at once, so the adapter, registry entry,
installer recipe, and matrix rows land together.

**Files:**
- Create: `src/pitwall/agents/harnesses/mcode.py`
- Create: `tests/agents/test_mcode.py`
- Modify: `src/pitwall/agents/harnesses/__init__.py` (import + `_ADAPTERS["mcode"]`, alphabetical after `kimi`)
- Modify: `src/pitwall/agents/resources/config/harness-registry.json` (`harnesses.mcode`, alphabetical position)
- Modify: `docs/agents/model-facts/families/minimax/facts.json` (mcode `register` routes)
- Modify: `src/pitwall/agents/resources/config/harness-installers.json` (`harnesses.mcode`)
- Modify: `src/pitwall/agents/cli.py` — both `choices` tuples in `_add_dispatch_commands` and the one in `_add_doctor_commands` (insert `"mcode"` after `"kimi"`)
- Modify: `src/pitwall/agents/doctor.py` — `HARNESS_HELP`
- Modify: `src/pitwall/agents/resources/schemas/doctor-result.schema.json` — `providerFilter` and `provider` enums
- Modify (matrices): `tests/agents/test_install.py` (`HARNESSES`, count `15 → 16`), `tests/agents/test_journeys.py` (`ARGV`, `STDIN_PROMPT`, `_shim_args`), `tests/agents/test_marketplaces.py` (`"mcode-shim"`), `tests/agents/test_provider_adapters.py`, `tests/agents/test_provider_setup.py` (lines 215 and 857 lists), `tests/agents/test_registry.py` (harness list at line 62), `tests/agents/test_shim_contract.py` (`HARNESS_ARGS`, the dicts in `test_missing_harness_binary_preserves_per_shim_ledger_asymmetry`, `test_unreadable_prompt_preserves_started_ordering`, `test_restricted_policy_flags_are_preserved`, and the stdin branches of `test_prompt_delivery_matches_harness_contract` / `test_stdin_prompt_source_matches_file_delivery`), `tests/agents/test_shim_receipt.py` (`HARNESS_ARGS`), `tests/agents/test_workflow_all_harnesses.py` (`ALL_HARNESSES`, `EXPECTED`)
- Create: `plugins/claude/agents/mcode-shim.md` (`test_marketplaces` and `validate_plugins.py` require an agent card per shim)

**Interfaces:**
- Consumes: Task 2's `promptReference` / `runtimeReference`.
- Produces: `get_adapter("mcode") -> MCodeAdapter`; `MCodeAdapter.DEFAULT_MODEL == "minimax/MiniMax-M3.1-Flash-Preview"`;
  `load_registry()["harnesses"]["mcode"]["models"]` keys = the four `minimax/...` ids; shim `mcode-shim.sh`.

- [ ] **Step 1: Write the failing adapter tests** — `tests/agents/test_mcode.py`:

```python
"""MiniMax Code dispatch contract: stdin prompt, explicit model, workspace, and policy."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from pitwall.agents import setup
from pitwall.agents.errors import UsageError
from pitwall.agents.harnesses import get_adapter
from pitwall.agents.registry import load_registry
from tests.agents.shim_test_support import ShimSandbox

M31 = "minimax/MiniMax-M3.1-Flash-Preview"


def _run(args: list[str], **env: str):
    sandbox = ShimSandbox()
    sandbox.install_harness("mcode")
    prompt = sandbox.prompt("private prompt\n")
    result = sandbox.run("mcode", [str(prompt), *args], env=sandbox.environment(**env))
    return sandbox, result


@pytest.mark.parametrize(
    "flag",
    [
        "--input=-",
        "--input-format=json",
        "--cwd=x",
        "--permission=full",
        "--output-format=json",
        "--session=x",
        "--continue",
        "-o",
        "--output-last-message=x",
        "--config=x",
        "--diagnostics-dir=x",
        "--output-schema=x",
        "review",
    ],
)
def test_reserved_flags_do_not_start_mcode(flag: str) -> None:
    sandbox, result = _run([flag])
    try:
        assert result.returncode == 64
        assert result.stdout == b"SHIM-DONE exit=64\n"
        assert not sandbox.args_file.exists()
    finally:
        sandbox.cleanup()


@pytest.mark.parametrize("unrestricted,policy", [("1", "full"), ("0", "smart")])
def test_dispatch_sends_prompt_on_stdin_with_workspace_policy_and_model(
    unrestricted: str, policy: str
) -> None:
    sandbox, result = _run([], PITWALL_AGENTS_UNRESTRICTED=unrestricted)
    try:
        assert result.returncode == 0, result.stderr
        args = sandbox.captured_args()
        assert args[:3] == ["exec", "--input", "-"]
        assert args[args.index("--permission") + 1] == policy
        assert args[args.index("--output-format") + 1] == "text"
        assert args[args.index("--model") + 1] == M31
        assert args.count("--model") == 1
        assert sandbox.captured_stdin() == b"private prompt\n"
        assert sandbox.ledger_records()[-1]["model"] == M31
    finally:
        sandbox.cleanup()
    adapter = get_adapter("mcode")
    prepared = adapter.prepare(
        adapter.parse(["/p.md"], {}, Path("/home")), "/bin/mcode", b"x", {}, {"workspacePath": "/ws"}
    )
    assert prepared.argv[prepared.argv.index("--cwd") + 1] == "/ws"
    assert prepared.stdin == b"x"


@pytest.mark.parametrize(
    "forwarded",
    [["-m", "minimax/MiniMax-M3"], ["-m=minimax/MiniMax-M3"], ["--model", "minimax/MiniMax-M3"]],
)
def test_short_model_flag_becomes_one_long_flag(forwarded: list[str]) -> None:
    sandbox, result = _run(forwarded)
    try:
        assert result.returncode == 0, result.stderr
        args = sandbox.captured_args()
        assert "-m" not in args and not any(a.startswith("-m=") for a in args)
        assert args.count("--model") == 1
        assert args[args.index("--model") + 1] == "minimax/MiniMax-M3"
    finally:
        sandbox.cleanup()


def test_variant_suffix_is_kept_and_validated_on_its_base() -> None:
    sandbox, result = _run(["--model", f"{M31}#none-thinking"])
    try:
        assert result.returncode == 0, result.stderr
        args = sandbox.captured_args()
        assert args[args.index("--model") + 1] == f"{M31}#none-thinking"
    finally:
        sandbox.cleanup()


@pytest.mark.parametrize("model", ["minimax/MiniMax-M3.2", "MiniMax-M3", "minimax/"])
def test_unregistered_minimax_model_is_refused(model: str) -> None:
    sandbox, result = _run(["--model", model])
    try:
        assert result.returncode == 64
        assert not sandbox.args_file.exists()
    finally:
        sandbox.cleanup()


def test_custom_provider_models_pass_through() -> None:
    sandbox, result = _run(["--model", "my-provider/some-model"])
    try:
        assert result.returncode == 0, result.stderr
        assert "my-provider/some-model" in sandbox.captured_args()
    finally:
        sandbox.cleanup()


def test_smart_mode_refusal_becomes_exit_77() -> None:
    adapter = get_adapter("mcode")
    tail = b"The runtime rejected the call:\nHOST_CAPABILITY_UNAVAILABLE: this Runtime host cannot prompt for permission.\n"
    reason = adapter.detect_soft_denial(0, tail)
    assert reason is not None and "PITWALL_AGENTS_UNRESTRICTED=1" in reason
    assert adapter.detect_soft_denial(0, b"all done\n") is None
    assert adapter.detect_soft_denial(1, tail) is None


def test_not_signed_in_exit_propagates() -> None:
    sandbox = ShimSandbox()
    try:
        sandbox.install_harness("mcode")
        result = sandbox.run(
            "mcode",
            [str(sandbox.prompt())],
            env=sandbox.environment(FAKE_EXIT="3", FAKE_STDERR="mcode exec failed: Sign in to MiniMax\n"),
        )
        assert result.returncode == 3
        assert b"Sign in to MiniMax" in result.stderr
    finally:
        sandbox.cleanup()


def test_workflow_args_and_registered_models() -> None:
    adapter = get_adapter("mcode")
    assert adapter.workflow_args(M31, "high", Path("/p.md")) == ["/p.md", "--model", M31, "--effort", "high"]
    assert adapter.workflow_args(M31, None, Path("/p.md")) == ["/p.md", "--model", M31]
    models = load_registry()["harnesses"]["mcode"]["models"]
    assert set(models) == {
        "minimax/MiniMax-M2.7",
        "minimax/MiniMax-M2.7-highspeed",
        "minimax/MiniMax-M3",
        M31,
    }
    assert models[M31]["effortValues"] == ["low", "medium", "high", "xhigh", "max"]
    assert models["minimax/MiniMax-M3"]["effortValues"] == []


def test_npm_install_recipe(tmp_path: Path) -> None:
    spec = next(s for s in setup.load_install_specs(system_name="Linux") if s.harness_id == "mcode")
    assert spec.recipe.kind == "npm"
    assert spec.recipe.package == "@minimax-ai/code"
    binary = tmp_path / ".minimax-code/bin/mcode"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    runner = Mock()
    result = setup.install_selected(
        [spec],
        {"HOME": str(tmp_path), "PATH": str(tmp_path / "bin")},
        -1,
        downloader=Mock(),
        installer_runner=runner,
        verifier=Mock(),
    )
    assert result[0].status == "already-installed"
    runner.assert_not_called()


def test_parse_rejects_missing_prompt_source() -> None:
    with pytest.raises(UsageError):
        get_adapter("mcode").parse([], {}, Path("/home"))
```

Helper facts (checked): `ShimSandbox.captured_stdin()` reads the fake harness's stdin capture;
the fake harness (`tests/agents/fixtures/fake_harness.py`) exits with `FAKE_EXIT` and writes `FAKE_STDERR`;
`PlatformRecipe.package` holds the npm package name.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/agents/test_mcode.py -q 2>&1 | tail -3`
Expected: errors, `KeyError`/`UsageError: unknown harness mcode`.

- [ ] **Step 3: Write the adapter** — `src/pitwall/agents/harnesses/mcode.py`:

```python
"""MiniMax Code CLI (`mcode exec`) using the operator's MiniMax login."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from pitwall.agents.errors import UsageError
from pitwall.agents.registry import load_registry

from .base import HarnessAdapter, ParsedRequest, PreparedCommand

DEFAULT_MODEL = "minimax/MiniMax-M3.1-Flash-Preview"
# mcode's own rule for --model (verified: anything else exits 2).
_MODEL_FORM = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:#[A-Za-z0-9_-]+)?")
_MODEL_FLAGS = ("-m", "--model")
_RESERVED_FLAGS = {
    "--input",
    "--input-format",
    "--cwd",
    "--permission",
    "--output-format",
    "--session",
    "--continue",
    "-o",
    "--output-last-message",
    "--config",
    "--diagnostics-dir",
    "--output-schema",
    "-h",
    "--help",
    "review",
}
# Under --permission smart, mcode declines a risky command, keeps exit 0, and the
# refusal reaches only the answer text (verified on 0.6.12).
_SOFT_DENIAL_MARKER = b"HOST_CAPABILITY_UNAVAILABLE"


def _without_model_flags(arguments: list[str]) -> list[str]:
    kept: list[str] = []
    skip = False
    for argument in arguments:
        if skip:
            skip = False
            continue
        if argument in _MODEL_FLAGS:
            skip = True
            continue
        if any(argument.startswith(f"{flag}=") for flag in _MODEL_FLAGS):
            continue
        kept.append(argument)
    return kept


class MCodeAdapter(HarnessAdapter):
    harness_id = "mcode"
    effort_values: tuple[str, ...] | None = ("low", "medium", "high", "xhigh", "max")
    model_bound = True
    prompt_delivery = "stdin"
    binary_override_env = "MCODE_BIN"
    endpoint_delivery = "none"

    def workflow_args(self, model: str, effort: str | None, prompt_path: Path) -> list[str]:
        args = [str(prompt_path), "--model", model]
        if effort is not None:
            args.extend(["--effort", effort])
        return args

    def usage(self) -> str:
        return (
            "mcode-shim: usage: mcode-shim.sh <prompt-source> "
            "[--model provider/model[#variant]] [--effort level] [extra mcode exec args]"
        )

    def parse(self, argv: list[str], env: Mapping[str, str], home: Path) -> ParsedRequest:
        del env, home
        self.require_args(argv, 1, self.usage())
        for argument in argv[1:]:
            flag = argument.split("=", 1)[0]
            if flag in _RESERVED_FLAGS:
                raise UsageError(f"mcode-shim: {flag} is managed by the shim")
        model, has_model = self.parse_model_flag(argv[1:], DEFAULT_MODEL)
        if _MODEL_FORM.fullmatch(model) is None:
            raise UsageError(
                f"mcode-shim: --model must be provider/model or provider/model#variant, got {model!r}"
            )
        base = model.split("#", 1)[0]
        known = load_registry()["harnesses"]["mcode"]["models"]
        if base.startswith("minimax/") and base not in known:
            # mcode accepts an unknown minimax/ id and answers anyway; refuse it here.
            raise UsageError(
                f"mcode-shim: {base} is not a registered MiniMax Code model "
                f"({', '.join(sorted(known))})"
            )
        return ParsedRequest(argv[0], model, _without_model_flags(argv[1:]), has_model)

    def detect_soft_denial(self, exit_code: int, stderr_tail: bytes) -> str | None:
        if exit_code == 0 and _SOFT_DENIAL_MARKER in stderr_tail:
            return (
                "MiniMax Code declined a command it could not ask permission for "
                "(HOST_CAPABILITY_UNAVAILABLE) and exited 0; reporting exit 77. Re-run "
                "unrestricted (set PITWALL_AGENTS_UNRESTRICTED=1) to use --permission full."
            )
        return None

    def resolve_binary(self, env: Mapping[str, str], home: Path) -> str | None:
        if env.get("MCODE_BIN"):
            return env["MCODE_BIN"]
        found = self.which("mcode", env)
        if found:
            return found
        fallback = home / ".minimax-code" / "bin" / "mcode"
        return str(fallback) if fallback.is_file() and os.access(fallback, os.X_OK) else None

    def missing_binary_message(self) -> str:
        return "mcode-shim: MiniMax Code CLI not found; see docs/agents/mcode.md"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        args = [
            "exec",
            "--input",
            "-",
            "--cwd",
            preflight_data.get("workspacePath", os.getcwd()),
            "--permission",
            "full" if self.unrestricted(env) else "smart",
            "--output-format",
            "text",
            "--model",
            request.model,
            *request.extra_args,
        ]
        return PreparedCommand([binary, *args], dict(env), prompt, self.sanitize_args(args))
```

Register it in `harnesses/__init__.py`: `from .mcode import MCodeAdapter` and `"mcode": MCodeAdapter,`.

- [ ] **Step 4: Add the registry entry** — `harness-registry.json`, `harnesses.mcode` (models stay `{}`; Step 5 fills them):

```json
"mcode": {
  "displayName": "MiniMax Code (MiniMax account)",
  "shim": "mcode-shim.sh",
  "binaryCandidates": ["mcode", "$HOME/.minimax-code/bin/mcode"],
  "binaryOverrideEnv": "MCODE_BIN",
  "nativeHosts": [],
  "promptDelivery": "stdin",
  "harnessKind": "model-bound",
  "endpointDelivery": "none",
  "allowUnknownModels": false,
  "defaultModel": {"source": "registry", "fallback": "minimax/MiniMax-M3.1-Flash-Preview"},
  "modelSelectors": ["-m", "--model"],
  "effort": {"kind": "harness-flag", "key": "--effort", "values": ["low", "medium", "high", "xhigh", "max"]},
  "capabilities": {"authProbe": false, "configProbe": false, "worktreeDispatch": true, "structuredOutput": false},
  "models": {},
  "routeFamilies": []
}
```

- [ ] **Step 5: Register the models through the facts** — in `families/minimax/facts.json` add to each model's `routes`:

| facts model | route to add |
|---|---|
| `minimax-m3.1-flash-preview` | `{"harness": "mcode", "model": "minimax/MiniMax-M3.1-Flash-Preview", "register": true}` |
| `minimax-m3` | `{"harness": "mcode", "model": "minimax/MiniMax-M3", "register": true}` |
| `minimax-m2.7` | `{"harness": "mcode", "model": "minimax/MiniMax-M2.7", "register": true}` and `{"harness": "mcode", "model": "minimax/MiniMax-M2.7-highspeed", "register": true}` |

Then:
```bash
uv run --frozen python tools/agents/sync_model_facts.py
uv run --frozen python tools/agents/sync_routes.py
jq -r '.harnesses.mcode.models | keys[]' src/pitwall/agents/resources/config/harness-registry.json
```
Expected: the four `minimax/...` ids, each with `capabilityCard` `.../ledger/minimax.md` and provenance `model-facts:families/minimax`.

- [ ] **Step 6: Installer recipe** — `harness-installers.json`, `harnesses.mcode`:

```json
"mcode": {
  "documentationUrl": "https://agent.minimax.io/docs/cli/quick-start",
  "platforms": {
    "darwin": {"kind": "npm", "package": "@minimax-ai/code", "version": "0.6.12"},
    "linux": {"kind": "npm", "package": "@minimax-ai/code", "version": "0.6.12"}
  },
  "allowedRedirectHosts": ["registry.npmjs.org"],
  "verifyArgs": ["--version"],
  "authArgs": ["login", "--region", "global"]
}
```

- [ ] **Step 7: CLI choices, doctor probe, schema enums**

- `cli.py`: add `"mcode",` after `"kimi",` in the two shim `choices` tuples of `_add_dispatch_commands` and the doctor `choices` tuple of `_add_doctor_commands`.
- `doctor.py` `HARNESS_HELP`: `"mcode": (("exec", "--help"), ("--input", "--cwd", "--permission", "--output-format")),`
- `doctor-result.schema.json`: add `"mcode"` after `"kimi"` in `providerFilter.enum` and `provider.enum`.

- [ ] **Step 8: Matrix rows**

- `test_install.py`: `"mcode"` in `HARNESSES`; `self.assertEqual(16, len(expected))`.
- `test_journeys.py`:
  ```python
  "mcode": [
      "exec", "--input", "-", "--cwd", "{cwd}", "--permission", "full",
      "--output-format", "text", "--model", "test-harness/journey-model",
  ],
  ```
  add `"mcode"` to `STDIN_PROMPT`; in `_shim_args` add
  `if shim == "mcode": return [str(prompt), "--model", "test-harness/journey-model", *extra]`.
- `test_shim_contract.py`: `HARNESS_ARGS["mcode"] = lambda prompt: [str(prompt)]`; `"mcode": []` in the missing-binary ledger dict; `"mcode": ["finished"]` in the unreadable-prompt dict (same defaults as zcode); `"mcode": ("--permission", "smart")` restricted and `"mcode": "full"` unrestricted in `test_restricted_policy_flags_are_preserved`; put `mcode` in the stdin-delivery branch (with `codex`, `claude`, `goose`, `opencode`) of `test_prompt_delivery_matches_harness_contract` and `test_stdin_prompt_source_matches_file_delivery`.
- `test_shim_receipt.py`: `"mcode": lambda prompt: [str(prompt)]`.
- `test_workflow_all_harnesses.py`: `"mcode"` in `ALL_HARNESSES`;
  `"mcode": ("minimax/MiniMax-M3.1-Flash-Preview", "high", [str(PROMPT), "--model", "minimax/MiniMax-M3.1-Flash-Preview", "--effort", "high"]),`
- `test_registry.py`, `test_provider_adapters.py`, `test_provider_setup.py` (both lists), `test_marketplaces.py` (`"mcode-shim"`): add the id.

- [ ] **Step 9: Agent card** — `plugins/claude/agents/mcode-shim.md`: copy `zcode-shim.md` verbatim, then change `name: mcode-shim`, the script to `mcode-shim.sh`, the description to
  `Dispatches a one-shot prompt to MiniMax Code (MiniMax account; model via --model provider/model; prompt delivered on stdin).`,
  and every "ZCode" to "MiniMax Code".

- [ ] **Step 9b: Usage parser (the session-usage plan landed first)** — set `output_mode = "json"`, change
  `prepare`'s `"--output-format", "text"` to `"json"`, and add `parse_output`: single JSON object;
  `input=usage.inputTokens`, `output=outputTokens`, `cache_read=cacheReadTokens`, `cache_write=None`,
  `reasoning=None`, `total=totalTokens`; answer `output + "\n"`; `status != "succeeded"` → `complete=False`.
  Add `("mcode", "mcode.json", b"ok\n", 16270, 3, 190, None, None, 16273, None)` to
  `test_parser_reads_answer_and_usage` and `"mcode"` to `test_garbage_is_not_usage` in
  `tests/agents/test_harness_usage_parsers.py` (fixture `tests/agents/fixtures/harness_usage/mcode.json`
  already exists). In this plan's other steps, the mcode argv expectations use `"--output-format", "json"`.

- [ ] **Step 10: Run the harness suites**

```bash
.venv/bin/python -m pytest tests/agents/test_mcode.py tests/agents/test_install.py tests/agents/test_journeys.py tests/agents/test_marketplaces.py tests/agents/test_provider_adapters.py tests/agents/test_provider_setup.py tests/agents/test_registry.py tests/agents/test_shim_contract.py tests/agents/test_shim_receipt.py tests/agents/test_workflow_all_harnesses.py tests/agents/test_dispatch_import_weight.py tests/test_startup_imports.py -q 2>&1 | tail -3
uv run --frozen python tools/agents/validate_registry.py && uv run --frozen python tools/agents/validate_json_schemas.py && uv run --frozen python tools/agents/validate_plugins.py && uv run --frozen python tools/agents/check_generated.py && uv run --frozen python tools/agents/sync_routes.py --check && uv run --frozen python tools/agents/sync_model_facts.py --check
s=$(mktemp -d); uv run --frozen python tools/agents/write_shims.py "$s"; ls "$s"/scripts | grep mcode; test "$(PATH="$s/bin:$PATH" bash "$s"/scripts/mcode-shim.sh 2>&1 | tail -1)" = "SHIM-DONE exit=64" && echo USAGE-OK
```
Expected: all pass; validators silent/OK; `mcode-shim.sh`; `USAGE-OK`.

- [ ] **Step 11: Commit**

```bash
git add -A src/pitwall/agents tests/agents docs/agents/model-facts/families/minimax plugins/claude/agents/mcode-shim.md
git commit -s -m "feat(agents): MiniMax Code harness with shim, registry models, and npm installer"
```

---

### Task 4: Channel MCP registration for MiniMax Code

**Files:**
- Modify: `src/pitwall/agents/capability_inventory.py` — `CHANNEL_HARNESSES` (add `"mcode"`), `channel_config_path`
- Modify: `src/pitwall/agents/mcp_registration.py` — `_JSON_SECTION["mcode"] = "mcpServers"`, `render_entry`
- Test: `tests/agents/test_mcp_registration.py`, `tests/agents/test_capability_inventory.py` (`test_a_disabled_channel_entry_is_not_registered`)

**Interfaces:**
- Consumes: Task 3's registered harness id.
- Produces: `channel_config_path("mcode", env, home) == home / ".minimax" / "mcp.json"`; `render_entry("mcode", cmd) == {"command": cmd, "args": ["mcp", "serve", "channel"], "enabled": True, "timeout": 3660000}`.

- [ ] **Step 1: Failing tests** — append to `RegistrationTests` in `tests/agents/test_mcp_registration.py`:

```python
    def test_mcode_registers_in_minimax_mcp_json_and_keeps_other_servers(self) -> None:
        config = self.home / ".minimax" / "mcp.json"
        config.parent.mkdir(parents=True)
        original = {"mcpServers": {"docs": {"command": "docs"}}}
        config.write_text(json.dumps(original), encoding="utf-8")
        plan = self._apply("mcode")
        self.assertEqual(config, plan.path)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(
            {"command": CMD, "args": ARGS, "enabled": True, "timeout": 3660000},
            data["mcpServers"]["pitwall-channel"],
        )
        self.assertEqual({"command": "docs"}, data["mcpServers"]["docs"])
        self.assertTrue(mcp_channel_registered("mcode", self.env, self.home))
        self.assertFalse(plan_registration("mcode", self.env, self.home, command=CMD).changed)
        self._apply("mcode", remove=True)
        self.assertEqual(original, json.loads(config.read_text(encoding="utf-8")))

    def test_mcode_fresh_registration_is_private_and_refuses_a_foreign_entry(self) -> None:
        plan = self._apply("mcode")
        self.assertEqual(0o600, plan.path.stat().st_mode & 0o777)
        plan.path.write_text(
            '{"mcpServers": {"pitwall-channel": {"command": "someone-else"}}}', encoding="utf-8"
        )
        with self.assertRaises(RegistrationError):
            plan_registration("mcode", self.env, self.home, command=CMD)
```

and in `test_a_disabled_channel_entry_is_not_registered` add `"mcode": ("mcpServers", "enabled", False),` to `json_hosts`.

Run: `.venv/bin/python -m pytest tests/agents/test_mcp_registration.py tests/agents/test_capability_inventory.py -q 2>&1 | tail -3`
Expected: FAIL (`KeyError: 'mcode'`).

- [ ] **Step 2: Implement**

`capability_inventory.py`:
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
    "agy",
    "muse",
    "hermes",
    "goose",
    "mcode",
)
```
and in `channel_config_path` before the final `raise`:
```python
    if harness_id == "mcode":
        # MiniMax Code reads user-scope servers from mcpServers in its data directory.
        return home / ".minimax" / "mcp.json"
```

`mcp_registration.py`: `"mcode": "mcpServers",` in `_JSON_SECTION`, and in `render_entry`:
```python
    if harness == "mcode":
        # MiniMax Code starts stdio servers with its own environment (verified with an
        # env-recording server), honours "enabled": false, and ignores "disabled".
        # timeout is in milliseconds and bounds a blocking ask.
        return {
            "command": command,
            "args": list(CHANNEL_ARGS),
            "enabled": True,
            "timeout": TOOL_TIMEOUT_S * 1000,
        }
```

- [ ] **Step 3: Pass**

Run: `.venv/bin/python -m pytest tests/agents/test_mcp_registration.py tests/agents/test_capability_inventory.py tests/agents/test_channel_probe.py tests/agents/test_install.py -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/pitwall/agents/capability_inventory.py src/pitwall/agents/mcp_registration.py tests/agents/test_mcp_registration.py tests/agents/test_capability_inventory.py
git commit -s -m "feat(agents): register the channel server with MiniMax Code"
```

---

### Task 5: Release-acceptance surfaces for the new harness

**Files:**
- Modify: `release_acceptance/surface-test-map.json` — two entries
- Modify: `release_acceptance/denominator.json` — `total 1322 → 1325`, `counts["routing:provider"] 14 → 15`, `counts["routing:shim"] 15 → 16`, `counts["routing_contracts:provider"] 14 → 15`, new `reason` prefix
- Regenerate: `release_acceptance/reviewed-bindings.json` (and any per-family binding file the tool rewrites)

**Interfaces:**
- Consumes: Tasks 3–4 tests (`J40ShimsJourney::test_every_harness_shim`, `RegistryTests::test_registry_adapter_contracts_are_aligned`).
- Produces: surfaces `routing:adapter:mcode`, `routing:provider:mcode`, `shim:mcode` bound.

- [ ] **Step 1: Map entries** (beside the zcode ones):

```json
"routing:adapter:mcode": {
  "test_node_id": "tests/agents/test_journeys.py::J40ShimsJourney::test_every_harness_shim",
  "framework": "unittest",
  "proof_lane": "hermetic",
  "reviewed_oracle": "MiniMax Code receives the stdin prompt, dispatch workspace, explicit permission policy, one --model, and the effort flag; child failure propagates"
},
"routing:provider:mcode": {
  "test_node_id": "tests/agents/test_registry.py::RegistryTests::test_registry_adapter_contracts_are_aligned",
  "framework": "unittest",
  "proof_lane": "hermetic",
  "reviewed_oracle": "The MiniMax Code registry entry agrees with its adapter contract and its facts-registered models"
}
```

Both use `"framework": "unittest"` (the zcode entries were corrected to that in `83a7c32d`, because both test classes are `unittest.TestCase`); set it so in both entries above.

- [ ] **Step 2: Denominator** — set the counts above and prepend to `reason`:
  `2026-10-15: add MiniMax Code provider, adapter contract, and shim, each bound to hermetic registry/journey checks; previous total 1322. Previous note: ` followed by the existing text.

- [ ] **Step 3: Bind and check**

```bash
uv run --frozen python -m tools.release_acceptance.bind_surfaces
.venv/bin/python -m pytest tests/release_acceptance -q -p no:randomly 2>&1 | tail -3
```
Expected: bind reports 0 unmapped; tests pass. If `test_denominator` names different family counts,
the surfaces discovered differ from the ZCode precedent — read which surface it lists and bind it; do not
edit counts to match without a bound test.

- [ ] **Step 4: Commit**

```bash
git add release_acceptance
git commit -s -m "test(release): bind the MiniMax Code surfaces"
```

---

### Task 6: Plugins teach the new route

**Files (each gets the mcode equivalent of its zcode text):**
- `plugins/claude/hooks/dag_tripwire_shims.py` and `plugins/claude/hooks/ledger-tripwire.py` — add `"mcode-shim"` and `"pitwall:mcode-shim"` to the shim name sets, and `mcode` to both alternations in the `-shim\.sh` regexes
- `plugins/claude/agents/route-shim.md` — harness list
- `plugins/claude/commands/distill.md` — `` - `shim:"mcode"` -> family from the model id (`minimax/...` -> `minimax.md`). ``
- `plugins/{claude,codex,copilot}/skills/subagent-model-routing/SKILL.md` — routing table row and the harness list:
  `` | `pitwall:mcode-shim` -> MiniMax Code | `minimax/MiniMax-M3.1-Flash-Preview` (default), `minimax/MiniMax-M3`, `minimax/MiniMax-M2.7[-highspeed]` | `--effort` on M3.1 only; MiniMax login | ``
- `plugins/{claude,codex,copilot}/skills/subagent-model-routing/references/model-prompting.md` — under `## MiniMax`, after the opencode line:
  ``Or through MiniMax Code: `mcode-shim.sh <prompt-file> --model minimax/MiniMax-M3.1-Flash-Preview [--effort high]` (MiniMax account login; the only route besides Token Plan that serves M3.1).``
  The three copies stay byte-identical.
- `plugins/claude/skills/subagent-model-routing/ARCHITECTURE.md`, `plugins/{claude,codex,copilot}/README.md` — harness lists and counts (`fourteen` → `fifteen` provider harnesses / shims, add `mcode`)

- [ ] **Step 1: Hook tests first** — find the hook tests (`grep -rln 'dag_tripwire_shims\|ledger-tripwire' tests`) and add `mcode-shim.sh` beside the zcode case in each; run them and see them fail.
- [ ] **Step 2: Edit the files above.**
- [ ] **Step 3: Check**

```bash
uv run --frozen python tools/agents/validate_plugins.py
cmp plugins/claude/skills/subagent-model-routing/references/model-prompting.md plugins/codex/skills/subagent-model-routing/references/model-prompting.md && cmp plugins/claude/skills/subagent-model-routing/references/model-prompting.md plugins/copilot/skills/subagent-model-routing/references/model-prompting.md && echo IDENTICAL
.venv/bin/python -m pytest $(grep -rln 'dag_tripwire_shims\|ledger-tripwire\|validate_plugins' tests) -q 2>&1 | tail -2
grep -rn 'fourteen' plugins | grep -v CHANGELOG
```
Expected: validator OK, `IDENTICAL`, tests pass, the last grep prints nothing.

- [ ] **Step 4: Commit**

```bash
git add plugins tests
git commit -s -m "docs(plugins): route skills, hooks, and READMEs know MiniMax Code"
```

---

### Task 7: Operator docs, QA concept page, changelog

**Files:**
- Create: `docs/agents/mcode.md` — modeled on `docs/agents/zcode.md`: install (`npm install -g @minimax-ai/code` or the vendor installer into `~/.minimax-code`), `mcode login --region global` / `mcode login`, models and effort table (from the probe evidence), `--permission smart` default vs `PITWALL_AGENTS_UNRESTRICTED=1` → `full`, exit 77 on `HOST_CAPABILITY_UNAVAILABLE`, channel registration file `~/.minimax/mcp.json`, exit codes 2/3/6.
- Create: `qa/concepts/mcode-routing.md` (modeled on `qa/concepts/zcode-routing.md`); add its line to `qa/concepts/README.md`.
- Create: `plugins/claude/skills/subagent-model-routing/ledger/harnesses/mcode.md`:
  ```markdown
  # MiniMax Code

  Harness: `mcode`. Models: `minimax/MiniMax-M3.1-Flash-Preview` (default), `minimax/MiniMax-M3`,
  `minimax/MiniMax-M2.7`, `minimax/MiniMax-M2.7-highspeed`; capability cards are the family's
  (`ledger/minimax.md`). Uses the operator's MiniMax login; no custom endpoint. `--effort` exists only
  on M3.1-Flash-Preview. Record task outcomes before choosing it for critical work.
  ```
- Modify (add mcode wherever zcode is listed, and fix counts): `docs/agents/harness-cli-setup.md`, `docs/agents/routing-readme.md`, `docs/agents/routes.md`, `docs/agents/architecture.md` (`fifteen public shell entrypoints` → `sixteen`, `fourteen provider transports` → `fifteen`), `docs/agents/doctor.md`, `docs/agents/SECURITY.md`, `docs/agents/orchestrator-channel.md`, `docs/agents/README.md`, `docs/agents/harness-registry.md`, `docs/agents/workflows.md`, `docs/sdlc/23-agent-routing.md`, `README.md`.
- Modify: `CHANGELOG.md` `## [Unreleased]` → `### Added`:
  ```markdown
  - MiniMax Code harness (`mcode-shim.sh`, `pitwall agents _shim mcode`): prompt on stdin, explicit
    `--model minimax/...` with registered models from the MiniMax facts, `--effort` on M3.1-Flash-Preview,
    `--permission smart` (or `full` when unrestricted), exit 77 when smart mode declines a command,
    channel registration in `~/.minimax/mcp.json`, and an npm installer recipe.
  ```
  and under `### Fixed`: `- MiniMax M3.1-Flash-Preview's OpenCode route uses the Token Plan id \`minimax-coding-plan/\`; the facts no longer say no route reaches it.`

- [ ] **Step 1: Write and edit the files above.**
- [ ] **Step 2: Check**

```bash
uv run --frozen python tools/ci/check_markdown_links.py
git ls-files -z | xargs -0 -r -n 200 uv run --frozen python tools/guards/repo_text_policy.py
grep -rn 'fourteen harnesses\|fourteen shims\|fourteen provider\|fifteen public' docs README.md | grep -vE 'docs/(superpowers|releases|evidence)/'
```
Expected: link check and text policy pass; the grep prints nothing.

- [ ] **Step 3: Commit**

```bash
git add docs qa README.md CHANGELOG.md plugins/claude/skills/subagent-model-routing/ledger/harnesses/mcode.md
git commit -s -m "docs(agents): MiniMax Code setup, routing, QA concept, and changelog"
```

---

### Task 8: Whole-branch verification (CI parity, journeys, live smoke)

**Files:** none modified unless a check fails; a failure is fixed in the task that owns the file, then this task restarts.

- [ ] **Step 1: CI lint-job parity** (exact commands from `.github/workflows/ci.yml`)

```bash
uv lock --check && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy --strict src/ && uv run --frozen mypy tools/agents
uv run --frozen python tools/agents/validate_json_schemas.py && uv run --frozen python tools/agents/validate_plugins.py && uv run --frozen python tools/agents/validate_registry.py && uv run --frozen python tools/agents/check_generated.py && uv run --frozen python tools/agents/sync_routes.py --check
uv run --frozen python -m compileall -q src/pitwall/agents tests/agents tools/agents
s=$(mktemp -d); uv run --frozen python tools/agents/write_shims.py "$s"; for f in "$s"/scripts/*.sh; do bash -n "$f"; test "$(PATH="$s/bin:$PATH" bash "$f" 2>&1 | tail -1)" = "SHIM-DONE exit=64" || echo "BAD $f"; done
uv run --frozen python -m pitwall.agents doctor --installation-only --json >/dev/null && echo DOCTOR-OK
git ls-files -z -- '*.py' | xargs -0 -r -n 200 uv run --frozen python tools/guards/python_policy.py
uv run --frozen python tools/ci/check_dco.py
make pi-extensions-check
make sec-semgrep
```
Expected: every command exits 0; no `BAD` lines; `DOCTOR-OK`.

- [ ] **Step 2: Unit suite** — `make test-fast 2>&1 | tail -3` → `0 failed`.

- [ ] **Step 3: Database suites, one at a time** (shared `pitwall_test`; never in parallel): `make up && make test-int 2>&1 | tail -3` → `0 failed`.

- [ ] **Step 4: Full journey runner** — `set -a; source ~/.config/pitwall/live-run.env; set +a; bash scripts/release/run-user-journeys.sh 2>&1 | tail -20` → the matrix gate passes.

- [ ] **Step 5: Live smoke (uses the maintainer's MiniMax login; run only with the maintainer's approval)** from a scratch repo, through the patched CLI in the worktree:

```bash
T=$(mktemp -d); git -C "$T" init -q && git -C "$T" commit -q --allow-empty -m i; cd "$T"
printf 'Run pwd, write ok to probe.txt, reply with the pwd output only.\n' > /tmp/mcode-smoke.md
~/git/pitwall-fix-pwd/.venv/bin/pitwall agents _shim mcode /tmp/mcode-smoke.md --routing-workspace isolated --routing-task-mode write --routing-base HEAD 2>&1 | tail -3
```
Expected: `SHIM-DONE exit=0`; the printed pwd is under `~/.local/state/pitwall/agents/worktrees/`; `probe.txt` exists in that worktree, not in `$T`. Repeat once with `--model minimax/MiniMax-M3` and once with `--model minimax/MiniMax-M3.2` (expect exit 64).

- [ ] **Step 6: Opencode PWD re-check** — the same isolated dispatch through `opencode` with `opencode/space-bunny-free` writes into its worktree (`git -C <worktree> status --short` shows `probe.txt`; the source repo stays clean).

---

### Task 9: Land and install

Each step here is outward-facing and runs only after the maintainer approves the batch.

- [ ] **Step 1:** Push `fix/agents-child-pwd`; open one PR to `main` (title `MiniMax Code harness; harness children see their working directory as PWD`), body summarizing Tasks 1–7 and Task 8's evidence. Wait with `gh pr checks --watch`.
- [ ] **Step 2:** After merge: `git archive <merge-sha>` into `~/.local/share/pitwall/releases/<date>-<short-sha>`, `uv sync --frozen --extra storage --no-dev --python 3.14.7`, relink `~/.local/bin/pitwall`, `pitwall agents install`, `claude plugin update pitwall@pitwall-local`, and check `pitwall agents doctor` (no fail) and `grep -c pitwall-channel ~/.minimax/mcp.json` (1).
- [ ] **Step 3:** Tell the peer session that reported the opencode isolation bug which release dir to re-probe its opencode routes against.
